"""LangGraph analiz akışı. Her çalıştırma ayrı thread_id ile SQLite checkpoint'e yazılır (yarıda kalan çalıştırma
aynı run_id ile kaldığı yerden sürdürülebilir).

plan → collect → analyze → [extra_evidence ≤ max_iterations] → explain → finalize
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import report as rpt
from .attribution import alternatives, detect
from .features import assess_quality, extract, window_list
from .llm import LlmUnavailable
from .mcp_gateway import McpUnavailable, QueryResult
from .redact import strip_fields
from .scoring import score_all
from .signals import CATALOG, EXTRA, Window, build_queries, iso, parse_ts


class AgentState(TypedDict, total=False):
    run_id: str
    mode: str
    question: str | None
    generated_at: str
    started_monotonic: float
    start: str
    end: str
    baseline_start: str | None
    baseline_end: str | None
    notes: list[str]
    windows: list[dict]
    results: dict[str, dict]
    evidence: list[dict]
    quality: dict
    features_summary: dict
    scores: dict
    attribution: dict
    alternatives: list[str]
    extra: dict
    iterations: int
    explanation: str | None
    explanation_source: str | None
    errors: list[str]
    status: str
    budget: dict
    use_llm: bool
    fixture: bool
    report_paths: list[str]
    report: dict


def _qr_dict(r: QueryResult) -> dict:
    return {"key": r.key, "dql": r.dql, "status": r.status, "records": r.records, "query_id": r.query_id,
            "error": r.error, "elapsed_ms": r.elapsed_ms, "evidence_id": r.evidence_id, "notifications": r.notifications}


def _windows(state) -> list[Window]:
    return [Window(w["name"], parse_ts(w["start"]), parse_ts(w["end"])) for w in state["windows"]]


def _summary(fw: dict) -> dict:
    """Raporda gösterilecek kısa özellik özeti (ham kayıt değil)."""
    def r(v):
        return round(v, 3) if isinstance(v, float) else v
    keep = ("rpm", "err_rate", "http_mean_ms", "span_n", "span_p95_ms", "pool_ratio", "pool_ratio_avg",
            "pool_pending_max", "pool_timeouts", "lock_waiters_avg", "db_sessions", "db_exec_ms_per_min", "gc_overhead")
    return {"apps": {a: {k: r(v) for k, v in f.items() if k in keep} for a, f in fw["apps"].items()},
            "hosts": {h: {k: r(v) for k, v in f.items()} for h, f in fw["hosts"].items()},
            "db": {k: r(v) for k, v in fw["db"].items() if k != "edges"},
            "containers": fw["containers"]}


def build_graph(settings, gateway_factory, llm=None, checkpointer=None, persist_files: bool = True):
    cfg = settings.cfg
    b = cfg["budgets"]

    def over_time(state) -> bool:
        return time.monotonic() - state["started_monotonic"] > b["run_timeout_seconds"]

    def plan(state: AgentState) -> dict:
        start, end = parse_ts(state["start"]), parse_ts(state["end"])
        if state.get("baseline_start"):
            ws = [Window("baseline", parse_ts(state["baseline_start"]), parse_ts(state["baseline_end"])),
                  Window("window", start, end)]
        else:
            ws = window_list(start, end, cfg["windows"]["baseline_minutes"])
        return {"windows": [w.iso() for w in ws], "iterations": 0, "errors": state.get("errors", []),
                "status": "running", "extra": {}}

    def collect(state: AgentState) -> dict:
        ws = _windows(state)
        errors = list(state.get("errors", []))
        try:
            gw = gateway_factory()
            res = gw.run_many(build_queries(ws, cfg, list(CATALOG)))
            results = {k: _qr_dict(v) for k, v in res.items()}
            used = gw.queries_used
        except McpUnavailable as e:
            errors.append(f"MCP erişilemedi: {e}. Başka veri kaynağına geçilmedi; rapor kısmidir.")
            results = {k: {"key": k, "dql": d, "status": "UNAVAILABLE", "records": [], "error": str(e)}
                       for k, d in build_queries(ws, cfg, list(CATALOG))}
            used = 0
        for k, r in results.items():
            r["records"] = strip_fields(r["records"], cfg["strip_fields"])
            r.setdefault("evidence_id", QueryResult(k, r["dql"], r["status"]).evidence_id)
        evidence = [{"id": r["evidence_id"], "signal": k, "status": r["status"], "records": len(r["records"]),
                     "dynatrace_query_id": r.get("query_id"), "dql": r["dql"], "error": r.get("error")}
                    for k, r in results.items()]
        return {"results": results, "evidence": evidence, "errors": errors,
                "budget": {**state.get("budget", {}), "queries_used": used}}

    def analyze(state: AgentState) -> dict:
        ws = _windows(state)
        results = state["results"]
        F = extract(results, ws, cfg)
        target = next(w for w in ws if w.name == "window")
        quality = assess_quality(results, target, cfg)
        if quality.get("collector", {}).get("status") == "OK":
            # Collector o pencerede başarılı ölçüm yazdıysa kilit sinyallerindeki boş sonuç "veri yok" değil, gerçek 0'dır
            for k in ("edges", "lock_waiters", "pg_lock_logs"):
                if quality.get(k, {}).get("status") == "EMPTY":
                    quality[k]["true_zero"] = True
                    quality[k]["detail"] = "gerçek sıfır (collector o pencerede başarılı ölçüm yazdı)"
        scores = score_all(F["window"], F["baseline"], cfg)
        ev_ids = {k: r.get("evidence_id") for k, r in results.items() if r["status"] == "OK"}
        attr = detect(F["window"], F["baseline"], cfg, ev_ids)
        status = "partial" if all(q["status"] != "OK" for q in quality.values()) or state.get("errors") else "complete"
        return {"quality": quality, "scores": scores, "attribution": attr,
                "alternatives": alternatives(attr, quality), "status": status,
                "features_summary": {"baseline": _summary(F["baseline"]), "window": _summary(F["window"])}}

    def needs_extra(state: AgentState) -> str:
        ids = {m["id"] for m in state["attribution"]["mechanisms"]}
        want = ("row_lock" in ids and "edges_detail" not in state["extra"]) or \
               ({"db_cpu", "global_connection_pressure"} & ids and "top_statements" not in state["extra"])
        if want and state["iterations"] < b["max_iterations"] and not over_time(state) and state["status"] != "partial":
            return "extra_evidence"
        return "explain"

    def extra_evidence(state: AgentState) -> dict:
        ws = _windows(state)
        ids = {m["id"] for m in state["attribution"]["mechanisms"]}
        keys = []
        if "row_lock" in ids and "edges_detail" not in state["extra"]:
            keys.append("edges_detail")
        if {"db_cpu", "global_connection_pressure"} & ids and "top_statements" not in state["extra"]:
            keys.append("top_statements")
        extra, errors = dict(state["extra"]), list(state.get("errors", []))
        evidence, used = list(state["evidence"]), state.get("budget", {}).get("queries_used", 0)
        try:
            gw = gateway_factory()
            gw.queries_used = used
            res = gw.run_many([(k, EXTRA[k](ws, cfg)) for k in keys])
            for k, r in res.items():
                recs = strip_fields(r.records, cfg["strip_fields"])
                for x in recs:                           # sorgu metnindeki SQL yorumları (op/rid etiketleri) atılır
                    x.pop("query_text", None)
                extra[k] = {"evidence_id": r.evidence_id, "status": r.status, "records": recs[:15]}
                evidence.append({"id": r.evidence_id, "signal": k, "status": r.status, "records": len(recs),
                                 "dynatrace_query_id": r.query_id, "dql": r.dql, "error": r.error})
            used = gw.queries_used
        except McpUnavailable as e:
            errors.append(f"Ek evidence toplanamadı (MCP): {e}")
            for k in keys:
                extra[k] = {"status": "UNAVAILABLE", "records": []}
        return {"extra": extra, "evidence": evidence, "errors": errors, "iterations": state["iterations"] + 1,
                "budget": {**state.get("budget", {}), "queries_used": used}}

    def explain(state: AgentState) -> dict:
        draft = rpt.build({**state, "generated_at": state["generated_at"]})
        if llm is None or not state.get("use_llm", True) or over_time(state):
            why = "LLM devre dışı" if llm is None or not state.get("use_llm", True) else "süre bütçesi doldu"
            return {"explanation": rpt.deterministic_explanation(draft), "explanation_source": f"deterministik ({why})"}
        payload = {k: draft[k] for k in ("windows", "risk", "data_coverage", "causality", "impacted", "mechanisms",
                                         "alternatives", "data_gaps", "human_review", "extra_evidence", "notes",
                                         "features_summary", "status")}
        try:
            text = llm.explain(payload, state.get("question"))
            return {"explanation": text, "explanation_source": f"Claude ({getattr(llm, 'rca_model_name', settings.claude_rca_model)})"}
        except LlmUnavailable as e:
            return {"explanation": rpt.deterministic_explanation(draft),
                    "explanation_source": "deterministik (Claude erişilemedi)",
                    "errors": list(state.get("errors", [])) + [f"Claude erişilemedi: {e}"]}

    def finalize(state: AgentState) -> dict:
        budget = {**state.get("budget", {}), "elapsed_s": round(time.monotonic() - state["started_monotonic"], 1)}
        if llm is not None:
            budget.update(llm_calls=llm.usage["calls"], input_tokens=llm.usage["input_tokens"],
                          output_tokens=llm.usage["output_tokens"])
        r = rpt.build({**state, "budget": budget})
        paths: list[str] = []
        if persist_files:                         # platform raporu PostgreSQL'e yazar; CLI dosyaya
            j, m = rpt.save(r, settings.reports_dir)
            paths = [str(j), str(m)]
        return {"budget": budget, "report_paths": paths, "report": r}

    g = StateGraph(AgentState)
    for name, fn in [("plan", plan), ("collect", collect), ("analyze", analyze), ("extra_evidence", extra_evidence),
                     ("explain", explain), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "collect")
    g.add_edge("collect", "analyze")
    g.add_conditional_edges("analyze", needs_extra, ["extra_evidence", "explain"])
    g.add_conditional_edges("extra_evidence", needs_extra, ["extra_evidence", "explain"])
    g.add_edge("explain", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer, name="airlab-sre-analysis")


def new_run_id(prefix: str = "rep") -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def initial_state(start: datetime, end: datetime, *, run_id: str, mode: str = "analyze", question: str | None = None,
                  notes: list[str] | None = None, use_llm: bool = True, fixture: bool = False,
                  baseline: tuple[datetime, datetime] | None = None) -> AgentState:
    return {"run_id": run_id, "mode": mode, "question": question,
            "generated_at": iso(datetime.now(timezone.utc)), "started_monotonic": time.monotonic(),
            "start": iso(start), "end": iso(end), "notes": notes or [], "errors": [],
            "budget": {}, "use_llm": use_llm, "fixture": fixture,
            "baseline_start": iso(baseline[0]) if baseline else None,
            "baseline_end": iso(baseline[1]) if baseline else None}


def run_analysis(settings, gateway_factory, start: datetime, end: datetime, *, llm=None, question: str | None = None,
                 mode: str = "analyze", notes: list[str] | None = None, callbacks: list | None = None,
                 run_id: str | None = None, use_llm: bool = True, fixture: bool = False,
                 baseline: tuple[datetime, datetime] | None = None, checkpointer=None, persist_files: bool = True,
                 on_node=None, recursion_limit: int = 25) -> dict[str, Any]:
    """Analizi çalıştırır. `checkpointer` verilmezse SQLite (CLI); platform PostgresSaver verir.
    `on_node(name, update)` her düğüm bittiğinde çağrılır (ilerleme olayları için)."""
    run_id = run_id or new_run_id("inc" if mode == "watch-incident" else "rep")
    init = initial_state(start, end, run_id=run_id, mode=mode, question=question, notes=notes, use_llm=use_llm,
                         fixture=fixture, baseline=baseline)
    cfg = {"configurable": {"thread_id": run_id}, "callbacks": callbacks or [], "run_name": f"airlab-{mode}",
           "metadata": {"run_id": run_id, "mode": mode}, "recursion_limit": recursion_limit}

    def _go(saver):
        graph = build_graph(settings, gateway_factory, llm, saver, persist_files=persist_files)
        if on_node is None:
            return graph.invoke(init, cfg)
        for chunk in graph.stream(init, cfg, stream_mode="updates"):
            for name, update in chunk.items():
                on_node(name, update or {})
        return graph.get_state(cfg).values

    if checkpointer is not None:
        if persist_files:
            settings.ensure_dirs()
        return _go(checkpointer)
    from langgraph.checkpoint.sqlite import SqliteSaver
    settings.ensure_dirs()
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        return _go(saver)
