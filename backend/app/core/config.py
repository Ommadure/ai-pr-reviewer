"""Application settings, loaded from environment variables (and backend/.env).

Every setting lives here so the rest of the code never reads os.environ directly.
Secrets are typed as SecretStr so they are masked if a Settings object is ever
printed or logged.
"""

import base64
import binascii
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LLMProviderName = Literal["gemini", "anthropic", "openai", "openai_compatible", "fake"]

# Settings the *server* (api/worker/beat) needs outside of tests. The LLM settings
# are checked separately below.
REQUIRED_OUTSIDE_TESTS: tuple[str, ...] = (
    "database_url",
    "redis_url",
    "github_app_private_key_b64",
    # An empty webhook secret would make every forged webhook "valid".
    "github_webhook_secret",
    # Dashboard login (GitHub App user authorization) and sessions.
    "github_app_client_id",
    "github_app_client_secret",
    "session_secret",
    "encryption_key",
)
MIN_SESSION_SECRET_LENGTH = 32


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
    review_max_output_tokens: int = Field(default=8_192, ge=256)  # per LLM call
    # Per review run, in USD. Calls whose worst-case cost would cross it are skipped.
    # Only priced models (LLM_PRICING) cost anything, so free tiers never hit it.
    review_max_cost_usd: float | None = Field(default=None, gt=0)


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

    session_secret: SecretStr = SecretStr("")  # signs session + OAuth-state cookies
    encryption_key: SecretStr = SecretStr("")  # Fernet key for stored GitHub user tokens
    session_ttl_days: int = 7
    # Override when the public callback differs from APP_BASE_URL (e.g. a Vercel rewrite).
    oauth_callback_url: str = ""

    sentry_dsn: str = ""  # empty: Sentry off
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

    # Redis command budget. An idle worker's queue poll (BRPOP) and its Flower
    # events are most of its Redis traffic; per-command hosts (Upstash) bill that.
    # BRPOP returns as soon as a task arrives, so a longer poll adds no latency.
    celery_poll_seconds: int = Field(default=1, ge=1, le=60)
    celery_task_events: bool = True  # live task events for Flower; off in production
    # Health URL of an API that sleeps when idle (Render free); the worker pings it.
    keep_warm_url: str = ""

    # Linked from `/reviewpilot help` replies.
    docs_url: str = "https://github.com/Ommadure/ai-pr-reviewer#configuration"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("app_base_url", "frontend_url", "oauth_callback_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        # Paths are appended with a leading "/". A pasted "https://x.vercel.app/"
        # sent GitHub ".app//api/v1/auth/github/callback", which matches no
        # registered callback, so sign-in failed with "redirect_uri is not associated".
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def _fail_fast_on_missing_values(self) -> Self:
        if self.app_env == "test":
            return self
        missing = [
            name.upper() for name in REQUIRED_OUTSIDE_TESTS if not _is_set(getattr(self, name))
        ]
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
        if len(self.session_secret.get_secret_value()) < MIN_SESSION_SECRET_LENGTH:
            raise ValueError(
                f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters"
            )
        try:
            Fernet(self.encryption_key.get_secret_value().encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("ENCRYPTION_KEY is not a valid Fernet key") from exc
        return self

    @property
    def oauth_redirect_uri(self) -> str:
        """Where GitHub sends users back after login; must match the App's callback URL."""
        return self.oauth_callback_url or f"{self.app_base_url}/api/v1/auth/github/callback"

    @property
    def secure_cookies(self) -> bool:
        # Browsers treat http://localhost as a secure context, but plain-http staging
        # hosts would silently drop Secure cookies; so: Secure whenever we serve https.
        return self.app_env == "production" or self.app_base_url.startswith("https://")

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


def normalize_database_url(url: str) -> str:
    """Accept the URL a host hands out (Neon, Render, Heroku-style) as-is.

    `postgres://…?sslmode=require&channel_binding=require` becomes
    `postgresql+asyncpg://…?ssl=require`: asyncpg needs its own driver name, spells the
    TLS option `ssl`, and rejects `channel_binding`.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        return url  # already has a driver (postgresql+asyncpg), or empty
    query = []
    for key, value in parse_qsl(parts.query):
        if key == "sslmode":
            query.append(("ssl", value))
        elif key != "channel_binding":
            query.append((key, value))
    return urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=urlencode(query)))


def _is_set(value: object) -> bool:
    # SecretStr is always truthy, even when empty, so unwrap it before checking.
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
