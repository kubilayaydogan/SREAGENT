"""SRE platform veritabanı modeli (SQLAlchemy 2.x, PostgreSQL 17, şema `sre`).

İlkeler (SRE-Frontend-DB.md §3, §9):
* Dynatrace ana kaynak olarak kalır; burada yalnız analiz metadatası, kanıt ÖZETİ, sorgu referansı (DQL +
  Dynatrace queryId), trace/entity kimlikleri, RCA, incident yaşam döngüsü ve araç geçmişi saklanır. Ham metrik,
  log ve trace kopyalanmaz.
* Gizli değer (token, parola) saklanmaz; parola yalnız scrypt özetidir. Metin alanları yazılmadan önce redakte edilir.
* Geçmiş araması: Türkçe + `simple` tam metin araması (STORED generated tsvector + GIN).
* LangGraph checkpoint/store tabloları ayrı `langgraph` şemasındadır (kütüphane kendi `setup()`'ı ile kurar).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Computed, DateTime, Enum, Float, ForeignKey, Index,
                        Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "sre"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: ARRAY(Text)}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                         server_default=text("gen_random_uuid()"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _created_clock() -> Mapped[datetime]:
    """Aynı transaction içindeki sıralı kayıtlar için (now() transaction başı sabittir → sıra kaybolur)."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


def _tsv(*cols: str) -> Mapped[Any]:
    """Türkçe kök + `simple` (teknik terimler, kimlikler) birleşik arama vektörü."""
    doc = " || ' ' || ".join(f"coalesce({c}, '')" for c in cols)
    return mapped_column(TSVECTOR, Computed(f"to_tsvector('turkish', {doc}) || to_tsvector('simple', {doc})",
                                            persisted=True), nullable=True)


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, schema=SCHEMA, values_callable=lambda x: [m.value for m in x])


# ---------------------------------------------------------------- enumlar
class Role(str, enum.Enum):
    ADMIN = "admin"
    SRE = "sre"
    VIEWER = "viewer"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class InvestigationStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"          # veri eksik / MCP kesintisi: rapor var ama kısmi
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"          # puan hesaplanamadı (veri kapsamı yetersiz) — "düşük" ile karıştırılmaz


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Trigger(str, enum.Enum):
    CHAT = "chat"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WATCH = "watch"
    IMPORT = "import"


class IncidentStatus(str, enum.Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"


class IncidentSource(str, enum.Enum):
    WATCH = "watch"
    MANUAL = "manual"
    AGENT = "agent"


class EvidenceType(str, enum.Enum):
    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    EVENT = "EVENT"
    PROBLEM = "PROBLEM"
    TOPOLOGY = "TOPOLOGY"


class ToolStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"          # bütçe doldu


class Verdict(str, enum.Enum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    INCORRECT = "INCORRECT"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, enum.Enum):
    RESTART = "RESTART"
    ROLLBACK = "ROLLBACK"
    DB_CONFIG = "DB_CONFIG"
    SQL_WRITE = "SQL_WRITE"
    INFRA = "INFRA"
    CONTAINER_STOP = "CONTAINER_STOP"


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class JobStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# ---------------------------------------------------------------- kullanıcı / uygulama
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[uuid.UUID] = _pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), nullable=False, default=Role.VIEWER)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Application(Base):
    """İzlenen uygulamalar. id = Dynatrace/Micrometer servis adı (ör. reservation-service)."""
    __tablename__ = "applications"
    __table_args__ = {"schema": SCHEMA}
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="business")   # business | tooling
    host: Mapped[str | None] = mapped_column(String(64))
    db_role: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- konuşma
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", text("updated_at DESC")), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)   # LangGraph checkpoint thread
    application_id: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="SET NULL"))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan",
                                                   order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),
                      Index("ix_messages_tsv", "tsv", postgresql_using="gin"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.conversations.id", ondelete="CASCADE"),
                                                       nullable=False)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                                 server_default=text("'{}'::jsonb"))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_clock()
    tsv: Mapped[Any] = _tsv("content")
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------- araştırma
class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        Index("ix_investigations_created", text("created_at DESC")),
        Index("ix_investigations_app_created", "application_id", text("created_at DESC")),
        Index("ix_investigations_status", "status"),
        Index("ix_investigations_severity", "severity"),
        Index("ix_investigations_tsv", "tsv", postgresql_using="gin"),
        CheckConstraint("window_end > window_start", name="ck_investigations_window"),
        CheckConstraint("risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)", name="ck_investigations_score"),
        CheckConstraint("data_coverage IS NULL OR (data_coverage >= 0 AND data_coverage <= 1)",
                        name="ck_investigations_coverage"),
        {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.conversations.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"))
    application_id: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="SET NULL"))
    run_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)   # agent run / checkpoint thread
    trigger: Mapped[Trigger] = mapped_column(_enum(Trigger, "investigation_trigger"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)                             # kullanıcı sorusu / tetikleyici
    status: Mapped[InvestigationStatus] = mapped_column(_enum(InvestigationStatus, "investigation_status"),
                                                        nullable=False, default=InvestigationStatus.PENDING)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"), nullable=False, default=Severity.UNKNOWN)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    data_coverage: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confidence: Mapped[Confidence | None] = mapped_column(_enum(Confidence, "confidence"))
    primary_mechanism: Mapped[str | None] = mapped_column(String(64))
    source_apps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                   server_default=text("'{}'"))
    affected_apps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                     server_default=text("'{}'"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    root_cause_hypothesis: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str | None] = mapped_column(String(128))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)                     # deterministik rapor (redakte)
    error: Mapped[str | None] = mapped_column(Text)
    # Koşunun başında alınan değişmez yapılandırma kopyası (bölüm revizyonları + etkili değerler; secret YOK).
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tsv: Mapped[Any] = _tsv("title", "description", "root_cause_hypothesis", "explanation", "primary_mechanism")
    evidence: Mapped[list[InvestigationEvidence]] = relationship(back_populates="investigation",
                                                                 cascade="all, delete-orphan")
    tools: Mapped[list[ToolExecution]] = relationship(back_populates="investigation", cascade="all, delete-orphan",
                                                      order_by="ToolExecution.created_at")


class InvestigationEvidence(Base):
    __tablename__ = "investigation_evidence"
    __table_args__ = (UniqueConstraint("investigation_id", "evidence_key", name="uq_evidence_key"),
                      Index("ix_evidence_investigation", "investigation_id"),
                      Index("ix_evidence_trace", "trace_id", postgresql_where=text("trace_id IS NOT NULL")),
                      Index("ix_evidence_entity", "entity_id", postgresql_where=text("entity_id IS NOT NULL")),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(32), nullable=False)            # ev-xxxxxxxxxx (rapordaki kimlik)
    evidence_type: Mapped[EvidenceType] = mapped_column(_enum(EvidenceType, "evidence_type"), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)                  # dynatrace-mcp:execute-dql
    signal: Mapped[str | None] = mapped_column(String(64))                           # katalog sinyali (http, edges…)
    data_status: Mapped[str | None] = mapped_column(String(16))                      # OK/EMPTY/PERMISSION/…/true_zero
    application_id: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(32))
    metric_name: Mapped[str | None] = mapped_column(String(200))
    query_reference: Mapped[str | None] = mapped_column(Text)                         # DQL (yeniden çalıştırılabilir)
    dynatrace_query_id: Mapped[str | None] = mapped_column(String(64))
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str | None] = mapped_column(Text)                                 # kısa özet (ham veri değil)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                                 server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_clock()
    investigation: Mapped[Investigation] = relationship(back_populates="evidence")


class ToolExecution(Base):
    __tablename__ = "agent_tool_executions"
    __table_args__ = (Index("ix_tools_investigation", "investigation_id", "created_at"),
                      Index("ix_tools_created", "created_at"),
                      Index("ix_tools_mcp_created", text("created_at DESC"),
                            postgresql_where=text("mcp_method IS NOT NULL")),
                      CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_tools_duration"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), nullable=False)
    step: Mapped[str | None] = mapped_column(String(64))                              # graf düğümü (collect, extra…)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)               # execute-dql, claude:explain…
    signal: Mapped[str | None] = mapped_column(String(64))
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                           server_default=text("'{}'::jsonb"))
    execution_status: Mapped[ToolStatus] = mapped_column(_enum(ToolStatus, "tool_status"), nullable=False)
    result_status: Mapped[str | None] = mapped_column(String(16))
    record_count: Mapped[int | None] = mapped_column(Integer)
    dynatrace_query_id: Mapped[str | None] = mapped_column(String(64))
    evidence_key: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    # MCP çağrı kaydı (0012): yalnız gönderilen tools/call'larda mcp_method dolu; ham DQL/yanıt/token yazılmaz
    mcp_method: Mapped[str | None] = mapped_column(String(32))
    connector: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(24))      # success|empty|timeout|permission_denied|tool_error…
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list,
                                                           server_default=text("'[]'::jsonb"))
    request_preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                            server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_clock()
    investigation: Mapped[Investigation] = relationship(back_populates="tools")


# ---------------------------------------------------------------- incident
class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_detected", "status", text("detected_at DESC")),
        Index("ix_incidents_app", "application_id"),
        Index("ix_incidents_tsv", "tsv", postgresql_using="gin"),
        # dedup: aynı anahtar için aynı anda tek açık (RESOLVED olmayan) incident
        Index("uq_incidents_open_dedup", "dedup_key", unique=True,
              postgresql_where=text("dedup_key IS NOT NULL AND status <> 'RESOLVED'")),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= detected_at", name="ck_incidents_resolved"),
        {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="SET NULL"))              # tetikleyen/birincil araştırma
    application_id: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"), nullable=False, default=Severity.UNKNOWN)
    status: Mapped[IncidentStatus] = mapped_column(_enum(IncidentStatus, "incident_status"), nullable=False,
                                                   default=IncidentStatus.OPEN)
    source: Mapped[IncidentSource] = mapped_column(_enum(IncidentSource, "incident_source"), nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(String(200))
    conditions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                  server_default=text("'{}'"))      # watch anahtarları
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    root_cause: Mapped[str | None] = mapped_column(Text)
    recommended_actions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                           server_default=text("'{}'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tsv: Mapped[Any] = _tsv("title", "description", "root_cause")
    events: Mapped[list[IncidentEvent]] = relationship(back_populates="incident", cascade="all, delete-orphan",
                                                       order_by="IncidentEvent.at")


class IncidentInvestigation(Base):
    """Incident ↔ araştırma (çoka çok): bir incident için birden fazla araştırma olabilir."""
    __tablename__ = "incident_investigations"
    __table_args__ = {"schema": SCHEMA}
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.incidents.id", ondelete="CASCADE"),
                                                   primary_key=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="related")   # trigger|related|recovery
    created_at: Mapped[datetime] = _created()


class IncidentEvent(Base):
    """Incident zaman çizelgesi (durum geçişleri, notlar, bağlanan araştırmalar)."""
    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident", "incident_id", "at"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.incidents.id", ondelete="CASCADE"),
                                                   nullable=False)
    at: Mapped[datetime] = _created_clock()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)                    # status|note|investigation|condition
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"))
    message: Mapped[str | None] = mapped_column(Text)
    incident: Mapped[Incident] = relationship(back_populates="events")


# ---------------------------------------------------------------- RCA / geri bildirim
class RcaReport(Base):
    __tablename__ = "rca_reports"
    __table_args__ = (Index("ix_rca_generated", text("generated_at DESC")),
                      Index("ix_rca_investigation", "investigation_id"),
                      Index("ix_rca_incident", "incident_id"),
                      Index("ix_rca_tsv", "tsv", postgresql_using="gin"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.incidents.id", ondelete="SET NULL"))
    application_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)                        # executive summary
    problem_description: Mapped[str | None] = mapped_column(Text)
    timeline: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    impact_analysis: Mapped[str | None] = mapped_column(Text)
    suspected_root_cause: Mapped[str | None] = mapped_column(Text)
    supporting_evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list,
                                                           server_default=text("'[]'::jsonb"))   # [{key, type, summary}]
    contributing_factors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                            server_default=text("'{}'"))
    recommended_actions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                           server_default=text("'{}'"))
    alternatives: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                    server_default=text("'{}'"))
    data_gaps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
    confidence: Mapped[Confidence | None] = mapped_column(_enum(Confidence, "confidence"))
    uncertainty: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(128), nullable=False)           # deterministik | Claude (model)
    generated_at: Mapped[datetime] = _created()
    tsv: Mapped[Any] = _tsv("title", "summary", "suspected_root_cause", "impact_analysis")


class AgentFeedback(Base):
    __tablename__ = "agent_feedback"
    __table_args__ = (UniqueConstraint("investigation_id", "user_id", name="uq_feedback_user"),
                      CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_feedback_rating"),
                      Index("ix_feedback_created", "created_at"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), nullable=False)
    rca_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.rca_reports.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False)
    verdict: Mapped[Verdict] = mapped_column(_enum(Verdict, "feedback_verdict"), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, Computed("verdict = 'CORRECT'", persisted=True))
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------------------------------------------------------------- bilgi bankası
class KnownIssue(Base):
    __tablename__ = "known_issues"
    __table_args__ = (Index("ix_known_issues_tsv", "tsv", postgresql_using="gin"),
                      Index("ix_known_issues_mechanism", "mechanism"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="SET NULL"))
    mechanism: Mapped[str | None] = mapped_column(String(64))                        # row_lock, pool_saturation…
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    symptoms: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    origin: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")  # manual | seed:<kaynak> | rca
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))       # 0015
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))                           # 0015
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tsv: Mapped[Any] = _tsv("title", "description", "symptoms", "root_cause", "resolution", "mechanism")


class Runbook(Base):
    __tablename__ = "runbooks"
    __table_args__ = (Index("ix_runbooks_tsv", "tsv", postgresql_using="gin"),
                      Index("ix_runbooks_mechanism", "mechanism"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    application_id: Mapped[str | None] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id", ondelete="SET NULL"))
    mechanism: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    procedure: Mapped[str] = mapped_column(Text, nullable=False)                      # Markdown
    risk_level: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "risk_level"), nullable=False,
                                                  default=RiskLevel.LOW)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                    server_default=text("false"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
    origin: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    # 0015 procedural memory yaşam döngüsü: agent yalnız 'approved' runbook'u görür; düzenleme onaya kadar ayrı revizyon
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved",
                                                 server_default=text("'approved'"))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    tsv: Mapped[Any] = _tsv("title", "description", "procedure", "mechanism")


# ---------------------------------------------------------------- human-in-the-loop
class ApprovalRequest(Base):
    """Yazma/müdahale eylemleri için onay kaydı. MVP: onay KAYDEDİLİR, eylem otomatik ÇALIŞTIRILMAZ."""
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approvals_status", "status", text("created_at DESC")),
                      CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="ck_approval_four_eyes"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="SET NULL"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.incidents.id", ondelete="SET NULL"))
    runbook_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.runbooks.id", ondelete="SET NULL"))
    action_type: Mapped[ActionType] = mapped_column(_enum(ActionType, "action_type"), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                    server_default=text("'{}'::jsonb"))
    status: Mapped[ApprovalStatus] = mapped_column(_enum(ApprovalStatus, "approval_status"), nullable=False,
                                                   default=ApprovalStatus.PENDING)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
                                                    nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- zamanlanmış işler / watch durumu
class ScheduledJob(Base):
    """Periyodik işler. Worker'lar `FOR UPDATE SKIP LOCKED` + kira (lease) ile alır → aynı iş iki kez çalışmaz."""
    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_jobs_due", "next_run_at", postgresql_where=text("enabled")),
                      CheckConstraint("interval_seconds >= 10", name="ck_jobs_interval"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)                # watch | health_check | retention
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                   server_default=text("'{}'::jsonb"))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_job_started", "job_id", text("started_at DESC")), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.scheduled_jobs.id", ondelete="CASCADE"),
                                              nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                   server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)


class WatchCondition(Base):
    """Watch durum makinesi (eskiden SQLite `cond`). Anahtar: latency:checkin-service, lock:db…"""
    __tablename__ = "watch_conditions"
    __table_args__ = {"schema": SCHEMA}
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = _updated()


class WatchWindow(Base):
    """Kayan baseline geçmişi: değerlendirilmiş 1 dk pencerelerin metrik özeti."""
    __tablename__ = "watch_windows"
    __table_args__ = {"schema": SCHEMA}
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class WatchEvent(Base):
    __tablename__ = "watch_events"
    __table_args__ = (Index("ix_watch_events_ts", text("ts DESC")), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                   server_default=text("'{}'::jsonb"))


class KeyValue(Base):
    """Küçük kalıcı durum (watch last_end, mcp_down…)."""
    __tablename__ = "kv"
    __table_args__ = {"schema": SCHEMA}
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = _updated()


class LlmRun(Base):
    """LangSmith'ten senkronlanan özet (worker `langsmith-sync`). Prompt/cevap içeriği TUTULMAZ; yalnız sayılar ve kimlikler.
    kind='trace': kök trace (chat-turn, airlab-scheduled …; token/maliyet aynı trace_id'li llm satırlarının toplamıdır).
    kind='llm'  : tek LLM çağrısı (name = çağrının amacı: route_question, answer, explain_findings …)."""
    __tablename__ = "llm_runs"
    __table_args__ = (Index("ix_llm_runs_kind_start", "kind", text("start_time DESC")),
                      Index("ix_llm_runs_trace", "trace_id"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)          # LangSmith run id
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str | None] = mapped_column(String(32))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)                       # success | error | pending
    error: Mapped[str | None] = mapped_column(Text)                                        # redakte, kısaltılmış
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    synced_at: Mapped[datetime] = _updated()


# ---------------------------------------------------------------------------------------------- Settings modülü
class SettingsSection(Base):
    """Bölüm başına yöneticinin kaydettiği değerler (yalnız şemadan geçmiş, secret içermeyen alanlar).
    Kayıt yoksa ortam/uygulama varsayılanları geçerlidir. `revision` iyimser eşzamanlılık için artar."""
    __tablename__ = "settings_sections"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_settings_revision"), {"schema": SCHEMA})
    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = _updated()


class SettingsRevision(Base):
    """Değişmez revizyon geçmişi. Rollback eski veriyi YENİ bir revizyon olarak yazar."""
    __tablename__ = "settings_revisions"
    __table_args__ = (UniqueConstraint("section", "revision", name="uq_settings_rev"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_clock()


class Connector(Base):
    """MCP sunucu bağlantısı. Secret değeri burada DEĞİL: `secret_ref` (env:AD / secret:ad) tutulur."""
    __tablename__ = "connectors"
    __table_args__ = (CheckConstraint("timeout_s BETWEEN 1 AND 300", name="ck_connector_timeout"),
                      CheckConstraint("retries BETWEEN 0 AND 5", name="ck_connector_retries"),
                      Index("uq_connectors_analysis", "analysis_source", unique=True,
                            postgresql_where=text("analysis_source")), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)            # dynatrace | custom
    transport: Mapped[str] = mapped_column(String(32), nullable=False)           # streamable_http
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)           # bearer | none
    secret_ref: Mapped[str | None] = mapped_column(String(128))
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    analysis_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # analiz grafiğinin veri kaynağı
    allowed_tools: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                     server_default=text("'{}'"))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                         server_default=text("'{}'::jsonb"))
    last_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                       server_default=text("'{}'::jsonb"))
    managed: Mapped[str | None] = mapped_column(String(16))                       # 'bootstrap' = ortamdan tohumlandı
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class LlmProfile(Base):
    __tablename__ = "llm_profiles"
    __table_args__ = (CheckConstraint("timeout_s BETWEEN 5 AND 600", name="ck_llm_timeout"),
                      CheckConstraint("max_tokens BETWEEN 256 AND 128000", name="ck_llm_max_tokens"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)            # anthropic (doğrulanmış adapter)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    secret_ref: Mapped[str | None] = mapped_column(String(128))
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=16000)
    effort: Mapped[str | None] = mapped_column(String(16))                       # low | medium | high
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                       server_default=text("'{}'::jsonb"))
    managed: Mapped[str | None] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class StoredSecret(Base):
    """Şifreli secret (Fernet; anahtar SRE_SETTINGS_KEY ortamında, veritabanında DEĞİL). Değer hiçbir API'de dönmez."""
    __tablename__ = "secrets"
    __table_args__ = {"schema": SCHEMA}
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str] = mapped_column(String(16), nullable=False)               # yalnız uzunluk ipucu
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # P2.4 yaşam döngüsü metadata'sı: süresi dolan secret çözülmez (fail-closed); rotation süresinin geçmesi yalnız uyarıdır
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotation_days: Mapped[int | None] = mapped_column(Integer, server_default=text("90"))
    description: Mapped[str | None] = mapped_column(String(256))


class AuditEvent(Base):
    """Yapılandırma değişikliği denetim kaydı (redakte before/after). Uygulama yalnız INSERT eder."""
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ts", text("ts DESC")), Index("ix_audit_section", "section"),
                      Index("ix_audit_action", "action"), Index("ix_audit_actor_ts", "actor", text("ts DESC")),
                      {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _created_clock()
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128))
    revision: Mapped[int | None] = mapped_column(Integer)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(16), nullable=False)               # ok | rejected | error | denied
    detail: Mapped[str | None] = mapped_column(Text)
    # 0006: güvenlik olayı bağlamı (DB tetikleyicisiyle append-only)
    actor_type: Mapped[str | None] = mapped_column(String(16))                    # user | system | anonymous
    source_ip: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str | None] = mapped_column(String(48))


class AuthSession(Base):
    """Sunucu tarafı oturum: access token `jti`'si. Her istekte iptal / mutlak süre / idle süre kontrol edilir."""
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user", "user_id"), Index("ix_auth_sessions_expires", "expires_at"),
                      {"schema": SCHEMA})
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
                                               nullable=False)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class AuthLoginFailure(Base):
    """Başarısız giriş denemesi (kalıcı throttle; anahtar = kullanıcı adı | istemci IP)."""
    __tablename__ = "auth_login_failures"
    __table_args__ = (Index("ix_auth_login_failures_key_ts", "key", "ts"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    ts: Mapped[datetime] = _created_clock()


class LlmUsage(Base):
    """Koşu başına LLM çağrı kaydı (P1.6; LangSmith'ten bağımsız). İçerik tutulmaz; denied = sınır nedeniyle yapılmadı."""
    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_ts", text("ts DESC")), Index("ix_llm_usage_run", "run_id"),
                      {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _created()
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str | None] = mapped_column(String(32))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    task: Mapped[str] = mapped_column(String(48), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    denied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class AgentEpisode(Base):
    """Episodic memory: bir investigation'ın 'ne görüldü → ne düşünüldü → ne çıktı' özeti.

    Yalnız kayda değer koşular için yazılır (impact / mechanism / bağlı incident / feedback). Ayrıntı her zaman
    investigation kaydından okunur; episode türetilmiş bir görünümdür ve investigation silinince silinir.
    Belirti parmak izi (symptoms) sorunun kelimelerinden bağımsızdır: "app:latency", "app:errors", "source:app",
    "lock:blocker>waiter" gibi deterministik etiketler.
    """
    __tablename__ = "agent_episodes"
    __table_args__ = (Index("ix_episodes_created", text("created_at DESC")),
                      Index("ix_episodes_symptoms", "symptoms", postgresql_using="gin"),
                      Index("ix_episodes_apps", "apps", postgresql_using="gin"),
                      Index("ix_episodes_mechanism", "mechanism"),
                      Index("ix_episodes_tsv", "tsv", postgresql_using="gin"),
                      CheckConstraint("outcome IN ('unverified', 'confirmed', 'partially_confirmed', 'rejected')",
                                      name="ck_episodes_outcome"),
                      {"schema": SCHEMA})
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"), primary_key=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.incidents.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)   # investigation zamanı
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    apps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
    source_apps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                   server_default=text("'{}'"))
    mechanism: Mapped[str | None] = mapped_column(String(64))
    mechanisms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                  server_default=text("'{}'"))
    symptoms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list,
                                                server_default=text("'{}'"))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    confidence: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified",
                                         server_default="unverified")
    feedback: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                     server_default=text("'{}'::jsonb"))     # {correct, partial, …}
    feedback_note: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))       # 0015: retrieval dışı
    updated_at: Mapped[datetime] = _updated()
    tsv: Mapped[Any] = _tsv("summary", "hypothesis", "resolution", "feedback_note", "mechanism")


class ApiKey(Base):
    """Service account API anahtarı (P2.2). Anahtarın kendisi tutulmaz: yalnız HMAC-SHA256 özeti (sunucu pepper'ı ile).

    Kimliği sahibinden (owner) gelir; etkin yetki = scope'lar ∩ sahibin o anki rol yetkileri. Süre sonu zorunlu.
    Silinmez, iptal edilir (audit izi korunur).
    """
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_owner", "owner_id"), {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
                                                nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(32))


class SecurityState(Base):
    """Güvenlik operasyonları durum anahtarları (P2.3): SIEM cursor'ı, teslim durumu, alarm cooldown'ları."""
    __tablename__ = "security_state"
    __table_args__ = ({"schema": SCHEMA},)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = _created()


class TraceLink(Base):
    """Platform run_id ↔ LangSmith run/trace kimliği (çalıştırma girişimi başına bir satır; trace içeriği tutulmaz)."""
    __tablename__ = "trace_links"
    __table_args__ = (Index("ix_trace_links_platform_run", "platform_run_id", "attempt"),
                      Index("ix_trace_links_investigation", "investigation_id"),
                      Index("ix_trace_links_created", "created_at"),
                      CheckConstraint("kind IN ('analysis', 'chat_turn')", name="ck_trace_links_kind"),
                      CheckConstraint("attempt >= 1", name="ck_trace_links_attempt"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    platform_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    langsmith_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    langsmith_trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger: Mapped[str | None] = mapped_column(String(16))
    mode: Mapped[str | None] = mapped_column(String(32))
    resumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_clock()


# ---------------------------------------------------------------- Agent Trace (0014)
class AgentTraceRun(Base):
    """Bir graph çalıştırmasının yerel trace kaydı (analysis = investigation; chat_turn = sohbet turu girişimi)."""
    __tablename__ = "agent_trace_runs"
    __table_args__ = (Index("ix_agent_trace_runs_started", text("started_at DESC")),
                      Index("ix_agent_trace_runs_investigation", "investigation_id"),
                      Index("ix_agent_trace_runs_conversation", "conversation_id"),
                      CheckConstraint("kind IN ('analysis', 'chat_turn')", name="ck_agent_trace_runs_kind"),
                      CheckConstraint("status IN ('running', 'completed', 'failed', 'interrupted')",
                                      name="ck_agent_trace_runs_status"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    graph: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default=text("'running'"))
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.investigations.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    parent_run_id: Mapped[str | None] = mapped_column(String(160))
    platform_run_id: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    truncated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    capture_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    lost_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AgentTraceEvent(Base):
    """Trace olayı; `id` = event_id, run içi `sequence` kararlı sıradır. payload maskeli ve boyutu sınırlıdır."""
    __tablename__ = "agent_trace_events"
    __table_args__ = (UniqueConstraint("trace_run_id", "sequence", name="uq_agent_trace_events_seq"),
                      Index("ix_agent_trace_events_exec", "trace_run_id", "node_exec_id"),
                      Index("ix_agent_trace_events_call", "trace_run_id", "call_id"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.agent_trace_runs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    node: Mapped[str | None] = mapped_column(String(64))
    node_exec_id: Mapped[str | None] = mapped_column(String(64))
    step: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int | None] = mapped_column(Integer)
    call_id: Mapped[str | None] = mapped_column(String(64))
    related_call_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(16))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                    server_default=text("'{}'::jsonb"))
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


# ---------------------------------------------------------------- Agent Memory (0015)
class MemoryRecord(Base):
    """Long-term memory kaydı (yeni semantic / procedural olgular). Mevcut kaynaklar (episode, known issue, runbook,
    Store notları) burada KOPYALANMAZ; Memory Manager onları adapter ile aynı görünümde sunar.
    `confidence_score` doğruluk garantisi değildir; sorguya göre hesaplanan `similarity` ile karıştırılmaz."""
    __tablename__ = "memory_records"
    __table_args__ = (Index("ix_memory_records_scope", "tenant_id", "agent_id", "memory_type", "status"),
                      Index("ix_memory_records_ns", "tenant_id", "agent_id", "namespace"),
                      Index("ix_memory_records_hash", "tenant_id", "agent_id", "content_hash"),
                      Index("ix_memory_records_updated", text("updated_at DESC")),
                      Index("ix_memory_records_tsv", "tsv", postgresql_using="gin"),
                      CheckConstraint("memory_type IN ('semantic', 'episodic', 'procedural')",
                                      name="ck_memory_records_type"),
                      CheckConstraint("verification_status IN ('unverified', 'verified', 'rejected')",
                                      name="ck_memory_records_verification"),
                      CheckConstraint("approval_status IN ('not_required', 'pending', 'approved', 'rejected')",
                                      name="ck_memory_records_approval"),
                      CheckConstraint("status IN ('active', 'archived', 'expired', 'rejected')",
                                      name="ck_memory_records_status"),
                      CheckConstraint("source_trust IN ('trusted', 'untrusted')", name="ck_memory_records_trust"),
                      CheckConstraint("confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
                                      name="ck_memory_records_confidence"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="sre-agent")
    namespace: Mapped[str] = mapped_column(String(200), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str | None] = mapped_column(String(48))           # topology, ownership, slo, preference…
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                            server_default=text("'{}'::jsonb"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)    # manual | agent_extraction | import
    source_trust: Mapped[str] = mapped_column(String(16), nullable=False, default="trusted")
    source_run_id: Mapped[str | None] = mapped_column(String(160))
    source_thread_id: Mapped[str | None] = mapped_column(String(128))
    source_trace_id: Mapped[str | None] = mapped_column(String(64))
    evidence_references: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list,
                                                           server_default=text("'[]'::jsonb"))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_required")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    verified_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_indexed",
                                                  server_default=text("'not_indexed'"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                                 server_default=text("'{}'::jsonb"))
    tsv: Mapped[Any] = _tsv("title", "content", "category")


class MemoryVersion(Base):
    """Memory kayıtlarının (memory_record / runbook / known_issue / episode) değişmez versiyon geçmişi.
    Procedural düzenlemeler onaya kadar burada `pending` revizyon olarak bekler; canlı kayıt değişmez."""
    __tablename__ = "memory_versions"
    __table_args__ = (UniqueConstraint("record_kind", "record_id", "version", name="uq_memory_versions"),
                      Index("ix_memory_versions_record", "record_kind", "record_id", text("version DESC")),
                      Index("ix_memory_versions_pending", "status", postgresql_where=text("status = 'pending'")),
                      CheckConstraint("status IN ('applied', 'pending', 'rejected', 'superseded')",
                                      name="ck_memory_versions_status"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="sre-agent")
    record_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[str] = mapped_column(String(24), nullable=False)    # create | update | archive | verify …
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MemoryEvent(Base):
    """Memory işlem olayı (MEMORY_READ, MEMORY_WRITE, MEMORY_RETRIEVED, MEMORY_CHECKPOINT_CREATED…).
    payload maskeli ve boyutu sınırlı kopyadır; checkpoint verisi kopyalanmaz (yalnız kimlikler)."""
    __tablename__ = "memory_events"
    __table_args__ = (Index("ix_memory_events_ts", text("ts DESC")),
                      Index("ix_memory_events_type_ts", "event_type", text("ts DESC")),
                      Index("ix_memory_events_run", "run_id"),
                      Index("ix_memory_events_thread", "thread_id"),
                      Index("ix_memory_events_retrieval", "retrieval_id"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="sre-agent")
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(160))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    node: Mapped[str | None] = mapped_column(String(64))
    memory_id: Mapped[str | None] = mapped_column(String(200))
    memory_type: Mapped[str | None] = mapped_column(String(24))
    namespace: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    duration_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    retrieval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                    server_default=text("'{}'::jsonb"))


class MemoryRetrieval(Base):
    """Bir retrieval işleminin pipeline kaydı (Query → Namespace Filter → Metadata Filter → Search → Ranking →
    Validation → Context Injection). Skorlar yalnız sistemin gerçekten ürettikleridir."""
    __tablename__ = "memory_retrievals"
    __table_args__ = (Index("ix_memory_retrievals_ts", text("ts DESC")),
                      Index("ix_memory_retrievals_run", "run_id"),
                      Index("ix_memory_retrievals_thread", "thread_id"),
                      {"schema": SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="sre-agent")
    origin: Mapped[str] = mapped_column(String(16), nullable=False)          # agent | analysis | playground
    run_id: Mapped[str | None] = mapped_column(String(160))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    node: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    namespaces: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    memory_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    top_k: Mapped[int | None] = mapped_column(Integer)
    returned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    excluded: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    stages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    injection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
