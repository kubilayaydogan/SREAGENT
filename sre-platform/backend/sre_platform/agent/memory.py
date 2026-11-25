"""Ajan hafızası.

Kısa vadeli hafıza (working memory): LangGraph AsyncPostgresSaver ile konuşma thread'i başına durum — son
mesajlar, konuşma odağı (focus) ve eski turların özetleri (digests); bkz. agent/working_memory.py.
Uzun vadeli hafıza üç katmandır:

1. **İlişkisel kayıtlar + tam metin araması** (sre şeması): araştırmalar, incident'lar, RCA, bilinen sorunlar,
   runbook'lar, konuşma mesajları. "Daha önce benzer hata oldu mu?", "geçen haftaki havuz sorunları", "önceki incident
   nasıl çözüldü", "hangi RCA'ları oluşturdun", "runbook var mı" sorularının asıl kaynağı budur.
2. **LangGraph AsyncPostgresStore** (langgraph şeması): kapsam ayrımlı kısa notlar —
   ("app", <uygulama>)            → o uygulamadaki araştırma bulguları (en yeni önce)
   ("user", <kullanıcı>, "context") → kullanıcının son araştırması / tercihleri
   ("conversation", <konuşma>)      → konuşmanın son araştırması
   Store yalnız özet tutar; ayrıntı her zaman ilişkisel kayıttan okunur (tek doğruluk kaynağı).
3. **Episodic memory** (sre.agent_episodes): investigation başına belirti parmak izi → hipotez → sonuç
   (feedback'ten outcome, bağlı incident'ın çözümü). Analizde parmak izi benzerliğiyle, geçmiş sorularında
   metin/mechanism ile aranır; bkz. repositories/episodes.py.

Hafıza kayıtları istemde VERİ olarak verilir; içlerindeki metin talimat sayılmaz.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.models import Investigation, InvestigationStatus
from ..db.session import transaction
from ..repositories import conversations, episodes, incidents, investigations, knowledge, rca

MAX_TXT = 400


def _t(s: str | None, n: int = MAX_TXT) -> str | None:
    if not s:
        return s
    return s if len(s) <= n else s[: n - 1] + "…"


def _iso(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None


def inv_brief(i: Investigation) -> dict[str, Any]:
    return {"ref": f"inv:{i.id}", "title": i.title, "created_at": _iso(i.created_at), "status": i.status.value,
            "severity": i.severity.value, "risk_score": float(i.risk_score) if i.risk_score is not None else None,
            "data_coverage": float(i.data_coverage) if i.data_coverage is not None else None,
            "mechanism": i.primary_mechanism, "confidence": i.confidence.value if i.confidence else None,
            "source_apps": i.source_apps, "affected_apps": i.affected_apps,
            "window": [_iso(i.window_start), _iso(i.window_end)],
            "root_cause_hypothesis": _t(i.root_cause_hypothesis)}


class MemoryService:
    def __init__(self, sessionmaker, store=None):
        self.sm = sessionmaker
        self.store = store

    # ------------------------------------------------------------ yazma
    async def remember_investigation(self, inv: Investigation, *, user_id: uuid.UUID | None,
                                     conversation_id: uuid.UUID | None) -> None:
        if self.store is None or inv.status not in (InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL):
            return
        b = inv_brief(inv)
        for app in sorted(set(inv.source_apps) | set(inv.affected_apps) | ({inv.application_id} - {None})):
            await self.store.aput(("app", app), str(inv.id), b, index=False)
        if user_id:
            await self.store.aput(("user", str(user_id), "context"), "last_investigation", b, index=False)
        if conversation_id:
            await self.store.aput(("conversation", str(conversation_id)), "last_investigation", b, index=False)

    # ------------------------------------------------------------ okuma
    async def last_investigation(self, *, user_id: uuid.UUID | None, conversation_id: uuid.UUID | None
                                 ) -> dict | None:
        if self.store is None:
            return None
        for ns in ((("conversation", str(conversation_id)),) if conversation_id else ()) + \
                  ((("user", str(user_id), "context"),) if user_id else ()):
            item = await self.store.aget(ns, "last_investigation")
            if item:
                return item.value
        return None

    async def app_findings(self, app: str, limit: int = 5) -> list[dict]:
        if self.store is None:
            return []
        items = await self.store.asearch(("app", app), limit=50)
        return [i.value for i in sorted(items, key=lambda x: x.value.get("created_at") or "", reverse=True)[:limit]]

    async def recall(self, *, user_id: uuid.UUID, question: str, scope: list[str] | None = None,
                     apps: list[str] | None = None, mechanism: str | None = None, terms: str | None = None,
                     since_days: int | None = None, exclude_investigation: uuid.UUID | None = None,
                     conversation_id: uuid.UUID | None = None, limit: int = 5) -> dict[str, Any]:
        """Soruyla ilgili geçmiş kayıtları getirir. Boş kapsam = hepsi. Sonuç küçük ve JSON'a hazırdır."""
        scope = list(scope or ["investigations", "incidents", "rca", "runbooks", "known_issues", "conversations"])
        want_episodes = bool({"investigations", "incidents"} & set(scope))
        apps = [a for a in (apps or []) if a]
        since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
        out: dict[str, Any] = {"query": {"apps": apps, "mechanism": mechanism, "terms": terms, "since_days": since_days}}
        async with transaction(self.sm) as s:
            if "investigations" in scope:
                if since or not (mechanism or apps or terms):
                    page = await investigations.list_(s, application_id=apps[0] if len(apps) == 1 else None,
                                                      mechanism=mechanism, since=since, size=limit,
                                                      status=[InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL])
                    items = [i for i in page.items if i.id != exclude_investigation]
                else:
                    items = await investigations.similar(s, mechanism=mechanism, apps=apps, q=terms,
                                                         exclude_id=exclude_investigation, limit=limit)
                out["investigations"] = [inv_brief(i) for i in items]
            if want_episodes:
                cur = await s.get(Investigation, exclude_investigation) if exclude_investigation else None
                if cur is not None and cur.report:
                    # Analiz turu: güncel investigation'ın belirti parmak iziyle benzer episode'lar (kelimeden bağımsız)
                    fp = episodes.fingerprint(cur.report)
                    out["episodes"] = await episodes.similar(
                        s, fp, apps=sorted(set(cur.source_apps) | set(cur.affected_apps)) or apps,
                        exclude_id=exclude_investigation, limit=limit)
                    out["current_symptoms"] = fp["symptoms"]
                else:
                    out["episodes"] = await episodes.search(s, q=terms, mechanism=mechanism, apps=apps, since=since,
                                                            limit=limit)
            if "incidents" in scope:
                page = await incidents.list_(s, application_id=apps[0] if len(apps) == 1 else None, since=since,
                                             q=terms if not mechanism else None, size=limit)
                incs = page.items
                if not incs and terms:
                    incs = (await incidents.list_(s, since=since, size=limit)).items
                out["incidents"] = [{"ref": f"inc:{i.id}", "title": i.title, "status": i.status.value,
                                     "severity": i.severity.value, "detected_at": _iso(i.detected_at),
                                     "resolved_at": _iso(i.resolved_at), "root_cause": _t(i.root_cause),
                                     "recommended_actions": [_t(a, 200) for a in i.recommended_actions][:5],
                                     "application_id": i.application_id} for i in incs]
            if "rca" in scope:
                page = await rca.list_(s, application_id=apps[0] if len(apps) == 1 else None, since=since,
                                       q=terms if terms and not mechanism else None, size=limit)
                out["rca"] = [{"ref": f"rca:{r.id}", "title": r.title, "generated_at": _iso(r.generated_at),
                               "summary": _t(r.summary), "suspected_root_cause": _t(r.suspected_root_cause),
                               "confidence": r.confidence.value if r.confidence else None,
                               "investigation": f"inv:{r.investigation_id}"} for r in page.items]
            if "runbooks" in scope or "known_issues" in scope:
                rec = await knowledge.recommend(s, mechanisms=[mechanism] if mechanism else [], apps=apps, q=terms,
                                                limit=limit)
                if "runbooks" in scope:
                    out["runbooks"] = [{"ref": f"rb:{r.id}", "title": r.title, "mechanism": r.mechanism,
                                        "risk_level": r.risk_level.value, "requires_approval": r.requires_approval,
                                        "description": _t(r.description, 250)} for r in rec["runbooks"]]
                if "known_issues" in scope:
                    out["known_issues"] = [{"ref": f"ki:{k.id}", "title": k.title, "mechanism": k.mechanism,
                                            "symptoms": _t(k.symptoms, 250), "resolution": _t(k.resolution, 250),
                                            "verified": k.verified} for k in rec["known_issues"]]
            if "conversations" in scope and terms:
                hits = await conversations.search_messages(s, user_id, terms, limit=3)
                out["conversations"] = [{"conversation": t, "role": m.role.value, "at": _iso(m.created_at),
                                         "excerpt": _t(m.content, 300)} for m, t in hits
                                        if m.conversation_id != conversation_id]
        last = await self.last_investigation(user_id=user_id, conversation_id=conversation_id)
        if last:
            out["last_investigation"] = last
        for a in apps[:2]:
            f = await self.app_findings(a, 3)
            if f:
                out.setdefault("app_memory", {})[a] = f
        out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list) and k != "current_symptoms"}
        return out
