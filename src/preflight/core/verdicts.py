import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from preflight.db.models import Failure, Pattern, Rule, RuleStatus

JUDGED = (RuleStatus.ACTIVE, RuleStatus.WORKING, RuleStatus.NEEDS_REVISION)


def verdict(before: int, after: int) -> RuleStatus:
    """Fewer failures in the rule's category after it went live means it is working."""
    if after == 0 or after < before:
        return RuleStatus.WORKING
    return RuleStatus.NEEDS_REVISION


def _count(db: Session, pattern: Pattern, start: datetime, end: datetime) -> int:
    stmt = select(func.count(Failure.id)).where(
        Failure.user_id == pattern.user_id,
        Failure.category == pattern.category,
        Failure.occurred_at > start,
        Failure.occurred_at <= end,
    )
    if pattern.project_scope is not None:
        stmt = stmt.where(Failure.project_id == pattern.project_scope)
    return db.scalar(stmt) or 0


def judge_rules(db: Session, user_id: uuid.UUID, now: datetime, period_days: int) -> list[Rule]:
    """Give a verdict to each rule whose observation period has passed. Rerunning within a period changes nothing."""
    period = timedelta(days=period_days)
    judged = []
    rules = db.scalars(select(Rule).where(Rule.user_id == user_id, Rule.status.in_(JUDGED)))
    for rule in rules:
        due = (rule.verdict_at or rule.created_at) + period
        if due > now:
            continue
        pattern = db.get(Pattern, rule.pattern_id)
        before = _count(db, pattern, rule.created_at - period, rule.created_at)
        after = _count(db, pattern, now - period, now)
        rule.failures_before, rule.failures_after = before, after
        rule.status, rule.verdict_at = verdict(before, after), now
        judged.append(rule)
    db.flush()
    return judged
