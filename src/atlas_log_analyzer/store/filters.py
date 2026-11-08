"""Typed filters of the :class:`~atlas_log_analyzer.store.logset.LogSet` read API.

:class:`GroupFilter` and :class:`EntryFilter` hold *resolved* values (datetimes,
level names or :class:`~atlas_log_analyzer.core.models.LogLevel` values, exact
strings); parsing user input (relative times, blank values) is the job of
:mod:`atlas_log_analyzer.service.params`. ``predicate()`` turns a filter into one
polars expression, or ``None`` when it filters nothing::

    where = EntryFilter(min_level="WARN", with_exception=True)
    ls.entries(where=where, limit=20)
    ls.count_entries(where=where)

polars is imported inside :meth:`GroupFilter.predicate` /
:meth:`EntryFilter.predicate`, so building a filter loads no data library.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.store.levels import level_values

if TYPE_CHECKING:
    import polars as pl

__all__ = [
    "EntryFilter",
    "EntryFilterArgs",
    "GroupFilter",
    "GroupFilterArgs",
    "LevelSpec",
    "entry_filter",
    "group_filter",
    "has_time",
]

type LevelSpec = LogLevel | str | Iterable[LogLevel | str]
"""A level filter: a :class:`LogLevel`, a name or comma-separated names, or an iterable.

Names as in :mod:`atlas_log_analyzer.store.levels` (``UNKNOWN`` included, ``WARNING``
= ``WARN``); a blank string means no filter."""


def has_time() -> pl.Expr:
    """Select the entries that have a timestamp (a boolean expression).

    A parse error without one (a line before a file's first header, an unreadable time)
    is stored at the epoch; it counts as an entry but stays out of time ranges,
    histograms and first/last seen.
    """
    import polars as pl

    return ~(pl.col("parse_error").is_not_null() & (pl.col("ts").dt.epoch("ms") == 0))


def _utc(ts: datetime) -> datetime:
    """Naive datetimes are taken as UTC."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def level_list(spec: LevelSpec) -> list[int]:
    """Normalise a :data:`LevelSpec` to level values; ``[]`` for a blank string.

    Names are parsed by :func:`~atlas_log_analyzer.store.levels.parse_levels`, the
    parser of the CLI and the web API.

    Raises:
        InvalidLevel: An unknown level name.
        ValueError: An empty iterable.

    """
    if isinstance(spec, LogLevel):
        return [int(spec)]
    if isinstance(spec, str):
        return level_values(spec)
    out: list[int] = []
    for item in spec:
        out.extend([int(item)] if isinstance(item, LogLevel) else level_values(item))
    if not out:
        raise ValueError("empty level filter")
    return out


def _level_filters(level: LevelSpec | None, min_level: LevelSpec | None) -> list[pl.Expr]:
    import polars as pl

    out: list[pl.Expr] = []
    if level is not None and (values := level_list(level)):
        out.append(pl.col("level").is_in(values))
    if min_level is not None and (lv := level_list(min_level)):
        if len(lv) != 1:
            raise ValueError("min_level takes a single level")
        out.append((pl.col("level") >= lv[0]) & (pl.col("level") != int(LogLevel.UNKNOWN)))
    return out


JIRA_FULL_PARTS = 3
"""Trailing parts Jira writes a logger with in full; the ones before are cut to an initial."""


def logger_forms(logger: str) -> list[tuple[str, int]]:
    """Return ``logger`` and the forms with its leading packages cut to their initial.

    Jira writes a logger with all but its last three parts abbreviated, so one package
    appears as ``c.a.jira.index.Builder`` and as ``c.a.j.issue.index.Finder``:
    ``com.atlassian.jira`` gives ``c.atlassian.jira``, ``c.a.jira`` and ``c.a.j``. Each
    form comes with the number of parts it cut; a logger written that way has at least
    :data:`JIRA_FULL_PARTS` more.
    """
    parts = logger.split(".")
    forms = [(logger, 0)]
    for cut in range(1, len(parts) + 1):
        if not parts[cut - 1]:
            break
        form = ".".join([p[0] for p in parts[:cut]] + parts[cut:])
        if form != logger:
            forms.append((form, cut))
    return forms


def _logger_filter(logger: str) -> pl.Expr:
    """Exact logger name, or any logger in that package (``com.foo`` matches ``com.foo.Bar``).

    Also in the abbreviated forms Jira writes (:func:`logger_forms`).
    """
    import polars as pl

    col = pl.col("logger").cast(pl.String)
    dots = col.str.count_matches(".", literal=True)
    cond = (col == logger) | col.str.starts_with(logger + ".")
    for form, cut in logger_forms(logger)[1:]:
        cond = cond | (col.str.starts_with(form + ".") & (dots >= cut + JIRA_FULL_PARTS - 1))
    return cond


def _icontains(column: str, text: str) -> pl.Expr:
    """Case-insensitive literal substring test on a (possibly dictionary-encoded) column."""
    import polars as pl

    return pl.col(column).cast(pl.String).str.contains(f"(?i){re.escape(text)}")


def _exception_filters(with_exception: bool, exception: str | None) -> list[pl.Expr]:
    import polars as pl

    out: list[pl.Expr] = []
    if with_exception:
        out.append(pl.col("exception_type").is_not_null())
    if exception is not None:
        out.append(_icontains("exception_type", exception))
    return out


def _and(exprs: Iterable[pl.Expr]) -> pl.Expr | None:
    result: pl.Expr | None = None
    for e in exprs:
        result = e if result is None else result & e
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupFilter:
    """Filters of :meth:`LogSet.groups` / :meth:`LogSet.count_groups` (resolved values).

    Attributes:
        level: Exact level(s): a :data:`LevelSpec` (``"WARN,ERROR"``, a ``LogLevel``, ...).
        min_level: Lowest level to include (one level; ``UNKNOWN`` rows are excluded).
        logger: Logger name; also matches loggers below it (``com.foo`` → ``com.foo.Bar``),
            also as Jira abbreviates them (``com.foo`` → ``c.foo.Bar``, ``c.f.x.y.Bar``).
        since: Keep groups seen at or after this time (``last_seen >= since``; naive = UTC).
        until: Keep groups first seen before this time (``first_seen < until``).
        with_exception: Keep groups whose ``exception_type`` is not null (any level).
        exception: Case-insensitive substring of ``exception_type``.
        contains: Case-insensitive substring of ``template``.

    """

    level: LevelSpec | None = None
    min_level: LevelSpec | None = None
    logger: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    with_exception: bool = False
    exception: str | None = None
    contains: str | None = None

    def predicate(self) -> pl.Expr | None:
        """Conjunction of the set filters as a polars expression; ``None`` when there is none.

        Raises:
            InvalidLevel: An unknown level name.
            ValueError: ``min_level`` names several levels.

        """
        import polars as pl

        filters = _level_filters(self.level, self.min_level)
        filters += _exception_filters(self.with_exception, self.exception)
        if self.contains is not None:
            filters.append(_icontains("template", self.contains))
        if self.logger is not None:
            filters.append(_logger_filter(self.logger))
        if self.since is not None:
            filters.append(pl.col("last_seen") >= _utc(self.since))
        if self.until is not None:
            filters.append(pl.col("first_seen") < _utc(self.until))
        return _and(filters)


@dataclass(frozen=True, slots=True, kw_only=True)
class EntryFilter:
    """Filters of :meth:`LogSet.entries` / :meth:`LogSet.count_entries` (resolved values).

    Attributes:
        signature: Primary signature (exact).
        coarse_signature: Coarse signature (exact).
        level: Exact level(s), as :attr:`GroupFilter.level`.
        min_level: Lowest level to include.
        logger: Logger name or package prefix (see :attr:`GroupFilter.logger`).
        user: User (exact).
        thread: Thread name (exact).
        request_id: Request id from the request context (exact).
        since: Inclusive lower bound on ``ts`` (naive = UTC).
        until: Exclusive upper bound on ``ts`` (naive = UTC). With either bound, entries
            without a time (:func:`has_time`) are left out, as their groups are.
        contains: Case-insensitive literal substring of ``raw``.
        with_exception: Keep entries whose ``exception_type`` is not null (any level).
        exception: Case-insensitive substring of ``exception_type``.

    """

    signature: str | None = None
    coarse_signature: str | None = None
    level: LevelSpec | None = None
    min_level: LevelSpec | None = None
    logger: str | None = None
    user: str | None = None
    thread: str | None = None
    request_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    contains: str | None = None
    with_exception: bool = False
    exception: str | None = None

    def predicate(self) -> pl.Expr | None:
        """Conjunction of the set filters as a polars expression; ``None`` when there is none.

        Raises:
            InvalidLevel: An unknown level name.
            ValueError: ``min_level`` names several levels.

        """
        import polars as pl

        filters = _level_filters(self.level, self.min_level)
        filters += _exception_filters(self.with_exception, self.exception)
        exact = (
            ("request_id", self.request_id),
            ("signature", self.signature),
            ("coarse_signature", self.coarse_signature),
        )
        filters += [pl.col(column) == value for column, value in exact if value is not None]
        if self.logger is not None:
            filters.append(_logger_filter(self.logger))
        if self.user is not None:
            filters.append(pl.col("user") == self.user)
        if self.thread is not None:
            filters.append(pl.col("thread") == self.thread)
        if self.since is not None:
            filters.append(pl.col("ts") >= _utc(self.since))
        if self.until is not None:
            filters.append(pl.col("ts") < _utc(self.until))
        if self.since is not None or self.until is not None:
            filters.append(has_time())  # an untimed entry is in no time range (as its group)
        if self.contains is not None:
            filters.append(pl.col("raw").str.contains(f"(?i){re.escape(self.contains)}"))
        return _and(filters)


class GroupFilterArgs(TypedDict, total=False):
    """The fields of :class:`GroupFilter` as keyword arguments (``LogSet.groups(level=...)``)."""

    level: LevelSpec | None
    min_level: LevelSpec | None
    logger: str | None
    since: datetime | None
    until: datetime | None
    with_exception: bool
    exception: str | None
    contains: str | None


class EntryFilterArgs(TypedDict, total=False):
    """The fields of :class:`EntryFilter` as keyword arguments (``LogSet.entries(user=...)``)."""

    signature: str | None
    coarse_signature: str | None
    level: LevelSpec | None
    min_level: LevelSpec | None
    logger: str | None
    user: str | None
    thread: str | None
    request_id: str | None
    since: datetime | None
    until: datetime | None
    contains: str | None
    with_exception: bool
    exception: str | None


def _merge[F: (GroupFilter, EntryFilter)](cls: type[F], where: F | None, args: dict[str, Any]) -> F:
    if not args:
        return where if where is not None else cls()
    if where is not None:
        names = ", ".join(sorted(args))
        raise TypeError(f"pass filters either as where= or as keywords, not both ({names})")
    unknown = set(args) - {f.name for f in fields(cls)}
    if unknown:
        raise TypeError(f"unknown filter(s): {', '.join(sorted(unknown))}")
    return cls(**args)


def group_filter(where: GroupFilter | None, args: GroupFilterArgs) -> GroupFilter:
    """``where`` or the filter built from keyword ``args`` (``TypeError`` when both are given)."""
    return _merge(GroupFilter, where, dict(args))


def entry_filter(where: EntryFilter | None, args: EntryFilterArgs) -> EntryFilter:
    """``where`` or the filter built from keyword ``args`` (``TypeError`` when both are given)."""
    return _merge(EntryFilter, where, dict(args))
