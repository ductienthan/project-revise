from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server settings, read from PREFLIGHT_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="PREFLIGHT_", env_file=".env", extra="ignore")

    database_url: str = Field("sqlite:///./preflight.db", description="SQLAlchemy URL of the database. Use Postgres in production.")
    ai_provider: Literal["auto", "anthropic", "ollama", "off"] = Field(
        "auto", description="Which AI to use. `auto` uses Anthropic when a key is set, otherwise local Ollama."
    )
    anthropic_api_key: str | None = Field(None, description="Anthropic API key. Never stored in the database.")
    anthropic_base_url: str | None = Field(None, description="Override the Anthropic API URL. Tests point it at a local fake.")
    review_model: str = Field("claude-opus-5-5", description="Anthropic model for project reviews.")
    small_model: str = Field("claude-haiku-5-5", description="Anthropic model for classification, rule drafting, and drill grading.")
    ai_monthly_budget_usd: float = Field(
        5.0, description="Refuse an AI call when its worst-case cost would pass this month's logged spend."
    )
    ollama_url: str = Field("http://127.0.0.1:11434", description="Address of the Ollama server used when there is no Anthropic key.")
    ollama_model: str | None = Field(None, description="Ollama model for reviews. Empty means the largest installed model.")
    ollama_small_model: str | None = Field(None, description="Ollama model for the smaller tasks. Empty means the review model.")
    ollama_context_tokens: int = Field(32_768, description="Context size requested from Ollama. Long material is split to fit it.")

    capture_allow_private: bool = Field(False, description="Allow capturing private and loopback addresses. For local testing only.")
    capture_browser: bool = Field(False, description="Render JavaScript pages in headless Chromium. Needs the `browser` extra.")
    retrospective_required: bool = Field(True, description="Require a retrospective summary to close a project.")
    rule_observation_days: int = Field(28, description="Days a rule is observed before it gets a verdict.")
    require_https: bool = Field(False, description="Redirect or refuse plain HTTP, and mark cookies Secure. Set it in production.")
    session_idle_minutes: int = Field(60 * 12, description="Dashboard sessions end after this much inactivity.")
    access_token_minutes: int = Field(60, description="Lifetime of a CLI access token. The CLI refreshes it.")
    device_code_minutes: int = Field(10, description="How long a `preflight login` code stays valid.")
    device_poll_seconds: int = Field(5, description="Minimum gap between CLI polls during device login.")
    max_failed_logins: int = Field(5, description="Failed logins from one address before that address is locked out.")
    lockout_minutes: int = Field(15, description="How long a lockout lasts.")
    rate_limit_per_minute: int = Field(20, description="Login and token requests allowed per client address per minute.")
