import ipaddress
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from preflight.core.errors import DomainError

USER_AGENT = "PreflightBot/0.1 (+personal reference capture)"
LOGIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|sso|auth|oauth|account/login)\b", re.IGNORECASE)
MAX_REDIRECTS = 5
MAX_BYTES = 5_000_000


class CaptureError(DomainError):
    pass


class LoginRequired(CaptureError):
    pass


class NeedsJavaScript(CaptureError):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    html: str


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


class Fetcher:
    """Fetches public pages politely: robots rules, one request per host per interval, no private addresses."""

    def __init__(
        self,
        allow_private: bool = False,
        min_interval: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        resolve: Callable[[str], list[str]] = _resolve,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.allow_private = allow_private
        self.min_interval = min_interval
        self.resolve = resolve
        self.clock = clock
        self.sleep = sleep
        self.http = httpx.Client(timeout=15, transport=transport, headers={"User-Agent": USER_AGENT})
        self._robots: dict[str, RobotFileParser] = {}
        self._last_hit: dict[str, float] = {}

    def _check_address(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise CaptureError("Only http and https links can be captured.")
        if self.allow_private:
            return
        try:
            addresses = self.resolve(parsed.hostname)
        except OSError:
            raise CaptureError(f"Could not resolve {parsed.hostname}.") from None
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise CaptureError(f"{parsed.hostname} points at a private address, so it is not fetched.")

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_hit[host] = self.clock()

    def _get(self, url: str) -> httpx.Response:
        host = urlparse(url).netloc
        self._wait_turn(host)
        try:
            return self.http.get(url, follow_redirects=False)
        except httpx.HTTPError as err:
            raise CaptureError(f"Could not reach {host}: {err.__class__.__name__}.") from None

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            parser = RobotFileParser()
            response = self._get(f"{root}/robots.txt")
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            self._robots[root] = parser
        return self._robots[root].can_fetch(USER_AGENT, url)

    def fetch(self, url: str) -> Page:
        for hop in range(MAX_REDIRECTS + 1):
            self._check_address(url)
            if not self._allowed(url):
                raise CaptureError("The site's robots rules do not allow fetching this page.")
            if hop and LOGIN_PATH.search(urlparse(url).path):
                raise LoginRequired("This page needs a login, so it was saved as a reference only.")
            response = self._get(url)
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            if response.status_code in (401, 403):
                raise LoginRequired("This page needs a login, so it was saved as a reference only.")
            if response.status_code >= 400:
                raise CaptureError(f"The page returned HTTP {response.status_code}.")
            if "html" not in response.headers.get("content-type", "text/html"):
                raise CaptureError("Only HTML pages can be captured.")
            return Page(url, response.status_code, response.content[:MAX_BYTES].decode(response.encoding or "utf-8", "replace"))
        raise CaptureError("Too many redirects.")
