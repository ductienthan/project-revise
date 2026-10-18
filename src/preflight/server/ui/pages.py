import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from preflight.core import (
    checklist,
    failures,
    lifecycle,
    links,
    patterns,
    progress,
    projects,
    report,
)
from preflight.core.errors import DomainError
from preflight.db.models import FailureCategory, Project
from preflight.server.auth import service
from preflight.server.deps import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    Db,
    Lockouts,
    Now,
    PagePrincipal,
    Source,
    rate_limit,
)

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
    lockout: Lockouts,
    source: Source,
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
        user = service.authenticate(db, lockout, source, username, password, totp or None, at)
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
def home(request: Request, principal: PagePrincipal, db: Db) -> HTMLResponse:
    rows = [
        (p, len(p.failures), len(p.wins), len(lifecycle.unresolved_required(p)))
        for p in projects.list_projects(db, principal.user.id)
    ]
    return render(request, "index.html", principal, rows=rows, form={})


@router.post("/projects", response_class=HTMLResponse)
def create_project(
    request: Request, principal: PagePrincipal, db: Db,
    name: str = Form(), idea: str = Form(""), goals: str = Form(""), requirements: str = Form(""),
):
    try:
        projects.create_project(db, principal.user.id, name.strip(), idea, goals, requirements)
    except DomainError as err:
        db.rollback()
        form = {"name": name, "idea": idea, "goals": goals, "requirements": requirements}
        rows = [(p, len(p.failures), len(p.wins), len(lifecycle.unresolved_required(p))) for p in projects.list_projects(db, principal.user.id)]
        return render(request, "index.html", principal, rows=rows, form=form, error=str(err), status_code=400)
    db.commit()
    return RedirectResponse(f"/projects/{name.strip()}", status_code=303)


@router.post("/projects/{name}/requirements")
def save_requirements(name: str, principal: PagePrincipal, db: Db, text: str = Form("")):
    project = projects.find_project(db, principal.user.id, name)
    projects.save_requirements(db, project, text)
    db.commit()
    return RedirectResponse(f"/projects/{name}", status_code=303)


@router.get("/projects/{name}", response_class=HTMLResponse)
def project_page(request: Request, name: str, principal: PagePrincipal, db: Db) -> HTMLResponse:
    project = projects.find_project(db, principal.user.id, name)
    return render(
        request,
        "project.html",
        principal,
        project=project,
        requirements=projects.latest_requirements(project),
        failures=failures.search_failures(db, principal.user.id, project_id=project.id),
        unresolved={i.id for i in lifecycle.unresolved_required(project)},
        linked=checklist.linked_failures(project),
    )


@router.get("/snapshots/{snapshot_id}", response_class=HTMLResponse)
def snapshot_page(request: Request, snapshot_id: uuid.UUID, principal: PagePrincipal, db: Db) -> HTMLResponse:
    snapshot = links.get_snapshot(db, principal.user.id, snapshot_id)
    link = links.get_link(db, principal.user.id, snapshot.link_id)
    return render(request, "snapshot.html", principal, snapshot=snapshot, link=link)


@router.post("/snapshots/{snapshot_id}")
def snapshot_action(
    snapshot_id: uuid.UUID, principal: PagePrincipal, db: Db, action: str = Form(), content: str = Form("")
):
    snapshot = links.get_snapshot(db, principal.user.id, snapshot_id)
    link = links.get_link(db, principal.user.id, snapshot.link_id)
    project = db.get(Project, link.project_id)
    match action:
        case "delete":
            db.delete(snapshot)
            db.commit()
            return RedirectResponse(f"/projects/{project.name}", status_code=303)
        case "save":
            snapshot.content, snapshot.approved = content, True
        case "approve":
            snapshot.approved = True
        case "unapprove":
            snapshot.approved = False
    db.commit()
    return RedirectResponse(f"/snapshots/{snapshot.id}", status_code=303)


@router.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, principal: PagePrincipal, db: Db, settings: AppSettings, at: Now) -> HTMLResponse:
    data = progress.progress(db, principal.user.id, at, settings.rule_observation_days)
    db.commit()
    categories = [c.value for c in FailureCategory] + ["uncategorized"]
    return render(request, "progress.html", principal, data=data, categories=categories)


@router.get("/weekly", response_class=HTMLResponse)
def weekly_page(request: Request, principal: PagePrincipal, db: Db, settings: AppSettings, at: Now) -> HTMLResponse:
    data = report.weekly(db, principal.user.id, at, settings.rule_observation_days)
    db.commit()
    return render(request, "weekly.html", principal, r=data)


@router.get("/failures", response_class=HTMLResponse)
def failures_page(request: Request, principal: PagePrincipal, db: Db, q: str = "") -> HTMLResponse:
    names = {p.id: p.name for p in projects.list_projects(db, principal.user.id)}
    rows = failures.search_failures(db, principal.user.id, q or None)
    return render(request, "failures.html", principal, rows=rows, names=names, q=q)


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, principal: PagePrincipal, db: Db) -> HTMLResponse:
    found = patterns.detect_patterns(db, principal.user.id)
    db.commit()
    suggestions = [
        (p, patterns.draft_rule_text(patterns.pattern_failures(db, p))) for p in found if patterns.live_rule(db, p) is None
    ]
    labels = {p.id: p.label for p in found}
    return render(
        request, "rules.html", principal, suggestions=suggestions, rules=patterns.list_rules(db, principal.user.id), labels=labels
    )


@router.post("/rules")
def approve_rule(principal: PagePrincipal, db: Db, pattern_id: uuid.UUID = Form(), checklist_text: str = Form()):
    patterns.approve_rule(db, patterns.get_pattern(db, principal.user.id, pattern_id), checklist_text)
    db.commit()
    return RedirectResponse("/rules", status_code=303)
