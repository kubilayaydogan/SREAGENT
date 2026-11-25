"""Güvenlik P0: audit_events güvenlik olayları için genişletilir ve append-only yapılır.

* Yeni (nullable, geriye uyumlu) kolonlar: actor_type, source_ip, correlation_id, session_id, run_id, reason_code.
* BEFORE UPDATE OR DELETE satır tetikleyicisi ve TRUNCATE deyim tetikleyicisi → hata (append-only).
  Kalan risk: tablo sahibi rol tetikleyiciyi kaldırabilir; uygulama/sahip rol ayrımı P1.5'te (DB rolleri).

Geri alma: `sre-platform migrate --downgrade 0005` tetikleyicileri ve yeni kolonları kaldırır; satırlar korunur
(yeni kolonlardaki değerler kaybolur).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26 12:10:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

_COLS = [('actor_type', sa.String(16)), ('source_ip', sa.String(64)), ('correlation_id', sa.String(64)),
         ('session_id', sa.String(64)), ('run_id', sa.String(64)), ('reason_code', sa.String(48))]


def upgrade() -> None:
    for name, typ in _COLS:
        op.add_column('audit_events', sa.Column(name, typ), schema='sre')
    op.create_index('ix_audit_action', 'audit_events', ['action'], schema='sre')
    op.create_index('ix_audit_actor_ts', 'audit_events', ['actor', sa.literal_column('ts DESC')], schema='sre')
    op.execute("""
        CREATE OR REPLACE FUNCTION sre.audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'sre.audit_events is append-only (% denied)', TG_OP USING ERRCODE = 'insufficient_privilege';
        END $$""")
    op.execute("""CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON sre.audit_events
                  FOR EACH ROW EXECUTE FUNCTION sre.audit_events_append_only()""")
    op.execute("""CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON sre.audit_events
                  FOR EACH STATEMENT EXECUTE FUNCTION sre.audit_events_append_only()""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON sre.audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON sre.audit_events")
    op.execute("DROP FUNCTION IF EXISTS sre.audit_events_append_only()")
    op.drop_index('ix_audit_actor_ts', table_name='audit_events', schema='sre')
    op.drop_index('ix_audit_action', table_name='audit_events', schema='sre')
    for name, _ in reversed(_COLS):
        op.drop_column('audit_events', name, schema='sre')
