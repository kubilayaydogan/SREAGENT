"""Kanıt ilişkilendirme (spec 8.2): çekirdek analiz bittikten sonra aynı pencere için
  - en yavaş sunucu isteklerinin trace.id / span.id'leri  → TRACE kanıtı (Dynatrace'te izi açmak için referans)
  - Dynatrace Davis problemleri                            → PROBLEM kanıtı
toplanır. Puanlamayı/mekanizma kararını DEĞİŞTİRMEZ; yalnız kanıt ekler. DQL şablonları sabittir (gerçek MCP'de
doğrulandı); kullanıcı girdisi DQL'e girmez — uygulama adları yapılandırmadan gelir.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..db.models import EvidenceType

SLOW_TRACE_LIMIT = 10
PROBLEM_LIMIT = 20


def _f(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def queries(start: datetime, end: datetime, cfg: dict) -> list[tuple[str, str]]:
    rng = f'from:"{_f(start)}", to:"{_f(end)}"'
    apps = [a for a in cfg["apps"] if all(c.isalnum() or c in "-_." for c in a)]
    app_filter = " or ".join(f'spring.application.name == "{a}"' for a in apps) or "false"
    return [
        ("slow_traces",
         f'fetch spans, {rng} | filter span.kind == "server" and ({app_filter}) '
         f'and not startsWith(url.path, "/actuator") and not startsWith(url.path, "/lab") '
         f"| sort duration desc | limit {SLOW_TRACE_LIMIT} "
         f"| fields start_time, trace.id, span.id, app=spring.application.name, endpoint=url.path, "
         f"method=http.request.method, status=http.response.status_code, duration_ms=duration/1000000"),
        ("problems",
         f"fetch dt.davis.problems, {rng} | fields display_id, event.name, event.status, event.category, "
         f"event.start, event.end, affected_entity_ids | limit {PROBLEM_LIMIT}"),
    ]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evidence(results: dict[str, Any], window_end: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tr = results.get("slow_traces")
    for r in (tr.records if tr and tr.status == "OK" else [])[:SLOW_TRACE_LIMIT]:
        tid = str(r.get("trace.id") or "")
        if not tid:
            continue
        d = _num(r.get("duration_ms"))
        out.append({
            "key": f"tr-{tid[:24]}", "type": EvidenceType.TRACE, "signal": "slow_traces", "data_status": "OK",
            "trace_id": tid[:64], "span_id": str(r.get("span.id") or "")[:32] or None,
            "application_id": r.get("app"), "timestamp": r.get("start_time") or window_end.isoformat(),
            "dynatrace_query_id": tr.query_id, "query_reference": tr.dql,
            "content": f"Yavaş istek: {r.get('app')} {r.get('method') or ''} {r.get('endpoint') or ''} "
                       f"{'%.0f ms' % d if d is not None else '? ms'}"
                       + (f" (HTTP {r.get('status')})" if r.get("status") else ""),
            "meta": {"duration_ms": d, "endpoint": r.get("endpoint"), "correlation": "pencerenin en yavaş istekleri"},
        })
    pr = results.get("problems")
    for r in (pr.records if pr and pr.status == "OK" else [])[:PROBLEM_LIMIT]:
        pid = str(r.get("display_id") or r.get("event.name") or "")
        if not pid:
            continue
        out.append({
            "key": f"pb-{pid[:40]}", "type": EvidenceType.PROBLEM, "signal": "problems", "data_status": "OK",
            "entity_id": ",".join(r.get("affected_entity_ids") or [])[:256] or None,
            "dynatrace_query_id": pr.query_id, "query_reference": pr.dql,
            "timestamp": r.get("event.start") or window_end.isoformat(),
            "content": f"Davis problemi {pid}: {r.get('event.name')} ({r.get('event.status')}"
                       + (f", {r.get('event.category')}" if r.get("event.category") else "") + ")",
            "meta": {"start": r.get("event.start"), "end": r.get("event.end")},
        })
    # veri yok / hata durumları da kanıt eksikliği olarak görünür kalsın (sahte kanıt yok)
    for k, res in (("slow_traces", tr), ("problems", pr)):
        if res is not None and res.status != "OK":
            out.append({"key": f"{k}-status", "signal": k, "data_status": res.status,
                        "type": EvidenceType.TRACE if k == "slow_traces" else EvidenceType.PROBLEM,
                        "content": f"{k}: " + {"EMPTY": "kayıt yok", "UNAVAILABLE": "MCP erişilemedi",
                                               "PERMISSION": "yetki hatası", "SKIPPED_BUDGET": "bütçe doldu"}.get(
                                                   res.status, res.status) + (f" — {res.error[:200]}" if res.error else ""),
                        "timestamp": window_end.isoformat(), "query_reference": res.dql, "meta": {}})
    return out
