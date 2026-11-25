"""Güvenlik P0: sunucu tarafı oturumlar ve kalıcı login throttle.

* auth_sessions: her access token'ın `jti`'si bir oturum satırıdır. İstek başına aktiflik (iptal, mutlak süre,
  idle süre) kontrol edilir; logout / parola değişikliği / kullanıcı devre dışı / rol değişikliği oturumları iptal eder.
* auth_login_failures: başarısız giriş denemeleri (kullanıcı adı + istemci IP anahtarı). Restart sonrası da geçerli.

UYUMSUZ DEĞİŞİKLİK: bu migration'dan önce üretilmiş token'ların oturum satırı yoktur → deploy sonrası herkes yeniden
giriş yapar.

Geri alma: `sre-platform migrate --downgrade 0004` iki tabloyu siler (yeniden giriş gerekir).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('user_id', pg.UUID(as_uuid=True), sa.ForeignKey('sre.users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_reason', sa.String(32)),
        sa.Column('ip', sa.String(64)),
        sa.Column('user_agent', sa.String(256)),
        schema='sre')
    op.create_index('ix_auth_sessions_user', 'auth_sessions', ['user_id'], schema='sre')
    op.create_index('ix_auth_sessions_expires', 'auth_sessions', ['expires_at'], schema='sre')
    op.create_table(
        'auth_login_failures',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('key', sa.String(200), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        schema='sre')
    op.create_index('ix_auth_login_failures_key_ts', 'auth_login_failures', ['key', 'ts'], schema='sre')


def downgrade() -> None:
    op.drop_index('ix_auth_login_failures_key_ts', table_name='auth_login_failures', schema='sre')
    op.drop_table('auth_login_failures', schema='sre')
    op.drop_index('ix_auth_sessions_expires', table_name='auth_sessions', schema='sre')
    op.drop_index('ix_auth_sessions_user', table_name='auth_sessions', schema='sre')
    op.drop_table('auth_sessions', schema='sre')
