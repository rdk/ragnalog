"""Rendering of command results (``json`` / ``jsonl`` / ``table``), error envelopes, exit codes.

Every JSON document written by the CLI is an object carrying a ``"schema"`` key
(``"<name>.v1"``). Listings put their rows under ``"items"``. In ``jsonl`` mode
listings are written one row per line (without the envelope); single documents
are written as one line. Errors in ``json``/``jsonl`` mode are written to
stdout as ``{"schema": "error.v1", "error": {"code": ..., "message": ...}}``;
in ``table`` mode they go to stderr. Diagnostics always go to stderr.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Literal, TextIO

import msgspec
import polars as pl
from rich.console import Console
from rich.table import Table

from atlas_log_analyzer.core.models import LogEntry

__all__ = [
    "CliError",
    "ExitCode",
    "Output",
    "OutputMode",
    "default_mode",
    "encode_json",
    "entry_to_dict",
    "error_from_exception",
    "iso_timestamp",
    "jsonable_rows",
    "mapping_tables",
    "rows_table",
]

type OutputMode = Literal["table", "json", "jsonl"]

MAX_CELL = 80
"""Table cells longer than this are truncated with an ellipsis."""

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%.f%:z"
"""chrono format for timezone-aware datetime columns (ISO 8601 with offset)."""

_ISO_RE = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?$")
"""Table cells longer than this are truncated with an ellipsis."""


class ExitCode(IntEnum):
    """Process exit codes shared by all commands."""

    OK = 0
    USAGE = 1
    """Invalid arguments, unknown format, bad SQL, version mismatch without ``--force``."""
    NOT_FOUND = 2
    """Nothing found (no files, no entries for a signature) or nothing could be parsed."""
    STORE = 3
    """Store failure: unknown log set, corrupt manifest, unsupported schema version."""


class CliError(Exception):
    """A failure reported to the user as an error envelope plus an exit code."""

    def __init__(self, code: str, message: str, exit_code: ExitCode = ExitCode.USAGE) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def error_from_exception(exc: BaseException) -> CliError | None:
    """Map a known library exception to a :class:`CliError`; ``None`` for unknown ones."""
    from cyclopts import CycloptsError

    from atlas_log_analyzer.core.formats.registry import UnknownFormatError
    from atlas_log_analyzer.store.store import StoreError

    if isinstance(exc, CliError):
        return exc
    if isinstance(exc, StoreError):
        return CliError("store_error", str(exc), ExitCode.STORE)
    if isinstance(exc, UnknownFormatError):
        return CliError("unknown_format", _message(exc), ExitCode.USAGE)
    if isinstance(exc, FileNotFoundError):
        name = exc.filename if exc.filename is not None else _message(exc)
        return CliError("file_not_found", f"no such file or directory: {name}", ExitCode.NOT_FOUND)
    if isinstance(exc, CycloptsError):
        return CliError("usage", str(exc).strip(), ExitCode.USAGE)
    if isinstance(exc, ValueError):
        return CliError("invalid_value", str(exc), ExitCode.USAGE)
    return None


def _message(exc: BaseException) -> str:
    # str() of a LookupError with one argument adds quotes around it.
    return str(exc.args[0]) if len(exc.args) == 1 else str(exc)


def default_mode(stream: TextIO | None = None) -> OutputMode:
    """``table`` when ``stream`` (default: stdout) is a terminal, ``json`` otherwise."""
    stream = stream if stream is not None else sys.stdout
    try:
        return "table" if stream.isatty() else "json"
    except AttributeError, ValueError:
        return "json"


# --- JSON


def _enc_hook(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, pl.DataFrame):
        return obj.to_dicts()
    if isinstance(obj, pl.Series):
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

    Timezone-aware datetime columns are rendered as ISO 8601 strings by polars
    itself: converting them to Python objects would need ``zoneinfo`` data,
    which is missing on Windows unless ``tzdata`` is installed.
    """
    if isinstance(data, pl.DataFrame):
        aware = [
            pl.col(name).dt.to_string(ISO_FORMAT)
            for name, dtype in data.schema.items()
            if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None
        ]
        return (data.with_columns(aware) if aware else data).to_dicts()
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
) -> Table:
    """Build a rich table from rows; ``columns`` restricts and orders the columns shown."""
    if columns is None:
        seen: dict[str, None] = {}
        for row in rows[:100]:
            seen.update(dict.fromkeys(row))
        columns = list(seen)
    table = Table(title=title, title_justify="left")
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*(_cell(row.get(col), max_len) for col in columns))
    return table


def mapping_tables(doc: Mapping[str, Any], *, title: str | None = None) -> list[Table]:
    """Render a document: scalars as a key/value table, nested values as extra tables."""
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


# --- Output


@dataclasses.dataclass
class Output:
    """Writes command results in the selected mode.

    ``stdout``/``stderr`` default to the *current* ``sys.stdout``/``sys.stderr``
    at write time, so redirection and test capture work.
    """

    mode: OutputMode = "json"
    quiet: bool = False
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

    def console(self) -> Console:
        """Return a rich console on stdout; plain text (no ANSI) unless stdout is a terminal."""
        tty = self.is_tty()
        return Console(
            file=self.out,
            force_terminal=tty,
            no_color=not tty,
            highlight=False,
            soft_wrap=False,
            width=None if tty else 160,
        )

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
            console.print(rows_table(rows_list, _present_columns(rows_list, columns), title=title))
            meta = {k: v for k, v in doc.items() if k != items and _is_scalar(v)}
            if meta:
                self.info("  ".join(f"{k}={_cell(v)}" for k, v in meta.items()))
            return
        for tbl in mapping_tables(doc, title=title):
            console.print(tbl)

    def error(self, err: CliError) -> int:
        """Report ``err`` (stdout envelope in json/jsonl mode, stderr in table mode)."""
        if self.mode in ("json", "jsonl"):
            envelope = {"schema": "error.v1", "error": {"code": err.code, "message": err.message}}
            self.write_line(encode_json(envelope))
            self.out.flush()
        else:
            self.err.write(f"error: {err.message} [{err.code}]\n")
            self.err.flush()
        return int(err.exit_code)
