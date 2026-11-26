"""Agent tanımı backend doğrulaması.

Kaydetme (`save` — yapısal hatalar engeller, eksik bağımlılık uyarıdır: import edilen taslak eşleştirmesiz
kaydedilebilir), test (`test`) ve yayımlama (`publish`) aşamalarında çalışır. Her sorun ilgili node / alanı taşır;
arayüz bunu canvas'taki node üzerinde gösterir. Doğrulama hiçbir workflow ya da tool ÇALIŞTIRMAZ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import definition as D

DQL_TOOL = "execute-dql"
NO_VALUE_OPS = {"exists", "not_exists", "empty", "not_empty"}
RESERVED = {"input", "now", "agent", "mode"}


@dataclass
class Env:
    """Doğrulama bağlamı (DB'den; secret içermez)."""
    profiles: dict[str, dict] = field(default_factory=dict)        # id → {name, model, enabled, max_tokens, check}
    connectors: dict[str, dict] = field(default_factory=dict)      # id → policy.connector_view + id
    allowed_models: frozenset[str] = frozenset()
    middleware: dict[str, dict] = field(default_factory=dict)      # name → {mandatory, title, params}


async def load_env(rt, s: AsyncSession) -> Env:
    from ..db.models import Connector, LlmProfile
    from ..middleware import service as mw_service
    from ..security import policy
    from ..settings import llm_profiles
    from airlab_agent import middleware as mw
    env = Env(allowed_models=llm_profiles.allowed_models(rt))
    for p in (await s.execute(select(LlmProfile))).scalars():
        env.profiles[str(p.id)] = {"id": str(p.id), "name": p.name, "provider": p.provider, "model": p.model,
                                   "enabled": p.enabled, "max_tokens": p.max_tokens, "timeout_s": p.timeout_s,
                                   "effort": p.effort, "check": dict(p.last_check or {})}
    for c in (await s.execute(select(Connector))).scalars():
        v = policy.connector_view(c) or {}
        env.connectors[str(c.id)] = {**v, "id": str(c.id), "read_only": c.read_only}
    snap = await mw_service.snapshot(s)
    for n, m in mw.REGISTRY.items():
        env.middleware[n] = {"mandatory": m.mandatory, "title": m.title,
                             "params": ((snap.get(n) or {}).get("params") or {}),
                             "enabled": bool((snap.get(n) or {}).get("enabled", True))}
    return env


def issue(code: str, message: str, *, node: str | None = None, field_: str | None = None,
          severity: str = "error") -> dict:
    return {"severity": severity, "code": code, "message": message, "node_id": node, "field": field_}


def _schema_ok(schema: Any) -> str | None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return "şema bir JSON Schema nesnesi olmalı (type: object)"
    try:
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(schema)
    except ImportError:  # pragma: no cover
        pass
    except Exception as e:  # noqa: BLE001
        return f"geçersiz JSON Schema: {str(getattr(e, 'message', e))[:200]}"
    return None


def structural(raw: dict, *, name: str | None = None, modes: list[str] | None = None) -> tuple[D.Definition | None, list[dict]]:
    """Şema + graph yapısı. Bağımlılık (profil / connector) kontrolü `dependencies`'tedir."""
    out: list[dict] = []
    try:
        d = D.parse(raw)
    except ValidationError as e:
        nodes = ((raw or {}).get("workflow") or {}).get("nodes") or []
        for err in e.errors()[:30]:
            loc = list(err.get("loc") or [])
            nid = None
            if loc[:2] == ["workflow", "nodes"] and len(loc) > 2 and isinstance(loc[2], int) and loc[2] < len(nodes):
                nid = (nodes[loc[2]] or {}).get("id") if isinstance(nodes[loc[2]], dict) else None
            out.append(issue("schema", f"{'.'.join(str(x) for x in loc)}: {err.get('msg')}", node=nid,
                             field_=".".join(str(x) for x in loc)))
        return None, out
    if name is not None and not name.strip():
        out.append(issue("name_required", "agent adı zorunlu", field_="name"))
    if modes is not None:
        if not modes:
            out.append(issue("mode_required", "en az bir çalışma modu seçin (soru-cevap / zamanlanmış)", field_="modes"))
        bad = [m for m in modes if m not in D.MODES]
        if bad:
            out.append(issue("mode_unknown", f"bilinmeyen çalışma modu: {', '.join(bad)}", field_="modes"))
    nodes, edges = d.workflow.nodes, d.workflow.edges
    ids = [n.id for n in nodes]
    by_id = {n.id: n for n in nodes}
    for nid in {i for i in ids if ids.count(i) > 1}:
        out.append(issue("duplicate_node", f"aynı node kimliği birden fazla: {nid}", node=nid))
    for n in nodes:
        if n.type not in D.NODE_TYPES:
            known = next((u for u in D.UNAVAILABLE if u["type"] == n.type), None)
            out.append(issue("node_unavailable" if known else "node_unknown",
                             f"{n.type}: {known['reason']}" if known else f"bilinmeyen node türü: {n.type}", node=n.id))
    starts = [n for n in nodes if n.type == "start"]
    results = [n for n in nodes if n.type == "result"]
    if len(starts) != 1:
        out.append(issue("start_count", "workflow tam olarak bir Başlangıç node'u içermeli",
                         node=starts[1].id if len(starts) > 1 else None))
    if not results:
        out.append(issue("result_missing", "workflow en az bir Sonuç node'u içermeli"))
    outgoing: dict[str, list[D.Edge]] = {i: [] for i in by_id}
    incoming: dict[str, list[D.Edge]] = {i: [] for i in by_id}
    seen = set()
    for e in edges:
        bad = [x for x in (e.source, e.target) if x not in by_id]
        if bad:
            out.append(issue("edge_dangling", f"bağlantı var olmayan node'a işaret ediyor: {', '.join(bad)}",
                             node=e.source if e.source in by_id else None, field_=f"edge:{e.id}"))
            continue
        if e.source == e.target:
            out.append(issue("edge_self", "node kendisine bağlanamaz (döngü desteklenmez)", node=e.source,
                             field_=f"edge:{e.id}"))
            continue
        key = (e.source, e.target, e.branch)
        if key in seen:
            out.append(issue("edge_duplicate", "aynı bağlantı iki kez tanımlı", node=e.source, field_=f"edge:{e.id}"))
            continue
        seen.add(key)
        outgoing[e.source].append(e)
        incoming[e.target].append(e)
    for n in nodes:
        o = outgoing.get(n.id, [])
        if n.type == "start" and incoming.get(n.id):
            out.append(issue("start_incoming", "Başlangıç node'una bağlantı gelemez", node=n.id))
        if n.type == "result":
            if o:
                out.append(issue("result_outgoing", "Sonuç node'undan çıkış bağlantısı olamaz", node=n.id))
        elif n.type == "condition":
            br = sorted(e.branch or "-" for e in o)
            if br != ["false", "true"]:
                out.append(issue("condition_branches", "Koşul node'u bir 'true' ve bir 'false' çıkışına sahip olmalı",
                                 node=n.id))
        elif n.type in D.NODE_TYPES:
            if len(o) != 1:
                out.append(issue("single_outgoing" if o else "no_outgoing",
                                 "node tek bir sonraki node'a bağlanmalı (paralel dal desteklenmez)" if o else
                                 "node'un çıkış bağlantısı yok (Sonuç'a ulaşmalı)", node=n.id))
            elif o[0].branch:
                out.append(issue("branch_label", "yalnız Koşul çıkışları true / false etiketi taşır", node=n.id,
                                 field_=f"edge:{o[0].id}"))
    # döngü (DFS) ve erişilebilirlik
    color: dict[str, int] = {}
    cyc: set[str] = set()

    def dfs(u: str, stack: list[str]):
        color[u] = 1
        stack.append(u)
        for e in outgoing.get(u, []):
            v = e.target
            if color.get(v) == 1:
                cyc.update(stack[stack.index(v):])
            elif not color.get(v):
                dfs(v, stack)
        stack.pop()
        color[u] = 2
    for n in nodes:
        if not color.get(n.id):
            dfs(n.id, [])
    for nid in sorted(cyc):
        out.append(issue("cycle", "desteklenmeyen döngü: workflow yönlü ve döngüsüz (DAG) olmalı", node=nid))
    reach: set[str] = set()
    if len(starts) == 1:
        st = [starts[0].id]
        while st:
            u = st.pop()
            if u in reach:
                continue
            reach.add(u)
            st.extend(e.target for e in outgoing.get(u, []))
        for n in nodes:
            if n.id not in reach:
                out.append(issue("unreachable", "node Başlangıç'tan erişilemiyor", node=n.id))
    # node ayarları + şablon değişkenleri (yalnız atalar tarafından üretilen değişkenler kullanılabilir)
    var_names = {v.name for v in d.settings.instructions.variables}
    for v in d.settings.instructions.variables:
        if not D.IDENT.match(v.name) or v.name in RESERVED:
            out.append(issue("variable_name", f"geçersiz değişken adı: {v.name} (küçük harf, rakam, _; ayrılmış: "
                                              f"{', '.join(sorted(RESERVED))})", field_="settings.instructions.variables"))
    ancestors: dict[str, set[str]] = {}

    def anc(nid: str, path: frozenset = frozenset()) -> set[str]:
        if nid in ancestors:
            return ancestors[nid]
        res: set[str] = set()
        for e in incoming.get(nid, []):
            if e.source in path:
                continue
            res |= {e.source} | anc(e.source, path | {nid})
        ancestors[nid] = res
        return res
    outputs: dict[str, str] = {}
    for n in nodes:
        ov = D.output_var(n)
        if ov:
            if ov in outputs and outputs[ov] != n.id:
                out.append(issue("output_var_duplicate", f"'{ov}' başka bir node'da da üretiliyor ({outputs[ov]})",
                                 node=n.id, field_="output_var", severity="warning"))
            outputs.setdefault(ov, n.id)
    for n in nodes:
        out.extend(_node_config(n, d))
        if cyc:
            continue
        avail = {"input", "now", "agent", "mode"} | var_names | {D.output_var(by_id[a]) for a in anc(n.id)
                                                                   if a in by_id and D.output_var(by_id[a])}
        refs = [(k, r) for k, txt in D.text_fields(n).items() for r in D.template_vars(txt)]
        if n.type == "condition" and isinstance(n.config.get("field"), str):
            refs.append(("field", n.config["field"].strip().removeprefix("{{").removesuffix("}}").strip()))
        for k, r in refs:
            if r.split(".")[0] not in avail:
                out.append(issue("unknown_variable", f"'{r}' bu node'dan önce üretilmiyor (kullanılabilir: "
                                                     f"{', '.join(sorted(x for x in avail if x)) or '—'})",
                                 node=n.id, field_=k))
    io = d.settings.io
    for k in ("input_schema", "output_schema"):
        sch = getattr(io, k)
        if sch is not None:
            bad = _schema_ok(sch)
            if bad:
                out.append(issue("io_schema", bad, field_=f"settings.io.{k}"))
    if modes and "scheduled" in modes and io.scheduled_output_format == "json" and io.output_schema is None:
        out.append(issue("scheduled_json_schema", "zamanlanmış çalışmada JSON çıktı için output şeması tanımlayın",
                         field_="settings.io.output_schema"))
    mcp_n = sum(1 for n in nodes if n.type == "mcp_tool")
    if mcp_n > d.settings.limits.max_tool_calls:
        out.append(issue("limit_tool_calls", f"{mcp_n} MCP Tool node'u var; maksimum tool çağrısı "
                                             f"{d.settings.limits.max_tool_calls}", field_="settings.limits.max_tool_calls"))
    llm_n = sum(1 for n in nodes if n.type in ("llm", "structured_output"))
    if llm_n > d.settings.limits.max_llm_calls:
        out.append(issue("limit_llm_calls", f"{llm_n} LLM node'u var; maksimum LLM çağrısı "
                                            f"{d.settings.limits.max_llm_calls}", field_="settings.limits.max_llm_calls"))
    if len(nodes) > d.settings.limits.max_steps:
        out.append(issue("limit_steps", f"{len(nodes)} node var; maksimum adım {d.settings.limits.max_steps}",
                         field_="settings.limits.max_steps"))
    return d, out


def _node_config(n: D.Node, d: D.Definition) -> list[dict]:
    c, out = n.config, []

    def need(k: str, msg: str):
        v = c.get(k)
        if not (isinstance(v, str) and v.strip()):
            out.append(issue("config_missing", msg, node=n.id, field_=k))

    def ovar():
        v = c.get("output_var")
        if not (isinstance(v, str) and D.IDENT.match(v)) or v in RESERVED:
            out.append(issue("output_var", "çıktı değişkeni adı geçersiz (küçük harf, rakam, _; input / now / agent / "
                                           "mode ayrılmış)", node=n.id, field_="output_var"))
    if n.type == "llm":
        need("prompt", "LLM node'u için prompt zorunlu")
        ovar()
        _model_fields(n, out)
    elif n.type == "structured_output":
        need("prompt", "Structured Output için prompt zorunlu")
        ovar()
        bad = _schema_ok(c.get("schema"))
        if bad:
            out.append(issue("schema_invalid", bad, node=n.id, field_="schema"))
        _model_fields(n, out)
    elif n.type == "mcp_tool":
        if not (isinstance(c.get("tool"), str) and c["tool"]):
            out.append(issue("config_missing", "tool seçin", node=n.id, field_="tool"))
        args = c.get("args") or {}
        if not isinstance(args, dict):
            out.append(issue("config_invalid", "args bir nesne olmalı", node=n.id, field_="args"))
        elif c.get("tool") == DQL_TOOL and not (isinstance(args.get("dqlQueryString"), str)
                                               and args["dqlQueryString"].strip()):
            out.append(issue("config_missing", "execute-dql için dqlQueryString zorunlu", node=n.id,
                             field_="args.dqlQueryString"))
        ovar()
        t = c.get("timeout_s")
        if t is not None and not (isinstance(t, int) and 1 <= t <= 300):
            out.append(issue("config_invalid", "timeout 1-300 sn", node=n.id, field_="timeout_s"))
    elif n.type == "memory_retrieval":
        need("query", "arama metni zorunlu (ör. {{input}})")
        ovar()
        if d.settings.memory.source != "platform":
            out.append(issue("memory_disabled", "Memory Retrieval için Memory ayarlarında kaynak 'platform' seçin",
                             node=n.id, field_="settings.memory.source"))
        k = c.get("top_k")
        if k is not None and not (isinstance(k, int) and 1 <= k <= 20):
            out.append(issue("config_invalid", "top_k 1-20", node=n.id, field_="top_k"))
    elif n.type == "condition":
        need("field", "karşılaştırılacak alanı seçin (ör. answer, result.status)")
        op = c.get("operator")
        if op not in D.OPERATORS:
            out.append(issue("config_invalid", f"operatör seçin ({', '.join(D.OPERATORS)})", node=n.id,
                             field_="operator"))
        elif op not in NO_VALUE_OPS and not isinstance(c.get("value"), (str, int, float)):
            out.append(issue("config_missing", "karşılaştırma değeri zorunlu", node=n.id, field_="value"))
    elif n.type == "result":
        need("template", "Sonuç şablonu zorunlu (ör. {{answer}})")
    return out


def _model_fields(n: D.Node, out: list[dict]) -> None:
    c = n.config
    mt = c.get("max_tokens")
    if mt is not None and not (isinstance(mt, int) and 256 <= mt <= 128000):
        out.append(issue("config_invalid", "maksimum çıktı token 256-128000", node=n.id, field_="max_tokens"))
    if c.get("effort") not in (None, "", *D.EFFORTS):
        out.append(issue("config_invalid", "effort: low / medium / high", node=n.id, field_="effort"))
    if c.get("stream") not in (None, True, False):
        out.append(issue("config_invalid", "stream true / false", node=n.id, field_="stream"))


def resolve_model(d: D.Definition, n: D.Node | None, env: Env) -> dict:
    """Çözülmüş model: node ayarı agent varsayılanını geçersiz kılar. Secret yok."""
    m = d.settings.model
    c = (n.config if n is not None else {}) or {}
    pid = c.get("llm_profile_id") or m.llm_profile_id
    prof = env.profiles.get(str(pid)) if pid else None
    model = c.get("model") or (m.model if not c.get("llm_profile_id") else None) or (prof or {}).get("model")
    return {"profile_id": str(pid) if pid else None, "profile": (prof or {}).get("name"),
            "provider": (prof or {}).get("provider"), "model": model,
            "source": "node" if c.get("llm_profile_id") or c.get("model") else "agent",
            "max_tokens": c.get("max_tokens") or m.max_tokens or (prof or {}).get("max_tokens"),
            "timeout_s": m.timeout_s or (prof or {}).get("timeout_s"),
            "effort": c.get("effort") or m.effort or (prof or {}).get("effort"),
            "temperature": c.get("temperature", m.temperature) if "temperature" in c else m.temperature}


def dependencies(d: D.Definition, env: Env, *, modes: list[str] | None = None) -> list[dict]:
    """Bağlantı / yetenek / policy kontrolleri. Eksik eşleştirme `dependency_missing` kodlu hatadır."""
    out: list[dict] = []
    s = d.settings
    llm_nodes = [n for n in d.workflow.nodes if n.type in ("llm", "structured_output")]
    if s.model.llm_profile_id and s.model.llm_profile_id not in env.profiles:
        out.append(issue("dependency_missing", "agent LLM profili bu ortamda yok; Model ayarından eşleştirin",
                         field_="settings.model.llm_profile_id"))
    for n in llm_nodes + ([None] if not llm_nodes and s.model.llm_profile_id else []):
        r = resolve_model(d, n, env)
        nid = n.id if n is not None else None
        fld = "llm_profile_id" if n is not None and n.config.get("llm_profile_id") else "settings.model.llm_profile_id"
        if not r["profile_id"]:
            out.append(issue("dependency_missing", "LLM bağlantısı seçilmedi (agent varsayılanı ya da node ayarı)",
                             node=nid, field_=fld))
            continue
        prof = env.profiles.get(r["profile_id"])
        if prof is None:
            out.append(issue("dependency_missing", "seçilen LLM profili bu ortamda yok; eşleştirin", node=nid,
                             field_=fld))
            continue
        if not prof["enabled"]:
            out.append(issue("profile_disabled", f"LLM profili '{prof['name']}' devre dışı", node=nid, field_=fld))
        if prof["provider"] != "anthropic":
            out.append(issue("provider_unsupported", f"'{prof['provider']}' için doğrulanmış adapter yok", node=nid,
                             field_=fld))
        model = r["model"]
        if env.allowed_models and model not in env.allowed_models:
            out.append(issue("model_not_allowed", f"model izinli listede değil: {model}", node=nid,
                             field_="model" if n is not None and n.config.get("model") else "settings.model.model"))
        chk = prof.get("check") or {}
        known = chk.get("ok") and chk.get("model") == model
        mx = chk.get("model_max_tokens") if known else None
        if mx and r["max_tokens"] and int(r["max_tokens"]) > int(mx):
            out.append(issue("max_tokens_exceeds", f"maksimum çıktı token ({r['max_tokens']}) modelin sınırını ({mx}) "
                                                   f"aşıyor", node=nid,
                             field_="max_tokens" if n is not None and n.config.get("max_tokens") else
                             "settings.model.max_tokens"))
        if r["temperature"] is not None and not D.supports_temperature(model):
            out.append(issue("temperature_unsupported", f"{model} temperature parametresini desteklemiyor (Claude 5 "
                                                        f"ailesinde örnekleme parametreleri kaldırıldı; davranışı "
                                                        f"prompt ve effort ile yönetin)", node=nid,
                             field_="temperature" if n is not None and "temperature" in n.config else
                             "settings.model.temperature"))
        caps = (chk.get("capabilities") or {}) if known else {}
        if n is not None and n.type == "structured_output" and known and caps and \
                not caps.get("structured_outputs", True):
            out.append(issue("structured_unsupported", f"{model} structured outputs desteklemiyor", node=nid))
        if r["effort"] and known and caps and not caps.get("effort", True):
            out.append(issue("effort_unsupported", f"{model} effort parametresini desteklemiyor", node=nid,
                             field_="effort"))
    mcp = [n for n in d.workflow.nodes if n.type == "mcp_tool"]
    con = env.connectors.get(str(s.tools.connector_id)) if s.tools.connector_id else None
    if s.tools.connector_id and con is None:
        out.append(issue("dependency_missing", "MCP connector bu ortamda yok; MCP ayarından eşleştirin",
                         field_="settings.tools.connector_id"))
    from ..security import policy
    allow_mw = (env.middleware.get("tool_allowlist") or {}).get("params") or {}
    for n in mcp:
        tool = n.config.get("tool")
        if con is None:
            if not s.tools.connector_id:
                out.append(issue("dependency_missing", "MCP connector seçilmedi (MCP ve araçlar ayarı)", node=n.id,
                                 field_="settings.tools.connector_id"))
            continue
        if tool not in s.tools.allowed_tools:
            out.append(issue("tool_not_in_agent_allowlist", f"'{tool}' agent'ın tool allowlist'inde yok", node=n.id,
                             field_="tool"))
        dec = policy.decide("tool.call", {"connector": con, "tool": tool})
        if not dec.allowed:
            out.append(issue(f"tool_{dec.reason_code}", f"yetkisiz tool: {dec.message}", node=n.id, field_="tool"))
        elif con.get("provider") != "dynatrace" or tool != DQL_TOOL:
            out.append(issue("tool_no_adapter", f"'{con['name']}/{tool}' için doğrulanmış çağrı yolu yok "
                                                f"(desteklenen: Dynatrace execute-dql)", node=n.id, field_="tool"))
        if allow_mw and ("dynatrace" not in (allow_mw.get("allowed_connectors") or [])
                         or tool not in (allow_mw.get("allowed_tools") or [])):
            out.append(issue("tool_platform_denied", "platformun zorunlu Tool allowlist middleware'i bu tool'a izin "
                                                     "vermiyor (Agent Middleware)", node=n.id, field_="tool"))
    for t in s.tools.allowed_tools:
        if con is not None and t not in (con.get("allowed_tools") or []):
            out.append(issue("tool_not_approved", f"'{t}' connector'da onaylı değil (Settings › Connectors)",
                             field_="settings.tools.allowed_tools"))
    for name, on in s.policies.optional.items():
        m = env.middleware.get(name)
        if m is None:
            out.append(issue("policy_unknown", f"kayıtlı olmayan middleware: {name}", field_="settings.policies"))
        elif m["mandatory"] and not on:
            out.append(issue("policy_mandatory", f"'{m['title']}' platformun zorunlu politikasıdır; kapatılamaz",
                             field_="settings.policies"))
    return out


def run_all(raw: dict, env: Env | None, *, name: str | None, modes: list[str] | None,
            stage: str) -> tuple[D.Definition | None, list[dict]]:
    """stage: save (yalnız yapısal hatalar engeller) | test | publish (bağımlılıklar dahil)."""
    d, issues = structural(raw, name=name, modes=modes)
    if d is not None and env is not None:
        dep = dependencies(d, env, modes=modes)
        if stage == "save":
            dep = [{**i, "severity": "warning"} for i in dep]
        issues += dep
    return d, issues


def blocking(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["severity"] == "error"]
