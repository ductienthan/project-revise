import os
from urllib.parse import urlparse

import click
import httpx

from preflight.cli import credentials


class ApiError(click.ClickException):
    def __init__(self, status: int, detail):
        self.status = status
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else str(detail))


class ServerUnreachable(click.ClickException):
    pass


def api_url() -> str:
    return os.environ.get("PREFLIGHT_API_URL", "http://localhost:8000").rstrip("/")


def is_secure(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" or parsed.hostname in ("localhost", "127.0.0.1")


class Client:
    def __init__(self, base_url: str | None = None, timeout: float = 10, transport=None):
        self.base_url = base_url or api_url()
        self.http = httpx.Client(base_url=f"{self.base_url}/api/v1", timeout=timeout, transport=transport)

    def _send(self, method: str, path: str, auth: bool, **kwargs) -> httpx.Response:
        headers = {}
        tokens = credentials.load(self.base_url) if auth else None
        if auth and not tokens:
            raise click.ClickException("Not signed in. Run `preflight login`.")
        if tokens:
            headers["Authorization"] = f"Bearer {tokens['access_token']}"
        try:
            return self.http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            click.echo("Waking the server...", err=True)
            try:
                return self.http.request(method, path, headers=headers, timeout=60, **kwargs)
            except httpx.TransportError as err:
                raise ServerUnreachable(f"Server unreachable at {self.base_url}: {err}") from None
        except httpx.TransportError as err:
            raise ServerUnreachable(f"Server unreachable at {self.base_url}: {err}") from None

    def _refresh(self) -> bool:
        tokens = credentials.load(self.base_url)
        if not tokens:
            return False
        response = self._send("POST", "/auth/token/refresh", auth=False, json={"refresh_token": tokens["refresh_token"]})
        if response.status_code != 200:
            credentials.clear(self.base_url)
            return False
        credentials.save(self.base_url, response.json())
        return True

    def request(self, method: str, path: str, auth: bool = True, **kwargs):
        response = self._send(method, path, auth, **kwargs)
        if response.status_code == 401 and auth and self._refresh():
            response = self._send(method, path, auth, **kwargs)
        if response.status_code == 401 and auth:
            raise click.ClickException("Session expired or revoked. Run `preflight login`.")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, detail)
        if not response.content:
            return None
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)
