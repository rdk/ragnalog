"""Settings (``config.py``): flags over ``RAGNALOG_*`` over defaults, and the docs' table."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ragnalog.config import DEFAULT_STORE, ENV_VARS, Settings, parse_packages

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
        "RAGNALOG_STORE": " /data/store ",
        "RAGNALOG_TOKEN": "t",
        "RAGNALOG_MAX_UPLOAD_MB": "7",
        "RAGNALOG_REPORT_CACHE": "2",
        "RAGNALOG_APP_PACKAGES": " org.acme. , com.example.,",
        "RAGNALOG_DEBUG": "1",
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
        ({"RAGNALOG_MAX_UPLOAD_MB": "lots"}, "RAGNALOG_MAX_UPLOAD_MB='lots' is not an integer"),
        ({"RAGNALOG_REPORT_CACHE": "0"}, "report_cache must be >= 1"),
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
        "import json, sys; import ragnalog.config\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('ragnalog'))))"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "ragnalog",
        "ragnalog._version",
        "ragnalog.config",
    ]


def test_core_reads_no_environment() -> None:
    core = ROOT / "src" / "ragnalog" / "core"
    for path in core.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text and "getenv" not in text, path.name


def test_cli_env_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ragnalog.cli.app import run

    monkeypatch.setenv("RAGNALOG_REPORT_CACHE", "many")
    assert run(["--output", "json", "--store", str(tmp_path), "version"]) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "invalid_value" and "RAGNALOG_REPORT_CACHE" in err["message"]
