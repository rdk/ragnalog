"""``--help`` coverage and argument-level errors (no core/store implementation needed)."""

from __future__ import annotations

import pytest

from atlas_log_analyzer import __version__

from .conftest import Invoke

COMMANDS = [
    "ingest",
    "sets",
    "groups",
    "show",
    "entries",
    "context",
    "query",
    "stats",
    "diff",
    "triage",
    "parse",
    "regroup",
    "report",
    "serve",
    "formats",
    "schema",
    "remove",
    "version",
]


def test_root_help_lists_every_command_and_globals(cli: Invoke) -> None:
    result = cli("--help")
    assert result.code == 0
    for command in COMMANDS:
        assert f" {command} " in result.stdout, command
    for option in ("--output", "--store", "--quiet", "ATLAS_LOG_STORE"):
        assert option in result.stdout
    assert "Exit codes" in result.stdout


@pytest.mark.parametrize("command", COMMANDS)
def test_command_help_is_complete(cli: Invoke, command: str) -> None:
    result = cli(command, "--help")
    assert result.code == 0
    text = result.stdout
    assert f"Usage: atlas-log {command}" in text
    assert "Exit codes:" in text
    assert "Output" in text
    assert '"schema"' in text or command == "parse"
    assert "--output" in text  # global options shown on every command


def test_help_option_documentation(cli: Invoke) -> None:
    text = cli("groups", "--help").stdout
    for option in ("--level", "--min-level", "--logger", "--since", "--until", "--sort"):
        assert option in text
    assert "--limit" in text and "--coarse" in text
    assert "--min-level" in cli("diff", "--help").stdout
    assert "--raw" in cli("show", "--help").stdout
    assert "--mine" in cli("ingest", "--help").stdout


def test_version_flag(cli: Invoke) -> None:
    result = cli("--version")
    assert result.code == 0
    assert __version__ in result.stdout


def test_version_command_json(cli: Invoke) -> None:
    result = cli("--output", "json", "version")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "version.v1"
    assert doc["version"] == __version__
    assert {"schema_version", "signature_version", "mask_rules_version", "python"} <= doc.keys()


def test_global_options_after_command(cli: Invoke) -> None:
    result = cli("version", "--output", "jsonl")
    assert result.code == 0
    assert len(result.jsonl()) == 1


def test_unknown_command_is_usage_error(cli: Invoke) -> None:
    result = cli("--output", "json", "frobnicate")
    assert result.code == 1
    assert result.json()["error"]["code"] == "usage"


def test_bad_output_mode_is_usage_error(cli: Invoke) -> None:
    result = cli("--output", "xml", "version")
    assert result.code == 1
    assert "error" in result.stdout or "error" in result.stderr


def test_missing_argument_is_usage_error(cli: Invoke) -> None:
    result = cli("--output", "json", "groups")
    assert result.code == 1
    assert result.json() == {
        "schema": "error.v1",
        "error": {"code": "usage", "message": result.json()["error"]["message"]},
    }


def test_invalid_level(cli: Invoke) -> None:
    result = cli("--output", "json", "groups", "someset", "--level", "loud")
    assert result.code == 1
    assert result.json()["error"]["code"] == "invalid_level"


def test_negative_limit(cli: Invoke) -> None:
    result = cli("--output", "json", "entries", "someset", "--limit", "-1")
    assert result.code == 1
    assert result.json()["error"]["code"] == "invalid_value"


def test_ingest_without_paths(cli: Invoke) -> None:
    result = cli("--output", "json", "ingest")
    assert result.code == 1
    assert result.json()["error"]["code"] == "usage"


def test_unknown_schema_name(cli: Invoke) -> None:
    result = cli("--output", "json", "schema", "bogus")
    assert result.code == 1
    assert result.json()["error"]["code"] == "usage"


def test_table_mode_error_goes_to_stderr(cli: Invoke) -> None:
    result = cli("--output", "table", "groups", "someset", "--level", "loud")
    assert result.code == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
