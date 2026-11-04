"""``diff --rate`` (hourly rates of changed groups) and the coverage warning of ``diff``."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke
from .synth import E, write_log

DAY = "2026-10-30"
_LINES = MINI_LOG.read_text(encoding="utf-8").splitlines()
NPE = "\n".join(_LINES[1:5])  # the NullPointerException trace of the GroovyCustomField errors


def groovy(ts: str, issue: int) -> E:
    return E(
        ts,
        "IssueIndexer:thread-15",
        "ERROR",
        "c.o.scriptrunner.customfield.GroovyCustomField",
        f"Script field failed on issue: ABC-{issue}, field: Finished On",
        body=NPE,
        day=DAY,
    )


def sla(ts: str) -> E:
    return E(
        ts,
        "Caesium-1-3",
        "INFO",
        "c.a.jira.servicedesk.sla.SlaUpdater",
        "Recalculated SLA for 77 issues in 500 ms",
        day=DAY,
    )


@pytest.fixture
def sets(jcli: Invoke, tmp_path: Path) -> None:
    """``base`` = mini.log (6.9 h; Groovy x3, SLA x2). ``short`` (30 min): Groovy x6, SLA x1.
    ``long`` (3 h): Groovy x4, SLA x2."""
    short = [groovy(f"02:{m:02d}:00,000", 100 + m) for m in range(0, 30, 5)] + [sla("02:30:00,000")]
    long = [groovy(f"0{h}:00:00,000", 200 + h) for h in range(2, 6)] + [
        sla("02:10:00,000"),
        sla("05:00:00,000"),
    ]
    assert jcli("ingest", MINI_LOG, "--name", "base").code == 0
    for name, entries in (("short", short), ("long", long)):
        log = write_log(tmp_path / name / "atlassian-jira.log", entries)
        assert jcli("ingest", log, "--name", name).code == 0, name


def test_diff_rate_columns_and_order(jcli: Invoke, sets: None) -> None:
    doc = jcli("diff", "base", "short", "--rate").json()
    changed = doc["changed"]
    assert [r["logger"].rsplit(".", 1)[-1] for r in changed] == ["GroovyCustomField", "SlaUpdater"]
    for row in changed:
        assert {"rate_a", "rate_b", "rate_ratio", "ratio"} <= row.keys()
        assert row["rate_ratio"] == pytest.approx(row["rate_b"] / row["rate_a"], rel=1e-2)
    groovy_row, sla_row = changed
    assert groovy_row["ratio"] == 2.0 and groovy_row["rate_ratio"] > 20
    assert sla_row["ratio"] == 0.5 and sla_row["rate_ratio"] > 1  # fewer entries, higher rate
    plain = jcli("diff", "base", "short").json()
    assert all("rate_ratio" not in r for r in plain["changed"])


def test_diff_coverage_warning(jcli: Invoke, sets: None) -> None:
    doc = jcli("diff", "base", "short").json()
    assert len(doc["warnings"]) == 1
    text = doc["warnings"][0]
    assert text.startswith("Set B (short) covers 30 min but set A (base) covers 6.9 h")
    assert "13.7x shorter" in text and "unreliable" in text
    assert "warnings" not in jcli("diff", "base", "long").json()  # 3 h vs 6.9 h
    assert "warnings" in jcli("diff", "short", "base", "--rate").json()  # either way


def test_diff_warning_on_stderr_in_table_mode(cli: Invoke, sets: None, store_dir: Path) -> None:
    res = cli("--output", "table", "--store", store_dir, "diff", "base", "short", "--rate")
    assert res.code == 0
    assert "warning: Set B (short) covers" in res.stderr
    assert "rate_ratio" in res.stdout
    quiet = cli("--output", "table", "--store", store_dir, "diff", "base", "long")
    assert "warning" not in quiet.stderr
