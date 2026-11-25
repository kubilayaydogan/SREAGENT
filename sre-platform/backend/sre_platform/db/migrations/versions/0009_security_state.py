"""Güvenlik P2.3: SIEM iletimi ve alarm kuralları için durum tablosu (sre.security_state).

Olayların kendisi sre.audit_events'tedir (outbox = audit tablosu + cursor). Geri alma: `--downgrade 0008` tabloyu
siler; SIEM cursor'ı kaybolur (yeniden açıldığında o andan itibaren gönderilir), alarm cooldown'ları sıfırlanır.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25 23:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'security_state',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('value', pg.JSONB, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='sre')


def downgrade() -> None:
    op.drop_table('security_state', schema='sre')
