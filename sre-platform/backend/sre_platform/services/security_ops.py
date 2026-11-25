"""Güvenlik operasyonları (P2.3): SIEM iletimi ve güvenlik alarm kuralları. Worker işi `security-ops` (60 sn).

SIEM iletimi (outbox = sre.audit_events + cursor):
* Audit append-only olduğu için ayrı outbox tablosu yoktur; `siem.cursor` son gönderilen audit id'sidir.
* At-least-once: batch 2xx ile kabul edilmeden cursor ilerlemez. Alıcı `id` ile tekrarı ayıklayabilir.
* İlk açılışta geçmiş gönderilmez (cursor o anki son id'ye konur); kapalıyken cursor dondurulmaz, yeniden
  açıldığında kaldığı yerden devam eder.
* Her gönderimde SSRF kontrolü (netguard), redirect izlenmez. İsteğe bağlı HMAC-SHA256 imzası (X-SRE-Signature).
* Olaylar audit'e yazılırken zaten redakte edilmiştir (DLP sink "audit"); gönderimde tekrar "export" sink'i uygulanır.
* Teslim hatası her turda değil, yalnız durum değişiminde audit'lenir (siem.delivery_failed / siem.delivery_recovered).

Secret hijyeni (P2.4): süresi dolmuş / 14 gün içinde dolacak / rotation süresi geçmiş saklanan secret varsa günde en
çok bir `security.alert` (reason_code=secret_hygiene). Süresi dolmuş secret zaten çözülmez (settings/secrets.py).

Alarm kuralları (kodda tanımlı, eşik ve pencere Settings › Notifications):
* Pencere içinde eşik aşılırsa `security.alert` (result=alert, reason_code=kural) audit'e yazılır ve Slack webhook
  tanımlıysa Slack'e gönderilir. Aynı kural pencere süresince bir kez alarm üretir (cooldown).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from airlab_agent.redact import redact
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditEvent, SecurityState
from ..db.session import transaction

log = logging.getLogger("sre.security_ops")
SOURCE = "airlab-sre-platform"
MAX_BATCHES_PER_TICK = 5
HYGIENE_COOLDOWN = timedelta(hours=24)        # secret hijyeni alarmı günde en çok bir kez
ACTOR = "system:security"

# (kural, etiket, eylemler, yalnız başarısız mı, threshold alanı | sabit threshold)
RULES: tuple[tuple[str, str, tuple[str, ...], bool, str | int], ...] = (
    ("login_failures", "Başarısız / engellenen giriş", ("auth.login",), True, "alert_login_failures"),
    ("authz_denied", "Yetki reddi (403)", ("authz.denied",), True, "alert_authz_denied"),
    ("apikey_rejected", "Reddedilen API anahtarı", ("auth.apikey.rejected",), True, "alert_apikey_rejected"),
    ("policy_denied", "Policy reddi", ("tool.denied", "run.denied", "egress.denied", "llm.model_denied",
                                       "llm.budget_exceeded"), True, "alert_policy_denied"),
    ("tool_definition_changed", "MCP tool tanımı değişti", ("connector.tool_changed",), False, 1),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_state(s: AsyncSession, key: str, default: Any = None) -> Any:
    row = await s.get(SecurityState, key)
    return default if row is None else row.value


async def set_state(s: AsyncSession, key: str, value: Any) -> None:
    stmt = insert(SecurityState).values(key=key, value=value, updated_at=_now())
    await s.execute(stmt.on_conflict_do_update(index_elements=[SecurityState.key],
                                               set_={"value": value, "updated_at": _now()}))


def event_json(r: AuditEvent) -> dict:
    return redact({"id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "actor_type": r.actor_type,
                   "action": r.action, "section": r.section, "target": r.target, "result": r.result,
                   "reason_code": r.reason_code, "detail": r.detail, "revision": r.revision, "before": r.before,
                   "after": r.after, "source_ip": r.source_ip, "correlation_id": r.correlation_id,
                   "session_id": r.session_id, "run_id": r.run_id}, "export")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _send(url: str, body: bytes, signing: str | None, batch_id: str) -> None:
    from ..settings import netguard
    await netguard.check_runtime(url)
    headers = {"Content-Type": "application/json", "X-SRE-Batch-Id": batch_id, "User-Agent": "sre-platform-siem/1"}
    if signing:
        headers["X-SRE-Signature"] = sign(signing, body)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
        r = await c.post(url, content=body, headers=headers)
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"HTTP {r.status_code}")


async def forward_siem(rt, cfg: dict) -> dict[str, Any]:
    from ..security import audit
    from ..settings import secrets
    if not cfg.get("siem_enabled"):
        return {"siem": "disabled"}
    async with transaction(rt.sm) as s:
        try:
            url = await secrets.resolve(s, cfg["siem_url_ref"])
            signing = await secrets.resolve(s, cfg.get("siem_signing_ref"))
        except secrets.SecretError as e:
            await _fail(rt, f"secret çözülemedi: {e}")
            return {"siem": "error", "error": "secret"}
        cursor = await get_state(s, "siem.cursor")
        if cursor is None:                       # ilk açılış: geçmiş gönderilmez
            cursor = int(await s.scalar(select(func.coalesce(func.max(AuditEvent.id), 0))))
            await set_state(s, "siem.cursor", cursor)
            await set_state(s, "siem.started_at", _now().isoformat())
    sent, size = 0, int(cfg.get("siem_batch_size") or 200)
    for _ in range(MAX_BATCHES_PER_TICK):
        async with transaction(rt.sm) as s:
            rows = (await s.execute(select(AuditEvent).where(AuditEvent.id > cursor).order_by(AuditEvent.id)
                                    .limit(size))).scalars().all()
            events = [event_json(r) for r in rows]
        if not events:
            break
        last = events[-1]["id"]
        body = json.dumps({"source": SOURCE, "sent_at": _now().isoformat(), "events": events},
                          ensure_ascii=False, separators=(",", ":")).encode()
        try:
            await _send(url, body, signing, f"{events[0]['id']}-{last}")
        except Exception as e:  # noqa: BLE001 — cursor ilerlemez, sonraki turda tekrar denenir
            from ..settings.netguard import UnsafeUrl
            msg = f"{type(e).__name__}: {e}"[:300].replace(url, "<siem>")
            if isinstance(e, UnsafeUrl):
                await audit.record_separate(rt.sm, actor=ACTOR, action="egress.denied", section="notifications",
                                            target="siem_webhook", result="denied", reason_code="ssrf_blocked",
                                            detail=msg)
            await _fail(rt, msg)
            return {"siem": "error", "sent": sent, "error": msg}
        cursor = last
        sent += len(events)
        async with transaction(rt.sm) as s:
            await set_state(s, "siem.cursor", cursor)
            await set_state(s, "siem.last_ok", {"ts": _now().isoformat(), "count": len(events), "last_id": last})
            if await get_state(s, "siem.failing"):
                await set_state(s, "siem.failing", False)
                await audit.record(s, actor=ACTOR, action="siem.delivery_recovered", section="notifications")
        if len(events) < size:
            break
    return {"siem": "ok", "sent": sent, "cursor": cursor}


async def _fail(rt, msg: str) -> None:
    from ..security import audit
    async with transaction(rt.sm) as s:
        was = await get_state(s, "siem.failing")
        await set_state(s, "siem.failing", {"since": (was or {}).get("since") or _now().isoformat(), "error": msg})
    if not was:
        await audit.record_separate(rt.sm, actor=ACTOR, action="siem.delivery_failed", section="notifications",
                                    result="error", reason_code="siem_delivery", detail=msg)


async def evaluate_alerts(rt, cfg: dict, *, now: datetime | None = None) -> list[dict]:
    from ..security import audit
    if not cfg.get("security_alerts_enabled"):
        return []
    now = now or _now()
    window = timedelta(minutes=int(cfg["alert_window_minutes"]))
    fired = []
    async with transaction(rt.sm) as s:
        for rule, label, actions, failed_only, thr in RULES:
            threshold = int(cfg[thr]) if isinstance(thr, str) else thr
            q = select(func.count()).select_from(AuditEvent).where(AuditEvent.ts >= now - window,
                                                                   AuditEvent.action.in_(actions))
            if failed_only:
                q = q.where(AuditEvent.result != "ok")
            n = int(await s.scalar(q) or 0)
            if n < threshold:
                continue
            last = await get_state(s, f"alert.{rule}")
            if last and datetime.fromisoformat(last) > now - window:
                continue                                         # cooldown
            await set_state(s, f"alert.{rule}", now.isoformat())
            detail = f"{label}: son {int(window.total_seconds() // 60)} dk'da {n} olay (threshold {threshold})"
            await audit.record(s, actor=ACTOR, action="security.alert", section="security", target=rule,
                               result="alert", reason_code=rule, detail=detail)
            fired.append({"rule": rule, "count": n, "threshold": threshold, "detail": detail})
        hyg = await secret_hygiene(s)
        bad = hyg["expired"] + hyg["expiring"] + hyg["rotation_due"]
        last = await get_state(s, "alert.secret_hygiene")
        if bad and not (last and datetime.fromisoformat(last) > now - HYGIENE_COOLDOWN):
            await set_state(s, "alert.secret_hygiene", now.isoformat())
            parts = [f"{len(hyg[k])} {lb}: {', '.join(hyg[k][:10])}" for k, lb in
                     (("expired", "süresi dolmuş"), ("expiring", "süresi yaklaşan"), ("rotation_due", "rotation süresi geçmiş"))
                     if hyg[k]]
            detail = "Secret hijyeni: " + "; ".join(parts)
            await audit.record(s, actor=ACTOR, action="security.alert", section="security", target="secret_hygiene",
                               result="alert", reason_code="secret_hygiene", detail=detail)
            fired.append({"rule": "secret_hygiene", "count": len(bad), "threshold": 1, "detail": detail})
    for f in fired:
        await _slack(rt, cfg, f)
    return fired


async def secret_hygiene(s: AsyncSession) -> dict[str, list[str]]:
    """Saklanan secret'ların yaşam döngüsü özeti (yalnız adlar; değer yok)."""
    from ..db.models import StoredSecret
    from ..settings.secrets import lifecycle
    out: dict[str, list[str]] = {"expired": [], "expiring": [], "rotation_due": [], "ok": []}
    for r in (await s.execute(select(StoredSecret).order_by(StoredSecret.name))).scalars():
        out[lifecycle(r)["status"]].append(f"secret:{r.name}")
    return out


async def _slack(rt, cfg: dict, alert: dict) -> None:
    ref = cfg.get("slack_webhook_ref")
    if not ref:
        return
    try:
        from ..services.slack_notify import _post
        from ..settings import secrets
        async with transaction(rt.sm) as s:
            url = await secrets.resolve(s, ref)
        await _post(url, {"text": f":rotating_light: [SRE Platform güvenlik alarmı] {alert['detail']} · "
                                  f"{rt.ps.public_url}/settings/security"})
    except Exception as e:  # noqa: BLE001 — alarm audit'te kayıtlı; Slack hatası işi durdurmaz
        log.warning("güvenlik alarmı Slack'e gönderilemedi: %s", type(e).__name__)


async def run(rt, params: dict) -> dict[str, Any]:
    from ..settings import runtime as srt
    cfg = (await srt.effective(rt))["notifications"]
    out = await forward_siem(rt, cfg)
    out["alerts"] = await evaluate_alerts(rt, cfg)
    return out


async def status(s: AsyncSession, cfg: dict) -> dict[str, Any]:
    """Settings › Security ekranı için: gerçekten bilinen durum (sahte başarı yok)."""
    cursor = await get_state(s, "siem.cursor")
    head = int(await s.scalar(select(func.coalesce(func.max(AuditEvent.id), 0))))
    return {
        "siem": {"enabled": bool(cfg.get("siem_enabled")), "signed": bool(cfg.get("siem_signing_ref")),
                 "cursor": cursor, "lag": (head - cursor) if cursor is not None else None,
                 "started_at": await get_state(s, "siem.started_at"), "last_ok": await get_state(s, "siem.last_ok"),
                 "failing": await get_state(s, "siem.failing")},
        "alerts": {"enabled": bool(cfg.get("security_alerts_enabled")),
                   "window_minutes": cfg.get("alert_window_minutes"), "slack": bool(cfg.get("slack_webhook_ref")),
                   "rules": [{"rule": r, "label": lb, "actions": list(a),
                              "threshold": int(cfg[t]) if isinstance(t, str) else t,
                              "last_fired": await get_state(s, f"alert.{r}")} for r, lb, a, _, t in RULES]
                  + [{"rule": "secret_hygiene", "label": "Secret süre sonu / rotation (günde bir)", "actions": [],
                      "threshold": 1, "last_fired": await get_state(s, "alert.secret_hygiene")}]},
        "secrets": {k: v for k, v in (await secret_hygiene(s)).items() if k != "ok"},
    }
