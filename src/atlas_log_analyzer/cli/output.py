"""Rendering of command results (``json`` / ``jsonl`` / ``table``), error envelopes, exit codes.

Every JSON document written by the CLI is an object carrying a ``"schema"`` key
(``"<name>.v1"``). Listings put their rows under ``"items"``. In ``jsonl`` mode
listings are written one row per line (without the envelope); single documents
are written as one line. Errors in ``json``/``jsonl`` mode are written to
stdout as ``{"schema": "error.v1", "error": {"code": ..., "message": ...}}``;
in ``table`` mode they go to stderr. Diagnostics always go to stderr. Errors and
exit codes are :mod:`atlas_log_analyzer.service.errors`.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TextIO

import msgspec

from atlas_log_analyzer.core.java import (
    APP_PACKAGES_ENV,
    LIBRARY_PACKAGES,
    app_packages_from_env,
    is_app_frame,
)
from atlas_log_analyzer.core.models import LogEntry
from atlas_log_analyzer.service.errors import ExitCode, OpError, from_exception

# polars and rich are imported where used: JSON output of a command that reads no
# log set needs neither, and rich is needed only for tables and coloured raw text.
if TYPE_CHECKING:
    import polars as pl
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

__all__ = [
    "APP_PACKAGES_ENV",
    "LIBRARY_PACKAGES",
    "ExitCode",
    "OpError",
    "Output",
    "OutputMode",
    "app_packages",
    "default_mode",
    "encode_json",
    "entry_to_dict",
    "error_from_exception",
    "highlight_entry",
    "iso_timestamp",
    "jsonable_rows",
    "mapping_tables",
    "rows_table",
]

type ColorMode = Literal["auto", "always", "never"]

type OutputMode = Literal["table", "json", "jsonl"]

MAX_CELL = 80
"""Table cells longer than this are truncated with an ellipsis."""

_ISO_RE = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?$")

DEFAULT_WIDTH = 160
"""Table width when stdout is not a terminal and neither ``--width`` nor ``COLUMNS`` is set."""

NO_WRAP_COLUMNS: Mapping[str, int] = {
    "signature": 16,
    "coarse_signature": 16,
    "level_name": 5,
    "level": 5,
    "count": 5,
    "count_a": 7,
    "count_b": 7,
    "ratio": 5,
    "ts": 20,
    "first_seen": 20,
    "last_seen": 20,
    "first_ts": 20,
    "last_ts": 20,
    "created_at": 20,
    "entry_count": 11,
    "group_count": 11,
}
"""Columns that are never wrapped, with their minimum width."""

WIDE_COLUMNS = frozenset({"template", "logger", "message", "drain_template"})
"""Columns that get a larger share of the free width (``ratio=2``)."""

LEVEL_STYLES: Mapping[str, str] = {"FATAL": "bold red", "ERROR": "red", "WARN": "yellow"}
"""Styles of ``level_name`` cells when stdout is a terminal."""


def error_from_exception(exc: BaseException) -> OpError | None:
    """Map a known exception to the CLI's :class:`OpError`; ``None`` for unknown ones.

    Library exceptions as :func:`atlas_log_analyzer.service.errors.from_exception`;
    a cyclopts parse error is ``usage``.
    """
    from cyclopts import CycloptsError

    if isinstance(exc, CycloptsError):
        return OpError("usage", str(exc).strip())
    return from_exception(exc, "cli")


def default_mode(stream: TextIO | None = None) -> OutputMode:
    """``table`` when ``stream`` (default: stdout) is a terminal, ``json`` otherwise."""
    stream = stream if stream is not None else sys.stdout
    try:
        return "table" if stream.isatty() else "json"
    except AttributeError, ValueError:
        return "json"


# --- JSON


def _enc_hook(obj: Any) -> Any:
    # polars objects can only exist if polars was imported; don't import it to check.
    pl_mod = sys.modules.get("polars")
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if pl_mod is not None and isinstance(obj, pl_mod.DataFrame):
        return obj.to_dicts()
    if pl_mod is not None and isinstance(obj, pl_mod.Series):
        return obj.to_list()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise NotImplementedError(f"cannot encode {type(obj).__name__} as JSON")


_ENCODER = msgspec.json.Encoder(enc_hook=_enc_hook, decimal_format="number")


def encode_json(obj: Any, *, indent: int = 0) -> str:
    """Encode ``obj`` as JSON.

    Datetimes become ISO 8601 strings, enums their name, paths POSIX strings,
    DataFrames lists of row objects.
    """
    data = _ENCODER.encode(_normalise(obj))
    if indent:
        data = msgspec.json.format(data, indent=indent)
    return data.decode("utf-8")


def _normalise(obj: Any) -> Any:
    # msgspec encodes enums by value natively; the CLI wants names, so convert up front.
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, dict):
        return {k: _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    return obj


def jsonable_rows(data: pl.DataFrame | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows of a DataFrame or an iterable of mappings as a list of plain dicts.

    A DataFrame goes through :func:`atlas_log_analyzer.store.logset.to_records`, the
    one row serialiser of both front ends: datetimes become ISO 8601 strings in UTC
    with milliseconds (naive values taken as UTC), maps become dicts. No ``zoneinfo``
    data is needed, which Windows lacks unless ``tzdata`` is installed.
    """
    if "polars" in sys.modules:  # a DataFrame implies polars was imported
        import polars as pl

        if isinstance(data, pl.DataFrame):
            from atlas_log_analyzer.store.logset import to_records

            return to_records(data)
    return [dict(row) for row in data]


def iso_timestamp(value: datetime) -> str:
    """ISO 8601 with offset; milliseconds when the value has no finer precision."""
    if value.microsecond % 1000 == 0:
        return value.isoformat(timespec="milliseconds")
    return value.isoformat()


def entry_to_dict(entry: LogEntry) -> dict[str, Any]:
    """Plain-dict form of a :class:`LogEntry`, as streamed by ``atlas-log parse``.

    Keys: ``id, format, timestamp (ISO 8601 with offset), level (name), thread,
    logger, message, body, exception_type, exceptions[{type, message,
    frames[{location, file, line, module}], omitted_frames}], context{},
    source{path, line, offset}, raw, parse_error``.
    """
    return {
        "id": entry.id,
        "format": entry.format,
        "timestamp": iso_timestamp(entry.timestamp),
        "level": entry.level.name,
        "thread": entry.thread,
        "logger": entry.logger,
        "message": entry.message,
        "body": entry.body,
        "exception_type": entry.exception_type,
        "exceptions": [
            {
                "type": exc.type,
                "message": exc.message,
                "frames": [
                    {"location": f.location, "file": f.file, "line": f.line, "module": f.module}
                    for f in exc.frames
                ],
                "omitted_frames": exc.omitted_frames,
            }
            for exc in entry.exceptions
        ],
        "context": dict(entry.context),
        "source": {
            "path": entry.source.path,
            "line": entry.source.line,
            "offset": entry.source.offset,
        },
        "raw": entry.raw,
        "parse_error": entry.parse_error,
    }


# --- tables


def _cell(value: Any, max_len: int = MAX_CELL) -> str:
    if value is None:
        text = ""
    elif isinstance(value, Enum):
        text = value.name
    elif isinstance(value, datetime):
        text = value.isoformat(sep=" ", timespec="seconds")
    elif isinstance(value, date):
        text = value.isoformat()
    elif isinstance(value, (list, tuple, Mapping)):
        text = encode_json(value)
    elif isinstance(value, str) and (m := _ISO_RE.match(value)):
        # ISO timestamps shown to the second; UTC marked with Z.
        offset = m.group(3) or ""
        text = f"{m.group(1)} {m.group(2)}{'Z' if offset in ('Z', '+00:00') else offset}"
    else:
        text = str(value)
    text = text.replace("\r", "").replace("\n", " ⏎ ")
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (list, tuple, Mapping))


def rows_table(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str] | None = None,
    *,
    title: str | None = None,
    max_len: int = MAX_CELL,
    color: bool = False,
) -> Table:
    """Build a rich table from rows; ``columns`` restricts and orders the columns shown.

    Numeric columns are right-aligned; ids, levels, counts and timestamps are
    never wrapped. With ``color`` the ``level_name`` cells are coloured.
    """
    from rich.markup import escape
    from rich.table import Table

    if columns is None:
        seen: dict[str, None] = {}
        for row in rows[:100]:
            seen.update(dict.fromkeys(row))
        columns = list(seen)
    table = Table(title=title, title_justify="left")
    for col in columns:
        sample = next((row.get(col) for row in rows[:100] if row.get(col) is not None), None)
        numeric = isinstance(sample, (int, float)) and not isinstance(sample, bool)
        justify: Literal["left", "right"] = "right" if numeric else "left"
        if col in NO_WRAP_COLUMNS:
            table.add_column(col, no_wrap=True, min_width=NO_WRAP_COLUMNS[col], justify=justify)
        else:
            wide = col in WIDE_COLUMNS
            table.add_column(
                col,
                overflow="fold",
                ratio=2 if wide else 1,
                min_width=8 if wide else 5,
                justify=justify,
            )
    for row in rows:
        cells = []
        for col in columns:
            text = escape(_cell(row.get(col), max_len))
            style = LEVEL_STYLES.get(text) if color and col == "level_name" else None
            cells.append(f"[{style}]{text}[/]" if style else text)
        table.add_row(*cells)
    return table


def mapping_tables(doc: Mapping[str, Any], *, title: str | None = None) -> list[Table]:
    """Render a document: scalars as a key/value table, nested values as extra tables."""
    from rich.table import Table

    kv = Table(title=title, title_justify="left", show_header=False)
    kv.add_column("key", style="bold")
    kv.add_column("value", overflow="fold")
    extra: list[Table] = []
    for key, value in doc.items():
        if key == "schema":
            continue
        if (
            isinstance(value, (list, tuple))
            and value
            and all(isinstance(v, Mapping) for v in value)
        ):
            extra.append(rows_table([dict(v) for v in value], title=key))
        elif isinstance(value, Mapping) and value and all(_is_scalar(v) for v in value.values()):
            sub = Table(title=key, title_justify="left")
            sub.add_column("key")
            sub.add_column("value", overflow="fold")
            for k, v in value.items():
                sub.add_row(_cell(k), _cell(v))
            extra.append(sub)
        else:
            kv.add_row(str(key), _cell(value, 200))
    return [kv, *extra]


def _present_columns(
    rows: Sequence[Mapping[str, Any]], preferred: Sequence[str] | None
) -> list[str] | None:
    if preferred is None:
        return None
    if not rows:
        return list(preferred)
    keys = rows[0].keys()
    chosen = [c for c in preferred if c in keys]
    return chosen or None


# --- raw entry highlighting

_HEADER_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[,.]\d+)?(?:Z|[+-]\d\d:?\d\d)?)"
    r"(?P<mid>.*?)\b(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\b"
    r"(?P<ctx>[^\[]*)(?:\[(?P<logger>[^\]\s]+)\])?(?P<msg>.*)$"
)
_EXC_RE = re.compile(
    r"^(?P<indent>\s*)(?P<prefix>(?:Caused by|Suppressed): )?"
    r"(?P<cls>(?:[A-Za-z_$][\w$]*\.)+[A-Z][\w$]*)(?P<rest>(?::\s.*|:)?)$"
)
_FRAME_RE = re.compile(
    r"^(?P<indent>\s*)at (?P<module>[\w$.@-]+/)?(?P<pkg>(?:[\w$]+\.)*?)"
    r"(?P<meth>[\w$]+\.[\w$<>]+)(?P<loc>\(.*\))\s*$"
)
_MORE_RE = re.compile(r"^\s*\.\.\. \d+ (?:more|common frames omitted)\s*$")


app_packages = app_packages_from_env
"""Application package prefixes from ``ATLAS_LOG_APP_PACKAGES`` (see ``core.java``)."""


HEADER_LEVEL_STYLES: Mapping[str, str] = {
    "FATAL": "bold red",
    "ERROR": "bold red",
    "WARN": "yellow",
    "WARNING": "yellow",
    "DEBUG": "dim",
    "TRACE": "dim",
}


def highlight_entry(raw: str) -> Text:
    """Return the raw text of one log entry as a styled :class:`rich.text.Text`.

    Header line: timestamp dim, level coloured (ERROR/FATAL bold red, WARN
    yellow, DEBUG/TRACE dim), logger cyan. Exception lines: class name bold,
    ``Caused by:``/``Suppressed:`` magenta. Stack frames: package dim,
    ``(File.java:12)`` cyan, application frames (outside :data:`LIBRARY_PACKAGES`,
    or inside ``ATLAS_LOG_APP_PACKAGES`` when set) bold. ``... N more`` dim italic.
    Unrecognised lines are unstyled.
    """
    from rich.text import Text

    prefixes = app_packages()
    text = Text()
    for i, line in enumerate(raw.replace("\r", "").split("\n")):
        if i:
            text.append("\n")
        if i == 0 and (m := _HEADER_RE.match(line)):
            text.append(m["ts"], "dim")
            text.append(m["mid"])
            text.append(m["level"], HEADER_LEVEL_STYLES.get(m["level"], ""))
            text.append(m["ctx"])
            if m["logger"] is not None:
                text.append("[")
                text.append(m["logger"], "cyan")
                text.append("]")
            text.append(m["msg"])
        elif m := _FRAME_RE.match(line):
            name = m["pkg"] + m["meth"]
            app_frame = is_app_frame(name, prefixes)
            text.append(m["indent"] + "at ", "dim")
            if m["module"]:
                text.append(m["module"], "dim")
            text.append(m["pkg"], "dim")
            text.append(m["meth"], "bold" if app_frame else "")
            text.append(m["loc"], "cyan")
        elif _MORE_RE.match(line):
            text.append(line, "dim italic")
        elif m := _EXC_RE.match(line):
            text.append(m["indent"])
            if m["prefix"]:
                text.append(m["prefix"], "magenta")
            text.append(m["cls"], "bold")
            text.append(m["rest"])
        else:
            text.append(line)
    return text


def resolve_color(mode: ColorMode, tty: bool) -> bool:
    """Whether to colour output: ``always``/``never``, or ``auto`` = a tty and no ``NO_COLOR``."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return tty and not os.environ.get("NO_COLOR")


# --- Output


@dataclasses.dataclass
class Output:
    """Writes command results in the selected mode.

    ``stdout``/``stderr`` default to the *current* ``sys.stdout``/``sys.stderr``
    at write time, so redirection and test capture work.
    """

    mode: OutputMode = "json"
    quiet: bool = False
    width: int | None = None
    color_mode: ColorMode = "auto"
    """``--color``: ``auto`` colours a terminal unless ``NO_COLOR`` is set."""
    """Table width (``--width``); default: the terminal, else ``COLUMNS``, else 160."""
    stdout: TextIO | None = None
    stderr: TextIO | None = None

    @property
    def out(self) -> TextIO:
        """The stream results are written to."""
        return self.stdout if self.stdout is not None else sys.stdout

    @property
    def err(self) -> TextIO:
        """The stream diagnostics are written to."""
        return self.stderr if self.stderr is not None else sys.stderr

    def is_tty(self) -> bool:
        """Whether stdout is a terminal."""
        try:
            return bool(self.out.isatty())
        except AttributeError, ValueError:
            return False

    @property
    def color(self) -> bool:
        """Whether table/raw output is coloured (see :func:`resolve_color`)."""
        return resolve_color(self.color_mode, self.is_tty())

    def console(self) -> Console:
        """Return a rich console on stdout; plain text (no ANSI) unless colour is enabled."""
        from rich.console import Console

        color = self.color
        return Console(
            file=self.out,
            force_terminal=color,
            no_color=not color,
            highlight=False,
            soft_wrap=False,
            width=self.table_width(),
        )

    def table_width(self) -> int | None:
        """Explicit ``--width``, else ``None`` on a terminal, else ``COLUMNS`` or 160."""
        if self.width is not None:
            return self.width
        if self.is_tty():
            return None
        try:
            columns = int(os.environ.get("COLUMNS", ""))
        except ValueError:
            columns = 0
        return columns if columns > 0 else DEFAULT_WIDTH

    def raw_entries(self, entries: Iterable[tuple[str, str | None]]) -> None:
        """Print raw entry texts; coloured, with a ``source:line`` rule, when colour is on.

        Args:
            entries: ``(raw, label)`` pairs; ``label`` (e.g. ``"app.log:12"``) titles the rule.

        """
        if not self.color:
            self.out.write("\n\n".join(raw for raw, _ in entries) + "\n")
            return
        from rich.text import Text

        console = self.console()
        for raw, label in entries:
            console.rule(Text(label or "", style="dim"), style="dim", align="left")
            console.print(highlight_entry(raw), soft_wrap=True)

    def write_line(self, text: str) -> None:
        """Write one line to stdout."""
        self.out.write(text + "\n")

    def info(self, message: str) -> None:
        """Diagnostic message to stderr (suppressed by ``--quiet``)."""
        if not self.quiet:
            self.err.write(message + "\n")
            self.err.flush()

    def warn(self, message: str) -> None:
        """Warning to stderr (always shown)."""
        self.err.write(f"warning: {message}\n")
        self.err.flush()

    def emit(
        self,
        schema: str,
        doc: Mapping[str, Any],
        *,
        items: str | None = "items",
        columns: Sequence[str] | None = None,
        title: str | None = None,
        jsonl_rows: Iterable[Mapping[str, Any]] | None = None,
        table: Callable[[Console], None] | None = None,
    ) -> None:
        """Write one result document in the selected mode.

        Args:
            schema: Value of the ``"schema"`` key, e.g. ``"groups.v1"``.
            doc: Document body (without ``"schema"``).
            items: Key in ``doc`` holding the row list, or ``None`` for a plain document.
            columns: Preferred table columns (those present in the rows are shown, in order).
            title: Table title.
            jsonl_rows: Rows for ``jsonl`` mode, overriding ``doc[items]``.
            table: Custom table renderer, overriding the default one.

        """
        full = {"schema": schema, **doc}
        if self.mode == "json":
            self.write_line(encode_json(full, indent=2 if self.is_tty() else 0))
            return
        if self.mode == "jsonl":
            rows = jsonl_rows
            if rows is None and items is not None and items in doc:
                rows = doc[items]
            if rows is None:
                self.write_line(encode_json(full))
            else:
                self.out.write("".join(encode_json(row) + "\n" for row in rows))
            return
        console = self.console()
        if table is not None:
            table(console)
            return
        if items is not None and items in doc:
            rows_list = list(doc[items])
            console.print(
                rows_table(
                    rows_list,
                    _present_columns(rows_list, columns),
                    title=title,
                    color=self.color,
                )
            )
            meta = {k: v for k, v in doc.items() if k != items and _is_scalar(v)}
            if meta:
                self.info("  ".join(f"{k}={_cell(v)}" for k, v in meta.items()))
            return
        for tbl in mapping_tables(doc, title=title):
            console.print(tbl)

    def document(
        self,
        doc: Mapping[str, Any],
        *,
        items: str | None = "items",
        columns: Sequence[str] | None = None,
        title: str | None = None,
        jsonl_rows: Iterable[Mapping[str, Any]] | None = None,
        table: Callable[[Console], None] | None = None,
    ) -> None:
        """Write a complete document (``"schema"`` included) as :meth:`emit` does."""
        body = {k: v for k, v in doc.items() if k != "schema"}
        self.emit(
            str(doc["schema"]),
            body,
            items=items,
            columns=columns,
            title=title,
            jsonl_rows=jsonl_rows,
            table=table,
        )

    def error(self, err: OpError) -> int:
        """Report ``err`` (stdout envelope in json/jsonl mode, stderr in table mode)."""
        if self.mode in ("json", "jsonl"):
            self.write_line(encode_json(err.envelope()))
            self.out.flush()
        else:
            self.err.write(f"error: {err.message} [{err.code}]\n")
            if err.hint is not None:
                self.err.write(f"hint: {err.hint}\n")
            self.err.flush()
        return int(err.exit_code)
