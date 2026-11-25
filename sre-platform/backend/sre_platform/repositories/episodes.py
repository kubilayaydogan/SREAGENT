"""Episodic memory deposu.

Episode = bir investigation'ın özeti: ne görüldü (deterministik belirti parmak izi), ne düşünüldü (mechanism +
hipotez), ne çıktı (feedback'ten türetilen outcome + bağlı incident'ın çözümü). `refresh()` idempotenttir ve
kaynak kayıt her değiştiğinde çağrılır (investigation tamamlandı, feedback geldi, incident bağlandı/çözüldü).

Benzerlik soru metnine değil parmak izine dayanır; farklı kelimelerle anlatılan aynı tablo yine eşleşir.
Benzerlik bir ipucudur, aynı Root Cause'un evidence'ı değildir.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact_text

from ..db.models import (AgentEpisode, AgentFeedback, Incident, IncidentInvestigation, IncidentStatus, Investigation,
                         InvestigationStatus, Verdict)
from .base import ts_match, ts_rank

log = logging.getLogger("sre.episodes")
DONE = (InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL)
OUTCOMES = ("unverified", "confirmed", "partially_confirmed", "rejected")
MIN_SIMILARITY = 0.35
MAX_LOCK_PAIRS = 5


def _t(s: str | None, n: int) -> str | None:
    if not s:
        return None
    s = redact_text(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- parmak izi
def fingerprint(report: dict[str, Any] | None) -> dict[str, Any]:
    """Rapordan deterministik belirti etiketleri. Aynı rapor → aynı etiketler (sıralı, tekrarsız)."""
    report = report or {}
    tags: set[str] = set()
    for app, imp in (report.get("impacted") or {}).items():
        for r in (imp or {}).get("reasons") or []:
            r = str(r).lower()
            if r.startswith("latency"):
                tags.add(f"{app}:latency")
            elif r.startswith("5xx") or "error" in r:
                tags.add(f"{app}:errors")
            elif r.startswith("pool"):
                tags.add(f"{app}:pool_timeout")
    caus = report.get("causality") or {}
    for app in caus.get("source_apps") or []:
        tags.add(f"source:{app}")
    mechs = [m.get("id") for m in report.get("mechanisms") or [] if isinstance(m, dict) and m.get("id")]
    for m in report.get("mechanisms") or []:
        pairs = sorted((p for p in (m or {}).get("edge_pairs") or []
                        if p.get("blocker_app") and p.get("waiter_app") and p["blocker_app"] != p["waiter_app"]),
                       key=lambda p: -(p.get("n") or 0))
        for p in pairs[:MAX_LOCK_PAIRS]:
            tags.add(f"lock:{p['blocker_app']}>{p['waiter_app']}")
    return {"symptoms": sorted(tags), "mechanisms": sorted(dict.fromkeys(mechs)),
            "mechanism": caus.get("primary_mechanism")}


def similarity(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, list[str]]:
    """0..1 skor + ortak belirtiler. Belirti örtüşmesi (Jaccard) 0.5, mechanism 0.35, uygulama örtüşmesi 0.15."""
    sa_, sb = set(a.get("symptoms") or []), set(b.get("symptoms") or [])
    shared = sorted(sa_ & sb)
    sym = len(shared) / len(sa_ | sb) if (sa_ | sb) else 0.0
    if a.get("mechanism") and a.get("mechanism") == b.get("mechanism"):
        mech = 1.0
    elif set(a.get("mechanisms") or []) & set(b.get("mechanisms") or []):
        mech = 0.5
    else:
        mech = 0.0
    aa, ab = set(a.get("apps") or []), set(b.get("apps") or [])
    app = len(aa & ab) / len(aa | ab) if (aa | ab) else 0.0
    return round(0.5 * sym + 0.35 * mech + 0.15 * app, 3), shared


# ---------------------------------------------------------------- sonuç
def outcome_of(verdicts: list[Verdict]) -> tuple[str, dict[str, int]]:
    c = {"correct": verdicts.count(Verdict.CORRECT), "partial": verdicts.count(Verdict.PARTIALLY_CORRECT),
         "incorrect": verdicts.count(Verdict.INCORRECT)}
    if not verdicts:
        return "unverified", c
    if c["incorrect"] > c["correct"] + c["partial"]:
        return "rejected", c
    if c["correct"] >= c["partial"] and c["correct"] > c["incorrect"]:
        return "confirmed", c
    return "partially_confirmed", c


async def _incident_for(s: AsyncSession, inv_id: uuid.UUID) -> Incident | None:
    """Doğrudan (incidents.investigation_id) ya da bağlantıyla ilişkili en son incident."""
    linked = select(IncidentInvestigation.incident_id).where(IncidentInvestigation.investigation_id == inv_id)
    stmt = (select(Incident).where(or_(Incident.investigation_id == inv_id, Incident.id.in_(linked)))
            .order_by(Incident.detected_at.desc()).limit(1))
    return (await s.execute(stmt)).scalar_one_or_none()


def _resolution(inc: Incident | None) -> str | None:
    if inc is None or not (inc.root_cause or inc.recommended_actions):
        return None
    parts = []
    if inc.root_cause:
        parts.append(f"Root Cause: {inc.root_cause}")
    if inc.recommended_actions:
        parts.append("Aksiyonlar: " + "; ".join(inc.recommended_actions[:5]))
    parts.append(f"Incident durumu: {inc.status.value}")
    return _t(" | ".join(parts), 1200)


# ---------------------------------------------------------------- yazma
async def refresh(s: AsyncSession, inv_id: uuid.UUID, *, inv: Investigation | None = None) -> AgentEpisode | None:
    """Episode'u kaynak kayıtlardan yeniden hesaplar (idempotent). Kayda değer değilse yazmaz / varsa siler."""
    inv = inv or await s.get(Investigation, inv_id)
    if inv is None or inv.status not in DONE or not inv.report:
        return None
    fp = fingerprint(inv.report)
    verdicts = list((await s.execute(select(AgentFeedback.verdict, AgentFeedback.comment)
                                     .where(AgentFeedback.investigation_id == inv.id)
                                     .order_by(AgentFeedback.updated_at.desc()))).all())
    outcome, counts = outcome_of([v for v, _ in verdicts])
    note = next((c for _, c in verdicts if c), None)
    inc = await _incident_for(s, inv.id)
    notable = bool(fp["symptoms"] or fp["mechanism"] or inc is not None or verdicts)
    if not notable:                                   # sağlıklı rutin health check → episode yok
        await s.execute(delete(AgentEpisode).where(AgentEpisode.investigation_id == inv.id))
        return None
    apps = sorted(set(inv.source_apps) | set(inv.affected_apps) | ({inv.application_id} - {None}))
    values = dict(
        investigation_id=inv.id, incident_id=inc.id if inc else None, trigger=inv.trigger.value,
        created_at=inv.created_at or datetime.now(timezone.utc), window_start=inv.window_start,
        window_end=inv.window_end, apps=apps, source_apps=list(inv.source_apps), mechanism=fp["mechanism"],
        mechanisms=fp["mechanisms"], symptoms=fp["symptoms"], severity=inv.severity.value,
        risk_score=inv.risk_score, confidence=inv.confidence.value if inv.confidence else None,
        summary=_t(inv.title, 300) or "investigation", hypothesis=_t(inv.root_cause_hypothesis, 600),
        outcome=outcome, feedback=counts, feedback_note=_t(note, 400), resolution=_resolution(inc),
        resolved_at=inc.resolved_at if inc and inc.status == IncidentStatus.RESOLVED else None)
    stmt = insert(AgentEpisode).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentEpisode.investigation_id],
        set_={k: stmt.excluded[k] for k in values if k != "investigation_id"} | {"updated_at": func.now()})
    await s.execute(stmt)
    await s.flush()
    return await s.get(AgentEpisode, inv.id, populate_existing=True)


async def refresh_for_incident(s: AsyncSession, inc: Incident) -> int:
    ids = {inc.investigation_id} - {None}
    ids |= set((await s.execute(select(IncidentInvestigation.investigation_id)
                                .where(IncidentInvestigation.incident_id == inc.id))).scalars())
    n = 0
    for i in ids:
        n += await refresh(s, i) is not None
    return n


async def safe_refresh(s: AsyncSession, *, inv_id: uuid.UUID | None = None, incident: Incident | None = None) -> None:
    """Kaynak kaydı yazan akışın (incident geçişi, feedback, investigation tamamlanması) bozulmaması için:
    episode hatası savepoint içinde kalır ve loglanır; asıl işlem sürer."""
    try:
        async with s.begin_nested():
            if incident is not None:
                await refresh_for_incident(s, incident)
            elif inv_id is not None:
                await refresh(s, inv_id)
    except Exception as e:  # noqa: BLE001
        log.warning("episode güncellenemedi", extra={"investigation_id": str(inv_id) if inv_id else None,
                                                       "incident_id": str(incident.id) if incident else None,
                                                       "error": f"{type(e).__name__}: {str(e)[:200]}"})


async def backfill(s: AsyncSession, *, limit: int = 5000) -> int:
    """Episode'u olmayan tamamlanmış investigation'lar için (en yeni önce). Tekrar çalıştırmak güvenlidir."""
    has = select(AgentEpisode.investigation_id)
    ids = list((await s.execute(select(Investigation.id).where(Investigation.status.in_(DONE),
                                                               Investigation.report.is_not(None),
                                                               Investigation.id.not_in(has))
                                .order_by(Investigation.created_at.desc()).limit(limit))).scalars())
    n = 0
    for i in ids:
        n += await refresh(s, i) is not None
    return n


# ---------------------------------------------------------------- okuma
def brief(e: AgentEpisode, *, score: float | None = None, shared: list[str] | None = None) -> dict[str, Any]:
    out = {"ref": f"inv:{e.investigation_id}", "at": e.created_at.astimezone(timezone.utc).isoformat(
               timespec="seconds").replace("+00:00", "Z"),
           "summary": e.summary, "mechanism": e.mechanism, "apps": e.apps, "symptoms": e.symptoms,
           "severity": e.severity, "risk_score": float(e.risk_score) if e.risk_score is not None else None,
           "hypothesis": e.hypothesis, "outcome": e.outcome, "feedback": e.feedback,
           "feedback_note": e.feedback_note, "resolution": e.resolution,
           "incident": f"inc:{e.incident_id}" if e.incident_id else None}
    if score is not None:
        out["similarity"] = score
        out["shared_symptoms"] = shared or []
    return out


async def similar(s: AsyncSession, fp: dict[str, Any], *, apps: list[str], exclude_id: uuid.UUID | None = None,
                  since_days: int | None = 180, limit: int = 5) -> list[dict[str, Any]]:
    """Parmak izine göre benzer episode'lar (skor ≥ MIN_SIMILARITY), skora göre; reddedilenler de döner
    (önceki hipotezin yanlış çıktığı bilgisi değerlidir) ama aynı skorda doğrulanmış olan önce gelir."""
    conds = []
    if fp.get("symptoms"):
        conds.append(AgentEpisode.symptoms.overlap(fp["symptoms"]))
    if fp.get("mechanisms"):
        conds.append(AgentEpisode.mechanism.in_(fp["mechanisms"]))
    if apps:
        conds.append(AgentEpisode.apps.overlap(apps))
    if not conds:
        return []
    stmt = select(AgentEpisode).where(or_(*conds))
    if exclude_id:
        stmt = stmt.where(AgentEpisode.investigation_id != exclude_id)
    if since_days:
        stmt = stmt.where(AgentEpisode.created_at >= datetime.now(timezone.utc) - timedelta(days=since_days))
    rows = list((await s.execute(stmt.order_by(AgentEpisode.created_at.desc()).limit(300))).scalars())
    me = {**fp, "apps": apps}
    rank = {"confirmed": 0, "partially_confirmed": 1, "unverified": 2, "rejected": 3}
    scored = []
    for e in rows:
        sc, shared = similarity(me, {"symptoms": e.symptoms, "mechanism": e.mechanism, "mechanisms": e.mechanisms,
                                     "apps": e.apps})
        if sc >= MIN_SIMILARITY:
            scored.append((sc, rank.get(e.outcome, 2), e, shared))
    scored.sort(key=lambda x: (-x[0], x[1], -x[2].created_at.timestamp()))
    return [brief(e, score=sc, shared=sh) for sc, _, e, sh in scored[:limit]]


async def search(s: AsyncSession, *, q: str | None = None, mechanism: str | None = None, apps: list[str] | None = None,
                 since: datetime | None = None, outcomes: list[str] | None = None,
                 limit: int = 5) -> list[dict[str, Any]]:
    """Geçmiş sorularında episode araması (metin / mechanism / uygulama). Önce doğrulanmış ve çözülmüş olanlar."""
    stmt = select(AgentEpisode)
    if mechanism:
        stmt = stmt.where(or_(AgentEpisode.mechanism == mechanism, AgentEpisode.mechanisms.contains([mechanism])))
    if apps:
        stmt = stmt.where(AgentEpisode.apps.overlap(apps))
    if since:
        stmt = stmt.where(AgentEpisode.created_at >= since)
    if outcomes:
        stmt = stmt.where(AgentEpisode.outcome.in_(outcomes))
    if q and not mechanism:
        stmt = stmt.where(ts_match(AgentEpisode.tsv, q)).order_by(ts_rank(AgentEpisode.tsv, q).desc())
    known = func.coalesce(AgentEpisode.resolution, "") != ""
    stmt = stmt.order_by((AgentEpisode.outcome == "confirmed").desc(), known.desc(), AgentEpisode.created_at.desc())
    return [brief(e) for e in (await s.execute(stmt.limit(limit))).scalars()]


async def stats(s: AsyncSession) -> dict[str, Any]:
    rows = (await s.execute(select(AgentEpisode.outcome, func.count()).group_by(AgentEpisode.outcome))).all()
    by = {o: 0 for o in OUTCOMES} | {o: n for o, n in rows}
    resolved = (await s.execute(select(func.count()).where(AgentEpisode.resolution.is_not(None)))).scalar_one()
    return {"total": sum(by.values()), "by_outcome": by, "with_resolution": resolved}
