import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core.errors import DomainError, NotFound
from preflight.db.models import Failure, FailureCategory, Pattern, Project, Rule, RuleStatus

REPEAT_THRESHOLD = 2


def detect_patterns(db: Session, user_id: uuid.UUID) -> list[Pattern]:
    """Recount repeated categories, within each project and across all projects. A category that no longer repeats
    is dropped, or kept recounted and unsuggested when a rule was approved from it. Safe to rerun."""
    rows = db.execute(
        select(Failure.project_id, Failure.category).where(Failure.user_id == user_id, Failure.category.is_not(None))
    ).all()
    counts: Counter[tuple[uuid.UUID | None, FailureCategory]] = Counter()
    for project_id, category in rows:
        counts[(project_id, category)] += 1
        counts[(None, category)] += 1

    names = dict(db.execute(select(Project.id, Project.name).where(Project.user_id == user_id)).all())
    existing = {(p.project_scope, p.category): p for p in db.scalars(select(Pattern).where(Pattern.user_id == user_id))}
    ruled = set(db.scalars(select(Rule.pattern_id).where(Rule.user_id == user_id)))
    repeated = []
    for scope, category in set(counts) | set(existing):
        count, pattern = counts[(scope, category)], existing.get((scope, category))
        where = f"in {names[scope]}" if scope else "across projects"
        label = f"{category} ({count} failure{'' if count == 1 else 's'} {where})"
        if count < REPEAT_THRESHOLD:
            if pattern is None:
                continue
            if pattern.id in ruled:
                pattern.label, pattern.failure_count = label, count
            else:
                db.delete(pattern)
            continue
        if pattern is None:
            pattern = Pattern(user_id=user_id, project_scope=scope, category=category, label=label, failure_count=count)
            db.add(pattern)
        else:
            pattern.label, pattern.failure_count = label, count
        repeated.append(pattern)
    db.flush()
    return sorted(repeated, key=lambda p: (-p.failure_count, p.category))


def pattern_labels(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return dict(db.execute(select(Pattern.id, Pattern.label).where(Pattern.user_id == user_id)).all())


def pattern_failures(db: Session, pattern: Pattern) -> list[Failure]:
    stmt = select(Failure).where(Failure.user_id == pattern.user_id, Failure.category == pattern.category)
    if pattern.project_scope is not None:
        stmt = stmt.where(Failure.project_id == pattern.project_scope)
    return list(db.scalars(stmt.order_by(Failure.occurred_at)))


def draft_rule_text(failures: list[Failure]) -> str:
    notes = [f.prevention.strip().rstrip(".") for f in failures if f.prevention.strip()]
    unique = list(dict.fromkeys(notes))
    if not unique:
        return f"Before starting, check for {failures[0].category} risks seen in: " + "; ".join(f.title for f in failures[:3])
    return "; ".join(unique[:3])


def get_pattern(db: Session, user_id: uuid.UUID, pattern_id: uuid.UUID) -> Pattern:
    pattern = db.get(Pattern, pattern_id)
    if pattern is None or pattern.user_id != user_id:
        raise NotFound("pattern not found")
    return pattern


def live_rule(db: Session, pattern: Pattern) -> Rule | None:
    return db.scalar(select(Rule).where(Rule.pattern_id == pattern.id, Rule.status != RuleStatus.RETIRED))


def approve_rule(db: Session, pattern: Pattern, checklist_text: str) -> Rule:
    if not checklist_text.strip():
        raise DomainError("A rule needs checklist text.")
    if live_rule(db, pattern) is not None:
        raise DomainError("This pattern already has a live rule. Edit that rule instead.")
    rule = Rule(user_id=pattern.user_id, pattern_id=pattern.id, checklist_text=checklist_text.strip())
    db.add(rule)
    db.flush()
    return rule


def get_rule(db: Session, user_id: uuid.UUID, rule_id: uuid.UUID) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.user_id != user_id:
        raise NotFound("rule not found")
    return rule


def list_rules(db: Session, user_id: uuid.UUID) -> list[Rule]:
    return list(db.scalars(select(Rule).where(Rule.user_id == user_id).order_by(Rule.created_at)))
