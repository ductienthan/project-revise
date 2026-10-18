import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from preflight.core import metrics
from preflight.core.metrics import Ratio
from preflight.core.verdicts import verdict
from preflight.db.models import (
    FailureCategory,
    ItemOutcome,
    ItemStatus,
    QuestionStatus,
    RuleStatus,
    WinTag,
)

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
EDGE, STATE = FailureCategory.MISSED_EDGE_CASE, FailureCategory.STATE_OR_CONCURRENCY


def f(days: float, category=EDGE, **kw):
    base = {"id": uuid.uuid4(), "category": category, "occurred_at": T0 + timedelta(days=days), "guess_correct": None,
                "root_cause_at": None, "minutes_lost": 0, "on_checklist": None, "checklist_item_id": None}
    return SimpleNamespace(**(base | kw))


def test_ratio_shows_counts_and_handles_no_data():
    assert str(Ratio(2, 3)) == "67% (2 of 3)"
    assert str(Ratio(0, 0)) == "No data yet" and Ratio(0, 0).value is None


def test_repeat_rate_counts_category_repeats_within_60_days():
    first, repeat, late, other, blank = f(0), f(30), f(95), f(31, STATE), f(32, None)
    history = [first, repeat, late, other, blank]
    assert metrics.repeats(history) == {repeat.id}, "day 95 is 65 days after day 30"
    assert metrics.repeat_rate(history, history) == Ratio(1, 5)
    assert metrics.repeat_rate([late, other], history) == Ratio(0, 2)


def test_guess_accuracy_uses_only_failures_with_both_recorded():
    assert metrics.guess_accuracy([f(0, guess_correct=True), f(1, guess_correct=False), f(2)]) == Ratio(1, 2)


def test_median_hours_to_root_cause():
    rows = [f(0, root_cause_at=T0 + timedelta(hours=2)), f(0, root_cause_at=T0 + timedelta(hours=6)),
            f(0, root_cause_at=T0 + timedelta(hours=30)), f(0)]
    assert metrics.median_hours_to_root_cause(rows) == 6.0
    assert metrics.median_hours_to_root_cause([f(0)]) is None


def test_minutes_lost_and_failures_are_grouped_by_monday_weeks():
    rows = [f(0, minutes_lost=10), f(1, minutes_lost=5), f(7, STATE, minutes_lost=20)]
    assert metrics.minutes_lost_per_week(rows, today=date(2026, 9, 10), weeks=3) == {
        "weeks": ["2026-08-24", "2026-08-31", "2026-09-07"], "minutes": [0, 15, 20]}
    series = metrics.failures_per_week_by_category(rows, today=date(2026, 9, 10), weeks=3)
    assert series == {"weeks": ["2026-08-24", "2026-08-31", "2026-09-07"], "missed edge case": [0, 2, 0], "state or concurrency bug": [0, 0, 1]}


def item(status=ItemStatus.NOT_CHECKED, resolved_at=None, outcome=None):
    return SimpleNamespace(id=uuid.uuid4(), status=status, resolved_at=resolved_at, outcome=outcome)


def test_checklist_coverage_counts_items_resolved_before_the_start():
    started = T0 + timedelta(days=1)
    items = [item(ItemStatus.DONE, T0), item(ItemStatus.DONE, T0 + timedelta(days=2)), item()]
    assert metrics.checklist_coverage(items, started) == Ratio(1, 3)
    assert metrics.checklist_coverage(items, None) == Ratio(0, 0)


def test_on_checklist_missed_and_not_on_checklist():
    done, open_ = item(ItemStatus.DONE), item()
    rows = [f(0, on_checklist=True, checklist_item_id=done.id), f(1, on_checklist=True, checklist_item_id=open_.id),
            f(2, on_checklist=False), f(3)]
    assert metrics.on_checklist_rate(rows) == Ratio(2, 4)
    assert metrics.missed_item_rate([done, open_], rows) == Ratio(1, 1), "a failure on an item marked done is a miss"
    assert metrics.not_on_checklist(rows) == 1


def test_question_coverage_counts_answered_only():
    statuses = [QuestionStatus.ANSWERED, QuestionStatus.NOT_RELEVANT, QuestionStatus.OPEN, QuestionStatus.ANSWERED]
    assert metrics.question_coverage(statuses) == Ratio(2, 4)


def test_wins_and_outcomes_are_counted_by_kind():
    wins = [SimpleNamespace(tag=WinTag.USED_RULE), SimpleNamespace(tag=None), SimpleNamespace(tag=WinTag.USED_RULE)]
    assert metrics.wins_by_tag(wins) == {"used a rule": 2, "untagged": 1}
    items = [item(outcome=ItemOutcome.HELPED), item(outcome=ItemOutcome.HELPED), item(outcome=ItemOutcome.MISSED), item()]
    assert metrics.outcomes(items) == {"helped": 2, "missed": 1, "irrelevant": 0}


def test_calibration_gap_is_confidence_minus_accuracy():
    predictions = [SimpleNamespace(confidence=90, correct=True), SimpleNamespace(confidence=80, correct=False),
                   SimpleNamespace(confidence=70, correct=None)]
    assert metrics.calibration_gap(predictions) == (0.35, 2), "0.85 average confidence, 0.5 accuracy"
    assert metrics.calibration_gap([]) == (None, 0)


def test_calibration_by_month_uses_closed_predictions_only():
    def p(month, confidence, correct):
        return SimpleNamespace(created_at=datetime(2026, month, 5, tzinfo=UTC), confidence=confidence, correct=correct)

    rows = [p(8, 90, False), p(8, 70, True), p(9, 60, True), p(9, 50, None)]
    assert metrics.calibration_by_month(rows) == [{"month": "2026-08", "gap": 0.3, "count": 2}, {"month": "2026-09", "gap": -0.4, "count": 1}]


def test_drill_retention_counts_scores_of_three_or_more():
    assert metrics.drill_retention([5, 3, 2, 0]) == Ratio(2, 4)


def test_rule_status_counts_include_zeroes():
    rules = [SimpleNamespace(status=RuleStatus.WORKING), SimpleNamespace(status=RuleStatus.WORKING)]
    assert metrics.rule_status_counts(rules) == {"active": 0, "working": 2, "needs revision": 0, "retired": 0}


def test_rule_verdicts():
    assert verdict(before=3, after=1) == RuleStatus.WORKING
    assert verdict(before=0, after=0) == RuleStatus.WORKING
    assert verdict(before=2, after=2) == RuleStatus.NEEDS_REVISION
    assert verdict(before=0, after=1) == RuleStatus.NEEDS_REVISION


def test_trends_need_three_points():
    assert not metrics.enough_for_trend(2) and metrics.enough_for_trend(3)


def test_stored_times_come_back_in_utc_so_day_arithmetic_ignores_dst():
    from zoneinfo import ZoneInfo

    from preflight.db.base import UTCDateTime

    pacific = datetime(2026, 10, 30, 2, tzinfo=ZoneInfo("America/Los_Angeles"))
    loaded = UTCDateTime().process_result_value(pacific, None)
    assert loaded.tzinfo == UTC
    assert loaded + timedelta(days=28) - loaded == timedelta(hours=28 * 24)
    assert (loaded + timedelta(days=28)).hour == 9, "Nov 27 09:00 UTC, not shifted by the DST change"
