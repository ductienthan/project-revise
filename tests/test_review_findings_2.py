import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from preflight.capture.fetch import CaptureError, Fetcher
from preflight.config import Settings
from preflight.db.base import Base
from preflight.db.models import AiCall
from preflight.server.auth import service
from preflight.server.main import create_app
from tests.conftest import PASSWORD, token_client
from tests.fakes import FakeAI
from tests.test_links import ARTICLE


def site(pages: dict[str, httpx.Response], seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("host"), request.extensions.get("sni_hostname")))
        return pages.get(request.url.path, httpx.Response(404))
    return httpx.MockTransport(handler)


def test_capture_connects_to_the_address_it_checked():
    seen = []
    pages = {"/robots.txt": httpx.Response(404), "/page": httpx.Response(200, html=ARTICLE)}
    fetcher = Fetcher(transport=site(pages, seen), resolve=lambda h: ["93.184.216.34"], sleep=lambda s: None)
    fetcher.fetch("https://docs.example.com/page")
    assert {url.split("/")[2] for url, _, _ in seen} == {"93.184.216.34"}, "the transport never resolves the name again"
    assert {host for _, host, _ in seen} == {"docs.example.com"}
    assert {sni for _, _, sni in seen} == {"docs.example.com"}, "TLS still verifies the real hostname"


def test_a_rebinding_answer_is_refused_before_connecting():
    answers = iter(["93.184.216.34", "127.0.0.1"])
    seen = []
    pages = {"/robots.txt": httpx.Response(404), "/page": httpx.Response(200, html=ARTICLE)}
    fetcher = Fetcher(transport=site(pages, seen), resolve=lambda h: [next(answers)], sleep=lambda s: None)
    with pytest.raises(CaptureError, match="private address"):
        fetcher.fetch("https://docs.example.com/page")
    assert [url for url, _, _ in seen] == ["https://93.184.216.34/robots.txt"]


def test_shared_address_space_is_not_public():
    fetcher = Fetcher(transport=site({}, []), resolve=lambda h: ["100.64.0.1"])
    with pytest.raises(CaptureError, match="private address"):
        fetcher.fetch("https://cgnat.example.com/")


@pytest.mark.parametrize("robots", [
    httpx.Response(301, headers={"location": "https://docs.example.com/real-robots.txt"}),
    httpx.Response(503),
])
def test_robots_redirects_are_followed_and_server_errors_block(robots):
    seen = []
    pages = {"/robots.txt": robots, "/real-robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
             "/doc": httpx.Response(200, html=ARTICLE)}
    fetcher = Fetcher(transport=site(pages, seen), resolve=lambda h: ["93.184.216.34"], sleep=lambda s: None)
    with pytest.raises(CaptureError, match="robots"):
        fetcher.fetch("https://docs.example.com/doc")
    assert not any(url.endswith("/doc") for url, _, _ in seen)


def test_capture_stops_reading_at_the_size_cap():
    sent = []

    def chunks():
        for _ in range(1000):
            sent.append(1)
            yield b"x" * 100_000

    pages = {"/robots.txt": httpx.Response(404),
             "/big": httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())}
    fetcher = Fetcher(transport=site(pages, []), resolve=lambda h: ["93.184.216.34"], sleep=lambda s: None)
    with pytest.raises(CaptureError, match="too large"):
        fetcher.fetch("https://docs.example.com/big")
    assert len(sent) <= 51, "reading stopped near 5 MB instead of draining 100 MB"


def test_capture_has_a_total_deadline():
    now = [0.0]

    def slow():
        for _ in range(100):
            now[0] += 10
            yield b"<p>slow</p>"

    pages = {"/robots.txt": httpx.Response(404),
             "/slow": httpx.Response(200, headers={"content-type": "text/html"}, content=slow())}
    fetcher = Fetcher(transport=site(pages, []), resolve=lambda h: ["93.184.216.34"], clock=lambda: now[0], sleep=lambda s: None)
    with pytest.raises(CaptureError, match="too long"):
        fetcher.fetch("https://docs.example.com/slow")
    assert now[0] <= 40


def test_the_browser_fallback_is_off_unless_enabled(app, api, monkeypatch):
    from preflight.capture import browser
    from tests.test_links import Web

    monkeypatch.setattr(browser, "render", lambda url, allowed: pytest.fail("browser must not run"))
    app.state.fetcher = Web().fetcher()
    api.post("/api/v1/projects", json={"name": "gateway"})
    captured = api.post("/api/v1/projects/gateway/links", json={"url": "https://docs.example.com/app", "capture": True}).json()
    assert "PREFLIGHT_CAPTURE_BROWSER" in captured["capture_error"]


class SlowAI(FakeAI):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.calls = 0

    def complete(self, **kwargs):
        from preflight.ai.client import Completion

        self.calls += 1
        self.release.wait(5)
        return Completion('{"questions": [], "gaps": [], "checklist_items": []}', kwargs["model"], 10, 10)


def test_concurrent_calls_cannot_overspend_the_budget(tmp_path):
    ai = SlowAI()
    settings = Settings(database_url=f"sqlite:///{tmp_path}/b.db", ai_monthly_budget_usd=0.5, rate_limit_per_minute=1000, _env_file=None)
    app = create_app(settings, ai=ai)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as db:
        service.create_user(db, "me", PASSWORD)
        db.commit()
    api = token_client(app, "me")
    api.post("/api/v1/projects", json={"name": "gateway", "requirements": "Retries."})
    results = []
    first = threading.Thread(target=lambda: results.append(api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]}).status_code))
    first.start()
    while ai.calls == 0:
        pass
    second = TestClient(app, headers=dict(api.headers)).post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    ai.release.set()
    first.join()
    assert results == [201] and second.status_code == 503 and "budget" in second.json()["detail"]
    assert ai.calls == 1
    with app.state.sessionmaker() as db:
        assert db.query(AiCall).one().cost_usd < 0.01, "the reservation is replaced by the real cost"


def test_every_call_is_logged_even_when_the_client_crashes(app, api):
    class Broken(FakeAI):
        def complete(self, **kwargs):
            raise RuntimeError("socket closed")

    app.state.ai = Broken()
    api.post("/api/v1/projects", json={"name": "gateway", "requirements": "Retries."})
    response = api.post("/api/v1/projects/gateway/reviews", json={"approved_item_ids": ["req:v1"]})
    assert response.status_code == 503
    with app.state.sessionmaker() as db:
        call = db.query(AiCall).one()
    assert "REQ:1 Retries." in call.payload and call.error == "AI call failed (RuntimeError)."
