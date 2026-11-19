"""Cross-log-set comparison."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ragnalog.store.logset import LogSet
from ragnalog.store.manifest import LogSetManifest

__all__ = [
    "COVERAGE_FACTOR",
    "DiffResult",
    "IncompatibleSets",
    "coverage_warning",
    "diff",
    "manifest_span_hours",
    "span_hours",
    "with_rates",
]

_ATTRS = ("template", "level", "level_name", "logger", "exception_type")

COVERAGE_FACTOR = 4.0
"""Spans of two compared sets that differ by more than this factor get a coverage warning."""


def manifest_span_hours(m: LogSetManifest) -> float:
    """Hours between the first and the last entry of a set (at least one minute; 0 if empty)."""
    if m.first_ts is None or m.last_ts is None:
        return 0.0
    return max((m.last_ts - m.first_ts).total_seconds() / 3600, 1 / 60)


def span_hours(ls: LogSet) -> float:
    """Hours between the first and the last entry of a set (at least one minute; 0 if empty)."""
    return manifest_span_hours(ls.manifest)


def _span_text(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def coverage_warning(
    hours_a: float, hours_b: float, *, label_a: str = "set A", label_b: str = "set B"
) -> str | None:
    """Return a sentence when the spans of two sets differ by more than :data:`COVERAGE_FACTOR`.

    Groups that occur only at some times of day (daytime traffic, nightly jobs)
    then appear as new or vanished although nothing changed. ``None`` when the
    spans are comparable or one of them is unknown (``0``).
    """
    if hours_a <= 0 or hours_b <= 0:
        return None
    ratio = hours_b / hours_a
    if 1 / COVERAGE_FACTOR <= ratio <= COVERAGE_FACTOR:
        return None
    factor, longer = (ratio, "longer") if ratio > 1 else (1 / ratio, "shorter")
    text = (
        f"{label_b} covers {_span_text(hours_b)} but {label_a} covers {_span_text(hours_a)} "
        f"({factor:.1f}x {longer}): groups that occur only at certain times (daytime "
        "traffic, nightly jobs) look new or vanished, so new/vanished groups are "
        "unreliable; compare sets of similar span"
    )
    return text[:1].upper() + text[1:]


def with_rates(frame: pl.DataFrame, hours_a: float, hours_b: float) -> pl.DataFrame:
    """Add hourly rates to diff rows and sort by their ratio.

    Adds ``rate_a = count_a / hours_a``, ``rate_b = count_b / hours_b`` (counts per
    hour of each set's span, see :func:`span_hours`) and ``rate_ratio = rate_b /
    rate_a`` (all rounded to 3 decimals; ``None`` when a span or ``rate_a`` is 0),
    sorted by ``rate_ratio`` descending, then ``signature``.
    """
    none = pl.lit(None, dtype=pl.Float64)
    rate_a = pl.col("count_a") / hours_a if hours_a > 0 else none
    rate_b = pl.col("count_b") / hours_b if hours_b > 0 else none
    ratio = pl.when(rate_a > 0).then(rate_b / rate_a).otherwise(none)
    return frame.with_columns(
        rate_a.round(3).alias("rate_a"),
        rate_b.round(3).alias("rate_b"),
        ratio.round(3).alias("rate_ratio"),
    ).sort(["rate_ratio", "signature"], descending=[True, False], nulls_last=True)


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Signatures that are new in ``b``, gone from ``a``, or present in both with changed counts.

    Each frame has at least ``signature, template, level, logger, exception_type,
    count_a, count_b``.
    """

    a: str
    b: str
    new: pl.DataFrame
    vanished: pl.DataFrame
    changed: pl.DataFrame
    """Present in both; ``count_b != count_a``; includes ``ratio`` column."""
    unchanged_count: int


class IncompatibleSets(ValueError):
    """Two sets were signed with different recipes, so their signatures do not match."""


def _check_compatible(a: LogSet, b: LogSet) -> None:
    """Raise :class:`IncompatibleSets` naming the recipe parts in which the sets differ."""
    from ragnalog.core.pipeline import recipe_differences

    ma, mb = a.manifest, b.manifest
    problems = recipe_differences(ma.recipe_key, mb.recipe_key)
    if problems:
        raise IncompatibleSets(
            "log sets are not comparable (recipe "
            + "; ".join(problems)
            + "); rebuild the older set with `ragnalog upgrade SET` (same spec) or "
            "`ragnalog regroup SET` (another spec), or use force to override"
        )


def _side(ls: LogSet, suffix: str, *, min_level: str | None, coarse: bool) -> pl.DataFrame:
    df = ls.groups(min_level=min_level, coarse=coarse)
    return df.select(
        "signature",
        *(
            pl.col(c).cast(pl.String) if c in ("level_name", "logger") else pl.col(c)
            for c in _ATTRS
        ),
        pl.col("count").alias(f"count_{suffix}"),
        pl.col("first_seen").alias(f"first_seen_{suffix}"),
        pl.col("last_seen").alias(f"last_seen_{suffix}"),
    )


def diff(
    a: LogSet,
    b: LogSet,
    *,
    min_level: str | None = None,
    coarse: bool = False,
    force: bool = False,
) -> DiffResult:
    """Compare group tables of two log sets.

    Raises :class:`IncompatibleSets` (a ``ValueError``) when the recipes differ,
    unless ``force`` is true.

    Args:
        a: Baseline log set.
        b: Log set compared against the baseline.
        min_level: Only consider groups at or above this level (e.g. ``"WARN"``).
        coarse: Compare by ``coarse_signature`` (the ``signature`` column then holds it).
        force: Compare even when the sets were signed differently.

    Returns:
        A :class:`DiffResult`. All frames have the columns ``signature, template, level,
        level_name, logger, exception_type, count_a, count_b, first_seen_a, last_seen_a,
        first_seen_b, last_seen_b`` (counts are ``0`` on the missing side). ``new`` is sorted
        by ``count_b`` desc, ``vanished`` by ``count_a`` desc, and ``changed`` adds
        ``ratio = count_b / count_a`` and is sorted by ``|log(ratio)|`` desc.

    """
    if not force:
        _check_compatible(a, b)
    da = _side(a, "a", min_level=min_level, coarse=coarse)
    db = _side(b, "b", min_level=min_level, coarse=coarse)
    joined = da.join(db, on="signature", how="full", coalesce=True, suffix="_bside")
    joined = joined.with_columns(
        *(pl.coalesce(pl.col(c), pl.col(f"{c}_bside")).alias(c) for c in _ATTRS),
        pl.col("count_a").fill_null(0),
        pl.col("count_b").fill_null(0),
    ).select(
        "signature",
        *_ATTRS,
        "count_a",
        "count_b",
        "first_seen_a",
        "last_seen_a",
        "first_seen_b",
        "last_seen_b",
    )
    new = joined.filter(pl.col("count_a") == 0).sort(
        ["count_b", "signature"], descending=[True, False]
    )
    vanished = joined.filter(pl.col("count_b") == 0).sort(
        ["count_a", "signature"], descending=[True, False]
    )
    both = joined.filter((pl.col("count_a") > 0) & (pl.col("count_b") > 0))
    changed = (
        both.filter(pl.col("count_a") != pl.col("count_b"))
        .with_columns((pl.col("count_b") / pl.col("count_a")).alias("ratio"))
        .with_columns(pl.col("ratio").log().abs().alias("_mag"))
        .sort(["_mag", "signature"], descending=[True, False])
        .drop("_mag")
    )
    return DiffResult(
        a=a.manifest.id,
        b=b.manifest.id,
        new=new,
        vanished=vanished,
        changed=changed,
        unchanged_count=both.height - changed.height,
    )
