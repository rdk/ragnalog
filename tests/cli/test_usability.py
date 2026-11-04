"""Totals, relative times, hints, table layout and raw-entry highlighting."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_log_analyzer.cli.output import highlight_entry
from tests.cli.conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def _styles(text: object, needle: str) -> set[str]:
    from rich.text import Text

    assert isinstance(text, Text)
    start = text.plain.index(needle)
    end = start + len(needle)
    return {str(s.style) for s in text.spans if s.start <= start and s.end >= end}


def test_groups_total_and_truncated(jcli: Invoke, mini: str) -> None:
    doc = jcli("groups", mini, "--limit", "2").json()
    assert doc["count"] == 2 and doc["total"] > 2 and doc["truncated"] is True
    full = jcli("groups", mini, "--limit", "0").json()
    assert full["count"] == full["total"] == doc["total"] and full["truncated"] is False


def test_entries_total_and_truncated(jcli: Invoke, mini: str) -> None:
    doc = jcli("entries", mini, "--limit", "3").json()
    assert doc["count"] == 3 and doc["total"] == 10 and doc["truncated"] is True
    assert jcli("entries", mini).json()["truncated"] is False


def test_query_and_diff_totals(jcli: Invoke, mini: str) -> None:
    q = jcli("query", mini, "select 1 as x").json()
    assert q["total"] == q["count"] == 1 and q["truncated"] is False
    d = jcli("diff", mini, mini).json()
    assert d["totals"] == {"new": 0, "vanished": 0, "changed": 0} and d["truncated"] is False


def test_relative_since(jcli: Invoke, mini: str) -> None:
    # The last entry is at 09:12 +01:00; 1h before keeps only the 09:0x-09:12 entries.
    recent = jcli("entries", mini, "--since", "1h", "--limit", "0").json()
    assert recent["count"] == 5
    assert jcli("entries", mini, "--since", "1d", "--limit", "0").json()["count"] == 10
    by_date = jcli("entries", mini, "--until", "2026-10-29", "--limit", "0").json()
    assert by_date["count"] == 0
    groups = jcli("groups", mini, "--since", "30m").json()
    assert 0 < groups["count"] < groups["total"] + 10


def test_invalid_time_is_invalid_value(jcli: Invoke, mini: str) -> None:
    res = jcli("groups", mini, "--since", "yesterday")
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_value" and "30m" in err["message"]


def test_unknown_set_hint(jcli: Invoke, mini: str, store_dir: Path) -> None:
    res = jcli("groups", "nope")
    assert res.code == 3
    err = res.json()["error"]
    assert err["message"] == "no log set 'nope'"
    assert "mini" in err["hint"]
    assert err["store"] == store_dir.as_posix()
    assert str(store_dir) not in err["message"]


@pytest.mark.parametrize("command", ["remove", "regroup", "stats", "set"])
def test_unknown_set_same_error_everywhere(
    jcli: Invoke, mini: str, store_dir: Path, command: str
) -> None:
    res = jcli(command, "nope")
    assert res.code == 3
    err = res.json()["error"]
    assert err == {
        "code": "store_error",
        "message": "no log set 'nope'",
        "hint": "available sets: mini",
        "store": store_dir.as_posix(),
    }


def test_unknown_signature_hint(jcli: Invoke, mini: str) -> None:
    res = jcli("show", mini, "0000000000000000")
    assert res.code == 2
    assert "atlas-log groups mini" in res.json()["error"]["hint"]


def test_table_error_hint_on_stderr(cli: Invoke, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "groups", "nope")
    assert res.code == 3
    assert "hint:" in res.stderr


def test_ingest_table_next_line(cli: Invoke, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "ingest", MINI_LOG, "--name", "m")
    assert res.code == 0
    assert "next: atlas-log groups m" in res.stderr


def test_show_table_header(cli: Invoke, mini: str, store_dir: Path, jcli: Invoke) -> None:
    sig = jcli("groups", mini, "--level", "ERROR", "--limit", "1").json()["items"][0]["signature"]
    res = cli("--output", "table", "--store", store_dir, "show", mini, sig)
    lines = res.stdout.splitlines()
    assert lines[0].startswith("Script field failed on issue: <ISSUE>")
    assert lines[1].startswith("logger ") and "level ERROR" in lines[1]
    assert lines[2].startswith("count 3")


def test_diff_table_none(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "diff", mini, mini)
    assert "vanished: none" in res.stdout and "new: none" in res.stdout


def test_stats_table_compact_hours(cli: Invoke, mini: str, store_dir: Path, jcli: Invoke) -> None:
    res = cli("--output", "table", "--store", store_dir, "stats", mini)
    assert "hours 00-23" in res.stdout and "2026-10-29" in res.stdout
    assert len(jcli("stats", mini).json()["hours"]) >= 2


def test_width_option(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--output", "table", "--width", "100", "--store", store_dir, "groups", mini)
    assert max(len(line) for line in res.stdout.splitlines()) <= 100
    bad = cli("--output", "json", "--width", "10", "--store", store_dir, "groups", mini)
    assert bad.code == 1


def test_columns_env(
    cli: Invoke, mini: str, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    res = cli("--output", "table", "--store", store_dir, "sets")
    assert max(len(line) for line in res.stdout.splitlines()) <= 200


def test_raw_plain_without_color(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--output", "table", "--color", "never", "--store", store_dir, "entries", mini)
    assert "\x1b[" not in res.stdout
    raw = cli("--color", "never", "--store", store_dir, "entries", mini, "--raw", "--limit", "2")
    assert raw.stdout.startswith("2026-10-29 02:19:41,415+0100")
    assert "\x1b[" not in raw.stdout


def test_raw_color_always(cli: Invoke, mini: str, store_dir: Path) -> None:
    res = cli("--color", "always", "--store", store_dir, "entries", mini, "--raw", "--limit", "1")
    assert "\x1b[" in res.stdout and "mini.log:1" in res.stdout


ENTRIES = MINI_LOG.read_text(encoding="utf-8").split("\n2026-")


def test_highlight_header_and_frames() -> None:
    text = highlight_entry(ENTRIES[0])
    assert "dim" in _styles(text, "2026-10-29 02:19:41,415+0100")
    assert "bold red" in _styles(text, "ERROR")
    assert "cyan" in _styles(text, "c.o.scriptrunner.customfield.GroovyCustomField")
    assert "bold" in _styles(text, "java.lang.NullPointerException")
    assert "cyan" in _styles(text, "(Script42.groovy:17)")
    # application frame in bold, its location cyan
    assert "bold" in _styles(text, "GroovyCustomField.getValueFromIssue")
    assert "dim" in _styles(text, "com.onresolve.scriptrunner.customfield.")


def test_highlight_caused_by_and_more() -> None:
    raw = "2026-" + next(e for e in ENTRIES if "Caused by" in e)
    text = highlight_entry(raw)
    assert "magenta" in _styles(text, "Caused by: ")
    assert "bold" in _styles(text, "java.net.ConnectException")
    assert "dim italic" in _styles(text, "... 42 more")
    # JDK frame: not bold
    assert "bold" not in _styles(text, "Net.connect0")
    assert "dim" in _styles(text, "java.base/")
    assert "yellow" in _styles(
        highlight_entry("2026-" + next(e for e in ENTRIES if " WARN " in e)), "WARN"
    )
