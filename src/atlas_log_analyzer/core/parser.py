"""Multi-line entry assembly: lines → :class:`LogEntry` objects."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path

import xxhash

from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.models import LogEntry, SourceRef
from atlas_log_analyzer.core.reader import Line, SourceFile, discover_sources, iter_raw_lines

__all__ = [
    "MAX_ENTRY_CHARS",
    "MAX_ENTRY_LINES",
    "ORPHAN_ERROR",
    "TRUNCATED_ERROR",
    "parse",
    "parse_file",
    "parse_lines",
    "parse_sources",
]

ORPHAN_ERROR = "continuation lines before the first header"
"""``parse_error`` of the entry made of lines that precede the first header of a source."""
TRUNCATED_ERROR = "entry truncated: {lines} lines, {bytes} bytes"
"""``parse_error`` of an entry cut at :data:`MAX_ENTRY_CHARS` or :data:`MAX_ENTRY_LINES`;
``lines`` and ``bytes`` (UTF-8, lines joined with ``\\n``) describe the whole entry."""
MAX_ENTRY_CHARS = 4 << 20
"""Default cap of the text kept per entry: 4 Mi characters, header line included."""
MAX_ENTRY_LINES = 100_000
"""Default cap of the lines kept per entry, header line included."""

DETECT_LINES = 200
"""Number of leading lines handed to :func:`formats.detect`."""


def _utf8_len(text: str) -> int:
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))


def _mark(
    entry: LogEntry, orphan: bool, cut: list[int] | None, header_line: str, cont: list[str]
) -> LogEntry:
    """Set ``parse_error`` of an orphan or truncated entry; a truncated one gets a new id."""
    error = ORPHAN_ERROR if orphan else entry.parse_error
    if cut is None:
        return dataclasses.replace(entry, parse_error=error)
    lines = 1 + len(cont) + cut[0]
    size = _utf8_len(header_line) + sum(_utf8_len(t) + 1 for t in cont) + cut[1]
    note = TRUNCATED_ERROR.format(lines=lines, bytes=size)
    return dataclasses.replace(
        entry,
        # the kept text alone could match another truncated entry: hash its full size too
        id=xxhash.xxh3_64_hexdigest(f"{entry.id}:{lines}:{size}".encode()),
        parse_error=f"{error}; {note}" if error else note,
    )


def _assemble(
    lines: Iterable[tuple[int, int, str]],
    fmt: LogFormat,
    path: str,
    *,
    max_chars: int = MAX_ENTRY_CHARS,
    max_lines: int = MAX_ENTRY_LINES,
) -> Iterator[LogEntry]:
    is_header = fmt.is_header
    parse_header = fmt.parse_header
    build = fmt.build
    header = None
    header_line: str | None = None
    source = SourceRef(path, 0, 0)
    orphan = False
    cont: list[str] = []
    append = cont.append
    room = 0  # characters the entry may still keep
    left = 0  # continuation lines the entry may still keep
    cut: list[int] | None = None  # [lines, bytes] not kept, once the entry hit a cap
    for number, offset, text in lines:
        if not is_header(text):
            if header_line is None:
                # leading continuation lines: the first one stands in for the header
                header_line = text
                source = SourceRef(path, number, offset)
                orphan = True
                room = max_chars - len(text)
                left = max_lines - 1
            elif cut is None:
                room -= len(text) + 1
                left -= 1
                if room >= 0 and left >= 0:
                    append(text)
                else:
                    cut = [1, _utf8_len(text) + 1]
            else:
                cut[0] += 1
                cut[1] += _utf8_len(text) + 1
            continue
        if header_line is not None:
            entry = build(header, header_line, cont, source)
            yield (
                entry
                if not orphan and cut is None
                else _mark(entry, orphan, cut, header_line, cont)
            )
            orphan = False
            cut = None
            cont = []
            append = cont.append
        header = parse_header(text)
        header_line = text
        source = SourceRef(path, number, offset)
        room = max_chars - len(text)
        left = max_lines - 1
    if header_line is not None:
        entry = build(header, header_line, cont, source)
        yield entry if not orphan and cut is None else _mark(entry, orphan, cut, header_line, cont)


def _distinct_ids(entries: Iterable[LogEntry]) -> Iterator[LogEntry]:
    """Make ids unique within one millisecond without remembering the entries.

    Entry ids hash ``(format, timestamp, thread, logger, raw)``, so a line that
    Jira writes twice in the same millisecond (separator banners, repeated
    warnings) would collapse into one entry when the store deduplicates. Every
    entry after the first in a millisecond is re-hashed with its ordinal ``k``
    in that millisecond: ``xxh3(id, k)``. The ordinal depends only on the file
    content before the entry, so the same entry keeps the same id when the file
    shows up again in another snapshot, and the state is one counter.
    """
    last_ts = None
    k = 0
    for entry in entries:
        if entry.timestamp != last_ts:
            last_ts = entry.timestamp
            k = 0
        else:
            k += 1
            entry = dataclasses.replace(
                entry, id=xxhash.xxh3_64_hexdigest(f"{entry.id}{k}".encode())
            )
        yield entry


def parse_lines(
    lines: Iterable[Line],
    fmt: LogFormat,
    path: str,
    *,
    max_entry_chars: int = MAX_ENTRY_CHARS,
    max_entry_lines: int = MAX_ENTRY_LINES,
) -> Iterator[LogEntry]:
    """Assemble entries from already-read lines (first-line / continuation state machine).

    Lines before the first header become one entry with ``parse_error``.
    A line for which ``fmt.is_header`` is true but ``fmt.parse_header`` returns
    ``None`` becomes an entry with ``parse_error`` and is *not* merged into the
    previous entry. Entries after the first in a millisecond get ids re-hashed
    with their ordinal (see :func:`_distinct_ids`).

    An entry keeps at most ``max_entry_lines`` lines and ``max_entry_chars``
    characters (header included, whole lines only); the lines after the cap are
    counted, not stored, and ``parse_error`` says ``entry truncated: N lines, M
    bytes`` (:data:`TRUNCATED_ERROR`). The id of a truncated entry also hashes N
    and M, so it stays deterministic.
    """
    rows = ((ln.number, ln.offset, ln.text) for ln in lines)
    return _distinct_ids(
        _assemble(rows, fmt, path, max_chars=max_entry_chars, max_lines=max_entry_lines)
    )


def parse_file(
    path: Path | str,
    fmt: LogFormat,
    *,
    max_entry_chars: int = MAX_ENTRY_CHARS,
    max_entry_lines: int = MAX_ENTRY_LINES,
) -> Iterator[LogEntry]:
    """Stream entries from one file (lines over 1 MiB are cut, see :func:`iter_raw_lines`)."""
    return _distinct_ids(
        _assemble(
            iter_raw_lines(path),
            fmt,
            str(path),
            max_chars=max_entry_chars,
            max_lines=max_entry_lines,
        )
    )


def parse_sources(sources: Iterable[SourceFile], fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from several sources in the given order (default caps)."""
    for source in sources:
        yield from parse_file(source.path, fmt)


def parse(paths: Iterable[Path | str], fmt: LogFormat | None = None) -> Iterator[LogEntry]:
    """Discover sources, detect the format when ``None`` and stream entries (convenience).

    Raises:
        ValueError: When ``fmt`` is ``None`` and no format recognises the first source.

    """
    sources = discover_sources(paths)
    if fmt is None:
        from atlas_log_analyzer.core.formats.registry import detect

        sample: list[str] = []
        for source in sources:
            raw_lines = iter_raw_lines(source.path)
            try:
                sample = [text for _, _, text in islice(raw_lines, DETECT_LINES)]
            finally:
                raw_lines.close()
            if sample:
                break
        detected = detect(sample)
        if detected is None:
            raise ValueError("could not detect the log format; pass one explicitly")
        fmt = detected
    return parse_sources(sources, fmt)
