import pytest

from tests.test_capture import fail, new_project

TIMEOUT_CHECK = "Every external call has a timeout, retry cap, and backoff"
ATOMIC_CHECK = "Read-then-write on shared state is atomic"


@pytest.fixture
def history(api):
    """A closed-out past project with the failures from the requirements example session."""
    new_project(api, "billing")
    fail(api, "billing", "Retry loop hung 40 minutes", category="missed edge case", severity="high",
         minutes_lost=40, prevention=TIMEOUT_CHECK)
    fail(api, "billing", "Budget counter lost updates", category="state or concurrency bug",
         minutes_lost=25, prevention=ATOMIC_CHECK)
    fail(api, "billing", "Two workers double-charged", category="state or concurrency bug",
         minutes_lost=15, prevention=ATOMIC_CHECK)
    fail(api, "billing", "Typo in log message", category="no verification", severity="low")
    return api


def start(api, name="gateway", **body):
    response = api.post(f"/api/v1/projects/{name}/start", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def check(api, item, status, **extra):
    return api.patch(f"/api/v1/checklist-items/{item['id']}", json={"status": status, **extra})


def by_text(checklist):
    return {i["text"]: i for i in checklist["items"]}


def test_checklist_comes_from_history_with_sources_priorities_and_required_flags(history):
    new_project(history, requirements="Proxy for LLM providers")
    result = start(history)
    items = by_text(result["checklist"])

    assert not result["started"]
    assert items[TIMEOUT_CHECK]["required"] and items[TIMEOUT_CHECK]["priority"] == "high"
    assert items[ATOMIC_CHECK]["required"], "repeated pattern makes it required"
    assert "seen 2 times" in items[ATOMIC_CHECK]["why"]
    low = items["Guard against a repeat of: Typo in log message"]
    assert not low["required"] and low["priority"] == "low"
    assert all(i["source_type"] == "failure" and i["source_ref"] for i in items.values())
    assert [i["position"] for i in result["checklist"]["items"]] == [1, 2, 3]
    assert [i["priority"] for i in result["checklist"]["items"]] == ["high", "high", "low"]
    assert result["checklist"]["project"]["checklist_requirements_version"] == 1


def test_project_starts_when_the_last_required_item_is_resolved(history):
    new_project(history)
    checklist = start(history)["checklist"]
    required = [i for i in checklist["items"] if i["required"]]
    assert check(history, required[0], "done").json()["project_started"] is False
    last = check(history, required[1], "not applicable", reason="no shared state in this service").json()
    assert last["project_started"] is True
    assert history.get("/api/v1/projects/gateway").json()["status"] == "started"


def test_not_applicable_and_deferred_need_reasons_and_a_future_date(history):
    new_project(history)
    item = start(history)["checklist"]["items"][0]
    assert check(history, item, "not applicable").status_code == 409
    assert check(history, item, "deferred", reason="later").status_code == 409
    assert check(history, item, "deferred", reason="later", revisit_date="2026-09-01").status_code == 409
    deferred = check(history, item, "deferred", reason="after load test", revisit_date="2026-11-01")
    assert deferred.status_code == 200 and deferred.json()["item"]["revisit_date"] == "2026-11-01"


def test_rerunning_start_keeps_resolved_statuses(history):
    new_project(history)
    first = start(history)["checklist"]["items"][0]
    check(history, first, "done")
    again = by_text(start(history)["checklist"])
    assert again[first["text"]]["status"] == "done"
    assert again[first["text"]]["id"] == first["id"]


def test_gate_override_needs_a_reason_and_is_recorded(app, history):
    new_project(history)
    assert history.post("/api/v1/projects/gateway/start", json={"override_reason": "  "}).status_code == 409
    assert start(history, override_reason="hackathon, accept the risk")["started"] is True

    from preflight.db.models import GateOverride

    with app.state.sessionmaker() as db:
        assert [o.reason for o in db.query(GateOverride)] == ["hackathon, accept the risk"]


def test_no_history_means_an_empty_checklist_and_an_immediate_start(api):
    new_project(api)
    result = start(api)
    assert result["started"] and result["checklist"]["items"] == []


def test_started_projects_cannot_start_again(api):
    new_project(api)
    start(api)
    assert api.post("/api/v1/projects/gateway/start", json={}).status_code == 409


def test_patterns_suggest_a_rule_drafted_from_prevention_notes(history):
    suggestions = history.get("/api/v1/patterns").json()
    concurrency = [s for s in suggestions if s["pattern"]["category"] == "state or concurrency bug"]
    assert {s["pattern"]["project_scope"] is None for s in concurrency} == {True, False}
    assert all(s["suggested_rule"] == ATOMIC_CHECK for s in concurrency)


def test_an_approved_rule_replaces_its_category_failures_on_the_checklist(history):
    pattern = next(
        s["pattern"] for s in history.get("/api/v1/patterns").json()
        if s["pattern"]["category"] == "state or concurrency bug" and s["pattern"]["project_scope"] is None
    )
    rule = history.post("/api/v1/rules", json={"pattern_id": pattern["id"], "checklist_text": "Rule: shared-state-safety"}).json()
    assert history.post("/api/v1/rules", json={"pattern_id": pattern["id"], "checklist_text": "dup"}).status_code == 409

    new_project(history)
    items = by_text(start(history)["checklist"])
    assert ATOMIC_CHECK not in items
    assert items["Rule: shared-state-safety"]["source_type"] == "rule"
    assert not items["Rule: shared-state-safety"]["required"]

    history.patch(f"/api/v1/rules/{rule['id']}", json={"status": "needs revision"})
    new_project(history, "second")
    items = by_text(start(history, "second")["checklist"])
    assert items["Rule: shared-state-safety"]["required"], "a rule that needs revision is required"


def test_failures_can_link_only_to_items_in_their_own_project(history):
    new_project(history)
    item = by_text(start(history)["checklist"])[TIMEOUT_CHECK]
    logged = fail(history, "gateway", "Retry loop again without timeout", prevention=TIMEOUT_CHECK)
    assert [i["id"] for i in logged["related_items"]][:1] == [item["id"]]

    linked = history.patch(f"/api/v1/failures/{logged['failure']['id']}", json={"link_item_id": item["id"]})
    assert linked.json()["on_checklist"] is True

    old = history.get("/api/v1/failures", params={"project": "billing"}).json()[0]
    assert history.patch(f"/api/v1/failures/{old['id']}", json={"link_item_id": item["id"]}).status_code == 409


def test_checklist_exports_as_markdown(history):
    new_project(history, requirements="v1")
    item = start(history)["checklist"]["items"][0]
    check(history, item, "done")
    md = history.get("/api/v1/projects/gateway/checklist", params={"format": "md"}).text
    assert md.startswith("# Checklist: gateway")
    assert "Built from requirements v1." in md
    assert f"- [x] **1.** {item['text']}" in md
