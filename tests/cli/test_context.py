"""``entries --request-id`` and the ``context`` command."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import Invoke
from .synth import EXEC_1, REQ_A, REQUESTS, write_log


@pytest.fixture
def reqs(jcli: Invoke, tmp_path: Path) -> list[str]:
    """Ingest REQUESTS; return the entry ids in file (= time) order."""
    log = write_log(tmp_path / "logs" / "atlassian-jira.log", REQUESTS)
    assert jcli("ingest", log, "--name", "reqs").code == 0
    doc = jcli("entries", "reqs", "--columns", "id", "--limit", "0").json()
    return [row["id"] for row in doc["items"]]


def _ids(doc: dict[str, object]) -> list[str]:
    items = doc["items"]
    assert isinstance(items, list)
    return [str(row["id"]) for row in items]


def test_entries_request_id(jcli: Invoke, reqs: list[str]) -> None:
    doc = jcli("entries", "reqs", "--request-id", REQ_A, "--brief").json()
    assert doc["total"] == 4 and {row["thread"] for row in doc["items"]} == {EXEC_1}
    assert _ids(doc) == [reqs[i] for i in (0, 2, 3, 6)]
    assert jcli("entries", "reqs", "--request-id", "540x2").json()["total"] == 0  # exact


def test_context_same_thread(jcli: Invoke, reqs: list[str]) -> None:
    anchor = reqs[6]
    res = jcli("context", "reqs", anchor)
    assert res.code == 0
    doc = res.json()
    assert doc["schema"] == "entries.v1" and doc["anchor"] == anchor
    assert doc["thread"] == EXEC_1 and doc["any_thread"] is False
    assert _ids(doc) == [reqs[i] for i in (0, 2, 3, 6, 7)]
    assert doc["count"] == 5
    narrow = jcli("context", "reqs", anchor, "--before", "1", "--after", "0").json()
    assert _ids(narrow) == [reqs[3], reqs[6]]


def test_context_ties_keep_file_order(jcli: Invoke, reqs: list[str]) -> None:
    doc = jcli("context", "reqs", reqs[2], "--before", "0", "--after", "1").json()
    assert _ids(doc) == [reqs[2], reqs[3]]
    doc = jcli("context", "reqs", reqs[3], "--before", "1", "--after", "0").json()
    assert _ids(doc) == [reqs[2], reqs[3]]


def test_context_any_thread(jcli: Invoke, reqs: list[str]) -> None:
    doc = jcli("context", "reqs", reqs[6], "--any-thread", "--before", "2", "--after", "1").json()
    assert doc["any_thread"] is True
    assert _ids(doc) == [reqs[4], reqs[5], reqs[6], reqs[7]]
    first = jcli("context", "reqs", reqs[0], "--any-thread", "--after", "0").json()
    assert _ids(first) == [reqs[0]]


def test_context_unknown_entry(jcli: Invoke, reqs: list[str]) -> None:
    res = jcli("context", "reqs", "0000000000000000")
    assert res.code == 2
    err = res.json()["error"]
    assert err["code"] == "not_found" and "ragnalog entries reqs" in err["hint"]


def test_context_bad_window(jcli: Invoke, reqs: list[str]) -> None:
    res = jcli("context", "reqs", reqs[0], "--before", "-1")
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"


def test_context_jsonl_and_table(cli: Invoke, reqs: list[str], store_dir: Path) -> None:
    rows = cli("--output", "jsonl", "--store", store_dir, "context", "reqs", reqs[6]).jsonl()
    marked = [row["id"] for row in rows if row.get("anchor") is True]
    assert marked == [reqs[6]] and len(rows) == 5
    table = cli("--output", "table", "--store", store_dir, "context", "reqs", reqs[6])
    anchor_line = next(line for line in table.stdout.splitlines() if "▶" in line)
    assert "Report failed" in anchor_line
    assert sum("▶" in line for line in table.stdout.splitlines()) == 1
