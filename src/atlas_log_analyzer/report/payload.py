"""Build the ``report.v1`` payload: everything the HTML report page needs, as plain JSON.

The payload is computed with polars over the log set's Parquet files; only the
group rows, per-hour aggregates and the example entries are materialised in
Python. See ``docs/formats/report.v1.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from atlas_log_analyzer import __version__
from atlas_log_analyzer.store.logset import LogSet, to_records
from atlas_log_analyzer.store.query import diff

__all__ = ["LEVEL_BUCKETS", "REPORT_SCHEMA", "build_payload"]

REPORT_SCHEMA = "report.v1"
LEVEL_BUCKETS = ("ERROR", "WARN", "INFO", "DEBUG", "OTHER")
"""Timeline series: FATAL folds into ERROR, TRACE into DEBUG, UNKNOWN into OTHER."""

HOUR_MS = 3_600_000

ENTRY_COLUMNS = (
    "ts",
    "level_name",
    "thread",
    "logger",
    "user",
    "request_id",
    "ip",
    "url",
    "message",
    "variables",
    "exception_type",
)
"""entries.v1 columns copied into ``entries`` (besides the derived ones)."""


def _hour_expr() -> pl.Expr:
    # Integer hours since the epoch: no time-zone database needed (Windows without tzdata).
    return (pl.col("ts").dt.epoch("ms") // HOUR_MS).alias("hour")


def _bucket_expr() -> pl.Expr:
    lv = pl.col("level")
    return (
        pl.when(lv >= 50)
        .then(pl.lit("ERROR"))
        .when(lv >= 40)
        .then(pl.lit("WARN"))
        .when(lv >= 30)
        .then(pl.lit("INFO"))
        .when(lv >= 10)
        .then(pl.lit("DEBUG"))
        .otherwise(pl.lit("OTHER"))
        .alias("bucket")
    )


def _hour_iso(hour: int) -> str:
    return datetime.fromtimestamp(hour * 3600, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timeline(logset: LogSet) -> tuple[dict[str, Any], int | None]:
    """Contiguous hourly buckets with per-level counts, and the first hour (epoch hours)."""
    df = (
        logset.entries_lazy()
        .select(_hour_expr(), _bucket_expr())
        .group_by("hour", "bucket")
        .agg(pl.len().alias("n"))
        .collect()
    )
    if df.height == 0:
        return {"hours": [], "levels": {b: [] for b in LEVEL_BUCKETS}}, None
    hours_col = df.get_column("hour")
    first, last = int(hours_col.min()), int(hours_col.max())  # type: ignore[arg-type]
    size = last - first + 1
    levels = {b: [0] * size for b in LEVEL_BUCKETS}
    for hour, bucket, n in df.iter_rows():
        levels[bucket][int(hour) - first] += int(n)
    hours = [_hour_iso(h) for h in range(first, last + 1)]
    return {"hours": hours, "levels": levels}, first


def _hourly(logset: LogSet, signatures: list[str], first: int | None) -> dict[str, list[list[int]]]:
    """Sparse ``[[hour_index, count], ...]`` per signature (one group-by over entries)."""
    if first is None or not signatures:
        return {}
    df = (
        logset.entries_lazy()
        .filter(pl.col("signature").is_in(signatures))
        .group_by("signature", _hour_expr())
        .agg(pl.len().alias("n"))
        .sort("signature", "hour")
        .collect()
    )
    out: dict[str, list[list[int]]] = {}
    for sig, hour, n in df.iter_rows():
        out.setdefault(sig, []).append([int(hour) - first, int(n)])
    return out


def _entries(logset: LogSet, ids: list[str], max_raw: int) -> dict[str, dict[str, Any]]:
    """Fetch the example entries by id in one scan; ``raw`` cut to ``max_raw`` characters."""
    if not ids:
        return {}
    el = pl.element()
    chain = pl.col("exception_chain").list.eval(
        pl.struct(
            el.struct.field("type"),
            el.struct.field("message"),
            el.struct.field("frames").list.len().cast(pl.Int64).alias("frames"),
            el.struct.field("omitted_frames").cast(pl.Int64),
        )
    )
    raw_len = pl.col("raw").str.len_chars()
    if max_raw > 0:
        raw = pl.col("raw").str.slice(0, max_raw)
        truncated = (raw_len > max_raw).fill_null(False)
    else:
        raw, truncated = pl.col("raw"), pl.lit(False)
    strings = ("level_name", "logger")
    df = (
        logset.entries_lazy()
        .filter(pl.col("id").is_in(ids))
        .unique("id", keep="first", maintain_order=True)
        .select(
            "id",
            *(pl.col(c).cast(pl.String) if c in strings else pl.col(c) for c in ENTRY_COLUMNS),
            chain.alias("exception_chain"),
            raw.alias("raw"),
            truncated.alias("raw_truncated"),
            pl.col("source_path").cast(pl.String),
            pl.col("source_line").cast(pl.Int64),
        )
        .collect()
    )
    out: dict[str, dict[str, Any]] = {}
    for rec in to_records(df):
        entry_id = rec.pop("id")
        rec["variables"] = rec["variables"] or []
        rec["exception_chain"] = rec["exception_chain"] or []
        out[entry_id] = rec
    return out


def _merges(logset: LogSet, present: set[str]) -> list[dict[str, Any]]:
    """Drain3 merge proposals restricted to the signatures in the payload (>= 2 members)."""
    lf = logset.merges_lazy()
    if lf is None:
        return []
    out: list[dict[str, Any]] = []
    for rec in lf.sort("cluster_id").collect().iter_rows(named=True):
        sigs = [s for s in rec["signatures"] or [] if s in present]
        if len(sigs) >= 2:
            out.append(
                {
                    "cluster_id": int(rec["cluster_id"]),
                    "drain_template": rec["drain_template"],
                    "signatures": sigs,
                }
            )
    return out


def _compare(compare: LogSet, logset: LogSet, min_level: str | None) -> dict[str, Any]:
    result = diff(compare, logset, min_level=min_level)
    other = compare.manifest.to_dict()
    other.pop("sources", None)
    return {
        "other": other,
        "min_level": min_level,
        "new": to_records(result.new),
        "vanished": to_records(result.vanished),
        "changed": to_records(result.changed),
        "unchanged_count": int(result.unchanged_count),
    }


def build_payload(
    logset: LogSet,
    *,
    compare: LogSet | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int | None = None,
) -> dict[str, Any]:
    """Build the ``report.v1`` payload for ``logset``.

    Args:
        logset: The log set to report on.
        compare: Baseline log set; adds a ``compare`` block from ``diff(compare, logset)``.
        min_level: Only groups of this level or more severe (also applied to the comparison).
            The overview (``stats``, ``timeline``) always covers the whole set.
        examples: Example entries per group (taken from ``groups.examples``); 0 = none.
        max_raw: Truncate each example's ``raw`` to this many characters; 0 = no limit.
        max_groups: Keep only the largest groups (by count); ``None`` = all.

    Returns:
        A JSON-serialisable dict (see ``docs/formats/report.v1.md``).

    Raises:
        ValueError: Invalid option, unknown level, or the sets are not comparable
            (different signature/mask-rule versions or specs).

    """
    if examples < 0:
        raise ValueError("examples must be >= 0")
    if max_raw < 0:
        raise ValueError("max_raw must be >= 0")
    if max_groups is not None and max_groups < 0:
        raise ValueError("max_groups must be >= 0")
    compare_block = _compare(compare, logset, min_level) if compare is not None else None

    gdf = logset.groups(min_level=min_level, sort="count", limit=max_groups)
    gdf = gdf.with_columns(
        pl.col("level_name").cast(pl.String),
        pl.col("logger").cast(pl.String),
        pl.col("examples").list.head(examples).fill_null([]),
    )
    groups = to_records(gdf)
    signatures = [g["signature"] for g in groups]

    timeline, first_hour = _timeline(logset)
    hourly = _hourly(logset, signatures, first_hour)
    ids = [i for g in groups for i in g["examples"]]
    entries = _entries(logset, ids, max_raw)
    for g in groups:
        g["examples"] = [i for i in g["examples"] if i in entries]
        g["hourly"] = hourly.get(g["signature"], [])

    stats = logset.stats()
    stats.pop("sources", None)
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": __version__,
        "options": {
            "min_level": min_level,
            "examples": examples,
            "max_raw": max_raw,
            "max_groups": max_groups,
        },
        "logset": logset.manifest.to_dict(),
        "stats": stats,
        "timeline": timeline,
        "groups": groups,
        "entries": entries,
        "merges": _merges(logset, set(signatures)),
        "compare": compare_block,
    }
