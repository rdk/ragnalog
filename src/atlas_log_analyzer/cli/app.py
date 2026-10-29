"""The ``atlas-log`` command line application.

Each command is a thin wrapper over :mod:`atlas_log_analyzer.core` and
:mod:`atlas_log_analyzer.store`; rendering and exit codes live in
:mod:`atlas_log_analyzer.cli.output`. Global options (``--output``, ``--store``,
``--quiet``) are parsed by the meta app and may appear anywhere on the line.
"""

from __future__ import annotations

import dataclasses
import importlib
import io
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Annotated, Any, Literal

from cyclopts import App, CycloptsError, Group, Parameter
from rich.console import Console

from atlas_log_analyzer import __version__
from atlas_log_analyzer.cli.output import (
    CliError,
    ExitCode,
    Output,
    OutputMode,
    default_mode,
    encode_json,
    entry_to_dict,
    error_from_exception,
    jsonable_rows,
    rows_table,
)
from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.formats.registry import UnknownFormatError
from atlas_log_analyzer.core.grouping import Grouper, SignedEntry
from atlas_log_analyzer.core.masking import GENERIC_RULES, Masker
from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.core.parser import parse_sources
from atlas_log_analyzer.core.reader import SourceFile, iter_lines, iter_sources
from atlas_log_analyzer.core.signature import DEFAULT_SPEC, SignatureSpec, Signer
from atlas_log_analyzer.store import LogSet, Store

__all__ = ["app", "main", "run"]

DEFAULT_STORE = Path(".atlas-log")
STORE_ENV = "ATLAS_LOG_STORE"
SNIFF_LINES = 200
"""Number of leading lines of the first source used for format detection."""

LEVEL_NAMES = tuple(level.name for level in LogLevel)
SCHEMA_NAMES = ("entries", "groups", "merges", "logset")

GROUP_COLUMNS = (
    "signature",
    "count",
    "level_name",
    "logger",
    "exception_type",
    "template",
    "first_seen",
    "last_seen",
)
ENTRY_COLUMNS = ("ts", "level_name", "thread", "user", "logger", "message", "variables")
SET_COLUMNS = (
    "id",
    "name",
    "format",
    "entry_count",
    "group_count",
    "first_ts",
    "last_ts",
    "created_at",
)
DIFF_COLUMNS = (
    "signature",
    "count_a",
    "count_b",
    "ratio",
    "level_name",
    "logger",
    "exception_type",
    "template",
)

GLOBAL = Group("Global options", sort_key=100)

app = App(
    name="atlas-log",
    help=(
        "Parse, group and compare application log files (Jira Data Center first).\n\n"
        "Typical flow: `ingest` log files into a log set, list `groups` (one row per\n"
        "message signature), `show` the entries of one group, `diff` two sets.\n"
        "Output is a table on a terminal and JSON otherwise (--output json|jsonl|table).\n"
        'Every JSON document has a "schema" key; listings put rows under "items".\n'
        'Errors in json/jsonl mode: {"schema": "error.v1", "error": {"code", "message"}}\n'
        "on stdout. Exit codes: 0 ok, 1 usage error, 2 nothing found / parse failure,\n"
        "3 store error. SET arguments accept a log-set id, a unique id prefix or a name."
    ),
    version=__version__,
    default_parameter=Parameter(negative=()),
    help_format="plaintext",
)


@dataclasses.dataclass
class _Context:
    output: Output
    store: Path


_CTX: _Context | None = None


def _ctx() -> _Context:
    if _CTX is None:  # only when a command function is called outside the CLI
        return _Context(Output(default_mode()), Path(os.environ.get(STORE_ENV, DEFAULT_STORE)))
    return _CTX


def _out() -> Output:
    return _ctx().output


# --- helpers


def _store() -> Store:
    return Store(_ctx().store)


def _open(ref: str) -> LogSet:
    return _store().open(ref)


def _level(value: str | None) -> str | None:
    if value is None:
        return None
    names = []
    for part in value.split(","):
        name = part.strip().upper()
        name = "WARN" if name == "WARNING" else name
        if name not in LEVEL_NAMES:
            raise CliError(
                "invalid_level",
                f"unknown level {part!r}; expected one of {', '.join(LEVEL_NAMES)}",
            )
        names.append(name)
    return ",".join(names)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _limit(value: int) -> int | None:
    if value < 0:
        raise CliError("invalid_value", "--limit must be >= 0 (0 = no limit)")
    return value or None


def _sources(paths: Sequence[Path]) -> list[SourceFile]:
    if not paths:
        raise CliError("usage", "at least one PATH is required")
    sources = iter_sources(paths)
    if not sources:
        shown = ", ".join(str(p) for p in paths)
        raise CliError("no_sources", f"no log files found in {shown}", ExitCode.NOT_FOUND)
    return sources


def _resolve_format(name: str | None, sources: Sequence[SourceFile]) -> LogFormat:
    if name is not None:
        return formats.get(name)
    first = [line.text for line in islice(iter_lines(sources[0].path), SNIFF_LINES)]
    fmt = formats.detect(first)
    if fmt is None:
        raise CliError(
            "format_not_detected",
            f"could not detect the log format of {sources[0].path}; pass --format "
            "(see `atlas-log formats`)",
            ExitCode.NOT_FOUND,
        )
    return fmt


def _manifest_dict(logset: LogSet) -> dict[str, Any]:
    data: dict[str, Any] = dict(logset.manifest.to_dict())
    data.pop("schema", None)
    return data


def _format_spec(format_name: str) -> SignatureSpec:
    try:
        spec: SignatureSpec = formats.get(format_name).signature_spec
    except UnknownFormatError:
        spec = DEFAULT_SPEC
    return spec


@dataclasses.dataclass
class _Counter:
    entries: int = 0
    parse_errors: int = 0


def _counted(
    signed: Iterable[SignedEntry], counter: _Counter, progress: Callable[[int], None] | None
) -> Iterator[SignedEntry]:
    for item in signed:
        counter.entries += 1
        if item.entry.parse_error is not None:
            counter.parse_errors += 1
        if progress is not None and counter.entries % 100_000 == 0:
            progress(counter.entries)
        yield item


# --- meta app


@app.meta.default
def _meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    output: Annotated[OutputMode | None, Parameter(group=GLOBAL)] = None,
    store: Annotated[Path, Parameter(group=GLOBAL, env_var=STORE_ENV)] = DEFAULT_STORE,
    quiet: Annotated[bool, Parameter(group=GLOBAL, negative="")] = False,
) -> int:
    """Run a command with global options applied.

    Args:
        tokens: Command and its arguments.
        output: Output mode. Default: table when stdout is a terminal, json otherwise.
        store: Store directory holding log sets (env ATLAS_LOG_STORE; default ./.atlas-log).
        quiet: Suppress progress and diagnostics on stderr (warnings and errors still shown).

    """
    global _CTX
    out = Output(output or default_mode(), quiet=quiet)
    _CTX = _Context(out, store)
    try:
        result = app(tokens, exit_on_error=False, print_error=False, result_action="return_value")
    except Exception as exc:
        err = error_from_exception(exc)
        if err is None:
            raise
        return out.error(err)
    return int(result) if isinstance(result, int) else 0


# --- commands


@app.command
def ingest(
    *paths: Path,
    format: str | None = None,
    name: str | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
) -> int:
    """Parse log files into a stored log set and print its manifest.

    PATHs are files or directories (directories are scanned non-recursively for
    *.log*; rotated files x.log.N are read oldest first). The log-set id is
    derived from the file digests, so ingesting the same files again is a no-op
    that prints the existing manifest (use --overwrite to rebuild it).
    Progress goes to stderr.

    Output (json): the manifest, schema "logset.v1":
      {"schema": "logset.v1", "id", "name", "format",
       "sources": [{"path", "size", "xxh3", "first_ts", "last_ts", "entries"}],
       "first_ts", "last_ts", "entry_count", "group_count", "created_at",
       "tool_version", "schema_version", "signature_version",
       "mask_rules_version", "signature_spec",
       "ingest": {"parsed_entries", "parse_errors", "elapsed_s", "existing"},
       "merges": [{"cluster_id", "signatures": [str], "drain_template"}]}
    "merges" is present only with --mine; the proposals are also stored in
    merges.parquet (the "merges" view of `query`).
    "ingest.existing" is true when the set already existed and was reused.

    Exit codes: 0 ok; 1 unknown --format or bad option; 2 no files found, format
    not detected, or no entries parsed (manifest still printed); 3 store error.

    Args:
        paths: Log files and/or directories.
        format: Format name (see `formats`); default: detected from the first 200 lines.
        name: Human-readable alias for the set, usable wherever SET is expected.
        frames: Add the top N stack frames to signatures (finer groups); default 0.
        overwrite: Rebuild the set if one with the same id already exists.
        mine: Propose group merges with Drain3 (needs extra "mine": uv sync --extra mine).

    """
    out = _out()
    if frames is not None and frames < 0:
        raise CliError("invalid_value", "--frames must be >= 0")
    sources = _sources(paths)
    fmt = _resolve_format(format, sources)
    spec = fmt.signature_spec
    if frames is not None:
        spec = dataclasses.replace(spec, frames=frames)
    grouper = Grouper(Signer(Masker([*fmt.mask_rules, *GENERIC_RULES]), spec))
    counter = _Counter()
    total = sum(s.size for s in sources)
    out.info(f"ingesting {len(sources)} file(s), {total / 1e6:.1f} MB, format {fmt.name}")
    started = time.perf_counter()

    def progress(n: int) -> None:
        out.info(f"  {n:,} entries")

    signed = _counted(grouper.sign(parse_sources(sources, fmt)), counter, progress)
    logset = _store().ingest(
        signed, grouper.sorted_groups, sources, fmt, name=name, spec=spec, overwrite=overwrite
    )
    elapsed = time.perf_counter() - started
    doc = _manifest_dict(logset)
    doc["ingest"] = {
        "parsed_entries": counter.entries,
        "parse_errors": counter.parse_errors,
        "elapsed_s": round(elapsed, 3),
        "existing": counter.entries == 0 and logset.manifest.entry_count > 0,
    }
    if doc["ingest"]["existing"]:
        out.info(f"log set {logset.manifest.id} already exists; use --overwrite to rebuild it")
    else:
        out.info(
            f"ingested {counter.entries:,} entries into {logset.manifest.id} "
            f"({logset.manifest.group_count:,} groups) in {elapsed:.1f} s"
        )
    if counter.parse_errors:
        out.warn(f"{counter.parse_errors:,} entries could not be parsed (parse_error is set)")
    if mine:
        doc["merges"] = _mine(logset)
    out.emit("logset.v1", doc, items=None, title=f"log set {logset.manifest.id}")
    if logset.manifest.entry_count == 0:
        out.warn("no entries were parsed")
        return int(ExitCode.NOT_FOUND)
    return 0


def _mine(logset: LogSet) -> list[dict[str, Any]]:
    from atlas_log_analyzer.store.writer import write_merges

    try:
        mining = importlib.import_module("atlas_log_analyzer.core.mining")
        pairs = list(logset.groups().select("signature", "template").iter_rows())
        proposals = list(mining.propose_merges(pairs))
    except ImportError as exc:
        raise CliError(
            "missing_dependency",
            f"--mine needs the optional 'mine' extra (drain3): {exc}. "
            "Install it with `uv sync --extra mine`.",
        ) from exc
    write_merges(logset.merges_path, proposals)
    _out().info(f"{len(proposals)} merge proposal(s) written to merges.parquet")
    return [
        {
            "cluster_id": p.cluster_id,
            "signatures": list(p.signatures),
            "drain_template": p.drain_template,
        }
        for p in proposals
    ]


@app.command
def sets() -> None:
    """List the log sets in the store, newest first.

    Output (json):
      {"schema": "sets.v1", "store": str, "count": int,
       "items": [<logset.v1 manifest without "schema">, ...]}
    Each item has id, name, format, sources, first_ts, last_ts, entry_count,
    group_count, created_at, tool_version, schema_version, signature_version,
    mask_rules_version, signature_spec. An empty or missing store gives count 0.

    Exit codes: 0 ok (also when empty); 3 store error.
    """
    manifests = _store().list()
    items = []
    for m in manifests:
        d = dict(m.to_dict())
        d.pop("schema", None)
        items.append(d)
    _out().emit(
        "sets.v1",
        {"store": _ctx().store.as_posix(), "count": len(items), "items": items},
        columns=SET_COLUMNS,
        title="log sets",
    )


@app.command
def groups(
    logset: str,
    /,
    *,
    level: str | None = None,
    min_level: str | None = None,
    logger: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sort: Literal["count", "last_seen", "first_seen", "signature", "template"] = "count",
    limit: int = 50,
    coarse: bool = False,
) -> None:
    """List the groups (one per message signature) of a log set.

    A group collects all entries whose level, logger, masked message and
    exception chain are equal; its "signature" (16 hex chars) is stable across
    sets, so it can be used with `show`, `entries --signature` and `diff`.

    Output (json):
      {"schema": "groups.v1", "logset": id, "count": int, "items": [
        {"signature", "coarse_signature", "template", "level", "logger",
         "level_name", "exception_type", "count", "first_seen", "last_seen",
         ...}, ...]}
    "level" is numeric (ERROR=50), "level_name" the name. "items" holds the
    groups.v1 columns (`atlas-log schema groups`: also user_count, thread_count,
    source_count, top_users, top_threads, sources, examples), at most --limit
    rows. With --coarse, "signature" is the coarse signature and "signatures"
    lists the member signatures.

    Exit codes: 0 ok (also when no group matches); 1 bad option; 3 unknown set
    or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        level: Only these levels, e.g. ERROR or WARN,ERROR (TRACE DEBUG INFO WARN ERROR FATAL).
        min_level: Only this level or more severe, e.g. WARN.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        since: Only groups last seen at/after this ISO 8601 time (naive = UTC).
        until: Only groups first seen before this ISO 8601 time (naive = UTC).
        sort: count, last_seen (newest first), first_seen (oldest first), signature, template.
        limit: Maximum number of rows; 0 = all.
        coarse: Group by coarse signature (level + logger + masked message; no exceptions).

    """
    lvl, min_lvl, lim = _level(level), _level(min_level), _limit(limit)
    ls = _open(logset)
    df = ls.groups(
        level=lvl,
        min_level=min_lvl,
        logger=logger,
        since=_aware(since),
        until=_aware(until),
        sort=sort,
        limit=lim,
        coarse=coarse,
    )
    items = jsonable_rows(df)
    _out().emit(
        "groups.v1",
        {"logset": ls.manifest.id, "count": len(items), "items": items},
        columns=GROUP_COLUMNS,
        title=f"groups of {ls.manifest.id}",
    )


@app.command
def show(logset: str, signature: str, /, *, limit: int = 20, raw: bool = False) -> int:
    """Show the entries of one group, with the variables extracted from each message.

    Output (json):
      {"schema": "entries.v1", "logset": id, "signature": str,
       "group": {<groups.v1 row>} | null, "count": int,
       "items": [{<entries.v1 row: id, ts, level, level_name, thread, logger,
                  message, body, user, ..., exception_type, signature,
                  template, variables, source_path, source_line, raw, ...>}, ...]}
    "variables" lists the values that masking replaced in the template, in
    order (issue keys, numbers, ids ...). Entries are ordered by time.
    With --raw the original log text of each entry is printed instead (any
    --output mode), entries separated by a blank line.

    Exit codes: 0 ok; 2 no entries with this signature; 3 unknown set or store
    error.

    Args:
        logset: Log set id, unique id prefix or name.
        signature: Group signature (from `groups`).
        limit: Maximum number of entries; 0 = all.
        raw: Print the raw log text of the entries.

    """
    lim = _limit(limit)
    ls = _open(logset)
    df = ls.entries(signature=signature, limit=lim)
    if df.height == 0:
        raise CliError(
            "not_found",
            f"no entries with signature {signature!r} in log set {ls.manifest.id}",
            ExitCode.NOT_FOUND,
        )
    out = _out()
    if raw:
        out.out.write("\n\n".join(str(r) for r in df.get_column("raw").to_list()) + "\n")
        return 0
    import polars as pl

    group_rows = jsonable_rows(ls.groups_lazy().filter(pl.col("signature") == signature).collect())
    items = jsonable_rows(df)
    out.emit(
        "entries.v1",
        {
            "logset": ls.manifest.id,
            "signature": signature,
            "group": group_rows[0] if group_rows else None,
            "count": len(items),
            "items": items,
        },
        columns=ENTRY_COLUMNS,
        title=f"entries of {signature}",
    )
    return 0


@app.command
def entries(
    logset: str,
    /,
    *,
    signature: str | None = None,
    level: str | None = None,
    min_level: str | None = None,
    logger: str | None = None,
    user: str | None = None,
    thread: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    contains: str | None = None,
    columns: list[str] | None = None,
    limit: int = 100,
) -> None:
    """List entries of a log set, filtered, ordered by time.

    Output (json):
      {"schema": "entries.v1", "logset": id, "count": int, "items": [<entries.v1 row>, ...]}
    Rows hold all entries.v1 columns (`atlas-log schema entries`) unless
    --columns is given: id, ts (UTC), tz_offset, level (numeric), level_name,
    thread, logger, message, body, user, request_id, session, ip, url,
    request_url, context, exception_type, exception_message, exception_chain,
    signature, coarse_signature, template, variables, source_path, source_line,
    source_offset, raw, parse_error.

    Exit codes: 0 ok (also when nothing matches); 1 bad option; 3 unknown set
    or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        signature: Only entries of this group signature.
        level: Only these levels, e.g. ERROR or WARN,ERROR.
        min_level: Only this level or more severe.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        user: Only this user (exact).
        thread: Only this thread (exact).
        since: Only entries at/after this ISO 8601 time (naive = UTC).
        until: Only entries before this ISO 8601 time (naive = UTC).
        contains: Only entries whose raw text contains this text (case-insensitive).
        columns: Columns to return (repeat the option or separate by spaces).
        limit: Maximum number of rows; 0 = all.

    """
    lvl, min_lvl, lim = _level(level), _level(min_level), _limit(limit)
    ls = _open(logset)
    df = ls.entries(
        signature=signature,
        level=lvl,
        min_level=min_lvl,
        logger=logger,
        user=user,
        thread=thread,
        since=_aware(since),
        until=_aware(until),
        contains=contains,
        limit=lim,
        columns=columns,
    )
    items = jsonable_rows(df)
    _out().emit(
        "entries.v1",
        {"logset": ls.manifest.id, "count": len(items), "items": items},
        columns=columns or ENTRY_COLUMNS,
        title=f"entries of {ls.manifest.id}",
    )


@app.command
def query(logset: str, sql: str, /) -> None:
    """Run DuckDB SQL over a log set and print the result rows.

    Views: "entries" (entries.v1), "groups" (groups.v1) and "merges" (merges.v1,
    only when present). See `atlas-log schema entries|groups|merges` for columns.

    SQL examples:
      select level, count(*) n from entries group by 1 order by 2 desc
      select unnest(variables) v, count(*) n from entries
        where signature = '0123456789abcdef' group by 1 order by 2 desc
      select signature, count, template from groups where exception_type is not null
        order by count desc limit 20

    Output (json):
      {"schema": "query.v1", "logset": id, "columns": [str, ...], "count": int,
       "items": [{<column>: value, ...}, ...]}

    Exit codes: 0 ok (also for an empty result); 1 SQL error; 3 unknown set or
    store error.

    Args:
        logset: Log set id, unique id prefix or name.
        sql: The SQL query (quote it as one argument).

    """
    import duckdb
    import polars as pl

    ls = _open(logset)
    try:
        df = ls.sql(sql)
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        raise CliError("sql_error", str(exc).strip()) from exc
    items = jsonable_rows(df)
    _out().emit(
        "query.v1",
        {
            "logset": ls.manifest.id,
            "columns": list(df.columns),
            "count": len(items),
            "items": items,
        },
        title="query result",
    )


@app.command
def stats(logset: str, /) -> None:
    """Print summary statistics of a log set.

    Counts per level, per logger (top N) and per hour, the time range and the
    sources, as computed by the store.

    Output (json): {"schema": "stats.v1", "logset": id, <store statistics...>}

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    ls = _open(logset)
    data = dict(ls.stats())
    data.pop("schema", None)
    _out().emit(
        "stats.v1", {"logset": ls.manifest.id, **data}, items=None, title=f"stats {ls.manifest.id}"
    )


@app.command
def diff(
    set_a: str,
    set_b: str,
    /,
    *,
    min_level: Annotated[str | None, Parameter(name=["--min-level", "--level"])] = None,
    coarse: bool = False,
    force: bool = False,
    limit: int = 0,
) -> None:
    """Compare the groups of two log sets (A = before, B = after).

    "new": signatures present only in B; "vanished": only in A; "changed": in
    both with a different count (ratio = count_b / count_a). Only the stored
    group tables are compared, never the raw logs.

    Output (json):
      {"schema": "diff.v1", "a": id_a, "b": id_b,
       "new": [row], "vanished": [row], "changed": [row], "unchanged_count": int}
    Each row has signature, template, level, level_name, logger,
    exception_type, count_a, count_b, first_seen_a, last_seen_a, first_seen_b,
    last_seen_b (counts are 0 on the missing side); "changed" rows also have
    ratio. "new" is sorted by count_b, "vanished" by count_a, "changed" by
    |log(ratio)|, all descending. In jsonl mode one row per
    line with an extra "change" key (new / vanished / changed).

    Exit codes: 0 ok; 1 the sets were built with different signature/mask-rule
    versions or specs (use --force); 3 unknown set or store error.

    Args:
        set_a: Baseline log set (id, id prefix or name).
        set_b: Log set to compare against the baseline.
        min_level: Only groups of this level or more severe (e.g. ERROR).
        coarse: Compare by coarse signature (exception chain ignored).
        force: Compare even if signature versions or specs differ.
        limit: Maximum rows per section; 0 = all.

    """
    from atlas_log_analyzer.store import diff as store_diff

    min_lvl, lim = _level(min_level), _limit(limit)
    a, b = _open(set_a), _open(set_b)
    result = store_diff(a, b, min_level=min_lvl, coarse=coarse, force=force)
    sections = {
        name: jsonable_rows(frame if lim is None else frame.head(lim))
        for name, frame in (
            ("new", result.new),
            ("vanished", result.vanished),
            ("changed", result.changed),
        )
    }
    doc = {"a": result.a, "b": result.b, **sections, "unchanged_count": result.unchanged_count}
    jsonl = [{"change": name, **row} for name, rows in sections.items() for row in rows]

    def render(console: Console) -> None:
        for name, rows in sections.items():
            cols = [c for c in DIFF_COLUMNS if not rows or c in rows[0]]
            console.print(rows_table(rows, cols, title=f"{name} ({len(rows)})"))
        console.print(f"unchanged: {result.unchanged_count}")

    _out().emit("diff.v1", doc, items=None, jsonl_rows=jsonl, table=render)


def _report_filename(logset: LogSet) -> Path:
    stem = re.sub(r"[^\w.-]+", "_", logset.manifest.name or logset.manifest.id).strip("._")
    return Path((stem or logset.manifest.id) + ".html")


@app.command
def report(
    logset: str,
    /,
    *,
    out: Path | None = None,
    compare: str | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int = 0,
    open_: Annotated[bool, Parameter(name="--open")] = False,
) -> None:
    """Write a self-contained HTML report of a log set (optionally compared with another).

    The page needs no network access (CSS, JS and data are inlined) and works
    from a file share or an e-mail attachment: overview tiles, a per-hour
    timeline by level (drag to select a time window), the filterable group
    table and a detail drawer per group with example entries and the
    equivalent CLI commands. The embedded data is the "report.v1" payload
    (docs/formats/report.v1.md). With --compare SET_B the report shows the
    groups of SET relative to SET_B (SET_B = baseline, as `diff SET_B SET`).

    Output (json):
      {"schema": "report-file.v1", "path": str, "bytes": int, "groups": int,
       "entries": int, "compare": bool}
    "groups" is the number of groups in the report, "entries" the number of
    embedded example entries. Table mode prints one line.

    Exit codes: 0 ok; 1 bad option, invalid level, or the sets were built with
    different signature/mask-rule versions or specs; 2 the output directory
    does not exist; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        out: Output file; default <name or id>.html in the current directory.
        compare: Baseline log set to compare with (id, id prefix or name).
        min_level: Only groups of this level or more severe (e.g. WARN); the
            overview always covers the whole set.
        examples: Example entries per group; 0 = none.
        max_raw: Truncate each example's raw text to this many characters; 0 = no limit.
        max_groups: Only the N largest groups; 0 = all.
        open_: Open the report in the default web browser.

    """
    from atlas_log_analyzer.report import write_report

    min_lvl = _level(min_level)
    for flag, value in (
        ("--examples", examples),
        ("--max-raw", max_raw),
        ("--max-groups", max_groups),
    ):
        if value < 0:
            raise CliError("invalid_value", f"{flag} must be >= 0")
    ls = _open(logset)
    baseline = _open(compare) if compare is not None else None
    path = out if out is not None else _report_filename(ls)
    info = write_report(
        ls,
        path,
        compare=baseline,
        min_level=min_lvl,
        examples=examples,
        max_raw=max_raw,
        max_groups=max_groups or None,
    )
    resolved = info.path.resolve()
    doc = {
        "path": resolved.as_posix(),
        "bytes": info.bytes,
        "groups": info.groups,
        "entries": info.entries,
        "compare": info.compare,
    }
    output = _out()

    def render(console: Console) -> None:
        extra = f", compared with {compare}" if compare is not None else ""
        output.write_line(
            f"wrote {resolved} ({info.bytes:,} bytes, {info.groups:,} groups, "
            f"{info.entries:,} example entries{extra})"
        )

    output.emit("report-file.v1", doc, items=None, table=render)
    if open_:
        import webbrowser

        webbrowser.open(resolved.as_uri())


@app.command
def parse(*paths: Path, format: str | None = None, limit: int = 0) -> int:
    """Parse log files and stream the entries as JSON Lines to stdout (no store).

    Always writes JSONL, one entry per line, regardless of --output:
      {"id", "format", "timestamp" (ISO 8601 with offset), "level" (name),
       "thread", "logger", "message", "body", "exception_type",
       "exceptions": [{"type", "message", "frames": [{"location", "file",
                       "line", "module"}], "omitted_frames"}],
       "context": {"user", "request_id", "session", "ip", "url", "request_url", ...},
       "source": {"path", "line", "offset"}, "raw", "parse_error"}
    Entries whose header could not be parsed have "parse_error" set.

    Exit codes: 0 ok; 1 unknown --format; 2 no files, format not detected or no
    entries.

    Args:
        paths: Log files and/or directories.
        format: Format name; detected from the first 200 lines when omitted.
        limit: Stop after this many entries; 0 = all.

    """
    import msgspec

    lim = _limit(limit)
    sources = _sources(paths)
    fmt = _resolve_format(format, sources)
    encoder = msgspec.json.Encoder()
    stream = _out().out
    count = 0
    batch: list[bytes] = []
    entries_iter: Iterable[Any] = parse_sources(sources, fmt)
    if lim is not None:
        entries_iter = islice(entries_iter, lim)
    for entry in entries_iter:
        batch.append(encoder.encode(entry_to_dict(entry)))
        count += 1
        if len(batch) >= 1000:
            stream.write(b"\n".join(batch).decode("utf-8") + "\n")
            batch.clear()
    if batch:
        stream.write(b"\n".join(batch).decode("utf-8") + "\n")
    stream.flush()
    if count == 0:
        raise CliError("no_entries", "no entries were parsed", ExitCode.NOT_FOUND)
    _out().info(f"parsed {count:,} entries")
    return 0


@app.command
def regroup(logset: str, /, *, frames: int = 0, no_exceptions: bool = False) -> None:
    """Recompute the groups of a stored log set with another signature spec.

    Rewrites groups.parquet (entries are kept); signatures change, so diffs
    against sets built with another spec need --force. Prints the updated
    manifest.

    Output (json): the manifest, {"schema": "logset.v1", ...} (see `ingest`).

    Exit codes: 0 ok; 1 bad option; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        frames: Include the top N stack frames of each exception in the signature.
        no_exceptions: Leave the exception chain out of the signature (like --coarse).

    """
    if frames < 0:
        raise CliError("invalid_value", "--frames must be >= 0")
    store = _store()
    ls = store.open(logset)
    spec = dataclasses.replace(
        _format_spec(ls.manifest.format), frames=frames, include_exceptions=not no_exceptions
    )
    updated = store.regroup(ls.manifest.id, spec)
    _out().emit("logset.v1", _manifest_dict(updated), items=None, title=f"log set {ls.manifest.id}")


@app.command(name="formats")
def formats_(*, sample: Path | None = None) -> None:
    """List the supported log formats.

    Output (json):
      {"schema": "formats.v1", "count": int,
       "items": [{"name", "description", "signature_spec", "mask_rules": [str]}]}
    With --sample FILE each item also has "confidence" (0..1) from sniffing the
    first 200 lines of FILE, and items are sorted by it.

    Exit codes: 0 ok; 2 sample file not found.

    Args:
        sample: A log file to test every format against.

    """
    lines: list[str] | None = None
    if sample is not None:
        lines = [line.text for line in islice(iter_lines(sample), SNIFF_LINES)]
    items: list[dict[str, Any]] = []
    for fmt_name in formats.names():
        fmt = formats.get(fmt_name)
        item: dict[str, Any] = {
            "name": fmt.name,
            "description": getattr(fmt, "description", ""),
            "signature_spec": fmt.signature_spec.key,
            "mask_rules": [rule.name for rule in fmt.mask_rules],
        }
        if lines is not None:
            item["confidence"] = round(float(fmt.sniff(lines)), 3)
        items.append(item)
    if lines is not None:
        items.sort(key=lambda i: -float(i["confidence"]))
    _out().emit(
        "formats.v1",
        {"count": len(items), "items": items},
        columns=("name", "description", "confidence", "signature_spec"),
        title="formats",
    )


@app.command
def schema(name: Literal["entries", "groups", "merges", "logset"] | None = None, /) -> None:
    """Print the JSON description of the stored data schemas (for agents).

    "entries", "groups" and "merges" are the Parquet tables (also the views
    available to `query`); "logset" is manifest.json. Without NAME all four
    are printed.

    Output (json):
      {"schema": "schema.v1", "definitions": {<name>: <schema description>, ...}}
    In table mode the same document is pretty-printed as JSON.

    Exit codes: 0 ok; 1 unknown NAME.

    Args:
        name: Which schema to print.

    """
    from atlas_log_analyzer.store.schema import schema_json

    names = (name,) if name is not None else SCHEMA_NAMES
    doc = {"definitions": {n: schema_json(n) for n in names}}
    out = _out()
    if out.mode == "table":
        out.write_line(encode_json({"schema": "schema.v1", **doc}, indent=2))
        return
    out.emit("schema.v1", doc, items=None)


@app.command
def remove(logset: str, /) -> None:
    """Delete a log set from the store (its directory and all files).

    Output (json): {"schema": "remove.v1", "removed": id, "name": str | null}

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    store = _store()
    ls = store.open(logset)
    store.remove(ls.manifest.id)
    _out().info(f"removed log set {ls.manifest.id}")
    _out().emit("remove.v1", {"removed": ls.manifest.id, "name": ls.manifest.name}, items=None)


@app.command
def version() -> None:
    """Print tool, schema and signature versions.

    Output (json):
      {"schema": "version.v1", "version": str, "schema_version": str,
       "signature_version": int, "mask_rules_version": int, "python": str}

    Exit codes: 0 ok.
    """
    from atlas_log_analyzer.core.masking import MASK_RULES_VERSION
    from atlas_log_analyzer.core.signature import SIGNATURE_VERSION
    from atlas_log_analyzer.store.schema import SCHEMA_VERSION

    _out().emit(
        "version.v1",
        {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "signature_version": SIGNATURE_VERSION,
            "mask_rules_version": MASK_RULES_VERSION,
            "python": sys.version.split()[0],
        },
        items=None,
        title="atlas-log",
    )


# --- entry points


def _sniff_mode(tokens: Sequence[str]) -> OutputMode:
    for i, tok in enumerate(tokens):
        value: str | None = None
        if tok == "--output" and i + 1 < len(tokens):
            value = tokens[i + 1]
        elif tok.startswith("--output="):
            value = tok.partition("=")[2]
        if value in ("table", "json", "jsonl"):
            return value  # type: ignore[return-value]
    return default_mode()


def run(argv: Sequence[str] | None = None) -> int:
    """Run the CLI with ``argv`` (default: ``sys.argv[1:]``) and return the exit code.

    Known errors are reported and mapped to exit codes; unexpected exceptions propagate.
    """
    global _CTX
    tokens = list(sys.argv[1:] if argv is None else argv)
    _CTX = None
    try:
        result = app.meta(
            tokens, exit_on_error=False, print_error=False, result_action="return_value"
        )
    except CycloptsError as exc:
        return Output(_sniff_mode(tokens)).error(CliError("usage", str(exc).strip()))
    return int(result) if isinstance(result, int) else 0


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Console-script entry point (``atlas-log``)."""
    _utf8_streams()
    tokens = sys.argv[1:]
    try:
        code = run(tokens)
    except KeyboardInterrupt:
        code = 130
    except BrokenPipeError:
        # Downstream closed the pipe (e.g. `| head`); silence the flush at exit.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        code = 1
    except Exception as exc:
        if os.environ.get("ATLAS_LOG_DEBUG"):
            raise
        out = _CTX.output if _CTX is not None else Output(_sniff_mode(tokens))
        code = out.error(
            CliError("internal_error", f"{type(exc).__name__}: {exc} (set ATLAS_LOG_DEBUG=1)")
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
