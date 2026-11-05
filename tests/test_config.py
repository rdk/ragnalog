"""Settings (``config.py``): flags over ``ATLAS_LOG_*`` over defaults, and the docs' table."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from atlas_log_analyzer.config import DEFAULT_STORE, ENV_VARS, Settings, parse_packages

ROOT = Path(__file__).resolve().parents[1]


def env_table() -> str:
    """The environment table of docs/cli.md, rendered from ENV_VARS."""
    rows = [f"| `{v.name}` | {v.default} | {v.help} |" for v in ENV_VARS]
    return "\n".join(["| Variable | Default | Meaning |", "|---|---|---|", *rows])


def test_docs_table_matches_env_vars() -> None:
    expected = env_table()
    assert expected in (ROOT / "docs" / "cli.md").read_text(encoding="utf-8"), expected


def test_defaults() -> None:
    s = Settings.load()
    assert s == Settings() and s.store == DEFAULT_STORE and s.implicit_store
    assert (s.token, s.max_upload_mb, s.report_cache, s.app_packages, s.debug) == (
        None,
        2048,
        8,
        None,
        False,
    )


def test_env_then_flags() -> None:
    env = {
        "ATLAS_LOG_STORE": " /data/store ",
        "ATLAS_LOG_TOKEN": "t",
        "ATLAS_LOG_MAX_UPLOAD_MB": "7",
        "ATLAS_LOG_REPORT_CACHE": "2",
        "ATLAS_LOG_APP_PACKAGES": " org.acme. , com.example.,",
        "ATLAS_LOG_DEBUG": "1",
    }
    s = Settings.load(env=env)
    assert s.store == Path("/data/store") and not s.implicit_store
    assert (s.token, s.max_upload_mb, s.report_cache, s.debug) == ("t", 7, 2, True)
    assert s.app_packages == ("org.acme.", "com.example.")
    flagged = Settings.load({"store": Path("x"), "token": "f", "max_upload_mb": None}, env)
    assert (flagged.store, flagged.token, flagged.max_upload_mb) == (Path("x"), "f", 7)


def test_blank_variables_are_unset() -> None:
    env = {var.name: "  " for var in ENV_VARS}
    assert Settings.load(env=env) == Settings()
    assert Settings.load(env=env).implicit_store


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"ATLAS_LOG_MAX_UPLOAD_MB": "lots"}, "ATLAS_LOG_MAX_UPLOAD_MB='lots' is not an integer"),
        ({"ATLAS_LOG_REPORT_CACHE": "0"}, "report_cache must be >= 1"),
    ],
)
def test_bad_numbers(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.load(env=env)


def test_to_env_round_trip(tmp_path: Path) -> None:
    s = Settings(store=tmp_path, token="x", app_packages=("a.",), debug=True, max_upload_mb=3)
    assert Settings.load(env=s.to_env()) == s
    assert {var.name for var in ENV_VARS} == set(s.to_env())


def test_parse_packages() -> None:
    assert parse_packages(None) is None and parse_packages(" , ") is None
    assert parse_packages("a.,b.") == ("a.", "b.")


def test_config_is_stdlib_only() -> None:
    script = (
        "import json, sys; import atlas_log_analyzer.config\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('atlas_log_analyzer'))))"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "atlas_log_analyzer",
        "atlas_log_analyzer._version",
        "atlas_log_analyzer.config",
    ]


def test_core_reads_no_environment() -> None:
    core = ROOT / "src" / "atlas_log_analyzer" / "core"
    for path in core.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text and "getenv" not in text, path.name


def test_cli_env_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from atlas_log_analyzer.cli.app import run

    monkeypatch.setenv("ATLAS_LOG_REPORT_CACHE", "many")
    assert run(["--output", "json", "--store", str(tmp_path), "version"]) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "invalid_value" and "ATLAS_LOG_REPORT_CACHE" in err["message"]
