"""The exact review payload for a fixed project. A prompt change shows up here as a reviewable diff.

Regenerate with PREFLIGHT_UPDATE_GOLDEN=1 after an intended change.
"""

import json
import os
from pathlib import Path

from preflight.ai.prompts.review import REVIEW
from tests.test_capture import new_project

GOLDEN = Path(__file__).parent / "golden" / "review_payload.txt"


def test_review_payload_matches_the_golden_file(api, ai):
    new_project(api, "billing")
    api.post("/api/v1/projects/billing/failures", json={
        "id": "1a2b3c4d-0000-4000-8000-000000000001", "title": "Retry loop hung 40 minutes", "category": "missed edge case",
        "severity": "high", "minutes_lost": 40, "what_happened": "Provider stalled", "root_cause": "No total deadline",
        "prevention": "Every external call has a timeout, retry cap, and backoff"})
    api.post("/api/v1/projects/billing/failures", json={
        "id": "5e6f7a8b-0000-4000-8000-000000000002", "title": "Budget counter lost updates", "category": "state or concurrency bug",
        "minutes_lost": 25, "prevention": "Read-then-write on shared state is atomic"})
    new_project(api, "gateway", idea="LLM proxy", goals="p95 under 800ms",
                requirements="Proxy for LLM providers.\nRetries on provider errors.\nPer-user budgets.")
    api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["project", "req:v1", "history"]})
    rendered = f"version: {REVIEW.version}\nsystem:\n{REVIEW.system}\n\n" + "\n\n".join(b["text"] for b in ai.calls[-1]["content"])
    rendered += "\n\nschema:\n" + json.dumps(ai.calls[-1]["schema"], indent=2, sort_keys=True) + "\n"
    if os.environ.get("PREFLIGHT_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(rendered)
    assert rendered == GOLDEN.read_text(), "the review prompt changed; rerun with PREFLIGHT_UPDATE_GOLDEN=1 if intended"
