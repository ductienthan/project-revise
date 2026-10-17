import uuid

from fastapi import APIRouter

from preflight.core import failures, projects
from preflight.server.api.v1.schemas import FailureOut, FailurePatch
from preflight.server.deps import CurrentPrincipal, Db

router = APIRouter(prefix="/failures", tags=["failures"])


@router.get("")
def search(principal: CurrentPrincipal, db: Db, q: str | None = None, project: str | None = None) -> list[FailureOut]:
    project_id = projects.find_project(db, principal.user.id, project).id if project else None
    return [FailureOut.model_validate(f) for f in failures.search_failures(db, principal.user.id, q, project_id)]


@router.get("/{failure_id}/similar")
def similar(failure_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> list[FailureOut]:
    failure = failures.get_failure(db, principal.user.id, failure_id)
    return [FailureOut.model_validate(f) for f in failures.similar_failures(db, failure)]


@router.patch("/{failure_id}")
def patch(failure_id: uuid.UUID, body: FailurePatch, principal: CurrentPrincipal, db: Db) -> FailureOut:
    failure = failures.get_failure(db, principal.user.id, failure_id)
    changes = body.model_dump(exclude_unset=True, exclude={"link_item_id", "not_on_checklist"})
    failures.update_failure(failure, changes)
    if body.link_item_id is not None or body.not_on_checklist:
        failures.link_failure(db, failure, body.link_item_id)
    db.commit()
    return FailureOut.model_validate(failure)
