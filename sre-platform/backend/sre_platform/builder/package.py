"""Agent paketleri: JSON dışa / içe aktarma.

Paket (`schema_version` = paket formatı, `agent_version` = dışa aktarılan yayımlanmış sürüm; taslakta null):

* İçerir: ad, açıklama, çalışma modları, workflow node / bağlantıları (yerel node kimlikleri korunur), canvas
  yerleşimi, prompt'lar, model / runtime ayarları, input / output şemaları, entegrasyon referansları.
* İçermez: API anahtarı, token, parola, konuşma geçmişi, checkpoint, run kaydı, RAG içeriği, schedule kaydı.
  Bağlantılar ortam kimliğiyle değil, hedefte eşleştirilecek referansla (`llm:1`, `mcp:1`) yazılır.
* Serbest metin alanlarında secret / PII tespit edilirse dışa aktarma engellenir ve alanlar listelenir.
* İçe aktarma doğrulaması hiçbir workflow ya da tool çalıştırmaz; tüm kayıt tek transaction'dadır (kısmi kayıt yok).
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.errors import UserError
from airlab_agent.redact import redact_text

from ..db.models import Agent, Connector, LlmProfile
from ..security import audit
from . import definition as D
from . import service
from . import validate as V

SCHEMA_VERSION = 1
MAX_BYTES = 512 * 1024


class SensitiveContent(Exception):
    def __init__(self, fields: list[dict]):
        super().__init__("serbest metin alanlarında hassas bilgi bulundu")
        self.fields = fields


class PackageInvalid(Exception):
    def __init__(self, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.message, self.issues = message, issues or []


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PkgAgent(_M):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    modes: list[Literal["chat", "scheduled"]] = Field(min_length=1)


class PkgTool(_M):
    name: str = Field(max_length=128)
    hash: str | None = Field(None, max_length=128)


class PkgLlm(_M):
    ref: str = Field(pattern=r"^llm:\d{1,3}$")
    name: str | None = Field(None, max_length=64)
    provider: str = Field(max_length=32)
    model: str | None = Field(None, max_length=128)
    used_by: list[str] = Field(default_factory=list, max_length=100)


class PkgConnector(_M):
    ref: str = Field(pattern=r"^mcp:\d{1,3}$")
    name: str | None = Field(None, max_length=64)
    provider: str = Field(max_length=32)
    tools: list[PkgTool] = Field(default_factory=list, max_length=50)


class PkgPolicy(_M):
    name: str = Field(max_length=64)
    mandatory: bool = False
    enabled: bool = True


class PkgDeps(_M):
    llm_profiles: list[PkgLlm] = Field(default_factory=list, max_length=20)
    connectors: list[PkgConnector] = Field(default_factory=list, max_length=5)
    middleware: list[PkgPolicy] = Field(default_factory=list, max_length=30)
    memory_source: Literal["none", "platform"] = "none"


class Package(_M):
    schema_version: int
    kind: Literal["airlab.agent"] = "airlab.agent"
    exported_at: str | None = Field(None, max_length=40)
    agent_version: int | None = None
    agent: PkgAgent
    definition: dict[str, Any]
    dependencies: PkgDeps = Field(default_factory=PkgDeps)


# ------------------------------------------------------------------------------------------------ hassas içerik
def free_text(d: D.Definition, agent: dict) -> list[tuple[str, str | None, str]]:
    """(alan, node_id, metin) — dışa aktarılan tüm serbest metin alanları."""
    out: list[tuple[str, str | None, str]] = [("agent.description", None, agent.get("description") or "")]
    ins = d.settings.instructions
    out += [("settings.instructions.system_prompt", None, ins.system_prompt),
            ("settings.instructions.task_instructions", None, ins.task_instructions),
            ("settings.io.scheduled_input", None, d.settings.io.scheduled_input)]
    out += [(f"settings.instructions.variables.{v.name}", None, f"{v.default} {v.description}") for v in ins.variables]
    for n in d.workflow.nodes:
        out += [(k, n.id, t) for k, t in D.text_fields(n).items()]
        out.append(("label", n.id, n.label))
    return out


def sensitive_fields(d: D.Definition, agent: dict) -> list[dict]:
    found = []
    for fld, nid, txt in free_text(d, agent):
        if txt and redact_text(txt, "export") != txt:
            found.append({"field": fld, "node_id": nid,
                          "message": "secret ya da kişisel veri kalıbı (token, anahtar, parola, e-posta, IBAN…) "
                                     "bulundu; kaldırıp tekrar deneyin"})
    return found


# ------------------------------------------------------------------------------------------------ dışa aktarma
async def export(rt, s: AsyncSession, a: Agent, *, version: int | None, actor: str) -> dict:
    if a.kind != "custom":
        raise UserError("gömülü SRE Agent dışa aktarılamaz (workflow'u platform kodundadır)")
    if version is not None:
        v = await service.version(s, a, version)
        raw = {k: x for k, x in v.definition.items() if k not in ("name", "description", "modes")}
        meta = {"name": v.definition.get("name", a.name), "description": v.definition.get("description"),
                "modes": v.definition.get("modes") or list(a.modes or [])}
    else:
        raw, meta = a.draft, {"name": a.name, "description": a.description, "modes": list(a.modes or [])}
    d = D.parse(raw)
    bad = sensitive_fields(d, meta)
    if bad:
        raise SensitiveContent(bad)
    defn = d.model_dump(mode="json")
    profiles = {str(p.id): p for p in (await s.execute(select(LlmProfile))).scalars()}
    connectors = {str(c.id): c for c in (await s.execute(select(Connector))).scalars()}
    refs: dict[str, str] = {}
    llms: dict[str, PkgLlm] = {}

    def llm_ref(pid: str | None, where: str) -> str | None:
        if not pid:
            return None
        if pid not in refs:
            refs[pid] = f"llm:{len(llms) + 1}"
            p = profiles.get(pid)
            llms[pid] = PkgLlm(ref=refs[pid], name=p.name if p else None, provider=p.provider if p else "anthropic",
                               model=p.model if p else None)
        llms[pid].used_by.append(where)
        return refs[pid]
    m = defn["settings"]["model"]
    m["llm_profile_id"] = llm_ref(m.get("llm_profile_id"), "settings.model")
    for n in defn["workflow"]["nodes"]:
        if n["config"].get("llm_profile_id"):
            n["config"]["llm_profile_id"] = llm_ref(n["config"]["llm_profile_id"], f"node:{n['id']}")
    cons: list[PkgConnector] = []
    cid = defn["settings"]["tools"].get("connector_id")
    if cid:
        c = connectors.get(cid)
        hashes = {t.get("name"): t.get("hash") for t in ((c.capabilities or {}).get("tools") or [])} if c else {}
        used = sorted({n["config"].get("tool") for n in defn["workflow"]["nodes"] if n["type"] == "mcp_tool"} |
                      set(defn["settings"]["tools"].get("allowed_tools") or []))
        cons.append(PkgConnector(ref="mcp:1", name=c.name if c else None, provider=c.provider if c else "dynatrace",
                                 tools=[PkgTool(name=t, hash=hashes.get(t)) for t in used if t]))
        defn["settings"]["tools"]["connector_id"] = "mcp:1"
    from airlab_agent import middleware as mw
    pol = [PkgPolicy(name=n, mandatory=x.mandatory, enabled=bool(defn["settings"]["policies"]["optional"].get(n, True)))
           for n, x in mw.REGISTRY.items()]
    pkg = Package(schema_version=SCHEMA_VERSION, exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  agent_version=version, agent=PkgAgent(**meta), definition=defn,
                  dependencies=PkgDeps(llm_profiles=list(llms.values()), connectors=cons, middleware=pol,
                                       memory_source=defn["settings"]["memory"]["source"]))
    await audit.record(s, actor=actor, action="agent.export", section="agent_builder", target=str(a.id),
                       after={"agent_version": version, "schema_version": SCHEMA_VERSION})
    return pkg.model_dump(mode="json")


# ------------------------------------------------------------------------------------------------ içe aktarma
def parse_package(raw: Any) -> Package:
    if not isinstance(raw, dict):
        raise PackageInvalid("paket bir JSON nesnesi olmalı")
    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise PackageInvalid(f"desteklenmeyen paket sürümü: {sv!r} (desteklenen: {SCHEMA_VERSION})")
    try:
        return Package.model_validate(raw)
    except ValidationError as e:
        raise PackageInvalid("paket şeması geçersiz", [V.issue("package_schema", f"{'.'.join(str(x) for x in er['loc'])}: "
                                                              f"{er['msg']}") for er in e.errors()[:20]]) from None


def _apply(defn: dict, mapping: dict[str, str | None]) -> dict:
    d = copy.deepcopy(defn)
    m = d.get("settings", {}).get("model", {})
    if isinstance(m.get("llm_profile_id"), str) and m["llm_profile_id"].startswith("llm:"):
        m["llm_profile_id"] = mapping.get(m["llm_profile_id"]) or None
    for n in d.get("workflow", {}).get("nodes", []) or []:
        c = n.get("config") or {}
        if isinstance(c.get("llm_profile_id"), str) and c["llm_profile_id"].startswith("llm:"):
            c["llm_profile_id"] = mapping.get(c["llm_profile_id"]) or None
    t = d.get("settings", {}).get("tools", {})
    if isinstance(t.get("connector_id"), str) and t["connector_id"].startswith("mcp:"):
        t["connector_id"] = mapping.get(t["connector_id"]) or None
    return d


def _suggest(pkg: Package, env: V.Env) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for l in pkg.dependencies.llm_profiles:
        cand = [p for p in env.profiles.values() if p["enabled"] and p["provider"] == l.provider]
        best = next((p for p in cand if p["name"] == l.name), None) or next((p for p in cand if p["model"] == l.model), None)
        out[l.ref] = best["id"] if best else None
    for c in pkg.dependencies.connectors:
        cand = [x for x in env.connectors.values() if x.get("enabled") and x.get("provider") == c.provider]
        best = next((x for x in cand if x["name"] == c.name), None) or (cand[0] if len(cand) == 1 else None)
        out[c.ref] = best["id"] if best else None
    return out


def mapping_issues(pkg: Package, mapping: dict[str, str | None], env: V.Env) -> list[dict]:
    """Eşleştirme uyumluluğu. Eksik eşleştirme uyarıdır (taslak kaydedilebilir); uyumsuzluk hatadır."""
    out = []
    defn = D.parse(_apply(pkg.definition, mapping))
    for l in pkg.dependencies.llm_profiles:
        pid = mapping.get(l.ref)
        f = f"mapping.{l.ref}"
        if not pid:
            out.append(V.issue("mapping_missing", f"{l.name or l.ref} ({l.model or '?'}) eşleştirilmedi; taslak "
                                                  f"olarak kaydedilir, test / yayın öncesi eşleştirin", field_=f,
                               severity="warning"))
            continue
        p = env.profiles.get(pid)
        if p is None:
            out.append(V.issue("mapping_invalid", "seçilen LLM profili yok", field_=f))
            continue
        if not p["enabled"]:
            out.append(V.issue("mapping_invalid", f"'{p['name']}' devre dışı", field_=f))
        if p["provider"] != l.provider:
            out.append(V.issue("mapping_invalid", f"provider uyumsuz: paket {l.provider}, seçilen {p['provider']}",
                               field_=f))
        if env.allowed_models and p["model"] not in env.allowed_models:
            out.append(V.issue("mapping_invalid", f"model izinli listede değil: {p['model']}", field_=f))
        for n in [None, *defn.workflow.nodes]:
            if n is not None and n.type not in ("llm", "structured_output"):
                continue
            r = V.resolve_model(defn, n, env)
            if r["profile_id"] != pid:
                continue
            where = f"node {n.id}" if n is not None else "agent varsayılanı"
            if r["temperature"] is not None and not D.supports_temperature(r["model"]):
                out.append(V.issue("mapping_incompatible", f"{r['model']} temperature desteklemiyor ({where} "
                                                           f"temperature={r['temperature']}); farklı model seçin ya da "
                                                           f"ayarı kaldırın", field_=f, node=n.id if n else None))
            chk = p.get("check") or {}
            mx = chk.get("model_max_tokens") if chk.get("ok") and chk.get("model") == r["model"] else None
            if mx and r["max_tokens"] and int(r["max_tokens"]) > int(mx):
                out.append(V.issue("mapping_incompatible", f"{r['model']} en fazla {mx} çıktı token'ı destekler "
                                                           f"({where}: {r['max_tokens']})", field_=f,
                                   node=n.id if n else None))
            caps = (chk.get("capabilities") or {}) if chk.get("model") == r["model"] else {}
            if n is not None and n.type == "structured_output" and caps and not caps.get("structured_outputs", True):
                out.append(V.issue("mapping_incompatible", f"{r['model']} structured outputs desteklemiyor ({where})",
                                   field_=f, node=n.id))
    for c in pkg.dependencies.connectors:
        cid = mapping.get(c.ref)
        f = f"mapping.{c.ref}"
        if not cid:
            out.append(V.issue("mapping_missing", f"MCP connector {c.name or c.ref} eşleştirilmedi; taslak olarak "
                                                  f"kaydedilir", field_=f, severity="warning"))
            continue
        x = env.connectors.get(cid)
        if x is None:
            out.append(V.issue("mapping_invalid", "seçilen connector yok", field_=f))
            continue
        if x.get("provider") != c.provider:
            out.append(V.issue("mapping_incompatible", f"provider uyumsuz: paket {c.provider}, seçilen "
                                                       f"{x.get('provider')}", field_=f))
        for t in c.tools:
            meta = (x.get("tools") or {}).get(t.name)
            if t.name not in (x.get("allowed_tools") or []):
                out.append(V.issue("mapping_tool_missing", f"'{t.name}' hedef connector'da onaylı tool değil",
                                   field_=f))
            elif t.hash and meta and meta.get("hash") and meta["hash"] != t.hash:
                out.append(V.issue("mapping_tool_schema", f"'{t.name}' tool tanımı / input şeması uyumsuz (tanım "
                                                          f"özeti farklı); connector'ı yeniden keşfedip inceleyin",
                                   field_=f))
    return out


async def preview(rt, s: AsyncSession, raw: Any, *, mapping: dict[str, str | None] | None = None) -> dict:
    pkg = parse_package(raw)
    env = await V.load_env(rt, s)
    mp = {**_suggest(pkg, env), **{k: v for k, v in (mapping or {}).items() if k}}
    d, struct = V.structural(_apply(pkg.definition, mp), name=pkg.agent.name, modes=pkg.agent.modes)
    exists = (await s.execute(select(Agent.id).where(Agent.status != "archived",
                                                     Agent.name.ilike(pkg.agent.name.strip())))).first() is not None
    return {"agent": pkg.agent.model_dump(), "agent_version": pkg.agent_version, "schema_version": pkg.schema_version,
            "node_count": len((pkg.definition.get("workflow") or {}).get("nodes") or []),
            "edge_count": len((pkg.definition.get("workflow") or {}).get("edges") or []),
            "dependencies": pkg.dependencies.model_dump(), "mapping": mp, "name_conflict": exists,
            "structural_issues": struct, "mapping_issues": mapping_issues(pkg, mp, env) if d is not None else [],
            "candidates": {"llm_profiles": [{"id": p["id"], "name": p["name"], "model": p["model"],
                                             "provider": p["provider"], "enabled": p["enabled"]}
                                            for p in env.profiles.values()],
                           "connectors": [{"id": c["id"], "name": c["name"], "provider": c.get("provider"),
                                           "enabled": c.get("enabled"), "allowed_tools": c.get("allowed_tools")}
                                          for c in env.connectors.values()]}}


async def import_(rt, s: AsyncSession, raw: Any, *, mapping: dict[str, str | None], target: str,
                  agent_id: str | None, name: str | None, actor: str) -> tuple[Agent, list[dict]]:
    """target: new (varsayılan) | existing (özel agent'a yeni taslak revizyonu). Hata → hiçbir şey kaydedilmez."""
    pkg = parse_package(raw)
    env = await V.load_env(rt, s)
    defn = _apply(pkg.definition, mapping)
    d, struct = V.structural(defn, name=name or pkg.agent.name, modes=pkg.agent.modes)
    if d is None or V.blocking(struct):
        raise PackageInvalid("workflow geçersiz; paket içe aktarılamaz", V.blocking(struct))
    mi = mapping_issues(pkg, mapping, env)
    if V.blocking(mi):
        raise PackageInvalid("bağımlılık eşleştirmesi uyumsuz", V.blocking(mi))
    clean = d.model_dump(mode="json")
    src = {"imported": True, "schema_version": pkg.schema_version, "agent_version": pkg.agent_version,
           "exported_at": pkg.exported_at, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if target == "existing":
        a = await service.get(s, agent_id or "")
        if a.kind != "custom":
            raise UserError("gömülü SRE Agent import hedefi olamaz")
        if a.status == "archived":
            raise UserError("arşivlenmiş agent'a içe aktarılamaz")
        a.draft, a.draft_revision = clean, a.draft_revision + 1
        a.modes = list(pkg.agent.modes)
        if pkg.agent.description is not None:
            a.description = pkg.agent.description
        a.source, a.updated_by = {**(a.source or {}), "last_import": src}, actor
        a.updated_at = datetime.now(timezone.utc)
        await s.flush()
    elif target == "new":
        a = await service.create(s, name=(name or pkg.agent.name), description=pkg.agent.description,
                                 modes=list(pkg.agent.modes), actor=actor, definition=clean, source=src)
    else:
        raise UserError("hedef: new | existing")
    _, issues = V.run_all(a.draft, env, name=a.name, modes=list(a.modes or []), stage="publish")
    await audit.record(s, actor=actor, action="agent.import", section="agent_builder", target=str(a.id),
                       after={"target": target, "schema_version": pkg.schema_version,
                              "agent_version": pkg.agent_version, "draft_revision": a.draft_revision,
                              "unmapped": sorted(k for k, v in mapping.items() if not v)})
    return a, issues + [i for i in mi if i["severity"] == "warning"]
