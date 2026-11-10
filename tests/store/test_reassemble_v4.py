"""``upgrade`` of sets built by jira@3: headers glued onto the previous entry, cut chains."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from atlas_log_analyzer.core.formats import jira
from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.models import ExceptionInfo
from atlas_log_analyzer.store import LogSet, Store
from atlas_log_analyzer.store.pipeline import ingest_paths

TAB = "\t"
LINES = [
    "2026-10-29 11:00:00,000+0100 http-nio-8080-exec-1 url: /a/b INFO anonymous 1x2x3 s1"
    " 10.0.0.1 /a/b [org.acme.Web] first",
    "2026-10-29 11:00:01,000+0100 http-nio-8080-exec-2 url: /a/[b]/c WARN anonymous 2x3x4 s2"
    " 10.0.0.2 /a/[b]/c [org.acme.Web] bracket url",
    "  continuation of the bracket entry",
    "2026-10-29 11:00:02,000+0100 main ERROR      [org.acme.Job] uncaught",
    'Exception in thread "main" java.lang.IllegalStateException: boom',
    f"{TAB}at org.acme.Job.run(Job.java:10)",
    "2026-10-29 11:00:03,000+0100 main ERROR      [org.acme.Job] gap",
    "org.acme.FooException: top",
    f"{TAB}at org.acme.A.b(A.java:1)",
    "",
    "Caused by: org.acme.BarError: below",
    f"{TAB}at org.acme.C.d(C.java:2)",
    "2026-10-29 11:00:04,000+0100 main INFO      [org.acme.Job] last",
]


class JiraV3(JiraFormat):
    """Jira as format version 3 recorded itself."""

    version = 3


def _v3_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse as jira@3 did: no ``[`` in the request context, chains cut at a blank line."""
    current = jira.parse_exceptions
    old_logger = jira._LOGGER_RE

    def old_logger_at(line: str, start: int) -> tuple[int, re.Match[str]] | None:
        bracket = line.find("[", start)
        if bracket < 0 or line[bracket - 1] != " ":
            return None
        m = old_logger.match(line, bracket)
        return None if m is None else (bracket, m)

    def old_chain(lines: Sequence[str]) -> tuple[ExceptionInfo, ...]:
        kept = [line for line in lines if not line.startswith("Exception in thread ")]
        if "" in kept:
            kept = kept[: kept.index("")]
        return current(kept)

    monkeypatch.setattr(jira, "_logger_at", old_logger_at)
    monkeypatch.setattr(jira, "parse_exceptions", old_chain)


def _tables(ls: LogSet) -> tuple[pl.DataFrame, pl.DataFrame]:
    return pl.read_parquet(ls.entries_path), pl.read_parquet(ls.groups_path)


def _manifest(ls: LogSet) -> dict[str, object]:
    doc = ls.manifest.to_dict()
    doc.pop("created_at")
    return doc


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
@pytest.mark.parametrize("row_group_size", [None, 2])
def test_upgrade_from_v3_splits_and_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, eol: str, row_group_size: int | None
) -> None:
    log = tmp_path / "v4.log"
    log.write_bytes((eol.join(LINES) + eol).encode("utf-8"))
    new = ingest_paths(Store(tmp_path / "new"), [log], name="fx").logset
    old_store = Store(tmp_path / "old")
    with monkeypatch.context() as patch:
        _v3_parse(patch)
        old = ingest_paths(old_store, [log], name="fx", fmt=JiraV3()).logset
    stale = pl.read_parquet(old.entries_path)
    assert stale.height == new.manifest.entry_count - 1  # the bracket header was glued on
    assert stale["exception_chain"].list.len().to_list()[1:3] == [0, 1]
    if row_group_size is not None:
        pq.write_table(pq.read_table(old.entries_path), old.entries_path, row_group_size=2)
    log.unlink()  # upgrade reads the stored entries only

    assert old_store.upgrade("fx").upgraded
    up = old_store.open("fx")
    entries, groups = _tables(up)
    want_entries, want_groups = _tables(new)
    assert entries.equals(want_entries)
    assert groups.equals(want_groups)
    assert _manifest(up) == _manifest(new)
    assert entries["message"].to_list()[1] == "bracket url"
    assert entries["exception_chain"].list.len().to_list()[2:4] == [1, 2]
