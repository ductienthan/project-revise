import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core import metrics, verdicts
from preflight.core.metrics import Ratio
from preflight.core.review import latest_review
from preflight.db.models import DrillReview, Failure, Pattern, Prediction, Project, Rule, Win


def ratio(r: Ratio) -> dict:
    return {"num": r.num, "den": r.den, "value": r.value, "text": str(r)}


def project_rows(db: Session, projects: list[Project], all_failures: list[Failure]) -> list[dict]:
    repeated = metrics.repeats(all_failures)
    rows = []
    for p in projects:
        review = latest_review(db, p, completed=True)
        rows.append({
            "name": p.name,
            "status": p.status.value,
            "failures": len(p.failures),
            "repeat_rate": ratio(Ratio(sum(f.id in repeated for f in p.failures), len(p.failures))),
            "minutes_lost": sum(f.minutes_lost for f in p.failures),
            "wins": len(p.wins),
            "wins_by_tag": metrics.wins_by_tag(p.wins),
            "checklist_coverage": ratio(metrics.checklist_coverage(p.checklist_items, p.started_at)),
            "on_checklist_rate": ratio(metrics.on_checklist_rate(p.failures)),
            "missed_item_rate": ratio(metrics.missed_item_rate(p.checklist_items, p.failures)),
            "not_on_checklist": metrics.not_on_checklist(p.failures),
            "question_coverage": ratio(metrics.question_coverage([q.status for q in review.questions] if review else [])),
            "outcomes": metrics.outcomes(p.checklist_items),
            "gate_overridden": bool(p.gate_overrides),
        })
    return rows


def accuracy_by_month(failures: list[Failure]) -> list[dict]:
    months: dict[str, list[Failure]] = {}
    for f in sorted(failures, key=lambda f: f.occurred_at):
        months.setdefault(f.occurred_at.strftime("%Y-%m"), []).append(f)
    return [{"month": m, **ratio(metrics.guess_accuracy(fs))} for m, fs in months.items() if metrics.guess_accuracy(fs).den]


def progress(db: Session, user_id: uuid.UUID, now: datetime, observation_days: int) -> dict:
    verdicts.judge_rules(db, user_id, now, observation_days)
    projects = list(db.scalars(select(Project).where(Project.user_id == user_id).order_by(Project.created_at)))
    failures = list(db.scalars(select(Failure).where(Failure.user_id == user_id)))
    recent = [f for f in failures if f.occurred_at >= now - timedelta(weeks=12)]
    rules = list(db.scalars(select(Rule).where(Rule.user_id == user_id).order_by(Rule.created_at)))
    labels = dict(db.execute(select(Pattern.id, Pattern.label).where(Pattern.user_id == user_id)).all())
    predictions = list(db.scalars(select(Prediction).where(Prediction.user_id == user_id)))
    scores = list(db.scalars(select(DrillReview.score).where(DrillReview.user_id == user_id)))
    wins = list(db.scalars(select(Win).where(Win.user_id == user_id)))
    gap, closed = metrics.calibration_gap(predictions)
    by_month = accuracy_by_month(failures)
    calibration = metrics.calibration_by_month(predictions)
    return {
        "projects": project_rows(db, projects, failures),
        "overall": {
            "failures": len(failures),
            "repeat_rate_12_weeks": ratio(metrics.repeat_rate(recent, failures)),
            "guess_accuracy": ratio(metrics.guess_accuracy(failures)),
            "median_hours_to_root_cause": metrics.median_hours_to_root_cause(failures),
            "minutes_lost": sum(f.minutes_lost for f in failures),
            "not_on_checklist": metrics.not_on_checklist(failures),
            "wins_by_tag": metrics.wins_by_tag(wins),
            "calibration_gap": gap,
            "closed_predictions": closed,
            "drill_retention": ratio(metrics.drill_retention(scores)),
            "rule_status": metrics.rule_status_counts(rules),
        },
        "failures_per_week": metrics.failures_per_week_by_category(failures, now.date()),
        "minutes_lost_per_week": metrics.minutes_lost_per_week(failures, now.date()),
        "calibration_by_month": calibration if metrics.enough_for_trend(len(calibration)) else [],
        "guess_accuracy_by_month": by_month if metrics.enough_for_trend(len(by_month)) else [],
        "rules": [
            {"text": r.checklist_text, "pattern": labels.get(r.pattern_id, ""), "status": r.status.value,
             "before": r.failures_before, "after": r.failures_after,
             "verdict_at": r.verdict_at.date().isoformat() if r.verdict_at else None}
            for r in rules
        ],
    }
