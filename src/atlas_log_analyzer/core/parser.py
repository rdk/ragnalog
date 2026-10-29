"""Multi-line entry assembly: lines → :class:`LogEntry` objects."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.models import LogEntry
from atlas_log_analyzer.core.reader import Line, SourceFile

__all__ = ["parse", "parse_file", "parse_lines", "parse_sources"]


def parse_lines(lines: Iterable[Line], fmt: LogFormat, path: str) -> Iterator[LogEntry]:
    """Assemble entries from already-read lines (first-line / continuation state machine).

    Lines before the first header become one entry with ``parse_error``.
    A line for which ``fmt.is_header`` is true but ``fmt.parse_header`` returns
    ``None`` becomes an entry with ``parse_error`` and is *not* merged into the
    previous entry.
    """
    raise NotImplementedError


def parse_file(path: Path | str, fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from one file."""
    raise NotImplementedError


def parse_sources(sources: Iterable[SourceFile], fmt: LogFormat) -> Iterator[LogEntry]:
    """Stream entries from several sources in the given order."""
    raise NotImplementedError


def parse(paths: Iterable[Path | str], fmt: LogFormat | None = None) -> Iterator[LogEntry]:
    """Convenience: discover sources, detect the format when ``None``, stream entries."""
    raise NotImplementedError
