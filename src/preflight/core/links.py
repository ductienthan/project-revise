import uuid
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from preflight.capture import browser
from preflight.capture.extract import extract
from preflight.capture.fetch import Fetcher, NeedsJavaScript
from preflight.core.errors import DomainError, NotFound
from preflight.db.models import Link, LinkType, Project, Snapshot

MAX_SNAPSHOT_CHARS = 200_000


def add_link(db: Session, project: Project, url: str, title: str, note: str, internal: bool, sensitive: bool) -> Link:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DomainError("Links need a full http or https URL.")
    link = Link(
        user_id=project.user_id, project_id=project.id, url=url, title=title, note=note,
        type=LinkType.INTERNAL if internal else LinkType.PUBLIC, sensitive=sensitive,
    )
    project.links.append(link)
    db.flush()
    return link


def capture(db: Session, fetcher: Fetcher, link: Link, at: datetime, use_browser: bool = False) -> Snapshot:
    """Fetch a public link into a new snapshot version. Internal links are never fetched."""
    if link.type == LinkType.INTERNAL:
        raise DomainError("Internal links are references only and are never fetched.")
    page = fetcher.fetch(link.url)
    try:
        found = extract(page.html, page.url)
    except NeedsJavaScript:
        if not use_browser:
            raise NeedsJavaScript(
                "This page needs JavaScript. Browser capture is off; set PREFLIGHT_CAPTURE_BROWSER=true "
                "and install the browser extra (preflight[browser]) to allow it."
            ) from None
        found = extract(browser.render(page.url, fetcher.allowed_address), page.url)
    text = f"# {found.title}\n\n{found.text}" if found.title and not found.text.startswith("# ") else found.text
    snapshot = Snapshot(
        user_id=link.user_id,
        link_id=link.id,
        content=text[:MAX_SNAPSHOT_CHARS],
        truncated=len(text) > MAX_SNAPSHOT_CHARS,
        version=(link.snapshots[-1].version + 1) if link.snapshots else 1,
        captured_at=at,
    )
    link.snapshots.append(snapshot)
    if not link.title:
        link.title = found.title
    db.flush()
    return snapshot


def get_link(db: Session, user_id: uuid.UUID, link_id: uuid.UUID) -> Link:
    link = db.get(Link, link_id)
    if link is None or link.user_id != user_id:
        raise NotFound("link not found")
    return link


def get_snapshot(db: Session, user_id: uuid.UUID, snapshot_id: uuid.UUID) -> Snapshot:
    snapshot = db.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.user_id != user_id:
        raise NotFound("snapshot not found")
    return snapshot
