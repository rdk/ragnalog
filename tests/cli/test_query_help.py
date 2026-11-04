"""``query``: view columns in ``--help`` and hints for SQL errors."""

from __future__ import annotations

import pytest

from atlas_log_analyzer.store.schema import ENTRIES_SCHEMA, GROUPS_SCHEMA, MERGES_SCHEMA

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def _words(text: str) -> set[str]:
    return {w.strip(",.") for w in text.split()}


def test_query_help_lists_view_columns(cli: Invoke) -> None:
    words = _words(cli("query", "--help").stdout)
    for schema in (ENTRIES_SCHEMA, GROUPS_SCHEMA, MERGES_SCHEMA):
        missing = set(schema.names) - words
        assert not missing, missing


def test_entries_help_lists_columns(cli: Invoke) -> None:
    assert not set(ENTRIES_SCHEMA.names) - _words(cli("entries", "--help").stdout)


def test_unknown_column_hint(jcli: Invoke, mini: str) -> None:
    res = jcli("query", mini, "select nope from entries")
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "sql_error" and "nope" in err["message"]
    assert err["hint"].startswith("candidate columns: ")
    assert "atlas-log query --help" in err["hint"]


def test_column_error_without_candidates_lists_views(jcli: Invoke, mini: str) -> None:
    res = jcli("query", mini, "select e.nope from entries e")
    err = res.json()["error"]
    assert err["code"] == "sql_error"
    assert "entries columns: id, ts," in err["hint"] and "groups columns: signature" in err["hint"]
    assert "merges columns" not in err["hint"]  # mini has no merges.parquet


def test_unknown_table_hint(jcli: Invoke, mini: str) -> None:
    err = jcli("query", mini, "select * from nowhere").json()["error"]
    assert err["code"] == "sql_error" and err["hint"] == "available views: entries, groups"


def test_syntax_error_has_no_hint(jcli: Invoke, mini: str) -> None:
    err = jcli("query", mini, "selec 1").json()["error"]
    assert err["code"] == "sql_error" and "hint" not in err
