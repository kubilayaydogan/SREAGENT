"""API sözleşmesi (Pydantic v2). Giriş modelleri katıdır (bilinmeyen alan reddedilir); çıkış modelleri ORM'den okunur."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..db.models import (ActionType, ApprovalStatus, Confidence, EvidenceType, IncidentSource, IncidentStatus,
                         InvestigationStatus, MessageRole, RiskLevel, Role, Severity, ToolStatus, Trigger, Verdict)

T = TypeVar("T")


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


def page_of(p, model) -> dict:
    return {"items": [model.model_validate(x) for x in p.items], "total": p.total, "page": p.page, "size": p.size,
            "pages": p.pages}


class ErrorOut(BaseModel):
    error: str
    message: str
    correlation_id: str
    details: Any = None


# ---------------------------------------------------------------- auth / kullanıcı
class LoginIn(In):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(Out):
    id: uuid.UUID
    username: str
    display_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


class UserCreateIn(In):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=128)
    role: Role = Role.VIEWER
    password: str = Field(min_length=10, max_length=256)


class UserUpdateIn(In):
    display_name: str | None = Field(None, max_length=128)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=10, max_length=256)


# ---------------------------------------------------------------- uygulamalar / dashboard
class ApplicationOut(Out):
    id: str
    name: str
    description: str | None
    kind: str
    host: str | None
    db_role: str | None


RangeKey = Literal["15m", "1h", "6h", "24h"]


# ---------------------------------------------------------------- konuşma
class ConversationIn(In):
    title: str = Field("Yeni konuşma", max_length=200)
    application_id: str | None = None


class ConversationPatch(In):
    title: str | None = Field(None, min_length=1, max_length=200)
    archived: bool | None = None


class ConversationOut(Out):
    id: uuid.UUID
    title: str
    thread_id: str
    application_id: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class MessageOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    meta: dict[str, Any] = Field(serialization_alias="metadata")
    investigation_id: uuid.UUID | None
    created_at: datetime


# ---------------------------------------------------------------- ajan
class TimeRangeIn(In):
    minutes: int | None = Field(None, ge=1, le=1440)
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("start ve end birlikte verilmeli")
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end, start'tan sonra olmalı")
        if self.start and self.minutes:
            raise ValueError("minutes ile start/end birlikte verilemez")
        for v in (self.start, self.end):
            if v is not None and v.tzinfo is None:
                raise ValueError("zaman damgası saat dilimi içermeli (ör. ...Z)")
        return self


class ChatIn(In):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    application_id: str | None = None
    time_range: TimeRangeIn | None = None


class InvestigateIn(In):
    title: str | None = Field(None, max_length=300)
    application_id: str | None = None
    time_range: TimeRangeIn = Field(default_factory=lambda: TimeRangeIn(minutes=15))
    baseline: TimeRangeIn | None = None
    incident_id: uuid.UUID | None = None
    use_llm: bool = True


class RunOut(BaseModel):
    run_id: str
    kind: str
    stream_url: str
    conversation_id: uuid.UUID | None = None
    investigation_id: uuid.UUID | None = None


# ---------------------------------------------------------------- araştırma
class InvestigationOut(Out):
    id: uuid.UUID
    run_id: str
    trigger: Trigger
    title: str
    description: str | None
    status: InvestigationStatus
    severity: Severity
    risk_score: Decimal | None
    data_coverage: Decimal | None
    confidence: Confidence | None
    primary_mechanism: str | None
    source_apps: list[str]
    affected_apps: list[str]
    application_id: str | None
    conversation_id: uuid.UUID | None
    user_id: uuid.UUID | None
    window_start: datetime
    window_end: datetime
    baseline_start: datetime | None
    baseline_end: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    root_cause_hypothesis: str | None
    error: str | None
    created_at: datetime


class InvestigationDetailOut(InvestigationOut):
    explanation: str | None
    explanation_source: str | None
    report: dict[str, Any] | None
    config_snapshot: dict[str, Any] | None = None      # koşu başında dondurulan Settings (secret içermez)


class EvidenceOut(Out):
    id: uuid.UUID
    evidence_key: str
    evidence_type: EvidenceType
    source: str
    signal: str | None
    data_status: str | None
    application_id: str | None
    entity_id: str | None
    trace_id: str | None
    span_id: str | None
    metric_name: str | None
    query_reference: str | None
    dynatrace_query_id: str | None
    timestamp: datetime | None
    content: str | None
    meta: dict[str, Any] = Field(serialization_alias="metadata")


class ToolOut(Out):
    id: uuid.UUID
    step: str | None
    tool_name: str
    signal: str | None
    tool_arguments: dict[str, Any]
    execution_status: ToolStatus
    result_status: str | None
    record_count: int | None
    dynatrace_query_id: str | None
    evidence_key: str | None
    started_at: datetime | None
    duration_ms: int | None
    error_message: str | None
    created_at: datetime


# ---------------------------------------------------------------- incident
class IncidentIn(In):
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(None, max_length=10000)
    severity: Severity = Severity.UNKNOWN
    application_id: str | None = None
    investigation_id: uuid.UUID | None = None


class IncidentPatch(In):
    status: IncidentStatus | None = None
    note: str | None = Field(None, max_length=4000)
    title: str | None = Field(None, min_length=3, max_length=300)
    description: str | None = Field(None, max_length=10000)
    severity: Severity | None = None
    root_cause: str | None = Field(None, max_length=10000)
    recommended_actions: list[str] | None = Field(None, max_length=30)
    application_id: str | None = None


class IncidentOut(Out):
    id: uuid.UUID
    investigation_id: uuid.UUID | None
    application_id: str | None
    title: str
    description: str | None
    severity: Severity
    status: IncidentStatus
    source: IncidentSource
    conditions: list[str]
    detected_at: datetime
    acknowledged_at: datetime | None
    mitigated_at: datetime | None
    resolved_at: datetime | None
    root_cause: str | None
    recommended_actions: list[str]
    created_at: datetime
    updated_at: datetime


class IncidentEventOut(Out):
    id: uuid.UUID
    at: datetime
    kind: str
    from_status: str | None
    to_status: str | None
    user_id: uuid.UUID | None
    message: str | None


class LinkIn(In):
    investigation_id: uuid.UUID
    relation: Literal["trigger", "related", "recovery"] = "related"


class NoteIn(In):
    message: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- RCA / geri bildirim
class RcaOut(Out):
    id: uuid.UUID
    investigation_id: uuid.UUID
    incident_id: uuid.UUID | None
    application_id: str | None
    title: str
    summary: str
    confidence: Confidence | None
    generated_by: str
    generated_at: datetime


class RcaDetailOut(RcaOut):
    problem_description: str | None
    timeline: list[Any]
    impact_analysis: str | None
    suspected_root_cause: str | None
    supporting_evidence: list[Any]
    contributing_factors: list[str]
    recommended_actions: list[str]
    alternatives: list[str]
    data_gaps: list[str]
    uncertainty: str | None
    markdown: str


class RcaGenerateIn(In):
    incident_id: uuid.UUID | None = None
    use_llm: bool = True


class FeedbackIn(In):
    investigation_id: uuid.UUID
    verdict: Verdict
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=4000)
    rca_id: uuid.UUID | None = None


class FeedbackOut(Out):
    id: uuid.UUID
    investigation_id: uuid.UUID
    rca_id: uuid.UUID | None
    user_id: uuid.UUID
    verdict: Verdict
    is_correct: bool
    rating: int | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- bilgi bankası
class KnownIssueIn(In):
    title: str = Field(min_length=3, max_length=300)
    application_id: str | None = None
    mechanism: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=10000)
    symptoms: str | None = Field(None, max_length=10000)
    root_cause: str | None = Field(None, max_length=10000)
    resolution: str | None = Field(None, max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    verified: bool = False


class KnownIssueOut(Out):
    id: uuid.UUID
    application_id: str | None
    mechanism: str | None
    title: str
    description: str | None
    symptoms: str | None
    root_cause: str | None
    resolution: str | None
    tags: list[str]
    verified: bool
    origin: str
    created_at: datetime
    updated_at: datetime


class RunbookIn(In):
    title: str = Field(min_length=3, max_length=300)
    application_id: str | None = None
    mechanism: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=10000)
    procedure: str = Field(min_length=10, max_length=50000)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    tags: list[str] = Field(default_factory=list, max_length=20)


class RunbookOut(Out):
    id: uuid.UUID
    application_id: str | None
    mechanism: str | None
    title: str
    description: str | None
    procedure: str
    risk_level: RiskLevel
    requires_approval: bool
    tags: list[str]
    origin: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- onay
class ApprovalIn(In):
    action_type: ActionType
    target: str = Field(min_length=1, max_length=200)
    justification: str = Field(min_length=10, max_length=4000)
    investigation_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    runbook_id: uuid.UUID | None = None


class DecisionIn(In):
    approve: bool
    comment: str | None = Field(None, max_length=4000)


class ApprovalOut(Out):
    id: uuid.UUID
    action_type: ActionType
    target: str
    justification: str
    status: ApprovalStatus
    investigation_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    runbook_id: uuid.UUID | None
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_comment: str | None
    expires_at: datetime | None
    created_at: datetime


class JobOut(Out):
    id: uuid.UUID
    name: str
    job_type: str
    interval_seconds: int
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    locked_by: str | None
    locked_until: datetime | None


class JobRunOut(Out):
    id: uuid.UUID
    job_id: uuid.UUID
    worker_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any]
    error: str | None

    @field_validator("status", mode="before")
    @classmethod
    def _st(cls, v):
        return getattr(v, "value", v)
