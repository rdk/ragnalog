"""Multi-line entry assembly: lines → :class:`LogEntry` objects."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path

import xxhash

from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.models import LogEntry, SourceRef
from atlas_log_analyzer.core.reader import Line, SourceFile, iter_raw_lines, iter_sources

__all__ = ["ORPHAN_ERROR", "parse", "parse_file", "parse_lines", "parse_sources"]

ORPHAN_ERROR = "continuation lines before the first header"
"""``parse_error`` of the entry made of lines that precede the first header of a source."""

DETECT_LINES = 200
"""Number of leading lines handed to :func:`formats.detect`."""


def _assemble(
    lines: Iterable[tuple[int, int, str]], fmt: LogFormat, path: str
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
    for number, offset, text in lines:
        if not is_header(text):
            if header_line is None:
                # leading continuation lines: the first one stands in for the header
                header_line = text
                source = SourceRef(path, number, offset)
                orphan = True
            else:
                append(text)
            continue
        if header_line is not None:
            entry = build(header, header_line, cont, source)
            yield dataclasses.replace(entry, parse_error=ORPHAN_ERROR) if orphan else entry
            orphan = False
            cont = []
            append = cont.append
        header = parse_header(text)
        header_line = text
        source = SourceRef(path, number, offset)
    if header_line is not None:
        entry = build(header, header_line, cont, source)
        yield dataclasses.replace(entry, parse_error=ORPHAN_ERROR) if orphan else entry


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


def parse_lines(lines: Iterable[Line], fmt: LogFormat, path: str) -> Iterator[LogEntry]:
    """Assemble entries from already-read lines (first-line / continuation state machine).

    Lines before the first header become one entry with ``parse_error``.
    A line for which ``fmt.is_header`` is true but ``fmt.parse_header`` returns
    ``None`` becomes an entry with ``parse_error`` and is *not* merged into the
    previous entry. Entries after the first in a millisecond get ids re-hashed
    with their ordinal (see :func:`_distinct_ids`).
    """
    return _distinct_ids(_assemble(((ln.number, ln.offset, ln.text) for ln in lines), fmt, path))


def parse_file(path: Path | str, fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from one file."""
    return _distinct_ids(_assemble(iter_raw_lines(path), fmt, str(path)))


def parse_sources(sources: Iterable[SourceFile], fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from several sources in the given order."""
    for source in sources:
        yield from parse_file(source.path, fmt)


def parse(paths: Iterable[Path | str], fmt: LogFormat | None = None) -> Iterator[LogEntry]:
    """Discover sources, detect the format when ``None`` and stream entries (convenience).

    Raises:
        ValueError: When ``fmt`` is ``None`` and no format recognises the first source.

    """
    sources = iter_sources(paths)
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
