"""Application settings, loaded from environment variables (and backend/.env).

Every setting lives here so the rest of the code never reads os.environ directly.
Secrets are typed as SecretStr so they are masked if a Settings object is ever
printed or logged.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LLMProviderName = Literal["gemini", "anthropic", "openai", "openai_compatible", "fake"]

# Settings that must be non-empty outside of tests. Later phases extend this
# (GitHub App credentials in Phase 1, LLM key in Phase 2, session secrets in Phase 5).
REQUIRED_OUTSIDE_TESTS: tuple[str, ...] = ("database_url", "redis_url")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `FOO=` in .env means "not set", not "set to empty string".
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: AppEnv = "development"
    app_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    database_url: str = ""
    redis_url: str = ""

    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: SecretStr = SecretStr("")
    github_app_private_key_b64: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    github_app_slug: str = "reviewpilot-om"

    session_secret: SecretStr = SecretStr("")
    encryption_key: SecretStr = SecretStr("")

    llm_provider: LLMProviderName = "gemini"
    llm_model: str = ""
    llm_summary_model: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    usd_to_inr: float | None = None

    review_max_files: int = 50
    review_max_input_tokens: int = 60_000
    review_max_chunk_tokens: int = 12_000
    review_max_concurrent_llm_calls: int = 2

    sentry_dsn: str = ""

    @model_validator(mode="after")
    def _fail_fast_on_missing_values(self) -> Self:
        if self.app_env == "test":
            return self
        missing = [name.upper() for name in REQUIRED_OUTSIDE_TESTS if not getattr(self, name)]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        return self

    @property
    def bot_login(self) -> str:
        """The login GitHub gives our App's bot user, used to ignore our own events."""
        return f"{self.github_app_slug}[bot]"


@lru_cache
def get_settings() -> Settings:
    return Settings()
