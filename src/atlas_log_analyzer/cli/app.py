"""The ``atlas-log`` command line application.

Each command is a thin wrapper over :mod:`atlas_log_analyzer.core` and
:mod:`atlas_log_analyzer.store`; rendering and exit codes live in
:mod:`atlas_log_analyzer.cli.output`. Global options (``--output``, ``--store``,
``--quiet``) are parsed by the meta app and may appear anywhere on the line.
"""

from __future__ import annotations

import dataclasses
import difflib
import errno
import importlib
import io
import os
import re
import sys
import textwrap
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from cyclopts import App, CycloptsError, Group, Parameter

from atlas_log_analyzer import __version__
from atlas_log_analyzer.cli.output import (
    CliError,
    ColorMode,
    ExitCode,
    Output,
    OutputMode,
    app_packages,
    default_mode,
    encode_json,
    entry_to_dict,
    error_from_exception,
    jsonable_rows,
    mapping_tables,
    rows_table,
)
from atlas_log_analyzer.cli.output import _cell as _cell
from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.registry import UnknownFormatError
from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.core.parser import parse_sources
from atlas_log_analyzer.core.reader import iter_lines
from atlas_log_analyzer.core.signature import DEFAULT_SPEC, SignatureSpec
from atlas_log_analyzer.store.schema import MERGES_SCHEMA
from atlas_log_analyzer.store.views import (
    DEFAULT_MAX_RAW,
    ENTRY_BRIEF,
    ENTRY_FIELDS,
    GROUP_BRIEF,
    GROUP_FIELDS,
    cap_text,
    cap_texts,
    diff_warning,
    frames_view,
)

# Startup time: the log-set reader (polars), the ingest pipeline and Parquet writer,
# DuckDB and rich are imported by the commands that need them, not here.
if TYPE_CHECKING:
    from rich.console import Console

    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.pipeline import IngestProgress, SourceSelection
    from atlas_log_analyzer.store.store import Store

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
MERGE_FIELDS: tuple[str, ...] = tuple(MERGES_SCHEMA.names)
"""Columns of merges.v1."""
VIEW_FIELDS: dict[str, tuple[str, ...]] = {
    "entries": ENTRY_FIELDS,
    "groups": GROUP_FIELDS,
    "merges": MERGE_FIELDS,
}
"""Columns of the views of `query`, from ``store/schema.py``."""


def _fill_doc(func: Any, **columns: Sequence[str]) -> None:
    """Replace ``<NAME>`` marker lines in ``func``'s docstring (= its ``--help``).

    Each marker becomes the comma-separated ``columns[NAME]``, wrapped and indented
    like the marker line.
    """
    lines = str(func.__doc__ or "").splitlines()
    for i, line in enumerate(lines):
        key = line.strip()[1:-1]
        if line.strip().startswith("<") and key in columns:
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = textwrap.fill(
                ", ".join(columns[key]), width=78, initial_indent=indent, subsequent_indent=indent
            )
    func.__doc__ = "\n".join(lines)


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
    "rate_ratio",
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
    implicit_store: bool = False
    """True when neither --store nor ATLAS_LOG_STORE was given (``./.atlas-log``)."""


_CTX: _Context | None = None


def _env_store() -> Path | None:
    """Return the store named by ``ATLAS_LOG_STORE``; ``None`` when unset or blank.

    A blank value counts as unset: ``Path("")`` would silently make the current
    directory the store.
    """
    value = os.environ.get(STORE_ENV, "").strip()
    return Path(value) if value else None


def _ctx() -> _Context:
    if _CTX is None:  # only when a command function is called outside the CLI
        env = _env_store()
        return _Context(Output(default_mode()), env or DEFAULT_STORE, env is None)
    return _CTX


def _out() -> Output:
    return _ctx().output


# --- helpers


def _in_git_work_tree(path: Path) -> Path | None:
    """Return the first of ``path`` and its parents with a ``.git`` entry, or ``None``."""
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def _store(*, create: bool = False) -> Store:
    """Return the store of this invocation.

    With ``create`` (commands that may create the store directory) the implicit
    default ``./.atlas-log`` is refused inside a git work tree unless it exists
    already: a store there risks committing real logs.
    """
    ctx = _ctx()
    if create and ctx.implicit_store and not ctx.store.exists():
        repo = _in_git_work_tree(Path.cwd().resolve())
        if repo is not None:
            raise CliError(
                "store_error",
                f"refusing to create the default store {ctx.store.as_posix()} inside the git "
                f"work tree {repo.as_posix()}: a store inside a repository risks committing "
                "real logs",
                ExitCode.STORE,
                hint=f"pass --store DIR outside the repository or set {STORE_ENV}",
            )
    from atlas_log_analyzer.store.store import Store

    return Store(ctx.store)


def _open(ref: str) -> LogSet:
    from atlas_log_analyzer.store.store import StoreError

    store = _store()
    try:
        return store.open(ref)
    except StoreError as exc:
        if not str(exc).startswith("no log set"):
            raise
        names = [m.name or m.id for m in store.list()]
        hint = (
            f"available sets: {', '.join(names)}"
            if names
            else "the store is empty; create a set with `atlas-log ingest PATH`"
        )
        raise CliError(
            "store_error",
            f"no log set {ref!r}",
            ExitCode.STORE,
            hint=hint,
            extra={"store": _ctx().store.as_posix()},
        ) from exc


_RELATIVE_RE = re.compile(r"^(\d+)\s*([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def _when(value: str | None, flag: str, anchor: datetime | None) -> datetime | None:
    """Parse a --since/--until value: ISO 8601, YYYY-MM-DD, or Nm/Nh/Nd before ``anchor``."""
    if value is None:
        return None
    text = value.strip()
    if m := _RELATIVE_RE.match(text.lower()):
        base = anchor if anchor is not None else datetime.now(UTC)
        base = base if base.tzinfo is not None else base.replace(tzinfo=UTC)
        return base - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError:
        raise CliError(
            "invalid_value",
            f"{flag} {value!r} is not a time; use ISO 8601 (2026-10-29T09:00, naive = UTC), "
            "a date (2026-10-29) or a duration before the last entry of the set "
            "(30m, 2h, 1d)",
        ) from None


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


def _offset(value: int) -> int:
    if value < 0:
        raise CliError("invalid_value", "--offset must be >= 0")
    return value


def _max_raw(value: int | None, *, text: bool) -> int | None:
    """Resolve ``--max-raw``: explicit value (0 = no limit), else 4000 for json/jsonl.

    ``text`` is true for the plain-text modes (``--raw`` and table), which default
    to no limit.
    """
    if value is not None:
        if value < 0:
            raise CliError("invalid_value", "--max-raw must be >= 0 (0 = no limit)")
        return value or None
    if text or _out().mode == "table":
        return None
    return DEFAULT_MAX_RAW


_cap_items = cap_texts


def _columns(values: Sequence[str] | None, valid: Sequence[str]) -> list[str] | None:
    """Parse ``--columns``: repeated options and/or comma-separated names, validated.

    Returns ``None`` when the option was not given; duplicates are dropped, order is kept.
    """
    if not values:
        return None
    chosen: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if not name:
                continue
            if name not in valid:
                close = difflib.get_close_matches(name, valid, n=1)
                guess = f" (did you mean {close[0]!r}?)" if close else ""
                raise CliError(
                    "invalid_value",
                    f"unknown column {name!r} in --columns{guess}",
                    hint=f"valid columns: {', '.join(valid)}",
                )
            if name not in chosen:
                chosen.append(name)
    if not chosen:
        raise CliError(
            "invalid_value", "--columns is empty", hint=f"valid columns: {', '.join(valid)}"
        )
    return chosen


def _selection(
    columns: Sequence[str] | None, brief: bool, valid: Sequence[str], preset: Sequence[str]
) -> list[str] | None:
    """Columns chosen by ``--columns`` or ``--brief`` (``None`` = all); both is a usage error."""
    if brief and columns:
        raise CliError(
            "usage",
            "--brief and --columns cannot be combined",
            hint=f"--brief is --columns {','.join(preset)}",
        )
    return list(preset) if brief else _columns(columns, valid)


def _select(paths: Sequence[Path], name: str | None) -> SourceSelection:
    """Files and format of `ingest`/`parse`; foreign files found in directories are skipped."""
    from atlas_log_analyzer.store.pipeline import FormatNotDetected, NoSourcesFound, select_sources

    if not paths:
        raise CliError("usage", "at least one PATH is required")
    try:
        selection = select_sources(paths, name)
    except NoSourcesFound as exc:
        raise CliError("no_sources", str(exc), ExitCode.NOT_FOUND) from exc
    except FormatNotDetected as exc:
        raise CliError("format_not_detected", str(exc), ExitCode.NOT_FOUND) from exc
    if selection.skipped:
        names = ", ".join(s.path.name for s in selection.skipped[:5])
        more = f" and {len(selection.skipped) - 5} more" if len(selection.skipped) > 5 else ""
        _out().info(
            f"skipped {len(selection.skipped)} file(s) that are not {selection.fmt.name} "
            f"logs: {names}{more}"
        )
    return selection


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


# --- meta app


@app.meta.default
def _meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    output: Annotated[OutputMode | None, Parameter(group=GLOBAL)] = None,
    store: Annotated[Path | None, Parameter(group=GLOBAL)] = None,
    quiet: Annotated[bool, Parameter(group=GLOBAL, negative="")] = False,
    width: Annotated[int | None, Parameter(group=GLOBAL)] = None,
    color: Annotated[ColorMode, Parameter(group=GLOBAL)] = "auto",
) -> int:
    """Run a command with global options applied.

    Args:
        tokens: Command and its arguments.
        output: Output mode. Default: table when stdout is a terminal, json otherwise.
        store: Store directory holding log sets (env ATLAS_LOG_STORE, ignored when empty;
            default ./.atlas-log, which ingest/serve do not create inside a git work tree).
        quiet: Suppress progress and diagnostics on stderr (warnings and errors still shown).
        width: Table width; default: the terminal width, else $COLUMNS, else 160.
        color: Colour tables and raw entries: auto (terminal and no $NO_COLOR), always, never.

    """
    global _CTX
    if width is not None and width < 40:
        return Output(output or default_mode()).error(
            CliError("invalid_value", "--width must be >= 40")
        )
    out = Output(output or default_mode(), quiet=quiet, width=width, color_mode=color)
    if store is None:
        store = _env_store()
    _CTX = _Context(out, store or DEFAULT_STORE, store is None)
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
    *.log*; rotated files x.log.N are read oldest first). Files found in a
    directory that the format does not recognise in their first 200 lines
    (access, GC or JSON logs next to atlassian-jira.log) are skipped and
    listed in "ingest.skipped"; files named explicitly are always read. The
    log-set id is derived from the file digests, so ingesting the same files
    again is a no-op that prints the existing manifest (use --overwrite to
    rebuild it). Progress goes to stderr.

    Output (json): the manifest, schema "logset.v1":
      {"schema": "logset.v1", "id", "name", "format",
       "sources": [{"path", "size", "xxh3", "first_ts", "last_ts", "entries"}],
       "first_ts", "last_ts", "entry_count", "group_count", "created_at",
       "tool_version", "schema_version", "signature_version",
       "mask_rules_version", "signature_spec",
       "ingest": {"parsed_entries", "parse_errors", "elapsed_s", "existing",
                  "skipped": [path, ...]},
       "merges": [{"cluster_id", "signatures": [str], "drain_template"}]}
    "merges" is present only with --mine; the proposals are also stored in
    merges.parquet (the "merges" view of `query`).
    "ingest.existing" is true when the set already existed and was reused.

    Without --store and ATLAS_LOG_STORE the store is ./.atlas-log; inside a git
    work tree it is not created (error "store_error": real logs could be
    committed), but an existing ./.atlas-log is used.

    Exit codes: 0 ok; 1 unknown --format or bad option; 2 no files found, format
    not detected, or no entries parsed (manifest still printed); 3 store error
    (including a refused default store inside a git work tree).

    Args:
        paths: Log files and/or directories.
        format: Format name (see `formats`); default: detected from the first 200 lines
            of the first file a format recognises (named files first).
        name: Human-readable alias for the set, usable wherever SET is expected.
        frames: Add the top N stack frames to signatures (finer groups); default 0.
        overwrite: Rebuild the set if one with the same id already exists.
        mine: Propose group merges with Drain3 (needs extra "mine": uv sync --extra mine).

    """
    from atlas_log_analyzer.store.pipeline import ingest_sources

    out = _out()
    if frames is not None and frames < 0:
        raise CliError("invalid_value", "--frames must be >= 0")
    selection = _select(paths, format)
    sources, fmt = selection.sources, selection.fmt
    store = _store(create=True)
    total = sum(s.size for s in sources)
    out.info(f"ingesting {len(sources)} file(s), {total / 1e6:.1f} MB, format {fmt.name}")

    def progress(p: IngestProgress) -> None:
        if p.entries % 100_000 == 0:
            out.info(f"  {p.entries:,} entries")

    result = ingest_sources(
        store, sources, fmt, name=name, frames=frames, overwrite=overwrite, progress=progress
    )
    result = dataclasses.replace(result, skipped=tuple(str(s.path) for s in selection.skipped))
    logset = result.logset
    doc = _manifest_dict(logset)
    doc["ingest"] = result.ingest_dict()
    if result.existing:
        out.info(f"log set {logset.manifest.id} already exists; use --overwrite to rebuild it")
    else:
        out.info(
            f"ingested {result.parsed_entries:,} entries into {logset.manifest.id} "
            f"({logset.manifest.group_count:,} groups) in {result.elapsed_s:.1f} s"
        )
    if result.parse_errors:
        out.warn(f"{result.parse_errors:,} entries could not be parsed (parse_error is set)")
    if mine:
        doc["merges"] = _mine(logset)
    out.emit("logset.v1", doc, items=None, title=f"log set {logset.manifest.id}")
    if out.mode == "table" and logset.manifest.entry_count:
        out.info(f"next: atlas-log groups {logset.manifest.name or logset.manifest.id}")
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
    since: str | None = None,
    until: str | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    contains: str | None = None,
    sort: Literal["count", "last_seen", "first_seen", "signature", "template"] = "count",
    limit: int = 50,
    offset: int = 0,
    coarse: bool = False,
    columns: list[str] | None = None,
    brief: bool = False,
) -> None:
    """List the groups (one per message signature) of a log set.

    A group collects all entries whose level, logger, masked message and
    exception chain are equal; its "signature" (16 hex chars) is stable across
    sets, so it can be used with `show`, `entries --signature` and `diff`.

    Output (json):
      {"schema": "groups.v1", "logset": id, "count": int, "total": int,
       "offset": int, "truncated": bool, "items": [
        {"signature", "coarse_signature", "template", "level", "logger",
         "level_name", "exception_type", "count", "first_seen", "last_seen",
         ...}, ...]}
    "level" is numeric (ERROR=50), "level_name" the name. "items" holds the
    groups.v1 columns (`atlas-log schema groups`: also user_count, thread_count,
    source_count, top_users, top_threads, sources, examples), at most --limit
    rows starting at --offset (in --sort order). "total" is the number of
    matching groups before --offset/--limit, "offset" echoes --offset and
    "truncated" is true when more rows follow this page (offset + count <
    total; the next page starts at offset + count). With --coarse, "signature"
    is the coarse signature and "signatures" lists the member signatures.
    --columns keeps only the named columns (comma-separated and/or repeated;
    unknown name: error "invalid_value", "hint" lists the valid ones).
    --brief is the preset --columns signature,template,level_name,logger,
    exception_type,count,first_seen,last_seen; --brief with --columns is a
    usage error.
    --with-exception keeps groups that carry an exception (exception_type not
    null) at any level, so a DEBUG entry with a stack trace is found even
    though --min-level WARN would hide it. --exception TEXT matches a
    case-insensitive substring of exception_type (the outermost exception);
    --contains TEXT a case-insensitive substring of template. With --coarse
    all filters apply to the member groups.

    Exit codes: 0 ok (also when no group matches); 1 bad option, unknown
    column, or --brief with --columns; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        level: Only these levels, e.g. ERROR or WARN,ERROR (TRACE DEBUG INFO WARN ERROR FATAL).
        min_level: Only this level or more severe, e.g. WARN.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        since: Only groups with an entry at/after this time (last_seen >= since): ISO 8601
            (naive = UTC), YYYY-MM-DD, or 30m/2h/1d before the set's last entry.
        until: Only groups first seen before this time (same forms as --since).
        with_exception: Only groups with an exception (exception_type not null), any level.
        exception: Only groups whose exception_type contains this text (case-insensitive).
        contains: Only groups whose template contains this text (case-insensitive).
        sort: count, last_seen (newest first), first_seen (oldest first), signature, template.
        limit: Maximum number of rows; 0 = all.
        offset: Skip this many rows first (paging: next offset = offset + count).
        coarse: Group by coarse signature (level + logger + masked message; no exceptions).
        columns: Columns to return: comma-separated (signature,count) and/or repeated.
        brief: Only signature, template, level_name, logger, exception_type, count,
            first_seen, last_seen.

    """
    lvl, min_lvl, lim, skip = _level(level), _level(min_level), _limit(limit), _offset(offset)
    valid = (*GROUP_FIELDS, "signatures") if coarse else GROUP_FIELDS
    columns = _selection(columns, brief, valid, GROUP_BRIEF)
    ls = _open(logset)
    last = ls.manifest.last_ts
    filters: dict[str, Any] = {
        "level": lvl,
        "min_level": min_lvl,
        "logger": logger,
        "since": _when(since, "--since", last),
        "until": _when(until, "--until", last),
        "coarse": coarse,
        "with_exception": with_exception,
        "exception": exception,
        "contains": contains,
    }
    df = ls.groups(**filters, sort=sort, limit=lim, offset=skip)
    complete = skip == 0 and (lim is None or df.height < lim)
    total = df.height if complete else ls.count_groups(**filters)
    items = jsonable_rows(df if columns is None else df.select(columns))
    _out().emit(
        "groups.v1",
        {
            "logset": ls.manifest.id,
            "count": len(items),
            "total": total,
            "offset": skip,
            "truncated": skip + len(items) < total,
            "items": items,
        },
        columns=columns or GROUP_COLUMNS,
        title=f"groups of {ls.manifest.id}",
    )


@app.command
def show(
    logset: str,
    signature: str,
    /,
    *,
    limit: int = 20,
    raw: bool = False,
    max_raw: int | None = None,
) -> int:
    """Show the entries of one group, with the variables extracted from each message.

    Output (json):
      {"schema": "entries.v1", "logset": id, "signature": str,
       "group": {<groups.v1 row>} | null, "count": int,
       "items": [{<entries.v1 row: id, ts, level, level_name, thread, logger,
                  message, body, user, ..., exception_type, signature,
                  template, variables, source_path, source_line, raw, ...>}, ...]}
    "variables" lists the values that masking replaced in the template, in
    order (issue keys, numbers, ids ...). Entries are ordered by time.
    "raw" and "body" are cut to --max-raw characters (default 4000 in json/jsonl,
    no limit in table and --raw mode); a cut text ends with
    "… [truncated: N chars total, use --max-raw 0]" and its row gets
    "body_truncated": true and "raw_chars": <original length of raw> (both keys
    only on cut rows).
    With --raw the original log text of each entry is printed instead (any
    --output mode), entries separated by a blank line (or, when colour is on,
    highlighted and separated by a source:line rule).

    Exit codes: 0 ok; 1 bad option; 2 no entries with this signature; 3 unknown
    set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        signature: Group signature (from `groups`).
        limit: Maximum number of entries; 0 = all.
        raw: Print the raw log text of the entries (highlighted when colour is on).
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table and --raw mode.

    """
    lim = _limit(limit)
    cap = _max_raw(max_raw, text=raw)
    ls = _open(logset)
    df = ls.entries(signature=signature, limit=lim)
    if df.height == 0:
        raise CliError(
            "not_found",
            f"no entries with signature {signature!r} in log set {ls.manifest.id}",
            ExitCode.NOT_FOUND,
            hint=f"list signatures with `atlas-log groups {ls.manifest.name or ls.manifest.id}`",
        )
    out = _out()
    if raw:
        out.raw_entries(_raw_pairs(df, cap))
        return 0
    import polars as pl

    group_rows = jsonable_rows(ls.groups_lazy().filter(pl.col("signature") == signature).collect())
    items = _cap_items(jsonable_rows(df), cap)
    group = group_rows[0] if group_rows else None

    def render(console: Console) -> None:
        from rich.text import Text

        if group is not None:
            exc = f"  exception {group['exception_type']}" if group.get("exception_type") else ""
            console.print(Text(str(group.get("template", "")), style="bold"))
            console.print(f"logger {group.get('logger')}  level {group.get('level_name')}{exc}")
            console.print(
                f"count {group.get('count')}  first {_cell(group.get('first_seen'))}"
                f"  last {_cell(group.get('last_seen'))}"
            )
        cols = [c for c in ENTRY_COLUMNS if not items or c in items[0]]
        console.print(rows_table(items, cols, title=f"entries of {signature}", color=out.color))

    out.emit(
        "entries.v1",
        {
            "logset": ls.manifest.id,
            "signature": signature,
            "group": group,
            "count": len(items),
            "items": items,
        },
        columns=ENTRY_COLUMNS,
        title=f"entries of {signature}",
        table=render,
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
    request_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    columns: list[str] | None = None,
    brief: bool = False,
    limit: int = 100,
    offset: int = 0,
    raw: bool = False,
    max_raw: int | None = None,
) -> None:
    """List entries of a log set, filtered, ordered by time.

    Output (json):
      {"schema": "entries.v1", "logset": id, "count": int, "total": int,
       "offset": int, "truncated": bool, "items": [<entries.v1 row>, ...]}
    "total" counts the matching entries before --offset/--limit; "offset"
    echoes --offset; "truncated" is true when more entries follow this page
    (offset + count < total; the next page starts at offset + count). With
    --raw the raw log text of the entries is printed instead (as `show --raw`;
    --offset/--limit apply).
    Rows hold all entries.v1 columns (`atlas-log schema entries`; ts is UTC,
    level numeric) unless --columns is given:
      <ENTRIES_COLUMNS>
    --columns takes comma-separated names and/or is repeated
    (--columns ts,level_name --columns message); an unknown name is error
    "invalid_value" whose "hint" lists the valid columns. --brief is the
    preset --columns id,ts,level_name,thread,logger,message,exception_type,
    signature; --brief with --columns is a usage error.
    --with-exception keeps entries with an exception (exception_type not
    null) at any level; --exception TEXT matches a case-insensitive substring
    of exception_type (the outermost exception).
    "raw" and "body" are cut to --max-raw characters (default 4000 in
    json/jsonl, no limit in table and --raw mode); a cut text ends with
    "… [truncated: N chars total, use --max-raw 0]" and its row gets
    "body_truncated": true and "raw_chars": <original length of raw> (of body
    when raw is not among --columns); both keys appear only on cut rows.

    Exit codes: 0 ok (also when nothing matches); 1 bad option, unknown
    column, or --brief with --columns; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        signature: Only entries of this group signature.
        level: Only these levels, e.g. ERROR or WARN,ERROR.
        min_level: Only this level or more severe.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        user: Only this user (exact).
        thread: Only this thread (exact).
        request_id: Only this request id (exact; the Jira request context, e.g. 540x101x1).
        since: Only entries at/after this time: ISO 8601 (naive = UTC), YYYY-MM-DD, or
            30m/2h/1d before the set's last entry.
        until: Only entries before this time (same forms as --since).
        contains: Only entries whose raw text contains this text (case-insensitive).
        with_exception: Only entries with an exception (exception_type not null), any level.
        exception: Only entries whose exception_type contains this text (case-insensitive).
        columns: Columns to return: comma-separated (ts,level_name) and/or repeated.
        brief: Only id, ts, level_name, thread, logger, message, exception_type, signature.
        limit: Maximum number of rows; 0 = all.
        offset: Skip this many entries first (paging: next offset = offset + count).
        raw: Print the raw log text of the entries (highlighted when colour is on).
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table and --raw mode.

    """
    lvl, min_lvl, lim, skip = _level(level), _level(min_level), _limit(limit), _offset(offset)
    columns = _selection(columns, brief, ENTRY_FIELDS, ENTRY_BRIEF)
    cap = _max_raw(max_raw, text=raw)
    ls = _open(logset)
    last = ls.manifest.last_ts
    filters: dict[str, Any] = {
        "signature": signature,
        "level": lvl,
        "min_level": min_lvl,
        "logger": logger,
        "user": user,
        "thread": thread,
        "request_id": request_id,
        "since": _when(since, "--since", last),
        "until": _when(until, "--until", last),
        "contains": contains,
        "with_exception": with_exception,
        "exception": exception,
    }
    if raw:
        cols = ["raw", "source_path", "source_line"]
        page = ls.entries(**filters, limit=lim, offset=skip, columns=cols)
        _out().raw_entries(_raw_pairs(page, cap))
        return
    df = ls.entries(**filters, limit=lim, offset=skip, columns=columns)
    complete = skip == 0 and (lim is None or df.height < lim)
    total = df.height if complete else _count_entries(ls, filters)
    items = _cap_items(jsonable_rows(df), cap)
    _out().emit(
        "entries.v1",
        {
            "logset": ls.manifest.id,
            "count": len(items),
            "total": total,
            "offset": skip,
            "truncated": skip + len(items) < total,
            "items": items,
        },
        columns=columns or ENTRY_COLUMNS,
        title=f"entries of {ls.manifest.id}",
    )


ANCHOR_MARK = "▶"
"""Marks the anchor row of `context` in table mode."""


@app.command
def context(
    logset: str,
    entry_id: str,
    /,
    *,
    before: int = 5,
    after: int = 5,
    any_thread: bool = False,
    max_raw: int | None = None,
) -> int:
    """Show the entries immediately around one entry, in time order (same thread by default).

    Answers "what did this request/thread do just before and after?": the
    --before entries preceding ENTRY_ID and the --after entries following it
    in time order (ties in file order), restricted to the anchor's thread
    unless --any-thread. The anchor itself is included. Entry ids come from
    the "id" column of `entries`, `show` or a group's "examples".

    Output (json):
      {"schema": "entries.v1", "logset": id, "anchor": <entry id>,
       "thread": <anchor's thread> | null, "any_thread": bool, "count": int,
       "items": [<entries.v1 row>, ...]}
    "items" holds at most --before + 1 + --after rows, the anchor among them.
    "raw" and "body" are cut as in `entries` (--max-raw, default 4000 in
    json/jsonl). In jsonl mode one row per line; the anchor row has an extra
    "anchor": true. The table marks the anchor row with ▶.

    Exit codes: 0 ok; 1 bad option; 2 no entry with this id; 3 unknown set or
    store error.

    Args:
        logset: Log set id, unique id prefix or name.
        entry_id: Id of the anchor entry (16 hex chars, from `entries`/`show`).
        before: Entries to show before the anchor.
        after: Entries to show after the anchor.
        any_thread: Consider entries of all threads, not only the anchor's thread.
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table mode.

    """
    for flag, value in (("--before", before), ("--after", after)):
        if value < 0:
            raise CliError("invalid_value", f"{flag} must be >= 0")
    cap = _max_raw(max_raw, text=False)
    ls = _open(logset)
    df = ls.context(entry_id, before=before, after=after, same_thread=not any_thread)
    if df is None:
        ref = ls.manifest.name or ls.manifest.id
        raise CliError(
            "not_found",
            f"no entry with id {entry_id!r} in log set {ls.manifest.id}",
            ExitCode.NOT_FOUND,
            hint=f'entry ids are the "id" column of `atlas-log entries {ref} --brief` '
            f"or `atlas-log show {ref} SIGNATURE`",
        )
    items = _cap_items(jsonable_rows(df), cap)
    anchor = next(row for row in items if row["id"] == entry_id)
    thread = anchor.get("thread")
    out = _out()
    title = f"context of {entry_id}" + ("" if any_thread else f" (thread {thread})")

    def render(console: Console) -> None:
        rows = [{"": ANCHOR_MARK if r["id"] == entry_id else "", **r} for r in items]
        cols = ["", "ts", "level_name", "thread", "user", "logger", "message"]
        console.print(rows_table(rows, cols, title=title, color=out.color))

    out.emit(
        "entries.v1",
        {
            "logset": ls.manifest.id,
            "anchor": entry_id,
            "thread": thread,
            "any_thread": any_thread,
            "count": len(items),
            "items": items,
        },
        jsonl_rows=[{**r, "anchor": True} if r["id"] == entry_id else r for r in items],
        table=render,
    )
    return 0


@app.command
def frames(
    logset: str,
    ref: str,
    /,
    *,
    limit: int = 10,
    all_: Annotated[bool, Parameter(name="--all")] = False,
) -> int:
    """Show the exception chain of one entry with its application frames first.

    REF is an entry id or a group signature (16 hex chars; entry ids are tried
    first). For a signature the group's first example entry is used. Each
    exception of the chain (outermost first, then each "Caused by:") lists its
    first --limit application frames: frames whose class is outside the
    library packages (JDK, Groovy, Jira platform: com.atlassian., org.ofbiz.,
    webwork., org.apache., com.google., JDBC drivers, ...), so plugin code
    stands out. With the environment variable ATLAS_LOG_APP_PACKAGES set
    (comma-separated prefixes, e.g. com.acme.,org.example.) an application
    frame is one that starts with one of those prefixes instead.

    Output (json):
      {"schema": "frames.v1", "logset": id, "entry": <entry id>,
       "signature": str, "ts": str, "level_name": str, "logger": str,
       "message": str, "app_packages": [str] | null, "count": int,
       "items": [{"type", "message",
                  "app_frames": [{"location", "file", "line"}, ...],
                  "app_frames_total": int, "frames_total": int,
                  "omitted_frames": int,
                  "frames": [{"location", "file", "line", "app": bool}]}]}
    "items" is the chain (count = its length; empty when the entry has no
    parsed exception). "app_frames" holds at most --limit frames and
    "app_frames_total" counts all of them; "frames_total" is the number of
    frames printed in the log, "omitted_frames" the "... N more" count.
    "frames" (every frame with an "app" flag) and all application frames are
    returned only with --all.
    "app_packages" echoes ATLAS_LOG_APP_PACKAGES. In jsonl mode one exception
    per line; the table prints each exception and its frames as text.

    Exit codes: 0 ok (also when the entry has no exception); 1 bad option; 2 no
    entry or group REF; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        ref: Entry id, or group signature (its first example entry is used).
        limit: Application frames per exception; 0 = all.
        all_: Return every frame (with an "app" flag) and all application frames.

    """
    lim = _limit(limit)
    ls = _open(logset)
    doc = frames_view(ls, ref, limit=lim, all_frames=all_, app_packages=app_packages())
    if doc is None:
        name = ls.manifest.name or ls.manifest.id
        raise CliError(
            "not_found",
            f"no entry or group {ref!r} in log set {ls.manifest.id}",
            ExitCode.NOT_FOUND,
            hint=f"pass an entry id (`atlas-log entries {name} --with-exception --brief`) "
            f"or a group signature (`atlas-log groups {name} --with-exception --brief`)",
        )
    items: list[dict[str, Any]] = doc["items"]
    out = _out()
    if not items:
        out.info(f"entry {doc['entry']} has no parsed exception")

    def render(console: Console) -> None:
        from rich.text import Text

        console.print(
            Text(f"{doc['level_name']} {doc['logger']}", style="bold")
            + f"  {_cell(doc['ts'])}  entry {doc['entry']}  group {doc['signature']}"
        )
        for i, it in enumerate(items):
            prefix = "Caused by: " if i else ""
            message = f": {it['message']}" if it["message"] else ""
            console.print(Text(f"{prefix}{it['type']}{message}", style="bold" if not i else ""))
            shown = it["frames"] if all_ else it["app_frames"]
            for f in shown:
                where = f"{f['file']}:{f['line']}" if f.get("line") is not None else f["file"]
                style = "bold" if f.get("app", True) else "dim"
                console.print(Text(f"    at {f['location']}({where or ''})", style=style))
            console.print(
                Text(
                    f"    [{len(it['app_frames'])} of {it['app_frames_total']} app frames, "
                    f"{it['frames_total']} frames, {it['omitted_frames']} omitted]",
                    style="dim",
                )
            )

    out.emit("frames.v1", doc, table=render)
    return 0


@app.command
def query(logset: str, sql: str, /) -> None:
    """Run DuckDB SQL over a log set and print the result rows.

    Views: "entries" (entries.v1), "groups" (groups.v1) and "merges" (merges.v1,
    only when present). Their columns (types and descriptions: `atlas-log
    schema entries|groups|merges`):
      entries:
        <ENTRIES_COLUMNS>
      groups:
        <GROUPS_COLUMNS>
      merges:
        <MERGES_COLUMNS>
    level is numeric (TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60),
    ts/first_seen/last_seen are UTC timestamps, variables a list of strings.
    The session time zone is UTC whatever the machine's zone: result
    timestamps are UTC (as in `entries`) and date_trunc('day', ts) buckets
    at UTC midnight.

    SQL examples:
      select level, count(*) n from entries group by 1 order by 2 desc
      select unnest(variables) v, count(*) n from entries
        where signature = '0123456789abcdef' group by 1 order by 2 desc
      select signature, count, template from groups where exception_type is not null
        order by count desc limit 20

    Output (json):
      {"schema": "query.v1", "logset": id, "columns": [str, ...], "count": int,
       "total": int, "truncated": false, "items": [{<column>: value, ...}, ...]}
    The CLI never truncates query results ("total" = "count"); use LIMIT in SQL.

    Errors: "sql_error"; for an unknown column its "hint" lists DuckDB's
    candidate columns (or the columns of the views), for an unknown table the
    available views.

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
        message = str(exc).strip()
        views = [v for v in VIEW_FIELDS if v != "merges" or ls.merges_path.exists()]
        raise CliError("sql_error", message, hint=_sql_hint(message, views)) from exc
    items = jsonable_rows(df)
    _out().emit(
        "query.v1",
        {
            "logset": ls.manifest.id,
            "columns": list(df.columns),
            "count": len(items),
            "total": len(items),
            "truncated": False,
            "items": items,
        },
        title="query result",
    )


_CANDIDATES_RE = re.compile(r"Candidate bindings: (.+)")
_COLUMN_ERRORS = ("Binder Error", "ColumnNotFound", "not found in FROM clause", "column named")


def _sql_hint(message: str, views: Sequence[str]) -> str | None:
    """Next step for a failed query: candidate columns, the views' columns, or the views."""
    if any(marker in message for marker in _COLUMN_ERRORS):
        if m := _CANDIDATES_RE.search(message):
            return (
                f"candidate columns: {m.group(1).strip()}; all columns: "
                "`atlas-log query --help` or `atlas-log schema entries|groups`"
            )
        return "; ".join(f"{v} columns: {', '.join(VIEW_FIELDS[v])}" for v in views)
    if "Catalog Error" in message and "does not exist" in message:
        return f"available views: {', '.join(views)}"
    return None


_fill_doc(entries, ENTRIES_COLUMNS=ENTRY_FIELDS)
_fill_doc(
    query, ENTRIES_COLUMNS=ENTRY_FIELDS, GROUPS_COLUMNS=GROUP_FIELDS, MERGES_COLUMNS=MERGE_FIELDS
)


@app.command
def stats(logset: str, /) -> None:
    """Print summary statistics of a log set.

    Counts per level, per logger (top N) and per hour, the time range and the
    sources, as computed by the store.

    Output (json): {"schema": "stats.v1", "logset": id, <store statistics...>}
    Table mode shows the hours as one row per day with a sparkline of the
    hourly counts; JSON keeps the full "hours" list.

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    ls = _open(logset)
    data = dict(ls.stats())
    data.pop("schema", None)
    doc = {"logset": ls.manifest.id, **data}
    out = _out()

    def render(console: Console) -> None:
        compact = {k: v for k, v in doc.items() if k != "hours"}
        for tbl in mapping_tables(compact, title=f"stats {ls.manifest.id}"):
            console.print(tbl)
        days = _hour_days(data.get("hours") or [])
        if days:
            console.print(rows_table(days, ("day", "count", "max/h", "hours 00-23"), title="hours"))

    out.emit("stats.v1", doc, items=None, title=f"stats {ls.manifest.id}", table=render)


SPARK = " ▁▂▃▄▅▆▇█"


def _hour_days(hours: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-day rows (day, count, max/h, 24-character sparkline) from ``stats`` hours."""
    by_day: dict[str, list[int]] = {}
    for h in hours:
        stamp = str(h.get("hour") or "")
        if len(stamp) < 13:
            continue
        by_day.setdefault(stamp[:10], [0] * 24)[int(stamp[11:13])] += int(h["count"])
    peak = max((max(c) for c in by_day.values()), default=0) or 1
    return [
        {
            "day": day,
            "count": sum(counts),
            "max/h": max(counts),
            "hours 00-23": "".join(
                SPARK[0] if c == 0 else SPARK[max(1, round(c / peak * 8))] for c in counts
            ),
        }
        for day, counts in sorted(by_day.items())
    ]


def _raw_pairs(df: Any, max_raw: int | None = None) -> list[tuple[str, str | None]]:
    """``(raw, "source:line")`` pairs of an entries frame; raw cut to ``max_raw`` chars."""
    pairs = []
    cols = [c for c in ("raw", "source_path", "source_line") if c in df.columns]
    for row in df.select(cols).iter_rows(named=True):
        path, line = row.get("source_path"), row.get("source_line")
        label = f"{Path(path).name}:{line}" if path else None
        raw = str(row["raw"])
        if max_raw is not None:
            raw = cap_text(raw, max_raw)
        pairs.append((raw, label))
    return pairs


def _count_entries(ls: LogSet, filters: dict[str, Any]) -> int:
    """Count the entries matching ``filters`` (no limit)."""
    count = getattr(ls, "count_entries", None)
    if callable(count):
        return int(count(**filters))
    return int(ls.entries(**filters, columns=["id"]).height)


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
    rate: bool = False,
) -> None:
    """Compare the groups of two log sets (A = before, B = after).

    "new": signatures present only in B; "vanished": only in A; "changed": in
    both with a different count (ratio = count_b / count_a). Only the stored
    group tables are compared, never the raw logs.

    Output (json):
      {"schema": "diff.v1", "a": id_a, "b": id_b,
       "new": [row], "vanished": [row], "changed": [row], "unchanged_count": int,
       "totals": {"new": int, "vanished": int, "changed": int}, "truncated": bool,
       "warnings": [str]}
    "totals" are the section sizes before --limit; "truncated" is true when
    any section was cut by --limit.
    Each row has signature, template, level, level_name, logger,
    exception_type, count_a, count_b, first_seen_a, last_seen_a, first_seen_b,
    last_seen_b (counts are 0 on the missing side); "changed" rows also have
    ratio. "new" is sorted by count_b, "vanished" by count_a, "changed" by
    |log(ratio)|, all descending. With --rate "changed" rows also have rate_a
    and rate_b (count per hour of each set's span, first to last entry) and
    rate_ratio = rate_b / rate_a, and "changed" is sorted by rate_ratio
    descending: use it when the sets cover different lengths of time.
    "warnings" is present only when the spans of A and B differ by more than
    4x either way: one sentence saying that new/vanished groups are
    unreliable (groups that occur only at certain times of day). In table and
    jsonl mode the sentence goes to stderr. In jsonl mode one row per line
    with an extra "change" key (new / vanished / changed).

    Exit codes: 0 ok; 1 the sets were built with different signature/mask-rule
    versions or specs (use --force); 3 unknown set or store error.

    Args:
        set_a: Baseline log set (id, id prefix or name).
        set_b: Log set to compare against the baseline.
        min_level: Only groups of this level or more severe (e.g. ERROR).
        coarse: Compare by coarse signature (exception chain ignored).
        force: Compare even if signature versions or specs differ.
        limit: Maximum rows per section; 0 = all.
        rate: Add hourly rates (rate_a, rate_b, rate_ratio) to "changed" and sort it by
            rate_ratio, for sets of different length.

    """
    from atlas_log_analyzer.store import diff as store_diff
    from atlas_log_analyzer.store.query import span_hours, with_rates

    min_lvl, lim = _level(min_level), _limit(limit)
    a, b = _open(set_a), _open(set_b)
    result = store_diff(a, b, min_level=min_lvl, coarse=coarse, force=force)
    changed = with_rates(result.changed, span_hours(a), span_hours(b)) if rate else result.changed
    sections = {
        name: jsonable_rows(frame if lim is None else frame.head(lim))
        for name, frame in (
            ("new", result.new),
            ("vanished", result.vanished),
            ("changed", changed),
        )
    }
    totals = {
        "new": result.new.height,
        "vanished": result.vanished.height,
        "changed": result.changed.height,
    }
    doc = {
        "a": result.a,
        "b": result.b,
        **sections,
        "unchanged_count": result.unchanged_count,
        "totals": totals,
        "truncated": any(len(sections[k]) < totals[k] for k in totals),
    }
    warning = diff_warning(a, b)
    out = _out()
    if warning is not None:
        doc["warnings"] = [warning]
        if out.mode != "json":
            out.warn(warning)
    jsonl = [{"change": name, **row} for name, rows in sections.items() for row in rows]

    def render(console: Console) -> None:
        for name, rows in sections.items():
            if not rows:
                console.print(f"{name}: none")
                continue
            cols = [c for c in DIFF_COLUMNS if c in rows[0]]
            shown = f"{len(rows)} of {totals[name]}" if len(rows) < totals[name] else len(rows)
            console.print(rows_table(rows, cols, title=f"{name} ({shown})", color=out.color))
        console.print(f"unchanged: {result.unchanged_count}")

    out.emit("diff.v1", doc, items=None, jsonl_rows=jsonl, table=render)


NEW_COLUMNS = (
    "signature",
    "level_name",
    "count_b",
    "rate_per_hour",
    "logger",
    "exception_type",
    "template",
)
GREW_COLUMNS = ("signature", "level_name", "count_a", "count_b", "rate_ratio", "logger", "template")
VANISHED_COLUMNS = ("signature", "level_name", "count_a", "logger", "exception_type", "template")


@app.command
def triage(
    logset: str,
    /,
    *,
    baseline: str = "auto",
    min_level: str = "WARN",
    grow: float = 2.0,
    limit: int = 50,
) -> None:
    """Answer "are there new kinds of errors since the baseline?" in one document.

    Compares SET with a baseline (as `diff BASELINE SET --min-level L`) and
    sorts the result for a decision: new groups (most severe first), new
    groups of a lower level that carry an exception, groups whose hourly rate
    grew, vanished groups. --baseline auto (default) picks the newest other
    set that ends before SET starts, else the newest other set that ends no
    later than SET (the web app's rule); with no such set the verdict is
    no_baseline.

    Output (json):
      {"schema": "triage.v1",
       "set": {"id", "name", "first_ts", "last_ts", "entries"},
       "baseline": {"id", "name", "first_ts", "last_ts",
                    "resolved_by": "auto" | "param"} | null,
       "min_level": "WARN",
       "verdict": "new_errors" | "new_warnings" | "new_exceptions" | "clean"
                  | "no_baseline",
       "summary": {"new", "new_by_level": {"ERROR": n, ...}, "new_exceptions",
                   "grew", "vanished", "changed", "unchanged"} | null,
       "coverage": {"hours_baseline", "hours_set", "ratio"},
       "warnings": [str, ...],
       "new": [<diff row> + "rate_per_hour"],
       "new_exceptions": [<diff row> + "rate_per_hour"],
       "grew": [<diff row> + "ratio", "rate_a", "rate_b", "rate_ratio"],
       "vanished": [<diff row>]}
    Diff rows are those of `diff` (signature, template, level, level_name,
    logger, exception_type, count_a, count_b, first/last_seen_a/b); set and
    baseline timestamps are written as in their manifests.
    "new": groups at/above --min-level only in SET; "new_exceptions": new
    groups of ANY level with an exception that are not in "new" (a new DEBUG
    stack trace); "grew": changed groups whose hourly rate rose at least --grow
    times (rates as `diff --rate`, which normalise different spans), highest
    rate_ratio first; "vanished": groups at
    /above --min-level only in the baseline. "verdict": new_errors (a new
    ERROR/FATAL group), new_warnings (other new groups), new_exceptions (no
    new group but a new exception at a lower level), clean, no_baseline.
    "coverage" gives both spans in hours and ratio = hours_set /
    hours_baseline; when they differ by more than 4x either way "warnings"
    says that "new"/"vanished" are unreliable (a nightly set lacks daytime
    groups). Lists hold at most --limit rows; "summary" counts all.
    jsonl mode prints the document on one line. Table mode prints the
    verdict, the summary and one table per list; the warnings go to stderr.

    Exit codes: 0 ok, whatever the verdict (also no_baseline); 1 bad option,
    invalid level, or the sets were built with different signature/mask-rule
    versions or specs; 3 unknown set or store error.

    Args:
        logset: The log set to judge (id, id prefix or name).
        baseline: Baseline set (id, id prefix or name), or auto.
        min_level: Only groups of this level or more severe count as new/grew/vanished.
        grow: Minimum hourly-rate factor for "grew" (> 1).
        limit: Maximum rows per list; 0 = all.

    """
    from atlas_log_analyzer.store.triage import auto_baseline
    from atlas_log_analyzer.store.triage import triage as store_triage

    lvl, lim = _level(min_level), _limit(limit)
    if not grow > 1:
        raise CliError("invalid_value", "--grow must be > 1")
    ls = _open(logset)
    if baseline == "auto":
        best = auto_baseline(_store().list(), ls.manifest)
        base = _open(best.id) if best is not None else None
    else:
        base = _open(baseline)
    doc = store_triage(
        ls,
        base,
        min_level=lvl,
        grow=grow,
        limit=lim,
        resolved_by="auto" if baseline == "auto" else "param",
    )
    out = _out()
    if out.mode == "table":
        for warning in doc["warnings"]:
            out.warn(warning)

    def render(console: Console) -> None:
        from rich.text import Text

        me = doc["set"]
        if doc["baseline"] is None:
            console.print(f"verdict: no_baseline ({me['name'] or me['id']} has no earlier set)")
            return
        b, s, cov = doc["baseline"], doc["summary"], doc["coverage"]
        console.print(
            Text(f"verdict: {doc['verdict']}", style="bold")
            + f"  {me['name'] or me['id']} vs {b['name'] or b['id']} ({b['resolved_by']})"
        )
        levels = ", ".join(f"{k} {v}" for k, v in s["new_by_level"].items())
        console.print(
            f"new {s['new']}"
            + (f" ({levels})" if levels else "")
            + f"  new exceptions {s['new_exceptions']}  grew {s['grew']} of {s['changed']}"
            f" changed  vanished {s['vanished']}  unchanged {s['unchanged']}"
        )
        console.print(
            f"coverage: set {cov['hours_set']} h, baseline {cov['hours_baseline']} h"
            f" (ratio {cov['ratio']})"
        )
        for name, cols in (
            ("new", NEW_COLUMNS),
            ("new_exceptions", NEW_COLUMNS),
            ("grew", GREW_COLUMNS),
            ("vanished", VANISHED_COLUMNS),
        ):
            rows = doc[name]
            if not rows:
                console.print(f"{name}: none")
                continue
            console.print(rows_table(rows, cols, title=name, color=out.color))

    body = {k: v for k, v in doc.items() if k != "schema"}
    out.emit("triage.v1", body, items=None, table=render)


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
    Entries whose header could not be parsed have "parse_error" set. Files
    found in a directory that are not in the format are skipped (as `ingest`).

    Exit codes: 0 ok; 1 unknown --format; 2 no files, format not detected or no
    entries.

    Args:
        paths: Log files and/or directories.
        format: Format name; detected from the first 200 lines when omitted.
        limit: Stop after this many entries; 0 = all.

    """
    import msgspec

    lim = _limit(limit)
    selection = _select(paths, format)
    sources, fmt = selection.sources, selection.fmt
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
def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8321,
    token: str | None = None,
    max_upload_mb: int = 2048,
    reload: bool = False,
) -> None:
    """Run the web app over the store: browse, compare and triage sets, upload new logs.

    Pages: / (sets with triage verdicts and upload), /sets/SET (interactive
    report, ?compare=auto|SET), /sets/SET/triage. JSON API under /api with
    the same documents as this CLI (OpenAPI at /docs). Needs the `web` extra.

    Output: server log on stderr until interrupted (Ctrl+C); nothing on stdout
    ("schema": none).

    Exit codes: 0 stopped normally; 1 the `web` extra is not installed or bad
    option; 3 store error (including the default ./.atlas-log not existing inside
    a git work tree: pass --store DIR or set ATLAS_LOG_STORE).

    Args:
        host: Interface to bind; use 0.0.0.0 to serve the network (set --token then).
        port: TCP port.
        token: Require `Authorization: Bearer TOKEN` on /api (env ATLAS_LOG_TOKEN).
        max_upload_mb: Largest accepted upload in MiB.
        reload: Restart on code changes (development).

    """
    try:
        import uvicorn

        from atlas_log_analyzer.webapp import Settings, create_app
    except ImportError as exc:
        raise CliError(
            "missing_dependency",
            f"the web app needs the 'web' extra ({exc.name or exc}); "
            "install it with: uv sync --extra web  (or pip install 'atlas-log-analyzer[web]')",
        ) from exc
    if not 0 < port < 65536:
        raise CliError("invalid_value", "--port must be between 1 and 65535")
    store = _store(create=True)  # uploads create the store directory
    settings = Settings.from_env(store=store.root, max_upload_mb=max_upload_mb, token=token)
    _out().info(f"serving {settings.store.as_posix()} at http://{host}:{port}/")
    if reload:
        import os

        os.environ["ATLAS_LOG_STORE"] = str(settings.store)
        uvicorn.run(
            "atlas_log_analyzer.webapp.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
        )
        return
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")


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


def _stdout_closed(exc: BaseException) -> bool:
    """Tell whether ``exc`` means that the reader of stdout has gone (e.g. ``| head``).

    POSIX raises :class:`BrokenPipeError`. Windows raises ``OSError(EINVAL)`` from
    the write or flush instead; it is told apart from other EINVAL errors (such as
    an invalid file name) by flushing stdout again, which fails the same way while
    the unwritten data is still buffered.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    if not isinstance(exc, OSError) or exc.errno != errno.EINVAL:
        return False
    try:
        sys.stdout.flush()
    except OSError:
        return True
    return False


def main() -> None:
    """Console-script entry point (``atlas-log``)."""
    _utf8_streams()
    tokens = sys.argv[1:]
    try:
        code = run(tokens)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        if _stdout_closed(exc):
            # Downstream closed the pipe; silence the flush at exit.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            sys.exit(1)
        if os.environ.get("ATLAS_LOG_DEBUG"):
            raise
        out = _CTX.output if _CTX is not None else Output(_sniff_mode(tokens))
        code = out.error(
            CliError("internal_error", f"{type(exc).__name__}: {exc} (set ATLAS_LOG_DEBUG=1)")
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
