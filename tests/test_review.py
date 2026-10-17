import json
import pathlib
import re

import pytest

from preflight.ai.client import AIError, AnthropicClient
from preflight.ai.schemas import ReviewOutput, strict_schema
from preflight.db.models import AiCall
from tests.fakes import FakeAnthropic
from tests.test_capture import fail, new_project
from tests.test_checklist import TIMEOUT_CHECK

REQUIREMENTS = "Proxy for LLM providers.\nRetries on provider errors.\nPer-user budgets."


@pytest.fixture
def ready(api):
    new_project(api, "billing")
    fail(api, "billing", "Retry loop hung 40 minutes", category="missed edge case", severity="high",
         minutes_lost=40, prevention=TIMEOUT_CHECK)
    new_project(api, "gateway", idea="LLM proxy", goals="p95 under 800ms", requirements=REQUIREMENTS)
    return api


def items(api):
    return api.get("/api/v1/projects/gateway/review-items").json()


def review(api, approved):
    response = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": approved})
    assert response.status_code == 201, response.text
    return response.json()


def sent_text(ai) -> str:
    return "\n".join(b["text"] for b in ai.calls[-1]["content"])


def test_review_items_list_what_could_be_sent_with_an_estimate(ready):
    found = items(ready)
    assert [i["id"] for i in found["items"]] == ["project", "req:v1", "history"]
    assert found["items"][2]["label"] == "Failure history and rules (1 failures, 0 rules)"
    assert found["ai_available"] and found["model"] == "claude-opus-5-5" and found["max_cost_usd"] > 0


def test_only_approved_items_are_sent_and_the_payload_is_logged(app, ready, ai):
    review(ready, ["req:v1"])
    text = sent_text(ai)
    assert "REQ:2 Retries on provider errors." in text
    assert "Retry loop hung" not in text and "LLM proxy" not in text
    with app.state.sessionmaker() as db:
        call = db.query(AiCall).one()
    assert call.approved_items == ["req:v1"] and call.prompt_version == "review-v1" and call.model == "claude-opus-5-5"
    assert "REQ:2" in call.payload and "Retry loop hung" not in call.payload
    assert call.cost_usd == pytest.approx((1000 * 4 + 200 * 20) / 1_000_000)


def test_unknown_or_sensitive_items_cannot_be_approved(app, ready):
    from preflight.db.models import Link, LinkType, Project

    with app.state.sessionmaker() as db:
        project = db.query(Project).filter_by(name="gateway").one()
        link = Link(user_id=project.user_id, project_id=project.id, url="https://wiki.corp/secret", type=LinkType.INTERNAL, note="secret plan", sensitive=True)
        db.add(link)
        db.commit()
        link_id = link.id
    found = items(ready)
    assert found["excluded_sensitive"] == 1 and all("secret" not in i["label"] for i in found["items"])
    response = ready.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": [f"link:{link_id}"]})
    assert response.status_code == 409


def test_drafts_must_cite_material_that_was_actually_sent(ready):
    only_requirements = review(ready, ["req:v1"])
    assert [d["source_ref"] for d in only_requirements["drafts"]] == ["REQ:1"]
    assert only_requirements["dropped_drafts"] == 1

    everything = review(ready, ["project", "req:v1", "history"])
    refs = [d["source_ref"] for d in everything["drafts"]]
    assert refs[0] == "REQ:1" and re.fullmatch(r"F:[0-9a-f]{8}", refs[1]) and "F:deadbeef" not in refs
    assert everything["dropped_drafts"] == 1 and len(everything["questions"]) == 3


def test_accepted_drafts_and_open_questions_join_the_checklist(ready):
    r = review(ready, ["project", "req:v1", "history"])
    q = r["questions"]
    ready.post(f"/api/v1/reviews/{r['id']}/answers", json={
        "answers": [
            {"question_id": q[0]["id"], "status": "answered", "answer": "3 attempts, 2s backoff, 10s total"},
            {"question_id": q[1]["id"], "status": "not relevant"},
            {"question_id": q[2]["id"], "status": "deferred"},
        ],
        "accepted_drafts": [0, 1],
    })
    checklist = ready.post("/api/v1/projects/gateway/start", json={}).json()["checklist"]
    by_text = {i["text"]: i for i in checklist["items"]}

    gap = by_text["Define behavior when the provider returns 429"]
    assert gap["source_type"] == "gap" and "(REQ:1)" in gap["why"] and not gap["required"]
    deadline = by_text["Every provider call has a total deadline across retries"]
    assert deadline["source_type"] == "failure" and deadline["required"], "cites a high-severity failure"
    assert "No answer yet: What happens to requests when the cache is down?" in by_text
    assert not any("max attempts" in t or "atomic" in t for t in by_text)
    assert TIMEOUT_CHECK in by_text


def test_unaccepted_drafts_are_not_saved(ready):
    r = review(ready, ["req:v1", "history"])
    ready.post(f"/api/v1/reviews/{r['id']}/answers", json={"answers": [], "accepted_drafts": []})
    texts = [i["text"] for i in ready.post("/api/v1/projects/gateway/start", json={}).json()["checklist"]["items"]]
    assert "Define behavior when the provider returns 429" not in texts


def test_answered_questions_need_an_answer(ready):
    r = review(ready, ["req:v1"])
    response = ready.post(f"/api/v1/reviews/{r['id']}/answers", json={
        "answers": [{"question_id": r["questions"][0]["id"], "status": "answered", "answer": " "}]})
    assert response.status_code == 409


def test_invalid_json_is_retried_once_then_reported(app, ready, ai):
    good = '{"questions": [], "gaps": [], "checklist_items": []}'
    ai.replies = ["not json", good]
    assert review(ready, ["req:v1"])["questions"] == []
    ai.replies = ["nope", "still nope"]
    response = ready.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    assert response.status_code == 503 and "format twice" in response.json()["detail"]
    with app.state.sessionmaker() as db:
        errors = [c.error for c in db.query(AiCall).order_by(AiCall.created_at)]
    assert errors == ["", "The model's reply did not match the expected format twice."]


def test_without_ai_the_review_fails_clearly_and_the_rule_checklist_still_works(app, ready):
    app.state.ai = None
    assert items(ready)["ai_available"] is False
    response = ready.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    assert response.status_code == 503 and "PREFLIGHT_ANTHROPIC_API_KEY" in response.json()["detail"]
    texts = [i["text"] for i in ready.post("/api/v1/projects/gateway/start", json={}).json()["checklist"]["items"]]
    assert texts == [TIMEOUT_CHECK]


def test_monthly_budget_stops_further_calls(app, ready):
    app.state.settings.ai_monthly_budget_usd = 0.005
    review(ready, ["req:v1"])
    response = ready.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    assert response.status_code == 503 and "budget" in response.json()["detail"]


def test_failure_classification_is_only_sent_when_approved(app, ready, ai):
    failure = ready.get("/api/v1/failures").json()[0]
    assert ready.post(f"/api/v1/failures/{failure['id']}/suggest", json={"approved": False}).json() == {"sent": False}
    assert ai.calls == []
    suggestion = ready.post(f"/api/v1/failures/{failure['id']}/suggest", json={"approved": True}).json()
    assert suggestion["category"] == "missed edge case" and ai.calls[-1]["model"] == "claude-haiku-5-5"
    assert "Retry loop hung 40 minutes" in sent_text(ai) and TIMEOUT_CHECK not in sent_text(ai)


def test_strict_schema_inlines_refs_and_forbids_extra_keys():
    schema = strict_schema(ReviewOutput)
    assert "$defs" not in json.dumps(schema)
    question = schema["properties"]["questions"]["items"]
    assert question["additionalProperties"] is False and question["required"] == ["category", "question"]
    assert "missed edge case" in question["properties"]["category"]["enum"]


def test_only_the_client_module_imports_the_sdk_and_only_the_gate_calls_it():
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "preflight"
    importers = sorted(str(p.relative_to(src)) for p in src.rglob("*.py") if re.search(r"^\s*(import|from) anthropic\b", p.read_text(), re.MULTILINE))
    callers = sorted(str(p.relative_to(src)) for p in src.rglob("*.py") if ".complete(" in p.read_text())
    assert importers == ["ai/client.py"]
    assert callers == ["ai/gate.py"]


@pytest.fixture
def fake_api():
    server = FakeAnthropic()
    yield server
    server.close()


def test_anthropic_client_sends_schema_cache_and_fallbacks(fake_api):
    client = AnthropicClient("sk-test", fake_api.url)
    schema = strict_schema(ReviewOutput)
    content = [{"type": "text", "text": "REQ:1 hello", "cache_control": {"type": "ephemeral"}}]
    completion = client.complete(model="claude-opus-5-5", system="sys", content=content, schema=schema, max_tokens=100)
    sent = fake_api.requests[-1]
    assert sent["path"].startswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in sent["headers"]["anthropic-beta"]
    assert sent["body"]["fallbacks"] == "default"
    assert sent["body"]["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert sent["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert ReviewOutput.model_validate_json(completion.text).questions
    assert (completion.input_tokens, completion.output_tokens) == (1200, 300)

    client.complete(model="claude-haiku-5-5", system="sys", content=content, schema=schema, max_tokens=100)
    assert "fallbacks" not in fake_api.requests[-1]["body"]


def test_anthropic_client_reports_refusals(fake_api):
    fake_api.next_stop_reason = "refusal"
    with pytest.raises(AIError, match="declined"):
        AnthropicClient("sk-test", fake_api.url).complete(
            model="claude-opus-5-5", system="s", content=[{"type": "text", "text": "x"}], schema=strict_schema(ReviewOutput), max_tokens=10
        )
