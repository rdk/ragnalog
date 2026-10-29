"""Cross-log-set comparison."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from atlas_log_analyzer.store.logset import LogSet

__all__ = ["DiffResult", "diff"]

_ATTRS = ("template", "level", "level_name", "logger", "exception_type")


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


def _check_compatible(a: LogSet, b: LogSet) -> None:
    ma, mb = a.manifest, b.manifest
    problems = [
        f"{field}: {getattr(ma, field)!r} != {getattr(mb, field)!r}"
        for field in ("signature_version", "mask_rules_version", "signature_spec")
        if getattr(ma, field) != getattr(mb, field)
    ]
    if problems:
        raise ValueError(
            "log sets are not comparable (" + "; ".join(problems) + "); use force to override"
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

    Raises ``ValueError`` when signature/mask-rule versions or specs differ,
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
