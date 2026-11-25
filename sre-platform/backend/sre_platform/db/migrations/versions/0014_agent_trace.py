"""Agent Trace kayıtları: sre.agent_trace_runs + sre.agent_trace_events.

Run: bir graph çalıştırması (analysis = investigation; chat_turn = sohbet turu, sürdürülen tur yeni girişim).
Event: run içi kararlı `sequence` ile node / state / stream / LLM / Pydantic olayları; payload backend'de maskelenmiş
ve boyutu sınırlanmış kopyadır (airlab_agent.agent_trace.safe_copy).
Geri alma: `--downgrade 0013` iki tabloyu siler.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-26 03:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_trace_runs',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.String(160), nullable=False, unique=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('graph', sa.String(64), nullable=True),
        sa.Column('trigger', sa.String(16), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='running'),
        sa.Column('investigation_id', pg.UUID(as_uuid=True),
                  sa.ForeignKey('sre.investigations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('conversation_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('thread_id', sa.String(128), nullable=True),
        sa.Column('parent_run_id', sa.String(160), nullable=True),
        sa.Column('platform_run_id', sa.String(128), nullable=True),
        sa.Column('attempt', sa.Integer, nullable=False, server_default='1'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('event_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('truncated_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('capture_errors', sa.Integer, nullable=False, server_default='0'),
        sa.Column('lost_events', sa.Integer, nullable=False, server_default='0'),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('analysis', 'chat_turn')", name='ck_agent_trace_runs_kind'),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'interrupted')",
                           name='ck_agent_trace_runs_status'),
        schema='sre')
    op.create_index('ix_agent_trace_runs_started', 'agent_trace_runs', [sa.text('started_at DESC')], schema='sre')
    op.create_index('ix_agent_trace_runs_investigation', 'agent_trace_runs', ['investigation_id'], schema='sre')
    op.create_index('ix_agent_trace_runs_conversation', 'agent_trace_runs', ['conversation_id'], schema='sre')
    op.create_table(
        'agent_trace_events',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('trace_run_id', pg.UUID(as_uuid=True),
                  sa.ForeignKey('sre.agent_trace_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sequence', sa.Integer, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_type', sa.String(32), nullable=False),
        sa.Column('node', sa.String(64), nullable=True),
        sa.Column('node_exec_id', sa.String(64), nullable=True),
        sa.Column('step', sa.Integer, nullable=True),
        sa.Column('attempt', sa.Integer, nullable=True),
        sa.Column('call_id', sa.String(64), nullable=True),
        sa.Column('related_call_id', sa.String(64), nullable=True),
        sa.Column('status', sa.String(16), nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('payload', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('truncated', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('size_bytes', sa.Integer, nullable=False, server_default='0'),
        sa.UniqueConstraint('trace_run_id', 'sequence', name='uq_agent_trace_events_seq'),
        schema='sre')
    op.create_index('ix_agent_trace_events_exec', 'agent_trace_events', ['trace_run_id', 'node_exec_id'],
                    schema='sre')
    op.create_index('ix_agent_trace_events_call', 'agent_trace_events', ['trace_run_id', 'call_id'], schema='sre')


def downgrade() -> None:
    op.drop_table('agent_trace_events', schema='sre')
    op.drop_table('agent_trace_runs', schema='sre')
