"""Agent Middleware: middleware_configs, middleware_config_versions, middleware_executions.

* Yalnız yeni tablolar EKLENİR; mevcut tablolara dokunulmaz.
* Middleware TANIMLARI kodda kayıtlıdır (airlab_agent.middleware.REGISTRY); burada yalnız yapılandırma (versiyonlu)
  ve execution kayıtları tutulur. Satırı olmayan middleware kod varsayılanıyla çalışır (mevcut davranış).
Rollback: `--downgrade 0015` üç tabloyu siler (yapılandırma geçmişi ve execution kayıtları kaybolur).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-26 18:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'middleware_configs',
        sa.Column('name', sa.String(64), primary_key=True),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('run_order', sa.Integer, nullable=False),
        sa.Column('params', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('scope', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by', sa.String(64), nullable=True),
        sa.CheckConstraint('run_order BETWEEN 1 AND 1000', name='ck_middleware_configs_order'),
        sa.CheckConstraint('version >= 1', name='ck_middleware_configs_version'),
        schema='sre')
    op.create_table(
        'middleware_config_versions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('change', sa.String(32), nullable=False),
        sa.Column('snapshot', pg.JSONB, nullable=False),
        sa.Column('actor', sa.String(64), nullable=False),
        sa.Column('note', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('name', 'version', name='uq_middleware_config_version'),
        schema='sre')
    op.create_index('ix_middleware_config_versions_name', 'middleware_config_versions',
                    ['name', sa.text('version DESC')], schema='sre')
    op.create_table(
        'middleware_executions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', sa.String(160), nullable=True),
        sa.Column('run_kind', sa.String(16), nullable=True),
        sa.Column('user_id', sa.String(64), nullable=True),
        sa.Column('thread_id', sa.String(160), nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True),
        sa.Column('parent_execution_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('sequence', sa.Integer, nullable=False),
        sa.Column('depth', sa.Integer, nullable=False, server_default='0'),
        sa.Column('entry_type', sa.String(16), nullable=False),
        sa.Column('middleware', sa.String(64), nullable=True),
        sa.Column('middleware_version', sa.Integer, nullable=True),
        sa.Column('hook', sa.String(32), nullable=True),
        sa.Column('target_kind', sa.String(16), nullable=True),
        sa.Column('target', sa.String(128), nullable=True),
        sa.Column('task', sa.String(128), nullable=True),
        sa.Column('connector', sa.String(64), nullable=True),
        sa.Column('tool_call_id', sa.String(64), nullable=True),
        sa.Column('attempt_no', sa.Integer, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('overhead_ms', sa.Float, nullable=True),
        sa.Column('wrapped_ms', sa.Float, nullable=True),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('decision', sa.String(16), nullable=True),
        sa.Column('outcome', sa.String(24), nullable=True),
        sa.Column('reason', sa.String(300), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('policy', sa.String(160), nullable=True),
        sa.Column('changed_fields', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('before', pg.JSONB, nullable=True),
        sa.Column('after', pg.JSONB, nullable=True),
        sa.Column('truncated', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('extra', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("entry_type IN ('agent', 'call', 'middleware')", name='ck_middleware_exec_entry'),
        sa.CheckConstraint("result IN ('success', 'error')", name='ck_middleware_exec_result'),
        sa.CheckConstraint("decision IS NULL OR decision IN ('allow', 'block', 'modify', 'short_circuit', "
                           "'not_applicable')", name='ck_middleware_exec_decision'),
        schema='sre')
    op.create_index('ix_middleware_exec_ts', 'middleware_executions', [sa.text('started_at DESC')], schema='sre')
    op.create_index('ix_middleware_exec_run', 'middleware_executions', ['run_id', 'sequence'], schema='sre')
    op.create_index('ix_middleware_exec_mw_ts', 'middleware_executions', ['middleware', sa.text('started_at DESC')],
                    schema='sre')
    op.create_index('ix_middleware_exec_parent', 'middleware_executions', ['parent_execution_id'], schema='sre')
    op.create_index('ix_middleware_exec_tool_call', 'middleware_executions', ['tool_call_id'], schema='sre')


def downgrade() -> None:
    op.drop_table('middleware_executions', schema='sre')
    op.drop_table('middleware_config_versions', schema='sre')
    op.drop_table('middleware_configs', schema='sre')
