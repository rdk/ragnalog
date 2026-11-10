"""ZIP inputs and the split into one set per node (``store/split.py``)."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from atlas_log_analyzer.core.reader import discover_sources
from atlas_log_analyzer.store.split import (
    ArchiveError,
    SplitError,
    expand_archives,
    extract_zip,
    plan_split,
    read_job,
    safe_node,
)
from tests.cluster import (
    JOB_ID,
    export_zip,
    job_json,
    node_lines,
    write_export,
    write_node_files,
)

# --- ZIP inputs


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_extracts_job_and_logs_only(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "x.zip",
        {
            "job.json": b"{}",
            "NODE1_atlassian-jira.log": b"a\n",
            "sub/NODE2_atlassian-jira.log.1": b"b\n",
            "readme.txt": b"no",
            "dir/": b"",
        },
    )
    budget = [1 << 20]
    assert extract_zip(archive, tmp_path / "out", budget=budget) == 3
    found = sorted(
        p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*")
    )
    assert found == [
        "NODE1_atlassian-jira.log",
        "job.json",
        "sub",
        "sub/NODE2_atlassian-jira.log.1",
    ]
    assert budget[0] == (1 << 20) - 6


@pytest.mark.parametrize(
    "name",
    ["../evil.log", "a/../../evil.log", "/abs/evil.log", "C:/evil.log", "C:evil.log", "..\\x.log"],
)
def test_refuses_unsafe_member_paths(tmp_path: Path, name: str) -> None:
    archive = _zip(tmp_path / "x.zip", {name: b"x\n"})
    with pytest.raises(ArchiveError, match="unsafe path"):
        extract_zip(archive, tmp_path / "out", budget=[1 << 20])
    assert not (tmp_path / "evil.log").exists()


def test_skips_links_and_other_special_entries(tmp_path: Path) -> None:
    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        link = zipfile.ZipInfo("link_atlassian-jira.log")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
        fifo = zipfile.ZipInfo("fifo.log")
        fifo.external_attr = (stat.S_IFIFO | 0o644) << 16
        zf.writestr(fifo, "")
        zf.writestr("ok.log", "fine\n")
    assert extract_zip(archive, tmp_path / "out", budget=[1 << 20]) == 1
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["ok.log"]


def test_caps_the_uncompressed_size(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "x.zip", {"a.log": b"x" * 1000, "b.log": b"y" * 1000})
    with pytest.raises(ArchiveError, match="more than"):
        extract_zip(archive, tmp_path / "out", budget=[1500])


def test_unreadable_zip_is_an_archive_error(tmp_path: Path) -> None:
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ArchiveError, match="not a readable ZIP"):
        extract_zip(bad, tmp_path / "out", budget=[1 << 20])


def test_expand_replaces_zips_and_cleans_up(tmp_path: Path) -> None:
    archive = export_zip(tmp_path / "export.zip")
    other = write_node_files(tmp_path / "plain", "NODE3", parts=1)[0]
    spool = tmp_path / "store" / ".spool"
    with expand_archives([archive, other], spool) as inputs:
        assert inputs[1] == other
        assert inputs[0].name == "export" and (inputs[0] / "job.json").is_file()
        assert (inputs[0] / "NODE1_atlassian-jira.log.2").is_file()
    assert not spool.exists()


def test_expand_cleans_up_after_an_error(tmp_path: Path) -> None:
    archive = export_zip(tmp_path / "export.zip")
    spool = tmp_path / "store" / ".spool"
    with pytest.raises(RuntimeError), expand_archives([archive], spool):
        raise RuntimeError("boom")
    assert not spool.exists()
    with pytest.raises(ArchiveError), expand_archives([archive], spool, max_bytes=10):
        pass
    assert not spool.exists()


def test_expand_descends_into_a_single_folder(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "x.zip", {"export/inner/job.json": b"{}", "export/inner/a.log": b"x"})
    with expand_archives([archive], tmp_path / ".spool") as inputs:
        assert inputs[0].name == "inner"


def test_paths_without_zips_pass_unchanged(tmp_path: Path) -> None:
    with expand_archives([tmp_path], tmp_path / ".spool") as inputs:
        assert inputs == [tmp_path]
    assert not (tmp_path / ".spool").exists()


# --- job.json


def test_read_job(tmp_path: Path) -> None:
    path = tmp_path / "job.json"
    path.write_text(job_json(failed="NODE2"), encoding="utf-8")
    job = read_job(path)
    assert job is not None and job.id == JOB_ID
    assert job.nodes == (("NODE1", "OK", None), ("NODE2", "FAILED", "timed out"))
    path.write_text("[1, 2]", encoding="utf-8")
    assert read_job(path) is None
    path.write_text("{not json", encoding="utf-8")
    assert read_job(path) is None


# --- the split


def _names(split_nodes: object) -> dict[str, list[str]]:
    return {n.node: [p.name for p in n.files] for n in split_nodes}  # type: ignore[attr-defined]


def test_export_folder_splits_by_job(tmp_path: Path) -> None:
    folder = write_export(tmp_path / "export")
    split = plan_split([folder])
    assert split.kind == "job" and split.job is not None and split.job.id == JOB_ID
    assert list(_names(split.nodes)) == ["NODE1", "NODE2"]
    assert sorted(_names(split.nodes)["NODE1"]) == [
        "NODE1_atlassian-jira.log",
        "NODE1_atlassian-jira.log.1",
        "NODE1_atlassian-jira.log.2",
    ]
    assert split.paths == (folder,) and split.warnings == ()


def test_job_warns_about_failed_nodes_and_stray_files(tmp_path: Path) -> None:
    folder = write_export(tmp_path / "export", ("NODE1",))
    (folder / "job.json").write_text(job_json(("NODE1", "NODE2"), failed="NODE2"), "utf-8")
    write_node_files(folder, "OTHER", parts=1, prefix=False)
    split = plan_split([folder])
    assert split.kind == "job" and list(_names(split.nodes)) == ["NODE1"]
    assert split.warnings == (
        "job.json: node NODE2 reported status FAILED: timed out",
        "1 file(s) belong to no node and are not read: atlassian-jira.log",
    )


def test_given_job_json_is_never_a_source(tmp_path: Path) -> None:
    folder = write_export(tmp_path / "export")
    files = sorted(folder.glob("*.log*"))
    split = plan_split([folder / "job.json", *files])
    assert split.kind == "job" and folder / "job.json" not in split.paths
    none = plan_split([folder / "job.json", *files], "none")
    assert none.kind is None and folder / "job.json" not in none.paths and none.job is None


def test_prefixes_split_without_job(tmp_path: Path) -> None:
    for node in ("NODE1", "NODE2"):
        write_node_files(tmp_path, node, parts=2)
    split = plan_split([tmp_path])
    assert split.kind == "prefix" and split.job is None
    assert _names(split.nodes)["NODE2"] == [
        "NODE2_atlassian-jira.log",
        "NODE2_atlassian-jira.log.1",
    ]


def test_rotation_orders_within_each_node(tmp_path: Path) -> None:
    for node in ("NODE1", "NODE2"):
        write_node_files(tmp_path, node, parts=11)
    split = plan_split([tmp_path])
    node1 = set(split.nodes[0].files)
    order = [s.path.name for s in discover_sources(split.paths) if s.path in node1]
    assert order == [
        "NODE1_atlassian-jira.log.10",
        *(f"NODE1_atlassian-jira.log.{n}" for n in range(9, 0, -1)),
        "NODE1_atlassian-jira.log",
    ]


@pytest.mark.parametrize(
    "names",
    [
        ["NODE1_atlassian-jira.log"],  # one node only
        ["2026_11_04_atlassian-jira.log", "2026_11_05_atlassian-jira.log"],  # dates, no node
        ["NODE1_app.log", "NODE2_other.log"],  # no log name in common
        ["atlassian-jira.log", "atlassian-jira.log.1"],
    ],
)
def test_auto_keeps_one_set(tmp_path: Path, names: list[str]) -> None:
    for name in names:
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    split = plan_split([tmp_path])
    assert split.kind is None and split.nodes == () and split.paths == (tmp_path,)


def test_forced_prefix(tmp_path: Path) -> None:
    write_node_files(tmp_path, "NODE1", parts=1)
    assert plan_split([tmp_path]).kind is None
    split = plan_split([tmp_path], "prefix")
    assert split.kind == "prefix" and list(_names(split.nodes)) == ["NODE1"]
    plain = write_node_files(tmp_path / "plain", "X", parts=1, prefix=False)[0]
    with pytest.raises(SplitError, match="--split prefix"):
        plan_split([plain], "prefix")


def test_node_directories(tmp_path: Path) -> None:
    for node in ("node-a", "node-b"):
        write_node_files(tmp_path / "logs" / node, node.upper(), parts=2, prefix=False)
    (tmp_path / "logs" / "empty").mkdir()
    split = plan_split([tmp_path / "logs"])
    assert split.kind == "dir" and list(_names(split.nodes)) == ["node-a", "node-b"]
    assert split.paths == (tmp_path / "logs" / "node-a", tmp_path / "logs" / "node-b")
    forced = plan_split([tmp_path / "logs" / "node-a", tmp_path / "logs" / "node-b"], "dir")
    assert forced.kind == "dir" and list(_names(forced.nodes)) == ["node-a", "node-b"]
    # several directories are one set unless --split dir says otherwise
    assert plan_split([tmp_path / "logs" / "node-a", tmp_path / "logs" / "node-b"]).kind is None
    assert plan_split([tmp_path / "logs"], "none").kind is None


def test_a_file_prefix_names_a_node_directory(tmp_path: Path) -> None:
    write_node_files(tmp_path / "logs" / "first", "NODE1", parts=2)
    write_node_files(tmp_path / "logs" / "second", "NODE2", parts=1)
    (tmp_path / "logs" / "second" / "atlassian-jira.log").write_text("x\n", encoding="utf-8")
    split = plan_split([tmp_path / "logs"])
    assert split.kind == "dir" and list(_names(split.nodes)) == ["NODE1", "second"]


def test_dir_split_errors(tmp_path: Path) -> None:
    write_node_files(tmp_path / "flat", "NODE1", parts=1, prefix=False)
    with pytest.raises(SplitError, match="--split dir"):
        plan_split([tmp_path / "flat"], "dir")
    write_node_files(tmp_path / "a" / "same", "NODE1", parts=1, prefix=False)
    write_node_files(tmp_path / "b" / "same", "NODE2", parts=1, prefix=False)
    with pytest.raises(SplitError, match="named same"):
        plan_split([tmp_path / "a" / "same", tmp_path / "b" / "same"], "dir")


def test_safe_node() -> None:
    assert safe_node("node 1 (old)") == "node_1_old"
    assert safe_node("NODE1") == "NODE1"
    assert safe_node("...") == "node"


# --- layouts one or two levels deep (support ZIPs, nested exports)

CATALINA = "01-Nov-2026 10:00:00.000 INFO [main] org.acme.Startup Server started\n"
ACCESS = '10.0.0.1 - - [01/Nov/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 5\n'


def _support_members(prefix: str = "", node: str = "NODE1") -> dict[str, bytes]:
    """The members of a single-node support ZIP (invented content)."""
    return {
        f"{prefix}application-logs/atlassian-jira.log": node_lines(node, 0).encode(),
        f"{prefix}application-logs/atlassian-jira.log.1": node_lines(node, 1).encode(),
        f"{prefix}application-properties/application.xml": b"<x/>",
        f"{prefix}tomcat-logs/catalina.2026-11-01.log": CATALINA.encode(),
        f"{prefix}tomcat-logs/localhost_access_log.2026-11-01.txt": ACCESS.encode(),
    }


def test_a_support_zip_is_one_set_of_its_application_logs(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "Jira_support_2026-11-01.zip", _support_members())
    with expand_archives([archive], tmp_path / ".spool") as inputs:
        split = plan_split(inputs)
        assert split.kind is None and split.nodes == () and split.warnings == ()
        assert [p.name for p in split.paths] == ["application-logs"]
        found = [s.path.name for s in discover_sources(split.paths)]
        assert found == ["atlassian-jira.log.1", "atlassian-jira.log"]
        assert plan_split(inputs, "none").paths == split.paths


def test_zip_with_node_folders_one_level_deeper_splits_by_dir(tmp_path: Path) -> None:
    members = {**_support_members("node1/", "NODE1"), **_support_members("node2/", "NODE2")}
    archive = _zip(tmp_path / "both.zip", members)
    with expand_archives([archive], tmp_path / ".spool") as inputs:
        split = plan_split(inputs)
        assert split.kind == "dir" and list(_names(split.nodes)) == ["node1", "node2"]
        assert [p.relative_to(inputs[0]).as_posix() for p in split.paths] == [
            "node1/application-logs",
            "node2/application-logs",
        ]
        assert _names(split.nodes)["node2"] == ["atlassian-jira.log", "atlassian-jira.log.1"]


def test_zip_with_job_json_and_a_logs_folder_splits_by_job(tmp_path: Path) -> None:
    members = {"job.json": job_json().encode()}
    for node in ("NODE1", "NODE2"):
        members[f"logs/{node}_atlassian-jira.log"] = node_lines(node, 0).encode()
    archive = _zip(tmp_path / "export.zip", members)
    with expand_archives([archive], tmp_path / ".spool") as inputs:
        split = plan_split(inputs)
        assert split.kind == "job" and split.job is not None and split.job.id == JOB_ID
        assert list(_names(split.nodes)) == ["NODE1", "NODE2"]
        assert [p.name for p in split.paths] == ["logs"] and split.warnings == ()


def test_zip_errors_name_the_zip_not_the_spool(tmp_path: Path) -> None:
    from atlas_log_analyzer.store.pipeline import NoSourcesFound, select_sources

    archive = _zip(tmp_path / "nothing.zip", {"a/b/readme.txt": b"x", "a/c/notes.txt": b"y"})
    spool = tmp_path / "store" / ".spool"
    with pytest.raises(NoSourcesFound) as caught, expand_archives([archive], spool) as inputs:
        select_sources(plan_split(inputs).paths)
    assert str(caught.value) == f"no log files found in {archive}"
    assert ".spool" not in str(caught.value) and not spool.exists()


def test_a_cancelled_zip_ingest_leaves_nothing_in_the_spool(tmp_path: Path) -> None:
    from atlas_log_analyzer.store.pipeline import ingest_paths
    from atlas_log_analyzer.store.store import Store

    class Cancelled(Exception):
        pass

    def cancel(progress: object) -> None:
        raise Cancelled

    archive = _zip(tmp_path / "logs.zip", {"atlassian-jira.log": node_lines("NODE1", 0).encode()})
    store = Store(tmp_path / "store")
    spool = store.root / ".spool"
    with pytest.raises(Cancelled), expand_archives([archive], spool) as inputs:
        ingest_paths(store, inputs, progress=cancel, progress_every=1)
    # the parser is closed before the spool is removed: on Windows an open file would stay
    assert not spool.exists()


def test_zip_members_are_recorded_as_zip_paths(tmp_path: Path) -> None:
    """The spool is gone after the ingest: manifests and skipped lists name the ZIP."""
    from atlas_log_analyzer.service.ingest import ingest_paths
    from atlas_log_analyzer.store import Store
    from atlas_log_analyzer.store.split import shown_path

    members = dict(_support_members())
    members["application-logs/access.log"] = b"10.0.0.1 - - [01/Nov/2026] GET / 200\n" * 3
    archive = _zip(tmp_path / "support.zip", members)
    store = Store(tmp_path / "store")
    doc = ingest_paths(store, [archive], front="cli")
    paths = [s.path for s in store.open(doc["id"]).manifest.sources]
    zip_path = str(archive)
    assert paths and all(p.startswith(zip_path + "/application-logs/") for p in paths)
    assert doc["ingest"]["skipped"] == [zip_path + "/application-logs/access.log"]
    # entries and groups name the same files, so the report can point at them
    import polars as pl

    from atlas_log_analyzer.report import build_payload

    ls = store.open(doc["id"])
    stored = set(pl.read_parquet(ls.entries_path)["source_path"].cast(pl.String).unique())
    assert stored == set(paths)
    named = {v["value"] for row in pl.read_parquet(ls.groups_path)["sources"] for v in row}
    assert named <= set(paths)
    payload = build_payload(ls)
    assert all(i >= 0 for g in payload["groups"] for i, _ in g["sources"])
    assert shown_path(str(tmp_path / "x.log")) == str(tmp_path / "x.log")  # outside a ZIP


def test_node_folders_under_one_folder_split_by_dir(tmp_path: Path) -> None:
    """case/logs/node1 and case/logs/node2: two nodes, not one set of both."""
    for node in ("node1", "node2"):
        d = tmp_path / "case" / "logs" / node
        d.mkdir(parents=True)
        (d / "atlassian-jira.log").write_text(node_lines(node.upper(), 0), encoding="utf-8")
    split = plan_split([tmp_path / "case"])
    assert split.kind == "dir" and [n.node for n in split.nodes] == ["node1", "node2"]


def test_a_job_json_above_the_input_is_not_its_capture(tmp_path: Path) -> None:
    """An old export's job.json in the parent folder is not today's capture, though it
    names the same nodes (a cluster keeps its node names)."""
    old = job_json(job_id="2020-01-01_00-00-00-000_OLD")
    (tmp_path / "job.json").write_text(old, "utf-8")
    today = tmp_path / "today"
    today.mkdir()
    for node in ("NODE1", "NODE2"):
        (today / f"{node}_atlassian-jira.log").write_text(node_lines(node, 0), encoding="utf-8")
    split = plan_split([today])
    assert split.job is None or split.job.id != "2020-01-01_00-00-00-000_OLD"


def test_export_logs_given_finds_the_job_beside_it(tmp_path: Path) -> None:
    """export/logs given: the job.json beside it names these files' nodes, so it counts."""
    (tmp_path / "job.json").write_text(job_json(), "utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    for node in ("NODE1", "NODE2"):
        (logs / f"{node}_atlassian-jira.log").write_text(node_lines(node, 0), encoding="utf-8")
    split = plan_split([logs])
    assert split.kind == "job" and split.job is not None and split.job.id == JOB_ID


def test_one_nodes_log_folders_stay_one_set(tmp_path: Path) -> None:
    """jira-node1/{application-logs, application-logs-archive}: one node, not two."""
    for name in ("application-logs", "application-logs-archive"):
        d = tmp_path / "case" / "jira-node1" / name
        d.mkdir(parents=True)
        (d / "atlassian-jira.log").write_text(node_lines("NODE1", 0), encoding="utf-8")
    assert plan_split([tmp_path / "case"]).kind is None


def test_node_folders_named_like_logs_still_split(tmp_path: Path) -> None:
    """case/export/{logs-node1, logs-node2}: node folders, though their names start with logs."""
    for node in ("logs-node1", "logs-node2"):
        d = tmp_path / "case" / "export" / node
        d.mkdir(parents=True)
        (d / "atlassian-jira.log").write_text(node_lines("NODE1", 0), encoding="utf-8")
    split = plan_split([tmp_path / "case"])
    assert split.kind == "dir" and [n.node for n in split.nodes] == ["logs-node1", "logs-node2"]


def test_members_that_are_not_extracted_are_not_judged(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "x.zip",
        {"NODE1_atlassian-jira.log": b"a\n", "notes/12:30 dump.txt": b"n", "../readme.md": b"r"},
    )
    assert extract_zip(archive, tmp_path / "out", budget=[1 << 20]) == 1


def test_gc_logs_with_a_time_suffix_are_no_nodes(tmp_path: Path) -> None:
    write_node_files(tmp_path, "NODE1", parts=2, prefix=False)
    for day in ("2026-01-01", "2026-02-01"):
        name = f"atlassian-jira-gc-{day}_03-00-00.log"
        (tmp_path / name).write_text("[0.001s][info][gc] Using G1\n", encoding="utf-8")
    split = plan_split([tmp_path])
    assert split.kind is None and split.nodes == ()
