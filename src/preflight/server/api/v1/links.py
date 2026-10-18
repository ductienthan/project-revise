import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from preflight.capture.fetch import CaptureError
from preflight.core import links
from preflight.db.models import Link, LinkType, Snapshot
from preflight.server.api.v1.projects import ProjectRef
from preflight.server.deps import CurrentPrincipal, Db, Now

router = APIRouter(tags=["links"])


class LinkIn(BaseModel):
    url: str
    title: str = ""
    note: str = ""
    internal: bool = False
    sensitive: bool = False
    capture: bool = False


class SnapshotSummary(BaseModel):
    id: uuid.UUID
    version: int
    words: int
    approved: bool
    truncated: bool
    captured_at: datetime


class SnapshotOut(SnapshotSummary):
    content: str


class LinkOut(BaseModel):
    id: uuid.UUID
    url: str
    title: str
    note: str
    type: LinkType
    sensitive: bool
    snapshots: list[SnapshotSummary]


class Captured(BaseModel):
    link: LinkOut
    snapshot: SnapshotOut | None = None
    capture_error: str | None = None


class SnapshotPatch(BaseModel):
    approved: bool | None = None
    content: str | None = None


def summary(s: Snapshot) -> SnapshotSummary:
    return SnapshotSummary(id=s.id, version=s.version, words=len(s.content.split()), approved=s.approved,
                           truncated=s.truncated, captured_at=s.captured_at)


def snapshot_out(s: Snapshot) -> SnapshotOut:
    return SnapshotOut(**summary(s).model_dump(), content=s.content)


def link_out(link: Link) -> LinkOut:
    return LinkOut(id=link.id, url=link.url, title=link.title, note=link.note, type=link.type,
                   sensitive=link.sensitive, snapshots=[summary(s) for s in link.snapshots])


def _capture(request: Request, db, link: Link, at: datetime) -> Captured:
    try:
        snapshot = links.capture(db, request.app.state.fetcher, link, at, request.app.state.settings.capture_browser)
    except CaptureError as err:
        db.commit()
        return Captured(link=link_out(link), capture_error=str(err))
    db.commit()
    return Captured(link=link_out(link), snapshot=snapshot_out(snapshot))


@router.post("/projects/{ref}/links", status_code=201)
def add(body: LinkIn, request: Request, project: ProjectRef, db: Db, at: Now) -> Captured:
    link = links.add_link(db, project, body.url, body.title, body.note, body.internal, body.sensitive)
    if body.capture and not body.internal:
        return _capture(request, db, link, at)
    db.commit()
    return Captured(link=link_out(link))


@router.get("/projects/{ref}/links")
def list_links(project: ProjectRef) -> list[LinkOut]:
    return [link_out(link) for link in project.links]


@router.post("/links/{link_id}/capture")
def capture(link_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: Db, at: Now) -> Captured:
    return _capture(request, db, links.get_link(db, principal.user.id, link_id), at)


@router.post("/links/{link_id}/refresh")
def refresh(link_id: uuid.UUID, request: Request, principal: CurrentPrincipal, db: Db, at: Now) -> Captured:
    return _capture(request, db, links.get_link(db, principal.user.id, link_id), at)


@router.delete("/links/{link_id}")
def delete_link(link_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> dict:
    db.delete(links.get_link(db, principal.user.id, link_id))
    db.commit()
    return {"ok": True}


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> SnapshotOut:
    return snapshot_out(links.get_snapshot(db, principal.user.id, snapshot_id))


@router.patch("/snapshots/{snapshot_id}")
def patch_snapshot(snapshot_id: uuid.UUID, body: SnapshotPatch, principal: CurrentPrincipal, db: Db) -> SnapshotOut:
    snapshot = links.get_snapshot(db, principal.user.id, snapshot_id)
    if body.content is not None:
        snapshot.content = body.content
    if body.approved is not None:
        snapshot.approved = body.approved
    db.commit()
    return snapshot_out(snapshot)


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> dict:
    db.delete(links.get_snapshot(db, principal.user.id, snapshot_id))
    db.commit()
    return {"ok": True}
