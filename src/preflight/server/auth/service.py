import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.config import Settings
from preflight.db.models import AuditEvent, Device, DeviceCode, User, WebSession

_hasher = PasswordHasher()
_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"


class AuthError(Exception):
    """Raised with a short machine-readable reason, such as 'invalid_credentials' or 'locked'."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def audit(db: Session, user_id: uuid.UUID | None, event: str, detail: str = "") -> None:
    db.add(AuditEvent(user_id=user_id, event=event, detail=detail))


def create_user(db: Session, username: str, password: str) -> User:
    if len(password) < 12:
        raise AuthError("password_too_short")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit(db, user.id, "user_created")
    return user


def enable_totp(db: Session, user: User) -> str:
    user.totp_secret = pyotp.random_base32()
    audit(db, user.id, "totp_enabled")
    return user.totp_secret


def authenticate(
    db: Session, settings: Settings, username: str, password: str, totp: str | None, now: datetime
) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        raise AuthError("invalid_credentials")
    if user.locked_until and user.locked_until > now:
        audit(db, user.id, "login_locked")
        raise AuthError("locked")
    ok = verify_password(user.password_hash, password)
    if ok and user.totp_secret:
        ok = bool(totp) and pyotp.TOTP(user.totp_secret).verify(totp, for_time=now, valid_window=1)
    if not ok:
        user.failed_logins += 1
        if user.failed_logins >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_logins = 0
        audit(db, user.id, "login_failed")
        raise AuthError("invalid_credentials")
    user.failed_logins = 0
    user.locked_until = None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


_DUMMY_HASH = hash_password("timing-equalizer-password")


def create_web_session(db: Session, user: User, now: datetime) -> tuple[str, WebSession]:
    raw = secrets.token_urlsafe(32)
    session = WebSession(
        user_id=user.id, token_hash=hash_token(raw), csrf_token=secrets.token_urlsafe(32), last_seen_at=now
    )
    db.add(session)
    audit(db, user.id, "login", "web")
    return raw, session


def resolve_web_session(db: Session, settings: Settings, raw: str, now: datetime) -> WebSession | None:
    session = db.scalar(select(WebSession).where(WebSession.token_hash == hash_token(raw)))
    if session is None:
        return None
    if session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) < now:
        db.delete(session)
        return None
    session.last_seen_at = now
    return session


def end_web_session(db: Session, session: WebSession) -> None:
    audit(db, session.user_id, "logout", "web")
    db.delete(session)


@dataclass(frozen=True)
class DeviceCodeGrant:
    device_code: str
    user_code: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: uuid.UUID


def _new_user_code() -> str:
    chars = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
    return f"{chars[:4]}-{chars[4:]}"


def normalize_user_code(code: str) -> str:
    chars = "".join(c for c in code.upper() if c.isalnum())
    return f"{chars[:4]}-{chars[4:]}"


def start_device_code(db: Session, settings: Settings, device_name: str, now: datetime) -> DeviceCodeGrant:
    raw = secrets.token_urlsafe(32)
    code = DeviceCode(
        device_code_hash=hash_token(raw),
        user_code=_new_user_code(),
        device_name=device_name[:100] or "cli",
        expires_at=now + timedelta(minutes=settings.device_code_minutes),
    )
    db.add(code)
    return DeviceCodeGrant(raw, code.user_code, settings.device_code_minutes * 60, settings.device_poll_seconds)


def find_pending_user_code(db: Session, user_code: str, now: datetime) -> DeviceCode | None:
    code = db.scalar(select(DeviceCode).where(DeviceCode.user_code == normalize_user_code(user_code)))
    if code is None or code.consumed or code.user_id is not None or code.expires_at <= now:
        return None
    return code


def approve_device_code(db: Session, user: User, code: DeviceCode) -> None:
    code.user_id = user.id
    audit(db, user.id, "device_approved", code.device_name)


def _issue_tokens(db: Session, settings: Settings, user_id: uuid.UUID, name: str, now: datetime) -> TokenPair:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    device = Device(
        user_id=user_id,
        name=name,
        access_hash=hash_token(access),
        access_expires_at=now + timedelta(minutes=settings.access_token_minutes),
        refresh_hash=hash_token(refresh),
    )
    db.add(device)
    db.flush()
    audit(db, user_id, "token_created", f"{device.id} {name}")
    return TokenPair(access, refresh, settings.access_token_minutes * 60, device.id)


def poll_device_code(db: Session, settings: Settings, raw_device_code: str, now: datetime) -> TokenPair:
    code = db.scalar(select(DeviceCode).where(DeviceCode.device_code_hash == hash_token(raw_device_code)))
    if code is None or code.consumed:
        raise AuthError("invalid_grant")
    if code.expires_at <= now:
        raise AuthError("expired_token")
    too_fast = code.last_polled_at and (now - code.last_polled_at).total_seconds() < settings.device_poll_seconds
    code.last_polled_at = now
    if too_fast:
        raise AuthError("slow_down")
    if code.user_id is None:
        raise AuthError("authorization_pending")
    code.consumed = True
    return _issue_tokens(db, settings, code.user_id, code.device_name, now)


def password_grant(
    db: Session, settings: Settings, username: str, password: str, totp: str | None, name: str, now: datetime
) -> TokenPair:
    user = authenticate(db, settings, username, password, totp, now)
    return _issue_tokens(db, settings, user.id, name[:100] or "cli", now)


def refresh_tokens(db: Session, settings: Settings, raw_refresh: str, now: datetime) -> TokenPair:
    device = db.scalar(select(Device).where(Device.refresh_hash == hash_token(raw_refresh)))
    if device is None or device.revoked_at is not None:
        raise AuthError("invalid_grant")
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    device.access_hash = hash_token(access)
    device.refresh_hash = hash_token(refresh)
    device.access_expires_at = now + timedelta(minutes=settings.access_token_minutes)
    return TokenPair(access, refresh, settings.access_token_minutes * 60, device.id)


def resolve_access_token(db: Session, raw: str, now: datetime) -> Device | None:
    device = db.scalar(select(Device).where(Device.access_hash == hash_token(raw)))
    if device is None or device.revoked_at is not None or device.access_expires_at <= now:
        return None
    device.last_used_at = now
    return device


def revoke_device(db: Session, user: User, device_id: uuid.UUID, now: datetime) -> bool:
    device = db.get(Device, device_id)
    if device is None or device.user_id != user.id:
        return False
    if device.revoked_at is None:
        device.revoked_at = now
        audit(db, user.id, "token_revoked", str(device.id))
    return True
