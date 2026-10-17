import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from preflight.core.errors import DomainError, NotFound
from preflight.core.text import keywords, overlap
from preflight.db.models import (
    ChecklistItem,
    Failure,
    FailureCategory,
    Project,
    Severity,
    Win,
    WinTag,
)

RELATED_THRESHOLD = 0.34
SEARCH_FIELDS = (
    Failure.title, Failure.what_happened, Failure.guessed_cause, Failure.root_cause, Failure.fix, Failure.prevention
)


@dataclass(frozen=True)
class FailureInput:
    title: str
    what_happened: str = ""
    guessed_cause: str = ""
    root_cause: str = ""
    fix: str = ""
    prevention: str = ""
    category: FailureCategory | None = None
    severity: Severity = Severity.MEDIUM
    minutes_lost: int = 0
    guess_correct: bool | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class Logged:
    failure: Failure
    created: bool
    related_items: list[ChecklistItem] = field(default_factory=list)
    similar: list[Failure] = field(default_factory=list)


def failure_text(f: Failure) -> str:
    return f"{f.title} {f.what_happened} {f.root_cause} {f.prevention}"


def log_failure(db: Session, project: Project, data: FailureInput, failure_id: uuid.UUID | None, at: datetime) -> Logged:
    """Replaying the same failure_id returns the stored failure, so offline retries never duplicate."""
    existing = db.get(Failure, failure_id) if failure_id else None
    if existing is not None:
        if existing.user_id != project.user_id:
            raise NotFound("failure not found")
        failure, created = existing, False
    else:
        values = {k: v for k, v in data.__dict__.items() if k != "occurred_at"}
        failure = Failure(
            id=failure_id or uuid.uuid4(),
            user_id=project.user_id,
            project_id=project.id,
            occurred_at=data.occurred_at or at,
            root_cause_at=at if data.root_cause.strip() else None,
            **values,
        )
        db.add(failure)
        db.flush()
        created = True
    return Logged(failure, created, related_items(project, failure), similar_failures(db, failure))


def related_items(project: Project, failure: Failure) -> list[ChecklistItem]:
    words = keywords(failure_text(failure))
    scored = [(overlap(words, keywords(item.text)), item) for item in project.checklist_items]
    return [item for score, item in sorted(scored, key=lambda s: -s[0]) if score >= RELATED_THRESHOLD]


def similar_failures(db: Session, failure: Failure, limit: int = 5) -> list[Failure]:
    others = db.scalars(select(Failure).where(Failure.user_id == failure.user_id, Failure.id != failure.id))
    words = keywords(failure_text(failure))
    scored = []
    for other in others:
        score = overlap(words, keywords(failure_text(other)))
        if failure.category is not None and other.category == failure.category:
            score += 0.5
        if score >= RELATED_THRESHOLD:
            scored.append((score, other))
    return [f for _, f in sorted(scored, key=lambda s: -s[0])[:limit]]


def link_failure(db: Session, failure: Failure, item_id: uuid.UUID | None) -> None:
    if item_id is None:
        failure.checklist_item_id, failure.on_checklist = None, False
        return
    item = db.get(ChecklistItem, item_id)
    if item is None or item.project_id != failure.project_id:
        raise DomainError("That checklist item is not in this failure's project.")
    failure.checklist_item_id, failure.on_checklist = item.id, True


def update_failure(failure: Failure, changes: dict, at: datetime) -> None:
    for key, value in changes.items():
        setattr(failure, key, value)
    if failure.root_cause.strip() and failure.root_cause_at is None:
        failure.root_cause_at = at


def get_failure(db: Session, user_id: uuid.UUID, failure_id: uuid.UUID) -> Failure:
    failure = db.get(Failure, failure_id)
    if failure is None or failure.user_id != user_id:
        raise NotFound("failure not found")
    return failure


def search_failures(
    db: Session, user_id: uuid.UUID, query: str | None = None, project_id: uuid.UUID | None = None
) -> list[Failure]:
    stmt = select(Failure).where(Failure.user_id == user_id)
    if project_id is not None:
        stmt = stmt.where(Failure.project_id == project_id)
    if query:
        if db.get_bind().dialect.name == "postgresql":
            document = func.to_tsvector("english", func.concat_ws(" ", *SEARCH_FIELDS))
            stmt = stmt.where(document.op("@@")(func.plainto_tsquery("english", query)))
        else:
            for word in query.split():
                stmt = stmt.where(or_(*(col.ilike(f"%{word}%") for col in SEARCH_FIELDS)))
    return list(db.scalars(stmt.order_by(Failure.occurred_at.desc())))


def log_win(db: Session, project: Project, text: str, tag: WinTag | None, win_id: uuid.UUID | None) -> Win:
    existing = db.get(Win, win_id) if win_id else None
    if existing is not None and existing.user_id == project.user_id:
        return existing
    win = Win(id=win_id or uuid.uuid4(), user_id=project.user_id, project_id=project.id, text=text, tag=tag)
    db.add(win)
    db.flush()
    return win
