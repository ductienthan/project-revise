import uuid
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core import metrics, verdicts
from preflight.db.models import ChecklistItem, Drill, Failure, ItemStatus, Project, Rule, Win


def weekly(db: Session, user_id: uuid.UUID, now: datetime, observation_days: int) -> dict:
    verdicts.judge_rules(db, user_id, now, observation_days)
    start, prev = now - timedelta(days=7), now - timedelta(days=14)
    failures = list(db.scalars(select(Failure).where(Failure.user_id == user_id)))
    this_week = [f for f in failures if start < f.occurred_at <= now]
    last_week = [f for f in failures if prev < f.occurred_at <= start]
    names = dict(db.execute(select(Project.id, Project.name).where(Project.user_id == user_id)).all())
    wins = list(db.scalars(select(Win).where(Win.user_id == user_id, Win.created_at > start)))
    rules = list(db.scalars(select(Rule).where(Rule.user_id == user_id, Rule.verdict_at > start)))
    projects = list(db.scalars(select(Project).where(Project.user_id == user_id)))
    deferred = list(db.scalars(select(ChecklistItem).where(
        ChecklistItem.user_id == user_id, ChecklistItem.status == ItemStatus.DEFERRED,
        ChecklistItem.revisit_date <= (now + timedelta(days=7)).date(),
    )))
    drills_due = len(list(db.scalars(select(Drill.id).where(Drill.user_id == user_id, Drill.due_date <= now.date()))))
    return {
        "from": start.date().isoformat(),
        "to": now.date().isoformat(),
        "failures": len(this_week),
        "failures_last_week": len(last_week),
        "by_category": dict(Counter(f.category.value if f.category else "uncategorized" for f in this_week).most_common()),
        "minutes_lost": sum(f.minutes_lost for f in this_week),
        "repeat_rate": str(metrics.repeat_rate(this_week, failures)),
        "repeat_rate_last_week": str(metrics.repeat_rate(last_week, failures)),
        "guess_accuracy": str(metrics.guess_accuracy(this_week)),
        "wins": [w.text for w in wins],
        "started": [p.name for p in projects if p.started_at and p.started_at >= start],
        "closed": [p.name for p in projects if p.closed_at and p.closed_at >= start],
        "rule_verdicts": [f"{r.checklist_text}: {r.status} ({r.failures_before} before, {r.failures_after} after)" for r in rules],
        "revisit": [f"{names.get(i.project_id, '?')} item {i.position}: {i.text} (due {i.revisit_date})" for i in deferred],
        "drills_due": drills_due,
    }


def weekly_markdown(r: dict) -> str:
    lines = [f"# Weekly report, {r['from']} to {r['to']}", ""]
    lines.append(f"- Failures: {r['failures']} (last week {r['failures_last_week']}), {r['minutes_lost']} minutes lost.")
    lines.append(f"- Repeat rate: {r['repeat_rate']} (last week {r['repeat_rate_last_week']}).")
    lines.append(f"- First guess right: {r['guess_accuracy']}.")
    lines.append(f"- Drills due today: {r['drills_due']}.")
    sections = (
        ("Failures by category", [f"{c}: {n}" for c, n in r["by_category"].items()]),
        ("Wins", r["wins"]),
        ("Projects started", r["started"]),
        ("Projects closed", r["closed"]),
        ("Rule verdicts", r["rule_verdicts"]),
        ("Deferred items to revisit", r["revisit"]),
    )
    for title, entries in sections:
        if entries:
            lines += ["", f"## {title}", ""] + [f"- {e}" for e in entries]
    return "\n".join(lines) + "\n"


