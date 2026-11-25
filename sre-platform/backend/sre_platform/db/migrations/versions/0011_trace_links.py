"""LangSmith trace eşlemesi (sre.trace_links): platform run_id ↔ LangSmith run/trace kimliği.

Her çalıştırma girişimi (analiz ya da sohbet turu; checkpoint'ten sürdürülen tur yeni girişimdir) bir satırdır;
platform run_id ve LangSmith trace_id ayrı alanlardadır. Yalnız kimlik/metadata tutulur, trace içeriği tutulmaz.
Geri alma: `--downgrade 0010` tabloyu siler (trace linkleri yeniden yalnız investigation id'siyle kurulur).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-26 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'trace_links',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('platform_run_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('attempt', sa.Integer, nullable=False, server_default='1'),
        sa.Column('investigation_id', pg.UUID(as_uuid=True),
                  sa.ForeignKey('sre.investigations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('conversation_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('thread_id', sa.String(128), nullable=True),
        sa.Column('langsmith_run_id', pg.UUID(as_uuid=True), nullable=False),
        sa.Column('langsmith_trace_id', pg.UUID(as_uuid=True), nullable=True),
        sa.Column('project', sa.String(100), nullable=False),
        sa.Column('trigger', sa.String(16), nullable=True),
        sa.Column('mode', sa.String(32), nullable=True),
        sa.Column('resumed', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('analysis', 'chat_turn')", name='ck_trace_links_kind'),
        sa.CheckConstraint('attempt >= 1', name='ck_trace_links_attempt'),
        schema='sre')
    op.create_index('ix_trace_links_platform_run', 'trace_links', ['platform_run_id', 'attempt'], schema='sre')
    op.create_index('ix_trace_links_investigation', 'trace_links', ['investigation_id'], schema='sre')
    op.create_index('ix_trace_links_created', 'trace_links', ['created_at'], schema='sre')


def downgrade() -> None:
    op.drop_table('trace_links', schema='sre')
