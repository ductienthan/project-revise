from preflight.ai.client import AIClient, AnthropicClient
from preflight.ai.ollama import OllamaClient
from preflight.config import Settings


def make_client(settings: Settings) -> AIClient | None:
    """Anthropic when a key is set, else local Ollama when enabled. None means AI features are off."""
    provider = settings.ai_provider
    if provider == "off":
        return None
    if provider in ("auto", "anthropic") and settings.anthropic_api_key:
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_base_url, settings.review_model, settings.small_model)
    if provider in ("auto", "ollama"):
        return OllamaClient(settings.ollama_url, settings.ollama_model, settings.ollama_small_model, settings.ollama_context_tokens)
    return None
