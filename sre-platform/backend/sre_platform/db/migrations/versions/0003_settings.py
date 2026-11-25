"""Settings modülü: bölüm ayarları + revizyon geçmişi, MCP connector'ları, LLM profilleri, şifreli secret'lar,
audit kayıtları; investigation başına değişmez yapılandırma kopyası.

Geri alma: `sre-platform migrate --downgrade 0002` bu tabloları siler; mevcut analiz kayıtları etkilenmez
(yalnız investigations.config_snapshot sütunu kalkar). Ortam değişkenleriyle çalışma davranışı aynen sürer.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25 18:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSONB_EMPTY = sa.text("'{}'::jsonb")


def _ts(name: str, default: str = 'now()') -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text(default), nullable=False)


def upgrade() -> None:
    op.add_column('investigations', sa.Column('config_snapshot', pg.JSONB(), nullable=True), schema='sre')
    op.create_table('settings_sections',
                    sa.Column('section', sa.String(32), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False),
                    sa.Column('data', pg.JSONB(), nullable=False),
                    sa.Column('updated_by', sa.String(64)),
                    _ts('updated_at'),
                    sa.CheckConstraint('revision >= 1', name='ck_settings_revision'),
                    schema='sre')
    op.create_table('settings_revisions',
                    sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
                    sa.Column('section', sa.String(32), nullable=False),
                    sa.Column('revision', sa.Integer(), nullable=False),
                    sa.Column('data', pg.JSONB(), nullable=False),
                    sa.Column('actor', sa.String(64)),
                    sa.Column('note', sa.String(300)),
                    _ts('created_at', 'clock_timestamp()'),
                    sa.UniqueConstraint('section', 'revision', name='uq_settings_rev'),
                    schema='sre')
    op.create_table('connectors',
                    sa.Column('id', sa.UUID(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
                    sa.Column('name', sa.String(64), nullable=False, unique=True),
                    sa.Column('provider', sa.String(32), nullable=False),
                    sa.Column('transport', sa.String(32), nullable=False),
                    sa.Column('endpoint', sa.String(500), nullable=False),
                    sa.Column('auth_type', sa.String(16), nullable=False),
                    sa.Column('secret_ref', sa.String(128)),
                    sa.Column('timeout_s', sa.Integer(), nullable=False),
                    sa.Column('retries', sa.Integer(), nullable=False),
                    sa.Column('enabled', sa.Boolean(), nullable=False),
                    sa.Column('read_only', sa.Boolean(), nullable=False),
                    sa.Column('analysis_source', sa.Boolean(), nullable=False),
                    sa.Column('allowed_tools', pg.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
                    sa.Column('capabilities', pg.JSONB(), server_default=JSONB_EMPTY, nullable=False),
                    sa.Column('last_check', pg.JSONB(), server_default=JSONB_EMPTY, nullable=False),
                    sa.Column('managed', sa.String(16)),
                    sa.Column('revision', sa.Integer(), nullable=False),
                    _ts('created_at'), _ts('updated_at'),
                    sa.CheckConstraint('timeout_s BETWEEN 1 AND 300', name='ck_connector_timeout'),
                    sa.CheckConstraint('retries BETWEEN 0 AND 5', name='ck_connector_retries'),
                    schema='sre')
    # aynı anda en fazla bir analiz veri kaynağı (graph tek DQL kaynağı bekler)
    op.create_index('uq_connectors_analysis', 'connectors', ['analysis_source'], unique=True, schema='sre',
                    postgresql_where=sa.text('analysis_source'))
    op.create_table('llm_profiles',
                    sa.Column('id', sa.UUID(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
                    sa.Column('name', sa.String(64), nullable=False, unique=True),
                    sa.Column('provider', sa.String(32), nullable=False),
                    sa.Column('model', sa.String(128), nullable=False),
                    sa.Column('base_url', sa.String(500)),
                    sa.Column('secret_ref', sa.String(128)),
                    sa.Column('timeout_s', sa.Integer(), nullable=False),
                    sa.Column('max_tokens', sa.Integer(), nullable=False),
                    sa.Column('effort', sa.String(16)),
                    sa.Column('retries', sa.Integer(), nullable=False),
                    sa.Column('monthly_budget_usd', sa.Numeric(10, 2)),
                    sa.Column('enabled', sa.Boolean(), nullable=False),
                    sa.Column('last_check', pg.JSONB(), server_default=JSONB_EMPTY, nullable=False),
                    sa.Column('managed', sa.String(16)),
                    sa.Column('revision', sa.Integer(), nullable=False),
                    _ts('created_at'), _ts('updated_at'),
                    sa.CheckConstraint('timeout_s BETWEEN 5 AND 600', name='ck_llm_timeout'),
                    sa.CheckConstraint('max_tokens BETWEEN 256 AND 128000', name='ck_llm_max_tokens'),
                    schema='sre')
    op.create_table('secrets',
                    sa.Column('name', sa.String(64), primary_key=True),
                    sa.Column('ciphertext', sa.Text(), nullable=False),
                    sa.Column('hint', sa.String(16), nullable=False),
                    sa.Column('created_by', sa.String(64)),
                    _ts('created_at'),
                    sa.Column('rotated_at', sa.DateTime(timezone=True)),
                    schema='sre')
    op.create_table('audit_events',
                    sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
                    _ts('ts', 'clock_timestamp()'),
                    sa.Column('actor', sa.String(64), nullable=False),
                    sa.Column('action', sa.String(48), nullable=False),
                    sa.Column('section', sa.String(32), nullable=False),
                    sa.Column('target', sa.String(128)),
                    sa.Column('revision', sa.Integer()),
                    sa.Column('before', pg.JSONB()),
                    sa.Column('after', pg.JSONB()),
                    sa.Column('result', sa.String(16), nullable=False),
                    sa.Column('detail', sa.Text()),
                    schema='sre')
    op.create_index('ix_audit_ts', 'audit_events', [sa.literal_column('ts DESC')], schema='sre')
    op.create_index('ix_audit_section', 'audit_events', ['section'], schema='sre')


def downgrade() -> None:
    op.drop_index('ix_audit_section', table_name='audit_events', schema='sre')
    op.drop_index('ix_audit_ts', table_name='audit_events', schema='sre')
    op.drop_table('audit_events', schema='sre')
    op.drop_table('secrets', schema='sre')
    op.drop_table('llm_profiles', schema='sre')
    op.drop_index('uq_connectors_analysis', table_name='connectors', schema='sre')
    op.drop_table('connectors', schema='sre')
    op.drop_table('settings_revisions', schema='sre')
    op.drop_table('settings_sections', schema='sre')
    op.drop_column('investigations', 'config_snapshot', schema='sre')
