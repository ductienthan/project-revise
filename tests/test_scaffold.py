from click.testing import CliRunner
from fastapi.testclient import TestClient

from preflight.ai.gate import GateItem, build_payload
from preflight.cli.main import cli
from preflight.server.auth.passwords import hash_password, verify_password
from preflight.server.main import app


def test_healthz_and_dashboard_serve():
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "Preflight" in client.get("/").text
    assert client.get("/api/v1/projects").json() == []


def test_cli_routes_project_first():
    result = CliRunner().invoke(cli, ["my-project", "fail", "off by one"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "my-project fail off by one"


def test_cli_rejects_reserved_project_name():
    result = CliRunner().invoke(cli, ["new", "drill"])
    assert result.exit_code != 0
    assert "reserved" in result.output


def test_gate_never_sends_sensitive_or_unapproved_items():
    items = [GateItem("a", "req", "x"), GateItem("b", "req", "y", sensitive=True), GateItem("c", "req", "z")]
    assert [i.id for i in build_payload(items, {"a", "b"})] == ["a"]


def test_password_hash_roundtrip():
    h = hash_password("hunter2")
    assert verify_password(h, "hunter2")
    assert not verify_password(h, "wrong")
