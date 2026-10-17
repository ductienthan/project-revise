from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from preflight.config import Settings
from preflight.db.models import Device, User, WebSession
from preflight.server.auth import service

SESSION_COOKIE = "preflight_session"
LOGIN_CSRF_COOKIE = "preflight_login_csrf"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as db:
        yield db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def now(request: Request) -> datetime:
    return request.app.state.clock()


Db = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Now = Annotated[datetime, Depends(now)]


@dataclass(frozen=True)
class Principal:
    user: User
    session: WebSession | None = None
    device: Device | None = None


def rate_limit(bucket: str):
    def check(request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        if not request.app.state.rate_limiter.allow(bucket, key):
            raise HTTPException(429, "too many requests")

    return Depends(check)


def _csrf_ok(request: Request, session: WebSession, form_token: str | None = None) -> bool:
    sent = request.headers.get("x-csrf-token") or form_token
    return bool(sent) and service.secrets.compare_digest(sent, session.csrf_token)


def web_session(request: Request, db: Session, settings: Settings, at: datetime) -> WebSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    session = service.resolve_web_session(db, settings, raw, at)
    db.commit()
    return session


def require_principal(request: Request, db: Db, settings: AppSettings, at: Now) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        device = service.resolve_access_token(db, auth[7:].strip(), at)
        db.commit()
        if device is None:
            raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        return Principal(user=db.get(User, device.user_id), device=device)
    session = web_session(request, db, settings, at)
    if session is None:
        raise HTTPException(401, "not signed in")
    if request.method in UNSAFE_METHODS and not _csrf_ok(request, session):
        raise HTTPException(403, "missing or invalid CSRF token")
    return Principal(user=db.get(User, session.user_id), session=session)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]


class LoginRequired(Exception):
    pass


async def require_page_session(request: Request, db: Db, settings: AppSettings, at: Now) -> Principal:
    session = web_session(request, db, settings, at)
    if session is None:
        raise LoginRequired()
    if request.method in UNSAFE_METHODS:
        form = await request.form()
        if not _csrf_ok(request, session, form.get("csrf_token")):
            raise HTTPException(403, "missing or invalid CSRF token")
    return Principal(user=db.get(User, session.user_id), session=session)


PagePrincipal = Annotated[Principal, Depends(require_page_session)]
