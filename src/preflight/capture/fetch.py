import ipaddress
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

from preflight.core.errors import DomainError

USER_AGENT = "PreflightBot/0.1 (+personal reference capture)"
LOGIN_PATH = re.compile(r"/(login|log-in|signin|sign-in|sso|auth|oauth|account/login)\b", re.IGNORECASE)
MAX_REDIRECTS = 5
MAX_BYTES = 5_000_000
DEADLINE_SECONDS = 30.0


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


@dataclass(frozen=True)
class Body:
    status: int
    headers: httpx.Headers
    content: bytes
    encoding: str | None


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class Fetcher:
    """Fetches public pages politely: robots rules, one request per host per interval, public addresses only.

    Each request connects to the exact address that passed the check, so a second DNS answer cannot redirect
    the connection to a private host.
    """

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

    def allowed_address(self, url: str) -> str:
        """The checked address to connect to for this URL. Raises when any answer for the host is not public."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise CaptureError("Only http and https links can be captured.")
        try:
            addresses = self.resolve(parsed.hostname)
        except OSError:
            raise CaptureError(f"Could not resolve {parsed.hostname}.") from None
        if not addresses:
            raise CaptureError(f"Could not resolve {parsed.hostname}.")
        if not self.allow_private and not all(is_public(a) for a in addresses):
            raise CaptureError(f"{parsed.hostname} points at a private address, so it is not fetched.")
        return addresses[0]

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_hit[host] = self.clock()

    def _get(self, url: str, deadline: float) -> Body:
        parsed = urlparse(url)
        address = self.allowed_address(url)
        host = f"[{address}]" if ":" in address else address
        pinned = urlunparse(parsed._replace(netloc=f"{host}:{parsed.port}" if parsed.port else host))
        self._wait_turn(parsed.netloc)
        extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
        request = self.http.build_request("GET", pinned, headers={"Host": parsed.netloc}, extensions=extensions)
        try:
            response = self.http.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as err:
            raise CaptureError(f"Could not reach {parsed.hostname}: {err.__class__.__name__}.") from None
        try:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise CaptureError("The page is too large to capture (over 5 MB).")
                if self.clock() > deadline:
                    raise CaptureError("The page took too long to load.")
                chunks.append(chunk)
        except httpx.HTTPError as err:
            raise CaptureError(f"Could not read {parsed.hostname}: {err.__class__.__name__}.") from None
        finally:
            response.close()
        return Body(response.status_code, response.headers, b"".join(chunks), response.encoding)

    def _robots_for(self, url: str, deadline: float) -> RobotFileParser:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._robots:
            return self._robots[root]
        parser = RobotFileParser()
        target = f"{root}/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            body = self._get(target, deadline)
            if 300 <= body.status < 400 and "location" in body.headers:
                target = urljoin(target, body.headers["location"])
                continue
            if body.status >= 500:
                parser.disallow_all = True
            else:
                text = body.content.decode(body.encoding or "utf-8", "replace") if body.status == 200 else ""
                parser.parse(text.splitlines())
            break
        else:
            parser.disallow_all = True
        self._robots[root] = parser
        return parser

    def fetch(self, url: str) -> Page:
        deadline = self.clock() + DEADLINE_SECONDS
        for hop in range(MAX_REDIRECTS + 1):
            if not self._robots_for(url, deadline).can_fetch(USER_AGENT, url):
                raise CaptureError("The site's robots rules do not allow fetching this page.")
            if hop and LOGIN_PATH.search(urlparse(url).path):
                raise LoginRequired("This page needs a login, so it was saved as a reference only.")
            body = self._get(url, deadline)
            if 300 <= body.status < 400 and "location" in body.headers:
                url = urljoin(url, body.headers["location"])
                continue
            if body.status in (401, 403):
                raise LoginRequired("This page needs a login, so it was saved as a reference only.")
            if body.status >= 400:
                raise CaptureError(f"The page returned HTTP {body.status}.")
            if "html" not in body.headers.get("content-type", "text/html"):
                raise CaptureError("Only HTML pages can be captured.")
            return Page(url, body.status, body.content.decode(body.encoding or "utf-8", "replace"))
        raise CaptureError("Too many redirects.")
