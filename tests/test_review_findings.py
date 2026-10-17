import json
import os
import stat

import click
import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient

from preflight.cli import credentials, queue
from preflight.cli.client import Client
from preflight.db.models import User
from preflight.server.auth import service
from tests.conftest import PASSWORD
from tests.test_capture import fail, new_project


def test_an_attacker_cannot_lock_the_owner_out_from_another_address(app, client):
    for _ in range(5):
        client.post("/api/v1/auth/token", json={"username": "me", "password": "nope"})
    assert client.post("/api/v1/auth/token", json={"username": "me", "password": PASSWORD}).status_code == 423
    owner = TestClient(app, client=("203.0.113.7", 5000))
    assert owner.post("/api/v1/auth/login", json={"username": "me", "password": PASSWORD}).status_code == 200


def test_locked_and_unknown_users_look_the_same(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope"})
    assert client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 423


def test_checking_an_item_does_not_start_from_a_stale_checklist(api):
    new_project(api, "old")
    fail(api, "old", "First outage", severity="high", prevention="Check A")
    new_project(api, "fresh")
    item = api.post("/api/v1/projects/fresh/start", json={}).json()["checklist"]["items"][0]
    fail(api, "old", "Second outage", severity="high", prevention="Check B")
    patched = api.patch(f"/api/v1/checklist-items/{item['id']}", json={"status": "done"}).json()
    assert patched["project_started"] is False
    checklist = api.get("/api/v1/projects/fresh/checklist").json()
    assert "Check B" in [i["text"] for i in checklist["items"]]
    assert api.get("/api/v1/projects/fresh").json()["status"] == "draft"


def test_totp_codes_cannot_be_replayed(app, client):
    with app.state.sessionmaker() as db:
        secret = service.enable_totp(db, db.query(User).one())
        db.commit()
    code = pyotp.TOTP(secret).at(app.state.clock())
    assert client.post("/api/v1/auth/login", json={"username": "me", "password": PASSWORD, "totp": code}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "me", "password": PASSWORD, "totp": code}).status_code == 401


def test_device_link_keeps_its_code_through_login(client):
    response = client.get("/device?code=ABCD-EFGH", follow_redirects=False)
    assert response.headers["location"] == "/login?next=/device%3Fcode%3DABCD-EFGH"


def _client(handler) -> Client:
    return Client("http://localhost:1", transport=httpx.MockTransport(handler))


def test_cli_keeps_tokens_when_refresh_fails_for_a_server_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PREFLIGHT_NO_KEYRING", "1")
    credentials.save("http://localhost:1", {"access_token": "a", "refresh_token": "r"})

    def handler(request):
        return httpx.Response(503 if request.url.path.endswith("/refresh") else 401, json={"detail": "x"})

    with pytest.raises(click.ClickException):
        _client(handler).get("/projects")
    assert credentials.load("http://localhost:1") == {"access_token": "a", "refresh_token": "r"}


def test_token_file_is_tightened_even_if_it_already_existed(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PREFLIGHT_NO_KEYRING", "1")
    path = tmp_path / "credentials.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    credentials.save("http://localhost:1", {"access_token": "a", "refresh_token": "r"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    os.chmod(path, 0o644)
    credentials.clear("http://localhost:1")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_queue_keeps_entries_on_server_errors_and_drops_only_rejections(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PREFLIGHT_NO_KEYRING", "1")
    credentials.save("http://localhost:1", {"access_token": "a", "refresh_token": "r"})
    queue.enqueue("/projects/p/failures", {"id": "1", "title": "x"})
    queue.flush(_client(lambda r: httpx.Response(503, json={"detail": "down"})))
    assert len(queue.pending()) == 1
    queue.flush(_client(lambda r: httpx.Response(429, json={"detail": "slow"})))
    assert len(queue.pending()) == 1
    queue.flush(_client(lambda r: httpx.Response(404, json={"detail": "No project named 'p'."})))
    assert queue.pending() == []
    rejected = (tmp_path / "queue.rejected.jsonl").read_text().splitlines()
    assert json.loads(rejected[0])["error"] == "No project named 'p'."
