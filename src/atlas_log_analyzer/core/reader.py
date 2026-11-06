"""Source discovery and line reading."""

from __future__ import annotations

import re
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import xxhash

__all__ = [
    "LOG_GLOB",
    "MAX_LINE_BYTES",
    "Line",
    "SourceFile",
    "discover_sources",
    "file_digest",
    "iter_lines",
    "iter_raw_lines",
    "iter_sources",
]

_ROTATION_RE = re.compile(r"^(?P<base>.+)\.(?P<n>\d+)$")
LOG_GLOB = "*.log*"
"""Names of the log files :func:`discover_sources` reads from a directory."""
_CHUNK = 1 << 18
MAX_LINE_BYTES = 1 << 20
"""Default cap of one line (1 MiB); :func:`iter_raw_lines` skips the rest of a longer line."""


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


def file_digest(path: Path | str) -> tuple[int, str]:
    """Return ``(size, xxh3-64 hex)`` of a file's content."""
    h = xxhash.xxh3_64()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def _rotation(path: Path) -> tuple[str, int]:
    m = _ROTATION_RE.match(path.name)
    if m is None:
        return path.name, 0
    return m.group("base"), int(m.group("n"))


def discover_sources(paths: Iterable[Path | str], *, glob: str = LOG_GLOB) -> list[SourceFile]:
    """Expand files and directories into sources, ordered oldest-first.

    Directories are searched (non-recursively) with ``glob``. Rotated files
    (``x.log.N``) sort by descending ``N`` so that ``x.log`` comes last. Files
    of different rotation families are ordered by the modification time of
    their oldest member, then by name. Duplicate paths are listed once.

    Raises:
        FileNotFoundError: When a given path does not exist.

    """
    files: dict[Path, None] = {}
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for child in sorted(path.glob(glob)):
                if child.is_file():
                    files[child.resolve()] = None
        elif path.is_file():
            files[path.resolve()] = None
        else:
            raise FileNotFoundError(f"no such file or directory: {path}")

    families: dict[tuple[Path, str], list[tuple[int, Path]]] = {}
    for path in files:
        base, n = _rotation(path)
        families.setdefault((path.parent, base), []).append((n, path))

    def family_key(item: tuple[tuple[Path, str], list[tuple[int, Path]]]) -> tuple[float, str]:
        (parent, base), members = item
        oldest = max(members)[1]
        return oldest.stat().st_mtime, str(parent / base)

    ordered: list[SourceFile] = []
    for _, members in sorted(families.items(), key=family_key):
        for n, path in sorted(members, key=lambda m: -m[0]):
            size, digest = file_digest(path)
            ordered.append(SourceFile(path, size, digest, n))
    return ordered


iter_sources = discover_sources
"""Former name of :func:`discover_sources` (it returns a list, not an iterator)."""


def _decode_line(raw: bytes, encoding: str) -> str:
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode(encoding, "replace")
    return text[:-1] if text and text[-1] == "\r" else text


def iter_raw_lines(
    path: Path | str, *, encoding: str = "utf-8", max_line_bytes: int = MAX_LINE_BYTES
) -> Generator[tuple[int, int, str]]:
    r"""Like :func:`iter_lines` but yields plain ``(number, offset, text)`` tuples (faster).

    A UTF-8 byte order mark is dropped from the first line's text (offsets still count
    it), so a file saved with one parses like any other.

    The file is read and decoded in blocks; a block that is not valid in
    ``encoding`` falls back to per-line decoding with ``errors="replace"`` so that
    byte offsets stay exact. ``encoding`` must be ASCII-compatible (``\n`` is
    split on the byte level).

    A line longer than ``max_line_bytes`` is cut to its first ``max_line_bytes``
    bytes and the rest, up to the next newline, is skipped, so memory stays
    bounded and time linear on files without line breaks (minified JSON, binary
    dumps). Line numbers and offsets stay exact.

    Raises:
        ValueError: ``max_line_bytes`` is not positive.

    """
    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be > 0")
    lines = _raw_lines(path, encoding, max_line_bytes)
    first = next(lines, None)
    if first is None:
        return
    if first[2].startswith("﻿"):
        first = (first[0], first[1], first[2][1:])
    yield first
    yield from lines


def _raw_lines(
    path: Path | str, encoding: str, max_line_bytes: int
) -> Generator[tuple[int, int, str]]:
    limit = max_line_bytes
    over = limit + 1  # size of a line with its "\n" above which the line is cut
    number = 0
    offset = 0
    # start of a line that continues past the current block: at most `limit` bytes are
    # kept in `head`, the `skipped` bytes after them are only counted
    head: list[bytes] = []
    kept = 0
    skipped = 0
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            cut = block.rfind(b"\n") + 1
            if cut == 0:
                room = max(0, limit - kept)
                if room:
                    head.append(block[:room])
                    kept += min(room, len(block))
                skipped += max(0, len(block) - room)
                continue
            start = 0
            if head:
                # the carried line ends at the first "\n" of this block
                start = block.find(b"\n") + 1
                raw = b"".join(head) + block[: start - 1]
                number += 1
                yield number, offset, _decode_line(raw[:limit], encoding)
                offset += len(raw) + skipped + 1
                head, kept, skipped = [], 0, 0
            if start < cut:
                data = block[start:cut]
                raws: list[bytes] | None = None
                try:
                    lines = data.decode(encoding).split("\n")
                except UnicodeDecodeError:
                    raws = data.split(b"\n")
                    lines = [raw.decode(encoding, "replace") for raw in raws]
                lines.pop()  # data ends with "\n"
                for i, text in enumerate(lines):
                    number += 1
                    if raws is not None:
                        size = len(raws[i]) + 1
                    elif text.isascii():
                        size = len(text) + 1
                    else:
                        size = len(text.encode(encoding)) + 1
                    if size > over:
                        long = raws[i] if raws is not None else text.encode(encoding)
                        text = _decode_line(long[:limit], encoding)
                    elif text and text[-1] == "\r":
                        text = text[:-1]
                    yield number, offset, text
                    offset += size
            rest = block[cut:]
            if rest:
                head = [rest[:limit]]
                kept = len(head[0])
                skipped = len(rest) - kept
    if head:
        yield number + 1, offset, _decode_line(b"".join(head), encoding)


def iter_lines(path: Path | str, *, encoding: str = "utf-8") -> Iterator[Line]:
    """Stream lines from a file, decoding with ``errors="replace"``."""
    for number, offset, text in iter_raw_lines(path, encoding=encoding):
        yield Line(number, offset, text)
