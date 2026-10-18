import json

import httpx
import pytest

from preflight.ai.client import AIError
from preflight.ai.gate import GateItem
from preflight.ai.ollama import OllamaClient
from preflight.ai.schemas import ClassifyOutput, strict_schema
from preflight.cli.importer import parse
from preflight.core.errors import DomainError
from preflight.core.review import plan_batches
from preflight.db.models import AiCall
from tests.test_capture import fail, new_project


def test_a_tag_typed_into_requirements_does_not_open_unapproved_history(api, ai):
    new_project(api, "old")
    secret = fail(api, "old", "Secret outage", severity="high", prevention="Never sent")["failure"]
    tag = f"F:{secret['id'][:8]}"
    new_project(api, "gateway", requirements=f"Like {tag} maybe.")
    ai.replies = [json.dumps({"questions": [], "gaps": [], "checklist_items": [
        {"text": "Cite unseen history", "why": "x", "source_ref": tag, "priority": "high"}]})]
    review = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).json()
    assert review["drafts"] == [] and review["dropped_drafts"] == 1


def test_a_requirement_tag_counts_only_for_lines_in_that_call(api, ai):
    ai.context_tokens = 6_000
    lines = ["See REQ:300 for the hard part."] + [f"Requirement {n}: handle provider case {n} with retries." for n in range(2, 301)]
    new_project(api, "gateway", requirements="\n".join(lines))
    ai.replies = [json.dumps({"questions": [], "gaps": [{"text": "x", "why": "y", "source_ref": "REQ:300"}], "checklist_items": []})]
    review = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).json()
    assert len(ai.calls) > 1
    assert "REQ:300" not in [d["source_ref"] for d in review["drafts"]], "the first call only mentioned REQ:300 in text"


def test_ollama_refuses_input_longer_than_its_context_instead_of_truncating():
    sent = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m", "size": 1}]})
        sent.append(json.loads(request.content))
        detail = {"code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 1508, "n_ctx": 512,
                  "message": "request (1508 tokens) exceeds the available context size (512 tokens)"}
        return httpx.Response(400, json={"error": json.dumps({"error": detail})})

    ollama = OllamaClient("http://x", "m", None, 512, transport=httpx.MockTransport(handler))
    with pytest.raises(AIError, match="1508 tokens.*512"):
        ollama.complete(model="m", system="s", content=[{"type": "text", "text": "x"}], schema=strict_schema(ClassifyOutput), max_tokens=64)
    assert sent[0]["truncate"] is False


def test_review_items_still_load_when_history_fills_the_context(app, api, ai):
    ai.context_tokens = 4_000
    new_project(api, "old")
    for n in range(40):
        fail(api, "old", f"Failure {n} " + "long detail " * 60, prevention="check " * 40)
    new_project(api, "gateway", requirements="Retries.")
    found = api.get("/api/v1/projects/gateway/review-items")
    assert found.status_code == 200
    assert found.json()["calls"] == 0 and "fill the model's context" in found.json()["warning"]


def test_fixed_items_alone_over_budget_are_refused():
    huge = GateItem("history", "history", "History", "x" * 400_000)
    with pytest.raises(DomainError, match="fill the model's context"):
        plan_batches([huge], budget_tokens=1_000)


def test_retrying_a_failed_multi_call_review_does_not_pay_for_finished_calls_again(app, api, ai):
    ai.context_tokens = 6_000
    text = "\n".join(f"Requirement {n}: the gateway handles provider case {n} with retries and budgets." for n in range(1, 400))
    new_project(api, "gateway", requirements=text)
    good = '{"questions": [], "gaps": [], "checklist_items": []}'
    ai.replies = [good, "bad", "bad"]
    assert api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).status_code == 503
    finished = ai.calls[0]["content"]
    first_round = len(ai.calls)
    ai.replies = []
    assert api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).status_code == 201
    assert all(call["content"] != finished for call in ai.calls[first_round:]), "the finished first call is reused, not sent again"
    with app.state.sessionmaker() as db:
        paid = [c for c in db.query(AiCall) if c.error == ""]
    assert len(paid) == len({c.payload for c in paid}), "no successful payload was paid for twice"

    resent = len(ai.calls)
    assert api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).status_code == 201
    assert ai.calls[resent]["content"] == finished, "a deliberate re-review asks again"


def test_one_bad_json_row_does_not_abort_the_import():
    rows = json.dumps([
        {"project": "a", "title": "ok", "date": "2026-01-02", "category": " missed edge case ", "severity": " high "},
        {"project": "a", "title": "bad", "date": "2026-01-03", "category": ["not", "a", "string"]},
    ])
    parsed = parse(rows, "x.json")
    assert [r.body["title"] for r in parsed.rows] == ["ok"] and parsed.rows[0].body["severity"] == "high"
    assert parsed.errors and parsed.errors[0].startswith("row 2:")
