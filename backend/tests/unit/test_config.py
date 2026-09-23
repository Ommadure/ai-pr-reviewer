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
