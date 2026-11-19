"""Cluster logs end to end: split ingest, labels, captures and per-node triage (CLI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.cluster import JOB_ID, export_zip, write_export, write_node_files

from .conftest import Invoke


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return export_zip(tmp_path / "export.zip")


def test_export_zip_becomes_one_set_per_node(jcli: Invoke, archive: Path, store_dir: Path) -> None:
    result = jcli("ingest", archive, "--cluster", "jira-test")
    assert result.code == 0, result.stderr
    doc = result.json()
    assert doc["schema"] == "ingest-capture.v1" and doc["warnings"] == []
    capture = doc["capture"]
    assert capture["capture"] == JOB_ID and capture["cluster"] == "jira-test"
    assert [n["node"] for n in capture["nodes"]] == ["NODE1", "NODE2"]
    assert capture["entry_count"] == sum(s["entry_count"] for s in doc["sets"])
    node1, node2 = doc["sets"]
    assert (node1["name"], node1["node"], node1["cluster"], node1["capture"]) == (
        "jira-test_node1",
        "NODE1",
        "jira-test",
        JOB_ID,
    )
    assert node2["name"] == "jira-test_node2" and node1["ingest"]["existing"] is False
    # rotated files are read oldest first within each node
    assert [Path(s["path"]).name for s in node1["sources"]] == [
        "NODE1_atlassian-jira.log.2",
        "NODE1_atlassian-jira.log.1",
        "NODE1_atlassian-jira.log",
    ]
    assert "NODE1: ingested" in result.stderr
    assert not (store_dir / ".spool").exists()  # the extracted files are gone


def test_reingest_is_idempotent_and_labels_what_is_missing(jcli: Invoke, archive: Path) -> None:
    first = jcli("ingest", archive).json()
    assert first["capture"]["cluster"] is None
    assert [s["name"] for s in first["sets"]] == [
        f"{JOB_ID[:8]}_node1",
        f"{JOB_ID[:8]}_node2",
    ]
    again = jcli("ingest", archive, "--cluster", "jira-test").json()
    assert [s["ingest"]["existing"] for s in again["sets"]] == [True, True]
    assert [s["id"] for s in again["sets"]] == [s["id"] for s in first["sets"]]
    assert [s["cluster"] for s in again["sets"]] == ["jira-test", "jira-test"]  # labelled
    assert again["capture"]["cluster"] == "jira-test"
    third = jcli("ingest", archive, "--cluster", "other").json()
    assert third["sets"][0]["cluster"] == "jira-test"
    assert any("--cluster other ignored" in w for w in third["warnings"])


NEXT_JOB = "2026-11-06_06-00-00-000_TST"


def next_day_export(directory: Path) -> Path:
    """The export of the following day: other files, so other sets."""
    write_export(directory, job_id=NEXT_JOB)
    for f in directory.glob("*.log*"):
        f.write_text(f.read_text(encoding="utf-8").replace("2026-11-05", "2026-11-06"), "utf-8")
    return directory


def test_cluster_is_remembered_per_node(jcli: Invoke, tmp_path: Path) -> None:
    jcli("ingest", export_zip(tmp_path / "a.zip"), "--cluster", "jira-test")
    doc = jcli("ingest", next_day_export(tmp_path / "b")).json()
    assert doc["capture"]["capture"] == NEXT_JOB and doc["capture"]["cluster"] == "jira-test"
    # the first capture took the names: the second gets numbered ones and says so
    assert [s["name"] for s in doc["sets"]] == ["jira-test_node1_2", "jira-test_node2_2"]
    assert any("name jira-test_node1 is used by another set" in w for w in doc["warnings"])
    listed = jcli("captures").json()
    assert [c["capture"] for c in listed["items"]] == [NEXT_JOB, JOB_ID]


def test_triage_compares_each_node_with_its_own_past(jcli: Invoke, tmp_path: Path) -> None:
    jcli("ingest", write_export(tmp_path / "a"), "--cluster", "jira-test")
    sets = jcli("ingest", next_day_export(tmp_path / "b"), "--name", "next").json()["sets"]
    doc = jcli("triage", sets[0]["id"]).json()
    assert doc["baseline"]["name"] == "jira-test_node1"
    assert "same node NODE1" in doc["baseline"]["reason"]
    rejected = {c["name"]: c["reason"] for c in doc["baseline"]["candidates"]}
    assert rejected["jira-test_node2"] == "other_node"


def test_prefix_and_dir_splits(jcli: Invoke, tmp_path: Path) -> None:
    for node in ("NODE1", "NODE2"):
        write_node_files(tmp_path / "flat", node)
        for f in write_node_files(tmp_path / "dirs" / node.lower(), node, prefix=False):
            f.write_text(f.read_text("utf-8").replace("part", "piece"), "utf-8")  # other sets
    flat = jcli("ingest", tmp_path / "flat").json()
    assert flat["schema"] == "ingest-capture.v1"
    assert [s["node"] for s in flat["sets"]] == ["NODE1", "NODE2"]
    assert len(flat["capture"]["capture"]) == 16  # a hash of the set ids
    dirs = jcli("ingest", tmp_path / "dirs", "--name", "byDir").json()
    assert [s["node"] for s in dirs["sets"]] == ["node1", "node2"]
    assert [s["name"] for s in dirs["sets"]] == ["byDir_node1", "byDir_node2"]
    forced = jcli(
        "ingest", tmp_path / "dirs" / "node1", tmp_path / "dirs" / "node2", "--split", "dir"
    ).json()
    assert forced["capture"]["capture"] == dirs["capture"]["capture"]  # the same two sets


def test_split_none_and_node_option(jcli: Invoke, archive: Path) -> None:
    refused = jcli("ingest", archive, "--node", "NODE1")
    assert refused.code == 1 and refused.json()["error"]["code"] == "invalid_value"
    assert "--split none" in refused.json()["error"]["message"]
    one = jcli("ingest", archive, "--split", "none", "--node", "MIXED", "--capture", "cap-1")
    doc = one.json()
    assert one.code == 0 and doc["schema"] == "logset.v1"
    assert (doc["node"], doc["cluster"], doc["capture"]) == ("MIXED", None, "cap-1")
    assert len(doc["sources"]) == 6
    assert any("names node NODE" in w for w in doc["ingest"]["warnings"])


def test_single_set_hints_suggest_a_node(jcli: Invoke, tmp_path: Path) -> None:
    files = write_node_files(tmp_path / "n1", "NODE1", prefix=False)
    plain = jcli("ingest", tmp_path / "n1")
    doc = plain.json()
    assert doc["node"] is None and doc["capture"] is None  # labels stay explicit
    assert doc["ingest"]["warnings"] == [
        "the log names node NODE1 (1 line(s)); pass --node NODE1 (or "
        f"`ragnalog label {doc['id']} --node NODE1`) to label the set"
    ]
    assert "pass --node NODE1" in plain.stderr
    labelled = jcli("ingest", tmp_path / "n1", "--node", "NODE1", "--cluster", "acme").json()
    assert labelled["ingest"]["existing"] is True and labelled["ingest"]["warnings"] == []
    assert (labelled["node"], labelled["cluster"]) == ("NODE1", "acme")
    other = write_node_files(tmp_path / "n1b", "NODE1", parts=1, prefix=False)
    other[0].write_text(files[0].read_text("utf-8").replace("10:", "11:"), "utf-8")
    remembered = jcli("ingest", other[0], "--node", "NODE1").json()
    assert remembered["cluster"] == "acme"  # from the newest earlier set of NODE1
    wrong = write_node_files(tmp_path / "n2", "NODE2", parts=1, prefix=False)
    mislabelled = jcli("ingest", wrong[0], "--node", "NODE1").json()
    assert mislabelled["ingest"]["warnings"] == [
        "the log names node NODE2 (2 line(s) of atlassian-jira.log), but the set is "
        "labelled node NODE1"
    ]


def test_label_command(jcli: Invoke, tmp_path: Path) -> None:
    write_node_files(tmp_path / "n1", "NODE1", prefix=False)
    created = jcli("ingest", tmp_path / "n1", "--name", "n1").json()
    doc = jcli("label", "n1", "--node", "NODE1", "--cluster", "acme").json()
    assert doc["schema"] == "logset.v1" and doc["id"] == created["id"]
    assert (doc["node"], doc["cluster"], doc["capture"]) == ("NODE1", "acme", None)
    cleared = jcli("label", "n1", "--cluster", "", "--capture", "c-1").json()
    assert (cleared["node"], cleared["cluster"], cleared["capture"]) == ("NODE1", None, "c-1")
    assert jcli("captures").json()["items"][0]["capture"] == "c-1"
    bad = jcli("label", "n1", "--node", "two words")
    assert bad.code == 1 and bad.json()["error"]["code"] == "invalid_value"
    assert jcli("label", "n1").code == 1
    missing = jcli("label", "nope", "--node", "NODE1")
    assert missing.code == 3 and missing.json()["error"]["reason"] == "set_not_found"


def test_bad_zip_is_invalid_value(jcli: Invoke, tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    result = jcli("ingest", bad)
    assert result.code == 1 and result.json()["error"]["code"] == "invalid_value"


def test_table_output(cli: Invoke, archive: Path, store_dir: Path) -> None:
    base = ("--output", "table", "--width", "200", "--store", store_dir)
    assert cli(*base, "ingest", archive).code == 0
    shown = cli(*base, "captures")
    assert shown.code == 0 and "NODE1, NODE2" in shown.stdout and JOB_ID in shown.stdout


# --- the capture views (documents: tests/contract; here the CLI-only behaviour)


@pytest.fixture
def captured(jcli: Invoke, archive: Path) -> str:
    """A store holding the export of two nodes; returns its cluster reference."""
    assert jcli("ingest", archive, "--cluster", "jira-test").code == 0
    return "cluster:jira-test"


def test_capture_views_from_the_cli(jcli: Invoke, captured: str, tmp_path: Path) -> None:
    doc = jcli("capture", captured).json()
    assert doc["schema"] == "capture.v1" and doc["capture"] == JOB_ID
    assert [n["node"] for n in doc["nodes"]] == ["NODE1", "NODE2"]
    assert {n["verdict"] for n in doc["nodes"]} == {"no_baseline"}  # triage is on by default
    assert {n["verdict"] for n in jcli("capture", captured, "--no-triage").json()["nodes"]} == {
        None
    }
    # a member set stands for its capture
    assert jcli("capture", "jira-test_node2").json()["capture"] == JOB_ID
    only = jcli("matrix", captured, "--spread", "only").json()
    assert only["schema"] == "matrix.v1" and {r["spread"] for r in only["items"]} == {"only"}
    merged = jcli("entries", captured, "--window", "union", "--columns", "ts,node").json()
    assert merged["logset"] == f"capture:{JOB_ID}" and merged["total"] > 0
    assert {r["node"] for r in merged["items"]} == {"NODE1", "NODE2"}
    assert set(merged["items"][0]) == {"ts", "node", "set"}
    raw = jcli("--color", "always", "entries", captured, "--window", "union", "--raw")
    assert raw.code == 0 and "NODE2 NODE2_atlassian-jira.log.2:" in raw.stdout  # the rule
    page = tmp_path / "cluster.html"
    written = jcli("report", captured, "--out", page).json()
    assert written["capture"] == JOB_ID and written["nodes"] == 2 and page.stat().st_size > 0
    assert 'id="cluster-data"' in page.read_text(encoding="utf-8")


def test_a_bare_capture_id_names_its_capture(jcli: Invoke, captured: str, tmp_path: Path) -> None:
    timeline = jcli("timeline", JOB_ID, "--bucket", "1d")
    assert timeline.code == 0 and timeline.json()["schema"] == "cluster-timeline.v1"
    merged = jcli("entries", JOB_ID, "--window", "union", "--columns", "id,node").json()
    assert merged["logset"] == f"capture:{JOB_ID}" and merged["total"] > 0
    written = jcli("report", JOB_ID, "--out", tmp_path / "c.html").json()
    assert written["capture"] == JOB_ID and written["nodes"] == 2
    # context --all-nodes: the anchor's set is the node that has the entry
    row = next(r for r in merged["items"] if r["node"] == "NODE2")
    around = jcli("context", JOB_ID, row["id"], "--all-nodes", "--columns", "id,node").json()
    assert {r["node"] for r in around["items"]} >= {"NODE2"}
    assert any(r["id"] == row["id"] and r["node"] == "NODE2" for r in around["items"])
    wide = jcli("context", JOB_ID, row["id"], "--all-nodes", "--window", "1e308")
    assert (wide.code, wide.json()["error"]["code"]) == (1, "invalid_value")  # was an overflow
    for ref in (JOB_ID, captured):
        missing = jcli("context", ref, "0" * 16, "--all-nodes")
        assert (missing.code, missing.json()["error"]["reason"]) == (2, "entry_not_found")
    # a unique prefix of the capture id, as capture:PREFIX takes it
    prefixed = jcli("capture", JOB_ID[:10])
    assert prefixed.code == 0 and prefixed.json()["capture"] == JOB_ID
    # a name that is neither a set nor a capture is still an unknown set
    unknown = jcli("entries", "nothing-here")
    assert unknown.code == 3 and unknown.json()["error"]["reason"] == "set_not_found"


def test_capture_usage_errors(jcli: Invoke, captured: str) -> None:
    triage = jcli("triage", captured)
    assert triage.code == 1 and triage.json()["error"]["code"] == "usage"
    assert f"ragnalog capture {captured} --triage" in triage.json()["error"]["hint"]
    for argv in (
        ("entries", "jira-test_node1", "--window", "union"),
        ("timeline", captured, "--signature", "aaaaaa,bbbbbb"),
        ("timeline", "jira-test_node1", "--node", "NODE1"),
        ("timeline", "jira-test_node1", "--window", "union"),
        ("report", captured, "--compare", "jira-test_node1"),
        ("report", "jira-test_node1", "--window", "union"),
    ):
        result = jcli(*argv)
        assert (result.code, result.json()["error"]["code"]) == (1, "usage"), argv
    unknown = jcli("capture", "capture:0000")
    assert unknown.code == 2 and unknown.json()["error"]["reason"] == "capture_not_found"


def test_context_all_nodes_needs_a_capture(jcli: Invoke, tmp_path: Path) -> None:
    files = write_node_files(tmp_path / "one", "NODE1", prefix=False)
    entry = jcli("ingest", *files, "--name", "lone").json()
    first = jcli("entries", "lone", "--limit", "1", "--columns", "id").json()["items"][0]["id"]
    result = jcli("context", "lone", first, "--all-nodes")
    error = result.json()["error"]
    assert result.code == 1 and error["code"] == "invalid_value"
    assert "ragnalog label lone" in error["hint"] and entry["capture"] is None


def test_capture_tables(cli: Invoke, archive: Path, store_dir: Path) -> None:
    base = ("--output", "table", "--width", "200", "--store", store_dir)
    assert cli(*base, "ingest", archive, "--cluster", "jira-test").code == 0
    ref = "cluster:jira-test"
    expected = {
        "capture": "nodes",
        "matrix": "groups by node",
        "events": "events",
        "interactions": "mentions",
        "timeline": "entries per 1h and node",
        "entries": "entries of capture:",
    }
    for command, text in expected.items():
        window = () if command in ("matrix", "entries") else ("--window", "union")
        result = cli(*base, command, ref, *window)
        assert result.code == 0 and text in result.stdout, (command, result.stderr)


def test_a_split_that_fails_halfway_removes_the_sets_it_added(
    jcli: Invoke, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ragnalog.store import pipeline
    from ragnalog.store.store import StoreError

    real = pipeline.ingest_sources
    calls: list[str | None] = []

    def second_node_fails(*args: object, **kwargs: Any) -> object:
        calls.append(kwargs.get("node"))
        if len(calls) == 2:
            raise StoreError("disk full")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "ingest_sources", second_node_fails)
    result = jcli("ingest", archive, "--cluster", "jira-test")
    assert result.code == 3 and calls == ["NODE1", "NODE2"]
    assert jcli("sets").json()["items"] == []  # NODE1's set is gone again
    monkeypatch.undo()
    assert len(jcli("ingest", archive, "--cluster", "jira-test").json()["sets"]) == 2


def test_a_failed_split_keeps_sets_that_existed(
    jcli: Invoke, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ragnalog.store import pipeline
    from ragnalog.store.store import StoreError

    before = jcli("ingest", archive, "--cluster", "jira-test").json()["sets"]
    real = pipeline.ingest_sources

    def second_node_fails(*args: object, **kwargs: Any) -> object:
        if kwargs.get("node") == "NODE2":
            raise StoreError("disk full")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "ingest_sources", second_node_fails)
    assert jcli("ingest", archive, "--overwrite").code == 3
    kept = {s["id"] for s in jcli("sets").json()["items"]}
    assert kept == {s["id"] for s in before}


def test_reingest_under_another_capture_keeps_the_sets_capture(
    jcli: Invoke, tmp_path: Path
) -> None:
    for node in ("NODE1", "NODE2"):
        write_node_files(tmp_path / "flat", node)
    first = jcli("ingest", tmp_path / "flat", "--capture", "cap-a")
    assert first.code == 0, first.stdout
    again = jcli("ingest", tmp_path / "flat", "--capture", "cap-b")
    assert again.code == 0, again.stdout
    doc = again.json()
    assert doc["capture"]["capture"] == "cap-a"
    assert any("--capture" in w or "capture" in w for w in doc["warnings"])
