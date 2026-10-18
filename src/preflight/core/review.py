import hashlib
import re
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
MAX_QUESTIONS = 12


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
        note = f"{ref}\nTitle: {link.title}\nURL: {link.url}\nNote: {link.note}"
        items.append(GateItem(f"link:{link.id}", "link_note", f"Note on {link.type} link: {link.title or link.url}", note))
        approved = [s for s in link.snapshots if s.approved]
        if link.type == LinkType.PUBLIC and approved:
            snap = approved[-1]
            snap_ref = f"S:{short_id(snap.id)}"
            refs[snap_ref] = None
            label = f"Snapshot: {link.title or link.url} v{snap.version} ({len(snap.content.split())} words)"
            items.append(GateItem(f"snapshot:{snap.id}", "snapshot", label, f"{snap_ref}\n{snap.content}", ref=snap_ref))
    return ReviewMaterial(items, refs, sum(link.sensitive for link in links))


def latest_review(db: Session, project: Project, completed: bool = False) -> Review | None:
    stmt = select(Review).where(Review.project_id == project.id)
    if completed:
        stmt = stmt.where(Review.completed_at.is_not(None))
    return db.scalars(stmt.order_by(Review.created_at.desc())).first()


SPLITTABLE = ("requirements", "snapshot")
CITATION = re.compile(r"\b(?:F|R|L|S):[0-9a-f]{8}\b|\bREQ:\d+\b")


def plan_batches(items: list[GateItem], budget_tokens: int) -> list[list[GateItem]]:
    """Pack approved items into calls that each fit the model's context.

    Context items (idea, history, link notes) ride in every call. Long documents are split on line boundaries,
    each part keeping its citation tags, so every reference stays checkable against what that call saw.
    """
    fixed = [i for i in items if i.kind not in SPLITTABLE]
    room = budget_tokens - sum(i.tokens for i in fixed)
    documents = [i for i in items if i.kind in SPLITTABLE]
    if not documents:
        return [fixed]
    if room < 500:
        raise DomainError("The history and notes alone fill the model's context. Approve fewer items or raise the context size.")
    pieces: list[GateItem] = []
    for doc in documents:
        pieces += [doc] if doc.tokens <= room else _split(doc, room)
    batches: list[list[GateItem]] = [[]]
    for piece in pieces:
        if batches[-1] and sum(p.tokens for p in batches[-1]) + piece.tokens > room:
            batches.append([])
        batches[-1].append(piece)
    return [fixed + batch for batch in batches]


def _split(doc: GateItem, room: int) -> list[GateItem]:
    header = f"{doc.ref} (continued)\n" if doc.ref else ""
    limit = (room - len(header) // gate.CHARS_PER_TOKEN - 1) * gate.CHARS_PER_TOKEN
    parts, current = [], ""
    for line in doc.text.splitlines(keepends=True):
        while len(line) > limit:
            parts.append(current + line[: limit - len(current)])
            line, current = line[limit - len(current):], ""
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    total = len(parts)
    return [
        GateItem(f"{doc.id}#{n}", doc.kind, f"{doc.label}, part {n} of {total}",
                 part if n == 1 or not header else header + part, ref=doc.ref)
        for n, part in enumerate(parts, start=1)
    ]


def run_review(
    db: Session, ai: AIClient | None, settings: Settings, project: Project, approved_ids: set[str], at: datetime
) -> tuple[Review, int]:
    """Send the approved items for review, in as many calls as the context needs.

    Returns the review and how many drafts were dropped because they cited something their call did not see.
    """
    if project.status == ProjectStatus.CLOSED:
        raise DomainError("A closed project cannot be reviewed.")
    if ai is None:
        raise gate.AIUnavailable("AI is not configured on the server. Set PREFLIGHT_ANTHROPIC_API_KEY or run Ollama.")
    found = material(db, project)
    payload = gate.build_payload(found.items, approved_ids)
    questions: dict[str, tuple[str, str]] = {}
    drafts, dropped, call_ids = [], 0, []
    for batch in plan_batches(payload, gate.input_budget(ai, REVIEW)):
        output, call = gate.run(db, ai, settings, project.user_id, REVIEW, batch, {i.id for i in batch}, at)
        assert isinstance(output, ReviewOutput)
        call_ids.append(str(call.id))
        seen = set(CITATION.findall("\n".join(i.text for i in batch)))
        for q in output.questions:
            questions.setdefault(" ".join(q.question.lower().split()), (q.category, q.question))
        for kind, entries in (("gap", output.gaps), ("item", output.checklist_items)):
            for entry in entries:
                if entry.source_ref not in seen or entry.source_ref not in found.refs:
                    dropped += 1
                    continue
                drafts.append({"kind": kind, "text": entry.text, "why": entry.why, "source_ref": entry.source_ref,
                               "priority": getattr(entry, "priority", "medium")})
    current = latest_requirements(project)
    review = Review(
        user_id=project.user_id,
        project_id=project.id,
        requirements_version=current.version if current else None,
        material_hash=material_hash(found),
        approved_items=sorted(approved_ids),
        ai_call_ids=call_ids,
        drafts=drafts,
    )
    db.add(review)
    db.flush()
    for position, (category, question) in enumerate(list(questions.values())[:MAX_QUESTIONS], start=1):
        review.questions.append(ReviewQuestion(user_id=project.user_id, review_id=review.id, position=position, category=category, question=question))
    db.commit()
    return review, dropped


def material_hash(found: ReviewMaterial) -> str:
    """Changes whenever anything that could be sent changes: requirements, links, notes, snapshots, history."""
    digest = hashlib.sha256()
    for item in sorted(gate.offerable(found.items), key=lambda i: i.id):
        digest.update(f"{item.id}\0{item.text}\0".encode())
    return digest.hexdigest()


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
