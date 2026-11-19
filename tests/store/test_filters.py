"""Typed filters (``store/filters.py``) and the ``where=`` parameter of the read API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ragnalog.core.models import LogLevel
from ragnalog.store.filters import EntryFilter, GroupFilter
from ragnalog.store.levels import InvalidLevel

from .conftest import Built
from .synth import BASE


def test_empty_filters_have_no_predicate() -> None:
    assert GroupFilter().predicate() is None
    assert EntryFilter().predicate() is None
    assert EntryFilter(level="").predicate() is None  # a blank level is no filter


def test_filters_are_frozen_and_keyword_only() -> None:
    where = EntryFilter(user="u")
    with pytest.raises(AttributeError):
        where.user = "v"  # type: ignore[misc]
    with pytest.raises(TypeError):
        EntryFilter("sig")  # type: ignore[misc]


@pytest.mark.parametrize(
    "filters",
    [
        {"level": "ERROR"},
        {"level": [LogLevel.WARN, "error"]},
        {"min_level": "WARN", "user": "user1"},
        {"contains": "abc-1", "with_exception": True},
        {"since": BASE + timedelta(minutes=1), "until": BASE + timedelta(minutes=30)},
        {"logger": "com.atlassian", "thread": "http-1"},
        {"exception": "sql", "request_id": "1x2x3"},
    ],
)
def test_where_equals_keywords_for_entries(built: Built, filters: dict[str, Any]) -> None:
    ls = built.logset
    where = EntryFilter(**filters)
    assert ls.count_entries(where=where) == ls.count_entries(**filters)
    assert ls.entries(where=where, columns=["id"]).equals(ls.entries(**filters, columns=["id"]))
    assert ls.histogram("1m", where=where) == ls.histogram("1m", **filters)


@pytest.mark.parametrize(
    "filters",
    [
        {"level": "ERROR,WARN"},
        {"min_level": "WARN", "logger": "com"},
        {"contains": "abc", "with_exception": True, "exception": "e"},
        {"since": BASE + timedelta(minutes=5), "until": BASE + timedelta(hours=1)},
    ],
)
@pytest.mark.parametrize("coarse", [False, True])
def test_where_equals_keywords_for_groups(
    built: Built, filters: dict[str, Any], coarse: bool
) -> None:
    ls = built.logset
    where = GroupFilter(**filters)
    assert ls.count_groups(where=where, coarse=coarse) == ls.count_groups(**filters, coarse=coarse)
    left = ls.groups(where=where, coarse=coarse).select("signature", "count")
    assert left.equals(ls.groups(**filters, coarse=coarse).select("signature", "count"))


def test_where_and_keywords_together_is_an_error(built: Built) -> None:
    ls = built.logset
    with pytest.raises(TypeError, match="not both"):
        ls.entries(where=EntryFilter(user="u"), level="ERROR")
    with pytest.raises(TypeError, match="not both"):
        ls.count_groups(where=GroupFilter(), min_level="WARN")


def test_unknown_keyword_filter(built: Built) -> None:
    with pytest.raises(TypeError, match="unknown filter"):
        built.logset.groups(user="u")  # type: ignore[call-arg]


def test_invalid_level_in_filter() -> None:
    with pytest.raises(InvalidLevel):
        GroupFilter(level="SEVERE").predicate()
    with pytest.raises(ValueError, match="single level"):
        EntryFilter(min_level="WARN,ERROR").predicate()


def test_filter_selects_expected_rows(built: Built) -> None:
    ls = built.logset
    errors = ls.entries(where=EntryFilter(level="ERROR"), columns=["level"])
    assert errors.height > 0 and set(errors["level"].to_list()) == {int(LogLevel.ERROR)}
    warn_up = ls.entries(where=EntryFilter(min_level=LogLevel.WARN), columns=["level"])
    assert set(warn_up["level"].to_list()) <= {40, 50, 60}


def test_time_bounds_leave_untimed_entries_out() -> None:
    """An untimed parse error (stored at the epoch) is in no time range, as its group."""
    import polars as pl

    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    rows = pl.DataFrame({"ts": [epoch, BASE.astimezone(UTC)], "parse_error": ["no time", None]})
    cases = (
        (EntryFilter(until=BASE + timedelta(minutes=1)), [BASE]),
        (EntryFilter(since=datetime(1960, 1, 1, tzinfo=UTC)), [BASE]),
        (EntryFilter(), [epoch, BASE]),
    )
    for where, expected in cases:
        predicate = where.predicate()
        kept = rows if predicate is None else rows.filter(predicate)
        assert kept["ts"].to_list() == expected, where


def test_store_views_shim_reexports_service_views() -> None:
    from ragnalog.service import views
    from ragnalog.store import views as old

    assert old.frames_view is views.frames_view and old.ENTRY_BRIEF is views.ENTRY_BRIEF
    assert set(old.__all__) <= set(views.__all__)


def test_logger_prefix_matches_the_forms_jira_abbreviates() -> None:
    """Jira writes all but a logger's last three parts as initials."""
    import polars as pl

    names = [
        "c.a.jira.index.Builder",  # com.atlassian.jira.index.Builder
        "c.a.j.issue.index.Finder",  # com.atlassian.jira.issue.index.Finder
        "com.atlassian.jira.Plain",
        "c.a.jx.Other",  # another package
        "c.a.j.x",  # too short to be an abbreviation
        "c.o.other.a.B",
    ]
    df = pl.DataFrame({"logger": names})

    def matching(prefix: str) -> list[str]:
        cond = GroupFilter(logger=prefix).predicate()
        assert cond is not None
        return df.filter(cond)["logger"].to_list()

    assert matching("com.atlassian.jira") == names[:3]
    assert matching("c.a.jira") == names[:2]
    assert matching("c.a.jira.index.Builder") == names[:1]
    assert matching("org.acme") == []


def test_groups_in_a_window_need_an_entry_inside_it(tmp_path: Path) -> None:
    """A group seen before and after a window, but not in it, is not listed for it."""
    from ragnalog.store import Store
    from ragnalog.store.pipeline import ingest_paths

    log = tmp_path / "atlassian-jira.log"
    lines = [
        "2026-10-29 08:00:00,000+0000 exec-1 ERROR anon [org.acme.A] gap 1",
        "2026-10-29 09:30:00,000+0000 exec-1 ERROR anon [org.acme.B] inside 1",
        "2026-10-29 11:00:00,000+0000 exec-1 ERROR anon [org.acme.A] gap 2",
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ls = ingest_paths(Store(tmp_path / "store"), [log]).logset
    window = {
        "since": datetime(2026, 10, 29, 9, tzinfo=UTC),
        "until": datetime(2026, 10, 29, 10, tzinfo=UTC),
    }
    assert ls.groups(**window)["logger"].cast(str).to_list() == ["org.acme.B"]
    assert ls.count_groups(**window) == 1
    assert ls.count_groups(since=window["since"]) == 2  # one bound: both have an entry after it
