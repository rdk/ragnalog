"""Streaming conversion of signed entries and groups to Parquet, and back to entries."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Protocol, Self

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from atlas_log_analyzer.core.formats.base import (
    CTX_IP,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    WELL_KNOWN_CONTEXT_KEYS,
)
from atlas_log_analyzer.core.grouping import Group, SignedEntry
from atlas_log_analyzer.core.models import (
    EMPTY_CONTEXT,
    ExceptionInfo,
    Frame,
    LogEntry,
    LogLevel,
    SourceRef,
)
from atlas_log_analyzer.store.schema import (
    DICT_STRING,
    ENTRIES_SCHEMA,
    EXCEPTION_TYPE,
    GROUPS_SCHEMA,
    MERGES_SCHEMA,
    TIMESTAMP,
)

__all__ = [
    "COMPRESSION",
    "ROW_GROUP_SIZE",
    "EntriesWriter",
    "MergeProposalLike",
    "SourceStats",
    "groups_from_entries",
    "iter_entries",
    "unique_entries",
    "write_groups",
    "write_merges",
]

ROW_GROUP_SIZE = 65_536
COMPRESSION = "zstd"
TOP_N = 10

_WELL_KNOWN = frozenset(WELL_KNOWN_CONTEXT_KEYS)
_NO_CONTEXT: list[tuple[str, str]] = []
_NO_EXCEPTIONS: list[Any] = []

# Index = numeric level value; used to turn the level column into names with one ``take``.
_LEVEL_NAMES = pa.array(
    [LogLevel(v).name if v in LogLevel._value2member_map_ else None for v in range(256)],
    pa.string(),
)


@dataclass(slots=True)
class SourceStats:
    """Per-source counters accumulated while writing entries."""

    entries: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    def update(self, n: int, first: datetime | None, last: datetime | None) -> None:
        """Merge the statistics of one batch."""
        self.entries += n
        if first is not None and (self.first_ts is None or first < self.first_ts):
            self.first_ts = first
        if last is not None and (self.last_ts is None or last > self.last_ts):
            self.last_ts = last


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ms(value: int | None) -> datetime | None:
    """Epoch milliseconds to an aware UTC datetime.

    pyarrow's own conversion of ``tz="UTC"`` timestamps needs tzdata, which is
    missing on Windows without the ``tzdata`` package, so it is avoided.
    """
    return None if value is None else _EPOCH + timedelta(milliseconds=value)


def _dict_array(values: Sequence[str | None]) -> pa.Array:
    return pa.array(values, pa.string()).dictionary_encode()


def _exception_rows(chain: Sequence[ExceptionInfo]) -> list[tuple[Any, ...]]:
    return [
        (
            ex.type,
            ex.message,
            [(f.location, f.file, f.line, f.module) for f in ex.frames],
            ex.omitted_frames,
        )
        for ex in chain
    ]


class EntriesWriter:
    """Streams :class:`SignedEntry` objects into ``entries.parquet``.

    Entries with an ``id`` already written are skipped. Rows are buffered in
    column lists and flushed as one row group every ``row_group_size`` rows.

    Example::

        with EntriesWriter(path) as w:
            w.write(signed_entries)
        w.count, w.duplicates, w.sources
    """

    def __init__(self, path: Path, *, row_group_size: int = ROW_GROUP_SIZE) -> None:
        self.path = path
        self.row_group_size = row_group_size
        self.count = 0
        """Rows written."""
        self.duplicates = 0
        """Entries skipped because their id was already written."""
        self.sources: dict[str, SourceStats] = {}
        """Statistics keyed by ``entry.source.path``."""
        self._seen: set[str] = set()
        self._writer = pq.ParquetWriter(
            str(path), ENTRIES_SCHEMA, compression=COMPRESSION, write_statistics=True
        )
        self._reset()

    def _reset(self) -> None:
        self._id: list[str] = []
        self._ts: list[datetime] = []
        self._tz: list[int | None] = []
        self._level: list[int] = []
        self._thread: list[str] = []
        self._logger: list[str] = []
        self._message: list[str] = []
        self._body: list[str] = []
        self._user: list[str | None] = []
        self._request_id: list[str | None] = []
        self._session: list[str | None] = []
        self._ip: list[str | None] = []
        self._url: list[str | None] = []
        self._request_url: list[str | None] = []
        self._context: list[list[tuple[str, str]]] = []
        self._exc_type: list[str | None] = []
        self._exc_message: list[str | None] = []
        self._exc_chain: list[list[Any]] = []
        self._signature: list[str] = []
        self._coarse: list[str] = []
        self._template: list[str] = []
        self._variables: list[tuple[str, ...]] = []
        self._source_path: list[str] = []
        self._source_line: list[int] = []
        self._source_offset: list[int] = []
        self._raw: list[str] = []
        self._parse_error: list[str | None] = []

    def write(self, signed: Iterable[SignedEntry]) -> None:
        """Consume ``signed`` and append its (deduplicated) entries."""
        seen = self._seen
        size = self.row_group_size
        # Local bindings keep the per-row loop cheap.
        id_, ts_, tz_, level_ = self._id.append, self._ts.append, self._tz.append, self._level
        for se in signed:
            e = se.entry
            eid = e.id
            if eid in seen:
                self.duplicates += 1
                continue
            seen.add(eid)
            id_(eid)
            ts = e.timestamp
            ts_(ts)
            off = ts.utcoffset()
            tz_(None if off is None else int(off.total_seconds()) // 60)
            level_.append(int(e.level))
            self._thread.append(e.thread)
            self._logger.append(e.logger)
            self._message.append(e.message)
            self._body.append(e.body)
            ctx = e.context
            if ctx:
                self._user.append(ctx.get(CTX_USER))
                self._request_id.append(ctx.get(CTX_REQUEST_ID))
                self._session.append(ctx.get(CTX_SESSION))
                self._ip.append(ctx.get(CTX_IP))
                self._url.append(ctx.get(CTX_URL))
                self._request_url.append(ctx.get(CTX_REQUEST_URL))
                extra = [(k, v) for k, v in ctx.items() if k not in _WELL_KNOWN]
                self._context.append(extra or _NO_CONTEXT)
            else:
                self._user.append(None)
                self._request_id.append(None)
                self._session.append(None)
                self._ip.append(None)
                self._url.append(None)
                self._request_url.append(None)
                self._context.append(_NO_CONTEXT)
            chain = e.exceptions
            if chain:
                self._exc_type.append(chain[0].type)
                self._exc_message.append(chain[0].message)
                self._exc_chain.append(_exception_rows(chain))
            else:
                self._exc_type.append(None)
                self._exc_message.append(None)
                self._exc_chain.append(_NO_EXCEPTIONS)
            sig = se.signature
            self._signature.append(sig.hash)
            self._coarse.append(se.coarse.hash)
            self._template.append(sig.template)
            self._variables.append(sig.variables)
            src = e.source
            self._source_path.append(src.path)
            self._source_line.append(src.line)
            self._source_offset.append(src.offset)
            self._raw.append(e.raw)
            self._parse_error.append(e.parse_error)
            if len(level_) >= size:
                self._flush()
                id_, ts_, tz_, level_ = (
                    self._id.append,
                    self._ts.append,
                    self._tz.append,
                    self._level,
                )

    def _flush(self) -> None:
        n = len(self._id)
        if not n:
            return
        level = pa.array(self._level, pa.uint8())
        level_name = pc.take(_LEVEL_NAMES, level.cast(pa.int32()))
        source_path = _dict_array(self._source_path)
        ts = pa.array(self._ts, TIMESTAMP)
        arrays = [
            pa.array(self._id, pa.string()),
            ts,
            pa.array(self._tz, pa.int16()),
            level,
            level_name.dictionary_encode(),
            pa.array(self._thread, pa.string()),
            _dict_array(self._logger),
            pa.array(self._message, pa.string()),
            pa.array(self._body, pa.string()),
            pa.array(self._user, pa.string()),
            pa.array(self._request_id, pa.string()),
            pa.array(self._session, pa.string()),
            pa.array(self._ip, pa.string()),
            pa.array(self._url, pa.string()),
            pa.array(self._request_url, pa.string()),
            pa.array(self._context, ENTRIES_SCHEMA.field("context").type),
            pa.array(self._exc_type, pa.string()),
            pa.array(self._exc_message, pa.string()),
            pa.array(self._exc_chain, pa.list_(EXCEPTION_TYPE)),
            pa.array(self._signature, pa.string()),
            pa.array(self._coarse, pa.string()),
            pa.array(self._template, pa.string()),
            pa.array(self._variables, pa.list_(pa.string())),
            source_path,
            pa.array(self._source_line, pa.uint32()),
            pa.array(self._source_offset, pa.uint64()),
            pa.array(self._raw, pa.string()),
            pa.array(self._parse_error, pa.string()),
        ]
        batch = pa.RecordBatch.from_arrays(arrays, schema=ENTRIES_SCHEMA)
        self._writer.write_batch(batch, row_group_size=self.row_group_size)
        self.count += n
        # Per-source counts and time range, computed vectorised on the batch.
        per_source = (
            pa.table({"p": source_path.cast(pa.string()), "ts": ts})
            .group_by("p")
            .aggregate([("ts", "count"), ("ts", "min"), ("ts", "max")])
        )
        for p, cnt, lo, hi in zip(
            per_source["p"].to_pylist(),
            per_source["ts_count"].to_pylist(),
            per_source["ts_min"].cast(pa.int64()).to_pylist(),
            per_source["ts_max"].cast(pa.int64()).to_pylist(),
            strict=True,
        ):
            self.sources.setdefault(p, SourceStats()).update(cnt, _ms(lo), _ms(hi))
        self._reset()

    def close(self) -> None:
        """Flush buffered rows and close the Parquet file."""
        self._flush()
        self._writer.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
        else:
            self._writer.close()


def _value_counts(counter: Any, limit: int | None) -> list[tuple[str, int]]:
    return [(str(k), int(v)) for k, v in counter.most_common(limit)]


def write_groups(path: Path, groups: Iterable[Group]) -> int:
    """Write ``groups.parquet`` (sorted by count, descending) and return the number of rows."""
    rows = sorted(groups, key=lambda g: (-g.count, g.signature))
    cols: dict[str, list[Any]] = {name: [] for name in GROUPS_SCHEMA.names}
    for g in rows:
        cols["signature"].append(g.signature)
        cols["coarse_signature"].append(g.coarse_signature)
        cols["template"].append(g.template)
        cols["level"].append(int(g.level))
        cols["level_name"].append(LogLevel(g.level).name)
        cols["logger"].append(g.logger)
        cols["exception_type"].append(g.exception_type)
        cols["count"].append(g.count)
        cols["first_seen"].append(g.first_seen)
        cols["last_seen"].append(g.last_seen)
        cols["user_count"].append(len(g.users))
        cols["thread_count"].append(len(g.threads))
        cols["source_count"].append(len(g.sources))
        cols["top_users"].append(_value_counts(g.users, TOP_N))
        cols["top_threads"].append(_value_counts(g.threads, TOP_N))
        cols["sources"].append(_value_counts(g.sources, None))
        cols["examples"].append(list(g.examples))
    arrays = []
    for field in GROUPS_SCHEMA:
        if field.type == DICT_STRING:
            arrays.append(pa.array(cols[field.name], pa.string()).dictionary_encode())
        else:
            arrays.append(pa.array(cols[field.name], field.type))
    table = pa.Table.from_arrays(arrays, schema=GROUPS_SCHEMA)
    pq.write_table(table, str(path), compression=COMPRESSION)
    return len(rows)


class MergeProposalLike(Protocol):
    """Shape of a Drain3 merge proposal (``core.mining.MergeProposal``)."""

    @property
    def cluster_id(self) -> int: ...  # noqa: D102

    @property
    def drain_template(self) -> str: ...  # noqa: D102

    @property
    def signatures(self) -> Sequence[str]: ...  # noqa: D102


def write_merges(path: Path, proposals: Iterable[MergeProposalLike]) -> int:
    """Write ``merges.parquet`` and return the number of rows."""
    ids: list[int] = []
    templates: list[str] = []
    sigs: list[list[str]] = []
    for p in proposals:
        ids.append(p.cluster_id)
        templates.append(p.drain_template)
        sigs.append(list(p.signatures))
    table = pa.Table.from_arrays(
        [
            pa.array(ids, pa.int64()),
            pa.array(templates, pa.string()),
            pa.array(sigs, pa.list_(pa.string())),
        ],
        schema=MERGES_SCHEMA,
    )
    pq.write_table(table, str(path), compression=COMPRESSION)
    return len(ids)


_TZ_CACHE: dict[int, timezone] = {}


def _tz(minutes: int | None) -> timezone:
    if minutes is None:
        return UTC
    tz = _TZ_CACHE.get(minutes)
    if tz is None:
        tz = _TZ_CACHE[minutes] = timezone(timedelta(minutes=minutes))
    return tz


def _row_to_entry(row: dict[str, Any], fmt: str) -> LogEntry:
    ctx: dict[str, str] = {}
    for key in WELL_KNOWN_CONTEXT_KEYS:
        value = row[key]
        if value is not None:
            ctx[key] = value
    if row["context"]:
        ctx.update(row["context"])
    exceptions = tuple(
        ExceptionInfo(
            type=ex["type"],
            message=ex["message"],
            frames=tuple(Frame(**f) for f in ex["frames"] or ()),
            omitted_frames=ex["omitted_frames"] or 0,
        )
        for ex in row["exception_chain"] or ()
    )
    return LogEntry(
        id=row["id"],
        format=fmt,
        timestamp=(_EPOCH + timedelta(milliseconds=row["ts"])).astimezone(_tz(row["tz_offset"])),
        level=LogLevel(row["level"]),
        thread=row["thread"] or "",
        logger=row["logger"] or "",
        message=row["message"] or "",
        body=row["body"] or "",
        exceptions=exceptions,
        context=MappingProxyType(ctx) if ctx else EMPTY_CONTEXT,
        source=SourceRef(row["source_path"] or "", row["source_line"], row["source_offset"]),
        raw=row["raw"] or "",
        parse_error=row["parse_error"],
    )


_ENTRY_COLUMNS = [
    "id",
    "ts",
    "tz_offset",
    "level",
    "thread",
    "logger",
    "message",
    "body",
    *WELL_KNOWN_CONTEXT_KEYS,
    "context",
    "exception_chain",
    "source_path",
    "source_line",
    "source_offset",
    "raw",
    "parse_error",
]


def iter_entries(path: Path, fmt: str, *, batch_size: int = 8192) -> Iterator[LogEntry]:
    """Rebuild :class:`LogEntry` objects from ``entries.parquet`` (timestamps at ms precision)."""
    with pq.ParquetFile(str(path)) as pf:
        for batch in pf.iter_batches(batch_size=batch_size, columns=_ENTRY_COLUMNS):
            ts_idx = batch.schema.get_field_index("ts")
            batch = batch.set_column(ts_idx, "ts", batch.column(ts_idx).cast(pa.int64()))
            for row in batch.to_pylist():
                yield _row_to_entry(row, fmt)


def unique_entries(entries: Iterable[LogEntry]) -> Iterator[LogEntry]:
    """Drop entries whose ``id`` was already seen (first occurrence wins).

    Put this in front of the grouper so group statistics match the
    deduplicated ``entries.parquet``.
    """
    seen: set[str] = set()
    for e in entries:
        if e.id not in seen:
            seen.add(e.id)
            yield e


def groups_from_entries(entries_path: Path, out_path: Path, *, max_examples: int = 3) -> int:
    """Recompute ``groups.parquet`` from ``entries.parquet`` and return the number of rows.

    Used by :meth:`Store.ingest` when the caller's groups disagree with the
    deduplicated entries (e.g. the grouper saw duplicate entries).
    """
    lf = pl.scan_parquet(entries_path)
    base = lf.group_by("signature").agg(
        pl.col("coarse_signature").first(),
        pl.col("template").first(),
        pl.col("level").first(),
        pl.col("level_name").cast(pl.String).first(),
        pl.col("logger").cast(pl.String).first(),
        pl.col("exception_type").first(),
        pl.len().cast(pl.Int64).alias("count"),
        pl.col("ts").min().alias("first_seen"),
        pl.col("ts").max().alias("last_seen"),
        pl.col("user").drop_nulls().n_unique().cast(pl.Int32).alias("user_count"),
        pl.col("thread").drop_nulls().n_unique().cast(pl.Int32).alias("thread_count"),
        pl.col("source_path").drop_nulls().n_unique().cast(pl.Int32).alias("source_count"),
        # first max_examples - 1 ids plus the last one, as Grouper does
        pl.col("id")
        .head(max_examples - 1)
        .append(pl.col("id").tail(1))
        .unique(maintain_order=True)
        .alias("examples"),
    )
    for src, name, limit in (
        ("user", "top_users", TOP_N),
        ("thread", "top_threads", TOP_N),
        ("source_path", "sources", None),
    ):
        vc = pl.struct(pl.col(src).cast(pl.String).alias("value"), pl.col("count"))
        top = (
            lf.filter(pl.col(src).is_not_null())
            .group_by("signature", src)
            .agg(pl.len().cast(pl.Int64).alias("count"))
            .sort(["count", src], descending=[True, False])
            .group_by("signature", maintain_order=True)
            .agg((vc.head(limit) if limit is not None else vc).alias(name))
        )
        base = base.join(top, on="signature", how="left")
    df = base.sort(["count", "signature"], descending=[True, False]).collect()
    table = df.to_arrow()
    arrays = []
    for field in GROUPS_SCHEMA:
        col = table[field.name]
        if field.type == DICT_STRING:
            arrays.append(col.cast(pa.string()).dictionary_encode())
        elif pa.types.is_list(field.type):
            arrays.append(pc.fill_null(col.cast(field.type), pa.scalar([], field.type)))
        else:
            arrays.append(col.cast(field.type))
    pq.write_table(
        pa.Table.from_arrays(arrays, schema=GROUPS_SCHEMA), str(out_path), compression=COMPRESSION
    )
    return df.height
