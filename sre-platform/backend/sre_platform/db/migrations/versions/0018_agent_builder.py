"""Agent Builder: agents, agent_versions, agent_runs + çalıştırma kayıtlarına agent kimliği.

* Yeni tablolar EKLENİR. Gömülü SRE Agent `system` kaydı olarak tohumlanır (çalışması mevcut kod yolundadır).
* conversations.agent_id (NULL = gömülü agent), agent_trace_runs.agent_id / agent_version / agent_mode eklenir;
  agent_trace_runs.kind kısıtına 'custom_agent' eklenir. Mevcut satırlar değişmez (NULL = gömülü agent).
* Tanımlarda secret yoktur: LLM profili ve MCP connector kimlikle referanslanır.
Rollback: `--downgrade 0017` yeni tabloları ve sütunları siler (özel agent'lar ve run kayıtları kaybolur).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-26 23:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

SYSTEM_AGENT_ID = '5be00000-0000-4000-8000-000000000001'


def upgrade() -> None:
    op.create_table(
        'agents',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(64), nullable=False, unique=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('modes', pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('draft', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('draft_revision', sa.Integer, nullable=False, server_default='1'),
        sa.Column('active_version', sa.Integer),
        sa.Column('source', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_by', sa.String(64)),
        sa.Column('updated_by', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('archived_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('system', 'custom')", name='ck_agents_kind'),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name='ck_agents_status'),
        schema='sre')
    op.create_index('uq_agents_name_live', 'agents', [sa.text('lower(name)')], unique=True, schema='sre',
                    postgresql_where=sa.text("status <> 'archived'"))
    op.create_table(
        'agent_versions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_id', pg.UUID(as_uuid=True), sa.ForeignKey('sre.agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('definition', pg.JSONB, nullable=False),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('draft_revision', sa.Integer),
        sa.Column('note', sa.Text),
        sa.Column('published_by', sa.String(64)),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('agent_id', 'version', name='uq_agent_versions'),
        schema='sre')
    op.create_table(
        'agent_runs',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(160), nullable=False, unique=True),
        sa.Column('agent_id', pg.UUID(as_uuid=True), sa.ForeignKey('sre.agents.id', ondelete='CASCADE')),
        sa.Column('agent_key', sa.String(64)),
        sa.Column('agent_version', sa.Integer),
        sa.Column('version_label', sa.String(32), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('trigger', sa.String(32)),
        sa.Column('user_id', pg.UUID(as_uuid=True)),
        sa.Column('conversation_id', pg.UUID(as_uuid=True)),
        sa.Column('investigation_id', pg.UUID(as_uuid=True)),
        sa.Column('schedule_name', sa.String(100)),
        sa.Column('retry_of', sa.String(160)),
        sa.Column('status', sa.String(16), nullable=False, server_default='running'),
        sa.Column('error_code', sa.String(64)),
        sa.Column('error', sa.Text),
        sa.Column('models', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('nodes', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('usage', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('output', pg.JSONB),
        sa.Column('inputs', pg.JSONB),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('duration_ms', sa.Integer),
        sa.CheckConstraint("mode IN ('chat', 'scheduled', 'test')", name='ck_agent_runs_mode'),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'rejected', 'cancelled')",
                           name='ck_agent_runs_status'),
        schema='sre')
    op.create_index('ix_agent_runs_agent_started', 'agent_runs', ['agent_id', sa.text('started_at DESC')], schema='sre')
    op.create_index('ix_agent_runs_started', 'agent_runs', [sa.text('started_at DESC')], schema='sre')

    op.add_column('conversations', sa.Column('agent_id', pg.UUID(as_uuid=True),
                                             sa.ForeignKey('sre.agents.id', ondelete='SET NULL')), schema='sre')
    op.add_column('agent_trace_runs', sa.Column('agent_id', pg.UUID(as_uuid=True)), schema='sre')
    op.add_column('agent_trace_runs', sa.Column('agent_version', sa.String(32)), schema='sre')
    op.add_column('agent_trace_runs', sa.Column('agent_mode', sa.String(16)), schema='sre')
    op.drop_constraint('ck_agent_trace_runs_kind', 'agent_trace_runs', schema='sre')
    op.create_check_constraint('ck_agent_trace_runs_kind', 'agent_trace_runs',
                               "kind IN ('analysis', 'chat_turn', 'custom_agent')", schema='sre')
    op.execute(f"""INSERT INTO sre.agents (id, key, name, description, kind, status, modes, draft, active_version,
                   created_by, updated_by)
                   VALUES ('{SYSTEM_AGENT_ID}', 'sre-agent', 'SRE Agent',
                           'Gömülü SRE Agent: Dynatrace MCP ile telemetri analizi, deterministik risk score, RCA ve '
                           'geçmiş kayıtlarla karşılaştırma. Davranışı platform kodunda tanımlıdır.',
                           'system', 'published', ARRAY['chat','scheduled'], '{{}}'::jsonb, NULL,
                           'system', 'system')""")


def downgrade() -> None:
    op.drop_constraint('ck_agent_trace_runs_kind', 'agent_trace_runs', schema='sre')
    op.execute("DELETE FROM sre.agent_trace_runs WHERE kind = 'custom_agent'")
    op.create_check_constraint('ck_agent_trace_runs_kind', 'agent_trace_runs', "kind IN ('analysis', 'chat_turn')",
                               schema='sre')
    for c in ('agent_mode', 'agent_version', 'agent_id'):
        op.drop_column('agent_trace_runs', c, schema='sre')
    op.drop_column('conversations', 'agent_id', schema='sre')
    op.drop_index('ix_agent_runs_started', table_name='agent_runs', schema='sre')
    op.drop_index('ix_agent_runs_agent_started', table_name='agent_runs', schema='sre')
    op.drop_table('agent_runs', schema='sre')
    op.drop_table('agent_versions', schema='sre')
    op.drop_index('uq_agents_name_live', table_name='agents', schema='sre')
    op.drop_table('agents', schema='sre')
