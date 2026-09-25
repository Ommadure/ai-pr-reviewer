"""Application settings, loaded from environment variables (and backend/.env).

Every setting lives here so the rest of the code never reads os.environ directly.
Secrets are typed as SecretStr so they are masked if a Settings object is ever
printed or logged.
"""

import base64
import binascii
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LLMProviderName = Literal["gemini", "anthropic", "openai", "openai_compatible", "fake"]

# Settings the *server* (api/worker/beat) needs outside of tests. The LLM settings
# are checked separately below; session secrets join in Phase 5.
REQUIRED_OUTSIDE_TESTS: tuple[str, ...] = (
    "database_url",
    "redis_url",
    "github_app_private_key_b64",
    # An empty webhook secret would make every forged webhook "valid".
    "github_webhook_secret",
)


class ReviewSettings(BaseSettings):
    """What the review engine needs: LLM choice, keys, budgets, prices.

    Split out so the CLI and eval harness can run a review from a laptop with
    only an LLM key, no GitHub App or database required.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `FOO=` in .env means "not set", not "set to empty string".
        env_ignore_empty=True,
        extra="ignore",
    )

    llm_provider: LLMProviderName = "gemini"
    llm_model: str = ""
    llm_summary_model: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    # {"model-name": [usd_per_1m_input_tokens, usd_per_1m_output_tokens]}, from
    # the provider's pricing page. JSON in the env var: LLM_PRICING='{"m": [0.1, 0.4]}'.
    llm_pricing: dict[str, tuple[float, float]] = {}
    usd_to_inr: float | None = None

    review_max_files: int = 50
    review_max_input_tokens: int = 60_000
    review_max_chunk_tokens: int = 12_000
    review_max_concurrent_llm_calls: int = 2


class Settings(ReviewSettings):
    """Everything the server processes (api, worker, beat) need."""

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

    sentry_dsn: str = ""

    @model_validator(mode="after")
    def _fail_fast_on_missing_values(self) -> Self:
        if self.app_env == "test":
            return self
        missing = [
            name.upper() for name in REQUIRED_OUTSIDE_TESTS if not _is_set(getattr(self, name))
        ]
        if not (self.github_app_client_id or self.github_app_id):
            missing.append("GITHUB_APP_CLIENT_ID (or GITHUB_APP_ID)")
        # The worker reviews with the LLM (Phase 3+): fail at startup, not on the first PR.
        if self.llm_provider != "fake":
            if not self.llm_model:
                missing.append("LLM_MODEL")
            if self.llm_provider == "gemini" and not _is_set(self.gemini_api_key):
                missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        if self.github_app_private_key_b64.get_secret_value():
            # Decode now so a bad key fails at startup, not on the first webhook.
            _ = self.github_app_private_key_pem
        return self

    @property
    def bot_login(self) -> str:
        """The login GitHub gives our App's bot user, used to ignore our own events."""
        return f"{self.github_app_slug}[bot]"

    @property
    def github_app_jwt_issuer(self) -> str:
        """GitHub recommends the Client ID; the numeric App ID also works."""
        return self.github_app_client_id or self.github_app_id

    @property
    def github_app_private_key_pem(self) -> str:
        """The App's RSA private key, decoded from the one-line base64 env value."""
        try:
            pem = base64.b64decode(self.github_app_private_key_b64.get_secret_value()).decode()
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("GITHUB_APP_PRIVATE_KEY_B64 is not valid base64") from exc
        if "PRIVATE KEY-----" not in pem:
            raise ValueError("GITHUB_APP_PRIVATE_KEY_B64 does not decode to a PEM private key")
        return pem


def _is_set(value: object) -> bool:
    # SecretStr is always truthy, even when empty, so unwrap it before checking.
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
