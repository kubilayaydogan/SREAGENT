"""Merkezi Guardrail Engine: policy çözümleme (kapsam + kalıtım), değerlendirme, karar birleştirme (deny-overrides),
enforcement, fail-safe ve kayıt olayı üretimi.

* Engine bir **snapshot**'tır (policy listesi + yapılandırma, değişmez). Koşu başında bağlanır; koşu sürerken yapılan
  policy değişiklikleri o koşuyu etkilemez (kill switch hariç: o süreç genelinde ayrıca okunur).
* Bir aşamada uygulanabilir tüm policy'ler değerlendirilir; kapsamı daha dar bir policy, global bir policy'yi
  kaldıramaz ya da zayıflatamaz (yalnız ekleyebilir). Birleşik karar en güçlü karardır (terminate > approval >
  block > error > sanitize > redact > warn > pass). Dönüşümler (redact / sanitize) (priority, kapsam, key) sırasıyla
  zincirlenir: deterministik.
* Mandatory policy her koşulda enforce çalışır; snapshot'ta disabled / monitor yazsa bile.
* Evaluator hatası: kategori fail-closed ise (tool, mcp, data_protection, memory, execution, action ve tüm mandatory
  policy'ler) karar `error` + koşu durur; fail-open kategorilerde `error` kaydedilir, koşu devam eder.
* `bound_to` dolu policy'ler başka bir katmanda (Agent Middleware, platform policy, memory validator) uygulanır;
  engine onları tekrar değerlendirmez, yalnız o katmanın kararını `observe` ile kaydeder (duplicate evaluation yok).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import detect
from .evaluators import REGISTRY
from .model import (ACTION_DECISION, FAIL_CLOSED_FIXED, SCOPE_RANK, STRENGTH, EvalContext, Evaluation, Finding,
                    Outcome, Policy, new_id)

log = logging.getLogger("airlab.guardrails")

DEFAULT_CONFIG: dict = {
    "default_mode": "enforce",
    "evaluation_timeout_ms": 50,
    "max_evaluation_retries": 1,
    "retention": {"evaluation_days": 30, "snapshot_days": 7, "audit_days": 365, "test_run_days": 30},
    "masking": {"capture_snapshots": True, "max_snapshot_bytes": 16000},
    "classifier": {"mode": "deterministic"},
    "notifications": {"slack_enabled": False, "min_severity": "critical"},
    "approval_timeout_minutes": 60,
    "fail_mode": {"input": "open", "model_context": "open", "output": "open", "evidence": "open"},
    "kill_switch": {"global": False, "agents": []},
    "record_passed": True,
    "optional_defaults": {"mode": "enforce"},
}


def merged_config(cfg: dict | None) -> dict:
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in (cfg or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def fail_closed(p: Policy, cfg: dict) -> bool:
    if p.mandatory or p.category in FAIL_CLOSED_FIXED:
        return True
    return (cfg.get("fail_mode") or {}).get(p.category, "open") == "closed"


_MARK = "⟦original:"


def safe_snapshot(obj: Any, *, original: bool, limit: int = 16000) -> Any:
    """Kayıt öncesi güvenli kopya: tüm hassas değerler maskelenir (Before görünümü de ham değeri İÇERMEZ). Before'da
    maske `⟦original:tür⟧`, After'da guardrail'in koyduğu `[redacted:tür]` görünür; böylece diff değeri ifşa etmeden
    neyin değiştiğini gösterir. Boyut sınırlıdır."""
    def fn(path, s):
        hits, red, _ = detect.scan(s, None, None, 100_000)
        if not hits:
            return s[:4000] if len(s) > 4000 else None
        if original:
            red = red.replace("[redacted:", _MARK).replace("]", "⟧") if "[redacted:" in red else red
        return red[:4000]
    view = obj.get("_view", obj) if isinstance(obj, dict) else obj
    view = {k: v for k, v in view.items() if not str(k).startswith("_")} if isinstance(view, dict) else view
    data = detect.walk_strings(_jsonable(view), fn)
    raw = json.dumps(data, ensure_ascii=False, default=str)
    if len(raw) > limit:
        return {"_truncated": True, "_original_bytes": len(raw), "preview": raw[:2000]}
    return data


def _jsonable(o: Any, depth: int = 0) -> Any:
    if depth > 10:
        return "…"
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if isinstance(o, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(o.items())[:500]}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in list(o)[:500]]
    return str(o)[:2000]


class Engine:
    def __init__(self, policies: list[Policy], config: dict | None = None, revision: int = 0):
        self.config = merged_config(config)
        self.revision = revision
        self.policies = [p for p in policies if p.status == "active"]
        self.by_key = {p.key: p for p in policies}
        self._by_stage: dict[str, list[Policy]] = {}
        for p in self.policies:
            if p.bound_to or (not p.mandatory and p.mode == "disabled"):
                continue
            if p.type not in REGISTRY:
                continue
            for s in p.stages:
                self._by_stage.setdefault(s, []).append(p)
        for s in self._by_stage:
            self._by_stage[s].sort(key=lambda p: (p.priority, SCOPE_RANK.get(p.scope_level, 99), p.key))

    # ---------------------------------------------------------------- çözümleme
    def applicable(self, stages: tuple[str, ...], ctx: EvalContext) -> list[Policy]:
        seen, out = set(), []
        for s in stages:
            for p in self._by_stage.get(s, []):
                if p.key not in seen and p.applies(ctx):
                    seen.add(p.key)
                    out.append(p)
        out.sort(key=lambda p: (p.priority, SCOPE_RANK.get(p.scope_level, 99), p.key))
        return out

    def effective(self, ctx: EvalContext) -> list[dict]:
        """Seçilen bağlam için geçerli policy'ler, kalıtım seviyesi ve mandatory bilgisi."""
        out = []
        for p in sorted(self.policies, key=lambda p: (SCOPE_RANK.get(p.scope_level, 99), p.priority, p.key)):
            matched = [a for a in p.assignments if a.matches(ctx)]
            if not matched:
                continue
            lvl = min((a.scope for a in matched), key=lambda s: SCOPE_RANK.get(s, 99))
            out.append({"key": p.key, "name": p.name, "category": p.category, "type": p.type, "mandatory": p.mandatory,
                        "mode": p.effective_mode() if p.mode != "disabled" or p.mandatory else "disabled",
                        "action": p.action, "severity": p.severity, "stages": list(p.stages), "inherited_from": lvl,
                        "scope_values": [a.value for a in matched if a.value], "version": p.version,
                        "bound_to": p.bound_to, "priority": p.priority})
        return out

    # ---------------------------------------------------------------- değerlendirme
    def evaluate(self, stages: str | tuple[str, ...], payload: Any, ctx: EvalContext, state: Any = None) -> Outcome:
        stages = (stages,) if isinstance(stages, str) else tuple(stages)
        ctx.stage = stages[0]
        evals: list[Evaluation] = []
        cur = payload
        final = "passed"
        message = None
        approval_id = None
        for p in self.applicable(stages, ctx):
            ev = self._one(p, cur, ctx, state)
            evals.append(ev)
            if ev.enforced:
                if p.action == "fallback" and ev.decision == "sanitized":
                    cur = _fallback(cur, ev.finding.fallback or FALLBACK_TEXT)
                elif ev.decision in ("redacted", "sanitized") and ev.finding.can_transform:
                    cur = ev.finding.transformed
                elif ev.decision == "sanitized" and ev.finding.fallback is not None:
                    cur = _fallback(cur, ev.finding.fallback)
                elif ev.decision in ("redacted", "sanitized") and not ev.finding.can_transform:
                    # Dönüştürülemeyen içerik: güvenli taraf (mandatory / fail-closed) → engelle
                    if fail_closed(p, self.config):
                        ev.decision = "blocked"
                        ev.finding.reason += " (dönüştürülemedi; engellendi)"
                if STRENGTH[ev.decision] > STRENGTH[final]:
                    final = ev.decision
                    if ev.decision in ("blocked", "approval_required", "terminated", "error"):
                        message = f"{p.name}: {ev.finding.reason or ev.error or ev.decision}"
                        approval_id = ev.approval_id or approval_id
            if state is not None:
                state.record(ev, ctx, payload, cur)
        if final == "error":
            # error yalnız fail-closed policy'den enforced gelir → durdur
            final = "blocked"
        return Outcome(stages[0], final, cur, evals, message, approval_id)

    def _one(self, p: Policy, payload: Any, ctx: EvalContext, state: Any) -> Evaluation:
        ev_impl = REGISTRY[p.type]
        mode = p.effective_mode()
        t0 = time.perf_counter()
        err = None
        finding: Finding | None = None
        tries = 1 + max(0, int(self.config.get("max_evaluation_retries", 1)))
        for _ in range(tries):
            try:
                params = ev_impl.Params(**(p.params or {}))
            except Exception as e:  # noqa: BLE001 — bozuk parametre: kod varsayılanı (güvenli)
                log.warning("guardrail parametre hatası %s: %s", p.key, type(e).__name__)
                params = ev_impl.Params()
            try:
                finding = ev_impl.evaluate(payload, ctx, params, state)
                err = None
                break
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {str(e)[:200]}"
        ms = (time.perf_counter() - t0) * 1000
        if finding is None:
            closed = fail_closed(p, self.config)
            f = Finding(closed, f"evaluator hatası ({'fail-closed' if closed else 'fail-open'})", "evaluation_error")
            return Evaluation(new_id(), p, ctx.stage or "", "error" if closed else "error",
                              enforced=closed and mode == "enforce", finding=f, duration_ms=ms, error=err)
        if ms > float(self.config.get("evaluation_timeout_ms", 50)) and err is None:
            err = "timeout_exceeded"                  # sonuç geçerli; süre sınırı aşıldı (sağlık metric'i)
        if not finding.matched:
            return Evaluation(new_id(), p, ctx.stage or "", "passed", enforced=mode == "enforce", finding=finding,
                              duration_ms=ms, error=err)
        decision = ACTION_DECISION.get(p.action, "blocked")
        ev = Evaluation(new_id(), p, ctx.stage or "", decision, enforced=mode == "enforce", finding=finding,
                        duration_ms=ms, error=err)
        if decision == "approval_required" and ev.enforced:
            ev.approval_id = new_id()
        return ev


FALLBACK_TEXT = "Bu çıktı bir güvenlik policy'si nedeniyle gösterilmedi."


def _fallback(payload: Any, text: str) -> Any:
    if isinstance(payload, dict):
        return {**payload, "text": text}
    return text
