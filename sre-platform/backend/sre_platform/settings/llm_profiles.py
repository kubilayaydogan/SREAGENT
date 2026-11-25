"""LLM provider profilleri.

* Doğrulanmış adapter yalnız Anthropic'tir (langchain-anthropic + anthropic SDK). Diğer provider türleri listelenir
  ama "bu deployment'ta adapter yok" olarak işaretlenir ve oluşturulamaz (sahte entegrasyon yok).
* Görevler: "chat" (soru yorumlama / yönlendirme / sohbet cevabı) ve "rca" (explain adımı). Yalnız gerçekten LLM
  kullanan graph düğümleri için profil seçilir. Açık fallback zinciri yoktur; sağlayıcının sunucu tarafı refusal
  fallback'i (aynı sağlayıcı) açıktır. Başka sağlayıcıya sessizce geçilmez.
* Test: `models.retrieve(model)` — anahtarı ve model kimliğini prompt göndermeden, token harcamadan doğrular; API'nin
  döndürdüğü yetenekleri (structured outputs, effort, thinking) ve azami çıktı token'ını kaydeder.
* Bütçe: aylık USD sınırı, LangSmith senkronundaki gerçek maliyetle (sre.llm_runs) koşu başında kontrol edilir;
  aşılmışsa o görev için LLM kullanılmaz (deterministik açıklama) ve bu, koşu notuna yazılır.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact_text

from ..db.models import LlmProfile, LlmRun
from . import netguard, secrets
from .schemas import LLM_PROVIDERS

TASKS = ("chat", "rca")


async def seed(s: AsyncSession, rt) -> None:
    """Ortamdaki Claude yapılandırmasını iki profil olarak bir kez kaydeder. Görev yönlendirmesi boş kalır (= ortam);
    yani tohumlama davranışı DEĞİŞTİRMEZ."""
    ag = rt.agent
    if not ag.anthropic_key:
        return
    if (await s.execute(select(LlmProfile.id).where(LlmProfile.managed == "bootstrap"))).first():
        return
    b = ag.cfg["budgets"]
    s.add_all([
        LlmProfile(name="claude-chat-env", provider="anthropic", model=ag.claude_model,
                   secret_ref="env:ANTHROPIC_API_KEY", timeout_s=int(b["llm_timeout_seconds"]),
                   max_tokens=int(b["llm_max_tokens"]), effort=b.get("llm_effort", "medium"),
                   retries=int(b["llm_retries"]), enabled=True, managed="bootstrap", revision=1),
        LlmProfile(name="claude-rca-env", provider="anthropic", model=ag.claude_rca_model,
                   secret_ref="env:ANTHROPIC_API_KEY", timeout_s=int(b["rca_timeout_seconds"]),
                   max_tokens=int(b["rca_max_tokens"]), effort=b.get("rca_effort", "high"),
                   retries=int(b["llm_retries"]), enabled=True, managed="bootstrap", revision=1),
    ])
    await s.flush()


def month_start() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_cost(s: AsyncSession, model: str) -> Decimal | None:
    v = await s.scalar(select(func.sum(LlmRun.cost_usd)).where(LlmRun.kind == "llm", LlmRun.model == model,
                                                               LlmRun.start_time >= month_start()))
    return v


def out(p: LlmProfile, usage: Decimal | None = None) -> dict[str, Any]:
    lc = p.last_check or {}
    return {"id": str(p.id), "name": p.name, "provider": p.provider,
            "provider_label": LLM_PROVIDERS.get(p.provider, {}).get("label", p.provider), "model": p.model,
            "base_url": p.base_url, "secret": secrets.describe(p.secret_ref), "timeout_s": p.timeout_s,
            "max_tokens": p.max_tokens, "effort": p.effort, "retries": p.retries,
            "monthly_budget_usd": float(p.monthly_budget_usd) if p.monthly_budget_usd is not None else None,
            "month_cost_usd": float(usage) if usage is not None else None, "enabled": p.enabled,
            "verified": bool(lc.get("ok")), "last_check": lc, "managed": p.managed, "revision": p.revision,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def _client(p: LlmProfile, key: str):
    import anthropic
    kw = {"base_url": p.base_url} if p.base_url else {}
    return anthropic.Anthropic(api_key=key, timeout=min(p.timeout_s, 30), max_retries=0, **kw)


def _err(e: BaseException, key: str | None) -> tuple[str, str]:
    msg = redact_text(f"{type(e).__name__}: {e}")
    if key:
        msg = msg.replace(key, "<secret>")
    name = type(e).__name__
    kind = {"AuthenticationError": "auth", "PermissionDeniedError": "auth", "NotFoundError": "model",
            "APITimeoutError": "timeout", "APIConnectionError": "network", "RateLimitError": "rate_limited"
            }.get(name, "error")
    return kind, msg[:500]


async def test(s: AsyncSession, p: LlmProfile) -> dict[str, Any]:
    if not LLM_PROVIDERS.get(p.provider, {}).get("supported"):
        res = {"ok": False, "kind": "unsupported", "message": "bu provider için doğrulanmış adapter yok"}
        p.last_check = {**res, "at": datetime.now(timezone.utc).isoformat()}
        return res
    t0 = time.monotonic()
    key = None
    try:
        if p.base_url:
            await asyncio.to_thread(netguard.check_url, p.base_url)
        key = await secrets.resolve(s, p.secret_ref)
        info = await asyncio.to_thread(lambda: _client(p, key).models.retrieve(p.model))
        caps = info.capabilities.model_dump() if getattr(info, "capabilities", None) else {}
        supported = {k: bool((v or {}).get("supported")) if isinstance(v, dict) else bool(v) for k, v in caps.items()}
        warnings = []
        if info.max_tokens and p.max_tokens > info.max_tokens:
            warnings.append(f"max_tokens ({p.max_tokens}) modelin sınırını ({info.max_tokens}) aşıyor")
        if p.effort and caps and not supported.get("effort", False):
            warnings.append("model effort parametresini desteklemiyor")
        res = {"ok": True, "kind": "ok", "model": info.id, "display_name": info.display_name,
               "model_max_tokens": info.max_tokens, "capabilities": supported,
               # tool use Messages API'nin parçasıdır; structured outputs API'nin bildirdiği yetenektir
               "tool_calling": "Messages API tool use (sağlayıcı)",
               "structured_output": supported.get("structured_outputs"), "warnings": warnings,
               "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    except (netguard.UnsafeUrl, secrets.SecretError) as e:
        res = {"ok": False, "kind": "config", "message": str(e)}
    except Exception as e:  # noqa: BLE001
        kind, msg = _err(e, key)
        res = {"ok": False, "kind": kind, "message": msg, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    at = datetime.now(timezone.utc).isoformat()
    p.last_check = {**res, "at": at, **({"last_success_at": at} if res["ok"] else
                                        {"last_success_at": (p.last_check or {}).get("last_success_at")})}
    await s.flush()
    return res


async def list_models(s: AsyncSession, p: LlmProfile) -> list[dict]:
    if p.base_url:
        await asyncio.to_thread(netguard.check_url, p.base_url)
    key = await secrets.resolve(s, p.secret_ref)

    def go():
        page = _client(p, key).models.list(limit=100)
        return [{"id": m.id, "display_name": m.display_name} for m in page.data]
    return await asyncio.to_thread(go)


async def by_name(s: AsyncSession, name: str | None) -> LlmProfile | None:
    if not name:
        return None
    return (await s.execute(select(LlmProfile).where(LlmProfile.name == name))).scalar_one_or_none()


async def task_profiles(s: AsyncSession, llm_eff: dict) -> tuple[dict[str, dict], dict[str, Any], list[str]]:
    """Etkili yönlendirmeden görev profilleri (secret çözülmüş) + snapshot bilgisi + notlar.
    Bütçesi aşılmış ya da devre dışı profil → o görev için `None` (LLM yok)."""
    profiles, snap, notes = {}, {}, []
    for task in TASKS:
        name = llm_eff.get(f"{task}_profile")
        if not name:
            snap[task] = {"profile": None, "source": "env"}
            continue
        p = await by_name(s, name)
        if p is None or not p.enabled:
            notes.append(f"LLM profili '{name}' ({task}) {'yok' if p is None else 'devre dışı'}; bu görevde LLM kullanılmadı")
            profiles[task] = None
            snap[task] = {"profile": name, "disabled": True}
            continue
        if p.monthly_budget_usd is not None:
            used = await month_cost(s, p.model)
            if used is not None and used >= p.monthly_budget_usd:
                notes.append(f"LLM profili '{name}' aylık bütçesi doldu (${used:.2f} / ${p.monthly_budget_usd}); "
                             f"bu görevde LLM kullanılmadı")
                profiles[task] = None
                snap[task] = {"profile": name, "budget_exhausted": True}
                continue
        try:
            key = await secrets.resolve(s, p.secret_ref)
        except secrets.SecretError as e:
            notes.append(f"LLM profili '{name}': {e}; bu görevde LLM kullanılmadı")
            profiles[task] = None
            snap[task] = {"profile": name, "secret_error": True}
            continue
        if p.base_url:
            try:
                await netguard.check_runtime(p.base_url)        # P0.5: özel base_url her koşu öncesi kontrol
            except netguard.UnsafeUrl as e:
                from ..security import audit
                await audit.record(s, actor="system:agent", action="egress.denied", section="llm", target=name,
                                   result="denied", reason_code="ssrf_guard", detail=str(e))
                notes.append(f"LLM profili '{name}' base_url engellendi ({e}); bu görevde LLM kullanılmadı")
                profiles[task] = None
                snap[task] = {"profile": name, "egress_denied": True}
                continue
        profiles[task] = {"model": p.model, "api_key": key, "base_url": p.base_url, "max_tokens": p.max_tokens,
                          "timeout_s": p.timeout_s, "effort": p.effort, "retries": p.retries}
        snap[task] = {"profile": name, "profile_revision": p.revision, "model": p.model}
    return profiles, snap, notes
