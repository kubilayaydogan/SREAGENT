"""Güvenlik P2.4: secret yaşam döngüsü metadata'sı (sre.secrets: expires_at, rotation_days, description).

Mevcut secret'lara rotation_days=90 atanır (yalnız uyarı üretir; kullanımı engellemez). expires_at boş kalır.
Geri alma: `--downgrade 0009` kolonları siler; secret değerleri etkilenmez.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('secrets', sa.Column('expires_at', sa.DateTime(timezone=True)), schema='sre')
    op.add_column('secrets', sa.Column('rotation_days', sa.Integer, server_default=sa.text('90')), schema='sre')
    op.add_column('secrets', sa.Column('description', sa.String(256)), schema='sre')


def downgrade() -> None:
    for c in ('description', 'rotation_days', 'expires_at'):
        op.drop_column('secrets', c, schema='sre')
