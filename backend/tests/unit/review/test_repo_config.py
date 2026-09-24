from app.config.repo_config import MAX_CONFIG_BYTES, RepoConfig, parse_repo_config


def test_missing_or_empty_file_means_defaults() -> None:
    for raw in (None, "", "   \n"):
        parsed = parse_repo_config(raw)
        assert parsed.is_valid and parsed.config == RepoConfig()


def test_full_example_from_the_spec() -> None:
    raw = """
version: 1
enabled: true
review_drafts: false
min_severity: medium
min_confidence: 0.7
max_comments: 10
focus: [bug, security]
ignore_paths:
  - "docs/**"
custom_rules:
  - "We use Pydantic v2; flag v1-style validators."
summary_language: en
"""
    parsed = parse_repo_config(raw)
    assert parsed.is_valid and not parsed.warnings
    config = parsed.config
    assert (config.min_severity, config.min_confidence, config.max_comments) == ("medium", 0.7, 10)
    assert config.focus == ["bug", "security"]
    assert config.custom_rules == ["We use Pydantic v2; flag v1-style validators."]


def test_unknown_keys_are_warnings_not_errors() -> None:
    parsed = parse_repo_config("max_comments: 5\nfavourite_colour: blue\n")
    assert parsed.is_valid
    assert parsed.config.max_comments == 5
    assert parsed.warnings == ["unknown key 'favourite_colour' ignored"]


def test_invalid_values_fall_back_to_defaults_with_errors() -> None:
    parsed = parse_repo_config("min_severity: apocalyptic\nmax_comments: -3\n")
    assert not parsed.is_valid
    assert parsed.config == RepoConfig()
    assert any(error.startswith("min_severity") for error in parsed.errors)
    assert any(error.startswith("max_comments") for error in parsed.errors)


def test_yaml_syntax_error() -> None:
    parsed = parse_repo_config("focus: [bug\n")
    assert not parsed.is_valid and "YAML syntax error" in parsed.errors[0]


def test_non_mapping_top_level() -> None:
    assert not parse_repo_config("- just\n- a list\n").is_valid


def test_unsafe_yaml_tags_are_rejected() -> None:
    # yaml.load could run this; safe_load refuses it.
    parsed = parse_repo_config("enabled: !!python/object/apply:os.system ['echo pwned']\n")
    assert not parsed.is_valid


def test_size_and_rule_limits() -> None:
    assert not parse_repo_config("x" * (MAX_CONFIG_BYTES + 1)).is_valid
    too_many = "custom_rules:\n" + "".join(f"  - rule {i}\n" for i in range(21))
    assert not parse_repo_config(too_many).is_valid
    too_long = f"custom_rules:\n  - {'y' * 301}\n"
    assert not parse_repo_config(too_long).is_valid
