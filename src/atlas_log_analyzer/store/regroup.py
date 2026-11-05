"""Re-signing stored entries column-wise, for ``regroup`` and ``upgrade``.

Signing depends only on a few columns (level, logger, message and the exception
chain), and a set has far fewer distinct combinations of them than entries
(105,883 for 712,673 Jira entries). :func:`resign_entries` selects those key
columns with polars, signs each distinct key once, and rewrites
``entries.parquet`` row group by row group with the derived columns replaced;
every other column is passed through as stored. The result equals re-signing
:class:`~atlas_log_analyzer.core.models.LogEntry` objects rebuilt from every row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from atlas_log_analyzer.core.models import ExceptionInfo, Frame, LogEntry, LogLevel
from atlas_log_analyzer.core.pipeline import Recipe
from atlas_log_analyzer.store.schema import ENTRIES_SCHEMA
from atlas_log_analyzer.store.writer import COMPRESSION, _entries_encodings

__all__ = ["DERIVED_COLUMNS", "resign_entries"]

DERIVED_COLUMNS = ("signature", "coarse_signature", "template", "variables")
"""Entry columns computed by the signer; :func:`resign_entries` replaces exactly these."""

_KEY_COLUMNS = ("level", "logger", "message", "chain")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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


def _sign_keys(keys: pl.DataFrame, recipe: Recipe, format_name: str) -> list[pa.Array]:
    """Sign each distinct key once; arrays of :data:`DERIVED_COLUMNS` in key order."""
    sign_both = recipe.signer().sign_both
    sigs: list[str] = []
    coarse: list[str] = []
    templates: list[str] = []
    variables: list[tuple[str, ...]] = []
    for level, logger, message, chain in keys.iter_rows():
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


def resign_entries(src: Path, dst: Path, recipe: Recipe, format_name: str) -> int:
    """Write ``dst`` = ``src`` with :data:`DERIVED_COLUMNS` recomputed by ``recipe``.

    Rows, row groups and every other column are kept as stored.

    Args:
        src: ``entries.parquet`` of the set.
        dst: Path of the rewritten file.
        recipe: Recipe to sign with (its spec decides how many frames the key holds).
        format_name: The set's format name (the first canonical part of a signature).

    Returns:
        The number of rows written.

    """
    import pyarrow.compute as pc

    keys = _key_frame(src, recipe.spec.frames)
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
    written = 0
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
            take = row_keys.slice(written, n)
            columns: list[Any] = list(table.columns)
            for name, values in zip(DERIVED_COLUMNS, derived, strict=True):
                columns[positions[name]] = pc.take(values, take)
            batch = pa.Table.from_arrays(columns, schema=ENTRIES_SCHEMA)
            writer.write_table(batch, row_group_size=max(n, 1))
            written += n
    return written
