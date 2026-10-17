import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.ai import gate
from preflight.ai.client import AIClient
from preflight.ai.gate import GateItem
from preflight.ai.prompts.small import GRADE
from preflight.config import Settings
from preflight.core.errors import DomainError, NotFound
from preflight.db.models import Drill, DrillReview, Failure, Project, ProjectStatus

LADDER = (1, 3, 7, 21)
PASSING_SCORE = 3
MIN_EASE = 1.3


def next_ease(ease: float, score: int) -> float:
    """SM-2's ease update: good answers widen future gaps, poor ones narrow them."""
    miss = 5 - score
    return max(MIN_EASE, round(ease + 0.1 - miss * (0.08 + miss * 0.02), 2))


def next_interval(interval: int, ease: float, score: int) -> int:
    """Climb the 1, 3, 7, 21 day ladder on a pass, then grow by ease. Any poor answer starts over at 1 day."""
    if score < PASSING_SCORE:
        return LADDER[0]
    for step in LADDER:
        if step > interval:
            return step
    return round(interval * ease)


def drill_for(failure: Failure, project_name: str) -> Drill | None:
    if not (failure.root_cause.strip() or failure.prevention.strip()):
        return None
    context = f" {failure.what_happened.strip()}" if failure.what_happened.strip() else ""
    prompt = f'In {project_name}: "{failure.title}".{context} What was the root cause, and what check would have caught it?'
    answer = ". ".join(p for p in (f"Root cause: {failure.root_cause.strip()}" if failure.root_cause.strip() else "",
                                   f"Check: {failure.prevention.strip()}" if failure.prevention.strip() else "") if p)
    return Drill(user_id=failure.user_id, failure_id=failure.id, prompt=prompt, answer=answer, interval_days=0,
                 due_date=failure.occurred_at.date() + timedelta(days=LADDER[0]))


def create_missing(db: Session, user_id: uuid.UUID, today: date) -> int:
    """One drill per closed-project failure that has a cause or a check. Safe to rerun."""
    have = set(db.scalars(select(Drill.failure_id).where(Drill.user_id == user_id)))
    rows = db.execute(
        select(Failure, Project.name).join(Project, Failure.project_id == Project.id)
        .where(Failure.user_id == user_id, Project.status == ProjectStatus.CLOSED)
    ).all()
    created = 0
    for failure, name in rows:
        if failure.id in have:
            continue
        drill = drill_for(failure, name)
        if drill is not None:
            drill.due_date = max(drill.due_date, today)
            db.add(drill)
            created += 1
    db.flush()
    return created


def due(db: Session, user_id: uuid.UUID, today: date) -> list[Drill]:
    create_missing(db, user_id, today)
    return list(db.scalars(select(Drill).where(Drill.user_id == user_id, Drill.due_date <= today).order_by(Drill.due_date)))


def get_drill(db: Session, user_id: uuid.UUID, drill_id: uuid.UUID) -> Drill:
    drill = db.get(Drill, drill_id)
    if drill is None or drill.user_id != user_id:
        raise NotFound("drill not found")
    return drill


def record(db: Session, drill: Drill, score: int, at: datetime) -> DrillReview:
    if not 0 <= score <= 5:
        raise DomainError("Scores run from 0 to 5.")
    drill.ease = next_ease(drill.ease, score)
    drill.interval_days = next_interval(drill.interval_days, drill.ease, score)
    drill.due_date = at.date() + timedelta(days=drill.interval_days)
    review = DrillReview(user_id=drill.user_id, drill_id=drill.id, score=score, reviewed_at=at)
    db.add(review)
    db.flush()
    return review


def grade(db: Session, ai: AIClient | None, settings: Settings, drill: Drill, attempt: str, at: datetime) -> tuple[int, str]:
    if not attempt.strip():
        raise DomainError("Type an answer to have it graded.")
    item = GateItem(f"drill:{drill.id}", "drill", "This drill and your answer",
                    f"Question: {drill.prompt}\nExpected answer: {drill.answer}\nPractice answer: {attempt}")
    output, _ = gate.run(db, ai, settings, drill.user_id, GRADE, [item], {item.id}, at)
    return output.score, output.feedback
