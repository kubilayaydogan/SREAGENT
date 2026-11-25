"""Merkezi policy kararı (AGT-01, plan P1.1).

Tek bir saf karar fonksiyonu (`decide`) ALLOW / DENY / REQUIRE_APPROVAL döndürür; her karar bir `reason_code` ve
`policy_version` taşır. Enforcement noktaları kararı kendileri yeniden yorumlamaz:

  run.start   chat / resume / manuel investigation (API), zamanlanmış health check (worker), incident (watch)
  tool.call   MCP connector + tool (koşu başında gateway kurulurken; gateway yalnız onaylı read-only DQL çağırır)

Fail-closed: karar sırasında beklenmeyen bir hata DENY (`policy_error`) üretir.
REQUIRE_APPROVAL: bugün yazma/yıkıcı yetenekli tool'lar için üretilir; onaylanmış bir isteği yürüten yol olmadığından
enforcement noktaları ALLOW dışındaki her kararı reddeder (yürütme eklendiğinde onay bağlama burada yapılır).

policy_version = kural sürümü + karara giren ayarların özeti (aynı sürüm → aynı girdiye aynı karar).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("sre.policy")
RULES_VERSION = "p1.1"
DQL_TOOL = "execute-dql"


class Outcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason_code: str
    message: str
    policy_version: str
    action: str
    facts: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


def version(inputs: dict) -> str:
    h = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return f"{RULES_VERSION}:{h}"


# ------------------------------------------------------------------------------------------------ kurallar
_RUN_KEYS = ("enabled", "execution_mode", "incident_triggered")


def _run_start(ctx: dict) -> tuple[Outcome, str, str, dict]:
    a = ctx["agent"]
    trigger = ctx["trigger"]
    if trigger not in ("manual", "scheduled", "incident"):
        return Outcome.DENY, "unknown_trigger", f"bilinmeyen tetikleyici: {trigger}", {}
    if not a["enabled"]:
        return Outcome.DENY, "agent_disabled", "Agent ayarlardan devre dışı bırakıldı; yeni investigation başlatılamaz", {}
    mode = a["execution_mode"]
    if trigger == "manual" and mode == "scheduled":
        return (Outcome.DENY, "mode_scheduled_only",
                "Execution mode 'scheduled': kullanıcı başlatmalı investigation kapalı", {})
    if trigger == "scheduled" and mode == "manual":
        return Outcome.DENY, "mode_manual_only", "Execution mode 'manual': zamanlanmış investigation kapalı", {}
    if trigger == "incident" and (mode == "manual" or not a["incident_triggered"]):
        return Outcome.DENY, "incident_trigger_disabled", "incident-triggered investigation kapalı", {}
    return Outcome.ALLOW, "ok", "izin verildi", {}


def _tool_call(ctx: dict) -> tuple[Outcome, str, str, dict]:
    c, tool = ctx["connector"], ctx["tool"]
    if c is None:
        return Outcome.DENY, "no_connector", "analiz veri kaynağı (MCP connector) tanımlı değil", {}
    name = c["name"]
    if not c["enabled"]:
        return Outcome.DENY, "connector_disabled", f"MCP connector '{name}' devre dışı", {}
    if tool not in (c.get("allowed_tools") or []):
        return Outcome.DENY, "tool_not_approved", f"'{name}/{tool}' onaylı tool değil", {}
    if tool in (c.get("needs_review") or []):
        return (Outcome.DENY, "tool_definition_changed",
                f"'{name}/{tool}' tanımı onaydan sonra değişti; yönetici yeniden incelemeli", {})
    meta = (c.get("tools") or {}).get(tool) or {}
    if meta.get("destructive") or meta.get("read_only") is False:
        # Yazma yetenekli tool: insan onayı gerekir; onaylı yürütme yolu olmadığı için çağrılmaz
        return (Outcome.REQUIRE_APPROVAL, "write_capable_tool",
                f"'{name}/{tool}' yazma yetenekli; insan onayı gerekir", {})
    if c.get("provider") == "dynatrace" and tool != DQL_TOOL:
        return Outcome.DENY, "no_adapter", f"'{name}/{tool}' için doğrulanmış adapter yok", {}
    return Outcome.ALLOW, "ok", "izin verildi", {}


_RULES = {"run.start": _run_start, "tool.call": _tool_call}


def _inputs(action: str, ctx: dict) -> dict:
    if action == "run.start":
        return {"trigger": ctx.get("trigger"), "agent": {k: ctx["agent"].get(k) for k in _RUN_KEYS}}
    if action == "tool.call":
        c = ctx.get("connector")
        return {"tool": ctx.get("tool"), "connector": None if c is None else
                {k: c.get(k) for k in ("name", "enabled", "allowed_tools", "provider", "revision", "needs_review")}}
    return {}


def decide(action: str, ctx: dict) -> Decision:
    """Saf karar. Hiçbir zaman exception fırlatmaz: hata → DENY (fail-closed)."""
    try:
        rule = _RULES.get(action)
        if rule is None:
            return Decision(Outcome.DENY, "unknown_action", f"tanımsız policy eylemi: {action}",
                            version({"action": action}), action)
        outcome, reason, msg, facts = rule(ctx)
        return Decision(outcome, reason, msg, version({"action": action, **_inputs(action, ctx)}), action, facts)
    except Exception as e:  # noqa: BLE001
        log.exception("policy değerlendirme hatası", extra={"policy_action": action})
        return Decision(Outcome.DENY, "policy_error", f"policy değerlendirilemedi ({type(e).__name__})",
                        f"{RULES_VERSION}:error", action)


def connector_view(c) -> dict | None:
    """ORM Connector → policy girdisi (secret içermez)."""
    if c is None:
        return None
    caps = c.capabilities or {}
    tools = {t.get("name"): t for t in (caps.get("tools") or []) if isinstance(t, dict)}
    return {"name": c.name, "enabled": c.enabled, "allowed_tools": list(c.allowed_tools or []),
            "provider": c.provider, "revision": c.revision, "tools": tools,
            "needs_review": list(caps.get("needs_review") or [])}


def log_decision(d: Decision, **extra: Any) -> None:
    log.info("policy decision", extra={"policy_action": d.action, "outcome": d.outcome.value,
                                       "reason_code": d.reason_code, "policy_version": d.policy_version, **extra})
