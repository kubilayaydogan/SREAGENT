"""Context Windows: llm_context_calls.

* Yalnız yeni tablo EKLENİR; mevcut tablolara dokunulmaz. Eski run'lar için satır yoktur (sayfa boş gösterir).
* İçerik (prompt, yanıt, MCP payload) saklanmaz; token sayıları, kaynak türleri ve güvenli referanslar.
Rollback: `--downgrade 0016` tabloyu siler (context window kayıtları kaybolur).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-26 21:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'llm_context_calls',
        sa.Column('call_id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('logical_call_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('attempt_no', sa.Integer, nullable=False, server_default='0'),
        sa.Column('attempt_count', sa.Integer, nullable=False, server_default='1'),
        sa.Column('run_id', sa.String(160)),
        sa.Column('run_kind', sa.String(16)),
        sa.Column('thread_id', sa.String(160)),
        sa.Column('user_id', sa.String(64)),
        sa.Column('node', sa.String(64)),
        sa.Column('task', sa.String(64)),
        sa.Column('call_seq', sa.Integer),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('model', sa.String(128)),
        sa.Column('served_model', sa.String(128)),
        sa.Column('streaming', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('duration_ms', sa.Float),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('error_code', sa.String(64)),
        sa.Column('error_message', sa.String(300)),
        sa.Column('http_status', sa.Integer),
        sa.Column('stop_reason', sa.String(48)),
        sa.Column('capacity', sa.Integer),
        sa.Column('capacity_source', sa.String(64)),
        sa.Column('capacity_note', sa.String(160)),
        sa.Column('model_max_output', sa.Integer),
        sa.Column('reserved_output', sa.Integer),
        sa.Column('estimated_input', sa.Integer),
        sa.Column('estimate_method', sa.String(96)),
        sa.Column('estimate_over', sa.Integer),
        sa.Column('input_tokens', sa.Integer),
        sa.Column('output_tokens', sa.Integer),
        sa.Column('cache_read_tokens', sa.Integer),
        sa.Column('cache_creation_tokens', sa.Integer),
        sa.Column('reasoning_tokens', sa.Integer),
        sa.Column('usage_source', sa.String(16), nullable=False, server_default='missing'),
        sa.Column('planned_fill', sa.Float),
        sa.Column('actual_fill', sa.Float),
        sa.Column('components', pg.JSONB),
        sa.Column('context_decisions', pg.JSONB),
        sa.Column('stream', pg.JSONB),
        sa.Column('request_id', sa.String(80)),
        sa.Column('trace_call_id', sa.String(64)),
        sa.Column('middleware_execution_id', sa.String(64)),
        sa.CheckConstraint("status IN ('ok', 'error', 'context_limit', 'retried', 'cancelled')",
                           name='ck_llm_ctx_status'),
        sa.CheckConstraint("usage_source IN ('provider', 'missing')", name='ck_llm_ctx_usage_source'),
        schema='sre',
    )
    op.create_index('ix_llm_ctx_started', 'llm_context_calls', [sa.text('started_at DESC')], schema='sre')
    op.create_index('ix_llm_ctx_run', 'llm_context_calls', ['run_id', 'call_seq'], schema='sre')
    op.create_index('ix_llm_ctx_logical', 'llm_context_calls', ['logical_call_id'], schema='sre')
    op.create_index('ix_llm_ctx_fill', 'llm_context_calls', [sa.text('actual_fill DESC NULLS LAST')], schema='sre')


def downgrade() -> None:
    op.drop_table('llm_context_calls', schema='sre')
