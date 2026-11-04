"""Log set manifest and directory conventions.

Directory layout::

    <store>/<logset-id>/
        manifest.json      logset.v1
        entries.parquet    entries.v1
        groups.parquet     groups.v1
        merges.parquet     merges.v1 (optional)
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.store.levels import level_values
from atlas_log_analyzer.store.schema import SCHEMA_VERSION, schema_major

__all__ = ["LevelSpec", "LogSet", "LogSetManifest", "SourceInfo", "rows", "to_records"]

MANIFEST_FILE = "manifest.json"
ENTRIES_FILE = "entries.parquet"
GROUPS_FILE = "groups.parquet"
MERGES_FILE = "merges.parquet"

MANIFEST_SCHEMA = "logset.v1"
GROUP_SORTS = ("count", "last_seen", "first_seen", "signature", "template")
"""Values accepted by ``LogSet.groups(sort=...)``."""
TOP_LOGGERS = 20

type LevelSpec = LogLevel | str | Iterable[LogLevel | str]
"""A level filter: a :class:`LogLevel`, a name or comma-separated names, or an iterable.

Names as in :mod:`atlas_log_analyzer.store.levels` (``UNKNOWN`` included, ``WARNING``
= ``WARN``); a blank string means no filter."""

_BUCKET_UNITS_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
_BUCKET_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
MAX_HISTOGRAM_BUCKETS = 100_000
"""Upper bound on the number of (zero-filled) buckets :meth:`LogSet.histogram` returns."""


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat()


def _parse_ts(text: str | None) -> datetime | None:
    if text is None:
        return None
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _utc(ts: datetime) -> datetime:
    """Naive datetimes are taken as UTC."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """One ingested file as recorded in the manifest."""

    path: str
    size: int
    xxh3: str
    first_ts: datetime | None
    last_ts: datetime | None
    entries: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict (ISO timestamps)."""
        return {
            "path": self.path,
            "size": self.size,
            "xxh3": self.xxh3,
            "first_ts": _iso(self.first_ts),
            "last_ts": _iso(self.last_ts),
            "entries": self.entries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceInfo:
        """Inverse of :meth:`to_dict`."""
        return cls(
            path=str(data["path"]),
            size=int(data["size"]),
            xxh3=str(data["xxh3"]),
            first_ts=_parse_ts(data.get("first_ts")),
            last_ts=_parse_ts(data.get("last_ts")),
            entries=int(data.get("entries", 0)),
        )


@dataclass(frozen=True, slots=True)
class LogSetManifest:
    """``manifest.json`` (schema ``logset.v1``)."""

    id: str
    """xxh3-64 hex of the sorted source digests."""
    name: str | None
    format: str
    sources: tuple[SourceInfo, ...]
    first_ts: datetime | None
    last_ts: datetime | None
    entry_count: int
    group_count: int
    created_at: datetime
    tool_version: str
    schema_version: str
    signature_version: int
    mask_rules_version: int
    signature_spec: str
    """``SignatureSpec.key`` used for the primary signature."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict with ``"schema": "logset.v1"``."""
        return {
            "schema": MANIFEST_SCHEMA,
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "sources": [s.to_dict() for s in self.sources],
            "first_ts": _iso(self.first_ts),
            "last_ts": _iso(self.last_ts),
            "entry_count": self.entry_count,
            "group_count": self.group_count,
            "created_at": self.created_at.isoformat(),
            "tool_version": self.tool_version,
            "schema_version": self.schema_version,
            "signature_version": self.signature_version,
            "mask_rules_version": self.mask_rules_version,
            "signature_spec": self.signature_spec,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogSetManifest:
        """Inverse of :meth:`to_dict`; raises ``ValueError`` on an unsupported schema version."""
        schema = data.get("schema")
        if not isinstance(schema, str) or not schema.startswith("logset.v"):
            raise ValueError(f"not a logset manifest (schema={schema!r})")
        try:
            schema_v = int(schema.removeprefix("logset.v"))
            version = str(data["schema_version"])
            major = schema_major(version)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid manifest schema version: {exc}") from exc
        current = schema_major(SCHEMA_VERSION)
        if schema_v > current or major > current:
            raise ValueError(
                f"log set uses schema {schema} / {version}, newer than supported {SCHEMA_VERSION}"
            )
        try:
            created = _parse_ts(data["created_at"])
            assert created is not None
            return cls(
                id=str(data["id"]),
                name=data.get("name"),
                format=str(data["format"]),
                sources=tuple(SourceInfo.from_dict(s) for s in data.get("sources", ())),
                first_ts=_parse_ts(data.get("first_ts")),
                last_ts=_parse_ts(data.get("last_ts")),
                entry_count=int(data["entry_count"]),
                group_count=int(data["group_count"]),
                created_at=created,
                tool_version=str(data["tool_version"]),
                schema_version=version,
                signature_version=int(data["signature_version"]),
                mask_rules_version=int(data["mask_rules_version"]),
                signature_spec=str(data["signature_spec"]),
            )
        except (KeyError, TypeError, AssertionError) as exc:
            raise ValueError(f"invalid manifest: missing or bad field {exc}") from exc

    def write(self, directory: Path) -> None:
        """Write ``manifest.json`` into ``directory``."""
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        (directory / MANIFEST_FILE).write_text(text, encoding="utf-8", newline="\n")

    @classmethod
    def read(cls, directory: Path) -> LogSetManifest:
        """Read ``manifest.json`` from ``directory``; ``ValueError`` when invalid."""
        try:
            data = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt manifest in {directory}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"corrupt manifest in {directory}: not an object")
        return cls.from_dict(data)


def _level_values(spec: LevelSpec) -> list[int]:
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
    out: list[pl.Expr] = []
    if level is not None and (values := _level_values(level)):
        out.append(pl.col("level").is_in(values))
    if min_level is not None and (lv := _level_values(min_level)):
        if len(lv) != 1:
            raise ValueError("min_level takes a single level")
        out.append((pl.col("level") >= lv[0]) & (pl.col("level") != int(LogLevel.UNKNOWN)))
    return out


def _logger_filter(logger: str) -> pl.Expr:
    """Exact logger name, or any logger in that package (``com.foo`` matches ``com.foo.Bar``)."""
    col = pl.col("logger").cast(pl.String)
    return (col == logger) | col.str.starts_with(logger + ".")


def _icontains(column: str, text: str) -> pl.Expr:
    """Case-insensitive literal substring test on a (possibly dictionary-encoded) column."""
    return pl.col(column).cast(pl.String).str.contains(f"(?i){re.escape(text)}")


def _exception_filters(with_exception: bool, exception: str | None) -> list[pl.Expr]:
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


def _entry_predicate(
    *,
    signature: str | None = None,
    coarse_signature: str | None = None,
    level: LevelSpec | None = None,
    min_level: LevelSpec | None = None,
    logger: str | None = None,
    user: str | None = None,
    thread: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    contains: str | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    request_id: str | None = None,
) -> pl.Expr | None:
    """Conjunction of the entry filters of :meth:`LogSet.entries`, or ``None`` when none."""
    filters = _level_filters(level, min_level)
    filters += _exception_filters(with_exception, exception)
    if request_id is not None:
        filters.append(pl.col("request_id") == request_id)
    if signature is not None:
        filters.append(pl.col("signature") == signature)
    if coarse_signature is not None:
        filters.append(pl.col("coarse_signature") == coarse_signature)
    if logger is not None:
        filters.append(_logger_filter(logger))
    if user is not None:
        filters.append(pl.col("user") == user)
    if thread is not None:
        filters.append(pl.col("thread") == thread)
    if since is not None:
        filters.append(pl.col("ts") >= _utc(since))
    if until is not None:
        filters.append(pl.col("ts") < _utc(until))
    if contains is not None:
        filters.append(pl.col("raw").str.contains(f"(?i){re.escape(contains)}"))
    return _and(filters)


def _group_predicate(
    *,
    level: LevelSpec | None = None,
    min_level: LevelSpec | None = None,
    logger: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    contains: str | None = None,
) -> pl.Expr | None:
    """Conjunction of the group filters of :meth:`LogSet.groups`, or ``None`` when none."""
    filters = _level_filters(level, min_level)
    filters += _exception_filters(with_exception, exception)
    if contains is not None:
        filters.append(_icontains("template", contains))
    if logger is not None:
        filters.append(_logger_filter(logger))
    if since is not None:
        filters.append(pl.col("last_seen") >= _utc(since))
    if until is not None:
        filters.append(pl.col("first_seen") < _utc(until))
    return _and(filters)


def _bucket_ms(bucket: str) -> int:
    """``"15m"``, ``"1h"``, ``"1d"`` … as milliseconds; ``ValueError`` when malformed."""
    m = _BUCKET_RE.match(bucket)
    if m is None or int(m.group(1)) <= 0:
        raise ValueError(f"invalid bucket {bucket!r}; expected e.g. '5m', '1h', '1d'")
    return int(m.group(1)) * _BUCKET_UNITS_MS[m.group(2)]


def _bucket_iso(start_ms: int) -> str:
    return datetime.fromtimestamp(start_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


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
        level: LevelSpec | None = None,
        min_level: LevelSpec | None = None,
        logger: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort: str = "count",
        limit: int | None = None,
        offset: int = 0,
        coarse: bool = False,
        with_exception: bool = False,
        exception: str | None = None,
        contains: str | None = None,
    ) -> pl.DataFrame:
        """Return filtered, sorted groups; ``coarse=True`` aggregates by ``coarse_signature``.

        Args:
            level: Level, level name, comma-separated names (``"WARN,ERROR"``) or an
                iterable of levels/names.
            min_level: Lowest level to include (``"WARN"`` or ``LogLevel.WARN`` → WARN,
                ERROR, FATAL).
            logger: Logger name; also matches loggers below it (``com.foo`` → ``com.foo.Bar``).
            since: Keep groups seen at or after this time (``last_seen >= since``).
            until: Keep groups seen before this time (``first_seen < until``).
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
            with_exception: Keep groups whose ``exception_type`` is not null (any level).
            exception: Case-insensitive substring of ``exception_type``.
            contains: Case-insensitive substring of ``template``.

        Raises:
            ValueError: Unknown level name or sort key.

        """
        if sort not in GROUP_SORTS:
            raise ValueError(f"unknown sort {sort!r}; expected one of {', '.join(GROUP_SORTS)}")
        lf = self.groups_lazy()
        cond = _group_predicate(
            level=level,
            min_level=min_level,
            logger=logger,
            since=since,
            until=until,
            with_exception=with_exception,
            exception=exception,
            contains=contains,
        )
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
        level: LevelSpec | None = None,
        min_level: LevelSpec | None = None,
        logger: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        coarse: bool = False,
        with_exception: bool = False,
        exception: str | None = None,
        contains: str | None = None,
    ) -> int:
        """Count the groups :meth:`groups` would return with the same filters (no limit).

        With ``coarse=True`` this counts distinct ``coarse_signature`` values among
        the matching groups.

        Raises:
            ValueError: Unknown level name.

        """
        lf = self.groups_lazy()
        cond = _group_predicate(
            level=level,
            min_level=min_level,
            logger=logger,
            since=since,
            until=until,
            with_exception=with_exception,
            exception=exception,
            contains=contains,
        )
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
        signature: str | None = None,
        level: LevelSpec | None = None,
        min_level: LevelSpec | None = None,
        logger: str | None = None,
        user: str | None = None,
        thread: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        contains: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        columns: list[str] | None = None,
        coarse_signature: str | None = None,
        with_exception: bool = False,
        exception: str | None = None,
        request_id: str | None = None,
    ) -> pl.DataFrame:
        """Return filtered entries, ordered by timestamp (ties keep file order).

        Args:
            signature: Primary signature (exact).
            level: Level, level name, comma-separated names or an iterable (see :meth:`groups`).
            min_level: Lowest level to include.
            logger: Logger name or package prefix (see :meth:`groups`).
            user: User (exact).
            thread: Thread name (exact).
            since: Inclusive lower bound on ``ts`` (naive = UTC).
            until: Exclusive upper bound on ``ts`` (naive = UTC).
            contains: Case-insensitive literal substring of ``raw``.
            limit: Maximum number of rows.
            offset: Number of rows to skip (in timestamp order) before ``limit`` applies.
            columns: Columns to return (default: all entries.v1 columns).
            coarse_signature: Coarse signature (exact).
            with_exception: Keep entries whose ``exception_type`` is not null (any level).
            exception: Case-insensitive substring of ``exception_type``.
            request_id: Request id from the request context (exact).

        Raises:
            ValueError: Unknown level name.

        """
        cond = _entry_predicate(
            signature=signature,
            coarse_signature=coarse_signature,
            level=level,
            min_level=min_level,
            logger=logger,
            user=user,
            thread=thread,
            since=since,
            until=until,
            contains=contains,
            with_exception=with_exception,
            exception=exception,
            request_id=request_id,
        )
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
        self,
        *,
        signature: str | None = None,
        level: LevelSpec | None = None,
        min_level: LevelSpec | None = None,
        logger: str | None = None,
        user: str | None = None,
        thread: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        contains: str | None = None,
        coarse_signature: str | None = None,
        with_exception: bool = False,
        exception: str | None = None,
        request_id: str | None = None,
    ) -> int:
        """Count the entries :meth:`entries` would return with the same filters (no limit).

        Raises:
            ValueError: Unknown level name.

        """
        cond = _entry_predicate(
            signature=signature,
            coarse_signature=coarse_signature,
            level=level,
            min_level=min_level,
            logger=logger,
            user=user,
            thread=thread,
            since=since,
            until=until,
            contains=contains,
            with_exception=with_exception,
            exception=exception,
            request_id=request_id,
        )
        lf = self.entries_lazy()
        if cond is not None:
            lf = lf.filter(cond)
        return int(lf.select(pl.len()).collect().item())

    def histogram(
        self,
        bucket: str = "1h",
        *,
        signature: str | None = None,
        level: LevelSpec | None = None,
        min_level: LevelSpec | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Entry counts per time bucket, contiguous from the first to the last non-empty one.

        Buckets are aligned to the Unix epoch in UTC (``"1d"`` starts at midnight UTC)
        and computed on integer epoch milliseconds, so no time-zone database is needed.
        Entries without a timestamp are not counted.

        Args:
            bucket: ``<n><unit>`` with unit ``s``, ``m``, ``h`` or ``d`` (``"5m"``, ``"1h"``).
            signature: Primary signature (exact).
            level: Level filter (see :meth:`entries`).
            min_level: Lowest level to include.
            since: Inclusive lower bound on ``ts``.
            until: Exclusive upper bound on ``ts``.

        Returns:
            ``[{"bucket": "2026-10-29T13:00:00Z", "count": int}, ...]`` (empty buckets
            have count 0).

        Raises:
            ValueError: Malformed bucket, unknown level, or more than
                :data:`MAX_HISTOGRAM_BUCKETS` buckets.

        """
        size = _bucket_ms(bucket)
        cond = _entry_predicate(
            signature=signature, level=level, min_level=min_level, since=since, until=until
        )
        lf = self.entries_lazy().filter(pl.col("ts").is_not_null())
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
                    pl.col("ts").min().alias("first"),
                    pl.col("ts").max().alias("last"),
                ),
                lf.group_by("level", "level_name").agg(pl.len().alias("count")).sort("level"),
                lf.group_by(pl.col("logger").cast(pl.String))
                .agg(pl.len().alias("count"))
                .sort(["count", "logger"], descending=[True, False])
                .head(TOP_LOGGERS),
                lf.group_by(pl.col("ts").dt.truncate("1h").alias("hour"))
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
        query live only for the duration of the call.

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
            if max_rows is not None:
                rel = rel.limit(max_rows)
            result: pl.DataFrame = rel.pl()
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
