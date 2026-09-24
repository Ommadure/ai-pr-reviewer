import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
        "github_app_private_key_b64": base64.b64encode(
            b"-----BEGIN RSA PRIVATE KEY-----\n..."
        ).decode(),
        "github_webhook_secret": "whsec",
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


def test_either_client_id_or_app_id_identifies_the_app() -> None:
    Settings(**_complete_dev_settings(github_app_client_id="", github_app_id="123"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="GITHUB_APP_CLIENT_ID"):
        Settings(**_complete_dev_settings(github_app_client_id="", github_app_id=""))  # type: ignore[arg-type]
