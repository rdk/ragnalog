"""Typed filters (``store/filters.py``) and the ``where=`` parameter of the read API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.store.filters import EntryFilter, GroupFilter
from atlas_log_analyzer.store.levels import InvalidLevel

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


def test_store_views_shim_reexports_service_views() -> None:
    from atlas_log_analyzer.service import views
    from atlas_log_analyzer.store import views as old

    assert old.frames_view is views.frames_view and old.ENTRY_BRIEF is views.ENTRY_BRIEF
    assert set(old.__all__) <= set(views.__all__)
