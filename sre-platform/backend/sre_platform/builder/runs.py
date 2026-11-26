"""Özel agent çalıştırma giriş noktaları: soru-cevap turu, taslak testi, zamanlanmış çalışma, retry.

Her yeni çalıştırmanın başında agent ve o an aktif yayımlanmış sürüm çözülür (`service.resolve`); tanım koşu
boyunca değişmez. Retry orijinal sürümü (agent_runs.agent_version) korur. Çalıştırılamayan agent başka bir agent'a
otomatik geçmez: koşu `rejected` olarak, nedeniyle kaydedilir.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..db.models import AgentRun, MessageRole
from ..db.session import transaction
from ..repositories import conversations
from . import definition as D
from . import engine, service


def _spec_from(a, ver, *, mode: str, semantics: str, **kw) -> engine.RunSpec:
    if ver is None:                                       # taslak testi
        defn, version, label = D.parse(a.draft), None, "draft"
    else:
        raw = {k: v for k, v in ver.definition.items() if k not in ("name", "description", "modes")}
        defn, version, label = D.parse(raw), ver.version, f"v{ver.version}"
    return engine.RunSpec(agent_id=a.id, agent_key=a.key, agent_name=a.name, version=version, version_label=label,
                          definition=defn, mode=mode, semantics=semantics, **kw)


def _answer_text(out: Any) -> str:
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    return "```json\n" + json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n```"


async def chat_turn(rt, *, agent, ver, conversation_id: uuid.UUID, thread_id: str, user_id: uuid.UUID,
                    question: str, emit, cancel) -> dict:
    question = question.strip()
    async with transaction(rt.sm) as s:
        um = await conversations.add_message(s, conversation_id, MessageRole.USER, question)
        hist = await conversations.recent_messages(s, conversation_id, 41)
    history = [{"role": m.role.value, "content": m.content} for m in hist
               if m.id != um.id and m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
    spec = _spec_from(agent, ver, mode="chat", semantics="chat", input=question, trigger="chat", user_id=user_id,
                      conversation_id=conversation_id, thread_id=thread_id, history=history, emit=emit, cancel=cancel)
    await emit("agent_thinking", {"step": "start", "message": f"{agent.name} ({spec.version_label}) çalışıyor"})
    res = await engine.execute(rt, spec, checkpointer=rt.asaver)
    if res["status"] != "completed":
        e = res["error"] or {}
        await emit("error", {"code": e.get("code", "failed"), "message": _err_text(e, agent.name)})
        return res
    meta = {"answer_source": "agent_builder", "suggestions": [],
            "agent": {"id": str(agent.id), "name": agent.name, "version": spec.version_label},
            "agent_run_id": spec.run_id, "models": res["models"]}
    async with transaction(rt.sm) as s:
        m = await conversations.add_message(s, conversation_id, MessageRole.ASSISTANT, _answer_text(res.get("output")),
                                            meta)
        mid = str(m.id)
    await emit("suggestions", {"items": []})
    await emit("message_saved", {"message_id": mid, "conversation_id": str(conversation_id)})
    await emit("agent_result", _public(res))
    return res


def _err_text(e: dict, name: str) -> str:
    where = f" (node: {e['node_id']})" if e.get("node_id") else ""
    return f"{name}: {e.get('message', 'çalıştırma başarısız')}{where}"


def _public(res: dict) -> dict:
    return {k: res.get(k) for k in ("run_id", "status", "error", "duration_ms", "nodes", "models", "usage",
                                    "version_label", "mode", "output")}


async def test_run(rt, *, agent, input_: str, variables: dict, semantics: str, user_id, emit, cancel) -> dict:
    spec = _spec_from(agent, None, mode="test", semantics=semantics, input=input_, variables=variables or {},
                      trigger="test", user_id=user_id, emit=emit, cancel=cancel)
    res = await engine.execute(rt, spec)
    await emit("agent_result", _public(res))
    if res["status"] != "completed":
        e = res["error"] or {}
        await emit("error", {"code": e.get("code", "failed"), "message": _err_text(e, agent.name)})
    return res


async def scheduled_run(rt, *, agent_id, schedule_name: str, input_: str, variables: dict | None = None,
                        created_by: str | None = None, pinned_version: int | None = None,
                        retry_of: str | None = None, user_id=None) -> dict:
    """Zamanlanmış çalışma. Çalıştırılamazsa `rejected` kaydı ve NotRunnable."""
    async with transaction(rt.sm) as s:
        try:
            await service.check_actor(s, created_by)
            a, ver = await service.resolve(s, agent_id, "scheduled", pinned_version=pinned_version)
        except service.NotRunnable as e:
            a = None
            try:
                a = await service.get(s, agent_id)
            except Exception:  # noqa: BLE001
                pass
            err = e
        else:
            err = None
    if err is not None:
        spec = engine.RunSpec(agent_id=a.id if a else None, agent_key=a.key if a else None,
                              agent_name=a.name if a else "?", version=None,
                              version_label=f"v{a.active_version}" if a and a.active_version else "-",
                              definition=D.Definition(), mode="scheduled", semantics="scheduled", input=input_,
                              variables=variables or {}, trigger="schedule", schedule_name=schedule_name,
                              retry_of=retry_of, user_id=user_id)
        await engine.open_run(rt, spec, status="rejected", error_code=err.code, error=err.message)
        raise err
    if a.kind == "system":
        raise service.NotRunnable("system_agent", "gömülü agent zamanlanmış çalışması health-check işidir")
    spec = _spec_from(a, ver, mode="scheduled", semantics="scheduled",
                      input=input_ or "",
                      variables=variables or {}, trigger="schedule", schedule_name=schedule_name, retry_of=retry_of,
                      user_id=user_id)
    if not spec.input:
        spec.input = spec.definition.settings.io.scheduled_input
    return await engine.execute(rt, spec)


async def retry(rt, run_row: AgentRun, *, user) -> dict:
    """Zamanlanmış koşuyu aynı sürüm ve girdiyle tekrar çalıştırır."""
    from airlab_agent.errors import UserError
    if run_row.mode != "scheduled" or run_row.agent_version is None:
        raise UserError("yalnız yayımlanmış sürümle yapılan zamanlanmış koşular tekrar denenebilir")
    inp = run_row.inputs or {}
    return await scheduled_run(rt, agent_id=run_row.agent_id, schedule_name=run_row.schedule_name or "retry",
                               input_=inp.get("input") or "", variables=inp.get("variables") or {},
                               pinned_version=run_row.agent_version, retry_of=run_row.run_id, user_id=user.id)
