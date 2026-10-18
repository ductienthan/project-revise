"""Dashboard numbers from plain code. Every ratio keeps its counts so a percentage is never shown alone."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from preflight.db.models import (
    ChecklistItem,
    Failure,
    ItemOutcome,
    ItemStatus,
    Prediction,
    QuestionStatus,
    Rule,
    Win,
)

REPEAT_WINDOW = timedelta(days=60)
MIN_POINTS_FOR_TREND = 3


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None

    def __str__(self) -> str:
        return f"{self.value:.0%} ({self.num} of {self.den})" if self.den else "No data yet"


def repeats(failures: list[Failure]) -> set:
    """Ids of failures whose category already appeared in an earlier failure within 60 days."""
    ordered = sorted(failures, key=lambda f: f.occurred_at)
    seen: dict[str, list[datetime]] = defaultdict(list)
    found = set()
    for f in ordered:
        if f.category is not None:
            if any(f.occurred_at - earlier <= REPEAT_WINDOW for earlier in seen[f.category]):
                found.add(f.id)
            seen[f.category].append(f.occurred_at)
    return found


def repeat_rate(in_period: list[Failure], history: list[Failure]) -> Ratio:
    repeated = repeats(history)
    return Ratio(sum(f.id in repeated for f in in_period), len(in_period))


def guess_accuracy(failures: list[Failure]) -> Ratio:
    known = [f for f in failures if f.guess_correct is not None]
    return Ratio(sum(f.guess_correct for f in known), len(known))


def median_hours_to_root_cause(failures: list[Failure]) -> float | None:
    hours = [(f.root_cause_at - f.occurred_at).total_seconds() / 3600 for f in failures if f.root_cause_at and f.root_cause_at >= f.occurred_at]
    return round(median(hours), 1) if hours else None


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_starts(today: date, weeks: int) -> list[date]:
    return [week_start(today) - timedelta(weeks=n) for n in reversed(range(weeks))]


def minutes_lost_per_week(failures: list[Failure], today: date, weeks: int = 12) -> dict[str, list]:
    starts = week_starts(today, weeks)
    index = {start: i for i, start in enumerate(starts)}
    minutes = [0] * weeks
    for f in failures:
        i = index.get(week_start(f.occurred_at.date()))
        if i is not None:
            minutes[i] += f.minutes_lost
    return {"weeks": [s.isoformat() for s in starts], "minutes": minutes}


def failures_per_week_by_category(failures: list[Failure], today: date, weeks: int = 12) -> dict[str, list[int]]:
    starts = week_starts(today, weeks)
    index = {start: i for i, start in enumerate(starts)}
    series: dict[str, list[int]] = defaultdict(lambda: [0] * weeks)
    for f in failures:
        i = index.get(week_start(f.occurred_at.date()))
        if i is not None:
            series[f.category or "uncategorized"][i] += 1
    return {"weeks": [s.isoformat() for s in starts], **dict(series)}


def checklist_coverage(items: list[ChecklistItem], started_at: datetime | None) -> Ratio:
    if started_at is None:
        return Ratio(0, 0)
    return Ratio(sum(1 for i in items if i.resolved_at and i.resolved_at <= started_at), len(items))


def on_checklist_rate(failures: list[Failure]) -> Ratio:
    return Ratio(sum(1 for f in failures if f.on_checklist), len(failures))


def missed_item_rate(items: list[ChecklistItem], failures: list[Failure]) -> Ratio:
    done = {i.id for i in items if i.status == ItemStatus.DONE}
    missed = {f.checklist_item_id for f in failures if f.checklist_item_id in done}
    return Ratio(len(missed), len(done))


def not_on_checklist(failures: list[Failure]) -> int:
    return sum(1 for f in failures if f.on_checklist is False)


def question_coverage(statuses: list[QuestionStatus]) -> Ratio:
    return Ratio(sum(1 for s in statuses if s == QuestionStatus.ANSWERED), len(statuses))


def wins_by_tag(wins: list[Win]) -> dict[str, int]:
    return dict(Counter(str(w.tag) if w.tag else "untagged" for w in wins))


def outcomes(items: list[ChecklistItem]) -> dict[str, int]:
    counts = Counter(i.outcome for i in items if i.outcome)
    return {o.value: counts.get(o, 0) for o in ItemOutcome}


def calibration_gap(predictions: list[Prediction]) -> tuple[float | None, int]:
    """Average confidence minus accuracy over closed predictions. Positive means overconfident."""
    closed = [p for p in predictions if p.correct is not None]
    if not closed:
        return None, 0
    confidence = sum(p.confidence for p in closed) / len(closed) / 100
    accuracy = sum(p.correct for p in closed) / len(closed)
    return round(confidence - accuracy, 3), len(closed)


def calibration_by_month(predictions: list[Prediction]) -> list[dict]:
    months: dict[str, list[Prediction]] = defaultdict(list)
    for p in sorted(predictions, key=lambda p: p.created_at):
        if p.correct is not None:
            months[p.created_at.strftime("%Y-%m")].append(p)
    return [{"month": m, "gap": calibration_gap(ps)[0], "count": len(ps)} for m, ps in months.items()]


def drill_retention(scores: list[int]) -> Ratio:
    return Ratio(sum(1 for s in scores if s >= 3), len(scores))


def rule_status_counts(rules: list[Rule]) -> dict[str, int]:
    counts = Counter(r.status.value for r in rules)
    return {s: counts.get(s, 0) for s in ("active", "working", "needs revision", "retired")}


def enough_for_trend(points: int) -> bool:
    return points >= MIN_POINTS_FOR_TREND
