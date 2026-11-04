"""``frames``: the exception chain of one entry with application frames first."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_log_analyzer.cli.output import highlight_entry

from .conftest import Invoke
from .synth import EXCEPTION_MIX, PLUGIN_TRACE, write_log


@pytest.fixture
def mix(jcli: Invoke, tmp_path: Path) -> dict[str, str]:
    """Ingest EXCEPTION_MIX; return the DEBUG entry's id and signature and an INFO entry id."""
    log = write_log(tmp_path / "logs" / "atlassian-jira.log", EXCEPTION_MIX)
    assert jcli("ingest", log, "--name", "mix").code == 0
    rows = jcli("entries", "mix", "--brief").json()["items"]
    debug = next(r for r in rows if r["level_name"] == "DEBUG")
    info = next(r for r in rows if r["level_name"] == "INFO")
    return {"id": debug["id"], "signature": debug["signature"], "info": info["id"]}


def _locations(frames: list[dict[str, object]]) -> list[str]:
    return [str(f["location"]) for f in frames]


def test_frames_by_entry_id(jcli: Invoke, mix: dict[str, str]) -> None:
    res = jcli("frames", "mix", mix["id"])
    assert res.code == 0, res.stdout
    doc = res.json()
    assert doc["schema"] == "frames.v1" and doc["entry"] == mix["id"]
    assert doc["signature"] == mix["signature"] and doc["level_name"] == "DEBUG"
    assert doc["count"] == 2 and doc["app_packages"] is None
    outer, cause = doc["items"]
    assert outer["type"] == "com.qoti.rf.exceptions.InternalException"
    assert outer["message"] == "Cannot compute stat type for customfield_10001"
    assert _locations(outer["app_frames"]) == [
        "com.qoti.rf.stats.type.StatTypeHelper.resolve",
        "org.acme.jira.reports.ReportService.build",
    ]
    assert outer["app_frames"][0] == {
        "location": "com.qoti.rf.stats.type.StatTypeHelper.resolve",
        "file": "StatTypeHelper.java",
        "line": 88,
    }
    assert outer["frames_total"] == 6 and outer["app_frames_total"] == 2
    assert outer["omitted_frames"] == 0 and "frames" not in outer
    assert cause["type"] == "java.lang.IllegalArgumentException"
    assert _locations(cause["app_frames"]) == ["com.qoti.rf.stats.type.StatTypes.of"]
    assert cause["frames_total"] == 3 and cause["omitted_frames"] == 12


def test_frames_by_signature_uses_first_example(jcli: Invoke, mix: dict[str, str]) -> None:
    doc = jcli("frames", "mix", mix["signature"]).json()
    assert doc["entry"] == mix["id"] and doc["count"] == 2


def test_frames_limit_and_all(jcli: Invoke, mix: dict[str, str]) -> None:
    one = jcli("frames", "mix", mix["id"], "--limit", "1").json()["items"][0]
    assert len(one["app_frames"]) == 1 and one["app_frames_total"] == 2
    full = jcli("frames", "mix", mix["id"], "--all", "--limit", "1").json()["items"][0]
    assert len(full["app_frames"]) == 2
    assert [(f["location"].split(".")[1], f["app"]) for f in full["frames"]] == [
        ("qoti", True),
        ("atlassian", False),
        ("acme", True),
        ("util", False),
        ("atlassian", False),
        ("apache", False),
    ]


def test_frames_app_packages_env(
    jcli: Invoke, mix: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_LOG_APP_PACKAGES", " org.acme. , com.google.")
    doc = jcli("frames", "mix", mix["id"]).json()
    assert doc["app_packages"] == ["org.acme.", "com.google."]
    outer, cause = doc["items"]
    assert _locations(outer["app_frames"]) == ["org.acme.jira.reports.ReportService.build"]
    assert _locations(cause["app_frames"]) == ["com.google.common.collect.Iterators.transform"]


def test_frames_without_exception_and_unknown(jcli: Invoke, mix: dict[str, str]) -> None:
    empty = jcli("frames", "mix", mix["info"])
    assert empty.code == 0 and empty.json()["items"] == [] and empty.json()["count"] == 0
    missing = jcli("frames", "mix", "ffffffffffffffff")
    assert missing.code == 2
    err = missing.json()["error"]
    assert err["code"] == "not_found" and "--with-exception" in err["hint"]
    assert jcli("frames", "mix", mix["id"], "--limit", "-1").code == 1


def test_frames_table_and_jsonl(cli: Invoke, mix: dict[str, str], store_dir: Path) -> None:
    table = cli("--output", "table", "--store", store_dir, "frames", "mix", mix["id"])
    lines = table.stdout.splitlines()
    assert lines[1].startswith("com.qoti.rf.exceptions.InternalException: Cannot compute")
    assert "    at org.acme.jira.reports.ReportService.build(ReportService.java:57)" in lines
    assert not any("com.atlassian." in line for line in lines)
    assert any(line.startswith("Caused by: java.lang.IllegalArgumentException") for line in lines)
    rows = cli("--output", "jsonl", "--store", store_dir, "frames", "mix", mix["id"]).jsonl()
    assert [r["type"] for r in rows] == [
        "com.qoti.rf.exceptions.InternalException",
        "java.lang.IllegalArgumentException",
    ]


def test_highlight_platform_frames_are_not_bold(monkeypatch: pytest.MonkeyPatch) -> None:
    def bold(text: object, needle: str) -> bool:
        from rich.text import Text

        assert isinstance(text, Text)
        start = text.plain.index(needle)
        return any("bold" in str(s.style) and s.start <= start < s.end for s in text.spans)

    raw = "2026-10-29 09:00:01,000+0100 exec-1 DEBUG [c.q.Foo] x\n" + PLUGIN_TRACE
    text = highlight_entry(raw)
    assert bold(text, "StatTypeHelper.resolve") and bold(text, "ReportService.build")
    assert not bold(text, "CustomFieldImpl.getValue")
    assert not bold(text, "Iterators.transform")
    monkeypatch.setenv("ATLAS_LOG_APP_PACKAGES", "org.acme.")
    text = highlight_entry(raw)
    assert bold(text, "ReportService.build") and not bold(text, "StatTypeHelper.resolve")
