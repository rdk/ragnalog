"""``--columns``: comma-separated or repeated, validated against the stored schema."""

from __future__ import annotations

import pytest

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def test_entries_columns_comma_separated(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", "ts,level_name", "--limit", "3")
    assert res.code == 0, res.stdout
    items = res.json()["items"]
    assert len(items) == 3 and all(list(row) == ["ts", "level_name"] for row in items)


def test_entries_columns_repeated_and_mixed(jcli: Invoke, mini: str) -> None:
    repeated = jcli("entries", mini, "--columns", "id", "--columns", "level").json()["items"]
    assert repeated and all(set(row) == {"id", "level"} for row in repeated)
    mixed = jcli(
        "entries", mini, "--columns", "ts, logger", "--columns", "message,ts", "--limit", "1"
    ).json()["items"]
    assert list(mixed[0]) == ["ts", "logger", "message"]  # order kept, duplicate dropped


def test_entries_unknown_column(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", "ts,lvl_name")
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_value"
    assert "'lvl_name'" in err["message"] and "level_name" in err["message"]
    assert "valid columns:" in err["hint"] and "exception_type" in err["hint"]


def test_entries_empty_columns(jcli: Invoke, mini: str) -> None:
    res = jcli("entries", mini, "--columns", ",")
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"


def test_entries_columns_table(cli: Invoke, mini: str, store_dir: object) -> None:
    res = cli("--output", "table", "--store", store_dir, "entries", mini, "--columns", "ts,logger")
    assert res.code == 0
    header = next(line for line in res.stdout.splitlines() if " ts " in line)
    assert "logger" in header and "message" not in header
