"""llm_runs — LangSmith'ten senkronlanan LLM çağrısı / trace özetleri (içerik yok, yalnız sayılar).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25 16:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('llm_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('trace_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('trigger', sa.String(length=32), nullable=True),
    sa.Column('investigation_id', sa.UUID(), nullable=True),
    sa.Column('conversation_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('first_token_ms', sa.Integer(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    schema='sre'
    )
    op.create_index('ix_llm_runs_kind_start', 'llm_runs', ['kind', sa.literal_column('start_time DESC')],
                    unique=False, schema='sre')
    op.create_index('ix_llm_runs_trace', 'llm_runs', ['trace_id'], unique=False, schema='sre')


def downgrade() -> None:
    op.drop_index('ix_llm_runs_trace', table_name='llm_runs', schema='sre')
    op.drop_index('ix_llm_runs_kind_start', table_name='llm_runs', schema='sre')
    op.drop_table('llm_runs', schema='sre')
