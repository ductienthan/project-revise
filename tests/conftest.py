from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from preflight.config import Settings
from preflight.db.base import Base
from preflight.server.auth import service
from preflight.server.main import create_app

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(clock):
    app = create_app(Settings(database_url="sqlite://", rate_limit_per_minute=1000, _env_file=None), clock=clock)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as db:
        service.create_user(db, "me", PASSWORD)
        db.commit()
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def web(client) -> TestClient:
    """A client signed in to the dashboard, sending its CSRF token on every request."""
    response = client.post("/api/v1/auth/login", json={"username": "me", "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def api(app) -> TestClient:
    """A client holding only a CLI bearer token, with no session cookie."""
    return token_client(app, "me")


def token_client(app, username: str, password: str = PASSWORD) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return client


@pytest.fixture
def other(app) -> TestClient:
    """A second user's client, for checking that data never crosses accounts."""
    with app.state.sessionmaker() as db:
        service.create_user(db, "someone-else", PASSWORD)
        db.commit()
    return token_client(app, "someone-else")
