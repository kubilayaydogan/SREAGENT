"""LangGraph analiz akışı. Her çalıştırma ayrı thread_id ile checkpoint'e yazılır (platformda PostgresSaver: yarıda
kalan çalıştırma aynı run_id ile kaldığı yerden sürdürülebilir).

plan → collect → analyze → [extra_evidence ≤ max_iterations] → explain → finalize

Trace: her düğüm sabit adlı bir run'dır; çıktısındaki `step_summary` LangSmith'te görünen GÜVENLİ özettir (ham DQL,
kayıt, soru ya da model metni içermez — tracing.trace_safe yalnız izinli alanları gönderir).
"""
from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .tracing import child_config
from . import report as rpt
from .attribution import alternatives, detect
from .features import assess_quality, extract, window_list
from .llm import RUN_BUDGET, LlmBudgetExceeded, LlmUnavailable
from .mcp_gateway import OUTCOME, McpUnavailable, QueryResult
from .redact import strip_fields
from .scoring import score_all
from .guardrails.model import GuardrailBlocked
from .guardrails.runtime import final_output as guard_final_output
from .guardrails.runtime import guard_node
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
    window_source: str
    step_summary: dict
    extra_decision: dict
    mcp_failure: str | None


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


# Query yanıt vermedi: sonuç "veri yok" değil, "okunamadı" demektir.
FAILED_SIGNAL = {"ERROR", "PERMISSION", "UNAVAILABLE", "TIMEOUT", "SKIPPED_BUDGET"}


def _outcomes(results: dict) -> dict:
    return dict(Counter(OUTCOME.get(r["status"], str(r["status"]).lower()) for r in results.values()))


def _llm_outcome(e: Exception) -> str:
    if isinstance(e, LlmBudgetExceeded):
        return "budget_exceeded"
    m = str(e)
    return "refusal" if "refusal" in m else "empty_response" if "boş yanıt" in m else "llm_unavailable"


def guard_facts(draft: dict) -> dict:
    """final_output guardrail'leri için doğrulanmış olgular (deterministik rapordan; LLM'den değil)."""
    o = (draft.get("risk") or {}).get("overall") or {}
    apps = (draft.get("risk") or {}).get("apps") or {}
    win = next((w for w in draft.get("windows") or [] if w.get("name") == "window"), None)
    facts = {"evidence": draft.get("evidence") or [],
             "evidence_ids": [e.get("id") for e in draft.get("evidence") or []],
             "risk": {"score": o.get("score"), "band": o.get("band"), "coverage": o.get("coverage"),
                      "allowed_scores": [o.get("score")] + [a.get("score") for a in apps.values()],
                      "allowed_bands": [o.get("band")] + [a.get("band") for a in apps.values()]},
             "window": win}
    return facts


def _guard_explanation(text: str, draft: dict) -> str:
    """Before final output: evidence ID / provenance, yetersiz evidence, risk skoru tutarlılığı, hassas veri.
    Scoring'e dokunmaz; yalnız LLM metnini düzeltir (sanitize) ya da engeller (deterministik açıklamaya düşülür)."""
    out = guard_final_output(text, guard_facts(draft), node="explain")
    return out.payload.get("text", text) if isinstance(out.payload, dict) else text


def build_graph(settings, gateway_factory, llm=None, checkpointer=None, persist_files: bool = True):
    cfg = settings.cfg
    b = cfg["budgets"]

    def over_time(state) -> bool:
        return time.monotonic() - state["started_monotonic"] > b["run_timeout_seconds"]

    def decide_extra(st) -> dict:
        """Ek evidence kararı (yönlendirme ve trace için TEK kaynak)."""
        ids = {m["id"] for m in st["attribution"]["mechanisms"]}
        keys, why = [], []
        if "row_lock" in ids and "edges_detail" not in st["extra"]:
            keys.append("edges_detail")
            why.append("row_lock → lock edge ayrıntısı")
        if {"db_cpu", "global_connection_pressure"} & ids and "top_statements" not in st["extra"]:
            keys.append("top_statements")
            why.append("db_cpu/connection pressure → top statements")
        it = st.get("iterations", 0)
        d = {"iteration": it, "keys": keys}
        if not keys:
            return {**d, "next": "explain", "decision": "skip", "reason": "ek evidence gerektiren mechanism yok"}
        q = st.get("quality") or {}
        if st.get("mcp_failure") or (q and all(x["status"] != "OK" for x in q.values())):
            return {**d, "next": "explain", "decision": "skip", "reason": "kullanılabilir veri yok; ek query yapılmadı"}
        if it >= b["max_iterations"]:
            return {**d, "next": "explain", "decision": "skip", "reason": f"iterasyon sınırı ({b['max_iterations']})"}
        if over_time(st):
            return {**d, "next": "explain", "decision": "skip", "reason": "süre bütçesi doldu"}
        return {**d, "next": "extra_evidence", "decision": "collect", "reason": "; ".join(why), "iteration": it + 1}

    def plan(state: AgentState) -> dict:
        start, end = parse_ts(state["start"]), parse_ts(state["end"])
        if state.get("baseline_start"):
            ws = [Window("baseline", parse_ts(state["baseline_start"]), parse_ts(state["baseline_end"])),
                  Window("window", start, end)]
        else:
            ws = window_list(start, end, cfg["windows"]["baseline_minutes"])
        wd = {w.name: w.iso() for w in ws}
        summary = {"analysis_window": {"start": wd["window"]["start"], "end": wd["window"]["end"]},
                   "baseline_window": ({"start": wd["baseline"]["start"], "end": wd["baseline"]["end"]}
                                       if "baseline" in wd else None),
                   "window_source": state.get("window_source") or "default",
                   "baseline_source": "user" if state.get("baseline_start") else
                   f"default ({cfg['windows']['baseline_minutes']} dk önce)"}
        return {"windows": [w.iso() for w in ws], "iterations": 0, "errors": state.get("errors", []),
                "status": "running", "extra": {}, "step_summary": summary}

    def collect(state: AgentState) -> dict:
        ws = _windows(state)
        errors = list(state.get("errors", []))
        failure = None
        try:
            gw = gateway_factory()
            res = gw.run_many(build_queries(ws, cfg, list(CATALOG)))
            results = {k: _qr_dict(v) for k, v in res.items()}
            used = gw.queries_used
        except McpUnavailable as e:
            failure = getattr(e, "kind", "unavailable")
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
        summary = {"signals_requested": len(results), "signals": sorted(results), "outcomes": _outcomes(results),
                   "mcp_failure": failure, "queries_used": used}
        return {"results": results, "evidence": evidence, "errors": errors, "mcp_failure": failure,
                "budget": {**state.get("budget", {}), "queries_used": used}, "step_summary": summary}

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
        # Yanıt vermeyen query (hata, timeout, yetki, bütçe) eksik veridir: rapor "partial" olur. EMPTY / STALE
        # yanıt alınmış sonuçtur; tek başına raporu kısmi yapmaz.
        failed = sorted(k for k, q in quality.items() if q["status"] in FAILED_SIGNAL)
        status = ("partial" if all(q["status"] != "OK" for q in quality.values()) or state.get("errors") or failed
                  else "complete")
        ov = scores.get("overall") or {}
        summary = {
            "evidence_ids": sorted({v for v in ev_ids.values() if v}),
            "data_quality": {k: ("true_zero" if q.get("true_zero") else q["status"]) for k, q in quality.items()},
            "risk": {k: ov.get(k) for k in ("score", "band", "coverage", "scored_apps", "total_apps")},
            "components": {a: {k: v for k, v in (x.get("components") or {}).items()
                               if isinstance(v, (int, float)) and not isinstance(v, bool)}
                           for a, x in (scores.get("apps") or {}).items()},
            "mechanisms": [m["id"] for m in attr["mechanisms"]], "status": status, "failed_signals": failed}
        decision = decide_extra({**state, "attribution": attr, "status": status, "quality": quality})
        return {"quality": quality, "scores": scores, "attribution": attr,
                "alternatives": alternatives(attr, quality), "status": status,
                "features_summary": {"baseline": _summary(F["baseline"]), "window": _summary(F["window"])},
                "step_summary": summary, "extra_decision": decision}

    def needs_extra(state: AgentState) -> str:
        return (state.get("extra_decision") or decide_extra(state))["next"]

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
        why = (state.get("extra_decision") or {}).get("reason")
        failure = state.get("mcp_failure")
        got: dict[str, str] = {}
        try:
            gw = gateway_factory()
            gw.queries_used = used
            res = gw.run_many([(k, EXTRA[k](ws, cfg)) for k in keys])
            for k, r in res.items():
                recs = strip_fields(r.records, cfg["strip_fields"])
                for x in recs:                           # sorgu metnindeki SQL yorumları (op/rid etiketleri) atılır
                    x.pop("query_text", None)
                extra[k] = {"evidence_id": r.evidence_id, "status": r.status, "records": recs[:15]}
                got[k] = r.status
                evidence.append({"id": r.evidence_id, "signal": k, "status": r.status, "records": len(recs),
                                 "dynatrace_query_id": r.query_id, "dql": r.dql, "error": r.error})
            used = gw.queries_used
        except McpUnavailable as e:
            failure = failure or getattr(e, "kind", "unavailable")
            errors.append(f"Ek evidence toplanamadı (MCP): {e}")
            for k in keys:
                extra[k] = {"status": "UNAVAILABLE", "records": []}
                got[k] = "UNAVAILABLE"
        upd = {"extra": extra, "evidence": evidence, "errors": errors, "iterations": state["iterations"] + 1,
               "budget": {**state.get("budget", {}), "queries_used": used}, "mcp_failure": failure}
        nxt = decide_extra({**state, **upd})
        upd["extra_decision"] = nxt
        upd["step_summary"] = {"iteration": upd["iterations"], "decision": "collect", "reason": why, "keys": keys,
                               "outcomes": dict(Counter(OUTCOME.get(v, v.lower()) for v in got.values())),
                               "evidence_ids": [extra[k].get("evidence_id") for k in keys if extra[k].get("evidence_id")],
                               "next": nxt["next"], "next_reason": nxt["reason"]}
        return upd

    def explain(state: AgentState) -> dict:
        draft = rpt.build({**state, "generated_at": state["generated_at"]})
        model = getattr(llm, "rca_model_name", None) if llm is not None else None
        if llm is None or not state.get("use_llm", True) or over_time(state):
            off = llm is None or not state.get("use_llm", True)
            why = "LLM devre dışı" if off else "süre bütçesi doldu"
            return {"explanation": rpt.deterministic_explanation(draft), "explanation_source": f"deterministik ({why})",
                    "step_summary": {"llm_called": False, "outcome": "llm_disabled" if off else "time_budget",
                                     "source": "deterministic", "model": None}}
        rb = RUN_BUDGET.get()
        n0, u0, t0 = (len(rb.calls) if rb is not None else 0), dict(llm.usage), time.monotonic()

        def info(outcome: str) -> dict:
            if rb is not None:
                calls = list(rb.calls)[n0:]
                tin, tout = sum(c["input_tokens"] for c in calls), sum(c["output_tokens"] for c in calls)
            else:
                tin = llm.usage["input_tokens"] - u0.get("input_tokens", 0)
                tout = llm.usage["output_tokens"] - u0.get("output_tokens", 0)
            return {"llm_called": True, "model": model, "outcome": outcome,
                    "duration_ms": int((time.monotonic() - t0) * 1000), "input_tokens": tin, "output_tokens": tout,
                    "structured_output": False, "source": "llm" if outcome == "ok" else "deterministic"}
        payload = {k: draft[k] for k in ("windows", "risk", "data_coverage", "causality", "impacted", "mechanisms",
                                         "alternatives", "data_gaps", "true_zero_signals", "human_review",
                                         "extra_evidence", "notes", "features_summary", "status")}
        try:
            text = llm.explain(payload, state.get("question"))
            text = _guard_explanation(text, draft)
            return {"explanation": text, "explanation_source": f"Claude ({getattr(llm, 'rca_model_name', settings.claude_rca_model)})",
                    "step_summary": info("ok")}
        except GuardrailBlocked as e:
            return {"explanation": rpt.deterministic_explanation(draft),
                    "explanation_source": "deterministik (guardrail)",
                    "errors": list(state.get("errors", [])) + [f"Guardrail LLM açıklamasını engelledi: {e}"],
                    "step_summary": info("guardrail_blocked")}
        except LlmUnavailable as e:
            return {"explanation": rpt.deterministic_explanation(draft),
                    "explanation_source": "deterministik (Claude erişilemedi)",
                    "errors": list(state.get("errors", [])) + [f"Claude erişilemedi: {e}"],
                    "step_summary": info(_llm_outcome(e))}

    def finalize(state: AgentState) -> dict:
        budget = {**state.get("budget", {}), "elapsed_s": round(time.monotonic() - state["started_monotonic"], 1)}
        rb = RUN_BUDGET.get()
        if rb is not None:                        # koşuya ait sayaç (paylaşılan istemcinin süreç toplamı değil)
            calls = list(rb.calls)
            budget.update(llm_calls=len(calls), input_tokens=sum(c["input_tokens"] for c in calls),
                          output_tokens=sum(c["output_tokens"] for c in calls), llm_budget=rb.summary())
        elif llm is not None:
            budget.update(llm_calls=llm.usage["calls"], input_tokens=llm.usage["input_tokens"],
                          output_tokens=llm.usage["output_tokens"])
        r = rpt.build({**state, "budget": budget})
        paths: list[str] = []
        if persist_files:                         # platform raporu PostgreSQL'e yazar; CLI dosyaya
            j, m = rpt.save(r, settings.reports_dir)
            paths = [str(j), str(m)]
        reasons: list[str] = []
        if state.get("mcp_failure"):
            reasons.append(f"mcp_{state['mcp_failure']}")
        q = state.get("quality") or {}
        if q and all(x["status"] != "OK" and not x.get("true_zero") for x in q.values()):
            reasons.append("no_usable_signal")
        bad = sorted({e["status"] for e in state.get("evidence", [])} & FAILED_SIGNAL)
        reasons += [f"signals_{x.lower()}" for x in bad]
        if r.get("status") == "partial" and not reasons:
            reasons.append("errors")
        summary = {"report_id": r.get("id"), "status": r.get("status"),
                   "partial_reasons": reasons if r.get("status") == "partial" else [],
                   "explanation_source": state.get("explanation_source"), "elapsed_s": budget["elapsed_s"],
                   "queries_used": budget.get("queries_used"), "llm_calls": budget.get("llm_calls")}
        return {"budget": budget, "report_paths": paths, "report": r, "step_summary": summary}

    g = StateGraph(AgentState)
    for name, fn in [("plan", plan), ("collect", collect), ("analyze", analyze), ("extra_evidence", extra_evidence),
                     ("explain", explain), ("finalize", finalize)]:
        g.add_node(name, guard_node(name, fn))     # Guardrails: before / after node execution
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
                  baseline: tuple[datetime, datetime] | None = None, window_source: str = "default") -> AgentState:
    return {"run_id": run_id, "mode": mode, "question": question, "window_source": window_source,
            "generated_at": iso(datetime.now(timezone.utc)), "started_monotonic": time.monotonic(),
            "start": iso(start), "end": iso(end), "notes": notes or [], "errors": [],
            "budget": {}, "use_llm": use_llm, "fixture": fixture,
            "baseline_start": iso(baseline[0]) if baseline else None,
            "baseline_end": iso(baseline[1]) if baseline else None}


def run_analysis(settings, gateway_factory, start: datetime, end: datetime, *, llm=None, question: str | None = None,
                 mode: str = "analyze", notes: list[str] | None = None, callbacks: list | None = None,
                 run_id: str | None = None, use_llm: bool = True, fixture: bool = False,
                 baseline: tuple[datetime, datetime] | None = None, checkpointer=None, persist_files: bool = True,
                 on_node=None, recursion_limit: int = 25, trace_run_id=None,
                 trace_metadata: dict | None = None, trace_tags: list[str] | None = None,
                 window_source: str = "default", trace_recorder=None) -> dict[str, Any]:
    """Analizi çalıştırır. `checkpointer` verilmezse bellek içi (eval/test); platform PostgresSaver verir.
    `on_node(name, update)` her düğüm bittiğinde çağrılır (ilerleme olayları için).
    Tracing: üstte çalışan bir adım (ör. sohbet turu) varsa analiz onun trace'inin altına yerleşir; yoksa kök trace
    açılır. `trace_run_id` verilirse bu run'ın LangSmith kimliği olur (platformda investigation id → trace linki).
    `window_source`: aralığın kaynağı (user | user_question | scheduler | watch | default) — plan adımında görünür.
    `trace_recorder` (agent_trace.TraceRecorder) verilirse graph `updates` + `debug` modlarıyla stream edilir; node
    State In / Node Update / State Out ve LLM/parser olayları kaydedilir. `on_node` yine yalnız `updates` alır."""
    run_id = run_id or new_run_id("inc" if mode == "watch-incident" else "rep")
    init = initial_state(start, end, run_id=run_id, mode=mode, question=question, notes=notes, use_llm=use_llm,
                         fixture=fixture, baseline=baseline, window_source=window_source)
    meta = {"run_id": run_id, "platform_run_id": run_id, "thread_id": run_id, "mode": mode, "model": getattr(llm, "rca_model_name", None) if use_llm and llm else None,
            "window_start": iso(start), "window_end": iso(end), **(trace_metadata or {})}
    meta = {k: v for k, v in meta.items() if v is not None}
    cfg = child_config(callbacks, f"airlab-{mode}", configurable={"thread_id": run_id}, metadata=meta,
                       tags=["airlab", f"mode:{mode}", *(trace_tags or [])], recursion_limit=recursion_limit)
    if trace_run_id is not None:
        cfg["run_id"] = trace_run_id
    if trace_recorder is not None:
        from .agent_trace import with_callback
        cfg = with_callback(cfg, trace_recorder)

    def _go(saver):
        graph = build_graph(settings, gateway_factory, llm, saver, persist_files=persist_files)
        if trace_recorder is not None:
            from .agent_trace import ACTIVE, STREAM_MODES
            token = ACTIVE.set(trace_recorder)
            try:
                for mode, chunk in graph.stream(init, cfg, stream_mode=STREAM_MODES):
                    trace_recorder.on_stream(mode, chunk)
                    if mode == "updates" and on_node is not None:
                        for name, update in chunk.items():
                            on_node(name, update or {})
            finally:
                ACTIVE.reset(token)
            return graph.get_state(cfg).values
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
    from langgraph.checkpoint.memory import InMemorySaver      # checkpointer verilmezse (eval, test) kalıcı değil
    if persist_files:
        settings.ensure_dirs()
    return _go(InMemorySaver())
