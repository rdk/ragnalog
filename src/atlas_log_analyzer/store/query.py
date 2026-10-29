"""Cross-log-set comparison."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from atlas_log_analyzer.store.logset import LogSet

__all__ = ["DiffResult", "diff"]


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
    """
    raise NotImplementedError
