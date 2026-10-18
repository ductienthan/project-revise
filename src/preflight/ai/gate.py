"""The approval gate. Nothing reaches the model unless it is offerable, approved for this call, and logged."""

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from preflight.ai.client import AIClient, AIError, cost_usd
from preflight.ai.prompts import Prompt
from preflight.ai.schemas import strict_schema
from preflight.config import Settings
from preflight.core.errors import DomainError
from preflight.db.models import AiCall, User

CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class GateItem:
    id: str
    kind: str
    label: str
    text: str
    sensitive: bool = False
    cacheable: bool = False

    @property
    def tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN + 1


@dataclass(frozen=True)
class Estimate:
    model: str
    input_tokens: int
    max_cost_usd: float


class AIUnavailable(DomainError):
    pass


def offerable(items: list[GateItem]) -> list[GateItem]:
    return [item for item in items if not item.sensitive]


def build_payload(items: list[GateItem], approved_ids: set[str]) -> list[GateItem]:
    offered = {item.id for item in offerable(items)}
    unknown = approved_ids - offered
    if unknown:
        raise DomainError(f"These items cannot be sent: {', '.join(sorted(unknown))}")
    return [item for item in items if item.id in approved_ids]


def model_for(settings: Settings, prompt: Prompt) -> str:
    return settings.review_model if prompt.role == "review" else settings.small_model


def estimate(settings: Settings, prompt: Prompt, items: list[GateItem]) -> Estimate:
    model = model_for(settings, prompt)
    tokens = sum(i.tokens for i in items) + len(prompt.system) // CHARS_PER_TOKEN
    return Estimate(model, tokens, round(cost_usd(model, tokens, prompt.max_tokens), 4))


def spent_this_month(db: Session, user_id: uuid.UUID, at: datetime) -> float:
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = db.scalar(select(func.sum(AiCall.cost_usd)).where(AiCall.user_id == user_id, AiCall.created_at >= start))
    return total or 0.0


def _content(payload: list[GateItem]) -> list[dict]:
    """Cacheable items go first so the repeated history prefix can be served from the prompt cache."""
    ordered = sorted(payload, key=lambda i: not i.cacheable)
    blocks = [
        {"type": "text", "text": f'<{i.kind} id="{i.id}" label="{i.label}">\n{i.text}\n</{i.kind}>'} for i in ordered
    ]
    cached = [n for n, i in enumerate(ordered) if i.cacheable]
    if cached:
        blocks[cached[-1]]["cache_control"] = {"type": "ephemeral"}
    return blocks


def run(
    db: Session,
    ai: AIClient | None,
    settings: Settings,
    user_id: uuid.UUID,
    prompt: Prompt,
    items: list[GateItem],
    approved_ids: set[str],
    at: datetime,
) -> tuple[BaseModel, AiCall]:
    """Send only approved, non-sensitive items. Invalid JSON is retried once, then reported."""
    if ai is None:
        raise AIUnavailable("AI is not configured on the server (PREFLIGHT_ANTHROPIC_API_KEY is not set).")
    payload = build_payload(items, approved_ids)
    if not payload:
        raise DomainError("Nothing was approved, so nothing was sent.")
    call = _reserve(db, settings, user_id, prompt, payload, approved_ids, at)
    schema = strict_schema(prompt.output)
    content = json.loads(call.payload)["content"]
    call.cost_usd = 0.0
    try:
        for _ in range(2):
            completion = ai.complete(model=call.model, system=prompt.system, content=content, schema=schema, max_tokens=prompt.max_tokens)
            call.input_tokens += completion.input_tokens
            call.output_tokens += completion.output_tokens
            call.cost_usd += cost_usd(
                completion.model, completion.input_tokens, completion.output_tokens,
                completion.cache_read_tokens, completion.cache_write_tokens,
            )
            call.response = completion.text
            try:
                result = prompt.output.model_validate_json(completion.text)
            except ValidationError:
                continue
            db.commit()
            return result, call
        raise AIError("The model's reply did not match the expected format twice.")
    except AIError as err:
        call.error = str(err)
    except Exception as err:  # noqa: BLE001
        call.error = f"AI call failed ({err.__class__.__name__})."
    db.commit()
    raise AIUnavailable(call.error)


_reservation_lock = threading.Lock()


def _reserve(
    db: Session,
    settings: Settings,
    user_id: uuid.UUID,
    prompt: Prompt,
    payload: list[GateItem],
    approved_ids: set[str],
    at: datetime,
) -> AiCall:
    """Log the exact payload and hold the call's worst-case cost before anything is sent.

    The row is committed first, so a crash mid-call still leaves the audit record, and concurrent calls see each
    other's reservations when checking the budget.
    """
    worst = estimate(settings, prompt, payload)
    with _reservation_lock:
        db.execute(select(User.id).where(User.id == user_id).with_for_update())
        spent = spent_this_month(db, user_id, at)
        if spent + worst.max_cost_usd > settings.ai_monthly_budget_usd:
            db.rollback()
            raise AIUnavailable(
                f"This call could cost up to ${worst.max_cost_usd:.2f}, which would pass this month's AI budget "
                f"of ${settings.ai_monthly_budget_usd:.2f} (${spent:.2f} used)."
            )
        call = AiCall(
            user_id=user_id,
            kind=prompt.kind,
            prompt_version=prompt.version,
            model=worst.model,
            approved_items=sorted(approved_ids),
            payload=json.dumps({"system": prompt.system, "content": _content(payload)}),
            cost_usd=worst.max_cost_usd,
        )
        db.add(call)
        db.commit()
    return call
