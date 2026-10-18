import re

from tests.test_capture import fail, new_project


def complete(api, approved):
    r = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": approved}).json()
    api.post(f"/api/v1/reviews/{r['id']}/answers", json={"answers": [], "accepted_drafts": list(range(len(r["drafts"])))})
    return r


def items(api):
    return api.get("/api/v1/projects/gateway/review-items").json()


def test_the_text_shown_for_approval_is_exactly_what_is_sent(api, ai):
    new_project(api, "old")
    fail(api, "old", "Retry hang", prevention="Every call has a deadline")
    new_project(api, "gateway", idea="LLM proxy", requirements="Retries.\nBudgets.")
    shown = {i["id"]: i["text"] for i in items(api)["items"]}
    complete(api, list(shown))
    sent = {re.search(r'id="([^"]+)"', b["text"]).group(1): b["text"].split("\n", 1)[1].rsplit("\n", 1)[0] for b in ai.calls[-1]["content"]}
    assert sent == shown


def test_a_review_goes_stale_when_links_or_history_change(api, clock):
    new_project(api, "old")
    new_project(api, "gateway", requirements="Retries.")
    assert items(api)["reviewed"] is False
    complete(api, ["req:v1"])
    assert items(api)["review_current"] is True
    api.post("/api/v1/projects/gateway/links", json={"url": "https://wiki.corp/spec", "internal": True, "note": "p95 800ms"})
    assert items(api)["review_current"] is False, "a new link makes the review stale"
    clock.advance(minutes=1)
    complete(api, ["req:v1"])
    assert items(api)["review_current"] is True
    fail(api, "old", "New outage", prevention="Check X")
    assert items(api)["review_current"] is False, "new history makes the review stale"


def test_reviewing_a_started_project_adds_to_its_checklist_and_keeps_it_started(api):
    new_project(api, "old")
    fail(api, "old", "Retry hang", prevention="Every call has a deadline")
    new_project(api, "gateway", requirements="Retries.")
    first = api.post("/api/v1/projects/gateway/start", json={}).json()["checklist"]["items"]
    api.patch(f"/api/v1/checklist-items/{first[0]['id']}", json={"status": "done"})
    assert api.get("/api/v1/projects/gateway").json()["status"] == "started"

    complete(api, ["req:v1", "history"])
    refreshed = api.post("/api/v1/projects/gateway/checklist/refresh").json()
    texts = {i["text"]: i for i in refreshed["items"]}
    assert "Define behavior when the provider returns 429" in texts
    assert texts["Every call has a deadline"]["status"] == "done", "statuses survive the refresh"
    assert refreshed["project"]["status"] == "started"


def test_closed_projects_cannot_be_reviewed_and_drafts_must_use_start(api):
    new_project(api, "gateway", requirements="Retries.")
    assert api.post("/api/v1/projects/gateway/checklist/refresh").status_code == 409
    api.post("/api/v1/projects/gateway/start", json={})
    api.post("/api/v1/projects/gateway/close", json={"summary": "done"})
    assert api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).status_code == 409
