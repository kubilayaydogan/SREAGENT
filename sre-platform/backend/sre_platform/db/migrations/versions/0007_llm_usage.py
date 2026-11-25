"""Güvenlik P1.6: LangSmith'ten bağımsız LLM kullanım kaydı.

Her koşu (sohbet turu / investigation) sonunda koşuya ait LLM çağrıları (görev, model, token) yazılır; koşu başına
sınır nedeniyle yapılmayan çağrılar `denied=true` satırıdır. Prompt / yanıt içeriği TUTULMAZ.

Geri alma: `--downgrade 0006` tabloyu siler (yalnız kullanım geçmişi kaybolur).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25 20:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'llm_usage',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('run_id', sa.String(64), nullable=False),
        sa.Column('trigger', sa.String(32)),
        sa.Column('investigation_id', pg.UUID(as_uuid=True)),
        sa.Column('conversation_id', pg.UUID(as_uuid=True)),
        sa.Column('task', sa.String(48), nullable=False),
        sa.Column('model', sa.String(128)),
        sa.Column('input_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('denied', sa.Boolean, nullable=False, server_default=sa.false()),
        schema='sre')
    op.create_index('ix_llm_usage_ts', 'llm_usage', [sa.text('ts DESC')], schema='sre')
    op.create_index('ix_llm_usage_run', 'llm_usage', ['run_id'], schema='sre')


def downgrade() -> None:
    op.drop_table('llm_usage', schema='sre')
