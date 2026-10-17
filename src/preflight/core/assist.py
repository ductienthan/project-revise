from datetime import datetime

from sqlalchemy.orm import Session

from preflight.ai import gate
from preflight.ai.client import AIClient
from preflight.ai.gate import GateItem
from preflight.ai.prompts.small import CLASSIFY, DRAFT_RULE
from preflight.ai.schemas import ClassifyOutput, RuleOutput
from preflight.config import Settings
from preflight.core.patterns import pattern_failures
from preflight.db.models import Failure, Pattern


def failure_item(f: Failure) -> GateItem:
    text = f"Title: {f.title}\nWhat happened: {f.what_happened}\nFirst guess: {f.guessed_cause}\nFix: {f.fix}"
    return GateItem(f"failure:{f.id}", "failure", f"Failure: {f.title}", text)


def suggest_classification(db: Session, ai: AIClient | None, settings: Settings, failure: Failure, at: datetime) -> ClassifyOutput:
    item = failure_item(failure)
    output, _ = gate.run(db, ai, settings, failure.user_id, CLASSIFY, [item], {item.id}, at)
    return output


def draft_rule(db: Session, ai: AIClient | None, settings: Settings, pattern: Pattern, at: datetime) -> RuleOutput:
    items = [failure_item(f) for f in pattern_failures(db, pattern)]
    output, _ = gate.run(db, ai, settings, pattern.user_id, DRAFT_RULE, items, {i.id for i in items}, at)
    return output
