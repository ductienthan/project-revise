import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from preflight.db.base import Base, Owned, UTCDateTime, str_enum, utcnow


class ProjectStatus(enum.StrEnum):
    DRAFT = "draft"
    STARTED = "started"
    CLOSED = "closed"


class LinkType(enum.StrEnum):
    INTERNAL = "internal"
    PUBLIC = "public"


class FailureCategory(enum.StrEnum):
    MISSED_EDGE_CASE = "missed edge case"
    WRONG_ASSUMPTION = "wrong assumption"
    MISREAD_REQUIREMENT = "misread requirement"
    KNOWLEDGE_GAP = "knowledge gap"
    NO_VERIFICATION = "no verification"
    WRONG_APPROACH = "wrong approach"
    STATE_OR_CONCURRENCY = "state or concurrency bug"


class Severity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(enum.StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(enum.StrEnum):
    FAILURE = "failure"
    RULE = "rule"
    GAP = "gap"
    QUESTION = "question"


class ItemStatus(enum.StrEnum):
    NOT_CHECKED = "not checked"
    DONE = "done"
    NOT_APPLICABLE = "not applicable"
    DEFERRED = "deferred"


class ItemOutcome(enum.StrEnum):
    HELPED = "helped"
    MISSED = "missed"
    IRRELEVANT = "irrelevant"


class RuleStatus(enum.StrEnum):
    ACTIVE = "active"
    WORKING = "working"
    NEEDS_REVISION = "needs revision"
    RETIRED = "retired"


class QuestionStatus(enum.StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    NOT_RELEVANT = "not relevant"
    DEFERRED = "deferred"


class WinTag(enum.StrEnum):
    USED_RULE = "used a rule"
    CAUGHT_RISK_EARLY = "caught a risk early"
    OTHER = "other"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WebSession(Owned, Base):
    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DeviceCode(Owned, Base):
    """A pending CLI sign-in. user_id is set only once the dashboard user approves it."""

    __tablename__ = "device_codes"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True)
    device_name: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(Owned, Base):
    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(100))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEvent(Owned, Base):
    __tablename__ = "audit_events"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


class Project(Owned, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    name: Mapped[str] = mapped_column(String(100))
    idea: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectStatus] = mapped_column(str_enum(ProjectStatus), default=ProjectStatus.DRAFT)
    checklist_requirements_version: Mapped[int | None] = mapped_column(Integer)
    checklist_built_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    requirement_versions: Mapped[list["RequirementVersion"]] = relationship(
        cascade="all, delete-orphan", order_by="RequirementVersion.version"
    )
    links: Mapped[list["Link"]] = relationship(cascade="all, delete-orphan")
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        cascade="all, delete-orphan", order_by="ChecklistItem.position"
    )
    failures: Mapped[list["Failure"]] = relationship(cascade="all, delete-orphan")
    wins: Mapped[list["Win"]] = relationship(cascade="all, delete-orphan")
    reviews: Mapped[list["Review"]] = relationship(cascade="all, delete-orphan")
    predictions: Mapped[list["Prediction"]] = relationship(cascade="all, delete-orphan")
    retrospectives: Mapped[list["Retrospective"]] = relationship(cascade="all, delete-orphan")
    gate_overrides: Mapped[list["GateOverride"]] = relationship(cascade="all, delete-orphan")


class RequirementVersion(Owned, Base):
    __tablename__ = "requirement_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)


class Link(Owned, Base):
    __tablename__ = "links"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[LinkType] = mapped_column(str_enum(LinkType))
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)

    snapshots: Mapped[list["Snapshot"]] = relationship(cascade="all, delete-orphan", order_by="Snapshot.version")


class Snapshot(Owned, Base):
    __tablename__ = "snapshots"

    link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("links.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AiCall(Owned, Base):
    """The exact payload sent to the model and the reply, kept for audit."""

    __tablename__ = "ai_calls"

    kind: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    approved_items: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")


class Review(Owned, Base):
    __tablename__ = "reviews"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    requirements_version: Mapped[int | None] = mapped_column(Integer)
    approved_items: Mapped[list] = mapped_column(JSON, default=list)
    ai_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_calls.id"))
    drafts: Mapped[list] = mapped_column(JSON, default=list)
    accepted_drafts: Mapped[list] = mapped_column(JSON, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    questions: Mapped[list["ReviewQuestion"]] = relationship(
        cascade="all, delete-orphan", order_by="ReviewQuestion.position"
    )


class ReviewQuestion(Owned, Base):
    __tablename__ = "review_questions"

    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviews.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[QuestionStatus] = mapped_column(str_enum(QuestionStatus), default=QuestionStatus.OPEN)


class ChecklistItem(Owned, Base):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("project_id", "source_type", "source_ref"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType))
    source_ref: Mapped[str] = mapped_column(String(64))
    priority: Mapped[Priority] = mapped_column(str_enum(Priority))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ItemStatus] = mapped_column(str_enum(ItemStatus), default=ItemStatus.NOT_CHECKED)
    reason: Mapped[str] = mapped_column(Text, default="")
    revisit_date: Mapped[date | None] = mapped_column(Date)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[ItemOutcome | None] = mapped_column(str_enum(ItemOutcome))


class Failure(Owned, Base):
    __tablename__ = "failures"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    what_happened: Mapped[str] = mapped_column(Text, default="")
    guessed_cause: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    root_cause_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fix: Mapped[str] = mapped_column(Text, default="")
    prevention: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[FailureCategory | None] = mapped_column(str_enum(FailureCategory))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity), default=Severity.MEDIUM)
    minutes_lost: Mapped[int] = mapped_column(Integer, default=0)
    guess_correct: Mapped[bool | None] = mapped_column(Boolean)
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checklist_items.id", ondelete="SET NULL"))
    on_checklist: Mapped[bool | None] = mapped_column(Boolean)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Pattern(Owned, Base):
    __tablename__ = "patterns"
    __table_args__ = (UniqueConstraint("user_id", "project_scope", "category"),)

    project_scope: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    category: Mapped[FailureCategory] = mapped_column(str_enum(FailureCategory))
    label: Mapped[str] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)


class Rule(Owned, Base):
    __tablename__ = "rules"

    pattern_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patterns.id"), index=True)
    checklist_text: Mapped[str] = mapped_column(Text)
    status: Mapped[RuleStatus] = mapped_column(str_enum(RuleStatus), default=RuleStatus.ACTIVE)
    verdict_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failures_before: Mapped[int | None] = mapped_column(Integer)
    failures_after: Mapped[int | None] = mapped_column(Integer)


class Drill(Owned, Base):
    __tablename__ = "drills"

    failure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("failures.id", ondelete="CASCADE"), unique=True)
    prompt: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)
    due_date: Mapped[date] = mapped_column(Date)


class DrillReview(Owned, Base):
    __tablename__ = "drill_reviews"

    drill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drills.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Prediction(Owned, Base):
    __tablename__ = "predictions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    evidence_planned: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    failure_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("failures.id", ondelete="SET NULL"))


class Win(Owned, Base):
    __tablename__ = "wins"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    tag: Mapped[WinTag | None] = mapped_column(str_enum(WinTag))


class Retrospective(Owned, Base):
    __tablename__ = "retrospectives"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)


class GateOverride(Owned, Base):
    __tablename__ = "gate_overrides"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
