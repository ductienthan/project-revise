import uuid
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from preflight.core import export, lifecycle, progress, report
from preflight.db.models import ItemOutcome
from preflight.server.api.v1.projects import ProjectRef, checklist_out
from preflight.server.api.v1.schemas import ChecklistOut
from preflight.server.deps import AppSettings, CurrentPrincipal, Db, Now

router = APIRouter(tags=["reports"])


class CloseIn(BaseModel):
    summary: str = ""
    outcomes: dict[uuid.UUID, ItemOutcome] = {}


class CloseOut(BaseModel):
    checklist: ChecklistOut
    summary: str
    gate_overrides: list[str]


@router.post("/projects/{ref}/close")
def close(body: CloseIn, project: ProjectRef, db: Db, settings: AppSettings, at: Now) -> CloseOut:
    retro = lifecycle.close(db, project, at, body.summary, body.outcomes, settings.retrospective_required)
    db.commit()
    return CloseOut(checklist=checklist_out(project), summary=retro.summary, gate_overrides=[o.reason for o in project.gate_overrides])


@router.get("/progress")
def get_progress(principal: CurrentPrincipal, db: Db, settings: AppSettings, at: Now) -> dict:
    data = progress.progress(db, principal.user.id, at, settings.rule_observation_days)
    db.commit()
    return data


@router.get("/reports/weekly", response_model=None)
def weekly(principal: CurrentPrincipal, db: Db, settings: AppSettings, at: Now, format: Literal["json", "md"] = "json"):
    data = report.weekly(db, principal.user.id, at, settings.rule_observation_days)
    db.commit()
    if format == "md":
        return PlainTextResponse(report.weekly_markdown(data), media_type="text/markdown")
    return data


@router.get("/export", response_model=None)
def export_all(principal: CurrentPrincipal, db: Db, format: Literal["json", "csv", "md"] = "json") -> Response:
    if format == "csv":
        return Response(
            export.as_csv_zip(db, principal.user.id), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="preflight-export.zip"'},
        )
    if format == "md":
        return PlainTextResponse(export.as_markdown(db, principal.user.id), media_type="text/markdown")
    return Response(export.as_json(db, principal.user.id), media_type="application/json")
