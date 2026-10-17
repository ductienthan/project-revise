import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core.patterns import REPEAT_THRESHOLD, detect_patterns
from preflight.core.projects import latest_requirements
from preflight.db.models import (
    ChecklistItem,
    Failure,
    ItemStatus,
    Pattern,
    Priority,
    Project,
    Rule,
    RuleStatus,
    Severity,
    SourceType,
)

MAX_FAILURE_ITEMS = 15
SEVERITY_WEIGHT = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}
PRIORITY_ORDER = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}


@dataclass(frozen=True)
class Candidate:
    source_type: SourceType
    source_ref: str
    text: str
    why: str
    priority: Priority
    required: bool
    score: float


def short_id(value: uuid.UUID | str) -> str:
    return str(value)[:8]


def priority_for(score: float) -> Priority:
    if score >= 4:
        return Priority.HIGH
    if score >= 2.5:
        return Priority.MEDIUM
    return Priority.LOW


def check_text(failure: Failure) -> str:
    return failure.prevention.strip() or f"Guard against a repeat of: {failure.title}"


def failure_candidates(failures: list[Failure], repeats: dict, covered_categories: set) -> list[Candidate]:
    """One item per distinct prevention check. Categories covered by a live rule are left to the rule."""
    by_text: dict[str, list[Failure]] = {}
    for f in failures:
        if f.category in covered_categories:
            continue
        by_text.setdefault(check_text(f).lower().rstrip("."), []).append(f)

    candidates = []
    for group in by_text.values():
        lead = max(group, key=lambda f: (SEVERITY_WEIGHT[f.severity], f.minutes_lost))
        repeat = repeats.get(lead.category, 0)
        score = SEVERITY_WEIGHT[lead.severity] + repeat + sum(f.minutes_lost for f in group) / 30 + (len(group) - 1)
        required = lead.severity == Severity.HIGH or repeat >= REPEAT_THRESHOLD or len(group) >= REPEAT_THRESHOLD
        reasons = [f"Failure #{short_id(lead.id)}: {lead.title}"]
        if len(group) > 1:
            reasons.append(f"seen {len(group)} times")
        if lead.minutes_lost:
            reasons.append(f"{sum(f.minutes_lost for f in group)} min lost")
        candidates.append(
            Candidate(
                SourceType.FAILURE,
                str(lead.id),
                check_text(lead),
                ", ".join(reasons),
                priority_for(score),
                required,
                score,
            )
        )
    candidates.sort(key=lambda c: (-c.required, -c.score))
    return [c for i, c in enumerate(candidates) if c.required or i < MAX_FAILURE_ITEMS]


def rule_candidates(rules: list[tuple[Rule, Pattern]]) -> list[Candidate]:
    return [
        Candidate(
            SourceType.RULE,
            str(rule.id),
            rule.checklist_text,
            f"Rule from pattern: {pattern.label}" + (" (needs revision)" if rule.status == RuleStatus.NEEDS_REVISION else ""),
            Priority.HIGH,
            rule.status == RuleStatus.NEEDS_REVISION,
            10.0,
        )
        for rule, pattern in rules
        if rule.status != RuleStatus.RETIRED
    ]


def order(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda c: (PRIORITY_ORDER[c.priority], -c.required, -c.score))


def build_checklist(db: Session, project: Project, at: datetime, extra: list[Candidate] = ()) -> list[ChecklistItem]:
    """Rebuild the project's items from history. Statuses of items that survive the rebuild are kept."""
    patterns = detect_patterns(db, project.user_id)
    repeats = {p.category: p.failure_count for p in patterns if p.project_scope is None}
    rules = db.execute(
        select(Rule, Pattern).join(Pattern, Rule.pattern_id == Pattern.id).where(Rule.user_id == project.user_id)
    ).all()
    covered = {pattern.category for rule, pattern in rules if rule.status != RuleStatus.RETIRED}
    history = list(db.scalars(select(Failure).where(Failure.user_id == project.user_id, Failure.project_id != project.id)))

    candidates = order(rule_candidates(rules) + failure_candidates(history, repeats, covered) + list(extra))
    existing = {(i.source_type, i.source_ref): i for i in project.checklist_items}
    keep = set()
    for position, c in enumerate(candidates, start=1):
        item = existing.get((c.source_type, c.source_ref))
        if item is None:
            item = ChecklistItem(user_id=project.user_id, project_id=project.id, source_type=c.source_type, source_ref=c.source_ref)
            project.checklist_items.append(item)
        item.position, item.text, item.why, item.priority, item.required = position, c.text, c.why, c.priority, c.required
        keep.add((c.source_type, c.source_ref))

    next_position = len(candidates) + 1
    for key, item in existing.items():
        if key in keep:
            continue
        if item.status == ItemStatus.NOT_CHECKED:
            project.checklist_items.remove(item)
        else:
            item.position, next_position = next_position, next_position + 1

    current = latest_requirements(project)
    project.checklist_requirements_version = current.version if current else None
    project.checklist_built_at = at
    db.flush()
    project.checklist_items.sort(key=lambda i: i.position)
    return project.checklist_items


def to_markdown(project: Project) -> str:
    lines = [f"# Checklist: {project.name}", ""]
    if project.checklist_requirements_version:
        lines += [f"Built from requirements v{project.checklist_requirements_version}.", ""]
    for item in project.checklist_items:
        box = "x" if item.status == ItemStatus.DONE else " "
        flag = "required" if item.required else "optional"
        lines.append(f"- [{box}] **{item.position}.** {item.text} _({flag}, {item.priority}; {item.why})_")
        if item.status in (ItemStatus.NOT_APPLICABLE, ItemStatus.DEFERRED):
            until = f" until {item.revisit_date}" if item.revisit_date else ""
            lines.append(f"  - {item.status}{until}: {item.reason}")
    return "\n".join(lines) + "\n"
