"""Guardrails Management & Monitoring Center.

* Yalnız yeni tablolar EKLENİR; mevcut tablolara dokunulmaz.
* Yerleşik policy'ler kodda tanımlıdır (airlab_agent.guardrails.catalog); platform açılışta eksik olanları ekler.
* guardrail_audit_events append-only'dir (UPDATE / DELETE / TRUNCATE tetikleyiciyle reddedilir) ve hash zinciri taşır.
  Retention silmesi yalnız `sre.guardrail_audit_prune = on` oturum ayarıyla ve en eski kayıtlardan yapılabilir.
Rollback: `--downgrade 0018` tabloları siler (policy, değerlendirme, ihlal, onay ve audit kayıtları kaybolur).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-27 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

S = 'sre'


def _uuid_pk():
    return sa.Column('id', pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()'))


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.func.now() if not nullable else None)


def _jsonb(name, default="'{}'::jsonb", nullable=False):
    return sa.Column(name, pg.JSONB, nullable=nullable, server_default=sa.text(default) if not nullable else None)


def upgrade() -> None:
    op.create_table(
        'guardrail_policies', _uuid_pk(),
        sa.Column('key', sa.String(80), nullable=False, unique=True),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('category', sa.String(32), nullable=False),
        sa.Column('policy_type', sa.String(48), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
        sa.Column('mandatory', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('builtin', sa.Boolean, nullable=False, server_default=sa.false()),
        _jsonb('stages', "'[]'::jsonb"),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('priority', sa.Integer, nullable=False, server_default='100'),
        _jsonb('params'),
        sa.Column('bound_to', sa.String(64)),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('latest_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('created_by', sa.String(64)), sa.Column('updated_by', sa.String(64)),
        _ts('created_at'), _ts('updated_at'), _ts('last_triggered_at', True),
        sa.CheckConstraint("mode IN ('monitor','enforce','disabled')", name='ck_guardrail_policy_mode'),
        sa.CheckConstraint("status IN ('active','inactive','archived')", name='ck_guardrail_policy_status'),
        sa.CheckConstraint("NOT mandatory OR mode = 'enforce'", name='ck_guardrail_policy_mandatory_enforce'),
        schema=S)
    op.create_index('ix_guardrail_policies_category', 'guardrail_policies', ['category'], schema=S)
    op.create_table(
        'guardrail_policy_versions', _uuid_pk(),
        sa.Column('policy_id', pg.UUID(as_uuid=True), sa.ForeignKey(f'{S}.guardrail_policies.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        _jsonb('snapshot'),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('change', sa.String(24), nullable=False),
        sa.Column('note', sa.Text), sa.Column('created_by', sa.String(64)), _ts('created_at'),
        sa.UniqueConstraint('policy_id', 'version', name='uq_guardrail_policy_version'), schema=S)
    op.create_table(
        'guardrail_policy_assignments', _uuid_pk(),
        sa.Column('policy_id', pg.UUID(as_uuid=True), sa.ForeignKey(f'{S}.guardrail_policies.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('scope', sa.String(24), nullable=False),
        sa.Column('value', sa.String(160)),
        sa.CheckConstraint("scope IN ('global','tenant','environment','agent','mcp_server','tool','graph_node')",
                           name='ck_guardrail_assignment_scope'),
        sa.UniqueConstraint('policy_id', 'scope', 'value', name='uq_guardrail_assignment'), schema=S)
    op.create_table(
        'guardrail_evaluations',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('environment', sa.String(64)),
        sa.Column('run_id', sa.String(160)), sa.Column('thread_id', sa.String(160)),
        sa.Column('run_kind', sa.String(24)), sa.Column('agent_id', sa.String(64)),
        sa.Column('agent_key', sa.String(64)), sa.Column('agent_version', sa.String(32)),
        sa.Column('user_id', sa.String(64)), sa.Column('user_role', sa.String(16)),
        sa.Column('node', sa.String(64)), sa.Column('stage', sa.String(24), nullable=False),
        sa.Column('policy_key', sa.String(80), nullable=False), sa.Column('policy_id', pg.UUID(as_uuid=True)),
        sa.Column('policy_version', sa.Integer), sa.Column('category', sa.String(32), nullable=False),
        sa.Column('decision', sa.String(24), nullable=False),
        sa.Column('enforced', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('severity', sa.String(16)), sa.Column('action', sa.String(24)),
        sa.Column('reason', sa.String(300)), sa.Column('code', sa.String(64)),
        sa.Column('count', sa.Integer, nullable=False, server_default='1'),
        sa.Column('duration_ms', sa.Float), sa.Column('duration_max_ms', sa.Float),
        _jsonb('latency_buckets', nullable=True), _jsonb('matched', "'[]'::jsonb"), _jsonb('thresholds'),
        _jsonb('changed_fields', "'[]'::jsonb"), _jsonb('snapshot', nullable=True), _jsonb('context', nullable=True),
        sa.Column('error', sa.String(200)), sa.Column('mcp_server', sa.String(64)), sa.Column('tool', sa.String(128)),
        sa.Column('model', sa.String(128)), sa.Column('provider', sa.String(32)),
        sa.Column('correlation_id', sa.String(64)), sa.Column('trace_id', sa.String(64)),
        sa.Column('parent_span_id', sa.String(64)), sa.Column('mcp_call_id', sa.String(64)),
        sa.Column('llm_call_id', sa.String(64)), sa.Column('approval_id', pg.UUID(as_uuid=True)),
        sa.Column('violation_id', pg.UUID(as_uuid=True)), sa.Column('bound_to', sa.String(64)),
        schema=S)
    op.create_index('ix_guardrail_eval_ts', 'guardrail_evaluations', [sa.text('ts DESC')], schema=S)
    op.create_index('ix_guardrail_eval_run', 'guardrail_evaluations', ['run_id'], schema=S)
    op.create_index('ix_guardrail_eval_policy_ts', 'guardrail_evaluations', ['policy_key', sa.text('ts DESC')], schema=S)
    op.create_index('ix_guardrail_eval_decision_ts', 'guardrail_evaluations', ['decision', sa.text('ts DESC')],
                    schema=S)
    op.create_index('ix_guardrail_eval_agent_ts', 'guardrail_evaluations', ['agent_id', sa.text('ts DESC')], schema=S)
    op.create_table(
        'guardrail_violations', _uuid_pk(),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False), sa.Column('category', sa.String(32), nullable=False),
        sa.Column('policy_key', sa.String(80), nullable=False), sa.Column('policy_id', pg.UUID(as_uuid=True)),
        sa.Column('agent_id', sa.String(64)), sa.Column('agent_key', sa.String(64)), sa.Column('run_id', sa.String(160)),
        sa.Column('node', sa.String(64)), sa.Column('stage', sa.String(24)), sa.Column('mcp_server', sa.String(64)),
        sa.Column('tool', sa.String(128)), sa.Column('reason', sa.String(300)), sa.Column('code', sa.String(64)),
        sa.Column('decision', sa.String(24), nullable=False), sa.Column('action_taken', sa.String(24)),
        sa.Column('enforced', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('occurrences', sa.Integer, nullable=False, server_default='1'),
        sa.Column('status', sa.String(24), nullable=False, server_default='open'),
        sa.Column('assigned_to', sa.String(64)), _jsonb('history', "'[]'::jsonb"),
        sa.Column('last_evaluation_id', pg.UUID(as_uuid=True)), _ts('updated_at', True),
        sa.Column('updated_by', sa.String(64)),
        sa.CheckConstraint("status IN ('open','acknowledged','investigating','resolved','false_positive')",
                           name='ck_guardrail_violation_status'),
        schema=S)
    op.create_index('ix_guardrail_violation_last', 'guardrail_violations', [sa.text('last_seen DESC')], schema=S)
    op.create_index('uq_guardrail_violation_open', 'guardrail_violations', ['tenant_id', 'fingerprint'], unique=True,
                    postgresql_where=sa.text("status IN ('open','acknowledged','investigating')"), schema=S)
    op.create_table(
        'guardrail_approvals',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('evaluation_id', pg.UUID(as_uuid=True)), sa.Column('agent_id', sa.String(64)),
        sa.Column('agent_key', sa.String(64)), sa.Column('run_id', sa.String(160)), sa.Column('thread_id', sa.String(160)),
        sa.Column('requested_action', sa.String(200), nullable=False),
        sa.Column('target_resource', sa.String(200), nullable=False),
        sa.Column('risk_level', sa.String(16), nullable=False), sa.Column('requested_by', sa.String(64), nullable=False),
        sa.Column('required_role', sa.String(16), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('reason', sa.String(300)), sa.Column('decision_reason', sa.String(1000)),
        sa.Column('decided_by', sa.String(64)), _ts('decided_at', True), _jsonb('action_params'),
        sa.Column('action_hash', sa.String(64), nullable=False), sa.Column('policy_key', sa.String(80)),
        sa.Column('policy_version', sa.Integer), _ts('consumed_at', True), sa.Column('consumed_by', sa.String(64)),
        sa.Column('result', sa.String(300)),
        sa.CheckConstraint("status IN ('pending','approved','rejected','expired','cancelled','executed','failed')",
                           name='ck_guardrail_approval_status'),
        schema=S)
    op.create_index('ix_guardrail_approval_status', 'guardrail_approvals', ['status', sa.text('requested_at DESC')],
                    schema=S)
    op.create_table(
        'guardrail_test_runs', _uuid_pk(),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'), _ts('created_at'),
        sa.Column('created_by', sa.String(64), nullable=False), sa.Column('test_type', sa.String(32), nullable=False),
        sa.Column('stage', sa.String(24), nullable=False), _jsonb('policy_keys', "'[]'::jsonb"), _jsonb('input'),
        _jsonb('context'), _jsonb('result'), sa.Column('decision', sa.String(24), nullable=False),
        sa.Column('duration_ms', sa.Float, nullable=False, server_default='0'),
        sa.Column('expected_decision', sa.String(24)), sa.Column('is_attack', sa.Boolean), schema=S)
    op.create_index('ix_guardrail_test_created', 'guardrail_test_runs', [sa.text('created_at DESC')], schema=S)
    op.create_table(
        'guardrail_config',
        sa.Column('id', sa.SmallInteger, primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False, server_default='1'),
        _jsonb('data'), sa.Column('updated_by', sa.String(64)), _ts('updated_at'),
        sa.CheckConstraint('id = 1', name='ck_guardrail_config_singleton'), schema=S)
    op.execute("INSERT INTO sre.guardrail_config (id, revision, data) VALUES (1, 1, '{}'::jsonb)")
    op.create_table(
        'guardrail_audit_events',
        sa.Column('seq', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'),
        sa.Column('actor', sa.String(64), nullable=False), sa.Column('action', sa.String(48), nullable=False),
        sa.Column('target', sa.String(160)), _jsonb('before', nullable=True), _jsonb('after', nullable=True),
        sa.Column('detail', sa.String(1000)), sa.Column('correlation_id', sa.String(64)),
        sa.Column('prev_hash', sa.String(64), nullable=False), sa.Column('hash', sa.String(64), nullable=False),
        schema=S)
    op.create_index('ix_guardrail_audit_ts', 'guardrail_audit_events', [sa.text('ts DESC')], schema=S)
    op.execute("""
        CREATE OR REPLACE FUNCTION sre.guardrail_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('sre.guardrail_audit_prune', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'sre.guardrail_audit_events is append-only (% denied)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$""")
    op.execute("""CREATE TRIGGER guardrail_audit_no_update BEFORE UPDATE OR DELETE ON sre.guardrail_audit_events
                  FOR EACH ROW EXECUTE FUNCTION sre.guardrail_audit_append_only()""")
    op.execute("""CREATE TRIGGER guardrail_audit_no_truncate BEFORE TRUNCATE ON sre.guardrail_audit_events
                  FOR EACH STATEMENT EXECUTE FUNCTION sre.guardrail_audit_append_only()""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS guardrail_audit_no_truncate ON sre.guardrail_audit_events")
    op.execute("DROP TRIGGER IF EXISTS guardrail_audit_no_update ON sre.guardrail_audit_events")
    op.execute("DROP FUNCTION IF EXISTS sre.guardrail_audit_append_only()")
    for t in ('guardrail_audit_events', 'guardrail_config', 'guardrail_test_runs', 'guardrail_approvals',
              'guardrail_violations', 'guardrail_evaluations', 'guardrail_policy_assignments',
              'guardrail_policy_versions', 'guardrail_policies'):
        op.drop_table(t, schema=S)
