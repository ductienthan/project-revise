import importlib.util
import json

import pytest

from preflight.capture import browser
from preflight.capture.fetch import CaptureError, Fetcher
from tests.fakes import FixtureSite

pytestmark = pytest.mark.skipif(importlib.util.find_spec("playwright") is None, reason="browser extra not installed")

TEXT = ("Rate limits apply per key. Exceeding the limit returns HTTP 429 with a retry-after header. "
        "Clients should back off exponentially and cap retries so a burst does not lock them out. ") * 3
JS_PAGE = """<html><head><title>Limits</title></head><body><article id="root"></article><script>
fetch('/data').then(r => r.json()).then(d => {
  document.getElementById('root').innerHTML = '<h1>Rate limits</h1><p>' + d.text + '</p>';
});
fetch('/secret').then(r => r.text()).then(t => {
  const p = document.createElement('p'); p.textContent = 'LEAKED ' + t; document.body.appendChild(p);
}).catch(() => {});
</script></body></html>"""


@pytest.fixture
def site():
    s = FixtureSite({
        "/robots.txt": (200, {"content-type": "text/plain"}, ""),
        "/app": (200, {}, JS_PAGE),
        "/data": (200, {"content-type": "application/json"}, json.dumps({"text": TEXT.strip()})),
        "/secret": (200, {"content-type": "text/plain"}, "metadata-credentials"),
    })
    yield s
    s.close()


def test_the_browser_renders_javascript_and_every_request_passes_the_guard(site):
    def allowed(url: str) -> str:
        if url.endswith("/secret"):
            raise CaptureError("private")
        return "127.0.0.1"

    html = browser.render(f"{site.url}/app", allowed)
    assert "Rate limits apply per key" in html
    assert "metadata-credentials" not in html and "/secret" not in site.hits


def test_capture_uses_the_browser_only_when_enabled(app, api, site):
    app.state.fetcher = Fetcher(allow_private=True, min_interval=0)
    api.post("/api/v1/projects", json={"name": "gateway"})
    off = api.post("/api/v1/projects/gateway/links", json={"url": f"{site.url}/app", "capture": True}).json()
    assert "PREFLIGHT_CAPTURE_BROWSER" in off["capture_error"]
    app.state.settings.capture_browser = True
    link_id = off["link"]["id"]
    on = api.post(f"/api/v1/links/{link_id}/capture").json()
    assert on["capture_error"] is None and "Rate limits apply per key" in on["snapshot"]["content"]
