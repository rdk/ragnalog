"""``query`` over all nodes of a capture, and ``--baseline``: views of two sets in one query."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ragnalog.cli.app import run
from ragnalog.store.store import Store
from tests.contract.test_parity import cluster_dir  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RAGNALOG_SERVER", "RAGNALOG_TOKEN", "RAGNALOG_STORE"):
        monkeypatch.delenv(name, raising=False)


def _query(store: Path, *argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    capsys.readouterr()
    code = run(["--output", "json", "--store", str(store), "query", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_capture_views_hold_every_node(
    cluster_dir: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(cluster_dir)
    n1, n2 = store.open("acme_node1"), store.open("acme_node2")
    sql = 'select node, "set", count(*) n from entries group by all order by all'
    code, doc = _query(cluster_dir, "cluster:acme", sql, capsys=capsys)
    assert code == 0, doc
    assert doc["logset"] == "capture:cap-acme" and "baseline" not in doc
    assert doc["items"] == [
        {"node": "NODE1", "set": n1.id, "n": n1.manifest.entry_count},
        {"node": "NODE2", "set": n2.id, "n": n2.manifest.entry_count},
    ]
    code, doc = _query(cluster_dir, "cap-acme", "select * from groups limit 1", capsys=capsys)
    assert code == 0 and doc["columns"][:2] == ["node", "set"]


def test_baseline_of_a_capture_and_of_a_set(
    cluster_dir: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    prev = Store(cluster_dir).open("acme_node1_prev")
    sql = "select node, count(*) n from baseline_entries group by 1"
    code, doc = _query(cluster_dir, "cap-acme", sql, "--baseline", "acme_node1_prev", capsys=capsys)
    assert code == 0, doc
    assert doc["baseline"] == f"capture:{prev.id}"  # a set in no capture: a capture of one node
    assert doc["items"] == [{"node": "NODE1", "n": prev.manifest.entry_count}]
    sql = "select count(*) n from baseline_entries"
    code, doc = _query(
        cluster_dir, "acme_node1", sql, "--baseline", "acme_node1_prev", capsys=capsys
    )
    assert code == 0 and doc["baseline"] == prev.id
    assert doc["items"] == [{"n": prev.manifest.entry_count}]
    new = (
        "select count(*) n from groups where signature not in "
        "(select signature from baseline_groups)"
    )
    code, doc = _query(
        cluster_dir, "acme_node1", new, "--baseline", "acme_node1_prev", capsys=capsys
    )
    assert code == 0 and isinstance(doc["items"][0]["n"], int)


def test_capture_query_errors(
    cluster_dir: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, doc = _query(cluster_dir, "cap-acme", "select * from baseline_groups", capsys=capsys)
    assert (code, doc["error"]["code"]) == (1, "sql_error")
    assert "available views: entries, groups" in doc["error"]["hint"]
    assert "baseline" not in doc["error"]["hint"]
    code, doc = _query(cluster_dir, "cap-acme", "create table t as select 1", capsys=capsys)
    assert (code, doc["error"]["code"]) == (1, "usage")
    code, doc = _query(cluster_dir, "capture:nope", "select 1", capsys=capsys)
    assert (code, doc["error"]["reason"]) == (2, "capture_not_found")
    code, doc = _query(cluster_dir, "cap-acme", "select 1", "--baseline", "nope", capsys=capsys)
    assert (code, doc["error"]["reason"]) == (3, "set_not_found")
