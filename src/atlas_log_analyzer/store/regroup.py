"""Re-signing stored entries column-wise, for ``regroup`` and ``upgrade``.

Signing depends only on a few columns (level, logger, message, the exception
chain and, for a format with ``titles_from_body``, the body's lead line of an
entry whose message is blank), and a set has far fewer distinct combinations of
them than entries (105,883 for 712,673 Jira entries). :func:`resign_entries`
selects those key columns with polars, signs each distinct key once, and rewrites
``entries.parquet`` row group by row group with the derived columns replaced;
every other column is passed through as stored. The result equals re-signing
:class:`~atlas_log_analyzer.core.models.LogEntry` objects rebuilt from every row.

With ``reassemble`` (a set built by an older version of its format) the entries
are also assembled as the format assembles them today, from the stored rows
alone (the log files may be gone): banners are attached to the entry they
announce, by the rule of :func:`~atlas_log_analyzer.core.parser.attach_banners`
applied in file order (the stored order of the rows). Their rows are
dropped and the entry they announce gets ``context["preamble"]``, which equals
ingesting the same files with the current format. Entries an older version built
differently from the same text (:data:`_REBUILD_HINT`) are first built again from
their stored ``raw`` text by the format itself.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from atlas_log_analyzer.core.decoration import is_banner_text, lead_line
from atlas_log_analyzer.core.formats.base import (
    CTX_IP,
    CTX_PREAMBLE,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    WELL_KNOWN_CONTEXT_KEYS,
    LogFormat,
    banner_window_ms,
    titles_from_body,
)
from atlas_log_analyzer.core.models import ExceptionInfo, Frame, LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.parser import MAX_BANNERS
from atlas_log_analyzer.core.pipeline import Recipe
from atlas_log_analyzer.store.schema import ENTRIES_SCHEMA
from atlas_log_analyzer.store.writer import COMPRESSION, _entries_encodings

__all__ = ["DERIVED_COLUMNS", "resign_entries"]

DERIVED_COLUMNS = ("signature", "coarse_signature", "template", "variables")
"""Entry columns computed by the signer; :func:`resign_entries` replaces exactly these."""

_KEY_COLUMNS = ("level", "logger", "message", "chain", "lead")
_REBUILT_COLUMNS = {
    "user": CTX_USER,
    "request_id": CTX_REQUEST_ID,
    "session": CTX_SESSION,
    "ip": CTX_IP,
    "url": CTX_URL,
    "request_url": CTX_REQUEST_URL,
}
"""Context columns of a re-built entry (the writer's well-known context keys)."""
_WELL_KNOWN = frozenset(WELL_KNOWN_CONTEXT_KEYS)
_REBUILD_HINT = pl.col("body").fill_null("").str.contains("Suppressed: ", literal=True) | (
    pl.col("user").fill_null("").str.contains(" ", literal=True) & pl.col("request_id").is_null()
)
"""Rows an older format version may have built differently from the same text: a
suppressed exception (before jira@3 its own ``Caused by:`` joined the main chain) and
a user with a space, request context unsplit (``Automation Robot 1x2x3 …`` was the
user). Re-building any other row would give it back unchanged."""
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ALNUM = "[0-9A-Za-z]"
"""Blank and banner messages hold no ASCII letter or digit: the polars pre-filter of
:func:`_special_rows`, whose rows the Python rules then decide exactly."""


def _key_frame(entries_path: Path, frames: int) -> pl.DataFrame:
    """Read the columns the signer uses, one row per entry, in file order."""
    exc = pl.element().struct
    parts = [exc.field("type"), exc.field("message")]
    if frames > 0:
        locations = (
            exc.field("frames").list.head(frames).list.eval(pl.element().struct.field("location"))
        )
        parts.append(locations.alias("frames"))
    return (
        pl.scan_parquet(entries_path)
        .select(
            pl.col("level"),
            pl.col("logger").cast(pl.String),
            pl.col("message"),
            pl.col("exception_chain").list.eval(pl.struct(parts)).alias("chain"),
        )
        .collect()
    )


def _special_rows(entries_path: Path, banners: bool, titles: bool) -> tuple[list[int], list[int]]:
    """Rows of banners (:func:`~atlas_log_analyzer.core.parser.is_banner`) and of blank messages.

    polars picks the rows whose message has no ASCII letter or digit; Python's
    rules (the ones the parser and the signer apply) decide among them.
    """
    if not banners and not titles:
        return [], []
    df = (
        pl.scan_parquet(entries_path)
        .with_row_index("_row")
        .filter(~pl.col("message").fill_null("").str.contains(_ALNUM))
        .select(
            "_row",
            pl.col("message").fill_null(""),
            (pl.col("body").fill_null("") == "").alias("_no_body"),
            pl.col("parse_error").is_null().alias("_parsed"),
        )
        .collect()
    )
    banner_rows: list[int] = []
    blank_rows: list[int] = []
    for row, message, no_body, parsed in df.iter_rows():
        if not message or message.isspace():
            if titles:
                blank_rows.append(row)
        elif banners and parsed and no_body and is_banner_text(message):
            banner_rows.append(row)
    return banner_rows, blank_rows


def _rows_of(entries_path: Path, rows: list[int], column: str) -> dict[int, str]:
    """``{row: value}`` of one string column for the given row indices."""
    df = (
        pl.scan_parquet(entries_path)
        .with_row_index("_row")
        .filter(pl.col("_row").is_in(pl.Series(rows, dtype=pl.UInt32).implode()))
        .select("_row", pl.col(column).fill_null(""))
        .collect()
    )
    return dict(df.iter_rows())


def _lead_column(entries_path: Path, height: int, blank_rows: list[int]) -> pl.Series:
    """Lead line of the body for rows with a blank message, null elsewhere."""
    lead = pl.Series("lead", [None] * height, dtype=pl.String)
    if blank_rows:
        bodies = _rows_of(entries_path, blank_rows, "body")
        rows = sorted(bodies)
        lead = lead.scatter(rows, [lead_line(bodies[r]) for r in rows])
    return lead


def _banner_plan(
    entries_path: Path, banner_rows: list[int], window_ms: int, max_banners: int
) -> tuple[list[int], dict[int, str]]:
    """Banner rows to drop and the preamble of each entry they announce.

    The rule of :func:`~atlas_log_analyzer.core.parser.attach_banners` in file order (the
    stored order): a
    banner *links* to the next row of its file when that row has the same thread and
    logger and is at most ``window_ms`` later. A row starts a segment unless the row
    before it links to it; a segment that ends on a row which is no banner gives that
    row the banners before it (the last ``max_banners`` of them).
    """
    if not banner_rows:
        return [], {}
    is_banner = pl.col("_row").is_in(pl.Series(banner_rows, dtype=pl.UInt32).implode())
    link = (
        pl.col("_banner")
        & pl.col("source_path").shift(-1).eq_missing(pl.col("source_path"))
        & pl.col("thread").shift(-1).eq_missing(pl.col("thread"))
        & pl.col("logger").shift(-1).eq_missing(pl.col("logger"))
        & (pl.col("_ms").shift(-1) - pl.col("_ms")).is_between(0, window_ms)
    ).fill_null(False)
    segment = (~pl.col("_link").shift(1, fill_value=False)).cum_sum()
    # stored rows are in file order already: the files of a set are parsed one after another
    # (no sort, no string casts of the dictionary columns: a 745k-row set peaked at 800 MB)
    attached = (
        pl.scan_parquet(entries_path)
        .with_row_index("_row")
        .select(
            "_row",
            "source_path",
            pl.col("ts").dt.epoch("ms").alias("_ms"),
            "thread",
            "logger",
        )
        .with_columns(is_banner.alias("_banner"))
        .with_columns(link.alias("_link"))
        .with_columns(segment.alias("_seg"))
        .with_columns(
            pl.len().over("_seg").alias("_len"),
            pl.int_range(pl.len()).over("_seg").alias("_pos"),
            pl.col("_banner").last().over("_seg").alias("_ends_on_banner"),
            pl.col("_row").last().over("_seg").alias("_target"),
        )
        .filter(
            pl.col("_banner")
            & ~pl.col("_ends_on_banner")
            & (pl.col("_len") - 1 - pl.col("_pos") <= max_banners)
        )
        .select("_row", "_target")
        .collect()
    )
    if attached.height == 0:
        return [], {}
    rows = attached["_row"].to_list()
    raws = _rows_of(entries_path, rows, "raw")
    parts: dict[int, list[str]] = defaultdict(list)
    for row, target in attached.iter_rows():  # file order: a target's banners in order
        parts[target].append(raws[row])
    return sorted(rows), {target: "\n".join(texts) for target, texts in parts.items()}


def _sign_keys(keys: pl.DataFrame, recipe: Recipe, format_name: str) -> list[pa.Array]:
    """Sign each distinct key once; arrays of :data:`DERIVED_COLUMNS` in key order."""
    sign_both = recipe.signer().sign_both
    sigs: list[str] = []
    coarse: list[str] = []
    templates: list[str] = []
    variables: list[tuple[str, ...]] = []
    for level, logger, message, chain, lead in keys.iter_rows():
        exceptions: tuple[ExceptionInfo, ...] = ()
        if chain:
            exceptions = tuple(
                ExceptionInfo(
                    type=exc["type"],
                    message=exc["message"],
                    frames=tuple(Frame(loc) for loc in exc.get("frames") or ()),
                )
                for exc in chain
            )
        entry = LogEntry(
            id="",
            format=format_name,
            timestamp=_EPOCH,
            level=LogLevel(level),
            thread="",
            logger=logger or "",
            message=message or "",
            # the lead line is its own lead line: the signer's title of the entry
            body=lead or "",
            exceptions=exceptions,
        )
        full, rough = sign_both(entry)
        sigs.append(full.hash)
        coarse.append(rough.hash)
        templates.append(full.template)
        variables.append(full.variables)
    return [
        pa.array(sigs, pa.string()),
        pa.array(coarse, pa.string()),
        pa.array(templates, pa.string()),
        pa.array(variables, pa.list_(pa.string())),
    ]


def _with_preambles(column: Any, lo: int, preambles: dict[int, str]) -> pa.Array:
    """Return the ``context`` map column of one row group with ``preamble`` set on targets."""
    values = column.to_pylist()
    for row, text in preambles.items():
        i = row - lo
        values[i] = [kv for kv in values[i] or () if kv[0] != CTX_PREAMBLE] + [(CTX_PREAMBLE, text)]
    return pa.array(values, ENTRIES_SCHEMA.field("context").type)


def _rebuilt_entries(entries_path: Path, fmt: LogFormat) -> dict[int, LogEntry]:
    """Entries of the :data:`_REBUILD_HINT` rows built again from ``raw``, by row index.

    A row stored with ``parse_error`` (lines before a file's first header, an unrecognised
    header) is built as the parser builds it, without a header: only its exception
    chain is taken (:func:`_rebuilt_columns`).
    """
    df = (
        pl.scan_parquet(entries_path)
        .with_row_index("_row")
        .filter(_REBUILD_HINT)
        .select("_row", pl.col("raw").fill_null(""), pl.col("parse_error").is_null())
        .collect()
    )
    out: dict[int, LogEntry] = {}
    nowhere = SourceRef("", 0, 0)
    for row, raw, parsed in df.iter_rows():
        header_line, *continuation = raw.split("\n")
        header = fmt.parse_header(header_line) if parsed else None
        if header is not None or not parsed:
            out[row] = fmt.build(header, header_line, continuation, nowhere)
    return out


def _rebuilt_columns(table: pa.Table, lo: int, entries: dict[int, LogEntry]) -> dict[str, Any]:
    """Columns of one row group with the parsed fields of re-built entries replaced."""
    names = (*_REBUILT_COLUMNS, "context", "exception_type", "exception_message")
    values = {name: table.column(name).to_pylist() for name in names}
    chains = table.column("exception_chain").to_pylist()
    for row, e in entries.items():
        i = row - lo
        if e.parse_error is None:  # an unparsed row has no context to split again
            ctx = e.context
            for name, key in _REBUILT_COLUMNS.items():
                values[name][i] = ctx.get(key)
            kept = [kv for kv in values["context"][i] or () if kv[0] == CTX_PREAMBLE]
            values["context"][i] = [(k, v) for k, v in ctx.items() if k not in _WELL_KNOWN] + kept
        chain = e.exceptions
        values["exception_type"][i] = chain[0].type if chain else None
        values["exception_message"][i] = chain[0].message if chain else None
        chains[i] = [
            {
                "type": ex.type,
                "message": ex.message,
                "frames": [
                    {"location": f.location, "file": f.file, "line": f.line, "module": f.module}
                    for f in ex.frames
                ],
                "omitted_frames": ex.omitted_frames,
            }
            for ex in chain
        ]
    values["exception_chain"] = chains
    return {name: pa.array(v, ENTRIES_SCHEMA.field(name).type) for name, v in values.items()}


def _rebuild_file(src: Path, dst: Path, fmt: LogFormat) -> bool:
    """Write ``dst``: ``src`` with the :data:`_REBUILD_HINT` rows built again (``False``: none)."""
    entries = _rebuilt_entries(src, fmt)
    if not entries:
        return False
    rows = sorted(entries)
    read = 0
    with (
        pq.ParquetFile(str(src)) as pf,
        pq.ParquetWriter(
            str(dst),
            ENTRIES_SCHEMA,
            compression=COMPRESSION,
            write_statistics=True,
            **_entries_encodings(),
        ) as writer,
    ):
        for i in range(pf.num_row_groups):
            table = pf.read_row_group(i)
            lo, hi = read, read + table.num_rows
            read = hi
            here = {r: entries[r] for r in rows[bisect_left(rows, lo) : bisect_left(rows, hi)]}
            if here:
                for name, column in _rebuilt_columns(table, lo, here).items():
                    table = table.set_column(ENTRIES_SCHEMA.get_field_index(name), name, column)
            writer.write_table(table, row_group_size=max(table.num_rows, 1))
    return True


def resign_entries(
    src: Path,
    dst: Path,
    recipe: Recipe,
    format_name: str,
    *,
    reassemble: bool = False,
    attach: bool | None = None,
    max_banners: int = MAX_BANNERS,
) -> int:
    """Write ``dst`` = ``src`` with :data:`DERIVED_COLUMNS` recomputed by ``recipe``.

    Rows, row groups and every other column are kept as stored, except with
    ``reassemble`` and a format that attaches banners
    (:func:`~atlas_log_analyzer.core.formats.base.banner_window_ms`): attached banner
    rows are dropped and the rows they announce get ``context["preamble"]``, and the
    :data:`_REBUILD_HINT` rows are first built again from their ``raw`` text.

    Args:
        src: ``entries.parquet`` of the set.
        dst: Path of the rewritten file.
        recipe: Recipe to sign with (its spec decides how many frames the key holds).
        format_name: The set's format name (the first canonical part of a signature).
        reassemble: Assemble the entries as the recipe's format does today (for a set
            built by an older version of the format): rebuild the hinted rows from
            ``raw``, and attach banners unless ``attach`` says otherwise.
        attach: Attach banners (default: ``reassemble``); ``False`` for a set whose
            format version attached them already
            (:func:`~atlas_log_analyzer.core.formats.base.banners_since`).
        max_banners: Most banners attached to one entry (as ``attach_banners``).

    Returns:
        The number of rows written.

    """
    banners = reassemble if attach is None else attach
    rebuilt = dst.with_name(f"{dst.stem}.rebuilt{dst.suffix}")
    if reassemble and _rebuild_file(src, rebuilt, recipe.fmt):
        try:
            return _resign(rebuilt, dst, recipe, format_name, banners, max_banners)
        finally:
            rebuilt.unlink(missing_ok=True)
    return _resign(src, dst, recipe, format_name, banners, max_banners)


def _resign(
    src: Path, dst: Path, recipe: Recipe, format_name: str, attach: bool, max_banners: int
) -> int:
    import pyarrow.compute as pc

    window = banner_window_ms(recipe.fmt) if attach else 0
    banner_rows, blank_rows = _special_rows(src, window > 0, titles_from_body(recipe.fmt))
    drop, preambles = _banner_plan(src, banner_rows, window, max_banners) if window else ([], {})
    keys = _key_frame(src, recipe.spec.frames)
    keys = keys.with_columns(_lead_column(src, keys.height, blank_rows))
    distinct = (
        keys.with_row_index("_row")
        .group_by(list(_KEY_COLUMNS))
        .agg(pl.col("_row"))
        .with_row_index("_key")
    )
    # key index of every row, in file order
    row_keys = (
        distinct.select("_key", "_row").explode("_row").sort("_row")["_key"].to_arrow()
    ).cast(pa.int64())
    del keys
    derived = _sign_keys(distinct.select(_KEY_COLUMNS), recipe, format_name)
    del distinct
    positions = {name: ENTRIES_SCHEMA.get_field_index(name) for name in DERIVED_COLUMNS}
    context_at = ENTRIES_SCHEMA.get_field_index("context")
    dropped = pa.array(drop, pa.int64())
    targets = sorted(preambles)
    read = written = 0
    with (
        pq.ParquetFile(str(src)) as pf,
        pq.ParquetWriter(
            str(dst),
            ENTRIES_SCHEMA,
            compression=COMPRESSION,
            write_statistics=True,
            **_entries_encodings(),
        ) as writer,
    ):
        for i in range(pf.num_row_groups):
            table = pf.read_row_group(i)
            n = table.num_rows
            lo, hi = read, read + n
            take = row_keys.slice(lo, n)
            columns: list[Any] = list(table.columns)
            for name, values in zip(DERIVED_COLUMNS, derived, strict=True):
                columns[positions[name]] = pc.take(values, take)
            first, last = bisect_left(targets, lo), bisect_left(targets, hi)
            here = {t: preambles[t] for t in targets[first:last]}
            if here:
                columns[context_at] = _with_preambles(table.column(context_at), lo, here)
            batch = pa.Table.from_arrays(columns, schema=ENTRIES_SCHEMA)
            if len(dropped):
                keep = pc.invert(pc.is_in(pa.array(range(lo, hi), pa.int64()), value_set=dropped))
                batch = batch.filter(keep)
            read = hi
            if batch.num_rows or n == 0:
                writer.write_table(batch, row_group_size=max(batch.num_rows, 1))
            written += batch.num_rows
    return written
