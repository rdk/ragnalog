"""Triage: "are there new kinds of errors since the baseline?" in one document (``triage.v1``).

Shared by ``atlas-log triage`` and ``GET /api/sets/{ref}/triage``; the web app
adds its ``report_url`` links on top of :func:`triage`'s result.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

import polars as pl

from atlas_log_analyzer.store.logset import LogSet, LogSetManifest, to_records
from atlas_log_analyzer.store.query import coverage_warning, diff, span_hours

__all__ = ["VERDICTS", "auto_baseline", "triage"]

VERDICTS = ("new_errors", "new_warnings", "new_exceptions", "clean", "no_baseline")
"""Values of ``triage.v1`` ``verdict``."""


def auto_baseline(manifests: Iterable[LogSetManifest], me: LogSetManifest) -> LogSetManifest | None:
    """Pick the default baseline of the set ``me`` among ``manifests``.

    That is the newest other set that ends before ``me`` starts (an earlier snapshot),
    else the newest other set that ends no later than ``me`` (an overlapping snapshot);
    ``None`` when there is none.
    """
    others = [m for m in manifests if m.id != me.id and m.last_ts is not None]
    if me.first_ts is None or not others:
        return None
    before = [m for m in others if m.last_ts is not None and m.last_ts <= me.first_ts]
    pool = before or [m for m in others if me.last_ts and m.last_ts and m.last_ts <= me.last_ts]
    if not pool:
        return None
    return max(pool, key=lambda m: (m.last_ts, str(m.created_at)))


def _set_info(m: LogSetManifest) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "first_ts": m.first_ts,
        "last_ts": m.last_ts,
        "entries": m.entry_count,
    }


def _new_rows(frame: pl.DataFrame, hours: float) -> list[dict[str, Any]]:
    """Rows of a ``new`` frame, most severe first, then by count, with ``rate_per_hour``."""
    rows = to_records(frame.sort(["level", "count_b", "signature"], descending=[True, True, False]))
    for row in rows:
        row["rate_per_hour"] = round(row["count_b"] / hours, 3) if hours else None
    return rows


def triage(
    ls: LogSet,
    baseline: LogSet | None,
    *,
    min_level: str | None = "WARN",
    grow: float = 2.0,
    limit: int | None = 50,
    resolved_by: Literal["auto", "param"] = "param",
) -> dict[str, Any]:
    """Compare ``ls`` with ``baseline`` and summarise what is new, grew or vanished.

    Args:
        ls: The set to judge.
        baseline: The earlier set to compare with; ``None`` gives verdict ``no_baseline``.
        min_level: Only groups of this level or more severe count for ``new``, ``grew``
            and ``vanished`` (``None`` = all levels).
        grow: ``grew`` lists changed groups whose hourly rate rose at least this factor.
        limit: Maximum rows per list (``new``, ``new_exceptions``, ``grew``, ``vanished``);
            ``None`` or ``0`` = all. ``summary`` always counts all rows.
        resolved_by: Recorded as ``baseline.resolved_by`` (``auto`` or ``param``).

    Returns:
        The ``triage.v1`` document (timestamps of ``set``/``baseline`` are datetimes,
        rows are JSON-ready). ``verdict``: ``new_errors`` (a new ERROR/FATAL group),
        ``new_warnings`` (other new groups), ``new_exceptions`` (no new group at
        ``min_level`` but a new group of a lower level carrying an exception),
        ``clean`` or ``no_baseline``. ``coverage`` compares the spans of both sets
        and ``warnings`` explains when they differ by more than 4x.

    Raises:
        ValueError: The sets were signed differently, or an unknown level.

    """
    me = ls.manifest
    hours_b = span_hours(ls)
    head: dict[str, Any] = {"schema": "triage.v1", "set": _set_info(me), "min_level": min_level}
    if baseline is None:
        return {
            **head,
            "baseline": None,
            "verdict": "no_baseline",
            "summary": None,
            "coverage": {"hours_baseline": None, "hours_set": round(hours_b, 3), "ratio": None},
            "warnings": [],
            "new": [],
            "new_exceptions": [],
            "grew": [],
            "vanished": [],
        }
    result = diff(baseline, ls, min_level=min_level)
    hours_a = span_hours(baseline)
    new = _new_rows(result.new, hours_b)
    new_exceptions: list[dict[str, Any]] = []
    if min_level is not None:
        known = set(result.new["signature"].to_list())
        everything = diff(baseline, ls).new
        extra = everything.filter(
            pl.col("exception_type").is_not_null() & ~pl.col("signature").is_in(list(known))
        )
        new_exceptions = _new_rows(extra, hours_b)
    changed = to_records(result.changed)
    grew = []
    for row in changed:
        rate_a = row["count_a"] / hours_a if hours_a else 0.0
        rate_b = row["count_b"] / hours_b if hours_b else 0.0
        row["rate_ratio"] = round(rate_b / rate_a, 3) if rate_a else None
        if row["rate_ratio"] is not None and row["rate_ratio"] >= grow:
            grew.append(row)
    grew.sort(key=lambda r: r["rate_ratio"], reverse=True)
    by_level: dict[str, int] = {}
    for row in new:
        by_level[row["level_name"]] = by_level.get(row["level_name"], 0) + 1
    errors = sum(n for k, n in by_level.items() if k in ("ERROR", "FATAL"))
    if errors:
        verdict = "new_errors"
    elif new:
        verdict = "new_warnings"
    elif new_exceptions:
        verdict = "new_exceptions"
    else:
        verdict = "clean"
    vanished = to_records(result.vanished)
    warning = coverage_warning(hours_a, hours_b, label_a="the baseline", label_b="the set")
    cut = limit or None
    bm = baseline.manifest
    return {
        **head,
        "baseline": {
            "id": bm.id,
            "name": bm.name,
            "first_ts": bm.first_ts,
            "last_ts": bm.last_ts,
            "resolved_by": resolved_by,
        },
        "verdict": verdict,
        "summary": {
            "new": len(new),
            "new_by_level": by_level,
            "new_exceptions": len(new_exceptions),
            "grew": len(grew),
            "vanished": len(vanished),
            "changed": len(changed),
            "unchanged": result.unchanged_count,
        },
        "coverage": {
            "hours_baseline": round(hours_a, 3),
            "hours_set": round(hours_b, 3),
            "ratio": round(hours_b / hours_a, 3) if hours_a else None,
        },
        "warnings": [warning] if warning else [],
        "new": new[:cut],
        "new_exceptions": new_exceptions[:cut],
        "grew": grew[:cut],
        "vanished": vanished[:cut],
    }
