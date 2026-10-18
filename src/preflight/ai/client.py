"""The Anthropic provider and the provider protocol. Only ai/gate.py calls complete()."""

from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic

Role = Literal["review", "small"]

# (input, output, cache read) in USD per million tokens. Haiku's cache-read rate is assumed at 10% of input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class AIError(Exception):
    pass


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class AIClient(Protocol):
    provider: str
    context_tokens: int

    def model_for(self, role: Role) -> str: ...

    def price(self, model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float: ...

    def available(self) -> bool: ...

    def complete(self, *, model: str, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion: ...


def anthropic_price(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    if model not in PRICES:
        raise AIError(f"No price is known for {model}, so its cost cannot be checked against the budget.")
    price_in, price_out, price_cache = PRICES[model]
    return (input_tokens * price_in + cache_write * price_in * 1.25 + cache_read * price_cache + output_tokens * price_out) / 1_000_000


class AnthropicClient:
    provider = "anthropic"
    context_tokens = 200_000

    def __init__(self, api_key: str, base_url: str | None, review_model: str, small_model: str):
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=2, timeout=120)
        self.models = {"review": review_model, "small": small_model}

    def model_for(self, role: Role) -> str:
        return self.models[role]

    def price(self, model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
        return anthropic_price(model, input_tokens, output_tokens, cache_read, cache_write)

    def available(self) -> bool:
        return True

    def complete(self, *, model: str, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in SERVER_FALLBACK_MODELS else {}
        try:
            response = self.client.beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                **extra,
            )
        except anthropic.AuthenticationError:
            raise AIError("The Anthropic API key was rejected.") from None
        except anthropic.RateLimitError:
            raise AIError("The Anthropic API is rate limiting this key. Try again shortly.") from None
        except anthropic.APIStatusError as err:
            raise AIError(f"The Anthropic API returned {err.status_code}.") from None
        except anthropic.APIConnectionError:
            raise AIError("Could not reach the Anthropic API.") from None
        except anthropic.APIError as err:
            raise AIError(f"The Anthropic API call failed ({err.__class__.__name__}).") from None
        if response.stop_reason == "refusal":
            raise AIError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise AIError("The reply was cut off at the output limit.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage
        return Completion(
            text=text,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        )

