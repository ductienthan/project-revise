import io
import json
import zipfile
from datetime import timedelta

import pytest

from tests.conftest import token_client
from tests.test_capture import fail, new_project
from tests.test_checklist import ATOMIC_CHECK, TIMEOUT_CHECK


@pytest.fixture
def api(app):
    """These tests move the clock by weeks, so they need a token that outlives the move."""
    app.state.settings.access_token_minutes = 10**6
    return token_client(app, "me")


def started(api, name, **fields):
    new_project(api, name, **fields)
    result = api.post(f"/api/v1/projects/{name}/start", json={"override_reason": "test"}).json()
    return result["checklist"]["items"]


def test_close_needs_a_summary_records_outcomes_and_shows_overrides(api):
    new_project(api, "old")
    fail(api, "old", "Retry hang", severity="high", prevention=TIMEOUT_CHECK)
    items = started(api, "gateway")
    assert api.post("/api/v1/projects/gateway/close", json={}).status_code == 409
    closed = api.post("/api/v1/projects/gateway/close", json={
        "summary": "Shipped. Timeouts caught two outages.", "outcomes": {items[0]["id"]: "helped"},
    }).json()
    assert closed["checklist"]["project"]["status"] == "closed"
    assert closed["checklist"]["items"][0]["outcome"] == "helped"
    assert closed["gate_overrides"] == ["test"]
    assert api.post("/api/v1/projects/gateway/close", json={"summary": "again"}).status_code == 409


def test_closing_a_draft_is_refused(api):
    new_project(api, "gateway")
    assert api.post("/api/v1/projects/gateway/close", json={"summary": "x"}).status_code == 409


def test_progress_numbers_match_the_data(api, clock):
    new_project(api, "billing")
    a = fail(api, "billing", "Lost update", category="state or concurrency bug", minutes_lost=25, prevention=ATOMIC_CHECK,
             guessed_cause="cache", root_cause="no lock", guess_correct=False)["failure"]
    clock.advance(days=3)
    fail(api, "billing", "Double charge", category="state or concurrency bug", minutes_lost=15, prevention=ATOMIC_CHECK,
         guessed_cause="race", root_cause="race", guess_correct=True)
    items = started(api, "gateway")
    item_id = next(i["id"] for i in items if i["text"] == ATOMIC_CHECK)
    clock.advance(minutes=5)
    api.patch(f"/api/v1/checklist-items/{item_id}", json={"status": "done"})
    clock.advance(days=1)
    g = fail(api, "gateway", "Race again", category="state or concurrency bug", minutes_lost=30)["failure"]
    api.patch(f"/api/v1/failures/{g['id']}", json={"link_item_id": item_id})
    api.post("/api/v1/projects/gateway/wins", json={"text": "Caught 429s early", "tag": "caught a risk early"})

    data = api.get("/api/v1/progress").json()
    rows = {p["name"]: p for p in data["projects"]}
    assert rows["billing"]["failures"] == 2 and rows["billing"]["repeat_rate"]["text"] == "50% (1 of 2)"
    assert rows["gateway"]["repeat_rate"]["text"] == "100% (1 of 1)"
    assert rows["gateway"]["on_checklist_rate"]["text"] == "100% (1 of 1)"
    assert rows["gateway"]["missed_item_rate"]["text"] == "100% (1 of 1)"
    assert rows["gateway"]["checklist_coverage"]["text"] == "0% (0 of 1)", "resolved after an override start"
    assert rows["gateway"]["wins_by_tag"] == {"caught a risk early": 1}
    overall = data["overall"]
    assert overall["guess_accuracy"]["text"] == "50% (1 of 2)"
    assert overall["minutes_lost"] == 70 and overall["repeat_rate_12_weeks"]["text"] == "67% (2 of 3)"
    assert overall["median_hours_to_root_cause"] == 0.0
    assert sum(data["failures_per_week"]["state or concurrency bug"]) == 3
    assert data["guess_accuracy_by_month"] == [], "one month of data is not a trend"
    assert a["id"]


def test_rule_verdict_is_given_after_the_observation_period(api, clock):
    new_project(api, "billing")
    for title in ("Lost update", "Double charge"):
        fail(api, "billing", title, category="state or concurrency bug", prevention=ATOMIC_CHECK)
    pattern = next(s["pattern"] for s in api.get("/api/v1/patterns").json() if s["pattern"]["project_scope"] is None)
    rule = api.post("/api/v1/rules", json={"pattern_id": pattern["id"], "checklist_text": "Rule: atomic"}).json()
    assert api.get("/api/v1/rules").json()[0]["status"] == "active"
    clock.advance(days=29)
    judged = api.get("/api/v1/rules").json()[0]
    assert (judged["status"], judged["failures_before"], judged["failures_after"]) == ("working", 2, 0)
    assert judged["id"] == rule["id"]
    clock.advance(days=1)
    new_project(api, "next")
    fail(api, "next", "Race", category="state or concurrency bug")
    fail(api, "next", "Race 2", category="state or concurrency bug")
    assert api.get("/api/v1/rules").json()[0]["status"] == "working", "not judged again within the period"
    clock.advance(days=27)
    judged = api.get("/api/v1/rules").json()[0]
    assert (judged["status"], judged["failures_before"], judged["failures_after"]) == ("needs revision", 2, 2)


def test_weekly_report_covers_the_last_seven_days(api, clock):
    new_project(api, "billing")
    fail(api, "billing", "Old one", category="missed edge case", minutes_lost=5)
    clock.advance(days=8)
    fail(api, "billing", "New one", category="missed edge case", minutes_lost=12)
    api.post("/api/v1/projects/billing/wins", json={"text": "Shipped v1"})
    data = api.get("/api/v1/reports/weekly").json()
    assert (data["failures"], data["failures_last_week"], data["minutes_lost"]) == (1, 1, 12)
    assert data["by_category"] == {"missed edge case": 1} and data["wins"] == ["Shipped v1"]
    md = api.get("/api/v1/reports/weekly", params={"format": "md"}).text
    assert md.startswith("# Weekly report") and "- Failures: 1 (last week 1), 12 minutes lost." in md


def test_deferred_items_show_up_for_revisit(api, clock):
    new_project(api, "old")
    fail(api, "old", "Retry hang", severity="high", prevention=TIMEOUT_CHECK)
    new_project(api, "gateway")
    item = api.post("/api/v1/projects/gateway/start", json={}).json()["checklist"]["items"][0]
    revisit = (clock.now + timedelta(days=5)).date().isoformat()
    api.patch(f"/api/v1/checklist-items/{item['id']}", json={"status": "deferred", "reason": "after load test", "revisit_date": revisit})
    assert api.get("/api/v1/reports/weekly").json()["revisit"] == [f"gateway item 1: {TIMEOUT_CHECK} (due {revisit})"]


def test_export_contains_my_data_and_no_auth_secrets(api, other):
    new_project(api, "billing", requirements="Retries.")
    fail(api, "billing", "Retry hang")
    other.post("/api/v1/projects", json={"name": "theirs"})

    data = json.loads(api.get("/api/v1/export").text)
    assert [p["name"] for p in data["projects"]] == ["billing"]
    assert data["failures"][0]["title"] == "Retry hang" and "requirement_versions" in data
    assert not {"users", "devices", "web_sessions", "device_codes", "audit_events"} & set(data)

    archive = zipfile.ZipFile(io.BytesIO(api.get("/api/v1/export", params={"format": "csv"}).content))
    assert "failures.csv" in archive.namelist()
    assert "Retry hang" in archive.read("failures.csv").decode()

    md = api.get("/api/v1/export", params={"format": "md"}).text
    assert "## billing (draft)" in md and "**Retry hang**" in md and "### Requirements v1" in md
