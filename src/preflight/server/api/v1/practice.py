import uuid
from datetime import date, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from preflight.core import drills, predictions
from preflight.core.errors import DomainError
from preflight.server.api.v1.projects import ProjectRef
from preflight.server.deps import AppSettings, CurrentPrincipal, Db, Now

router = APIRouter(tags=["practice"])


class DrillOut(BaseModel):
    id: uuid.UUID
    prompt: str
    answer: str
    interval_days: int
    due_date: date


class DrillReviewIn(BaseModel):
    score: int | None = Field(None, ge=0, le=5)
    attempt: str = ""
    grade_with_ai: bool = False


class DrillReviewOut(BaseModel):
    score: int
    feedback: str
    next_due: date
    interval_days: int


class PredictionIn(BaseModel):
    hypothesis: str
    evidence_planned: str = ""
    confidence: int = Field(ge=0, le=100)


class PredictionOut(BaseModel):
    id: uuid.UUID
    hypothesis: str
    evidence_planned: str
    confidence: int
    correct: bool | None
    failure_id: uuid.UUID | None
    created_at: datetime


class ResolveIn(BaseModel):
    correct: bool
    failure_id: uuid.UUID | None = None


@router.get("/drills/today")
def today(principal: CurrentPrincipal, db: Db, at: Now) -> list[DrillOut]:
    rows = drills.due(db, principal.user.id, at.date())
    db.commit()
    return [DrillOut(id=d.id, prompt=d.prompt, answer=d.answer, interval_days=d.interval_days, due_date=d.due_date) for d in rows]


@router.post("/drills/{drill_id}/review")
def review(drill_id: uuid.UUID, body: DrillReviewIn, request: Request, principal: CurrentPrincipal, db: Db, settings: AppSettings, at: Now) -> DrillReviewOut:
    drill = drills.get_drill(db, principal.user.id, drill_id)
    if body.grade_with_ai:
        score, feedback = drills.grade(db, request.app.state.ai, settings, drill, body.attempt, at)
    elif body.score is None:
        raise DomainError("Give a score from 0 to 5, or ask for AI grading.")
    else:
        score, feedback = body.score, ""
    drills.record(db, drill, score, at)
    db.commit()
    return DrillReviewOut(score=score, feedback=feedback, next_due=drill.due_date, interval_days=drill.interval_days)


def prediction_out(p) -> PredictionOut:
    return PredictionOut.model_validate(p, from_attributes=True)


@router.post("/projects/{ref}/predictions", status_code=201)
def predict(body: PredictionIn, project: ProjectRef, db: Db) -> PredictionOut:
    p = predictions.predict(db, project, body.hypothesis, body.evidence_planned, body.confidence)
    db.commit()
    return prediction_out(p)


@router.get("/projects/{ref}/predictions")
def list_predictions(project: ProjectRef) -> list[PredictionOut]:
    return [prediction_out(p) for p in sorted(project.predictions, key=lambda p: p.created_at)]


@router.post("/predictions/{prediction_id}/close")
def close_prediction(prediction_id: uuid.UUID, body: ResolveIn, principal: CurrentPrincipal, db: Db) -> PredictionOut:
    p = predictions.get_prediction(db, principal.user.id, prediction_id)
    predictions.resolve(db, p, body.correct, body.failure_id)
    db.commit()
    return prediction_out(p)
