import re

import pyotp
import pytest
from fastapi.testclient import TestClient

from preflight.config import Settings
from preflight.db.base import Base
from preflight.db.models import AuditEvent, User
from preflight.server.auth import service
from preflight.server.main import create_app
from tests.conftest import PASSWORD


def login(client, password=PASSWORD, **extra):
    return client.post("/api/v1/auth/login", json={"username": "me", "password": password, **extra})


def page_csrf(client, path):
    html = client.get(path).text
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def test_login_sets_httponly_lax_session_cookie(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "preflight_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_wrong_password_is_rejected(client):
    assert login(client, "nope").status_code == 401


def test_lockout_after_repeated_failures_blocks_correct_password_until_it_expires(client, clock):
    for _ in range(5):
        assert login(client, "nope").status_code == 401
    assert login(client).status_code == 423
    clock.advance(minutes=16)
    assert login(client).status_code == 200


def test_cookie_requests_need_csrf_header_for_writes(client):
    login(client)
    assert client.get("/api/v1/auth/devices").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 403


def test_session_expires_after_inactivity(web, clock):
    clock.advance(hours=13)
    assert web.get("/api/v1/auth/devices").status_code == 401


def test_logout_ends_the_session(web):
    assert web.post("/api/v1/auth/logout").status_code == 200
    assert web.get("/api/v1/auth/devices").status_code == 401


def test_dashboard_redirects_to_login_without_session(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login?next=/"


def test_login_form_rejects_mismatched_csrf(client):
    client.get("/login")
    response = client.post("/login", data={"username": "me", "password": PASSWORD, "csrf_token": "forged"})
    assert response.status_code == 403


def test_login_form_signs_in_and_ignores_offsite_next(client):
    token = page_csrf(client, "/login")
    response = client.post(
        "/login",
        data={"username": "me", "password": PASSWORD, "csrf_token": token, "next": "//evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_page_forms_require_session_csrf(web):
    response = web.post("/device", data={"code": "XXXX-XXXX"}, headers={"X-CSRF-Token": ""})
    assert response.status_code == 403


def test_device_flow_issues_a_token_only_after_dashboard_approval(app, web, clock):
    cli = TestClient(app)
    grant = cli.post("/api/v1/auth/device/code", json={"device_name": "laptop"}).json()
    poll = lambda: cli.post("/api/v1/auth/device/token", json={"device_code": grant["device_code"]})

    assert poll().json()["detail"] == "authorization_pending"
    assert poll().json()["detail"] == "slow_down"

    approved = web.post("/device", data={"code": grant["user_code"].lower().replace("-", "")})
    assert "Connected" in approved.text

    clock.advance(seconds=6)
    tokens = poll()
    assert tokens.status_code == 200
    cli.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
    assert [d["name"] for d in cli.get("/api/v1/auth/devices").json()] == ["laptop"]

    clock.advance(seconds=6)
    assert poll().json()["detail"] == "invalid_grant"


def test_device_code_expires(app, web, clock):
    cli = TestClient(app)
    grant = cli.post("/api/v1/auth/device/code", json={}).json()
    clock.advance(minutes=11)
    assert "unknown or expired" in web.post("/device", data={"code": grant["user_code"]}).text
    assert cli.post("/api/v1/auth/device/token", json={"device_code": grant["device_code"]}).json()["detail"] == "expired_token"


def test_bearer_token_cannot_open_dashboard(api):
    assert api.get("/", follow_redirects=False).status_code == 303


def test_bearer_requests_do_not_need_csrf(api):
    devices = api.get("/api/v1/auth/devices").json()
    assert api.delete(f"/api/v1/auth/devices/{devices[0]['id']}").status_code == 200


def test_revoking_a_device_kills_its_token_but_not_the_web_session(api, web):
    device_id = api.get("/api/v1/auth/devices").json()[0]["id"]
    assert web.delete(f"/api/v1/auth/devices/{device_id}").status_code == 200
    assert api.get("/api/v1/auth/devices").status_code == 401
    assert web.get("/api/v1/auth/devices").status_code == 200


def test_access_token_expires_and_refresh_rotates(app, clock):
    cli = TestClient(app)
    tokens = cli.post("/api/v1/auth/token", json={"username": "me", "password": PASSWORD}).json()
    clock.advance(minutes=61)
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert cli.get("/api/v1/auth/devices", headers=auth).status_code == 401

    fresh = cli.post("/api/v1/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert fresh.status_code == 200
    auth = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
    assert cli.get("/api/v1/auth/devices", headers=auth).status_code == 200
    assert cli.post("/api/v1/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_password_is_not_an_api_token(client):
    assert client.get("/api/v1/auth/devices", headers={"Authorization": f"Bearer {PASSWORD}"}).status_code == 401


def test_totp_is_required_once_enabled(app, client):
    with app.state.sessionmaker() as db:
        secret = service.enable_totp(db, db.query(User).one())
        db.commit()
    assert login(client).status_code == 401
    assert login(client, totp="000000").status_code == 401
    assert login(client, totp=pyotp.TOTP(secret).at(app.state.clock())).status_code == 200


def test_audit_log_records_logins_and_tokens(app, api, client):
    login(client, "nope")
    with app.state.sessionmaker() as db:
        events = {e.event for e in db.query(AuditEvent)}
    assert {"user_created", "token_created", "login_failed"} <= events


def test_password_hash_is_argon2(app):
    with app.state.sessionmaker() as db:
        assert db.query(User).one().password_hash.startswith("$argon2")


def test_short_passwords_are_refused(app):
    with app.state.sessionmaker() as db, pytest.raises(service.AuthError):
        service.create_user(db, "other", "short")


def test_security_headers(client):
    headers = client.get("/healthz").headers
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_https_is_enforced_when_configured():
    app = create_app(Settings(database_url="sqlite://", require_https=True, _env_file=None))
    client = TestClient(app)
    assert client.get("/healthz", follow_redirects=False).status_code == 308
    assert client.post("/api/v1/auth/login", json={}).status_code == 403
    assert client.get("/healthz", headers={"x-forwarded-proto": "https"}).status_code == 200


def test_login_endpoint_is_rate_limited(clock):
    app = create_app(Settings(database_url="sqlite://", rate_limit_per_minute=3, _env_file=None), clock=clock)
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    codes = [client.post("/api/v1/auth/login", json={"username": "x", "password": "y"}).status_code for _ in range(4)]
    assert codes[-1] == 429
