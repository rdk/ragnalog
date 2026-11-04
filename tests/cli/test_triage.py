"""``atlas-log triage``: verdict, new exceptions below --min-level, coverage warnings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import MINI_LOG, Invoke
from .synth import PLUGIN_TRACE, E, render

DAY = "2026-10-30"
NEW_ERROR = E(
    "02:30:00,000",
    "Caesium-1-4",
    "ERROR",
    "c.a.jira.cluster.DefaultClusterManager",
    "Node node-2 is not responding",
    day=DAY,
)
NEW_INFO = E("02:50:00,000", "Caesium-1-4", "INFO", "c.e.reports.Web", "Report started", day=DAY)


def debug_trace(ts: str) -> E:
    return E(
        ts,
        "http-nio-8080-exec-1",
        "DEBUG",
        "c.q.r.stats.type.StatTypeHelper",
        "Stat type failed for field customfield_10001",
        body=PLUGIN_TRACE,
        day=DAY,
    )


def write_set(path: Path, extra: list[E]) -> Path:
    """The three GroovyCustomField errors of mini.log one day later, plus ``extra``."""
    text = MINI_LOG.read_text(encoding="utf-8")
    groovy = text[: text.index("2026-10-29 02:20:00,001")].replace("2026-10-29", DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes((groovy + "\n".join(render(e) for e in extra) + "\n").encode())
    return path


@pytest.fixture
def sets(jcli: Invoke, tmp_path: Path) -> dict[str, str]:
    """``base`` = mini.log (6.9 h); ``short`` (30 min) adds a new ERROR, a DEBUG trace and an
    INFO; ``quiet`` (2.7 h) adds only the DEBUG trace."""
    ids = {}
    for name, path in (
        ("base", MINI_LOG),
        (
            "short",
            write_set(
                tmp_path / "short" / "a.log", [NEW_ERROR, debug_trace("02:40:00,000"), NEW_INFO]
            ),
        ),
        ("quiet", write_set(tmp_path / "quiet" / "a.log", [debug_trace("05:00:00,000")])),
    ):
        res = jcli("ingest", path, "--name", name)
        assert res.code == 0, res.stdout
        ids[name] = res.json()["id"]
    return ids


def _loggers(rows: list[dict[str, Any]]) -> list[str]:
    return [r["logger"] for r in rows]


def test_triage_new_errors_and_exceptions(jcli: Invoke, sets: dict[str, str]) -> None:
    res = jcli("triage", "short", "--baseline", "base")
    assert res.code == 0, res.stdout
    doc = res.json()
    assert doc["schema"] == "triage.v1" and doc["verdict"] == "new_errors"
    assert doc["baseline"]["id"] == sets["base"] and doc["baseline"]["resolved_by"] == "param"
    assert _loggers(doc["new"]) == ["c.a.jira.cluster.DefaultClusterManager"]
    assert _loggers(doc["new_exceptions"]) == ["c.q.r.stats.type.StatTypeHelper"]
    exc = doc["new_exceptions"][0]
    assert exc["level_name"] == "DEBUG" and exc["rate_per_hour"] > 0
    assert exc["exception_type"] == "com.qoti.rf.exceptions.InternalException"
    assert "report_url" not in exc
    s = doc["summary"]
    assert s["new"] == 1 and s["new_exceptions"] == 1 and s["new_by_level"] == {"ERROR": 1}
    cov = doc["coverage"]
    assert cov["hours_set"] < 1 < cov["hours_baseline"] and cov["ratio"] < 0.25
    assert len(doc["warnings"]) == 1 and "unreliable" in doc["warnings"][0]


def test_triage_timestamps_match_manifests(jcli: Invoke, sets: dict[str, str]) -> None:
    doc = jcli("triage", "short", "--baseline", "base").json()
    manifests = {m["name"]: m for m in jcli("sets").json()["items"]}
    for key, name in (("set", "short"), ("baseline", "base")):
        for field in ("first_ts", "last_ts"):
            assert doc[key][field] == manifests[name][field]
            assert not doc[key][field].endswith("Z")
    assert doc["vanished"][0]["first_seen_a"].endswith("+00:00")


def test_triage_grew_uses_diff_rates(jcli: Invoke, sets: dict[str, str], tmp_path: Path) -> None:
    """The GroovyCustomField errors twice within ten minutes grow against mini.log."""
    text = MINI_LOG.read_text(encoding="utf-8")
    groovy = text[: text.index("2026-10-29 02:20:00,001")]
    busy = tmp_path / "busy" / "a.log"
    busy.parent.mkdir()
    busy.write_bytes((groovy + groovy.replace("02:19:4", "02:29:4")).encode())
    assert jcli("ingest", busy, "--name", "busy").code == 0
    doc = jcli("triage", "busy", "--baseline", "base").json()
    assert len(doc["grew"]) == 1 and doc["summary"]["grew"] == 1
    row = doc["grew"][0]
    assert (row["count_a"], row["count_b"]) == (3, 6)
    assert row["rate_ratio"] == pytest.approx(row["rate_b"] / row["rate_a"], rel=1e-2)
    diffed = jcli("diff", "base", "busy", "--rate", "--min-level", "WARN").json()
    same = next(r for r in diffed["changed"] if r["signature"] == row["signature"])
    assert {k: same[k] for k in ("rate_a", "rate_b", "rate_ratio")} == {
        k: row[k] for k in ("rate_a", "rate_b", "rate_ratio")
    }


def test_triage_auto_baseline(jcli: Invoke, sets: dict[str, str]) -> None:
    doc = jcli("triage", "short").json()
    assert doc["baseline"]["id"] == sets["base"] and doc["baseline"]["resolved_by"] == "auto"


def test_triage_new_exceptions_verdict(jcli: Invoke, sets: dict[str, str]) -> None:
    doc = jcli("triage", "quiet", "--baseline", "base").json()
    assert doc["new"] == [] and doc["verdict"] == "new_exceptions"
    assert _loggers(doc["new_exceptions"]) == ["c.q.r.stats.type.StatTypeHelper"]
    assert doc["warnings"] == [] and 0.25 < doc["coverage"]["ratio"] < 1


def test_triage_without_min_level_filter_has_no_extra_exceptions(jcli: Invoke, sets: dict) -> None:
    doc = jcli("triage", "short", "--baseline", "base", "--min-level", "TRACE").json()
    assert "c.q.r.stats.type.StatTypeHelper" in _loggers(doc["new"])
    assert doc["new_exceptions"] == []


def test_triage_no_baseline(jcli: Invoke, sets: dict[str, str]) -> None:
    res = jcli("triage", "base")
    assert res.code == 0
    doc = res.json()
    assert doc["verdict"] == "no_baseline" and doc["baseline"] is None
    assert doc["new"] == doc["new_exceptions"] == [] and doc["warnings"] == []
    assert doc["coverage"]["hours_baseline"] is None and doc["coverage"]["hours_set"] > 6


def test_triage_against_itself_is_clean(jcli: Invoke, sets: dict[str, str]) -> None:
    doc = jcli("triage", "base", "--baseline", "base").json()
    assert doc["verdict"] == "clean" and doc["warnings"] == []
    assert doc["coverage"]["ratio"] == 1.0


def test_triage_limit_and_options(jcli: Invoke, sets: dict[str, str]) -> None:
    doc = jcli("triage", "short", "--baseline", "base", "--limit", "1").json()
    assert len(doc["vanished"]) == 1 and doc["summary"]["vanished"] > 1
    bad = jcli("triage", "short", "--grow", "1")
    assert bad.code == 1 and bad.json()["error"]["code"] == "invalid_value"
    assert jcli("triage", "short", "--min-level", "loud").json()["error"]["code"] == (
        "invalid_level"
    )
    assert jcli("triage", "short", "--baseline", "nope").code == 3


def test_triage_table_and_jsonl(cli: Invoke, sets: dict[str, str], store_dir: Path) -> None:
    table = cli("--output", "table", "--store", store_dir, "triage", "short")
    assert table.code == 0
    assert "verdict: new_errors" in table.stdout and "new_exceptions" in table.stdout
    assert "warning:" in table.stderr and "unreliable" in table.stderr
    lines = cli("--output", "jsonl", "--store", store_dir, "triage", "short").jsonl()
    assert len(lines) == 1 and lines[0]["verdict"] == "new_errors"


def test_store_auto_baseline_rule(jcli: Invoke, sets: dict[str, str], store_dir: Path) -> None:
    from atlas_log_analyzer.store import Store
    from atlas_log_analyzer.store.triage import auto_baseline

    store = Store(store_dir)
    manifests = store.list()
    by_name = {m.name: m for m in manifests}
    assert auto_baseline(manifests, by_name["base"]) is None
    picked = auto_baseline(manifests, by_name["short"])
    assert picked is not None and picked.name == "base"
