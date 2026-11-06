"""Lines before a file's first header have no time: they count, but never reach 1970."""

from __future__ import annotations

from pathlib import Path

from tests.cli.conftest import MINI_LOG, Invoke


def test_headless_lines_stay_out_of_time_ranges(jcli: Invoke, tmp_path: Path) -> None:
    log = tmp_path / "atlassian-jira.log"
    log.write_bytes(b"\tat com.example.Orphan.run(Orphan.java:1)\n" + MINI_LOG.read_bytes())
    assert jcli("ingest", log, "--name", "headless").code == 0
    hist = jcli("timeline", "headless").json()
    assert hist["items"] and not hist["items"][0]["bucket"].startswith("1970")
    stats = jcli("stats", "headless").json()
    assert not stats["time_range"]["first"].startswith("1970")
    assert all(not h["hour"].startswith("1970") for h in stats["hours"])
    assert stats["entry_count"] == 11
    groups = jcli("groups", "headless", "--limit", "0").json()["items"]
    assert all(not (g.get("first_seen") or "").startswith("1970") for g in groups)
