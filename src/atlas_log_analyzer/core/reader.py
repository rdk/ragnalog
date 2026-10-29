"""Source discovery and line reading."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Line", "SourceFile", "iter_lines", "iter_sources"]


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One input file with identity information for the manifest."""

    path: Path
    size: int
    xxh3: str
    """xxh3-64 hex digest of the file content."""
    rotation: int
    """``0`` for ``x.log``, ``N`` for ``x.log.N``; used to order rotated files oldest-first."""


@dataclass(frozen=True, slots=True)
class Line:
    """A decoded line with provenance."""

    number: int
    """1-based."""
    offset: int
    """Byte offset of the line start."""
    text: str
    """Without trailing ``\\r\\n``/``\\n``."""


def iter_sources(paths: Iterable[Path | str], *, glob: str = "*.log*") -> list[SourceFile]:
    """Expand files and directories into sources, ordered oldest-first.

    Directories are searched (non-recursively) with ``glob``. Rotated files
    (``x.log.N``) sort by descending ``N`` so that ``x.log`` comes last.
    """
    raise NotImplementedError


def iter_lines(path: Path | str, *, encoding: str = "utf-8") -> Iterator[Line]:
    """Stream lines from a file, decoding with ``errors="replace"``."""
    raise NotImplementedError
