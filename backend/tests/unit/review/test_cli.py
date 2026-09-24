import json
from pathlib import Path

import pytest

from app.review.cli import main

DIFFS = Path(__file__).parents[2] / "fixtures" / "diffs"


@pytest.fixture(autouse=True)
def _no_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI reads ./.env; run from an empty dir so a developer's real keys don't leak in.
    monkeypatch.chdir(tmp_path)


def test_cli_reviews_a_patch_offline(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(DIFFS / "sample.patch"), "--provider", "fake"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "0 comment(s)" in out
    assert "2/6 files reviewed" in out
    assert "web/package-lock.json  (ignored_path)" in out


def test_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(DIFFS / "clean.patch"), "--provider", "fake", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["files_reviewed"] == 1 and result["prompt_version"] == "v1"


def test_cli_explains_missing_llm_configuration(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    exit_code = main([str(DIFFS / "clean.patch"), "--provider", "gemini", "--model", "m"])
    assert exit_code == 2
    assert "GEMINI_API_KEY is not set" in capsys.readouterr().err
