"""Format registry: built-in formats, entry-point discovery and sniffing."""

from __future__ import annotations

from collections.abc import Sequence

from atlas_log_analyzer.core.formats.base import LogFormat

__all__ = ["detect", "get", "names", "register"]

ENTRY_POINT_GROUP = "atlas_log_analyzer.formats"


class UnknownFormatError(LookupError):
    """Raised by :func:`get` for an unknown format name."""


def register(fmt: LogFormat) -> None:
    """Register a format instance under ``fmt.name`` (replaces an existing one)."""
    raise NotImplementedError


def get(name: str) -> LogFormat:
    """Return the format registered under ``name``; raise :class:`UnknownFormatError` otherwise."""
    raise NotImplementedError


def names() -> list[str]:
    """Sorted names of all known formats (built-in + entry points + registered)."""
    raise NotImplementedError


def detect(lines: Sequence[str]) -> LogFormat | None:
    """Pick the format with the highest ``sniff`` confidence (> 0) for the given first lines."""
    raise NotImplementedError
