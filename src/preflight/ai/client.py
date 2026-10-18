"""The only module that imports the Anthropic SDK. Everything else goes through preflight.ai.gate."""

from dataclasses import dataclass
from typing import Protocol

import anthropic

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


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    price_in, price_out, price_cache = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (
        input_tokens * price_in + cache_write_tokens * price_in * 1.25 + cache_read_tokens * price_cache + output_tokens * price_out
    ) / 1_000_000


class AIClient(Protocol):
    def complete(self, *, model: str, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion: ...


class AnthropicClient:
    def __init__(self, api_key: str, base_url: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=2, timeout=120)

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
