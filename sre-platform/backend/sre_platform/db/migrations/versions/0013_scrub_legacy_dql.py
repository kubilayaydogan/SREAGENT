"""Eski MCP çağrı kayıtlarından ham DQL'in temizlenmesi (veri migration'ı, GERİ ALINAMAZ).

0012 öncesi `sre.agent_tool_executions.tool_arguments.dqlQueryString` ham DQL tutuyordu. Bu migration her satır için:
* `request_preview` boşsa ham DQL'den güvenli önizleme üretir (query aralığı, limit, string literal'leri maskeli şablon,
  karakter sayısı, sha256 parmak izi) — 0012 sonrası gateway'in ürettiği önizlemeyle aynı biçim;
* `tool_arguments`'tan `dqlQueryString`'i siler, yerine `query_key` koyar.

Mantık bu dosyaya kopyalanmıştır (uygulama kodu ileride değişse de migration sabit kalır).
Geri alma: `downgrade` yapı değiştirmez; silinen ham DQL geri gelmez (kullanıcı onayı: 2026-11-26).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-26 02:00:00
"""
import hashlib
import json
import re

from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

_STR_LIT = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")
_TIME_RANGE = re.compile(r'from:\s*"([^"]+)"\s*,\s*to:\s*"([^"]+)"')
_LIMIT = re.compile(r"\|\s*limit\s+(\d+)", re.I)
BATCH = 500


def preview(dql: str, key: str | None, connector: str | None, tool: str) -> dict:
    found = _TIME_RANGE.findall(dql)
    lim = _LIMIT.findall(dql)
    t = re.sub(r"\s+", " ", _STR_LIT.sub('"…"', dql)).strip()
    return {"method": "tools/call", "connector": connector or "dynatrace", "tool": tool, "query_key": key,
            "params": {"includeTypes": False},
            "query_start": min(a for a, _ in found) if found else None,
            "query_end": max(b for _, b in found) if found else None,
            "limit": int(lim[-1]) if lim else None,
            "dql_template": t if len(t) <= 400 else t[:400] + " …", "dql_chars": len(dql),
            "dql_sha256": hashlib.sha256(dql.encode()).hexdigest()[:16], "omitted_params": [], "backfilled": True}


def upgrade() -> None:
    bind = op.get_bind()
    sel = sa.text("""SELECT id, tool_arguments, request_preview, signal, connector, tool_name
                     FROM sre.agent_tool_executions WHERE tool_arguments ? 'dqlQueryString'
                     ORDER BY id LIMIT :n""")
    upd = sa.text("""UPDATE sre.agent_tool_executions
                     SET tool_arguments = CAST(:args AS jsonb), request_preview = CAST(:prev AS jsonb)
                     WHERE id = :id""")
    while True:
        rows = bind.execute(sel, {"n": BATCH}).mappings().all()
        if not rows:
            break
        for r in rows:
            args = dict(r["tool_arguments"] or {})
            dql = args.pop("dqlQueryString", None)
            args.setdefault("query_key", r["signal"])
            prev = r["request_preview"] or {}
            if not prev and isinstance(dql, str):
                prev = preview(dql, r["signal"], r["connector"], r["tool_name"])
            bind.execute(upd, {"id": r["id"], "args": json.dumps(args), "prev": json.dumps(prev)})


def downgrade() -> None:
    pass        # veri temizliği geri alınamaz; yapı değişmedi
