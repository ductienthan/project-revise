import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from preflight.db.models import Device
from preflight.server.auth import service
from preflight.server.deps import SESSION_COOKIE, AppSettings, CurrentPrincipal, Db, Now, rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str
    totp: str | None = None


class DeviceCodeIn(BaseModel):
    device_name: str = "cli"


class DeviceTokenIn(BaseModel):
    device_code: str


class PasswordTokenIn(LoginIn):
    device_name: str = "cli"


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


def _token_out(pair: service.TokenPair) -> TokenOut:
    return TokenOut(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in)


def _auth_error(err: service.AuthError) -> HTTPException:
    reason = str(err)
    status = 423 if reason == "locked" else 401
    return HTTPException(status, reason)


@router.post("/login", dependencies=[rate_limit("login")])
def login(body: LoginIn, response: Response, db: Db, settings: AppSettings, at: Now) -> dict:
    try:
        user = service.authenticate(db, settings, body.username, body.password, body.totp, at)
    except service.AuthError as err:
        db.commit()
        raise _auth_error(err) from None
    raw, session = service.create_web_session(db, user, at)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, raw, httponly=True, secure=settings.require_https, samesite="lax", path="/"
    )
    return {"csrf_token": session.csrf_token}


@router.post("/logout")
def logout(principal: CurrentPrincipal, response: Response, db: Db) -> dict:
    if principal.session is not None:
        service.end_web_session(db, principal.session)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/device/code", dependencies=[rate_limit("device")])
def device_code(body: DeviceCodeIn, db: Db, settings: AppSettings, at: Now) -> dict:
    grant = service.start_device_code(db, settings, body.device_name, at)
    db.commit()
    return {
        "device_code": grant.device_code,
        "user_code": grant.user_code,
        "verification_uri": "/device",
        "expires_in": grant.expires_in,
        "interval": grant.interval,
    }


@router.post("/device/token", dependencies=[rate_limit("token")])
def device_token(body: DeviceTokenIn, db: Db, settings: AppSettings, at: Now) -> TokenOut:
    try:
        pair = service.poll_device_code(db, settings, body.device_code, at)
    except service.AuthError as err:
        db.commit()
        raise HTTPException(400, str(err)) from None
    db.commit()
    return _token_out(pair)


@router.post("/token", dependencies=[rate_limit("login")])
def password_token(body: PasswordTokenIn, db: Db, settings: AppSettings, at: Now) -> TokenOut:
    try:
        pair = service.password_grant(db, settings, body.username, body.password, body.totp, body.device_name, at)
    except service.AuthError as err:
        db.commit()
        raise _auth_error(err) from None
    db.commit()
    return _token_out(pair)


@router.post("/token/refresh", dependencies=[rate_limit("token")])
def refresh(body: RefreshIn, db: Db, settings: AppSettings, at: Now) -> TokenOut:
    try:
        pair = service.refresh_tokens(db, settings, body.refresh_token, at)
    except service.AuthError as err:
        raise HTTPException(401, str(err)) from None
    db.commit()
    return _token_out(pair)


@router.get("/devices")
def devices(principal: CurrentPrincipal, db: Db) -> list[DeviceOut]:
    rows = db.scalars(select(Device).where(Device.user_id == principal.user.id).order_by(Device.created_at))
    return [DeviceOut.model_validate(d, from_attributes=True) for d in rows]


@router.delete("/devices/{device_id}")
def revoke(device_id: uuid.UUID, principal: CurrentPrincipal, db: Db, at: Now) -> dict:
    if not service.revoke_device(db, principal.user, device_id, at):
        raise HTTPException(404, "device not found")
    db.commit()
    return {"ok": True}
