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

__all__ = ["LEVEL_BUCKETS", "REPORT_SCHEMA", "build_payload", "example_entries"]

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
)
"""entries.v1 columns copied into ``entries`` (besides the derived ones)."""

OPTIONAL_ENTRY_KEYS = ("user", "ip", "url", "request_id", "variables", "raw_truncated")
"""Entry keys omitted from the payload when null, empty or false."""


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


def _timeline_and_hourly(
    logset: LogSet, signatures: list[str], filtered: bool
) -> tuple[dict[str, Any], dict[str, list[list[int]]]]:
    """Per-level hourly timeline and sparse per-signature hourly counts, in one collect.

    Returns ``({"hours", "levels"}, {signature: [[hour_index, count], ...]})``; hour
    indices count from the first hour of the set. ``filtered`` restricts the per-signature
    scan to ``signatures`` (skipped when every group of the set is present).
    """
    lf = logset.entries_lazy()
    queries = [
        lf.select(_hour_expr(), _bucket_expr()).group_by("hour", "bucket").agg(pl.len().alias("n"))
    ]
    if signatures:
        hourly_lf = lf.select("signature", _hour_expr())
        if filtered:
            hourly_lf = hourly_lf.filter(pl.col("signature").is_in(signatures))
        queries.append(
            hourly_lf.group_by("signature", "hour")
            .agg(pl.len().alias("n"))
            .sort("signature", "hour")
        )
    frames = pl.collect_all(queries)
    df = frames[0]
    if df.height == 0:
        return {"hours": [], "levels": {b: [] for b in LEVEL_BUCKETS}}, {}
    hours_col = df.get_column("hour")
    first, last = int(hours_col.min()), int(hours_col.max())  # type: ignore[arg-type]
    size = last - first + 1
    levels = {b: [0] * size for b in LEVEL_BUCKETS}
    for hour, bucket, n in df.iter_rows():
        levels[bucket][int(hour) - first] += int(n)
    hours = [_hour_iso(h) for h in range(first, last + 1)]
    hourly: dict[str, list[list[int]]] = {}
    if len(frames) > 1:
        for sig, hour, n in frames[1].iter_rows():
            hourly.setdefault(sig, []).append([int(hour) - first, int(n)])
    return {"hours": hours, "levels": levels}, hourly


def _entries(
    logset: LogSet, ids: list[str], max_raw: int, source_index: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """Fetch the example entries by id in one scan; ``raw`` cut to ``max_raw`` characters.

    ``source`` is the index of the entry's file in ``logset.sources`` (-1 if unknown);
    the keys in :data:`OPTIONAL_ENTRY_KEYS` are dropped when null, empty or false.
    """
    if not ids:
        return {}
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
        rec["source"] = source_index.get(rec.pop("source_path") or "", -1)
        for key in OPTIONAL_ENTRY_KEYS:
            if not rec.get(key):
                rec.pop(key, None)
        out[entry_id] = rec
    return out


def example_entries(
    logset: LogSet, ids: list[str], *, max_raw: int = 4000
) -> dict[str, dict[str, Any]]:
    """Entries by id in the ``report.v1`` ``entries`` shape (for lazy loading by the web app).

    Args:
        logset: The log set holding the entries.
        ids: Entry ids; unknown ids are skipped.
        max_raw: Truncate ``raw`` to this many characters; 0 = no limit.

    """
    source_index = {src.path: i for i, src in enumerate(logset.manifest.sources)}
    return _entries(logset, ids, max_raw, source_index)


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


def _compare(
    compare: LogSet, logset: LogSet, min_level: str | None, present: set[str]
) -> dict[str, Any]:
    """Compact comparison block: ``new``/``changed`` only for ``present`` signatures."""
    result = diff(compare, logset, min_level=min_level)
    other = compare.manifest.to_dict()
    other.pop("sources", None)
    new = [
        [sig, int(b)]
        for sig, b in result.new.select("signature", "count_b").iter_rows()
        if sig in present
    ]
    changed = [
        [sig, int(a), int(b)]
        for sig, a, b in result.changed.select("signature", "count_a", "count_b").iter_rows()
        if sig in present
    ]
    return {
        "other": other,
        "min_level": min_level,
        "counts": {
            "new": result.new.height,
            "changed": result.changed.height,
            "vanished": result.vanished.height,
            "unchanged": int(result.unchanged_count),
        },
        "new": new,
        "changed": changed,
        "vanished": to_records(result.vanished),
    }


def _pairs(items: list[dict[str, Any]] | None, index: dict[str, int] | None = None) -> list[Any]:
    """``[{value, count}, ...]`` as ``[[value, count], ...]`` (value mapped through ``index``)."""
    if index is None:
        return [[it["value"], it["count"]] for it in items or ()]
    return [[index.get(it["value"], -1), it["count"]] for it in items or ()]


def build_payload(
    logset: LogSet,
    *,
    compare: LogSet | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int | None = None,
    entries_url: str | None = None,
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
        entries_url: URL template for fetching more entries of a group (the page replaces
            ``{sig}`` with the signature); added as ``entries_url`` only when given.

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
    source_index = {src.path: i for i, src in enumerate(logset.manifest.sources)}
    gdf = logset.groups(min_level=min_level, sort="count")
    truncated_groups = 0
    if max_groups is not None and gdf.height > max_groups:
        truncated_groups = gdf.height - max_groups
        gdf = gdf.head(max_groups)
    gdf = gdf.with_columns(
        pl.col("level_name").cast(pl.String),
        pl.col("logger").cast(pl.String),
        pl.col("examples").list.head(examples).fill_null([]),
    )
    groups = to_records(gdf)
    signatures = [g["signature"] for g in groups]
    present = set(signatures)
    filtered = len(signatures) < logset.manifest.group_count

    timeline, hourly = _timeline_and_hourly(logset, signatures, filtered)
    ids = [i for g in groups for i in g["examples"]]
    entries = _entries(logset, ids, max_raw, source_index)
    for g in groups:
        if g.get("coarse_signature") == g["signature"]:
            del g["coarse_signature"]
        g["top_users"] = _pairs(g["top_users"])
        g["top_threads"] = _pairs(g["top_threads"])
        g["sources"] = _pairs(g["sources"], source_index)
        g["examples"] = [i for i in g["examples"] if i in entries]
        g["hourly"] = hourly.get(g["signature"], [])
    compare_block = _compare(compare, logset, min_level, present) if compare is not None else None

    stats = logset.stats()
    stats.pop("sources", None)
    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": __version__,
        "options": {
            "min_level": min_level,
            "examples": examples,
            "max_raw": max_raw,
            "max_groups": max_groups,
            "truncated_groups": truncated_groups,
        },
        "logset": logset.manifest.to_dict(),
        "stats": stats,
        "timeline": timeline,
        "groups": groups,
        "entries": entries,
        "merges": _merges(logset, set(signatures)),
        "compare": compare_block,
    }
    if entries_url is not None:
        payload["entries_url"] = entries_url
    return payload
