import uuid

from fastapi import APIRouter

from preflight.core import patterns
from preflight.server.api.v1.schemas import (
    PatternOut,
    PatternSuggestion,
    RuleIn,
    RuleOut,
    RulePatch,
)
from preflight.server.deps import CurrentPrincipal, Db

router = APIRouter(tags=["rules"])


@router.get("/patterns")
def list_patterns(principal: CurrentPrincipal, db: Db) -> list[PatternSuggestion]:
    found = patterns.detect_patterns(db, principal.user.id)
    db.commit()
    result = []
    for pattern in found:
        rule = patterns.live_rule(db, pattern)
        draft = None if rule else patterns.draft_rule_text(patterns.pattern_failures(db, pattern))
        result.append(PatternSuggestion(pattern=PatternOut.model_validate(pattern), suggested_rule=draft, rule_id=rule.id if rule else None))
    return result


@router.get("/rules")
def list_rules(principal: CurrentPrincipal, db: Db) -> list[RuleOut]:
    return [RuleOut.model_validate(r) for r in patterns.list_rules(db, principal.user.id)]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, principal: CurrentPrincipal, db: Db) -> RuleOut:
    pattern = patterns.get_pattern(db, principal.user.id, body.pattern_id)
    rule = patterns.approve_rule(db, pattern, body.checklist_text)
    db.commit()
    return RuleOut.model_validate(rule)


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: uuid.UUID, body: RulePatch, principal: CurrentPrincipal, db: Db) -> RuleOut:
    rule = patterns.get_rule(db, principal.user.id, rule_id)
    if body.checklist_text is not None:
        rule.checklist_text = body.checklist_text.strip()
    if body.status is not None:
        rule.status = body.status
    db.commit()
    return RuleOut.model_validate(rule)
