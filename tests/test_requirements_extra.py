import httpx

from preflight.capture.fetch import Fetcher
from preflight.db.base import Base
from tests.test_capture import new_project


def test_no_table_can_store_an_api_key():
    columns = {f"{t.name}.{c.name}" for t in Base.metadata.tables.values() for c in t.columns}
    assert not [c for c in columns if "api_key" in c or "anthropic" in c], "the API key is read from the environment only"


def test_an_unreachable_link_gives_a_clear_message(app, api):
    def down(request):
        raise httpx.ConnectError("refused")

    app.state.fetcher = Fetcher(transport=httpx.MockTransport(down), resolve=lambda h: ["93.184.216.34"], sleep=lambda s: None)
    new_project(api, "gateway")
    captured = api.post("/api/v1/projects/gateway/links", json={"url": "https://docs.example.com/limits", "capture": True}).json()
    assert captured["capture_error"] == "Could not reach docs.example.com: ConnectError."
    assert captured["link"]["url"] == "https://docs.example.com/limits", "the link is still saved"


def test_a_missing_ai_key_gives_a_clear_message(app, api):
    app.state.ai = None
    new_project(api, "gateway", requirements="Retries.")
    response = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    assert response.status_code == 503 and "PREFLIGHT_ANTHROPIC_API_KEY" in response.json()["detail"]


def test_the_gate_itself_never_offers_or_sends_sensitive_items():
    from preflight.ai.gate import GateItem, build_payload, offerable
    from preflight.core.errors import DomainError

    items = [GateItem("a", "link_note", "A", "x"), GateItem("b", "link_note", "B", "y", sensitive=True)]
    assert [i.id for i in offerable(items)] == ["a"]
    try:
        build_payload(items, {"a", "b"})
    except DomainError as err:
        assert "b" in str(err)
    else:
        raise AssertionError("approving a sensitive item must be refused")


def test_the_gate_counts_an_item_resolved_only_with_its_reason_and_date():
    from datetime import date
    from types import SimpleNamespace

    from preflight.core.lifecycle import is_resolved
    from preflight.db.models import ItemStatus

    def item(status, reason="", revisit=None):
        return SimpleNamespace(status=status, reason=reason, revisit_date=revisit)

    assert is_resolved(item(ItemStatus.DONE))
    assert not is_resolved(item(ItemStatus.NOT_APPLICABLE)) and is_resolved(item(ItemStatus.NOT_APPLICABLE, "no cache"))
    assert not is_resolved(item(ItemStatus.DEFERRED, "later")) and is_resolved(item(ItemStatus.DEFERRED, "later", date(2026, 12, 1)))
    assert not is_resolved(item(ItemStatus.NOT_CHECKED))
