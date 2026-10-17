import uuid

import pytest


def new_project(api, name="gateway", **fields):
    response = api.post("/api/v1/projects", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def fail(api, project, title, **fields):
    response = api.post(f"/api/v1/projects/{project}/failures", json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("name", ["drill", "new", "export"])
def test_reserved_command_names_cannot_be_projects(api, name):
    response = api.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 409 and "reserved" in response.json()["detail"]


def test_project_names_are_unique_and_slug_shaped(api):
    new_project(api)
    assert api.post("/api/v1/projects", json={"name": "gateway"}).status_code == 409
    assert api.post("/api/v1/projects", json={"name": "Has Spaces"}).status_code == 409


def test_every_cli_command_is_reserved():
    from preflight.cli.main import cli
    from preflight.core.projects import RESERVED_NAMES

    assert set(cli.commands) <= RESERVED_NAMES


def test_requirements_are_versioned_and_identical_saves_are_ignored(api):
    new_project(api, requirements="Proxy with retries")
    api.put("/api/v1/projects/gateway/requirements", json={"text": "Proxy with retries and caching"})
    api.put("/api/v1/projects/gateway/requirements", json={"text": "Proxy with retries and caching"})
    versions = api.get("/api/v1/projects/gateway/requirements").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[-1]["text"] == "Proxy with retries and caching"


def test_logging_a_failure_on_a_draft_project_is_allowed_and_reports_the_status(api):
    new_project(api)
    logged = fail(api, "gateway", "Retry loop hung for 40 minutes", severity="high", minutes_lost=40)
    assert logged["created"] and logged["project_status"] == "draft"
    assert logged["failure"]["minutes_lost"] == 40


def test_replaying_a_failure_id_does_not_duplicate(api):
    new_project(api)
    failure_id = str(uuid.uuid4())
    first = fail(api, "gateway", "Cache stampede", id=failure_id)
    again = fail(api, "gateway", "Cache stampede", id=failure_id)
    assert first["created"] and not again["created"]
    assert len(api.get("/api/v1/projects/gateway/failures").json()) == 1


def test_similar_past_failures_are_shown_when_logging(api):
    new_project(api, "old")
    new_project(api, "fresh")
    fail(api, "old", "Retry loop hung without timeout", category="missed edge case")
    fail(api, "old", "Rendered the wrong locale", category="knowledge gap")
    logged = fail(api, "fresh", "Webhook retry loop never stopped", category="missed edge case")
    assert [f["title"] for f in logged["similar"]] == ["Retry loop hung without timeout"]


def test_search_matches_any_text_field_and_filters_by_project(api):
    new_project(api, "a")
    new_project(api, "b")
    fail(api, "a", "Stale cache", root_cause="TTL ignored after prompt change")
    fail(api, "b", "Deadlock in worker pool")
    assert [f["title"] for f in api.get("/api/v1/failures", params={"q": "ttl prompt"}).json()] == ["Stale cache"]
    assert [f["title"] for f in api.get("/api/v1/failures", params={"project": "b"}).json()] == ["Deadlock in worker pool"]


def test_failure_fields_can_be_filled_in_later(api):
    new_project(api)
    failure = fail(api, "gateway", "Timeout missing")["failure"]
    patched = api.patch(f"/api/v1/failures/{failure['id']}", json={"root_cause": "no deadline", "guess_correct": False})
    assert patched.json()["root_cause"] == "no deadline" and patched.json()["guess_correct"] is False
    assert api.patch(f"/api/v1/failures/{failure['id']}", json={"bogus": 1}).status_code == 422


def test_failure_can_be_marked_not_on_the_checklist(api):
    new_project(api)
    failure = fail(api, "gateway", "Surprise")["failure"]
    patched = api.patch(f"/api/v1/failures/{failure['id']}", json={"not_on_checklist": True}).json()
    assert patched["on_checklist"] is False and patched["checklist_item_id"] is None


def test_wins_replay_idempotently(api):
    new_project(api)
    win_id = str(uuid.uuid4())
    for _ in range(2):
        assert api.post("/api/v1/projects/gateway/wins", json={"id": win_id, "text": "Caught 429 handling early", "tag": "caught a risk early"}).status_code == 201
    assert api.get("/api/v1/projects/gateway/status").json()["wins"] == 1


def test_other_users_cannot_see_or_touch_my_data(api, other):
    new_project(api)
    failure = fail(api, "gateway", "Private failure")["failure"]
    assert other.get("/api/v1/projects/gateway").status_code == 404
    assert other.get("/api/v1/failures").json() == []
    assert other.patch(f"/api/v1/failures/{failure['id']}", json={"fix": "x"}).status_code == 404
    other.post("/api/v1/projects", json={"name": "gateway"})
    assert other.post("/api/v1/projects/gateway/failures", json={"id": failure["id"], "title": "steal"}).status_code == 404


def test_deleting_a_project_deletes_its_failures(api):
    new_project(api)
    fail(api, "gateway", "Gone soon")
    assert api.delete("/api/v1/projects/gateway").status_code == 200
    assert api.get("/api/v1/failures").json() == []


def test_unknown_project_is_404(api):
    assert api.post("/api/v1/projects/nope/failures", json={"title": "x"}).status_code == 404
