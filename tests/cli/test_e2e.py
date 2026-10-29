"""End-to-end CLI tests: ingest ``tests/cli/data/mini.log`` into a temporary store.

``mini.log`` holds 10 Jira entries: three GroovyCustomField errors that differ
only in the issue key (one group), two SLA INFO lines, and five single entries.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from .conftest import MINI_LOG, SAMPLE_LOG, Invoke

ISSUES = {"ABC-69517", "ABC-70001", "ABC-71234"}
NEW_ERROR = (
    "2026-10-30 08:00:00,000+0100 Caesium-1-4 ERROR ServiceRunner     "
    "[c.a.jira.cluster.DefaultClusterManager] Node node-2 is not responding\n"
)


def is_level(value: Any, name: str) -> bool:
    """Levels may be stored as names or as LogLevel integer values."""
    from atlas_log_analyzer.core.models import LogLevel

    return value == name or value == int(LogLevel[name])


def ingest(jcli: Invoke, path: Path = MINI_LOG, *extra: object, name: str = "mini") -> Any:
    result = jcli("ingest", path, "--format", "jira", "--name", name, *extra)
    assert result.code == 0, result.stdout + result.stderr
    return result.json()


def top_group(jcli: Invoke, ref: str = "mini") -> Any:
    result = jcli("groups", ref, "--limit", "1")
    assert result.code == 0
    return result.json()["items"][0]


def write_set_b(tmp_path: Path) -> Path:
    """The three GroovyCustomField errors of mini.log plus one new error."""
    text = MINI_LOG.read_text(encoding="utf-8")
    cut = text.index("2026-10-29 02:20:00,001")
    path = tmp_path / "b" / "atlassian-jira.log"
    path.parent.mkdir()
    path.write_bytes((text[:cut] + NEW_ERROR).encode())
    return path


# --- ingest / sets


def test_ingest_prints_manifest(jcli: Invoke) -> None:
    doc = ingest(jcli)
    assert doc["schema"] == "logset.v1"
    assert doc["format"] == "jira"
    assert doc["name"] == "mini"
    assert doc["entry_count"] == 10
    assert 1 < doc["group_count"] < 10  # the three Groovy errors share a group
    assert len(doc["sources"]) == 1
    assert doc["ingest"]["parsed_entries"] == 10
    assert doc["ingest"]["parse_errors"] == 0
    assert doc["ingest"]["existing"] is False
    assert len(doc["id"]) >= 8


def test_ingest_detects_format(jcli: Invoke) -> None:
    result = jcli("ingest", MINI_LOG)
    assert result.code == 0, result.stdout
    assert result.json()["format"] == "jira"


def test_ingest_is_idempotent(jcli: Invoke) -> None:
    first = ingest(jcli)
    second = ingest(jcli)
    assert second["id"] == first["id"]
    assert second["ingest"]["existing"] is True
    assert jcli("sets").json()["count"] == 1


def test_ingest_unknown_format(jcli: Invoke) -> None:
    result = jcli("ingest", MINI_LOG, "--format", "nope")
    assert result.code == 1
    assert result.json()["error"]["code"] == "unknown_format"


def test_ingest_missing_path(jcli: Invoke, tmp_path: Path) -> None:
    result = jcli("ingest", tmp_path / "nope.log")
    assert result.code == 2
    assert result.json()["error"]["code"] in {"file_not_found", "no_sources"}


def test_sets(jcli: Invoke, store_dir: Path) -> None:
    manifest = ingest(jcli)
    doc = jcli("sets").json()
    assert doc["schema"] == "sets.v1"
    assert doc["count"] == 1
    assert doc["items"][0]["id"] == manifest["id"]
    assert doc["items"][0]["name"] == "mini"
    assert Path(doc["store"]) == store_dir


def test_sets_empty_store(jcli: Invoke) -> None:
    result = jcli("sets")
    assert result.code == 0
    assert result.json()["count"] == 0


# --- groups / show / entries


def test_groups(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    result = jcli("groups", "mini", "--limit", "0")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "groups.v1"
    assert doc["logset"] == manifest["id"]
    assert doc["count"] == len(doc["items"]) == manifest["group_count"]
    assert sum(g["count"] for g in doc["items"]) == 10
    top = doc["items"][0]
    assert top["count"] == 3
    assert is_level(top["level"], "ERROR")
    assert top["logger"].endswith("GroovyCustomField")
    assert not ISSUES & set(top["template"].split())
    assert "ABC-69517" not in top["template"]
    assert len(top["signature"]) == 16


def test_groups_filters(jcli: Invoke) -> None:
    ingest(jcli)
    errors = jcli("groups", "mini", "--level", "error", "--limit", "0").json()["items"]
    assert errors and all(is_level(g["level"], "ERROR") for g in errors)
    assert sum(g["count"] for g in errors) == 5
    warn_up = jcli("groups", "mini", "--min-level", "WARN", "--limit", "0").json()["items"]
    assert sum(g["count"] for g in warn_up) == 7
    assert len(jcli("groups", "mini", "--limit", "2").json()["items"]) == 2
    groovy = jcli("groups", "mini", "--logger", "c.o.scriptrunner").json()["items"]
    assert [g["count"] for g in groovy] == [3]
    both = jcli("groups", "mini", "--level", "WARN,ERROR", "--limit", "0").json()["items"]
    assert sum(g["count"] for g in both) == 7


def test_groups_jsonl_and_table(cli: Invoke, jcli: Invoke, store_dir: Path) -> None:
    ingest(jcli)
    lines = cli("--output", "jsonl", "--store", store_dir, "groups", "mini").jsonl()
    assert lines and all("signature" in row for row in lines)
    table = cli("--output", "table", "--store", store_dir, "groups", "mini")
    assert table.code == 0
    assert "signature" in table.stdout and "\x1b[" not in table.stdout


def test_show(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    signature = top_group(jcli)["signature"]
    result = jcli("show", "mini", signature)
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "entries.v1"
    assert doc["logset"] == manifest["id"]
    assert doc["signature"] == signature
    assert doc["group"]["count"] == 3
    assert doc["count"] == 3
    variables = {v for item in doc["items"] for v in item["variables"]}
    assert variables >= ISSUES
    assert all(item["signature"] == signature for item in doc["items"])
    assert len(jcli("show", "mini", signature, "--limit", "1").json()["items"]) == 1


def test_show_raw(jcli: Invoke) -> None:
    ingest(jcli)
    signature = top_group(jcli)["signature"]
    result = jcli("show", "mini", signature, "--raw")
    assert result.code == 0
    for issue in ISSUES:
        assert f"Script field failed on issue: {issue}" in result.stdout
    assert result.stdout.count("java.lang.NullPointerException") == 3


def test_show_unknown_signature(jcli: Invoke) -> None:
    ingest(jcli)
    result = jcli("show", "mini", "ffffffffffffffff")
    assert result.code == 2
    assert result.json()["error"]["code"] == "not_found"


def test_entries(jcli: Invoke) -> None:
    ingest(jcli)
    doc = jcli("entries", "mini", "--limit", "0").json()
    assert doc["schema"] == "entries.v1"
    assert doc["count"] == 10
    assert len(jcli("entries", "mini", "--limit", "4").json()["items"]) == 4
    assert jcli("entries", "mini", "--user", "jdoe").json()["count"] == 2
    refused = jcli("entries", "mini", "--contains", "Connection refused").json()["items"]
    assert len(refused) == 1
    assert refused[0]["exception_type"] == "com.atlassian.jira.exception.DataAccessException"
    narrow = jcli("entries", "mini", "--columns", "id", "--columns", "level").json()["items"]
    assert narrow and all(set(row) == {"id", "level"} for row in narrow)


# --- query / stats / schema


def test_query(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    result = jcli("query", "mini", "select count(*) as n from entries")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "query.v1"
    assert doc["logset"] == manifest["id"]
    assert doc["columns"] == ["n"]
    assert doc["items"] == [{"n": 10}]
    groups = jcli("query", "mini", "select sum(count) as total from groups").json()
    assert groups["items"] == [{"total": 10}]


def test_query_bad_sql(jcli: Invoke) -> None:
    ingest(jcli)
    result = jcli("query", "mini", "select nope from nowhere")
    assert result.code == 1
    assert result.json()["error"]["code"] in {"sql_error", "invalid_value"}


def test_stats(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    result = jcli("stats", "mini")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "stats.v1"
    assert doc["logset"] == manifest["id"]


def test_schema(jcli: Invoke) -> None:
    doc = jcli("schema").json()
    assert doc["schema"] == "schema.v1"
    assert set(doc["definitions"]) == {"entries", "groups", "merges", "logset"}
    only = jcli("schema", "entries").json()
    assert list(only["definitions"]) == ["entries"]
    assert only["definitions"]["entries"]


def test_formats(jcli: Invoke) -> None:
    doc = jcli("formats").json()
    assert doc["schema"] == "formats.v1"
    names = [item["name"] for item in doc["items"]]
    assert "jira" in names
    sniffed = jcli("formats", "--sample", MINI_LOG).json()["items"]
    assert sniffed[0]["name"] == "jira"
    assert sniffed[0]["confidence"] > 0


# --- diff / regroup / remove


def test_diff(jcli: Invoke, tmp_path: Path) -> None:
    a = ingest(jcli, MINI_LOG, name="a")
    b = ingest(jcli, write_set_b(tmp_path), name="b")
    assert a["id"] != b["id"]
    result = jcli("diff", "a", "b")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "diff.v1"
    assert (doc["a"], doc["b"]) == (a["id"], b["id"])
    assert len(doc["new"]) == 1
    assert doc["new"][0]["logger"].endswith("DefaultClusterManager")
    assert len(doc["vanished"]) == a["group_count"] - 1
    assert doc["changed"] == []
    assert doc["unchanged_count"] == 1
    errors_only = jcli("diff", "a", "b", "--level", "ERROR").json()
    assert all(is_level(row["level"], "ERROR") for row in errors_only["vanished"])
    assert len(errors_only["vanished"]) < len(doc["vanished"])


def test_diff_jsonl(cli: Invoke, jcli: Invoke, store_dir: Path, tmp_path: Path) -> None:
    ingest(jcli, MINI_LOG, name="a")
    ingest(jcli, write_set_b(tmp_path), name="b")
    rows = cli("--output", "jsonl", "--store", store_dir, "diff", "a", "b").jsonl()
    assert {row["change"] for row in rows} == {"new", "vanished"}


def test_regroup(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    result = jcli("regroup", "mini", "--frames", "2")
    assert result.code == 0
    doc = result.json()
    assert doc["schema"] == "logset.v1"
    assert doc["id"] == manifest["id"]
    assert doc["signature_spec"] != manifest["signature_spec"]
    assert top_group(jcli)["count"] == 3  # same frames in all three Groovy errors


def test_remove_and_unknown_set(jcli: Invoke) -> None:
    manifest = ingest(jcli)
    result = jcli("remove", "mini")
    assert result.code == 0
    assert result.json() == {"schema": "remove.v1", "removed": manifest["id"], "name": "mini"}
    assert jcli("sets").json()["count"] == 0
    missing = jcli("groups", "mini")
    assert missing.code == 3
    assert missing.json()["error"]["code"] == "store_error"


@pytest.mark.skipif(importlib.util.find_spec("drain3") is None, reason="drain3 not installed")
def test_ingest_mine(jcli: Invoke) -> None:
    result = jcli("ingest", MINI_LOG, "--format", "jira", "--mine")
    assert result.code == 0, result.stdout
    doc = result.json()
    assert isinstance(doc["merges"], list)
    count = jcli("query", doc["id"], "select count(*) as n from merges").json()["items"][0]["n"]
    assert count == len(doc["merges"])


# --- parse


def test_parse_streams_jsonl(jcli: Invoke) -> None:
    result = jcli("parse", MINI_LOG, "--format", "jira")
    assert result.code == 0
    rows = result.jsonl()
    assert len(rows) == 10
    first = rows[0]
    assert first["level"] == "ERROR"
    assert first["format"] == "jira"
    assert first["timestamp"] == "2026-10-29T02:19:41.415+01:00"
    assert first["thread"] == "IssueIndexer:thread-15"
    assert first["logger"] == "c.o.scriptrunner.customfield.GroovyCustomField"
    assert first["message"] == "Script field failed on issue: ABC-69517, field: Finished On"
    assert first["exception_type"] == "java.lang.NullPointerException"
    assert len(first["exceptions"][0]["frames"]) == 3
    assert first["source"]["line"] == 1
    assert first["parse_error"] is None
    chained = next(r for r in rows if r["thread"] == "http-nio-8080-exec-12")
    assert [e["type"] for e in chained["exceptions"]] == [
        "com.atlassian.jira.exception.DataAccessException",
        "java.net.ConnectException",
    ]
    assert chained["exceptions"][1]["omitted_frames"] == 42
    assert chained["context"]["user"] == "asmith"


def test_parse_limit_and_detection(jcli: Invoke) -> None:
    result = jcli("parse", MINI_LOG, "--limit", "3")
    assert result.code == 0
    assert len(result.jsonl()) == 3


def test_parse_unknown_format(jcli: Invoke) -> None:
    result = jcli("parse", MINI_LOG, "--format", "nope")
    assert result.code == 1
    assert result.json()["error"]["code"] == "unknown_format"


@pytest.mark.skipif(not SAMPLE_LOG.exists(), reason="tests/fixtures/jira/sample.log not present")
def test_sample_fixture_roundtrip(jcli: Invoke) -> None:
    doc = ingest(jcli, SAMPLE_LOG, name="sample")
    assert doc["entry_count"] > 0
    groups = jcli("groups", "sample", "--limit", "0").json()
    assert sum(g["count"] for g in groups["items"]) == doc["entry_count"]
    assert jcli("stats", "sample").code == 0
