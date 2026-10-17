import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from preflight.db.models import (
    FailureCategory,
    ItemOutcome,
    ItemStatus,
    Priority,
    ProjectStatus,
    RuleStatus,
    Severity,
    SourceType,
    WinTag,
)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    name: str
    idea: str = ""
    goals: str = ""
    requirements: str = ""


class ProjectOut(Out):
    id: uuid.UUID
    name: str
    idea: str
    goals: str
    status: ProjectStatus
    checklist_requirements_version: int | None
    created_at: datetime
    started_at: datetime | None
    closed_at: datetime | None


class RequirementsIn(BaseModel):
    text: str


class RequirementsOut(Out):
    version: int
    text: str
    created_at: datetime


class ChecklistItemOut(Out):
    id: uuid.UUID
    position: int
    text: str
    why: str
    source_type: SourceType
    source_ref: str
    priority: Priority
    required: bool
    status: ItemStatus
    reason: str
    revisit_date: date | None
    outcome: ItemOutcome | None


class ChecklistOut(BaseModel):
    project: ProjectOut
    items: list[ChecklistItemOut]
    unresolved_required: list[int]


class ChecklistItemPatch(BaseModel):
    status: ItemStatus
    reason: str = ""
    revisit_date: date | None = None


class ItemPatched(BaseModel):
    item: ChecklistItemOut
    project_started: bool


class StartIn(BaseModel):
    override_reason: str | None = None


class StartOut(BaseModel):
    started: bool
    checklist: ChecklistOut


class FailureIn(BaseModel):
    id: uuid.UUID | None = Field(None, description="Client-generated id so offline replays are idempotent.")
    title: str = Field(min_length=1)
    what_happened: str = ""
    guessed_cause: str = ""
    root_cause: str = ""
    fix: str = ""
    prevention: str = ""
    category: FailureCategory | None = None
    severity: Severity = Severity.MEDIUM
    minutes_lost: int = Field(0, ge=0)
    guess_correct: bool | None = None
    occurred_at: datetime | None = None


class FailureOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    what_happened: str
    guessed_cause: str
    root_cause: str
    fix: str
    prevention: str
    category: FailureCategory | None
    severity: Severity
    minutes_lost: int
    guess_correct: bool | None
    checklist_item_id: uuid.UUID | None
    on_checklist: bool | None
    occurred_at: datetime


class FailureLogged(BaseModel):
    failure: FailureOut
    created: bool
    project_status: ProjectStatus
    related_items: list[ChecklistItemOut]
    similar: list[FailureOut]


class FailurePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    what_happened: str | None = None
    guessed_cause: str | None = None
    root_cause: str | None = None
    fix: str | None = None
    prevention: str | None = None
    category: FailureCategory | None = None
    severity: Severity | None = None
    minutes_lost: int | None = Field(None, ge=0)
    guess_correct: bool | None = None
    link_item_id: uuid.UUID | None = None
    not_on_checklist: bool = False


class WinIn(BaseModel):
    id: uuid.UUID | None = None
    text: str = Field(min_length=1)
    tag: WinTag | None = None


class WinOut(Out):
    id: uuid.UUID
    text: str
    tag: WinTag | None
    created_at: datetime


class PatternOut(Out):
    id: uuid.UUID
    project_scope: uuid.UUID | None
    category: FailureCategory
    label: str
    failure_count: int


class PatternSuggestion(BaseModel):
    pattern: PatternOut
    suggested_rule: str | None
    rule_id: uuid.UUID | None


class RuleIn(BaseModel):
    pattern_id: uuid.UUID
    checklist_text: str


class RulePatch(BaseModel):
    checklist_text: str | None = None
    status: RuleStatus | None = None


class RuleOut(Out):
    id: uuid.UUID
    pattern_id: uuid.UUID
    checklist_text: str
    status: RuleStatus
    created_at: datetime
    verdict_at: datetime | None
    failures_before: int | None
    failures_after: int | None


class StatusOut(BaseModel):
    project: ProjectOut
    requirements_version: int | None
    checklist_total: int
    checklist_resolved: int
    unresolved_required: int
    failures: int
    wins: int
    minutes_lost: int
    gate_overrides: list[str]
