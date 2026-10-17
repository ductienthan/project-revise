import secrets
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from preflight.server.auth import service
from preflight.server.deps import LOGIN_CSRF_COOKIE, SESSION_COOKIE, AppSettings, Db, Now, PagePrincipal, rate_limit

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


def render(request: Request, name: str, principal=None, status_code: int = 200, **context) -> HTMLResponse:
    csrf = principal.session.csrf_token if principal and principal.session else None
    return templates.TemplateResponse(
        request, name, {"user": principal.user if principal else None, "csrf": csrf, **context}, status_code
    )


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, settings: AppSettings, next: str = "/") -> HTMLResponse:
    token = secrets.token_urlsafe(32)
    response = render(request, "login.html", login_csrf=token, next=_safe_next(next))
    response.set_cookie(
        LOGIN_CSRF_COOKIE, token, httponly=True, secure=settings.require_https, samesite="strict", path="/login"
    )
    return response


@router.post("/login", dependencies=[rate_limit("login")])
def login_submit(
    request: Request,
    db: Db,
    settings: AppSettings,
    at: Now,
    username: str = Form(),
    password: str = Form(),
    csrf_token: str = Form(),
    totp: str = Form(""),
    next: str = Form("/"),
):
    cookie = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if not cookie or not secrets.compare_digest(cookie, csrf_token):
        return render(request, "login.html", error="Your form expired. Try again.", login_csrf="", next="/", status_code=403)
    try:
        user = service.authenticate(db, settings, username, password, totp or None, at)
    except service.AuthError as err:
        db.commit()
        message = "Too many attempts. Try again later." if str(err) == "locked" else "Wrong username, password or code."
        return render(request, "login.html", error=message, login_csrf=csrf_token, next=_safe_next(next), status_code=401)
    raw, _ = service.create_web_session(db, user, at)
    db.commit()
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, secure=settings.require_https, samesite="lax", path="/")
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
    return response


@router.post("/logout")
def logout(principal: PagePrincipal, db: Db):
    service.end_web_session(db, principal.session)
    db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/device", response_class=HTMLResponse)
def device_form(request: Request, principal: PagePrincipal, code: str = "") -> HTMLResponse:
    return render(request, "device.html", principal, code=code)


@router.post("/device", response_class=HTMLResponse)
def device_approve(request: Request, principal: PagePrincipal, db: Db, at: Now, code: str = Form()) -> HTMLResponse:
    pending = service.find_pending_user_code(db, code, at)
    if pending is None:
        return render(request, "device.html", principal, code=code, error="That code is unknown or expired.", status_code=400)
    service.approve_device_code(db, principal.user, pending)
    db.commit()
    return render(request, "device.html", principal, approved=pending.device_name)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, principal: PagePrincipal) -> HTMLResponse:
    return render(request, "index.html", principal)
