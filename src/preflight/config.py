from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREFLIGHT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./preflight.db"
    ai_provider: Literal["auto", "anthropic", "ollama", "off"] = "auto"
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    review_model: str = "claude-opus-5-5"
    small_model: str = "claude-haiku-5-5"
    ai_monthly_budget_usd: float = 5.0
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    ollama_small_model: str | None = None
    ollama_context_tokens: int = 32_768

    capture_allow_private: bool = False
    capture_browser: bool = False
    retrospective_required: bool = True
    rule_observation_days: int = 28
    require_https: bool = False
    session_idle_minutes: int = 60 * 12
    access_token_minutes: int = 60
    device_code_minutes: int = 10
    device_poll_seconds: int = 5
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    rate_limit_per_minute: int = 20
