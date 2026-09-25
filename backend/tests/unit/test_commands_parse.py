import pytest

from app.services.commands import help_text, parse_command


@pytest.mark.parametrize(
    ("body", "command"),
    [
        ("/reviewpilot review", "review"),
        ("  /ReviewPilot   Summary  \nthanks!", "summary"),
        ("\n\n/reviewpilot pause", "pause"),
        ("/reviewpilot", "help"),  # bare command → help
        ("/reviewpilot dance", "dance"),  # unknown, answered with help by the worker
        ("Nice work! /reviewpilot review", None),  # must be at the start of the first line
        ("/reviewpilotreview", None),
        ("", None),
    ],
)
def test_parse_command(body: str, command: str | None) -> None:
    assert parse_command(body) == command


def test_help_lists_every_command_and_links_docs() -> None:
    text = help_text("https://example.test/docs")
    for name in ("review", "summary", "pause", "resume", "help"):
        assert f"`/reviewpilot {name}`" in text
    assert "(https://example.test/docs)" in text
