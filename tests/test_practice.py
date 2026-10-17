from datetime import timedelta

import pytest

from preflight.core.drills import next_ease, next_interval
from tests.conftest import token_client
from tests.test_capture import fail, new_project


@pytest.fixture
def api(app):
    app.state.settings.access_token_minutes = 10**6
    return token_client(app, "me")


def test_intervals_climb_the_ladder_then_grow_by_ease():
    steps, interval = [], 0
    for _ in range(6):
        interval = next_interval(interval, 2.5, 4)
        steps.append(interval)
    assert steps == [1, 3, 7, 21, 52, 130]
    assert next_interval(21, 2.5, 2) == 1, "a poor answer starts over"


def test_ease_rises_on_good_answers_and_never_drops_below_the_floor():
    assert next_ease(2.5, 5) == 2.6 and next_ease(2.5, 3) == 2.36
    assert next_ease(1.3, 0) == 1.3


@pytest.fixture
def closed(api):
    new_project(api, "billing")
    api.post("/api/v1/projects/billing/start", json={})
    fail(api, "billing", "Retry loop hung", what_happened="provider stalled", root_cause="no total deadline",
         prevention="Every external call has a deadline")
    fail(api, "billing", "Typo")
    api.post("/api/v1/projects/billing/close", json={"summary": "done"})
    return api


def test_closing_a_project_creates_one_drill_per_failure_with_a_cause(closed, clock):
    assert closed.get("/api/v1/drills/today").json() == [], "first drill is due one day later"
    clock.advance(days=1)
    due = closed.get("/api/v1/drills/today").json()
    assert len(due) == 1
    assert due[0]["prompt"] == 'In billing: "Retry loop hung". provider stalled What was the root cause, and what check would have caught it?'
    assert due[0]["answer"] == "Root cause: no total deadline. Check: Every external call has a deadline"
    assert len(closed.get("/api/v1/drills/today").json()) == 1, "rerunning does not duplicate"


def test_self_scored_reviews_reschedule_and_feed_retention(closed, clock):
    clock.advance(days=1)
    drill = closed.get("/api/v1/drills/today").json()[0]
    good = closed.post(f"/api/v1/drills/{drill['id']}/review", json={"score": 4}).json()
    assert good["interval_days"] == 1 and good["next_due"] == (clock.now + timedelta(days=1)).date().isoformat()
    assert closed.get("/api/v1/drills/today").json() == []
    clock.advance(days=1)
    poor = closed.post(f"/api/v1/drills/{drill['id']}/review", json={"score": 1}).json()
    assert poor["interval_days"] == 1
    assert closed.post(f"/api/v1/drills/{drill['id']}/review", json={}).status_code == 409
    assert closed.get("/api/v1/progress").json()["overall"]["drill_retention"]["text"] == "50% (1 of 2)"


def test_ai_grading_sends_only_this_drill_and_the_attempt(closed, clock, ai):
    clock.advance(days=1)
    drill = closed.get("/api/v1/drills/today").json()[0]
    assert closed.post(f"/api/v1/drills/{drill['id']}/review", json={"grade_with_ai": True}).status_code == 409
    graded = closed.post(f"/api/v1/drills/{drill['id']}/review", json={"grade_with_ai": True, "attempt": "missing deadline"}).json()
    assert graded["score"] == 4 and graded["feedback"].startswith("Right cause")
    sent = ai.calls[-1]
    assert sent["model"] == "claude-haiku-5-5"
    text = sent["content"][0]["text"]
    assert "Practice answer: missing deadline" in text and "Typo" not in text


def test_predictions_close_with_an_outcome_and_feed_calibration(api):
    new_project(api, "gateway")
    failure = fail(api, "gateway", "Stale cache")["failure"]
    first = api.post("/api/v1/projects/gateway/predictions", json={"hypothesis": "TTL ignored", "evidence_planned": "cache logs", "confidence": 90}).json()
    second = api.post("/api/v1/projects/gateway/predictions", json={"hypothesis": "Clock skew", "confidence": 60}).json()
    assert api.post("/api/v1/projects/gateway/predictions", json={"hypothesis": "x", "confidence": 101}).status_code == 422

    closed = api.post(f"/api/v1/predictions/{first['id']}/close", json={"correct": True, "failure_id": failure["id"]}).json()
    assert closed["correct"] is True and closed["failure_id"] == failure["id"]
    api.post(f"/api/v1/predictions/{second['id']}/close", json={"correct": False})
    assert api.post(f"/api/v1/predictions/{second['id']}/close", json={"correct": True}).status_code == 409

    overall = api.get("/api/v1/progress").json()["overall"]
    assert overall["calibration_gap"] == pytest.approx(0.25) and overall["closed_predictions"] == 2
    assert [p["hypothesis"] for p in api.get("/api/v1/projects/gateway/predictions").json()] == ["TTL ignored", "Clock skew"]


def test_a_prediction_links_only_to_a_failure_in_its_project(api):
    new_project(api, "a")
    new_project(api, "b")
    other = fail(api, "b", "Elsewhere")["failure"]
    p = api.post("/api/v1/projects/a/predictions", json={"hypothesis": "h", "confidence": 50}).json()
    assert api.post(f"/api/v1/predictions/{p['id']}/close", json={"correct": True, "failure_id": other["id"]}).status_code == 409


def test_other_users_cannot_review_my_drills(app, closed, clock, other):
    clock.advance(days=1)
    drill = closed.get("/api/v1/drills/today").json()[0]
    stranger = token_client(app, "someone-else")
    assert stranger.post(f"/api/v1/drills/{drill['id']}/review", json={"score": 5}).status_code == 404
