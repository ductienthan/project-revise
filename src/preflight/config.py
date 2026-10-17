from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREFLIGHT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./preflight.db"
    secret_key: str = "dev-only"
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    review_model: str = "claude-opus-5-5"
    small_model: str = "claude-haiku-5-5"
    ai_monthly_budget_usd: float = 5.0
    api_url: str = "http://localhost:8000"

    capture_allow_private: bool = False
    require_https: bool = False
    session_idle_minutes: int = 60 * 12
    access_token_minutes: int = 60
    device_code_minutes: int = 10
    device_poll_seconds: int = 5
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    rate_limit_per_minute: int = 20
