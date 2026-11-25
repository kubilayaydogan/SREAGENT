"""Güvenlik P2.2: service account API anahtarları (sre.api_keys).

Anahtar düz metin tutulmaz (HMAC-SHA256 özeti). Geri alma: `--downgrade 0007` tabloyu siler; tüm API anahtarları
geçersiz olur (oturumla giriş etkilenmez).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-25 22:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('prefix', sa.String(16), nullable=False, unique=True),
        sa.Column('key_hash', sa.String(64), nullable=False),
        sa.Column('owner_id', pg.UUID(as_uuid=True), sa.ForeignKey('sre.users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scopes', pg.JSONB, nullable=False),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True)),
        sa.Column('last_used_ip', sa.String(64)),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_reason', sa.String(32)),
        schema='sre')
    op.create_index('ix_api_keys_owner', 'api_keys', ['owner_id'], schema='sre')


def downgrade() -> None:
    op.drop_table('api_keys', schema='sre')
