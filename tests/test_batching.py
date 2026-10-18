import json

import pytest

from preflight.ai.gate import GateItem
from preflight.core.errors import DomainError
from preflight.core.review import REQ_LINE, plan_batches
from preflight.db.models import AiCall
from tests.test_capture import fail, new_project

HISTORY = GateItem("history", "history", "History", "F:1a2b3c4d [missed edge case] Retry hang. prevention: deadline", cacheable=True)


def requirements(lines: int) -> GateItem:
    text = "\n".join(f"REQ:{n} The gateway must handle case number {n} with retries and a deadline." for n in range(1, lines + 1))
    return GateItem("req:v1", "requirements", f"Requirements v1 ({lines} lines)", text)


def test_small_material_is_one_call():
    batches = plan_batches([HISTORY, requirements(5)], budget_tokens=10_000)
    assert [[i.id for i in b] for b in batches] == [["history", "req:v1"]]


def test_long_documents_split_on_lines_and_every_call_keeps_the_context():
    batches = plan_batches([HISTORY, requirements(400)], budget_tokens=3_000)
    assert len(batches) > 1
    assert all(b[0].id == "history" for b in batches), "history rides in every call"
    assert all(sum(i.tokens for i in b) <= 3_000 for b in batches)
    seen = [ref for b in batches for i in b[1:] for ref in REQ_LINE.findall(i.text)]
    assert seen == [f"REQ:{n}" for n in range(1, 401)], "every line is sent exactly once, in order, with its tag"
    assert batches[1][1].label == f"Requirements v1 (400 lines), part 2 of {len(batches)}"


def test_snapshot_parts_carry_their_citation_tag():
    snap = GateItem("snapshot:abc", "snapshot", "Snapshot", "S:abcdef12\n" + "word " * 20_000, ref="S:abcdef12")
    parts = [b[-1] for b in plan_batches([HISTORY, snap], budget_tokens=3_000)]
    assert len(parts) > 1 and all("S:abcdef12" in p.text for p in parts)


def test_context_too_small_for_the_history_is_refused():
    with pytest.raises(DomainError, match="fill the model's context"):
        plan_batches([HISTORY, requirements(5)], budget_tokens=100)


def test_a_long_review_runs_in_several_calls_and_merges_the_results(app, api, ai):
    ai.context_tokens = 6_000
    new_project(api, "old")
    fail(api, "old", "Retry hang", severity="high", prevention="Every call has a deadline")
    text = "\n".join(f"Requirement {n}: the gateway handles provider case {n} with retries and budgets." for n in range(1, 600))
    new_project(api, "gateway", requirements=text)
    review = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1", "history"]}).json()
    calls = len(ai.calls)
    assert calls > 1
    assert len(review["questions"]) == 3, "the same questions from every call are merged"
    gaps = [d for d in review["drafts"] if d["kind"] == "gap"]
    assert len(gaps) == calls and len({d["source_ref"] for d in gaps}) == calls, "each call cites a line it saw"
    assert review["dropped_drafts"] == calls, "each call's invented reference is dropped"
    with app.state.sessionmaker() as db:
        assert db.query(AiCall).count() == calls
        assert review["cost_usd"] == pytest.approx(round(sum(c.cost_usd for c in db.query(AiCall)), 4))
    for call in ai.calls:
        sent = "\n".join(b["text"] for b in call["content"])
        assert "Retry hang" in sent


def test_a_link_note_can_be_cited_now_that_its_tag_is_in_the_material(app, api, ai):
    new_project(api, "gateway", requirements="Retries.")
    link = api.post("/api/v1/projects/gateway/links", json={"url": "https://wiki.corp/spec", "internal": True, "note": "800ms p95"}).json()["link"]
    tag = f"L:{link['id'][:8]}"
    ai.replies = [json.dumps({"questions": [], "gaps": [{"text": "Latency budget per hop", "why": "p95 target", "source_ref": tag}], "checklist_items": []})]
    review = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": [f"link:{link['id']}"]}).json()
    assert [d["source_ref"] for d in review["drafts"]] == [tag] and review["dropped_drafts"] == 0
