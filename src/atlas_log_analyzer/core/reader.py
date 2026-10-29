"""Source discovery and line reading."""

from __future__ import annotations

import re
from collections.abc import Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import xxhash

__all__ = ["Line", "SourceFile", "file_digest", "iter_lines", "iter_raw_lines", "iter_sources"]

_ROTATION_RE = re.compile(r"^(?P<base>.+)\.(?P<n>\d+)$")
_CHUNK = 1 << 18


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


def iter_sources(paths: Iterable[Path | str], *, glob: str = "*.log*") -> list[SourceFile]:
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


def iter_raw_lines(path: Path | str, *, encoding: str = "utf-8") -> Generator[tuple[int, int, str]]:
    r"""Like :func:`iter_lines` but yields plain ``(number, offset, text)`` tuples (faster).

    The file is read and decoded in blocks; a block that is not valid in
    ``encoding`` falls back to per-line decoding with ``errors="replace"`` so that
    byte offsets stay exact. ``encoding`` must be ASCII-compatible (``\n`` is
    split on the byte level).
    """
    number = 0
    offset = 0
    tail = b""
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            if tail:
                block = tail + block
            cut = block.rfind(b"\n") + 1
            if cut == 0:
                tail = block
                continue
            tail = block[cut:]
            data = block[:cut]
            try:
                lines = data.decode(encoding).split("\n")
                exact = False
            except UnicodeDecodeError:
                lines = [raw.decode(encoding, "replace") for raw in data.split(b"\n")]
                raw_lens = [len(raw) for raw in data.split(b"\n")]
                exact = True
            lines.pop()  # data ends with "\n"
            for i, text in enumerate(lines):
                number += 1
                if exact:
                    size = raw_lens[i] + 1
                elif text.isascii():
                    size = len(text) + 1
                else:
                    size = len(text.encode(encoding)) + 1
                if text and text[-1] == "\r":
                    text = text[:-1]
                yield number, offset, text
                offset += size
    if tail:
        text = tail.decode(encoding, "replace")
        if text and text[-1] == "\r":
            text = text[:-1]
        yield number + 1, offset, text


def iter_lines(path: Path | str, *, encoding: str = "utf-8") -> Iterator[Line]:
    """Stream lines from a file, decoding with ``errors="replace"``."""
    for number, offset, text in iter_raw_lines(path, encoding=encoding):
        yield Line(number, offset, text)
