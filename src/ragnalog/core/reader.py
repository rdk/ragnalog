"""Source discovery and line reading."""

from __future__ import annotations

import gzip
import re
import zlib
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import xxhash

__all__ = [
    "LOG_GLOB",
    "MAX_LINE_BYTES",
    "DamagedFileError",
    "Line",
    "SourceFile",
    "discover_sources",
    "file_digest",
    "is_gzip",
    "iter_lines",
    "iter_raw_lines",
    "iter_sources",
    "side_logs",
]

_ROTATION_RE = re.compile(r"^(?P<base>.+)\.(?P<n>\d+)$")
_STEM_RE = re.compile(r"^(?P<stem>.+?)\.log", re.IGNORECASE)
_DATE_SUFFIX_MIN = 100_000
"""A rotation suffix this large (six digits or more) is a date stamp, not a counter."""
GZIP_MAGIC = bytes((0x1F, 0x8B))
"""First two bytes of a gzip file."""
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
    """Bytes of content (decompressed for a gzip file)."""
    xxh3: str
    """xxh3-64 hex digest of the file content (decompressed for a gzip file)."""
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
    """Return ``(size, xxh3-64 hex)`` of a file's content.

    A gzip file counts by its decompressed content, so ``x.log.3.gz`` is the same
    source as the ``x.log.3`` it was made from (a set ingested both ways is one set).

    Raises:
        DamagedFileError: A gzip file that is truncated or corrupt.

    """
    h = xxhash.xxh3_64()
    size = 0
    gz = is_gzip(path)
    try:
        with gzip.open(path, "rb") if gz else open(path, "rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
                size += len(chunk)
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise DamagedFileError(f"damaged gzip file {Path(path).name}: {exc}") from exc
    return size, h.hexdigest()


def is_gzip(path: Path | str) -> bool:
    """Tell whether ``path`` is a gzip file (``x.log.3.gz``): by its first two bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == GZIP_MAGIC
    except OSError:
        return False


def _rotation(path: Path) -> tuple[str, int]:
    name = path.name[:-3] if path.name.lower().endswith(".gz") else path.name
    m = _ROTATION_RE.match(name)
    if m is None:
        return name, 0
    return m.group("base"), int(m.group("n"))


def discover_sources(paths: Iterable[Path | str], *, glob: str = LOG_GLOB) -> list[SourceFile]:
    """Expand files and directories into sources, ordered oldest-first.

    Directories are searched (non-recursively) with ``glob``. Rotated files
    (``x.log.N``) sort by descending ``N`` so that ``x.log`` comes last; a date stamp
    (``x.log.20261101``, six digits or more) sorts ascending, oldest first. Files
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

    def age(member: tuple[int, Path]) -> tuple[int, int]:
        # a date stamp (x.log.20261101, 6+ digits) counts up with time, a counter down
        n = member[0]
        return (0, n) if n >= _DATE_SUFFIX_MIN else (1, -n)

    def family_key(item: tuple[tuple[Path, str], list[tuple[int, Path]]]) -> tuple[float, str]:
        (parent, base), members = item
        oldest = min(members, key=age)[1]
        return oldest.stat().st_mtime, str(parent / base)

    ordered: list[SourceFile] = []
    for _, members in sorted(families.items(), key=family_key):
        for n, path in sorted(members, key=age):
            size, digest = file_digest(path)
            ordered.append(SourceFile(path, size, digest, n))
    return ordered


iter_sources = discover_sources
"""Former name of :func:`discover_sources` (it returns a list, not an iterator)."""


def _stem(path: Path) -> str | None:
    m = _STEM_RE.match(path.name)
    return m.group("stem").lower() if m else None


def side_logs(paths: Iterable[Path]) -> set[Path]:
    """Return the side logs among ``paths``: ``<stem>-<name>.log*`` next to ``<stem>.log*``.

    A Jira home's ``log/`` directory holds ``atlassian-jira-security.log``,
    ``-outgoing-mail.log`` or ``-slow-queries.log`` beside the main
    ``atlassian-jira.log`` family, in the same header layout. A file is a side log
    when another file in the same directory has the stem it extends with ``-`` and
    a part that does not start with a digit (``app-2026-11-01.log`` is a dated
    rotation, not a side log). The stem is the name up to its first ``.log``,
    compared case-insensitively; a ``<NODE>_`` prefix is part of it, so each node
    keeps its own main family.
    """
    stems: dict[Path, set[str]] = {}
    named: list[tuple[Path, str]] = []
    for path in paths:
        stem = _stem(path)
        if stem is not None:
            stems.setdefault(path.parent, set()).add(stem)
            named.append((path, stem))
    side: set[Path] = set()
    for path, stem in named:
        mains = stems[path.parent]
        for i, char in enumerate(stem):
            if (
                char == "-"
                and stem[:i] in mains
                and i + 1 < len(stem)
                and not stem[i + 1].isdigit()
            ):
                side.add(path)
                break
    return side


def _decode_line(raw: bytes, encoding: str) -> str:
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode(encoding, "replace")
    return text[:-1] if text and text[-1] == "\r" else text


def iter_raw_lines(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    max_line_bytes: int = MAX_LINE_BYTES,
    max_bytes: int | None = None,
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

    With ``max_bytes`` only the file's first ``max_bytes`` bytes are read, so a log
    that is still being written parses exactly as far as it was hashed
    (:attr:`SourceFile.size`); a line cut by that limit ends there.

    A gzip file (a rotated ``x.log.3.gz``) is read decompressed, whole (``max_bytes``
    counts compressed bytes, and a compressed log is finished); offsets count the
    decompressed bytes.

    Raises:
        ValueError: ``max_line_bytes`` is not positive.

    """
    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be > 0")
    lines = _raw_lines(path, encoding, max_line_bytes, max_bytes)
    first = next(lines, None)
    if first is None:
        return
    if first[2].startswith("﻿"):
        first = (first[0], first[1], first[2][1:])
    yield first
    yield from lines


class DamagedFileError(ValueError):
    """A gzip file that is truncated or corrupt (a copy cut short): named, not a crash."""


def _raw_lines(
    path: Path | str, encoding: str, max_line_bytes: int, max_bytes: int | None
) -> Generator[tuple[int, int, str]]:
    try:
        yield from _read_lines(path, encoding, max_line_bytes, max_bytes)
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        # the name only: a web upload's spool path is not the user's (nor to be shown)
        raise DamagedFileError(f"damaged gzip file {Path(path).name}: {exc}") from exc


def _read_lines(
    path: Path | str, encoding: str, max_line_bytes: int, max_bytes: int | None
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
    gz = is_gzip(path)
    left = -1 if max_bytes is None or gz else max(0, max_bytes)
    with gzip.open(path, "rb") if gz else open(path, "rb") as f:
        while left and (block := f.read(_CHUNK if left < 0 else min(_CHUNK, left))):
            if left > 0:
                left -= len(block)
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
