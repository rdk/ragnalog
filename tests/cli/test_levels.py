"""Level filters: one parser (``store.levels``) for the CLI, the web API and ``LogSet``."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def pe(jcli: Invoke, tmp_path: Path) -> str:
    """mini.log behind two lines without a header: one ``parse_error`` entry (level 0)."""
    log = tmp_path / "pe" / "atlassian-jira.log"
    log.parent.mkdir()
    log.write_text("preamble\nmore preamble\n" + MINI_LOG.read_text(encoding="utf-8"), "utf-8")
    assert jcli("ingest", log, "--name", "pe").code == 0
    return "pe"


def test_unknown_level_finds_parse_errors(jcli: Invoke, pe: str) -> None:
    doc = jcli("entries", pe, "--level", "UNKNOWN", "--columns", "level,parse_error").json()
    assert doc["count"] == 1 and doc["items"][0]["level"] == 0
    assert doc["items"][0]["parse_error"]
    both = jcli("entries", pe, "--level", "unknown,Error", "--limit", "0").json()
    assert both["count"] == 1 + jcli("entries", pe, "--level", "ERROR").json()["count"]


def test_blank_level_is_unset(jcli: Invoke, pe: str) -> None:
    total = jcli("entries", pe, "--limit", "0").json()["total"]
    assert jcli("entries", pe, "--level", "", "--limit", "0").json()["total"] == total
    assert jcli("entries", pe, "--min-level", " ", "--limit", "0").json()["total"] == total
    assert jcli("groups", pe, "--level", "").json()["total"] == jcli("groups", pe).json()["total"]


@pytest.mark.parametrize("name", ["SEVERE", "loud", "FINE"])
def test_unknown_level_names(jcli: Invoke, pe: str, name: str) -> None:
    res = jcli("entries", pe, "--level", name)
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_level"
    assert err["message"] == (
        f"unknown level {name!r}; expected one of TRACE, DEBUG, INFO, WARN, ERROR, FATAL, "
        "UNKNOWN (WARNING = WARN)"
    )


def test_warning_alias(jcli: Invoke, pe: str) -> None:
    warn = jcli("entries", pe, "--level", "WARN").json()["items"]
    assert warn and jcli("entries", pe, "--level", "warning").json()["items"] == warn
