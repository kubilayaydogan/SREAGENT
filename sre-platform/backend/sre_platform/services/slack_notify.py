"""Yüksek riskli RCA'lar için Slack bildirimi (Incoming Webhook).

Kural: araştırma tamamlanmış, risk_score >= SRE_SLACK_RISK_THRESHOLD ve SRE_SLACK_WEBHOOK_URL tanımlıysa tek mesaj
gönderilir (PARTIAL raporlar "kısmi veri" notuyla). Başarıyla gönderilmiş bir araştırma için ikinci kez gönderilmez
(agent_tool_executions'ta başarılı `slack:notify` kaydı); başarısız deneme sonraki tetiklemede tekrar denenir. Bildirim
hatası araştırmayı etkilemez; sonuç araç kaydına yazılır, webhook adresi hiçbir yere yazılmaz.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from airlab_agent.redact import redact_text
from airlab_agent.tracing import ToolSpan, parent_config

from ..db.models import Investigation, InvestigationStatus, ToolExecution, ToolStatus
from ..db.session import transaction
from ..repositories import investigations

log = logging.getLogger("sre.slack")
TOOL = "slack:notify"
LOCAL = ZoneInfo("Europe/Istanbul")
MAX_TEXT = 2500          # Slack section bloğu 3000 karakter sınırı; altında kal


def to_mrkdwn(md: str) -> str:
    """Markdown → Slack mrkdwn (kalın, başlık, link)."""
    s = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", md, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", s)
    return s


def _summary(text: str) -> str:
    t = to_mrkdwn(redact_text(text or "", "notify").strip())
    if len(t) <= MAX_TEXT:
        return t
    cut = t.rfind("\n\n", 0, MAX_TEXT)
    return t[: cut if cut > MAX_TEXT // 2 else MAX_TEXT].rstrip() + "\n…"


def build_message(inv: Investigation, public_url: str) -> dict:
    score = float(inv.risk_score)
    sev = inv.severity.value if inv.severity else "unknown"
    ws, we = inv.window_start.astimezone(LOCAL), inv.window_end.astimezone(LOCAL)
    window = f"{ws:%Y-%m-%d %H:%M}–{we:%H:%M} İstanbul"
    src = ", ".join(inv.source_apps or []) or "—"
    aff = ", ".join(inv.affected_apps or []) or "—"
    link = f"{public_url}/investigations/{inv.id}"
    partial = " — kısmi veri" if inv.status == InvestigationStatus.PARTIAL else ""
    head = f":rotating_light: RCA — risk {score:.0f} ({sev}){partial}"
    fields = [f"*Mechanism:*\n{inv.primary_mechanism or '—'}", f"*Risk score:*\n{score:.1f} ({sev})",
              f"*Kaynak:*\n{src}", f"*Etkilenen:*\n{aff}", f"*Pencere:*\n{window}",
              f"*Confidence:*\n{inv.confidence.value if inv.confidence else '—'}"]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": head[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{inv.title[:250]}*"}},
        {"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields]},
    ]
    if inv.explanation:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _summary(inv.explanation)}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   f"<{link}|Araştırmayı aç> · kaynak: {inv.explanation_source or '—'}"}]})
    return {"text": f"{head}: {inv.title[:200]} ({link})", "blocks": blocks}


def should_notify(inv: Investigation, threshold: float) -> bool:
    return (inv.status in (InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL) and inv.risk_score is not None
            and float(inv.risk_score) >= threshold)


async def _post(url: str, payload: dict, attempts: int = 3) -> None:
    from ..settings import netguard
    await netguard.check_runtime(url)               # P0.5: her gönderimde SSRF kontrolü (redirect takip edilmez)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
        for i in range(attempts):
            try:
                r = await c.post(url, json=payload)
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}"
            else:
                if r.status_code == 200:
                    return
                err = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code != 429 and r.status_code < 500:
                    raise RuntimeError(err)          # 4xx (ör. geçersiz webhook) tekrar denenmez
                if r.status_code == 429:
                    await asyncio.sleep(min(float(r.headers.get("retry-after", "1")), 10))
                    continue
            if i < attempts - 1:
                await asyncio.sleep(2 ** i)
        raise RuntimeError(err)


async def notify_rca(rt, inv_id: uuid.UUID) -> bool:
    """Koşullar sağlanıyorsa Slack'e gönderir. Gönderildiyse True. Asla exception fırlatmaz."""
    ps = rt.ps
    try:
        from ..settings import secrets, service
        async with transaction(rt.sm) as s:
            cfg = (await service.effective(rt, s))["notifications"]      # Settings > Notifications (yoksa ortam)
            if not cfg["slack_enabled"]:
                return False
            try:
                url = await secrets.resolve(s, cfg["slack_webhook_ref"])
            except secrets.SecretError as e:
                log.warning("Slack webhook secret çözülemedi: %s", e)
                return False
            threshold = float(cfg["slack_risk_threshold"])
            inv = await investigations.get(s, inv_id)
            if not should_notify(inv, threshold):
                return False
            sent = await s.scalar(select(ToolExecution.id).where(ToolExecution.investigation_id == inv_id,
                                                                 ToolExecution.tool_name == TOOL,
                                                                 ToolExecution.execution_status == ToolStatus.SUCCEEDED)
                                   .limit(1))
            if sent:
                return False
            payload = build_message(inv, ps.public_url)
            score = float(inv.risk_score)
        t0 = time.monotonic()
        error = None
        span = ToolSpan(parent_config(), TOOL, {"investigation_id": str(inv_id), "risk_score": score,
                                                "threshold": threshold})
        try:
            await _post(url, payload)
        except Exception as e:  # noqa: BLE001
            error = redact_text(str(e), "notify").replace(url, "<webhook>")
            from ..settings.netguard import UnsafeUrl
            if isinstance(e, UnsafeUrl):
                from ..security import audit
                await audit.record_separate(rt.sm, actor="system:notifier", action="egress.denied",
                                            section="notifications", target="slack_webhook", result="denied",
                                            reason_code="ssrf_guard", detail=error)
        if error:
            span.error(error)
        else:
            span.end({"status": "SENT", "elapsed_ms": int((time.monotonic() - t0) * 1000)})
        async with transaction(rt.sm) as s:
            await investigations.record_tool(s, inv_id, TOOL, ok=error is None, step="notify",
                                             arguments={"risk_score": score, "threshold": threshold},
                                             duration_ms=int((time.monotonic() - t0) * 1000), error=error)
        if error:
            log.warning("Slack bildirimi gönderilemedi (%s): %s", inv_id, error)
        return error is None
    except Exception:  # noqa: BLE001 — bildirim araştırmayı asla bozmaz
        log.exception("Slack bildirimi hazırlanamadı (%s)", inv_id)
        return False
