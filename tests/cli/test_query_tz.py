"""`query` runs in UTC whatever the machine's time zone."""

from __future__ import annotations

import pytest

from tests.cli.conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def test_query_ts_equals_entries_ts(jcli: Invoke, mini: str) -> None:
    listed = jcli("entries", mini, "--columns", "id,ts", "--limit", "0").json()["items"]
    queried = jcli("query", mini, "select id, ts from entries").json()["items"]
    assert {r["id"]: r["ts"] for r in queried} == {r["id"]: r["ts"] for r in listed}
    assert all(r["ts"].endswith("+00:00") for r in queried)


def test_query_session_is_utc(jcli: Invoke, mini: str) -> None:
    sql = (
        "select current_setting('TimeZone') tz, min(date_trunc('day', ts)) d,"
        " min(strftime(ts, '%H:%M')) hm from entries"
    )
    row = jcli("query", mini, sql).json()["items"][0]
    # The first entry is 2026-10-29 02:19:41 +01:00, i.e. 01:19 UTC.
    assert row["tz"] == "UTC"
    assert row["d"].startswith("2026-10-29T00:00:00") and row["d"].endswith("+00:00")
    assert row["hm"] == "01:19"


def test_query_interval_results_are_text(jcli: Invoke, mini: str) -> None:
    """polars cannot import a DuckDB INTERVAL: it used to be a sql_error."""
    sql = 'select max(ts) - min(ts) "span ""a""", epoch(max(ts) - min(ts)) s, 1 n from entries'
    result = jcli("query", mini, sql)
    assert result.code == 0, result.stdout
    row = result.json()["items"][0]
    assert row == {'span "a"': "06:52:20.584", "s": 24740.584, "n": 1}
    gaps = jcli("query", mini, "select ts - lag(ts) over (order by ts) gap from entries limit 2")
    first, second = (r["gap"] for r in gaps.json()["items"])
    assert first is None and isinstance(second, str)
