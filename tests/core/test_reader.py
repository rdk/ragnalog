from __future__ import annotations

import os
from pathlib import Path

import pytest
import xxhash

from atlas_log_analyzer.core.reader import Line, iter_lines, iter_raw_lines, iter_sources


def _touch(path: Path, content: bytes, mtime: float) -> None:
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))


def test_rotation_order(tmp_path: Path) -> None:
    names = ["x.log", "x.log.1", "x.log.2", "x.log.9", "x.log.10"]
    for i, name in enumerate(names):
        _touch(tmp_path / name, name.encode(), 1_000_000 - i)
    (tmp_path / "notes.txt").write_text("ignored")
    sources = iter_sources([tmp_path])
    assert [s.path.name for s in sources] == ["x.log.10", "x.log.9", "x.log.2", "x.log.1", "x.log"]
    assert [s.rotation for s in sources] == [10, 9, 2, 1, 0]
    first = sources[0]
    assert first.size == len(b"x.log.10")
    assert first.xxh3 == xxhash.xxh3_64_hexdigest(b"x.log.10")


def test_families_and_dedup(tmp_path: Path) -> None:
    _touch(tmp_path / "b.log", b"b", 2000)
    _touch(tmp_path / "a.log", b"a", 3000)
    _touch(tmp_path / "a.log.1", b"a1", 1000)
    sources = iter_sources([tmp_path / "a.log", tmp_path, tmp_path / "a.log.1"])
    assert [s.path.name for s in sources] == ["a.log.1", "a.log", "b.log"]


def test_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        iter_sources([tmp_path / "missing.log"])


def test_iter_lines_offsets_crlf_and_bad_bytes(tmp_path: Path) -> None:
    data = b"first\r\nsecond \xc3\xa9\nbad \xff byte\n\nlast-no-newline"
    path = tmp_path / "x.log"
    path.write_bytes(data)
    lines = list(iter_lines(path))
    assert lines == [
        Line(1, 0, "first"),
        Line(2, 7, "second é"),
        Line(3, 17, "bad � byte"),
        Line(4, 28, ""),
        Line(5, 29, "last-no-newline"),
    ]
    for ln in lines:
        assert data[ln.offset :].startswith(data.split(b"\n")[ln.number - 1][:3])


def test_iter_lines_block_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import atlas_log_analyzer.core.reader as reader

    monkeypatch.setattr(reader, "_CHUNK", 7)
    data = "".join(f"line {i} éé\n" for i in range(50)).encode() + b"\xfe\n tail"
    path = tmp_path / "x.log"
    path.write_bytes(data)
    rows = list(iter_raw_lines(path))
    expected = data.split(b"\n")
    assert len(rows) == len(expected)
    offset = 0
    for (_number, off, text), raw in zip(rows, expected, strict=True):
        assert off == offset
        assert text == raw.decode("utf-8", "replace")
        offset += len(raw) + 1
    assert rows[0][0] == 1 and rows[-1][0] == len(expected)


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_bytes(b"")
    assert list(iter_lines(path)) == []
