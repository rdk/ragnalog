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
    assert store.open("prod-a") is a  # cached, not reparsed
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
