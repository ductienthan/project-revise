import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from preflight.core import review
from preflight.core.checklist import build_checklist
from preflight.core.errors import DomainError
from preflight.db.models import (
    ChecklistItem,
    GateOverride,
    ItemOutcome,
    ItemStatus,
    Project,
    ProjectStatus,
    Retrospective,
)

TRANSITIONS = {ProjectStatus.DRAFT: {ProjectStatus.STARTED}, ProjectStatus.STARTED: {ProjectStatus.CLOSED}}


def transition(project: Project, target: ProjectStatus, at: datetime) -> None:
    if target not in TRANSITIONS.get(project.status, set()):
        raise DomainError(f"A {project.status} project cannot become {target}.")
    project.status = target
    if target == ProjectStatus.STARTED:
        project.started_at = at
    elif target == ProjectStatus.CLOSED:
        project.closed_at = at


def is_resolved(item: ChecklistItem) -> bool:
    match item.status:
        case ItemStatus.DONE:
            return True
        case ItemStatus.NOT_APPLICABLE:
            return bool(item.reason.strip())
        case ItemStatus.DEFERRED:
            return bool(item.reason.strip()) and item.revisit_date is not None
    return False


def unresolved_required(project: Project) -> list[ChecklistItem]:
    return [i for i in project.checklist_items if i.required and not is_resolved(i)]


@dataclass(frozen=True)
class StartResult:
    started: bool
    unresolved: list[ChecklistItem]


def start(db: Session, project: Project, at: datetime, override_reason: str | None = None) -> StartResult:
    """Build or refresh the checklist, then pass the gate. Calling it again after resolving items is safe."""
    if project.status != ProjectStatus.DRAFT:
        raise DomainError(f"'{project.name}' is already {project.status}.")
    build_checklist(db, project, at, review.candidates(db, project))
    unresolved = unresolved_required(project)
    if unresolved and override_reason is not None:
        if not override_reason.strip():
            raise DomainError("Overriding the gate needs a typed reason.")
        db.add(GateOverride(user_id=project.user_id, project_id=project.id, reason=override_reason.strip()))
        unresolved = []
    if unresolved:
        return StartResult(False, unresolved)
    transition(project, ProjectStatus.STARTED, at)
    return StartResult(True, [])


def set_item_status(
    db: Session, item: ChecklistItem, project: Project, status: ItemStatus, reason: str, revisit: date | None, at: datetime
) -> bool:
    """Update one item. Returns True when this resolved the last required item and started the project."""
    if status == ItemStatus.NOT_APPLICABLE and not reason.strip():
        raise DomainError("Not applicable needs a reason.")
    if status == ItemStatus.DEFERRED and (not reason.strip() or revisit is None):
        raise DomainError("Deferring needs a reason and a revisit date.")
    if status == ItemStatus.DEFERRED and revisit <= at.date():
        raise DomainError("The revisit date must be in the future.")
    item.status, item.reason = status, reason.strip()
    item.revisit_date = revisit if status == ItemStatus.DEFERRED else None
    item.resolved_at = at if is_resolved(item) else None
    if project.status != ProjectStatus.DRAFT or project.checklist_built_at is None or unresolved_required(project):
        return False
    build_checklist(db, project, at, review.candidates(db, project))
    if unresolved_required(project):
        return False
    transition(project, ProjectStatus.STARTED, at)
    return True


def close(
    db: Session, project: Project, at: datetime, summary: str, outcomes: dict[uuid.UUID, ItemOutcome], summary_required: bool
) -> Retrospective:
    """Record the retrospective and item outcomes, then close. Closed data feeds patterns, drills and the dashboard."""
    if project.status != ProjectStatus.STARTED:
        raise DomainError(f"Only a started project can be closed. '{project.name}' is {project.status}.")
    if summary_required and not summary.strip():
        raise DomainError("Closing needs a retrospective summary.")
    items = {i.id: i for i in project.checklist_items}
    for item_id, outcome in outcomes.items():
        if item_id not in items:
            raise DomainError("That checklist item is not in this project.")
        items[item_id].outcome = outcome
    retrospective = Retrospective(user_id=project.user_id, project_id=project.id, summary=summary.strip())
    project.retrospectives.append(retrospective)
    transition(project, ProjectStatus.CLOSED, at)
    db.flush()
    return retrospective
