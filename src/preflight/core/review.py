import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.ai import gate
from preflight.ai.client import AIClient
from preflight.ai.gate import GateItem
from preflight.ai.prompts.review import REVIEW
from preflight.ai.schemas import ReviewOutput
from preflight.config import Settings
from preflight.core.checklist import Candidate, short_id
from preflight.core.errors import DomainError, NotFound
from preflight.core.projects import latest_requirements
from preflight.core.text import keywords, overlap
from preflight.db.models import (
    Failure,
    Link,
    LinkType,
    Priority,
    Project,
    ProjectStatus,
    QuestionStatus,
    Review,
    ReviewQuestion,
    Rule,
    RuleStatus,
    Severity,
    SourceType,
)

MAX_HISTORY_FAILURES = 40


@dataclass(frozen=True)
class ReviewMaterial:
    items: list[GateItem]
    refs: dict[str, tuple[SourceType, str] | None]
    excluded_sensitive: int


def _history(db: Session, project: Project, requirement_words: frozenset[str]) -> tuple[str, dict]:
    failures = list(db.scalars(select(Failure).where(Failure.user_id == project.user_id, Failure.project_id != project.id)))
    failures.sort(
        key=lambda f: (f.severity != Severity.HIGH, -overlap(requirement_words, keywords(f"{f.title} {f.prevention}")), -f.minutes_lost)
    )
    rules = db.scalars(select(Rule).where(Rule.user_id == project.user_id, Rule.status != RuleStatus.RETIRED))
    lines, refs = [], {}
    for f in failures[:MAX_HISTORY_FAILURES]:
        ref = f"F:{short_id(f.id)}"
        refs[ref] = (SourceType.FAILURE, str(f.id))
        detail = ". ".join(x for x in (f.what_happened, f"root cause: {f.root_cause}" if f.root_cause else "", f"prevention: {f.prevention}" if f.prevention else "") if x)
        lines.append(f"{ref} [{f.category or 'uncategorized'}, {f.severity}, {f.minutes_lost} min] {f.title}. {detail}")
    for r in rules:
        ref = f"R:{short_id(r.id)}"
        refs[ref] = (SourceType.RULE, str(r.id))
        lines.append(f"{ref} [rule, {r.status}] {r.checklist_text}")
    return "\n".join(lines), refs


def material(db: Session, project: Project) -> ReviewMaterial:
    """Everything that could be sent for this project's review. Sensitive links are never offered."""
    items, refs = [], {}
    if project.idea or project.goals:
        items.append(GateItem("project", "project", "Idea and goals", f"Idea: {project.idea}\nGoals: {project.goals}"))
    current = latest_requirements(project)
    words = frozenset()
    if current:
        lines = [line for line in current.text.splitlines() if line.strip()]
        numbered = "\n".join(f"REQ:{n} {line}" for n, line in enumerate(lines, start=1))
        refs.update({f"REQ:{n}": None for n in range(1, len(lines) + 1)})
        items.append(GateItem(f"req:v{current.version}", "requirements", f"Requirements v{current.version} ({len(current.text.split())} words)", numbered))
        words = keywords(current.text)
    history, history_refs = _history(db, project, words)
    if history:
        refs.update(history_refs)
        count = sum(1 for r in history_refs if r.startswith("F:"))
        items.append(GateItem("history", "history", f"Failure history and rules ({count} failures, {len(history_refs) - count} rules)", history, cacheable=True))

    links = list(db.scalars(select(Link).where(Link.project_id == project.id).order_by(Link.created_at)))
    for link in links:
        if link.sensitive:
            continue
        ref = f"L:{short_id(link.id)}"
        refs[ref] = None
        note = f"Title: {link.title}\nURL: {link.url}\nNote: {link.note}"
        items.append(GateItem(f"link:{link.id}", "link_note", f"Note on {link.type} link: {link.title or link.url}", note))
        approved = [s for s in link.snapshots if s.approved]
        if link.type == LinkType.PUBLIC and approved:
            snap = approved[-1]
            refs[f"S:{short_id(snap.id)}"] = None
            items.append(GateItem(f"snapshot:{snap.id}", "snapshot", f"Snapshot: {link.title or link.url} v{snap.version} ({len(snap.content.split())} words)", snap.content))
    return ReviewMaterial(items, refs, sum(link.sensitive for link in links))


def latest_review(db: Session, project: Project, completed: bool = False) -> Review | None:
    stmt = select(Review).where(Review.project_id == project.id)
    if completed:
        stmt = stmt.where(Review.completed_at.is_not(None))
    return db.scalars(stmt.order_by(Review.created_at.desc())).first()


def run_review(
    db: Session, ai: AIClient | None, settings: Settings, project: Project, approved_ids: set[str], at: datetime
) -> tuple[Review, int]:
    """Send the approved items for review. Returns the review and how many drafts were dropped for bad references."""
    if project.status != ProjectStatus.DRAFT:
        raise DomainError("Reviews run before a project starts.")
    found = material(db, project)
    output, call = gate.run(db, ai, settings, project.user_id, REVIEW, found.items, approved_ids, at)
    assert isinstance(output, ReviewOutput)
    sent_refs = {ref for ref in found.refs if _ref_was_sent(ref, approved_ids)}
    drafts, dropped = [], 0
    for kind, entries in (("gap", output.gaps), ("item", output.checklist_items)):
        for entry in entries:
            if entry.source_ref not in sent_refs:
                dropped += 1
                continue
            drafts.append({"kind": kind, "text": entry.text, "why": entry.why, "source_ref": entry.source_ref,
                           "priority": getattr(entry, "priority", "medium")})
    current = latest_requirements(project)
    review = Review(
        user_id=project.user_id,
        project_id=project.id,
        requirements_version=current.version if current else None,
        approved_items=sorted(approved_ids),
        ai_call_id=call.id,
        drafts=drafts,
    )
    db.add(review)
    db.flush()
    for position, q in enumerate(output.questions, start=1):
        review.questions.append(ReviewQuestion(user_id=project.user_id, review_id=review.id, position=position, category=q.category, question=q.question))
    db.flush()
    return review, dropped


def _ref_was_sent(ref: str, approved: set[str]) -> bool:
    prefix = ref.split(":")[0]
    if prefix in ("F", "R"):
        return "history" in approved
    if prefix == "REQ":
        return any(a.startswith("req:") for a in approved)
    if prefix == "L":
        return any(a.startswith("link:") and a.split(":")[1].startswith(ref[2:]) for a in approved)
    if prefix == "S":
        return any(a.startswith("snapshot:") and a.split(":")[1].startswith(ref[2:]) for a in approved)
    return False


def get_review(db: Session, user_id: uuid.UUID, review_id: uuid.UUID) -> Review:
    review = db.get(Review, review_id)
    if review is None or review.user_id != user_id:
        raise NotFound("review not found")
    return review


@dataclass(frozen=True)
class Answer:
    question_id: uuid.UUID
    status: QuestionStatus
    answer: str = ""


def answer_review(review: Review, answers: list[Answer], accepted: list[int], at: datetime) -> None:
    by_id = {q.id: q for q in review.questions}
    for a in answers:
        question = by_id.get(a.question_id)
        if question is None:
            raise DomainError("That question is not part of this review.")
        if a.status == QuestionStatus.ANSWERED and not a.answer.strip():
            raise DomainError("An answered question needs an answer.")
        question.status, question.answer = a.status, a.answer.strip()
    bad = [i for i in accepted if not 0 <= i < len(review.drafts)]
    if bad:
        raise DomainError(f"No draft numbered {bad[0]}.")
    review.accepted_drafts = sorted(set(accepted))
    review.completed_at = at


def candidates(db: Session, project: Project) -> list[Candidate]:
    """Checklist items from the latest completed review: accepted drafts and questions still open."""
    review = latest_review(db, project, completed=True)
    if review is None:
        return []
    found = material(db, project).refs
    result = []
    for index in review.accepted_drafts:
        d = review.drafts[index]
        target = found.get(d["source_ref"])
        priority = Priority(d["priority"])
        source_ref = f"{short_id(review.id)}:{index}"
        if d["kind"] == "item" and target is not None:
            source_type, cited = target
            required = source_type == SourceType.FAILURE and _is_high(db, cited)
            why = f"Review: {d['why']} ({d['source_ref']})"
        else:
            source_type, required = SourceType.GAP, False
            why = f"{'Gap' if d['kind'] == 'gap' else 'Review'}: {d['why']} ({d['source_ref']})"
        result.append(Candidate(source_type, source_ref, d["text"], why, priority, required, _score(priority)))
    for q in review.questions:
        if q.status in (QuestionStatus.OPEN, QuestionStatus.DEFERRED):
            result.append(Candidate(SourceType.QUESTION, str(q.id), f"No answer yet: {q.question}",
                                    f"Review question ({q.category}), {q.status}", Priority.MEDIUM, False, 3.0))
    return result


def _is_high(db: Session, failure_id: str) -> bool:
    failure = db.get(Failure, uuid.UUID(failure_id))
    return failure is not None and failure.severity == Severity.HIGH


def _score(priority: Priority) -> float:
    return {Priority.HIGH: 6.0, Priority.MEDIUM: 3.0, Priority.LOW: 1.0}[priority]
