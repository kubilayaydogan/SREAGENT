"""Observer adapter'ları: mevcut memory mekanizmaları DEĞİŞMEDEN gözlemlenir.

* `ObservedMemoryService` — agent/memory.py MemoryService'in alt sınıfı. Okuma/yazma/arama olayları, retrieval
  pipeline kaydı, LangSmith `memory.*` span'leri (allowlist'li özet; içerik gitmez) ve Memory Settings'in
  agent'a etkisi (kategori anahtarları, top_k, similarity threshold, semantic kayıtlar) burada uygulanır.
  Varsayılan ayarlarda `recall` çıktısı üst sınıfınkiyle birebir aynıdır (regresyon testi).
* `ObservedPostgresSaver` / `ObservedAsyncPostgresSaver` — checkpoint yazma/okuma olayları (yalnız kimlikler; veri
  kopyalanmaz). Checkpoint hatası YUTULMAZ: MEMORY_BACKEND_ERROR yazılır ve hata aynen yükseltilir.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select, update

from ..agent.memory import MemoryService
from ..db.models import MemoryRecord
from ..db.session import transaction
from . import context as mctx
from . import events, policy
from .registry import DEFAULT_TENANT, EMBEDDED_AGENT

log = logging.getLogger("sre.memory.observed")

ALL_SCOPE = ["investigations", "incidents", "rca", "runbooks", "known_issues", "conversations"]
SCOPE_TYPE = {"investigations": "episodic", "incidents": "episodic", "rca": "episodic", "runbooks": "procedural",
              "known_issues": "semantic", "conversations": "short_term"}


def effective_cfg() -> dict:
    """Koşu başında bağlanan snapshot (MemCtx.cfg) önceliklidir; yoksa publisher'ın son okuduğu ayar."""
    c = events.current()
    snap = getattr(c, "cfg", None)
    return dict(snap) if snap else dict(events.cfg())


def flag(cfg: dict, name: str, default: bool = True) -> bool:
    return bool(cfg.get("memory_enabled", True)) and bool(cfg.get(name, default))


class ObservedMemoryService(MemoryService):
    # ------------------------------------------------------------ yazma
    async def remember_investigation(self, inv, *, user_id, conversation_id) -> None:
        cfg = effective_cfg()
        if not flag(cfg, "long_term_enabled") or not flag(cfg, "episodic_enabled"):
            events.publish("MEMORY_REJECTED", memory_id=f"inv:{inv.id}", memory_type="episodic",
                           payload={"reason": "long-term / episodic memory kapalı (Settings)"}, status="rejected")
            return
        t0 = time.perf_counter()
        try:
            await super().remember_investigation(inv, user_id=user_id, conversation_id=conversation_id)
        except Exception as e:
            events.publish("MEMORY_BACKEND_ERROR", memory_id=f"inv:{inv.id}", memory_type="episodic",
                           namespace="langgraph.store", status="error", error=e,
                           duration_ms=(time.perf_counter() - t0) * 1000)
            raise
        if self.store is None:
            return
        apps = sorted(set(inv.source_apps) | set(inv.affected_apps) | ({inv.application_id} - {None}))
        ns = [f"app/{a}" for a in apps] + (["user/<id>/context"] if user_id else []) + \
             (["conversation/<id>"] if conversation_id else [])
        events.publish("MEMORY_WRITE", memory_id=f"inv:{inv.id}", memory_type="episodic", namespace="store",
                       duration_ms=(time.perf_counter() - t0) * 1000,
                       payload={"store": "AsyncPostgresStore", "namespaces": ns, "key": str(inv.id),
                                "value_kind": "investigation brief", "status": getattr(inv.status, "value", None)})

    # ------------------------------------------------------------ okuma
    async def last_investigation(self, *, user_id, conversation_id):
        t0 = time.perf_counter()
        res = await super().last_investigation(user_id=user_id, conversation_id=conversation_id)
        events.publish("MEMORY_READ", memory_id=(res or {}).get("ref"), memory_type="episodic",
                       namespace="store:last_investigation", duration_ms=(time.perf_counter() - t0) * 1000,
                       status="ok" if res else "empty", payload={"found": bool(res)})
        return res

    async def app_findings(self, app: str, limit: int = 5) -> list[dict]:
        t0 = time.perf_counter()
        res = await super().app_findings(app, limit)
        events.publish("MEMORY_SEARCH", memory_type="episodic", namespace=f"store:app/{app}",
                       duration_ms=(time.perf_counter() - t0) * 1000, status="ok" if res else "empty",
                       payload={"limit": limit, "returned": len(res), "refs": [r.get("ref") for r in res]})
        return res

    async def semantic_records(self, *, apps: list[str], terms: str | None, mechanism: str | None, limit: int,
                               cfg: dict, agent_id: str = EMBEDDED_AGENT,
                               tenant_id: str = DEFAULT_TENANT) -> tuple[list[dict], list[dict]]:
        """memory_records semantic kayıtları (aktif, süresi dolmamış) → (seçilen, validation ile dışlanan)."""
        now = datetime.now(timezone.utc)
        async with transaction(self.sm) as s:
            q = select(MemoryRecord).where(MemoryRecord.tenant_id == tenant_id, MemoryRecord.agent_id == agent_id,
                                           MemoryRecord.memory_type == "semantic", MemoryRecord.status == "active",
                                           or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now))
            conds = []
            if apps:
                conds.append(MemoryRecord.tags.overlap(apps))
            if mechanism:
                conds.append(MemoryRecord.tags.contains([mechanism]))
            if terms:
                from ..repositories.base import ts_match
                conds.append(ts_match(MemoryRecord.tsv, terms))
            if not conds:
                return [], []
            rows = list((await s.execute(q.where(or_(*conds)).order_by(MemoryRecord.updated_at.desc())
                                         .limit(limit * 4))).scalars())
            sel, exc = [], []
            for r in rows:
                d = {"status": r.status, "verification_status": r.verification_status, "memory_type": r.memory_type,
                     "approval_status": r.approval_status, "source_trust": r.source_trust}
                ok, why = policy.retrievable(d, cfg)
                brief = {"ref": f"mem:{r.id}", "title": r.title, "category": r.category,
                         "content": r.content[:400], "verification_status": r.verification_status,
                         "confidence_score": r.confidence_score, "source": r.source, "version": r.version,
                         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                if ok and len(sel) < limit:
                    sel.append(brief)
                elif not ok:
                    exc.append({"ref": brief["ref"], "title": r.title, "reason": why})
            if sel:
                await s.execute(update(MemoryRecord).where(MemoryRecord.id.in_(
                    [uuid.UUID(x["ref"][4:]) for x in sel])).values(
                    access_count=MemoryRecord.access_count + 1, last_accessed_at=now))
            return sel, exc

    async def recall(self, *, user_id, question: str, scope=None, apps=None, mechanism=None, terms=None,
                     since_days=None, exclude_investigation=None, conversation_id=None, limit: int = 5,
                     origin: str = "agent") -> dict[str, Any]:
        cfg = effective_cfg()
        t0 = time.perf_counter()
        rid = uuid.uuid4()
        req_scope = list(scope or ALL_SCOPE)
        stages: list[dict] = [{"stage": "query", "status": "ok", "output": {
            "question": question, "apps": apps or [], "mechanism": mechanism, "terms": terms,
            "since_days": since_days, "exclude": f"inv:{exclude_investigation}" if exclude_investigation else None}}]
        top_k = int(cfg.get("top_k") or limit)
        threshold = cfg.get("similarity_threshold")
        # --- Namespace Filter: Settings'teki memory türü anahtarları kapsamı daraltır
        allowed = {"episodic": flag(cfg, "episodic_enabled"), "semantic": flag(cfg, "semantic_enabled"),
                   "procedural": flag(cfg, "procedural_enabled"), "short_term": flag(cfg, "short_term_enabled")}
        lt = flag(cfg, "long_term_enabled") and flag(cfg, "memory_retrieval_enabled")
        eff_scope = [k for k in req_scope if lt and allowed.get(SCOPE_TYPE[k], True)]
        removed = [k for k in req_scope if k not in eff_scope]
        stages.append({"stage": "namespace_filter", "status": "ok", "input": {"requested": req_scope},
                       "output": {"scope": eff_scope, "removed": removed,
                                  "reason": None if not removed else "Settings: memory türü / retrieval kapalı"}})
        stages.append({"stage": "metadata_filter", "status": "ok", "output": {
            "apps": apps or [], "mechanism": mechanism, "since_days": since_days,
            "exclude_current_investigation": bool(exclude_investigation),
            "rules": ["archived episode / known issue / runbook hariç", "procedural: yalnız approved",
                      f"episode similarity ≥ {threshold if threshold is not None else 0.35}"]}})
        if not eff_scope and not (lt and allowed["semantic"]):
            out: dict[str, Any] = {"query": {"apps": [a for a in (apps or []) if a], "mechanism": mechanism,
                                             "terms": terms, "since_days": since_days}, "counts": {}}
            stages.append({"stage": "search", "status": "skipped", "output": {"reason": "retrieval kapalı"}})
            self._finish(rid, origin, cfg, stages, out, [], t0, top_k, eff_scope, user_id, question)
            return out
        events.publish("MEMORY_SEARCH", memory_type="mixed", namespace=",".join(eff_scope), retrieval_id=rid,
                       payload={"scope": eff_scope, "top_k": top_k, "strategy": "fts_fingerprint"})
        t1 = time.perf_counter()
        from ..repositories import episodes as ep_repo
        thr_token = ep_repo.THRESHOLD.set(float(threshold)) if threshold is not None else None
        try:
            if eff_scope:
                out = await super().recall(user_id=user_id, question=question, scope=eff_scope, apps=apps,
                                           mechanism=mechanism, terms=terms, since_days=since_days,
                                           exclude_investigation=exclude_investigation,
                                           conversation_id=conversation_id, limit=top_k)
            else:
                out = {"query": {"apps": [a for a in (apps or []) if a], "mechanism": mechanism, "terms": terms,
                                 "since_days": since_days}, "counts": {}}
        except Exception as e:
            events.publish("MEMORY_BACKEND_ERROR", status="error", error=e, retrieval_id=rid,
                           duration_ms=(time.perf_counter() - t1) * 1000)
            stages.append({"stage": "search", "status": "error", "output": {"error": type(e).__name__}})
            self._finish(rid, origin, cfg, stages, {}, [], t0, top_k, eff_scope, user_id, question, error=e)
            raise
        finally:
            if thr_token is not None:
                ep_repo.THRESHOLD.reset(thr_token)
        excluded: list[dict] = []
        if lt and allowed["semantic"]:
            try:
                sem, sem_exc = await self.semantic_records(apps=[a for a in (apps or []) if a], terms=terms,
                                                           mechanism=mechanism, limit=top_k, cfg=cfg)
            except Exception as e:  # noqa: BLE001 — yeni kaynak: hatası mevcut recall'u bozmaz (kontrollü degradation)
                sem, sem_exc = [], []
                events.publish("MEMORY_BACKEND_ERROR", memory_type="semantic", namespace="sre.memory_records",
                               status="error", error=e, retrieval_id=rid)
            if sem:
                out["semantic"] = sem
                out["counts"] = {**out.get("counts", {}), "semantic": len(sem)}
            excluded += sem_exc
        search_ms = (time.perf_counter() - t1) * 1000
        stages.append({"stage": "search", "status": "ok", "duration_ms": round(search_ms, 2), "output": {
            "strategy": "fts_fingerprint", "counts": out.get("counts", {})}})
        ranking = {"episodes": "belirti parmak izi benzerliği (Jaccard tabanlı) ↓, sonra outcome, sonra yenilik",
                   "investigations": "mechanism/uygulama eşleşmesi + tam metin rank (ts_rank) / yenilik",
                   "runbooks": "mechanism eşleşmesi (3) + tam metin (1) puanı", "known_issues": "aynı",
                   "incidents": "yenilik", "rca": "yenilik", "semantic": "güncellenme zamanı"}
        stages.append({"stage": "ranking", "status": "ok", "output": {
            "method": {k: v for k, v in ranking.items() if out.get(k)}, "reranking": "implement edilmedi",
            "similarity": [{"ref": e.get("ref"), "similarity": e.get("similarity")}
                           for e in out.get("episodes") or [] if e.get("similarity") is not None]}})
        stages.append({"stage": "validation", "status": "ok", "output": {
            "excluded": excluded, "notes": ["rejected outcome'lu episode'lar bilinçli olarak döner (hipotezin yanlış "
                                            "çıktığı bilgi değerlidir)"]}})
        sel = mctx.refs(out)
        for r in sel:
            events.publish("MEMORY_RETRIEVED", memory_id=r["ref"], memory_type=r["memory_type"],
                           namespace=r["source"], retrieval_id=rid,
                           payload={"title": r.get("title"), "similarity": r.get("similarity")})
        self._finish(rid, origin, cfg, stages, out, excluded, t0, top_k, eff_scope, user_id, question)
        return out

    def _finish(self, rid, origin, cfg, stages, out, excluded, t0, top_k, scope, user_id, question, error=None):
        pub = events.publisher()
        if pub is None:
            return
        sel = mctx.refs(out)
        dur = (time.perf_counter() - t0) * 1000
        stages.append({"stage": "context_injection", "status": "pending",
                       "output": {"note": "LLM isteği hazırlanınca güncellenir"}})
        pub.retrieval({"id": rid, "origin": origin, "strategy": "fts_fingerprint",
                       "query": {"question": question, **(out.get("query") or {})}, "namespaces": scope,
                       "memory_types": sorted({SCOPE_TYPE[k] for k in scope} | ({"semantic"} if out.get("semantic")
                                                                              else set())),
                       "filters": {k: (out.get("query") or {}).get(k) for k in ("apps", "mechanism", "since_days")},
                       "top_k": top_k, "returned_count": len(sel), "empty": not sel, "selected": sel,
                       "excluded": excluded, "stages": stages, "duration_ms": round(dur, 2),
                       "status": "error" if error else "ok",
                       "error": {"error_type": type(error).__name__} if error else None,
                       "injection": {"status": "pending"},
                       "user_id": user_id if isinstance(user_id, uuid.UUID) else None})
        c = events.current()
        if c.run_id:
            _PENDING[c.run_id] = rid
        # LangSmith span: içerik değil, yalnız sayılar ve kimlikler
        _ls_span("memory.retrieve", {"strategy": "fts_fingerprint", "scope": scope, "top_k": top_k},
                 {"returned": len(sel), "refs": [r["ref"] for r in sel][:50], "duration_ms": round(dur, 1)})


_PENDING: dict[str, uuid.UUID] = {}


def take_pending(run_id: str | None) -> uuid.UUID | None:
    return _PENDING.pop(run_id, None) if run_id else None


def _ls_span(name: str, inp: dict, out: dict) -> None:
    """Graph node'u içindeysek (callback'li config var) LangSmith'e allowlist'li özet span'i gönderir."""
    try:
        from langchain_core.callbacks.manager import CallbackManager
        from langchain_core.runnables.config import var_child_runnable_config
        cfg = var_child_runnable_config.get()
        cbs = (cfg or {}).get("callbacks")
        if not cbs:
            return
        handlers = cbs.handlers if hasattr(cbs, "handlers") else list(cbs)
        from langchain_core.tracers.langchain import LangChainTracer
        ls = [h for h in handlers if isinstance(h, LangChainTracer)]
        if not ls:
            return
        parent = getattr(cbs, "parent_run_id", None)
        cm = CallbackManager(handlers=ls, inheritable_handlers=ls, parent_run_id=parent)
        rm = cm.on_chain_start({"name": name}, {"step_summary": inp}, run_name=name, run_type="chain",
                               tags=["memory"])
        rm.on_chain_end({"step_summary": out})
    except Exception as e:  # noqa: BLE001 — trace hatası memory işlemini etkilemez
        log.debug("memory span gönderilemedi: %s", type(e).__name__)


def record_injection(*, section: str, before: Any, after: Any, formatted: str | None, dropped: list[dict],
                     budget: dict, injected: bool, reason: str | None = None, llm_task: str | None = None,
                     model: str | None = None) -> None:
    """Context Injection Inspector kaydı + MEMORY_CONTEXT_INJECTED / _TRUNCATED olayları."""
    pub = events.publisher()
    if pub is None:
        return
    c = events.current()
    rid = take_pending(c.run_id)
    sel = mctx.refs(after) if isinstance(after, dict) else []
    tokens = mctx.estimate_tokens(formatted) if formatted else 0
    inj = {"status": "injected" if injected else "not_injected", "reason": reason, "section": section,
           "prompt_part": "human message (veri bölümü, talimat değil)", "llm_task": llm_task, "model": model,
           "before_formatting": before, "formatted": formatted, "token_estimate": tokens,
           "token_estimate_method": "characters / 4", "budget": budget, "truncated": dropped,
           "injected_refs": [r["ref"] for r in sel], "injected_at": datetime.now(timezone.utc).isoformat()}
    if rid is not None:
        try:
            from airlab_agent.agent_trace import safe_copy
            pub.q.put(("retrieval_update", {"id": rid, "injection": safe_copy(inj)[0]}))
        except Exception:  # noqa: BLE001
            pub.lost += 1
    if injected:
        events.publish("MEMORY_CONTEXT_INJECTED", retrieval_id=rid, memory_type="mixed", namespace=section,
                       payload={"section": section, "token_estimate": tokens, "refs": inj["injected_refs"],
                                "llm_task": llm_task})
        _ls_span("memory.context.inject", {"section": section, "budget": budget},
                 {"token_estimate": tokens, "refs": inj["injected_refs"][:50]})
    if dropped:
        events.publish("MEMORY_CONTEXT_TRUNCATED", retrieval_id=rid, memory_type="mixed", namespace=section,
                       payload={"dropped": dropped, "budget": budget})


# ------------------------------------------------------------------ checkpointer gözlemi
def _ckpt_ids(config: dict | None, out: dict | None = None) -> dict:
    c = (out or config or {}).get("configurable", {}) if isinstance(out or config, dict) else {}
    return {"thread_id": c.get("thread_id"), "checkpoint_ns": c.get("checkpoint_ns") or "",
            "checkpoint_id": c.get("checkpoint_id")}


def _on_put(config, metadata, out, ms):
    ids = _ckpt_ids(config, out)
    md = metadata or {}
    events.publish("MEMORY_CHECKPOINT_CREATED", memory_id=ids["checkpoint_id"], memory_type="checkpoint",
                   namespace=ids["checkpoint_ns"] or "root", thread_id=ids["thread_id"], duration_ms=ms,
                   payload={**ids, "step": md.get("step"), "source": md.get("source"),
                            "parent_checkpoint_id": (config or {}).get("configurable", {}).get("checkpoint_id")})


def _on_get(config, tup, ms):
    if tup is None:
        return
    ids = _ckpt_ids(None, getattr(tup, "config", None))
    events.publish("MEMORY_CHECKPOINT_LOADED", memory_id=ids["checkpoint_id"], memory_type="checkpoint",
                   namespace=ids["checkpoint_ns"] or "root", thread_id=ids["thread_id"], duration_ms=ms,
                   payload={**ids, "step": (getattr(tup, "metadata", None) or {}).get("step"),
                            "pending_writes": len(getattr(tup, "pending_writes", None) or [])})


def _on_err(op: str, config, e):
    ids = _ckpt_ids(config)
    events.publish("MEMORY_BACKEND_ERROR", memory_type="checkpoint", namespace=ids["checkpoint_ns"] or "root",
                   thread_id=ids["thread_id"], status="error", error=e, payload={"operation": op, **ids})


def observed_savers():
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    class ObservedPostgresSaver(PostgresSaver):
        def put(self, config, checkpoint, metadata, new_versions):
            t0 = time.perf_counter()
            try:
                out = super().put(config, checkpoint, metadata, new_versions)
            except Exception as e:
                _on_err("put", config, e)
                raise
            _on_put(config, metadata, out, (time.perf_counter() - t0) * 1000)
            return out

        def get_tuple(self, config):
            t0 = time.perf_counter()
            try:
                tup = super().get_tuple(config)
            except Exception as e:
                _on_err("get_tuple", config, e)
                raise
            _on_get(config, tup, (time.perf_counter() - t0) * 1000)
            return tup

    class ObservedAsyncPostgresSaver(AsyncPostgresSaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            t0 = time.perf_counter()
            try:
                out = await super().aput(config, checkpoint, metadata, new_versions)
            except Exception as e:
                _on_err("put", config, e)
                raise
            _on_put(config, metadata, out, (time.perf_counter() - t0) * 1000)
            return out

        async def aget_tuple(self, config):
            t0 = time.perf_counter()
            try:
                tup = await super().aget_tuple(config)
            except Exception as e:
                _on_err("get_tuple", config, e)
                raise
            _on_get(config, tup, (time.perf_counter() - t0) * 1000)
            return tup

    return ObservedPostgresSaver, ObservedAsyncPostgresSaver
