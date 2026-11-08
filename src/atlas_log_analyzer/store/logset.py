"""Reading a stored log set; its manifest is :mod:`atlas_log_analyzer.store.manifest`.

Directory layout::

    <store>/<logset-id>/
        manifest.json      logset.v1
        entries.parquet    entries.v1
        groups.parquet     groups.v1
        merges.parquet     merges.v1 (optional)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Unpack

import polars as pl

from atlas_log_analyzer.store.filters import (
    EntryFilter,
    EntryFilterArgs,
    GroupFilter,
    GroupFilterArgs,
    LevelSpec,
    entry_filter,
    group_filter,
    has_time,
)
from atlas_log_analyzer.store.manifest import (
    ENTRIES_FILE,
    GROUPS_FILE,
    MERGES_FILE,
    LogSetManifest,
    SourceInfo,
    _iso,
)
from atlas_log_analyzer.store.manifest import MANIFEST_FILE as MANIFEST_FILE
from atlas_log_analyzer.store.manifest import MANIFEST_SCHEMA as MANIFEST_SCHEMA

__all__ = ["LevelSpec", "LogSet", "LogSetManifest", "SourceInfo", "rows", "to_records"]

GROUP_SORTS = ("count", "last_seen", "first_seen", "signature", "template")
"""Values accepted by ``LogSet.groups(sort=...)``."""
TOP_LOGGERS = 20

_BUCKET_UNITS_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_BUCKET_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
MAX_HISTOGRAM_BUCKETS = 100_000
"""Upper bound on the number of (zero-filled) buckets :meth:`LogSet.histogram` returns."""
MAX_BUCKET_MS = 366 * 86_400_000
"""Longest histogram bucket (366 days)."""


def _bucket_ms(bucket: str) -> int:
    """``"15m"``, ``"1h"``, ``"1d"`` … as milliseconds; ``ValueError`` when malformed.

    A bucket is at most :data:`MAX_BUCKET_MS` (a year) long.
    """
    m = _BUCKET_RE.match(bucket)
    if m is None or int(m.group(1)) <= 0:
        raise ValueError(f"invalid bucket {bucket!r}; expected e.g. '5m', '1h', '1d'")
    size = int(m.group(1)) * _BUCKET_UNITS_MS[m.group(2)]
    if size > MAX_BUCKET_MS:
        raise ValueError(f"bucket {bucket!r} is longer than a year; use at most '366d'")
    return size


def _bucket_iso(start_ms: int) -> str:
    # not fromtimestamp: it raises OSError on Windows before 1970 (a log stamped year 1)
    start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=start_ms)
    return start.isoformat(timespec="seconds").removesuffix("+00:00") + "Z"  # year 1: 0001


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _ident(name: str) -> str:
    """Quote a column name for DuckDB SQL."""
    return '"' + name.replace('"', '""') + '"'


def _tz_columns(df: pl.DataFrame) -> list[str]:
    return [
        name
        for name, dtype in df.schema.items()
        if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None
    ]


def _kv_columns(df: pl.DataFrame) -> list[str]:
    """Map columns, which polars reads back as ``list[struct{key, value}]``."""
    out = []
    for name, dtype in df.schema.items():
        inner = dtype.inner if isinstance(dtype, pl.List) else None
        if isinstance(inner, pl.Struct) and [f.name for f in inner.fields] == ["key", "value"]:
            out.append(name)
    return out


def _kv_to_dicts(out: list[dict[str, Any]], kv_cols: list[str]) -> list[dict[str, Any]]:
    for row in out:
        for c in kv_cols:
            v = row[c]
            if isinstance(v, list):
                row[c] = {kv["key"]: kv["value"] for kv in v}
    return out


def rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    """Rows of ``df`` as dicts, with aware UTC datetimes and maps as dicts.

    Unlike ``df.to_dicts()`` this does not need the IANA time-zone database
    (missing on Windows unless the ``tzdata`` package is installed).
    """
    tz_cols = _tz_columns(df)
    if tz_cols:
        df = df.with_columns(
            pl.col(c).dt.convert_time_zone("UTC").dt.replace_time_zone(None) for c in tz_cols
        )
    out = df.to_dicts()
    for row in out:
        for c in tz_cols:
            v = row[c]
            if v is not None:
                row[c] = v.replace(tzinfo=UTC)
    return _kv_to_dicts(out, _kv_columns(df))


def to_records(df: pl.DataFrame) -> list[dict[str, Any]]:
    """JSON-serialisable rows: datetimes become ISO 8601 strings (UTC, ms), maps dicts.

    Map columns (``context``) become dicts here and in :func:`rows`.
    """
    exprs = []
    for name, dtype in df.schema.items():
        if isinstance(dtype, pl.Datetime):
            col = pl.col(name)
            if dtype.time_zone is not None:
                col = col.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            exprs.append(col.dt.strftime("%Y-%m-%dT%H:%M:%S%.3f+00:00").alias(name))
        elif isinstance(dtype, pl.Date):
            exprs.append(pl.col(name).dt.strftime("%Y-%m-%d").alias(name))
    if exprs:
        df = df.with_columns(exprs)
    return _kv_to_dicts(df.to_dicts(), _kv_columns(df))


class LogSet:
    """A stored log set: manifest plus lazy access to its Parquet files."""

    def __init__(self, path: Path, manifest: LogSetManifest) -> None:
        self.path = path
        self.manifest = manifest

    def __repr__(self) -> str:
        return f"LogSet(id={self.manifest.id!r}, name={self.manifest.name!r}, path={self.path!r})"

    @property
    def id(self) -> str:
        """Log-set id (same as ``manifest.id``)."""
        return self.manifest.id

    @classmethod
    def load(cls, path: Path) -> LogSet:
        """Open the log set stored in ``path``.

        Raises:
            FileNotFoundError: No ``manifest.json`` in ``path``.
            ValueError: The manifest is corrupt or uses a newer schema major.

        """
        return cls(path, LogSetManifest.read(path))

    @property
    def entries_path(self) -> Path:
        """Path of ``entries.parquet``."""
        return self.path / ENTRIES_FILE

    @property
    def groups_path(self) -> Path:
        """Path of ``groups.parquet``."""
        return self.path / GROUPS_FILE

    @property
    def merges_path(self) -> Path:
        """Path of ``merges.parquet`` (may not exist)."""
        return self.path / MERGES_FILE

    def entries_lazy(self) -> pl.LazyFrame:
        """``entries.parquet`` as a lazy frame."""
        return pl.scan_parquet(self.entries_path)

    def groups_lazy(self) -> pl.LazyFrame:
        """``groups.parquet`` as a lazy frame."""
        return pl.scan_parquet(self.groups_path)

    def merges_lazy(self) -> pl.LazyFrame | None:
        """``merges.parquet`` as a lazy frame, or ``None`` when the set has no merges."""
        return pl.scan_parquet(self.merges_path) if self.merges_path.exists() else None

    def groups(
        self,
        *,
        where: GroupFilter | None = None,
        sort: str = "count",
        limit: int | None = None,
        offset: int = 0,
        coarse: bool = False,
        **filters: Unpack[GroupFilterArgs],
    ) -> pl.DataFrame:
        """Return filtered, sorted groups; ``coarse=True`` aggregates by ``coarse_signature``.

        Filters come as one :class:`~atlas_log_analyzer.store.filters.GroupFilter`
        (``where=``) or as its fields given as keywords (``level="ERROR"``), not both.

        Args:
            where: The filters (default: none).
            sort: One of ``count`` (desc), ``last_seen`` (desc), ``first_seen`` (asc),
                ``signature``, ``template``.
            limit: Maximum number of rows.
            offset: Number of rows to skip (after sorting).
            coarse: Aggregate by ``coarse_signature``. The result keeps the groups.v1
                columns (``signature`` then equals ``coarse_signature``) and adds
                ``signatures`` (member signatures). ``count``/``first_seen``/``last_seen``/
                ``sources``/``source_count`` are exact; ``user_count``/``thread_count`` are
                the maximum over members (a lower bound) and ``top_users``/``top_threads``
                merge the members' top-10 lists. Template, level, logger and exception type
                come from the largest member. Filters apply to the member groups.
            **filters: The fields of :class:`GroupFilter` (``level``, ``min_level``,
                ``logger``, ``since``, ``until``, ``with_exception``, ``exception``,
                ``contains``) instead of ``where``.

        Raises:
            ValueError: Unknown level name or sort key.
            TypeError: Both ``where`` and keyword filters.

        """
        if sort not in GROUP_SORTS:
            raise ValueError(f"unknown sort {sort!r}; expected one of {', '.join(GROUP_SORTS)}")
        lf = self.groups_lazy()
        cond = group_filter(where, filters).predicate()
        if cond is not None:
            lf = lf.filter(cond)
        if coarse:
            lf = _coarse_groups(lf)
        match sort:
            case "count":
                lf = lf.sort(["count", "signature"], descending=[True, False])
            case "last_seen":
                lf = lf.sort(["last_seen", "signature"], descending=[True, False], nulls_last=True)
            case "first_seen":
                lf = lf.sort(["first_seen", "signature"], nulls_last=True)
            case _:
                lf = lf.sort([sort, "signature"])
        if offset or limit is not None:
            lf = lf.slice(offset, limit)
        return lf.collect()

    def count_groups(
        self,
        *,
        where: GroupFilter | None = None,
        coarse: bool = False,
        **filters: Unpack[GroupFilterArgs],
    ) -> int:
        """Count the groups :meth:`groups` would return with the same filters (no limit).

        With ``coarse=True`` this counts distinct ``coarse_signature`` values among
        the matching groups.

        Args:
            where: The filters (default: none).
            coarse: Count coarse groups.
            **filters: The fields of :class:`GroupFilter` instead of ``where``.

        Raises:
            ValueError: Unknown level name.
            TypeError: Both ``where`` and keyword filters.

        """
        lf = self.groups_lazy()
        cond = group_filter(where, filters).predicate()
        if cond is not None:
            lf = lf.filter(cond)
        agg = pl.col("coarse_signature").n_unique() if coarse else pl.len()
        return int(lf.select(agg).collect().item())

    def group(self, signature: str, *, coarse: bool = False) -> dict[str, Any] | None:
        """One group as a JSON-ready dict (see :func:`to_records`), or ``None`` when absent.

        With ``coarse=True``, ``signature`` is a coarse signature and the row is the
        aggregate described in :meth:`groups` (it has an extra ``signatures`` list).
        """
        if coarse:
            lf = _coarse_groups(self.groups_lazy().filter(pl.col("coarse_signature") == signature))
        else:
            lf = self.groups_lazy().filter(pl.col("signature") == signature)
        df = lf.head(1).collect()
        return to_records(df)[0] if df.height else None

    def entries(
        self,
        *,
        where: EntryFilter | None = None,
        limit: int | None = None,
        offset: int = 0,
        columns: list[str] | None = None,
        **filters: Unpack[EntryFilterArgs],
    ) -> pl.DataFrame:
        """Return filtered entries, ordered by timestamp (ties keep file order).

        Filters come as one :class:`~atlas_log_analyzer.store.filters.EntryFilter`
        (``where=``) or as its fields given as keywords (``signature="…"``), not both.

        Args:
            where: The filters (default: none).
            limit: Maximum number of rows.
            offset: Number of rows to skip (in timestamp order) before ``limit`` applies.
            columns: Columns to return (default: all entries.v1 columns).
            **filters: The fields of :class:`EntryFilter` (``signature``,
                ``coarse_signature``, ``level``, ``min_level``, ``logger``, ``user``,
                ``thread``, ``request_id``, ``since``, ``until``, ``contains``,
                ``with_exception``, ``exception``) instead of ``where``.

        Raises:
            ValueError: Unknown level name.
            TypeError: Both ``where`` and keyword filters.

        """
        cond = entry_filter(where, filters).predicate()
        lf = self.entries_lazy()
        if cond is not None:
            lf = lf.filter(cond)
        lf = lf.sort("ts", maintain_order=True)
        if offset or limit is not None:
            lf = lf.slice(offset, limit)
        if columns is not None:
            lf = lf.select(columns)
        return lf.collect()

    def count_entries(
        self, *, where: EntryFilter | None = None, **filters: Unpack[EntryFilterArgs]
    ) -> int:
        """Count the entries :meth:`entries` would return with the same filters (no limit).

        Args:
            where: The filters (default: none).
            **filters: The fields of :class:`EntryFilter` instead of ``where``.

        Raises:
            ValueError: Unknown level name.
            TypeError: Both ``where`` and keyword filters.

        """
        cond = entry_filter(where, filters).predicate()
        lf = self.entries_lazy()
        if cond is not None:
            lf = lf.filter(cond)
        return int(lf.select(pl.len()).collect().item())

    def histogram(
        self,
        bucket: str = "1h",
        *,
        where: EntryFilter | None = None,
        **filters: Unpack[EntryFilterArgs],
    ) -> list[dict[str, Any]]:
        """Entry counts per time bucket, contiguous from the first to the last non-empty one.

        Buckets are aligned to the Unix epoch in UTC (``"1d"`` starts at midnight UTC)
        and computed on integer epoch milliseconds, so no time-zone database is needed.
        Entries without a timestamp are not counted.

        Args:
            bucket: ``<n><unit>`` with unit ``s``, ``m``, ``h`` or ``d`` (``"5m"``, ``"1h"``).
            where: Entry filters (see :meth:`entries`).
            **filters: The fields of :class:`EntryFilter` instead of ``where``.

        Returns:
            ``[{"bucket": "2026-10-29T13:00:00Z", "count": int}, ...]`` (empty buckets
            have count 0).

        Raises:
            ValueError: Malformed bucket, unknown level, or more than
                :data:`MAX_HISTOGRAM_BUCKETS` buckets.
            TypeError: Both ``where`` and keyword filters.

        """
        size = _bucket_ms(bucket)
        cond = entry_filter(where, filters).predicate()
        lf = self.entries_lazy().filter(pl.col("ts").is_not_null() & has_time())
        if cond is not None:
            lf = lf.filter(cond)
        df = (
            lf.select((pl.col("ts").dt.epoch("ms") // size).alias("b"))
            .group_by("b")
            .agg(pl.len().alias("count"))
            .collect()
        )
        if df.height == 0:
            return []
        counts = {int(b): int(n) for b, n in df.iter_rows()}
        first, last = min(counts), max(counts)
        if last - first + 1 > MAX_HISTOGRAM_BUCKETS:
            raise ValueError(f"bucket {bucket!r} gives too many buckets; use a larger one")
        return [
            {"bucket": _bucket_iso(b * size), "count": counts.get(b, 0)}
            for b in range(first, last + 1)
        ]

    def context(
        self,
        entry_id: str,
        *,
        before: int = 5,
        after: int = 5,
        same_thread: bool = True,
        columns: list[str] | None = None,
    ) -> pl.DataFrame | None:
        """Return the entries immediately around one entry in time order, the entry included.

        Order is the order of :meth:`entries` (timestamp, ties in file order).

        Args:
            entry_id: Id of the anchor entry (exact).
            before: Entries to return before the anchor.
            after: Entries to return after the anchor.
            same_thread: Only consider entries of the anchor's thread.
            columns: Columns to return (default: all entries.v1 columns).

        Returns:
            At most ``before + 1 + after`` rows, or ``None`` when no entry has this id.

        Raises:
            ValueError: ``before`` or ``after`` is negative.

        """
        if before < 0 or after < 0:
            raise ValueError("before and after must be >= 0")
        lf = self.entries_lazy().with_row_index("_row")
        anchor = lf.filter(pl.col("id") == entry_id).select("_row", "thread").head(1).collect()
        if anchor.height == 0:
            return None
        row, thread = anchor.row(0)
        keys = lf
        if same_thread:
            same = pl.col("thread").is_null() if thread is None else pl.col("thread") == thread
            keys = keys.filter(same)
        order = keys.select("_row", "ts").sort(["ts", "_row"]).collect()["_row"]
        pos = int(order.index_of(row) or 0)
        start = max(0, pos - before)
        wanted = order.slice(start, pos - start + after + 1)
        out = lf.filter(pl.col("_row").is_in(wanted.implode())).sort(["ts", "_row"]).drop("_row")
        if columns is not None:
            out = out.select(columns)
        return out.collect()

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        """One entry as a dict (all columns), or ``None``.

        Values are Python objects: ``ts`` is a UTC ``datetime``, ``context`` a dict,
        ``exception_chain`` a list of dicts.
        """
        df = self.entries_lazy().filter(pl.col("id") == entry_id).head(1).collect()
        return rows(df)[0] if df.height else None

    def stats(self) -> dict[str, Any]:
        """Summarise the set: counts per level, logger (top 20) and hour; time range; sources.

        Returns:
            JSON-serialisable dict with keys ``id``, ``name``, ``format``, ``entry_count``,
            ``group_count``, ``parse_errors``, ``levels`` (``{name: count}``), ``loggers``
            (top 20 ``[{"logger", "count"}]``), ``hours`` (``[{"hour": iso, "count"}]``),
            ``time_range`` (``{"first", "last"}``) and ``sources`` (manifest entries).

        """
        lf = self.entries_lazy()
        totals, levels, loggers, hours = pl.collect_all(
            [
                lf.select(
                    pl.len().alias("n"),
                    pl.col("parse_error").is_not_null().sum().alias("parse_errors"),
                    pl.col("ts").filter(has_time()).min().alias("first"),
                    pl.col("ts").filter(has_time()).max().alias("last"),
                ),
                lf.group_by("level", "level_name").agg(pl.len().alias("count")).sort("level"),
                lf.group_by(pl.col("logger").cast(pl.String))
                .agg(pl.len().alias("count"))
                .sort(["count", "logger"], descending=[True, False])
                .head(TOP_LOGGERS),
                lf.filter(has_time())
                .group_by(pl.col("ts").dt.truncate("1h").alias("hour"))
                .agg(pl.len().alias("count"))
                .sort("hour"),
            ]
        )
        t = rows(totals)[0]
        m = self.manifest
        return {
            "id": m.id,
            "name": m.name,
            "format": m.format,
            "entry_count": int(t["n"]),
            "group_count": m.group_count,
            "parse_errors": int(t["parse_errors"] or 0),
            "levels": {str(r["level_name"]): int(r["count"]) for r in levels.iter_rows(named=True)},
            "loggers": [
                {"logger": r["logger"], "count": int(r["count"])}
                for r in loggers.iter_rows(named=True)
            ],
            "hours": [{"hour": _iso(r["hour"]), "count": int(r["count"])} for r in rows(hours)],
            "time_range": {"first": _iso(t["first"]), "last": _iso(t["last"])},
            "sources": [s.to_dict() for s in m.sources],
        }

    def sql(self, query: str, *, max_rows: int | None = None) -> pl.DataFrame:
        """Run DuckDB SQL with views ``entries`` and ``groups`` (and ``merges`` when present).

        The connection is an in-memory database meant for reading: after the views
        are created, file-system access is restricted to this set's Parquet files
        (``enable_external_access=false``) and the configuration is locked, so a
        query cannot read or write other files. The session time zone is UTC
        whatever the machine's zone: timestamps come back in UTC and
        ``date_trunc('day', ts)`` buckets at UTC midnight. Tables created by the
        query live only for the duration of the call. ``INTERVAL`` columns come back
        as text (``4 days 20:15:28.033``), which polars cannot import otherwise.

        Args:
            query: SQL text; the last statement's result is returned.
            max_rows: Return at most this many rows (``None`` = all).

        Raises:
            duckdb.Error: The query is invalid or tries to access other files.

        """
        import duckdb  # only this method needs it; keeps it out of every other command

        con = duckdb.connect(":memory:")
        try:
            views = {"entries": self.entries_path, "groups": self.groups_path}
            if self.merges_path.exists():
                views["merges"] = self.merges_path
            for view, path in views.items():
                con.execute(
                    f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{_sql_path(path)}')"
                )
            allowed = ", ".join(f"'{_sql_path(p)}'" for p in views.values())
            con.execute(f"SET allowed_paths = [{allowed}]")
            con.execute("SET enable_external_access = false")
            con.execute("SET TimeZone = 'UTC'")
            con.execute("SET lock_configuration = true")
            rel = con.sql(query)
            if rel is None:
                return pl.DataFrame()
            if any("INTERVAL" in str(t) for t in rel.types):
                # polars cannot import DuckDB intervals (also inside lists and structs): return
                # those columns as text ('4 days 20:15:28'); columns are renamed by position
                # first, as two names may differ only in case (DuckDB names ignore case)
                names = [f"_c{i}" for i in range(len(rel.columns))]
                cols = ", ".join(
                    f"CAST({n} AS VARCHAR) AS {_ident(c)}"
                    if "INTERVAL" in str(t)
                    else f"{n} AS {_ident(c)}"
                    for n, c, t in zip(names, rel.columns, rel.types, strict=True)
                )
                cast = rel.query("_q", f"SELECT {cols} FROM _q AS _r({', '.join(names)})")
                if cast is not None:
                    rel = cast
            if max_rows is not None:
                rel = rel.limit(max_rows)
            try:
                result: pl.DataFrame = rel.pl()
            except Exception:
                raise
            except BaseException as exc:  # polars panics on a type it cannot import
                if type(exc).__name__ != "PanicException":
                    raise
                raise duckdb.ConversionException(f"cannot return this result: {exc}") from exc
            return result
        finally:
            con.close()


def _coarse_groups(lf: pl.LazyFrame) -> pl.LazyFrame:
    keyed = lf.sort(["count", "signature"], descending=[True, False])
    base = keyed.group_by("coarse_signature", maintain_order=True).agg(
        pl.col("template").first(),
        pl.col("level").first(),
        pl.col("level_name").first(),
        pl.col("logger").first(),
        pl.col("exception_type").first(),
        pl.col("count").sum(),
        pl.col("first_seen").min(),
        pl.col("last_seen").max(),
        pl.col("user_count").max(),
        pl.col("thread_count").max(),
        pl.col("examples").explode().head(3).alias("examples"),
        pl.col("signature").alias("signatures"),
    )
    tops = []
    vc_expr = pl.struct("value", "count")
    for col, n in (("top_users", 10), ("top_threads", 10), ("sources", None)):
        vc = (
            lf.select("coarse_signature", pl.col(col).alias("vc"))
            .explode("vc")
            .drop_nulls("vc")
            .unnest("vc")
            .group_by("coarse_signature", "value")
            .agg(pl.col("count").sum())
            .sort(["count", "value"], descending=[True, False])
            .group_by("coarse_signature", maintain_order=True)
            .agg(vc_expr.head(n).alias(col) if n is not None else vc_expr.alias(col))
        )
        tops.append(vc)
    out = base
    for vc in tops:
        out = out.join(vc, on="coarse_signature", how="left")
    return out.with_columns(
        pl.col("coarse_signature").alias("signature"),
        pl.col("sources").list.len().fill_null(0).cast(pl.Int32).alias("source_count"),
        *(pl.col(c).fill_null([]) for c in ("top_users", "top_threads", "sources")),
    ).select(
        "signature",
        "coarse_signature",
        "template",
        "level",
        "level_name",
        "logger",
        "exception_type",
        "count",
        "first_seen",
        "last_seen",
        "user_count",
        "thread_count",
        "source_count",
        "top_users",
        "top_threads",
        "sources",
        "examples",
        "signatures",
    )
