"""Episodic memory: agent_episodes (investigation başına 'belirti → hipotez → sonuç' özeti).

Tablo türetilmiştir: kaynak kayıtlar (investigations, agent_feedback, incidents) değişmez. Mevcut kayıtlar için
episode'lar `sre-platform migrate` sırasında (idempotent backfill) oluşturulur.

Geri alma: `sre-platform migrate --downgrade 0003` tabloyu siler; agent episode'suz (yalnız ilişkisel arama + Store)
çalışmaya devam eder. Kaynak kayıtlar etkilenmez.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

_DOC = ("coalesce(summary, '') || ' ' || coalesce(hypothesis, '') || ' ' || coalesce(resolution, '') || ' ' || "
        "coalesce(feedback_note, '') || ' ' || coalesce(mechanism, '')")
_ARR = sa.text("'{}'")


def upgrade() -> None:
    op.create_table(
        'agent_episodes',
        sa.Column('investigation_id', pg.UUID(as_uuid=True),
                  sa.ForeignKey('sre.investigations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('incident_id', pg.UUID(as_uuid=True), sa.ForeignKey('sre.incidents.id', ondelete='SET NULL')),
        sa.Column('trigger', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('apps', pg.ARRAY(sa.Text()), server_default=_ARR, nullable=False),
        sa.Column('source_apps', pg.ARRAY(sa.Text()), server_default=_ARR, nullable=False),
        sa.Column('mechanism', sa.String(64)),
        sa.Column('mechanisms', pg.ARRAY(sa.Text()), server_default=_ARR, nullable=False),
        sa.Column('symptoms', pg.ARRAY(sa.Text()), server_default=_ARR, nullable=False),
        sa.Column('severity', sa.String(16), nullable=False),
        sa.Column('risk_score', sa.Numeric(5, 1)),
        sa.Column('confidence', sa.String(16)),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('hypothesis', sa.Text()),
        sa.Column('outcome', sa.String(24), server_default='unverified', nullable=False),
        sa.Column('feedback', pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('feedback_note', sa.Text()),
        sa.Column('resolution', sa.Text()),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('tsv', pg.TSVECTOR(), sa.Computed(f"to_tsvector('turkish', {_DOC}) || to_tsvector('simple', {_DOC})",
                                                    persisted=True)),
        sa.CheckConstraint("outcome IN ('unverified', 'confirmed', 'partially_confirmed', 'rejected')",
                           name='ck_episodes_outcome'),
        schema='sre')
    op.create_index('ix_episodes_created', 'agent_episodes', [sa.text('created_at DESC')], schema='sre')
    op.create_index('ix_episodes_symptoms', 'agent_episodes', ['symptoms'], schema='sre', postgresql_using='gin')
    op.create_index('ix_episodes_apps', 'agent_episodes', ['apps'], schema='sre', postgresql_using='gin')
    op.create_index('ix_episodes_mechanism', 'agent_episodes', ['mechanism'], schema='sre')
    op.create_index('ix_episodes_tsv', 'agent_episodes', ['tsv'], schema='sre', postgresql_using='gin')


def downgrade() -> None:
    op.drop_table('agent_episodes', schema='sre')
