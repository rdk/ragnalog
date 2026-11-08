from __future__ import annotations

import os
from pathlib import Path

import pytest
import xxhash

from atlas_log_analyzer.core.reader import (
    Line,
    iter_lines,
    iter_raw_lines,
    iter_sources,
    side_logs,
)


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


def _reference(data: bytes, limit: int) -> list[tuple[int, int, str]]:
    """Expected ``iter_raw_lines`` rows: lines cut to ``limit`` bytes, exact offsets."""
    parts = data.split(b"\n")
    if parts[-1] == b"":
        parts.pop()
    rows, offset = [], 0
    for number, raw in enumerate(parts, 1):
        text = raw[:limit].decode("utf-8", "replace")
        rows.append((number, offset, text[:-1] if text.endswith("\r") else text))
        offset += len(raw) + 1
    return rows


@pytest.mark.parametrize(("chunk", "limit"), [(7, 5), (7, 16), (16, 7), (64, 1000), (5, 1)])
def test_long_lines_are_cut_with_exact_offsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunk: int, limit: int
) -> None:
    import random

    import atlas_log_analyzer.core.reader as reader

    monkeypatch.setattr(reader, "_CHUNK", chunk)
    rnd = random.Random(chunk * 1000 + limit)
    alphabet = [b"a", b"b", b"\xc3\xa9", b"\xff", b"\r", b" "]
    path = tmp_path / "x.log"
    for case in range(40):
        lines = [
            b"".join(rnd.choice(alphabet) for _ in range(rnd.choice([0, 1, 3, 8, 30, 200])))
            for _ in range(rnd.randint(1, 12))
        ]
        data = b"\n".join(lines) + (b"\n" if case % 2 else b"")
        path.write_bytes(data)
        assert list(iter_raw_lines(path, max_line_bytes=limit)) == _reference(data, limit), data


def test_newline_free_file_is_read_in_bounded_memory(tmp_path: Path) -> None:
    import tracemalloc

    path = tmp_path / "blob.log"
    path.write_bytes(b"x" * (8 << 20) + b"\nnext line\n" + b"y" * 3000)
    tracemalloc.start()
    rows = list(iter_raw_lines(path, max_line_bytes=1024))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert rows == [
        (1, 0, "x" * 1024),
        (2, (8 << 20) + 1, "next line"),
        (3, (8 << 20) + 11, "y" * 1024),
    ]
    assert peak < 2 << 20  # one read block, not the 8 MiB line


def test_default_line_cap(tmp_path: Path) -> None:
    from atlas_log_analyzer.core.reader import MAX_LINE_BYTES

    path = tmp_path / "x.log"
    path.write_bytes(b"z" * (MAX_LINE_BYTES + 5) + b"\nok")
    assert [(n, o, len(t)) for n, o, t in iter_raw_lines(path)] == [
        (1, 0, MAX_LINE_BYTES),
        (2, MAX_LINE_BYTES + 6, 2),
    ]
    with pytest.raises(ValueError, match="max_line_bytes"):
        list(iter_raw_lines(path, max_line_bytes=0))


def test_byte_order_mark_is_dropped_from_the_first_line(tmp_path: Path) -> None:
    path = tmp_path / "bom.log"
    path.write_bytes(b"\xef\xbb\xbf2026-10-29 first\r\nsecond\n")
    lines = list(iter_raw_lines(path))
    assert lines == [(1, 0, "2026-10-29 first"), (2, 21, "second")]


@pytest.mark.parametrize("limit", [0, 5, 6, 11, 12, 100])
def test_max_bytes_stops_reading(
    tmp_path: Path, limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_log_analyzer.core import reader

    monkeypatch.setattr(reader, "_CHUNK", 4)  # limits that fall inside and across blocks
    path = tmp_path / "live.log"
    path.write_bytes(b"first\nsecond\nthird\n")
    data = path.read_bytes()[:limit]
    want = [line for line in data.decode().split("\n") if line] if data else []
    assert [text for _, _, text in iter_raw_lines(path, max_bytes=limit)] == want


def test_a_growing_file_is_parsed_up_to_its_hashed_size(tmp_path: Path) -> None:
    from atlas_log_analyzer.core.formats.jira import JiraFormat
    from atlas_log_analyzer.core.parser import parse_sources
    from atlas_log_analyzer.core.reader import discover_sources

    path = tmp_path / "atlassian-jira.log"
    line = "2026-10-29 10:00:0{},000+0100 exec-1 ERROR      [org.acme.A] boom {}\n"
    path.write_text(line.format(0, 1) + line.format(1, 2), encoding="utf-8")
    sources = discover_sources([path])
    with path.open("a", encoding="utf-8") as f:  # the application keeps logging
        f.write(line.format(2, 3) + "\tat org.acme.A.run(A.java:1)\n")
    entries = list(parse_sources(sources, JiraFormat()))
    assert [e.message for e in entries] == ["boom 1", "boom 2"]
    assert entries[-1].body == ""


def test_side_logs_extend_a_main_stem_in_the_same_directory(tmp_path: Path) -> None:
    home, node = tmp_path / "log", tmp_path / "node"
    names = [
        "atlassian-jira.log",
        "atlassian-jira.log.1",
        "atlassian-jira-security.log",
        "atlassian-jira-security.log.2",
        "atlassian-jira-outgoing-mail.log",
        "Atlassian-Jira-Slow-Queries.log",
        "atlassian-jira-2026-11-01.log",  # a dated rotation, not a side log
        "catalina.log",
    ]
    paths = [home / n for n in names]
    paths += [node / "NODE1_atlassian-jira.log", node / "NODE1_atlassian-jira-security.log"]
    paths += [node / "NODE2_atlassian-jira-security.log"]  # no NODE2 main log beside it
    paths += [tmp_path / "other" / "atlassian-jira-security.log"]  # main log elsewhere
    assert {p.relative_to(tmp_path).as_posix() for p in side_logs(paths)} == {
        "log/atlassian-jira-security.log",
        "log/atlassian-jira-security.log.2",
        "log/atlassian-jira-outgoing-mail.log",
        "log/Atlassian-Jira-Slow-Queries.log",
        "node/NODE1_atlassian-jira-security.log",
    }


def test_gzip_rotations_read_decompressed_in_order(tmp_path: Path) -> None:
    """A rotated x.log.N.gz reads like x.log.N: decompressed, ordered by N."""
    import gzip

    from atlas_log_analyzer.core.reader import discover_sources, is_gzip

    text = "line one\nline two\n"
    (tmp_path / "app.log").write_text("newest\n", encoding="utf-8")
    (tmp_path / "app.log.1").write_text("middle\n", encoding="utf-8")
    with gzip.open(tmp_path / "app.log.2.gz", "wb") as f:
        f.write(text.encode())  # bytes: text mode would write CRLF on Windows
    found = discover_sources([tmp_path])
    assert [(s.path.name, s.rotation) for s in found] == [
        ("app.log.2.gz", 2),
        ("app.log.1", 1),
        ("app.log", 0),
    ]
    gz = found[0]
    assert is_gzip(gz.path) and not is_gzip(found[1].path)
    # max_bytes (the compressed size it was hashed at) does not cut a finished gzip file
    lines = list(iter_raw_lines(gz.path, max_bytes=gz.size))
    assert lines == [(1, 0, "line one"), (2, 9, "line two")]
