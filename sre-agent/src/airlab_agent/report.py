"""Rapor: JSON (makine) + Markdown (insan). Risk şiddeti, veri kapsamı ve nedensellik güveni ayrı alanlardır."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .redact import redact



def coverage_summary(quality: dict) -> dict:
    total = len(quality)
    ok = sum(1 for q in quality.values() if q["status"] == "OK" or q.get("true_zero"))
    gaps = {k: q["status"] for k, q in quality.items() if q["status"] != "OK" and not q.get("true_zero")}
    return {"signals_ok": ok, "signals_total": total, "ratio": round(ok / total, 3) if total else 0.0, "gaps": gaps}


def human_review(attr: dict, quality: dict, scores: dict) -> list[str]:
    out = []
    for m in attr.get("mechanisms", []):
        if m["id"] == "row_lock":
            out.append("Source uygulamanın long-running/idle transaction'larını (blocker_state, xact_age) inceleyin; "
                       "pg_stat_activity'de 'idle in transaction' session'ları ve uygulamanın transaction boundary'lerini doğrulayın.")
        if m["id"] == "pool_saturation" and not m.get("secondary_to"):
            out.append(f"{m['affected_apps'][0]} için connection pool size'ı ve gelen load'un beklenen olup olmadığını teyit edin.")
        if m["id"] == "global_connection_pressure":
            out.append("max_connections / uygulama pool size toplamını ve connection leak olup olmadığını kontrol edin.")
        if m["id"] == "db_cpu":
            out.append("DB_VM'de en pahalı query'lerin execution plan'larını (EXPLAIN) ve maintenance job'larını inceleyin.")
        if m["id"] == "app_vm_cpu":
            if m.get("self_is_source"):
                out.append("CPU artışının bir kısmı SRE Agent'ın kendisinden; watch interval'ını/concurrency'yi gözden geçirin.")
            out.append("APP_VM'de CPU tüketen container'ları doğrulayın; DB tarafı normal mi kontrol edin.")
        if m["id"] == "app_container_cpu":
            out.append(f"{m['source_apps'][0]} container'ında CPU harcayan thread'leri (thread dump, profiler) ve GC "
                       "aktivitesini inceleyin; container CPU limitini ve son deploy'u kontrol edin.")
        if m["id"] == "network_path_latency":
            out.append(f"{m['source_apps'][0]} ile PostgreSQL arasındaki network path'ini doğrulayın (RTT, packet loss, "
                       "tc/qdisc, MTU, DNS); DB server-side süreleri normal olduğu için query tuning çözüm değildir.")
    if scores["overall"]["score"] is None:
        out.append("Risk score hesaplanamadı (data coverage yetersiz): telemetry pipeline'ını (OneAgent, collector, ingest) kontrol edin.")
    if any(q["status"] in ("PERMISSION",) for q in quality.values()):
        out.append("Platform token scope'larını (storage:*:read) kontrol edin; bazı query'ler permission hatası verdi.")
    return out or ["Belirgin bir risk yok; rutin monitoring yeterli."]


def build(state: dict) -> dict:
    attr = state.get("attribution") or {"mechanisms": [], "impacted": {}, "unexplained_impacted": []}
    scores = state.get("scores") or {"apps": {}, "overall": {"score": None, "band": None, "coverage": 0.0}}
    quality = state.get("quality") or {}
    mechs = attr["mechanisms"]
    primary = next((m for m in mechs if not m.get("secondary_to")), None)
    rep = {
        "id": state["run_id"], "kind": state.get("mode", "analyze"), "generated_at": state["generated_at"],
        "status": state.get("status", "complete"), "question": state.get("question"),
        "windows": state.get("windows"), "notes": state.get("notes", []),
        "risk": {"overall": scores["overall"], "apps": {a: {k: v for k, v in s.items()}
                                                         for a, s in scores["apps"].items()}},
        "data_coverage": coverage_summary(quality),
        "causality": {"primary_mechanism": primary["id"] if primary else None,
                      "confidence": primary["confidence"] if primary else None,
                      "source_apps": primary["source_apps"] if primary else [],
                      "affected_apps": primary["affected_apps"] if primary else sorted(attr["impacted"])},
        "impacted": attr["impacted"], "mechanisms": mechs, "alternatives": state.get("alternatives", []),
        "unexplained_impacted": attr.get("unexplained_impacted", []),
        "data_gaps": [f"{k}: {q['status']}" + (f" — {q['detail']}" if q.get("detail") else "")
                      for k, q in quality.items() if q["status"] != "OK" and not q.get("true_zero")],
        "true_zero_signals": sorted(k for k, q in quality.items() if q.get("true_zero")),
        "human_review": human_review(attr, quality, scores),
        "evidence": state.get("evidence", []), "extra_evidence": state.get("extra", {}),
        "explanation": state.get("explanation"), "explanation_source": state.get("explanation_source"),
        "errors": state.get("errors", []), "budget": state.get("budget", {}),
        "features_summary": state.get("features_summary"),
        "fixture": bool(state.get("fixture")),
    }
    return redact(rep)


def _fmt(v: Any, pct=False) -> str:
    if v is None:
        return "veri yok"
    if isinstance(v, float):
        return f"{v:.1%}" if pct else f"{v:.1f}"
    return str(v)


def to_markdown(r: dict) -> str:
    o = r["risk"]["overall"]
    L = []
    if r.get("fixture"):
        L.append("> **FIXTURE / SENTETİK VERİ** — bu rapor test verisiyle üretildi, gerçek bir ölçüm değildir.\n")
    L.append(f"# airlab SRE report `{r['id']}`")
    L.append(f"- Kind: {r['kind']} · Status: **{r['status']}** · Generated: {_stamp(r['generated_at'])}")
    for w in r.get("windows") or []:
        L.append(f"- Window `{w['name']}`: {_stamp(w['start'])} → {_stamp(w['end'])}")
    if r.get("question"):
        L.append(f"- Soru: {r['question']}")
    for n in r.get("notes", []):
        L.append(f"- Not: {n}")
    c = r["causality"]
    L += ["", "## Üç ayrı değerlendirme", "| Boyut | Value |", "|---|---|",
          f"| Risk score (0–100) | {_fmt(o['score'])} ({o.get('band') or 'hesaplanamadı'}) |",
          f"| Data coverage | {r['data_coverage']['signals_ok']}/{r['data_coverage']['signals_total']} signal OK, "
          f"score coverage ort. {_fmt(o.get('coverage'), pct=True)} |",
          f"| Causality confidence | {c['confidence'] or '—'} "
          f"({c['primary_mechanism'] or 'mechanism yok'}) |", ""]
    L += ["## Uygulama risk score'ları", "| Uygulama | Score | Band | Coverage | Latency ratio | Eksik component |", "|---|---|---|---|---|---|"]
    for a, s in r["risk"]["apps"].items():
        comp = s.get("components", {})
        lr, src = comp.get("_latency_ratio"), comp.get("_latency_source")
        lat = f"×{lr:.2f} ({src})" if lr else "veri yok"
        L.append(f"| {a} | {_fmt(s['score'])} | {s.get('band') or '—'} | {_fmt(s['coverage'], pct=True)} | {lat} | "
                 f"{', '.join(s.get('missing', [])) or '—'} |")
    L += ["", "## Source → Etkilenen",
          f"- Source: {', '.join(c['source_apps']) or 'belirlenemedi'}",
          f"- Etkilenen: {', '.join(c['affected_apps']) or 'yok'}"]
    for a, i in r["impacted"].items():
        L.append(f"  - {a}: {'; '.join(i['reasons'])}")
    prim = next((m for m in r["mechanisms"] if m["id"] == c["primary_mechanism"]), None)
    if prim and prim.get("chain_path"):
        L.append(f"- Lock wait chain: {prim['chain_path']} ({prim['chain_depth']} halka)")
    L += ["", "## Mechanism'ler"]
    if not r["mechanisms"]:
        L.append("- Tespit edilen mechanism yok.")
    for m in r["mechanisms"]:
        L.append(f"- **{m['title']}** (`{m['id']}`, confidence: {m['confidence']}"
                 + (f", secondary → {m['secondary_to']}" if m.get("secondary_to") else "") + ")")
        L.append(f"  - Source: {', '.join(m['source_apps']) or '—'} · Etkilenen: {', '.join(m['affected_apps']) or '—'}")
        L.append(f"  - {m['mechanism']}")
        L.append(f"  - Signal'ler: {', '.join(m['signals'])} · Evidence: {', '.join(m['evidence_ids']) or '—'}")
    L += ["", "## Alternatif açıklamalar"] + ([f"- {x}" for x in r["alternatives"]] or ["- —"])
    L += ["", "## Data gap'ler"] + ([f"- {x}" for x in r["data_gaps"]] or ["- Yok"])
    L += ["", "## Önerilen human review"] + [f"- {x}" for x in r["human_review"]]
    if r.get("explanation"):
        L += ["", f"## Açıklama ({r.get('explanation_source')})", r["explanation"]]
    if r.get("errors"):
        L += ["", "## Error'lar"] + [f"- {e}" for e in r["errors"]]
    L += ["", "## Evidence", "| ID | Signal | Status | Record | Dynatrace queryId |", "|---|---|---|---|---|"]
    for e in r["evidence"]:
        L.append(f"| {e['id']} | {e['signal']} | {e['status']} | {e['records']} | {e.get('dynatrace_query_id') or '—'} |")
    b = r.get("budget") or {}
    L += ["", f"_Budget: {b.get('queries_used', 0)} query, LLM {b.get('llm_calls', 0)} call / "
              f"{b.get('input_tokens', 0)}+{b.get('output_tokens', 0)} token, süre {b.get('elapsed_s', 0)} sn._"]
    return "\n".join(L) + "\n"


def _stamp(iso_ts: str | None) -> str:
    from .timefmt import tsi
    return tsi(iso_ts, missing=str(iso_ts or "?"))


def _clock(iso_ts: str | None) -> str:
    """'2026-11-21T20:42:00Z' → '23:42 TSİ'."""
    from .timefmt import tsi
    return tsi(iso_ts, date=False)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " ve " + items[-1]


def deterministic_explanation(r: dict) -> str:
    """Claude yokken kural tabanlı, paragraf düzeninde Türkçe rapor. Yalnız rapordaki ölçülmüş değerleri kullanır."""
    o, c = r["risk"]["overall"], r["causality"]
    win = next((w for w in r.get("windows") or [] if w.get("name") == "window"), None)
    base = next((w for w in r.get("windows") or [] if w.get("name") == "baseline"), None)
    period = (f"{_clock(win['start'])} – {_clock(win['end'])} aralığı" if win else "İncelenen aralık")
    prim = next((m for m in r["mechanisms"] if m["id"] == c["primary_mechanism"]), None)
    paras = []

    # 1) Özet
    if prim:
        summary = (f"{period}, "
                   + (f"{_clock(base['start'])} – {_clock(base['end'])} arasındaki önceki dönemle " if base
                      else "önceki dönemle ")
                   + f"karşılaştırıldı. Tespit edilen mechanism: **{prim['title']}**. "
                   f"Root cause source'u {_join(prim['source_apps']) or 'belirlenemeyen bir uygulama'}, etkilenen "
                   f"uygulamalar {_join(prim['affected_apps']) or 'yok'}. ")
    elif r["impacted"]:
        summary = (f"{period}nda {_join(list(r['impacted']))} uygulamalarında kötüleşme ölçüldü, ancak eldeki "
                   "evidence tek bir mechanism'e bağlanamadı. ")
    else:
        summary = f"{period}nda önceki döneme göre anlamlı bir kötüleşme ölçülmedi. "
    summary += (f"Genel risk score {_fmt(o['score'])}/100 ({o.get('band') or 'hesaplanamadı'}); bu puan şiddeti "
                "gösterir, nedenselliğin kesinliğini değil.")
    paras.append("**Özet.** " + summary)

    # 2) Etki
    if r["impacted"]:
        parts = [f"{a} uygulamasında {', '.join(i['reasons'])}" for a, i in r["impacted"].items()]
        unaffected = [a for a in r["risk"]["apps"] if a not in r["impacted"]]
        txt = "Ölçülen impact şöyle: " + "; ".join(parts) + "."
        if unaffected:
            txt += f" {_join(unaffected)} uygulamalarında önceki döneme göre anlamlı değişiklik görülmedi."
        paras.append("**Impact.** " + txt)

    # 3) Kök neden ve yayılım
    if prim:
        txt = prim["mechanism"]
        if prim.get("chain_path"):
            txt += (f" Lock wait zinciri {prim['chain_path']} biçiminde ({prim['chain_depth']} halka). "
                    + prim.get("cascade", ""))
            for h in prim.get("lock_chain", []):
                if h["level"] > 0:
                    txt += (f" {h['app']}, {_join([w['app'] for w in h['waits_on']])} uygulamasını "
                            f"{int(h['n'])} kez bekledi.")
        if prim.get("edges_total"):
            txt += f" Bu aralıkta toplam {int(prim['edges_total'])} blocking edge kaydı var."
        secondary = [m["title"] for m in r["mechanisms"] if m.get("secondary_to") == prim["id"]]
        if secondary:
            txt += f" Buna bağlı ikincil etkiler: {_join(secondary)}."
        paras.append("**Root cause ve yayılım.** " + txt.strip())

    # 4) Kanıt ve güven
    dc = r["data_coverage"]
    txt = (f"Değerlendirme {dc['signals_ok']}/{dc['signals_total']} signal ile yapıldı. Causality confidence "
           f"{c['confidence'] or '—'}")
    if prim:
        txt += f"; mechanism'i destekleyen signal'ler: {', '.join(prim['signals'])}"
        if prim.get("evidence_ids"):
            txt += f" (evidence: {', '.join(prim['evidence_ids'])})"
    txt += "."
    if r["data_gaps"]:
        txt += f" Data gap'ler: {'; '.join(r['data_gaps'])}. Eksik veri sıfır olarak yorumlanmadı."
    if r.get("alternatives"):
        txt += " Dışlanamayan alternatif açıklamalar: " + " ".join(r["alternatives"])
    paras.append("**Evidence ve confidence.** " + txt)

    # 5) Öneri
    paras.append("**Önerilen inceleme.** " + " ".join(r["human_review"])
                 + " Ajan salt-okunurdur; değişiklik gerektiren adımlar onay akışından geçmelidir.")
    return "\n\n".join(paras)


def save(r: dict, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    j, m = reports_dir / f"{r['id']}.json", reports_dir / f"{r['id']}.md"
    j.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    m.write_text(to_markdown(r), encoding="utf-8")
    return j, m


def list_reports(reports_dir: Path, limit: int = 20) -> list[dict]:
    out = []
    for p in sorted(reports_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": r["id"], "kind": r["kind"], "status": r["status"], "generated_at": r["generated_at"],
                        "score": r["risk"]["overall"]["score"], "primary": r["causality"]["primary_mechanism"]})
        except Exception:  # noqa: BLE001
            continue
    return out
