"""Immutable core models shared by every layer.

All models are frozen, slotted dataclasses: hashable, cheap to construct and
safe to pass between threads/processes. They carry no behaviour that does I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from types import MappingProxyType

__all__ = ["EMPTY_CONTEXT", "ExceptionInfo", "Frame", "LogEntry", "LogLevel", "SourceRef"]


class LogLevel(IntEnum):
    """Severity levels, ordered so that ``level >= LogLevel.WARN`` works."""

    TRACE = 10
    DEBUG = 20
    INFO = 30
    WARN = 40
    ERROR = 50
    FATAL = 60
    UNKNOWN = 0

    @classmethod
    def parse(cls, text: str) -> LogLevel:
        """Map a level token (``WARN``, ``WARNING``, ``SEVERE`` …) to a level."""
        return _LEVEL_ALIASES.get(text.upper(), cls.UNKNOWN)


_LEVEL_ALIASES: dict[str, LogLevel] = {
    "TRACE": LogLevel.TRACE,
    "FINEST": LogLevel.TRACE,
    "DEBUG": LogLevel.DEBUG,
    "FINE": LogLevel.DEBUG,
    "INFO": LogLevel.INFO,
    "WARN": LogLevel.WARN,
    "WARNING": LogLevel.WARN,
    "ERROR": LogLevel.ERROR,
    "SEVERE": LogLevel.ERROR,
    "FATAL": LogLevel.FATAL,
}


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where an entry came from.

    ``line`` is the 1-based line number of the header line, ``offset`` the byte
    offset of that line within the file.
    """

    path: str
    line: int
    offset: int


@dataclass(frozen=True, slots=True)
class Frame:
    """One ``at …`` line of a Java stack trace."""

    location: str
    """Fully qualified ``package.Class.method``."""
    file: str | None = None
    line: int | None = None
    module: str | None = None
    """Module/classloader prefix such as ``java.base`` when present."""


@dataclass(frozen=True, slots=True)
class ExceptionInfo:
    """One element of an exception chain (the outer exception or a ``Caused by:``)."""

    type: str
    """Fully qualified exception class name."""
    message: str | None
    frames: tuple[Frame, ...] = ()
    omitted_frames: int = 0
    """Value of a trailing ``... N more`` line."""


EMPTY_CONTEXT: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One logical log entry: a header line plus its continuation lines.

    ``context`` holds format-specific header fields (user, request id, …) under
    the well-known keys defined in :mod:`atlas_log_analyzer.core.formats.base`.
    ``raw`` is the exact original text, header and continuation included,
    without the trailing newline.
    """

    id: str
    """xxh3-64 hex digest of ``(format, timestamp, thread, logger, raw)``, re-hashed with the
    entry's ordinal within its run of same-millisecond entries when that is non-zero, and again
    when the id was issued already in that millisecond; stable across runs."""
    format: str
    timestamp: datetime
    """Timezone-aware."""
    level: LogLevel
    thread: str
    logger: str
    message: str
    """First line of the message (header remainder)."""
    body: str = ""
    """Continuation lines joined with ``\\n``; empty when the entry is a single line."""
    exceptions: tuple[ExceptionInfo, ...] = ()
    """Parsed exception chain, outermost first; empty when none was found in ``body``."""
    context: Mapping[str, str] = EMPTY_CONTEXT
    source: SourceRef = field(default=SourceRef("", 0, 0))
    raw: str = ""
    parse_error: str | None = None
    """Set when the header was not recognised; ``message`` then holds the raw header line."""

    @property
    def exception_type(self) -> str | None:
        """Type of the outermost exception, if any."""
        return self.exceptions[0].type if self.exceptions else None

    @property
    def root_cause(self) -> ExceptionInfo | None:
        """Innermost exception of the chain, if any."""
        return self.exceptions[-1] if self.exceptions else None
