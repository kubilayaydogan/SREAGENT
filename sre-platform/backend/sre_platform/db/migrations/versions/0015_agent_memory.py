"""Agent Memory: memory_records, memory_versions, memory_events, memory_retrievals + procedural/semantic yaşam döngüsü.

* Yeni tablolar yalnız EKLENİR. Mevcut episode / known issue / runbook / checkpoint / Store verisi taşınmaz veya
  kopyalanmaz; Memory Manager bunları adapter ile okur.
* runbooks: version, approval_status (mevcutlar 'approved' → agent davranışı değişmez), approved_by/at, archived_at.
* known_issues: version, archived_at. agent_episodes: archived_at (arşivlenen episode retrieval dışı kalır).
Geri alma: `--downgrade 0014` yeni tabloları ve kolonları siler (memory kayıtları/olayları kaybolur; mevcut veri kalır).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-26 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

_TSV = ("to_tsvector('turkish', coalesce(title, '') || ' ' || coalesce(content, '') || ' ' || coalesce(category, '')) "
        "|| to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, '') || ' ' || coalesce(category, ''))")


def _scope():
    return [sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
            sa.Column('agent_id', sa.String(64), nullable=False, server_default='sre-agent')]


def upgrade() -> None:
    op.create_table(
        'memory_records',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        *_scope(),
        sa.Column('namespace', sa.String(200), nullable=False),
        sa.Column('memory_type', sa.String(16), nullable=False),
        sa.Column('category', sa.String(48), nullable=True),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('structured_data', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('source_trust', sa.String(16), nullable=False, server_default='trusted'),
        sa.Column('source_run_id', sa.String(160), nullable=True),
        sa.Column('source_thread_id', sa.String(128), nullable=True),
        sa.Column('source_trace_id', sa.String(64), nullable=True),
        sa.Column('evidence_references', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('confidence_score', sa.Float, nullable=True),
        sa.Column('verification_status', sa.String(16), nullable=False, server_default='unverified'),
        sa.Column('approval_status', sa.String(16), nullable=False, server_default='not_required'),
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('approved_by', sa.String(64), nullable=True),
        sa.Column('verified_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_accessed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('access_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('tags', pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('embedding_model', sa.String(64), nullable=True),
        sa.Column('embedding_status', sa.String(16), nullable=False, server_default='not_indexed'),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('metadata', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('tsv', pg.TSVECTOR(), sa.Computed(_TSV, persisted=True), nullable=True),
        sa.CheckConstraint("memory_type IN ('semantic', 'episodic', 'procedural')", name='ck_memory_records_type'),
        sa.CheckConstraint("verification_status IN ('unverified', 'verified', 'rejected')",
                           name='ck_memory_records_verification'),
        sa.CheckConstraint("approval_status IN ('not_required', 'pending', 'approved', 'rejected')",
                           name='ck_memory_records_approval'),
        sa.CheckConstraint("status IN ('active', 'archived', 'expired', 'rejected')", name='ck_memory_records_status'),
        sa.CheckConstraint("source_trust IN ('trusted', 'untrusted')", name='ck_memory_records_trust'),
        sa.CheckConstraint("confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
                           name='ck_memory_records_confidence'),
        schema='sre')
    op.create_index('ix_memory_records_scope', 'memory_records', ['tenant_id', 'agent_id', 'memory_type', 'status'],
                    schema='sre')
    op.create_index('ix_memory_records_ns', 'memory_records', ['tenant_id', 'agent_id', 'namespace'], schema='sre')
    op.create_index('ix_memory_records_hash', 'memory_records', ['tenant_id', 'agent_id', 'content_hash'], schema='sre')
    op.create_index('ix_memory_records_updated', 'memory_records', [sa.text('updated_at DESC')], schema='sre')
    op.create_index('ix_memory_records_tsv', 'memory_records', ['tsv'], postgresql_using='gin', schema='sre')

    op.create_table(
        'memory_versions',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        *_scope(),
        sa.Column('record_kind', sa.String(24), nullable=False),
        sa.Column('record_id', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('change', sa.String(24), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='applied'),
        sa.Column('snapshot', pg.JSONB, nullable=False),
        sa.Column('actor', sa.String(64), nullable=False),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('decided_by', sa.String(64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('record_kind', 'record_id', 'version', name='uq_memory_versions'),
        sa.CheckConstraint("status IN ('applied', 'pending', 'rejected', 'superseded')",
                           name='ck_memory_versions_status'),
        schema='sre')
    op.create_index('ix_memory_versions_record', 'memory_versions',
                    ['record_kind', 'record_id', sa.text('version DESC')], schema='sre')
    op.create_index('ix_memory_versions_pending', 'memory_versions', ['status'],
                    postgresql_where=sa.text("status = 'pending'"), schema='sre')

    op.create_table(
        'memory_events',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        *_scope(),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('run_id', sa.String(160), nullable=True),
        sa.Column('thread_id', sa.String(128), nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True),
        sa.Column('node', sa.String(64), nullable=True),
        sa.Column('memory_id', sa.String(200), nullable=True),
        sa.Column('memory_type', sa.String(24), nullable=True),
        sa.Column('namespace', sa.String(200), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='ok'),
        sa.Column('duration_ms', sa.Float, nullable=True),
        sa.Column('error', pg.JSONB, nullable=True),
        sa.Column('correlation_id', sa.String(64), nullable=True),
        sa.Column('retrieval_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('actor', sa.String(64), nullable=True),
        sa.Column('payload', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        schema='sre')
    op.create_index('ix_memory_events_ts', 'memory_events', [sa.text('ts DESC')], schema='sre')
    op.create_index('ix_memory_events_type_ts', 'memory_events', ['event_type', sa.text('ts DESC')], schema='sre')
    op.create_index('ix_memory_events_run', 'memory_events', ['run_id'], schema='sre')
    op.create_index('ix_memory_events_thread', 'memory_events', ['thread_id'], schema='sre')
    op.create_index('ix_memory_events_retrieval', 'memory_events', ['retrieval_id'], schema='sre')

    op.create_table(
        'memory_retrievals',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        *_scope(),
        sa.Column('origin', sa.String(16), nullable=False),
        sa.Column('run_id', sa.String(160), nullable=True),
        sa.Column('thread_id', sa.String(128), nullable=True),
        sa.Column('trace_id', sa.String(64), nullable=True),
        sa.Column('node', sa.String(64), nullable=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('strategy', sa.String(32), nullable=False),
        sa.Column('query', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('namespaces', pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('memory_types', pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('filters', pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('top_k', sa.Integer, nullable=True),
        sa.Column('returned_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('empty', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('selected', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('excluded', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('stages', pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('injection', pg.JSONB, nullable=True),
        sa.Column('duration_ms', sa.Float, nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='ok'),
        sa.Column('error', pg.JSONB, nullable=True),
        schema='sre')
    op.create_index('ix_memory_retrievals_ts', 'memory_retrievals', [sa.text('ts DESC')], schema='sre')
    op.create_index('ix_memory_retrievals_run', 'memory_retrievals', ['run_id'], schema='sre')
    op.create_index('ix_memory_retrievals_thread', 'memory_retrievals', ['thread_id'], schema='sre')

    for col in (sa.Column('version', sa.Integer, nullable=False, server_default='1'),
                sa.Column('approval_status', sa.String(16), nullable=False, server_default='approved'),
                sa.Column('approved_by', sa.String(64), nullable=True),
                sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
                sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True)):
        op.add_column('runbooks', col, schema='sre')
    op.add_column('known_issues', sa.Column('version', sa.Integer, nullable=False, server_default='1'), schema='sre')
    op.add_column('known_issues', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True), schema='sre')
    op.add_column('agent_episodes', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True), schema='sre')


def downgrade() -> None:
    op.drop_column('agent_episodes', 'archived_at', schema='sre')
    op.drop_column('known_issues', 'archived_at', schema='sre')
    op.drop_column('known_issues', 'version', schema='sre')
    for c in ('archived_at', 'approved_at', 'approved_by', 'approval_status', 'version'):
        op.drop_column('runbooks', c, schema='sre')
    op.drop_table('memory_retrievals', schema='sre')
    op.drop_table('memory_events', schema='sre')
    op.drop_table('memory_versions', schema='sre')
    op.drop_table('memory_records', schema='sre')
