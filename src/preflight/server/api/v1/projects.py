import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from preflight.core import checklist, failures, lifecycle, projects
from preflight.core.failures import FailureInput
from preflight.db.models import ChecklistItem, Project
from preflight.server.api.v1.schemas import (
    ChecklistItemOut,
    ChecklistItemPatch,
    ChecklistOut,
    FailureIn,
    FailureLogged,
    FailureOut,
    ItemPatched,
    ProjectIn,
    ProjectOut,
    RequirementsIn,
    RequirementsOut,
    StartIn,
    StartOut,
    StatusOut,
    WinIn,
    WinOut,
)
from preflight.server.deps import CurrentPrincipal, Db, Now

router = APIRouter(tags=["projects"])


def project_ref(ref: str, principal: CurrentPrincipal, db: Db) -> Project:
    return projects.find_project(db, principal.user.id, ref)


ProjectRef = Annotated[Project, Depends(project_ref)]


def checklist_out(project: Project) -> ChecklistOut:
    linked = checklist.linked_failures(project)
    return ChecklistOut(
        project=ProjectOut.model_validate(project),
        items=[ChecklistItemOut.model_validate(i).model_copy(update={"linked_failures": linked.get(i.id, [])}) for i in project.checklist_items],
        unresolved_required=[i.position for i in lifecycle.unresolved_required(project)],
    )


@router.post("/projects", status_code=201)
def create(body: ProjectIn, principal: CurrentPrincipal, db: Db) -> ProjectOut:
    project = projects.create_project(db, principal.user.id, body.name, body.idea, body.goals, body.requirements)
    db.commit()
    return ProjectOut.model_validate(project)


@router.get("/projects")
def list_all(principal: CurrentPrincipal, db: Db) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in projects.list_projects(db, principal.user.id)]


@router.get("/projects/{ref}")
def get(project: ProjectRef) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.delete("/projects/{ref}")
def delete(project: ProjectRef, db: Db) -> dict:
    db.delete(project)
    db.commit()
    return {"ok": True}


@router.get("/projects/{ref}/requirements")
def requirements(project: ProjectRef) -> list[RequirementsOut]:
    return [RequirementsOut.model_validate(v) for v in project.requirement_versions]


@router.put("/projects/{ref}/requirements")
def save_requirements(body: RequirementsIn, project: ProjectRef, db: Db) -> RequirementsOut:
    version = projects.save_requirements(db, project, body.text)
    db.commit()
    return RequirementsOut.model_validate(version)


@router.post("/projects/{ref}/start")
def start(body: StartIn, project: ProjectRef, db: Db, at: Now) -> StartOut:
    result = lifecycle.start(db, project, at, body.override_reason)
    db.commit()
    return StartOut(started=result.started, checklist=checklist_out(project))


@router.post("/projects/{ref}/checklist/refresh")
def refresh_checklist(project: ProjectRef, db: Db, at: Now) -> ChecklistOut:
    lifecycle.refresh(db, project, at)
    db.commit()
    return checklist_out(project)


@router.get("/projects/{ref}/checklist", response_model=None)
def get_checklist(project: ProjectRef, format: str = "json") -> ChecklistOut | PlainTextResponse:
    if format == "md":
        return PlainTextResponse(checklist.to_markdown(project), media_type="text/markdown")
    return checklist_out(project)


@router.patch("/checklist-items/{item_id}")
def patch_item(item_id: uuid.UUID, body: ChecklistItemPatch, principal: CurrentPrincipal, db: Db, at: Now) -> ItemPatched:
    item = db.get(ChecklistItem, item_id)
    if item is None or item.user_id != principal.user.id:
        raise HTTPException(404, "checklist item not found")
    project = db.get(Project, item.project_id)
    started = lifecycle.set_item_status(db, item, project, body.status, body.reason, body.revisit_date, at)
    db.commit()
    return ItemPatched(item=ChecklistItemOut.model_validate(item), project_started=started)


@router.post("/projects/{ref}/failures", status_code=201)
def log_failure(body: FailureIn, project: ProjectRef, db: Db, at: Now) -> FailureLogged:
    data = FailureInput(**body.model_dump(exclude={"id"}))
    logged = failures.log_failure(db, project, data, body.id, at)
    db.commit()
    return FailureLogged(
        failure=FailureOut.model_validate(logged.failure),
        created=logged.created,
        project_status=project.status,
        related_items=[ChecklistItemOut.model_validate(i) for i in logged.related_items],
        similar=[FailureOut.model_validate(f) for f in logged.similar],
    )


@router.get("/projects/{ref}/failures")
def project_failures(project: ProjectRef, principal: CurrentPrincipal, db: Db) -> list[FailureOut]:
    return [FailureOut.model_validate(f) for f in failures.search_failures(db, principal.user.id, project_id=project.id)]


@router.post("/projects/{ref}/wins", status_code=201)
def log_win(body: WinIn, project: ProjectRef, db: Db) -> WinOut:
    win = failures.log_win(db, project, body.text, body.tag, body.id)
    db.commit()
    return WinOut.model_validate(win)


@router.get("/projects/{ref}/status")
def status(project: ProjectRef) -> StatusOut:
    current = projects.latest_requirements(project)
    return StatusOut(
        project=ProjectOut.model_validate(project),
        requirements_version=current.version if current else None,
        checklist_total=len(project.checklist_items),
        checklist_resolved=sum(lifecycle.is_resolved(i) for i in project.checklist_items),
        unresolved_required=len(lifecycle.unresolved_required(project)),
        failures=len(project.failures),
        wins=len(project.wins),
        minutes_lost=sum(f.minutes_lost for f in project.failures),
        gate_overrides=[o.reason for o in project.gate_overrides],
    )

