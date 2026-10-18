import httpx
import pytest

from preflight.capture.fetch import Fetcher
from preflight.db.models import Snapshot
from tests.test_capture import new_project

ARTICLE = """<html><head><title>Rate limits | Example API</title></head><body><nav>menu</nav><article><h1>Rate limits</h1>
<p>Requests are limited per minute per key. Exceeding the limit returns HTTP 429 with a retry-after header that tells you
how long to wait before the next attempt.</p><h2>Tiers</h2><table><tr><th>Tier</th><th>RPM</th></tr><tr><td>Free</td>
<td>50</td></tr><tr><td>Pro</td><td>1000</td></tr></table><p>Burst traffic above the tier limit is rejected immediately
rather than queued, so clients should back off exponentially and cap retries.</p></article></body></html>"""
JS_SHELL = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
LOGIN_FORM = '<html><body><form><input name="u"><input type="password" name="p"></form></body></html>'
ADDRESSES = {"docs.example.com": "93.184.216.34", "sso.example.com": "93.184.216.35", "wiki.corp": "10.0.0.5",
             "169.254.169.254": "169.254.169.254"}


class Web:
    def __init__(self):
        self.requests: list[str] = []
        self.sleeps: list[float] = []
        self.pages = {
            "https://docs.example.com/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private/\n"),
            "https://docs.example.com/rate-limits": httpx.Response(200, html=ARTICLE),
            "https://docs.example.com/docs/auth": httpx.Response(200, html=ARTICLE),
            "https://docs.example.com/private/plan": httpx.Response(200, html=ARTICLE),
            "https://docs.example.com/wiki": httpx.Response(302, headers={"location": "https://sso.example.com/login?next=/wiki"}),
            "https://docs.example.com/members": httpx.Response(401),
            "https://docs.example.com/form": httpx.Response(200, html=LOGIN_FORM),
            "https://docs.example.com/app": httpx.Response(200, html=JS_SHELL),
            "https://docs.example.com/huge": httpx.Response(200, html=ARTICLE.replace("</article>", "<p>" + "word " * 60_000 + "</p></article>")),
            "https://docs.example.com/leak": httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"}),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = f"{request.url.scheme}://{request.headers['host']}{request.url.raw_path.decode()}"
        self.requests.append(url)
        return self.pages.get(url, httpx.Response(404))

    def fetcher(self) -> Fetcher:
        return Fetcher(transport=httpx.MockTransport(self.handler), resolve=lambda h: [ADDRESSES[h]],
                       clock=lambda: 0.0, sleep=self.sleeps.append)


@pytest.fixture
def web(app):
    fake = Web()
    app.state.fetcher = fake.fetcher()
    return fake


def add(api, url, **body):
    response = api.post("/api/v1/projects/gateway/links", json={"url": url, **body})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def project(api):
    new_project(api, "gateway", requirements="Retries on provider errors.")
    return api


def test_public_capture_keeps_headings_and_tables_and_waits_for_approval(project, web):
    captured = add(project, "https://docs.example.com/rate-limits", capture=True, note="limits per key")
    snapshot = captured["snapshot"]
    assert captured["link"]["title"] == "Rate limits"
    assert snapshot["content"].startswith("# Rate limits") and "## Tiers" in snapshot["content"]
    assert "| Pro | 1000 |" in snapshot["content"] and "menu" not in snapshot["content"]
    assert snapshot["version"] == 1 and not snapshot["approved"]

    offered = lambda: [i["id"] for i in project.get("/api/v1/projects/gateway/review-items").json()["items"]]
    assert not any(i.startswith("snapshot:") for i in offered()), "unapproved snapshots are never offered"
    project.patch(f"/api/v1/snapshots/{snapshot['id']}", json={"approved": True})
    assert f"snapshot:{snapshot['id']}" in offered()


def test_refresh_adds_a_version_and_keeps_the_old_one(project, web):
    link = add(project, "https://docs.example.com/rate-limits", capture=True)["link"]
    again = project.post(f"/api/v1/links/{link['id']}/refresh").json()
    assert [s["version"] for s in again["link"]["snapshots"]] == [1, 2]


def test_internal_links_are_never_fetched(project, web):
    link = add(project, "https://wiki.corp/gateway-spec", internal=True, capture=True, note="Latency target 800ms p95")
    assert link["snapshot"] is None and web.requests == []
    assert project.post(f"/api/v1/links/{link['link']['id']}/capture").status_code == 409
    assert web.requests == []
    items = project.get("/api/v1/projects/gateway/review-items").json()["items"]
    assert any(i["label"] == "Note on internal link: https://wiki.corp/gateway-spec" for i in items)


@pytest.mark.parametrize("path", ["wiki", "members", "form"])
def test_pages_behind_a_login_are_skipped_and_kept_as_references(project, web, path):
    captured = add(project, f"https://docs.example.com/{path}", capture=True)
    assert captured["snapshot"] is None and "needs a login" in captured["capture_error"]
    assert project.get("/api/v1/projects/gateway/links").json()[0]["url"].endswith(path)


def test_an_auth_word_in_a_docs_path_is_not_a_login_wall(project, web):
    assert add(project, "https://docs.example.com/docs/auth", capture=True)["snapshot"] is not None


def test_robots_rules_are_respected(project, web):
    captured = add(project, "https://docs.example.com/private/plan", capture=True)
    assert "robots" in captured["capture_error"]
    assert "https://docs.example.com/private/plan" not in web.requests


def test_javascript_pages_point_to_the_browser_extra(project, web):
    assert "preflight[browser]" in add(project, "https://docs.example.com/app", capture=True)["capture_error"]


def test_private_addresses_are_refused_even_through_a_redirect(project, web):
    direct = add(project, "https://wiki.corp/page", capture=True)
    assert "private address" in direct["capture_error"]
    redirected = add(project, "https://docs.example.com/leak", capture=True)
    assert "private address" in redirected["capture_error"]
    assert not any("169.254" in r or "wiki.corp" in r for r in web.requests)


def test_capture_waits_between_requests_to_one_host(project, web):
    add(project, "https://docs.example.com/rate-limits", capture=True)
    add(project, "https://docs.example.com/docs/auth", capture=True)
    assert web.sleeps and all(s == pytest.approx(1.0) for s in web.sleeps)


def test_large_snapshots_are_capped_and_flagged(project, web):
    snapshot = add(project, "https://docs.example.com/huge", capture=True)["snapshot"]
    assert snapshot["truncated"] and len(snapshot["content"]) == 200_000


def test_snapshots_can_be_edited_and_deleted(project, web):
    snapshot = add(project, "https://docs.example.com/rate-limits", capture=True)["snapshot"]
    edited = project.patch(f"/api/v1/snapshots/{snapshot['id']}", json={"content": "# Rate limits\n\nOnly the part I need."}).json()
    assert edited["content"].endswith("Only the part I need.")
    assert project.delete(f"/api/v1/snapshots/{snapshot['id']}").status_code == 200
    assert project.get(f"/api/v1/snapshots/{snapshot['id']}").status_code == 404


def test_deleting_a_project_deletes_its_snapshots(app, project, web):
    add(project, "https://docs.example.com/rate-limits", capture=True)
    project.delete("/api/v1/projects/gateway")
    with app.state.sessionmaker() as db:
        assert db.query(Snapshot).count() == 0


def test_other_users_cannot_read_my_snapshots(project, web, other):
    snapshot = add(project, "https://docs.example.com/rate-limits", capture=True)["snapshot"]
    assert other.get(f"/api/v1/snapshots/{snapshot['id']}").status_code == 404


def test_links_need_a_full_url(project):
    assert project.post("/api/v1/projects/gateway/links", json={"url": "javascript:alert(1)"}).status_code == 409
