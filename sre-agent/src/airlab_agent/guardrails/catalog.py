"""Yerleşik (built-in) guardrail policy'leri. Platform açılışta eksik olanları veritabanına ekler (mevcutlara
dokunmaz); veritabanına ulaşılamazsa engine bu kataloğun kendisiyle çalışır (fail-safe: mandatory'ler her koşulda).

`bound_to`: kuralın asıl uygulandığı katman. Engine bu policy'leri yeniden değerlendirmez; o katmanın kararını kaydeder.
"""
from __future__ import annotations

from .model import Assignment, Policy

# (key, name, category, type, severity, action, mandatory, stages, priority, params, bound_to, description)
_B = [
    # ---- input
    ("input_max_length", "Maximum input length", "input", "input_length", "medium", "block", False,
     ("before_agent",), 10, {}, None, "Kullanıcı / schedule girdisi uzunluk sınırı."),
    ("input_invalid", "Invalid input & sanitization", "input", "invalid_input", "low", "sanitize", False,
     ("before_agent",), 20, {}, None, "Kontrol ve görünmez karakterler temizlenir."),
    ("input_schema", "Input schema validation", "input", "input_schema", "medium", "block", False,
     ("before_agent",), 30, {}, None, "Custom agent input şeması (tanımlıysa)."),
    ("input_prompt_injection", "Direct prompt injection", "input", "prompt_injection", "high", "block", False,
     ("before_agent",), 40, {}, None, "Talimat geçersiz kılma, rol ele geçirme, sızdırma, policy atlatma."),
    ("input_unsupported_request", "Unsupported request (action intent)", "input", "unsupported_request", "low", "warn",
     False, ("before_agent",), 50, {}, None, "Agent salt okunur: eylem istekleri işaretlenir."),
    # ---- model / context
    ("llm_context_size", "Context size validation", "model_context", "context_size", "medium", "block", False,
     ("llm_request",), 10, {}, None, "LLM'e giden tahmini girdi token'ı sınırı."),
    ("llm_call_budget", "LLM call & token budget", "model_context", "builtin", "high", "block", True,
     ("llm_request",), 15, {}, "middleware:call_limit", "RunBudget (Settings › Agent)."),
    ("llm_request_redaction", "Secret redaction / PII masking (LLM input)", "data_protection", "builtin", "critical",
     "redact", True, ("llm_request",), 20, {}, "middleware:redaction", "LLM'e giden mesajlar DLP ile maskelenir."),
    ("llm_system_prompt_protection", "System prompt protection", "model_context", "system_prompt_protection", "high",
     "sanitize", False, ("llm_request", "llm_response"), 30, {}, None,
     "Veri içindeki prompt sınırlayıcıları; yanıtta system prompt sızıntısı."),
    ("llm_output_validation", "Model output validation", "model_context", "model_output_validation", "low", "warn",
     False, ("llm_response",), 40, {}, None, "Boş / aşırı uzun model yanıtı."),
    ("llm_output_sensitive", "Sensitive data in model output", "data_protection", "sensitive_data", "high", "redact",
     True, ("llm_response",), 50, {}, None, "Model yanıtındaki secret ve PII maskelenir."),
    # ---- tool / mcp
    ("tool_allowlist", "Tool / MCP tool allowlist", "tool", "builtin", "critical", "block", True,
     ("mcp_request",), 10, {}, "middleware:tool_allowlist", "Yalnız izinli connector + tool."),
    ("mcp_connector_authorization", "MCP connector authorization", "mcp", "builtin", "critical", "block", True,
     ("mcp_request",), 11, {}, "policy:tool.call", "Connector onayı, tool tanım hash'i (rug-pull)."),
    ("tool_args_dql", "Tool argument validation (DQL)", "tool", "builtin", "high", "block", True,
     ("mcp_request",), 12, {}, "middleware:tool_param_validation", "DQL salt okuma komutları, uzunluk."),
    ("tool_call_budget", "Tool call budget", "tool", "builtin", "high", "block", True,
     ("mcp_request",), 13, {}, "middleware:call_limit", "Koşu başına MCP query sınırı."),
    ("tool_denylist", "Tool denylist", "tool", "tool_denylist", "high", "block", True,
     ("tool_request", "mcp_request"), 14, {}, None, "Listedeki tool hiçbir koşulda çağrılmaz."),
    ("mcp_server_allowlist", "MCP server allowlist", "mcp", "mcp_server_allowlist", "critical", "block", True,
     ("mcp_request",), 15, {}, None, "Yalnız izinli MCP sunucuları."),
    ("mcp_read_only", "Read-only policy enforcement", "mcp", "read_only_enforcement", "critical", "block", True,
     ("tool_request", "mcp_request"), 16, {}, None,
     "Yalnız doğrulanmış salt-okuma adapter'ı; sunucu metadata'sı yetki vermez."),
    ("tool_rbac", "Tool authorization (RBAC)", "tool", "tool_rbac", "critical", "block", True,
     ("tool_request", "mcp_request"), 17, {}, None, "Tool risk sınıfı → rol."),
    ("mcp_tenant_isolation", "Tenant isolation (MCP)", "mcp", "tenant_isolation", "critical", "block", True,
     ("tool_request", "mcp_request"), 18, {}, None, "Koşu tenant'ı dışında kaynak yok."),
    ("tool_dangerous_operation", "Dangerous operation detection", "tool", "dangerous_operation", "high", "block", True,
     ("tool_request", "mcp_request"), 19, {}, None, "Argümanda yazma / yıkıcı işlem."),
    ("mcp_request_validation", "MCP request validation", "mcp", "mcp_request_validation", "medium", "block", True,
     ("tool_request", "mcp_request"), 20, {}, None, "Argüman şeması ve boyutu."),
    ("mcp_query_time_range", "Query time range limit", "mcp", "query_time_range", "medium", "block", False,
     ("mcp_request",), 30, {}, None, "DQL zaman aralığı sınırı."),
    ("mcp_rate_limit", "MCP rate limiting", "mcp", "rate_limit", "medium", "block", False,
     ("mcp_request",), 40, {}, None, "Sunucu başına dakikalık istek."),
    ("action_approval", "Action risk / human approval", "action", "action_risk", "critical", "require_approval", True,
     ("tool_request", "mcp_request"), 50, {}, None, "Yazma yetenekli eylem insan onayı ister."),
    ("mcp_response_limits", "MCP response size & record limits", "mcp", "mcp_response_limits", "high", "sanitize", True,
     ("mcp_response", "tool_response"), 10, {}, None, "Kayıt ve boyut sınırı."),
    ("mcp_response_validation", "MCP response validation", "mcp", "mcp_response_validation", "medium", "block", True,
     ("mcp_response", "tool_response"), 20, {}, None, "Yanıt biçimi."),
    ("mcp_indirect_injection", "Indirect prompt injection (MCP data)", "mcp", "indirect_injection", "high", "sanitize",
     False, ("mcp_response", "tool_response"), 30, {}, None, "Güvenilmeyen veri içindeki talimatlar etkisizleştirilir."),
    ("mcp_sensitive_masking", "Sensitive data masking (MCP data)", "data_protection", "sensitive_data", "high",
     "redact", False, ("mcp_response", "tool_response"), 40, {"types": ["private_key", "api_key", "access_token",
                                                                       "bearer_token", "auth_header",
                                                                       "connection_string", "db_credential",
                                                                       "password", "session_id", "webhook"]},
     None, "MCP kayıtlarındaki secret değerleri persist / LLM öncesi maskelenir."),
    # ---- output / evidence
    ("output_schema", "Structured output validation", "output", "output_schema", "medium", "block", False,
     ("llm_response", "final_output"), 10, {}, None, "JSON / şema (tanımlıysa)."),
    ("output_sensitive", "Sensitive output / data leakage", "data_protection", "sensitive_data", "critical", "redact",
     True, ("final_output",), 20, {}, None, "Kullanıcıya giden çıktıda secret / PII."),
    ("output_unsupported_claim", "Unsupported claim detection", "output", "unsupported_claim", "low", "sanitize", False,
     ("final_output",), 30, {}, None, "Kesin ifade + evidence referansı yok."),
    ("evidence_integrity", "Evidence existence / ID / provenance", "evidence", "evidence_integrity", "high", "sanitize",
     False, ("final_output",), 40, {}, None, "Geçersiz evidence ID, provenance."),
    ("evidence_sufficiency", "Missing evidence / unsupported root cause", "evidence", "evidence_sufficiency", "high",
     "sanitize", False, ("final_output",), 50, {}, None, "Yetersiz evidence ile kesin kök sebep yok."),
    ("evidence_window", "Evidence freshness & time window", "evidence", "evidence_window", "medium", "warn", False,
     ("final_output",), 60, {}, None, "Pencere geçerliliği ve tazelik."),
    ("risk_score_integrity", "Deterministic risk score integrity", "evidence", "risk_score_integrity", "critical",
     "sanitize", True, ("final_output",), 70, {}, None, "LLM risk skorunu değiştiremez."),
    # ---- memory
    ("memory_write_auth", "Memory write authorization", "memory", "memory_write_auth", "high", "block", True,
     ("memory_write",), 10, {}, None, "Rol ve procedural memory koruması."),
    ("memory_write_validation", "Memory validator (secret, PII, injection, duplicate)", "memory", "builtin", "high",
     "block", True, ("memory_write",), 20, {}, "memory:validate_write", "Memory Policy Manager kararları."),
    ("memory_read_tenant", "Memory tenant isolation", "memory", "tenant_isolation", "critical", "quarantine", True,
     ("memory_read",), 10, {}, None, "Başka tenant kaydı bağlama girmez."),
    ("memory_read_poisoning", "Memory poisoning (read)", "memory", "memory_poisoning", "high", "quarantine", True,
     ("memory_read",), 20, {}, None, "Doğrulanmamış güvenilmeyen kayıt bağlama girmez."),
    ("memory_read_injection", "Indirect injection (memory)", "memory", "indirect_injection", "high", "sanitize", False,
     ("memory_read",), 30, {}, None, "Memory içeriğindeki talimatlar etkisizleştirilir."),
    ("checkpoint_protection", "Checkpoint data protection", "data_protection", "sensitive_data", "critical", "redact",
     True, ("checkpoint",), 10, {"types": ["private_key", "api_key", "access_token", "bearer_token", "auth_header",
                                           "connection_string", "db_credential", "password", "session_id", "webhook"]},
     None, "Checkpoint'e secret yazılmaz."),
    # ---- execution
    ("exec_kill_switch", "Kill switch", "execution", "kill_switch", "critical", "terminate", True,
     ("before_agent", "node_start", "llm_request", "mcp_request", "tool_request"), 1, {}, None,
     "Global / agent bazlı acil durdurma."),
    ("exec_limits", "Execution limits", "execution", "execution_limits", "high", "terminate", True,
     ("node_start", "mcp_request", "tool_request", "llm_request"), 5, {}, None,
     "Node, tool, LLM çağrısı ve süre sınırları (atomik sayaç)."),
    ("exec_loop_detection", "Agent loop detection", "execution", "loop_detection", "high", "terminate", True,
     ("node_start", "mcp_request", "tool_request"), 6, {}, None, "Tekrarlayan node / çağrı."),
    ("exec_concurrency", "Concurrent execution limit", "execution", "concurrency_limit", "medium", "block", False,
     ("before_agent",), 7, {}, None, "Agent başına eşzamanlı koşu."),
]


def builtin_policies() -> list[dict]:
    out = []
    for (key, name, cat, typ, sev, action, mandatory, stages, prio, params, bound, desc) in _B:
        out.append({"key": key, "name": name, "category": cat, "type": typ, "severity": sev, "action": action,
                    "mandatory": mandatory, "stages": list(stages), "priority": prio, "params": dict(params),
                    "bound_to": bound, "description": desc, "mode": "enforce", "status": "active",
                    "assignments": [{"scope": "global", "value": None}]})
    return out


def builtin_engine_policies() -> list[Policy]:
    return [Policy(id=f"builtin:{d['key']}", key=d["key"], name=d["name"], category=d["category"], type=d["type"],
                   severity=d["severity"], mode="enforce", mandatory=d["mandatory"], stages=tuple(d["stages"]),
                   action=d["action"], priority=d["priority"], version=1, params=d["params"],
                   assignments=(Assignment("global"),), bound_to=d["bound_to"]) for d in builtin_policies()]


# Başka katmanda uygulanan kuralların eşlemesi (katman adı → policy key)
BOUND = {"middleware:redaction": "llm_request_redaction", "middleware:tool_allowlist": "tool_allowlist",
         "middleware:tool_param_validation": "tool_args_dql", "middleware:call_limit": None,   # model/tool'a göre
         "policy:tool.call": "mcp_connector_authorization", "memory:validate_write": "memory_write_validation"}
