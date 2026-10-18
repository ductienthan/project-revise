import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from preflight.ai import gate
from preflight.ai.prompts.review import REVIEW
from preflight.core import assist, failures, patterns, review
from preflight.core.errors import DomainError
from preflight.db.models import AiCall, QuestionStatus, Review
from preflight.server.api.v1.projects import ProjectRef
from preflight.server.deps import AppSettings, CurrentPrincipal, Db, Now

router = APIRouter(tags=["review"])


class ReviewItemOut(BaseModel):
    id: str
    kind: str
    label: str
    tokens: int
    text: str


class ReviewItemsOut(BaseModel):
    ai_available: bool
    provider: str | None
    items: list[ReviewItemOut]
    excluded_sensitive: int
    model: str | None
    max_cost_usd: float
    calls: int
    warning: str | None = None
    reviewed: bool
    review_current: bool
    requirements_version: int | None


class ReviewIn(BaseModel):
    approved_item_ids: list[str]


class QuestionOut(BaseModel):
    id: uuid.UUID
    category: str
    question: str
    status: QuestionStatus
    answer: str


class ReviewOut(BaseModel):
    id: uuid.UUID
    requirements_version: int | None
    questions: list[QuestionOut]
    drafts: list[dict]
    accepted_drafts: list[int]
    dropped_drafts: int = 0
    cost_usd: float
    completed: bool


class AnswerIn(BaseModel):
    question_id: uuid.UUID
    status: QuestionStatus
    answer: str = ""


class AnswersIn(BaseModel):
    answers: list[AnswerIn]
    accepted_drafts: list[int] = []


class Approval(BaseModel):
    approved: bool


def _ai(request: Request):
    return request.app.state.ai


def review_out(db, r: Review, dropped: int = 0) -> ReviewOut:
    calls = [db.get(AiCall, uuid.UUID(call_id)) for call_id in r.ai_call_ids]
    return ReviewOut(
        id=r.id,
        requirements_version=r.requirements_version,
        questions=[QuestionOut(id=q.id, category=q.category, question=q.question, status=q.status, answer=q.answer) for q in r.questions],
        drafts=r.drafts,
        accepted_drafts=r.accepted_drafts,
        dropped_drafts=dropped,
        cost_usd=round(sum(c.cost_usd for c in calls), 4),
        completed=r.completed_at is not None,
    )


@router.get("/projects/{ref}/review-items")
def review_items(request: Request, project: ProjectRef, db: Db) -> ReviewItemsOut:
    found = review.material(db, project)
    offered = gate.offerable(found.items)
    ai = _ai(request)
    ready = ai is not None and ai.available()
    warning = None
    try:
        batches = review.plan_batches(offered, gate.input_budget(ai, REVIEW)) if ready and offered else []
    except DomainError as err:
        batches, warning = [], str(err)
    estimates = [gate.estimate(ai, REVIEW, batch) for batch in batches]
    last = review.latest_review(db, project, completed=True)
    current = review.latest_requirements(project)
    return ReviewItemsOut(
        ai_available=ready,
        provider=ai.provider if ai is not None else None,
        items=[ReviewItemOut(id=i.id, kind=i.kind, label=i.label, tokens=i.tokens, text=i.text) for i in offered],
        excluded_sensitive=found.excluded_sensitive,
        model=ai.model_for(REVIEW.role) if ready else None,
        max_cost_usd=round(sum(e.max_cost_usd for e in estimates), 4),
        calls=len(batches),
        warning=warning,
        reviewed=last is not None,
        review_current=last is not None and last.material_hash == review.material_hash(found),
        requirements_version=current.version if current else None,
    )


@router.post("/projects/{ref}/reviews", status_code=201)
def create_review(body: ReviewIn, request: Request, project: ProjectRef, db: Db, settings: AppSettings, at: Now) -> ReviewOut:
    created, dropped = review.run_review(db, _ai(request), settings, project, set(body.approved_item_ids), at)
    db.commit()
    return review_out(db, created, dropped)


@router.get("/reviews/{review_id}")
def get_review(review_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> ReviewOut:
    return review_out(db, review.get_review(db, principal.user.id, review_id))


@router.post("/reviews/{review_id}/answers")
def answer(review_id: uuid.UUID, body: AnswersIn, principal: CurrentPrincipal, db: Db, at: Now) -> ReviewOut:
    r = review.get_review(db, principal.user.id, review_id)
    answers = [review.Answer(a.question_id, a.status, a.answer) for a in body.answers]
    review.answer_review(r, answers, body.accepted_drafts, at)
    db.commit()
    return review_out(db, r)


@router.post("/failures/{failure_id}/suggest")
def suggest(failure_id: uuid.UUID, body: Approval, request: Request, principal: CurrentPrincipal, db: Db, settings: AppSettings, at: Now) -> dict:
    failure = failures.get_failure(db, principal.user.id, failure_id)
    if not body.approved:
        return {"sent": False}
    output = assist.suggest_classification(db, _ai(request), settings, failure, at)
    db.commit()
    return {"sent": True, **output.model_dump()}


@router.post("/patterns/{pattern_id}/draft-rule")
def ai_rule(pattern_id: uuid.UUID, body: Approval, request: Request, principal: CurrentPrincipal, db: Db, settings: AppSettings, at: Now) -> dict:
    pattern = patterns.get_pattern(db, principal.user.id, pattern_id)
    if not body.approved:
        return {"sent": False}
    output = assist.draft_rule(db, _ai(request), settings, pattern, at)
    db.commit()
    return {"sent": True, **output.model_dump()}
