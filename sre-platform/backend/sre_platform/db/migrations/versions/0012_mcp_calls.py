"""MCP çağrı kayıtları: mevcut sre.agent_tool_executions tablosuna MCP alanları.

Her mantıksal `tools/call` bir satırdır (kimlik = gateway'in mcp_call_id'si); retry denemeleri `attempts` JSONB'sinde.
`request_preview` connector/tool bazlı alan listesiyle üretilen güvenli önizlemedir: token, header, ham DQL, ham MCP
yanıtı ve Dynatrace log içeriği yazılmaz. Gönderilmeyen çağrılarda (DQL reddi, query budget) `mcp_method` NULL kalır.

Mevcut satırlar yalnız sınıflandırılır (mcp_method/connector/outcome); eski satırlardaki tool_arguments'a dokunulmaz —
API onları güvenli önizlemeye çevirerek döner.
Geri alma: `--downgrade 0011` eklenen kolonları siler.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-26 01:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

T = 'agent_tool_executions'


def upgrade() -> None:
    op.add_column(T, sa.Column('mcp_method', sa.String(32), nullable=True), schema='sre')
    op.add_column(T, sa.Column('connector', sa.String(64), nullable=True), schema='sre')
    op.add_column(T, sa.Column('outcome', sa.String(24), nullable=True), schema='sre')
    op.add_column(T, sa.Column('http_status', sa.SmallInteger, nullable=True), schema='sre')
    op.add_column(T, sa.Column('attempts', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
                  schema='sre')
    op.add_column(T, sa.Column('request_preview', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
                  schema='sre')
    op.create_index('ix_tools_mcp_created', T, [sa.text('created_at DESC')], schema='sre',
                    postgresql_where=sa.text('mcp_method IS NOT NULL'))
    op.execute("""
        UPDATE sre.agent_tool_executions SET
          mcp_method = 'tools/call', connector = 'dynatrace',
          outcome = CASE result_status WHEN 'OK' THEN 'success' WHEN 'EMPTY' THEN 'empty'
                    WHEN 'PERMISSION' THEN 'permission_denied' WHEN 'ERROR' THEN 'tool_error'
                    WHEN 'TIMEOUT' THEN 'timeout' WHEN 'UNAVAILABLE' THEN 'unavailable' END
        WHERE tool_name = 'execute-dql' AND execution_status <> 'SKIPPED'
          AND coalesce(error_message, '') NOT LIKE 'DQL reddedildi%'
    """)


def downgrade() -> None:
    op.drop_index('ix_tools_mcp_created', T, schema='sre')
    for c in ('request_preview', 'attempts', 'http_status', 'outcome', 'connector', 'mcp_method'):
        op.drop_column(T, c, schema='sre')
