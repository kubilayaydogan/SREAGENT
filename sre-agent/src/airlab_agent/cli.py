"""sre-agent CLI: doctor | analyze | ask | chat | watch | watch-status | report list/show"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

from .config import load_settings
from .graph import run_analysis
from .llm import AnalysisRequest, Llm, LlmUnavailable, heuristic_request, resolve_window
from .mcp_gateway import DQL_TOOL, McpGateway, McpUnavailable
from .redact import register_secrets
from .report import list_reports, to_markdown
from .signals import REQUIRED, Window, build_queries, iso
from .tracing import langsmith_ping, setup_tracing


def _ctx(no_llm: bool = False):
    s = load_settings()
    register_secrets(s.secrets())
    s.ensure_dirs()
    cbs = setup_tracing(s)
    b = s.cfg["budgets"]
    gwf = partial(McpGateway, s.mcp_url, s.dt_token, timeout_s=b["query_timeout_seconds"],
                  retries=b["query_retries"], max_queries=b["max_queries_per_run"])
    llm = None
    if s.llm_enabled and not no_llm:
        try:
            llm = Llm(s, cbs)
        except Exception as e:  # noqa: BLE001
            print(f"[uyarı] Claude istemcisi kurulamadı, deterministik mod: {type(e).__name__}", file=sys.stderr)
    return s, gwf, llm, cbs


def _print_result(st: dict, as_json: bool):
    paths = st.get("report_paths") or []
    if as_json and paths:
        print(open(paths[0], encoding="utf-8").read())
        return
    if len(paths) > 1:
        print(open(paths[1], encoding="utf-8").read())
    print(f"\nRapor: {', '.join(paths)}")


# ---------------------------------------------------------------- doctor
def cmd_doctor(a):
    s, gwf, llm, _ = _ctx(a.no_llm)
    ok = True
    print("== Yapılandırma")
    for name, val in [("DT_ENV", s.dt_env), ("DT_MCP_URL", s.mcp_url), ("DT_PLATFORM_TOKEN", s.dt_token),
                      ("ANTHROPIC_API_KEY", s.anthropic_key), ("CLAUDE_MODEL", s.claude_model),
                      ("LANGSMITH_API_KEY", s.langsmith_key)]:
        shown = val if name in ("DT_ENV", "DT_MCP_URL", "CLAUDE_MODEL") else ("tanımlı" if val else "YOK")
        print(f"  {name:20} {shown or 'YOK'}")
    print(f"  {'veri dizini':20} {s.data_dir.resolve()}  (yazılabilir: {_writable(s.data_dir)})")
    print("== Dynatrace MCP")
    try:
        gw = gwf()
        d = gw.discover()
        print(f"  araçlar ({len(d['tools'])}): {', '.join(d['tools'])}")
        if DQL_TOOL not in d["tools"]:
            print(f"  HATA: '{DQL_TOOL}' aracı yok — agent bu sunucuyla çalışamaz")
            return 2
        lag = s.cfg["windows"]["ingest_lag_seconds"]
        end = (datetime.now(timezone.utc) - timedelta(seconds=lag)).replace(second=0, microsecond=0)
        ws = [Window("baseline", end - timedelta(minutes=20), end - timedelta(minutes=10)),
              Window("window", end - timedelta(minutes=10), end)]
        res = gw.run_many(build_queries(ws, s.cfg, REQUIRED))
        for k in REQUIRED:
            r = res[k]
            print(f"  {k:14} {r.status:10} kayıt={len(r.records):<4} {r.elapsed_ms} ms"
                  + (f"  {r.error[:160]}" if r.error else ""))
            ok &= r.status in ("OK", "EMPTY")
    except McpUnavailable as e:
        print(f"  HATA: MCP erişilemiyor: {e}")
        return 2
    print("== Claude")
    if llm is None:
        print("  devre dışı (deterministik açıklama kullanılacak)")
    else:
        try:
            req = llm.interpret("son 20 dakikada checkin servisinde sorun var mı?")
            print(f"  {s.claude_model}: OK (örnek ayrıştırma: {req.model_dump()})")
        except LlmUnavailable as e:
            print(f"  {s.claude_model}: ERİŞİLEMEDİ ({e}) — agent deterministik açıklamayla çalışır")
    print("== LangSmith")
    p, why = langsmith_ping(s)
    print(f"  izleme={'açık' if s.langsmith_tracing else 'kapalı'}  erişim={'OK' if p else 'yok'} ({why})"
          " — LangSmith yoksa agent çalışmaya devam eder")
    print("\nSONUÇ:", "OK" if ok else "UYARI (bazı sinyaller OK/EMPTY değil)")
    return 0 if ok else 1


def _writable(p) -> bool:
    try:
        t = p / f".w{uuid.uuid4().hex[:6]}"
        t.write_text("x")
        t.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- analyze / ask
def _parse_t(x: str) -> datetime:
    return datetime.fromisoformat(x.replace("Z", "+00:00")).astimezone(timezone.utc)


def cmd_analyze(a):
    s, gwf, llm, cbs = _ctx(a.no_llm)
    if a.start:
        req = AnalysisRequest(intent="analyze", start_utc=a.start, end_utc=a.end, minutes=a.last)
    else:
        req = AnalysisRequest(intent="analyze", minutes=a.last)
    start, end, notes = resolve_window(req, s.cfg)
    baseline = None
    if a.baseline_from:
        baseline = (_parse_t(a.baseline_from), _parse_t(a.baseline_to))
        if baseline[1] > start:
            raise SystemExit("baseline pencereden önce bitmeli")
        notes.append("Baseline kullanıcı tarafından verildi.")
    st = run_analysis(s, gwf, start, end, llm=llm, notes=notes, callbacks=cbs, use_llm=llm is not None,
                      baseline=baseline)
    _print_result(st, a.json)
    return 0


def _answer(s, gwf, llm, cbs, question: str, history: list[dict] | None = None, as_json=False) -> str:
    notes = []
    if llm is not None:
        try:
            req = llm.interpret(question, history)
        except LlmUnavailable as e:
            req = heuristic_request(question, s.cfg["apps"])
            notes.append(f"Claude soruyu yorumlayamadı ({e}); deterministik yorum kullanıldı.")
    else:
        req = heuristic_request(question, s.cfg["apps"])
    if req.intent == "smalltalk":
        return "Ben airlab SRE Agent'ıyım; yalnız airlab telemetrisi (Dynatrace MCP) üzerine soruları yanıtlarım."
    if req.intent == "report_followup" and history:
        last = next((h for h in reversed(history) if h["role"] == "assistant"), None)
        if last and llm is not None:
            try:
                return llm.explain({"previous_answer": last["content"]}, question)
            except LlmUnavailable:
                pass
    if req.focus_apps:
        notes.append(f"Soruda odaklanılan uygulamalar: {', '.join(req.focus_apps)} (tüm uygulamalar yine analiz edildi).")
    start, end, wnotes = resolve_window(req, s.cfg)
    st = run_analysis(s, gwf, start, end, llm=llm, question=question, mode="ask", notes=notes + wnotes,
                      callbacks=cbs, use_llm=llm is not None)
    paths = st.get("report_paths") or []
    if as_json:
        return open(paths[0], encoding="utf-8").read()
    head = f"[{st['status']}] pencere {st['start']} → {st['end']} · rapor `{st['run_id']}`\n\n"
    return head + (st.get("explanation") or "") + f"\n\n(Ayrıntı: sre-agent report show {st['run_id']})"


def cmd_ask(a):
    s, gwf, llm, cbs = _ctx(a.no_llm)
    print(_answer(s, gwf, llm, cbs, a.question, as_json=a.json))
    return 0


def cmd_chat(a):
    from .watch import StateStore
    s, gwf, llm, cbs = _ctx(a.no_llm)
    store = StateStore(s.state_db)
    thread = a.thread or f"chat-{uuid.uuid4().hex[:8]}"
    hist = [{"role": r, "content": c} for r, c in
            store.db.execute("SELECT role, content FROM chat WHERE thread=? ORDER BY ts", (thread,))]
    print(f"airlab SRE Agent sohbet (oturum: {thread}{', ' + str(len(hist)) + ' mesaj yüklendi' if hist else ''}). "
          "Çıkış: /q")
    while True:
        try:
            q = input("\nsoru> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q in ("/q", "/quit", "exit"):
            break
        if not q:
            continue
        try:
            ans = _answer(s, gwf, llm, cbs, q, hist)
        except Exception as e:  # noqa: BLE001
            ans = f"Hata: {type(e).__name__}: {e}"
        print("\n" + ans)
        for role, content in (("human", q), ("assistant", ans)):
            hist.append({"role": role, "content": content})
            store.db.execute("INSERT INTO chat VALUES(?,?,?,?)", (thread, iso(datetime.now(timezone.utc)), role, content))
        store.db.commit()
    print(f"\nOturum kaydedildi: --thread {thread}")
    return 0


# ---------------------------------------------------------------- watch
def cmd_watch(a):
    from .watch import Watcher
    s, gwf, llm, cbs = _ctx(a.no_llm)
    analyze_fn = partial(run_analysis, s, gwf, llm=llm, callbacks=cbs)

    def af(**kw):
        kw["use_llm"] = kw.get("use_llm", True) and llm is not None
        return analyze_fn(**kw)
    w = Watcher(s, gwf, af)
    if a.once:
        print(json.dumps(w.tick(), ensure_ascii=False, indent=2, default=str))
        return 0
    w.run_forever()
    return 0


def cmd_watch_status(a):
    from .watch import StateStore, Watcher
    s = load_settings()
    store = StateStore(s.state_db)
    w = Watcher(s, None, None, store)
    print(json.dumps(w.status(), ensure_ascii=False, indent=2, default=str))
    rows = store.db.execute("SELECT ts, kind, key, detail FROM events ORDER BY ts DESC LIMIT ?", (a.events,)).fetchall()
    for ts, kind, key, detail in reversed(rows):
        print(f"{ts}  {kind:20} {key:28} {detail[:160]}")
    return 0


# ---------------------------------------------------------------- report
def cmd_report(a):
    s = load_settings()
    if a.action == "list":
        for r in list_reports(s.reports_dir, a.limit):
            print(f"{r['id']:40} {r['kind']:16} {r['status']:9} puan={r['score']!s:6} {r['primary'] or '—'}")
        return 0
    p = s.reports_dir / f"{a.id}.json"
    if not p.exists():
        print(f"rapor yok: {a.id}", file=sys.stderr)
        return 1
    if a.json:
        print(p.read_text(encoding="utf-8"))
    else:
        print(to_markdown(json.loads(p.read_text(encoding="utf-8"))))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "mcp", "langchain_mcp_adapters", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="sre-agent", description="airlab SRE Agent (Dynatrace MCP, salt-okur)")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--no-llm", action="store_true"); d.set_defaults(fn=cmd_doctor)
    an = sub.add_parser("analyze", help="zaman penceresi analizi")
    an.add_argument("--last", type=int, help="son N dakika (varsayılan yapılandırmadan)")
    an.add_argument("--from", dest="start"); an.add_argument("--to", dest="end")
    an.add_argument("--baseline-from"); an.add_argument("--baseline-to")
    an.add_argument("--no-llm", action="store_true"); an.add_argument("--json", action="store_true")
    an.set_defaults(fn=cmd_analyze)
    ak = sub.add_parser("ask"); ak.add_argument("question")
    ak.add_argument("--no-llm", action="store_true"); ak.add_argument("--json", action="store_true")
    ak.set_defaults(fn=cmd_ask)
    ch = sub.add_parser("chat"); ch.add_argument("--thread"); ch.add_argument("--no-llm", action="store_true")
    ch.set_defaults(fn=cmd_chat)
    wa = sub.add_parser("watch"); wa.add_argument("--once", action="store_true")
    wa.add_argument("--no-llm", action="store_true"); wa.set_defaults(fn=cmd_watch)
    ws = sub.add_parser("watch-status"); ws.add_argument("--events", type=int, default=20); ws.set_defaults(fn=cmd_watch_status)
    rp = sub.add_parser("report"); rp.add_argument("action", choices=["list", "show"]); rp.add_argument("id", nargs="?")
    rp.add_argument("--limit", type=int, default=20); rp.add_argument("--json", action="store_true")
    rp.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    if a.cmd == "analyze" and a.start and not a.end:
        p.error("--from ile birlikte --to verin")
    if a.cmd == "analyze" and bool(a.baseline_from) != bool(a.baseline_to):
        p.error("--baseline-from ve --baseline-to birlikte verilmeli")
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
