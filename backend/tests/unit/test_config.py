import base64

import pytest
from pydantic import ValidationError

from app.core.config import ReviewSettings, Settings


def test_test_env_does_not_require_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None, app_env="test")
    assert settings.database_url == ""


def test_non_test_env_fails_fast_when_required_values_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL, REDIS_URL"):
        Settings(_env_file=None, app_env="development")


def test_empty_env_values_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USD_TO_INR", "")
    settings = Settings(_env_file=None, app_env="test")
    assert settings.usd_to_inr is None


def test_secrets_are_masked_when_printed() -> None:
    settings = Settings(_env_file=None, app_env="test", github_webhook_secret="s3cr3t")
    assert "s3cr3t" not in repr(settings)
    assert settings.github_webhook_secret.get_secret_value() == "s3cr3t"


def test_bot_login_derived_from_slug() -> None:
    settings = Settings(_env_file=None, app_env="test", github_app_slug="reviewpilot-om")
    assert settings.bot_login == "reviewpilot-om[bot]"


def _complete_dev_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "development",
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "redis_url": "redis://localhost",
        "github_app_client_id": "Iv23liTestClientId",
        "github_app_client_secret": "client-secret",
        "session_secret": "s" * 32,
        "encryption_key": "Hgl4Jz2Xuv7w5Xc4h8QKZg3VJvG1H8m1nX2ycZ3Y0zE=",
        "github_app_private_key_b64": base64.b64encode(
            b"-----BEGIN RSA PRIVATE KEY-----\n..."
        ).decode(),
        "github_webhook_secret": "whsec",
        "llm_provider": "gemini",
        "llm_model": "gemini-test",
        "gemini_api_key": "test-key",
    }
    return {**values, **overrides}


def test_complete_settings_are_accepted() -> None:
    Settings(**_complete_dev_settings())  # type: ignore[arg-type]


def test_empty_secret_counts_as_missing() -> None:
    # SecretStr("") is truthy in Python; the check must look inside it.
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET"):
        Settings(**_complete_dev_settings(github_webhook_secret=""))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_key",
    ["not base64 at all!", base64.b64encode(b"just some text").decode()],
)
def test_bad_private_key_fails_at_startup(bad_key: str) -> None:
    with pytest.raises(ValidationError, match="GITHUB_APP_PRIVATE_KEY_B64"):
        Settings(**_complete_dev_settings(github_app_private_key_b64=bad_key))  # type: ignore[arg-type]


def test_jwt_issuer_prefers_client_id_but_accepts_app_id() -> None:
    assert Settings(**_complete_dev_settings()).github_app_jwt_issuer == "Iv23liTestClientId"  # type: ignore[arg-type]
    with_app_id = _complete_dev_settings(github_app_client_id="", github_app_id="123")
    assert (
        Settings(_env_file=None, app_env="test", github_app_id="123").github_app_jwt_issuer == "123"
    )
    with pytest.raises(ValidationError, match="GITHUB_APP_CLIENT_ID"):
        Settings(**with_app_id)  # type: ignore[arg-type]  # login needs the client id


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"session_secret": "short"}, "SESSION_SECRET must be at least 32"),
        ({"encryption_key": "not-a-fernet-key"}, "ENCRYPTION_KEY is not a valid Fernet key"),
        ({"github_app_client_secret": ""}, "GITHUB_APP_CLIENT_SECRET"),
    ],
)
def test_login_and_session_secrets_are_validated(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**_complete_dev_settings(**overrides))  # type: ignore[arg-type]


def test_llm_pricing_is_parsed_from_json_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICING", '{"model-a": [0.1, 0.4]}')
    assert ReviewSettings(_env_file=None).llm_pricing == {"model-a": (0.1, 0.4)}


def test_review_settings_need_no_server_credentials() -> None:
    # The CLI/evals run with only an LLM key: no GitHub App, database or Redis.
    ReviewSettings(_env_file=None)


def test_server_needs_llm_model_and_key() -> None:
    with pytest.raises(ValidationError, match="LLM_MODEL, GEMINI_API_KEY"):
        Settings(**_complete_dev_settings(llm_model="", gemini_api_key=""))  # type: ignore[arg-type]
    Settings(**_complete_dev_settings(llm_provider="fake", llm_model="", gemini_api_key=""))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Neon's connection string, pasted as-is (password with an escaped @).
        (
            "postgresql://u:p%40ss@ep-x.ap-southeast-1.aws.neon.tech/neondb"
            "?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p%40ss@ep-x.ap-southeast-1.aws.neon.tech/neondb?ssl=require",
        ),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        # Already asyncpg: untouched.
        ("postgresql+asyncpg://a:b@localhost/x", "postgresql+asyncpg://a:b@localhost/x"),
    ],
)
def test_hosted_database_urls_are_normalized_for_asyncpg(given: str, expected: str) -> None:
    assert Settings(app_env="test", database_url=given).database_url == expected


@pytest.mark.parametrize(
    "given", ["https://x.vercel.app", "https://x.vercel.app/", " https://x.vercel.app// "]
)
def test_public_urls_lose_trailing_slashes(given: str) -> None:
    settings = Settings(app_env="test", app_base_url=given, frontend_url=given)
    assert settings.app_base_url == settings.frontend_url == "https://x.vercel.app"
    assert settings.oauth_redirect_uri == "https://x.vercel.app/api/v1/auth/github/callback"
    explicit = Settings(app_env="test", oauth_callback_url="https://x.vercel.app/cb/")
    assert explicit.oauth_redirect_uri == "https://x.vercel.app/cb"
