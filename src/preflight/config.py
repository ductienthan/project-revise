from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREFLIGHT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./preflight.db"
    secret_key: str = "dev-only"
    anthropic_api_key: str | None = None
    api_url: str = "http://localhost:8000"

    require_https: bool = False
    session_idle_minutes: int = 60 * 12
    access_token_minutes: int = 60
    device_code_minutes: int = 10
    device_poll_seconds: int = 5
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    rate_limit_per_minute: int = 20
