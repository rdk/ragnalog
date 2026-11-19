"""Totals, relative times, hints, table layout and raw-entry highlighting."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragnalog.cli.output import highlight_entry
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
        "reason": "set_not_found",
        "message": "no log set 'nope'",
        "hint": "available sets: mini",
        "store": store_dir.as_posix(),
    }


def test_unknown_signature_hint(jcli: Invoke, mini: str) -> None:
    res = jcli("show", mini, "0000000000000000")
    assert res.code == 2
    assert "ragnalog groups mini" in res.json()["error"]["hint"]


def test_table_error_hint_on_stderr(cli: Invoke, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "groups", "nope")
    assert res.code == 3
    assert "hint:" in res.stderr


def test_ingest_table_next_line(cli: Invoke, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "ingest", MINI_LOG, "--name", "m")
    assert res.code == 0
    assert "next: ragnalog triage m" in res.stderr


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


def test_paths_the_system_refuses_are_errors_not_crashes(
    cli: Invoke, jcli: Invoke, mini: str, tmp_path: Path
) -> None:
    """A directory or file where the other belongs used to be internal_error."""
    sample = jcli("formats", "--sample", tmp_path)
    assert (sample.code, sample.json()["error"]["code"]) == (1, "usage")
    report = jcli("report", mini, "--out", tmp_path)
    assert (report.code, report.json()["error"]["code"]) == (1, "usage")
    assert "is a directory" in report.json()["error"]["message"]
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    ingest = cli("--output", "json", "--store", afile, "ingest", MINI_LOG)
    assert (ingest.code, ingest.json()["error"]["code"]) == (3, "store_error")


def test_missing_input_is_said_once(jcli: Invoke, tmp_path: Path) -> None:
    error = jcli("ingest", tmp_path / "nothing").json()["error"]
    assert error["code"] == "file_not_found"
    assert error["message"].count("no such file or directory") == 1


def test_a_damaged_set_is_a_store_error(jcli: Invoke, mini: str, store_dir: Path) -> None:
    set_id = jcli("set", mini).json()["id"]
    (store_dir / set_id / "groups.parquet").unlink()
    result = jcli("set", mini)
    assert result.code == 3 and result.json()["error"]["code"] == "store_error"
    assert "groups.parquet is missing" in result.json()["error"]["message"]
    assert f"ragnalog remove {set_id}" in result.json()["error"]["hint"]


def test_report_into_a_missing_folder_names_the_folder(
    jcli: Invoke, mini: str, tmp_path: Path
) -> None:
    res = jcli("report", mini, "--out", tmp_path / "nope" / "r.html")
    err = res.json()["error"]
    assert res.code == 2 and err["code"] == "file_not_found"
    assert f"the folder {tmp_path / 'nope'} does not exist" in err["message"]


def test_audit_edge_cases(jcli: Invoke, mini: str, store_dir: Path) -> None:
    # an unknown group has no timeline: exit 2, as show and the capture form say
    r = jcli("timeline", mini, "--signature", "zzzz")
    assert r.code == 2 and r.json()["error"]["reason"] == "group_not_found"
    # a window that runs backwards is refused, not silently empty
    r = jcli("entries", mini, "--since", "2026-10-29T09:00", "--until", "2026-10-29T08:00")
    assert r.code == 1 and r.json()["error"]["code"] == "invalid_value"
    # --window is checked with or without --all-nodes
    entry = jcli("entries", mini, "--limit", "1").json()["items"][0]["id"]
    r = jcli("context", mini, entry, "--window", "-5")
    assert r.code == 1 and r.json()["error"]["code"] == "invalid_value"
    # a name that reads as a reference or a path is refused
    r = jcli("ingest", MINI_LOG, "--name", "capture:x")
    assert r.code == 1 and r.json()["error"]["code"] == "invalid_value"
    # the baseline keywords ignore case and blanks
    assert jcli("triage", mini, "--baseline", " AUTO ").code == 0
    # empty SQL is a usage error
    assert jcli("query", mini, " ").code == 1
    # a damaged set file is a store error (exit 3), not a bad value or an internal error
    sid = jcli("set", mini).json()["id"]
    (store_dir / sid / "groups.parquet").write_text("not parquet", encoding="utf-8")
    for argv in (("groups", mini), ("query", mini, "select count(*) from groups")):
        r = jcli(*argv)
        assert r.code == 3 and r.json()["error"]["code"] == "store_error", (argv, r.stdout)
