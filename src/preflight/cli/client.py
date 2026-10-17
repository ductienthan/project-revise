import httpx

from preflight.config import settings


def api() -> httpx.Client:
    return httpx.Client(base_url=f"{settings.api_url}/api/v1", timeout=10)
