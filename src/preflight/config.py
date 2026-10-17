from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREFLIGHT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./preflight.db"
    secret_key: str = "dev-only"
    admin_username: str = "admin"
    admin_password: str | None = None
    anthropic_api_key: str | None = None
    api_url: str = "http://localhost:8000"


settings = Settings()
