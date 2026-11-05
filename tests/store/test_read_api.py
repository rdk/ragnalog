"""Read API of :class:`LogSet` used by the web app, typed store errors, ingest pipeline."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.core.reader import discover_sources, iter_sources
from atlas_log_analyzer.store import (
    AmbiguousLogSet,
    FormatNotDetected,
    IngestProgress,
    LogSetNotFound,
    NoSourcesFound,
    Store,
    StoreError,
    ingest_paths,
    rows,
    to_records,
)
from atlas_log_analyzer.store.levels import LEVEL_NAMES, InvalidLevel, level_values, parse_levels
from atlas_log_analyzer.store.logset import MANIFEST_FILE

from .conftest import Built
from .synth import BASE

MINI = Path(__file__).resolve().parent.parent / "cli" / "data" / "mini.log"


def test_count_entries_and_offset(built: Built) -> None:
    ls = built.logset
    all_ids = ls.entries(columns=["id"])["id"].to_list()
    assert ls.count_entries() == len(all_ids)
    assert ls.entries(offset=5, limit=3, columns=["id"])["id"].to_list() == all_ids[5:8]
    assert ls.entries(offset=len(all_ids) - 2, columns=["id"]).height == 2
    filter_sets: list[dict[str, Any]] = [
        {"level": "ERROR"},
        {"min_level": "WARN", "user": "user1"},
        {"contains": "abc-1"},
        {"since": BASE + timedelta(minutes=1)},
        {"logger": "com.atlassian"},
    ]
    for filters in filter_sets:
        assert ls.count_entries(**filters) == ls.entries(**filters).height


def test_contains_is_literal_and_case_insensitive(built: Built) -> None:
    ls = built.logset
    assert ls.count_entries(contains="ABC-1") == ls.count_entries(contains="abc-1") > 0
    assert ls.count_entries(contains=".*") == 0  # regex metacharacters are literal


def test_level_param_forms(built: Built) -> None:
    ls = built.logset
    n = ls.count_entries(level="WARN,ERROR")
    assert ls.count_entries(level=[LogLevel.WARN, LogLevel.ERROR]) == n
    assert ls.count_entries(level=("WARN", LogLevel.ERROR)) == n
    assert ls.count_entries(level=LogLevel.ERROR) == ls.count_entries(level="ERROR")
    assert ls.count_entries(min_level=LogLevel.WARN) == ls.count_entries(min_level="WARN")
    assert ls.groups(level=[LogLevel.ERROR]).height == ls.groups(level="ERROR").height
    with pytest.raises(ValueError):
        ls.count_entries(level=["NOPE"])
    with pytest.raises(ValueError):
        ls.count_entries(level=[])
    with pytest.raises(ValueError):
        ls.count_entries(min_level=[LogLevel.WARN, LogLevel.ERROR])


def test_level_names_shared_with_front_ends(built: Built) -> None:
    ls = built.logset
    total = ls.count_entries()
    assert ls.count_entries(level="") == ls.count_entries(level=" , ") == total
    assert ls.count_entries(min_level="") == total
    assert ls.count_entries(level="warning") == ls.count_entries(level="WARN")
    assert ls.count_entries(level="UNKNOWN") == ls.count_entries(level=[LogLevel.UNKNOWN])
    for alias in ("SEVERE", "FINE", "FINEST"):  # input-format aliases are not filter names
        with pytest.raises(InvalidLevel, match="expected one of TRACE"):
            ls.count_entries(level=alias)


def test_parse_levels() -> None:
    assert parse_levels(None) is None and parse_levels("") is None and parse_levels(" ,") is None
    assert parse_levels("warn, Error,WARNING") == "WARN,ERROR"
    assert parse_levels("unknown") == "UNKNOWN"
    assert level_values("INFO,FATAL") == [30, 60] and level_values("") == []
    assert set(LEVEL_NAMES) == {lv.name for lv in LogLevel}
    with pytest.raises(InvalidLevel, match=r"unknown level 'loud'.*WARNING = WARN"):
        parse_levels("INFO, loud")
    assert issubclass(InvalidLevel, ValueError)


def test_count_groups_and_offset(built: Built) -> None:
    ls = built.logset
    assert ls.count_groups() == ls.groups().height == ls.manifest.group_count
    assert ls.count_groups(level="ERROR") == ls.groups(level="ERROR").height
    assert ls.count_groups(coarse=True) == ls.groups(coarse=True).height
    sigs = ls.groups()["signature"].to_list()
    assert ls.groups(offset=1, limit=2)["signature"].to_list() == sigs[1:3]


def test_group(built: Built) -> None:
    ls = built.logset
    sig = ls.groups()["signature"][0]
    g = ls.group(sig)
    assert g is not None and g["signature"] == sig
    assert g["count"] == built.grouper.groups[sig].count
    assert isinstance(g["first_seen"], str) and g["first_seen"].endswith("+00:00")
    assert ls.group("0" * 16) is None
    top = ls.groups(coarse=True).row(0, named=True)
    cg = ls.group(top["signature"], coarse=True)
    assert cg is not None
    assert cg["signature"] == top["signature"] and cg["count"] == top["count"]
    assert sorted(cg["signatures"]) == sorted(top["signatures"])
    assert ls.group("0" * 16, coarse=True) is None


def test_histogram(built: Built) -> None:
    ls = built.logset
    with_ts = ls.count_entries(since=datetime(1970, 1, 1, tzinfo=UTC))
    for bucket in ("1h", "1m", "15s"):
        hist = ls.histogram(bucket)
        assert sum(b["count"] for b in hist) == with_ts
        assert all(b["bucket"].endswith("Z") for b in hist)
        assert [b["bucket"] for b in hist] == sorted(b["bucket"] for b in hist)
    minute = ls.histogram("1m")
    starts = [datetime.fromisoformat(b["bucket"]) for b in minute]
    assert all(b - a == timedelta(minutes=1) for a, b in pairwise(starts))
    assert all(s.second == 0 for s in starts)
    err = ls.histogram("1m", level="ERROR")
    assert sum(b["count"] for b in err) == ls.count_entries(level="ERROR")
    sig = ls.groups()["signature"][0]
    by_sig = ls.histogram(signature=sig)
    assert sum(b["count"] for b in by_sig) == ls.count_entries(signature=sig)
    assert ls.histogram(until=datetime(1970, 1, 1, tzinfo=UTC)) == []
    for bad in ("", "1y", "0h", "h"):
        with pytest.raises(ValueError):
            ls.histogram(bad)


def test_stats_single_scan_matches(built: Built) -> None:
    s = built.logset.stats()
    assert s["entry_count"] == built.logset.count_entries()
    assert sum(s["levels"].values()) == s["entry_count"]


def test_context_is_dict_everywhere(built: Built) -> None:
    df = built.logset.entries(columns=["id", "context"], limit=20)
    assert all(isinstance(r["context"], dict) for r in rows(df))
    assert all(isinstance(r["context"], dict) for r in to_records(df))


def test_sql_sandbox_and_max_rows(built: Built, tmp_path: Path) -> None:
    ls = built.logset
    assert ls.sql("select id from entries", max_rows=4).height == 4
    other = tmp_path / "x.csv"
    other.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(Exception, match=r"(?i)permission|disabled"):
        ls.sql(f"select * from read_csv('{other.as_posix()}')")
    with pytest.raises(Exception, match=r"(?i)lock"):
        ls.sql("set enable_external_access = true")


def test_typed_open_errors(built: Built) -> None:
    store = built.store
    with pytest.raises(LogSetNotFound, match="no log set"):
        store.open("nope")
    assert issubclass(LogSetNotFound, StoreError) and issubclass(AmbiguousLogSet, StoreError)
    m = built.logset.manifest
    for new_id in ("abcd0001", "abcd0002"):
        d = store.root / new_id
        d.mkdir()
        replace(m, id=new_id, name="twin").write(d)
    with pytest.raises(AmbiguousLogSet, match="id prefix 'abcd' is ambiguous"):
        store.open("abcd")
    with pytest.raises(AmbiguousLogSet, match="name 'twin' is ambiguous"):
        store.open("twin")


def test_open_uses_manifest_cache(built: Built) -> None:
    store = Store(built.store.root)
    a = store.open("prod-a")
    assert store.open("prod-a").manifest is a.manifest  # cached, not reparsed
    replace(a.manifest, name="renamed").write(a.path)
    manifest = a.path / MANIFEST_FILE
    st = manifest.stat()
    os.utime(manifest, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert store.open("renamed").manifest.id == a.id
    with pytest.raises(LogSetNotFound):
        store.open("prod-a")


def test_discover_sources_alias() -> None:
    assert iter_sources is discover_sources
    assert [s.path for s in discover_sources([MINI])] == [MINI.resolve()]


def test_ingest_paths_progress(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    seen: list[IngestProgress] = []
    result = ingest_paths(store, [MINI], name="mini", progress=seen.append, progress_every=3)
    ls = result.logset
    assert not result.existing and result.parsed_entries == ls.manifest.entry_count == 10
    assert ls.manifest.name == "mini" and ls.manifest.format == "jira"
    assert [p.entries for p in seen] == [3, 6, 9]
    assert all(p.bytes_total == MINI.stat().st_size for p in seen)
    assert all(0 < p.bytes_read <= p.bytes_total for p in seen)
    assert [p.bytes_read for p in seen] == sorted(p.bytes_read for p in seen)
    assert seen[0].current_file == str(MINI.resolve())
    assert result.ingest_dict()["existing"] is False
    again = ingest_paths(store, [MINI], fmt="jira")
    assert again.existing and again.parsed_entries == 0 and again.logset.id == ls.id
    rebuilt = ingest_paths(store, [MINI], overwrite=True, frames=1)
    assert rebuilt.logset.manifest.signature_spec.endswith("F1")


def test_ingest_paths_errors(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(NoSourcesFound):
        ingest_paths(store, [empty])
    junk = tmp_path / "junk.log"
    junk.write_text("not a log line\n", encoding="utf-8")
    with pytest.raises(FormatNotDetected):
        ingest_paths(store, [junk])


ACCESS_LINE = (
    '10.0.0.1 1x2x3 jdoe [29/Oct/2026:02:19:41 +0100] "GET /rest/api/2/issue HTTP/1.1" 200\n'
)
GC_LINE = "[2026-10-29T02:19:41.418+0100][12.123s] GC(1) Pause Young (Normal) 512M->128M 12.3ms\n"


def mixed_dir(tmp_path: Path) -> Path:
    """A Jira log directory: atlassian-jira.log plus an access and a GC log, the GC log oldest."""
    folder = tmp_path / "log"
    folder.mkdir()
    (folder / "atlassian-jira.log").write_bytes(MINI.read_bytes())
    (folder / "atlassian-jira-http-access.log").write_text(ACCESS_LINE * 500, encoding="utf-8")
    gc = folder / "atlassian-jira-gc-2026-10-29_02-00-00.log.0.current"
    gc.write_text(GC_LINE * 500, encoding="utf-8")
    old = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
    os.utime(gc, (old, old))
    return folder


def test_ingest_paths_skips_foreign_files_in_directories(tmp_path: Path) -> None:
    folder = mixed_dir(tmp_path)
    result = ingest_paths(Store(tmp_path / "store"), [folder])
    m = result.logset.manifest
    assert m.format == "jira" and m.entry_count == 10 and result.parse_errors == 0
    assert [Path(s.path).name for s in m.sources] == ["atlassian-jira.log"]
    skipped = sorted(Path(p).name for p in result.skipped)
    assert skipped == [
        "atlassian-jira-gc-2026-10-29_02-00-00.log.0.current",
        "atlassian-jira-http-access.log",
    ]
    assert result.ingest_dict()["skipped"] == list(result.skipped)


def test_named_files_are_always_read(tmp_path: Path) -> None:
    folder = mixed_dir(tmp_path)
    access = folder / "atlassian-jira-http-access.log"
    result = ingest_paths(Store(tmp_path / "store"), [folder / "atlassian-jira.log", access])
    assert result.skipped == () and result.parse_errors == 1
    assert len(result.logset.manifest.sources) == 2


def test_select_sources_without_format_files(tmp_path: Path) -> None:
    from atlas_log_analyzer.store.pipeline import select_sources

    folder = tmp_path / "only-access"
    folder.mkdir()
    (folder / "access.log").write_text(ACCESS_LINE * 3, encoding="utf-8")
    with pytest.raises(NoSourcesFound, match="no jira log files"):
        select_sources([folder], "jira")


def test_large_named_file_not_in_format_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas_log_analyzer.store.pipeline as pipeline

    folder = mixed_dir(tmp_path)
    access = folder / "atlassian-jira-http-access.log"
    jira_log = folder / "atlassian-jira.log"
    monkeypatch.setattr(pipeline, "MAX_FOREIGN_BYTES", jira_log.stat().st_size - 1)
    with pytest.raises(pipeline.NotInFormat, match=r"atlassian-jira-http-access.log \(0 MB\)"):
        pipeline.select_sources([jira_log, access])
    with pytest.raises(pipeline.NotInFormat, match="none of its first 200 lines"):
        ingest_paths(Store(tmp_path / "store"), [access], fmt="jira")
    assert not (tmp_path / "store").exists()
    # in the format, found in a directory, or forced: no refusal
    assert [s.path.name for s in pipeline.select_sources([jira_log]).sources] == [jira_log.name]
    assert len(pipeline.select_sources([folder]).skipped) == 2
    forced = pipeline.select_sources([jira_log, access], force=True)
    assert len(forced.sources) == 2
    result = ingest_paths(Store(tmp_path / "store"), [access], fmt="jira", force=True)
    assert result.parse_errors == 1


def test_reingest_under_another_name_warns(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    first = ingest_paths(store, [MINI], name="night")
    assert first.warnings == () and first.ingest_dict()["warnings"] == []
    again = ingest_paths(store, [MINI], name="tonight")
    assert again.existing and again.logset.manifest.name == "night"
    assert again.warnings == ("--name tonight ignored: set exists as 'night'",)
    assert again.ingest_dict()["warnings"] == list(again.warnings)
    assert ingest_paths(store, [MINI], name="night").warnings == ()
    assert ingest_paths(store, [MINI]).warnings == ()
    renamed = ingest_paths(store, [MINI], name="tonight", overwrite=True)
    assert renamed.warnings == () and renamed.logset.manifest.name == "tonight"
    unnamed = Store(tmp_path / "other")
    set_id = ingest_paths(unnamed, [MINI]).logset.manifest.id
    assert ingest_paths(unnamed, [MINI], name="x").warnings == (
        f"--name x ignored: set exists as {set_id} (unnamed)",
    )
