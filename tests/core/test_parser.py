from __future__ import annotations

import tracemalloc
from collections import Counter
from pathlib import Path

import pytest

from atlas_log_analyzer.core.formats.base import CTX_PREAMBLE, CTX_USER
from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.models import LogEntry, LogLevel
from atlas_log_analyzer.core.parser import (
    ORPHAN_ERROR,
    TRUNCATED_ERROR,
    parse,
    parse_file,
    parse_lines,
)
from atlas_log_analyzer.core.reader import iter_lines, iter_sources

from .conftest import EDGE_CASES, SAMPLE

HEADER = "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      [x.Y] message {}"


def test_sample_counts(jira: JiraFormat) -> None:
    entries = list(parse_file(SAMPLE, jira))
    assert len(entries) == 45  # 47 lines start an entry; two banners joined the next entry
    assert [e for e in entries if e.parse_error] == []
    assert Counter(e.level for e in entries) == {
        LogLevel.WARN: 18,
        LogLevel.INFO: 15,
        LogLevel.ERROR: 11,
        LogLevel.DEBUG: 1,
    }
    types = Counter(e.exception_type for e in entries if e.exceptions)
    assert types["org.codehaus.groovy.control.MultipleCompilationErrorsException"] == 3
    assert types["org.eclipse.jgit.errors.TransportException"] == 3
    dispatch = next(e for e in entries if e.logger.endswith("AsynchronousAbleEventDispatcher"))
    assert [x.type.rsplit(".", 1)[-1] for x in dispatch.exceptions] == [
        "RuntimeException",
        "InvalidPropertyException",
        "InvocationTargetException",
        "NullPointerException",
    ]
    assert [x.omitted_frames for x in dispatch.exceptions][1:] == [9, 20, 23]
    # every line of the file is accounted for exactly once (a preamble is header lines)
    total_lines = sum(1 for _ in iter_lines(SAMPLE))
    preambles = [e.context[CTX_PREAMBLE] for e in entries if CTX_PREAMBLE in e.context]
    assert len(preambles) == 2 and not any("\n" in p for p in preambles)
    lines = sum(1 + (e.body.count("\n") + 1 if e.body else 0) for e in entries)
    assert lines + len(preambles) == total_lines
    assert entries[0].message == ""
    assert entries[0].body.splitlines()[2] == "    ROLLOVER"
    assert entries[0].context[CTX_USER] == "user1"
    assert len({e.id for e in entries}) == len(entries)


def test_edge_cases(jira: JiraFormat) -> None:
    entries = list(parse_file(EDGE_CASES, jira))
    assert len(entries) == 15
    assert [e for e in entries if e.parse_error] == []
    dump = entries[13]
    assert dump.message == "Mail queue dump follows"
    assert dump.body.startswith("2026-10-28 09:59:59,123+0100 Sending mailitem queued")
    assert CTX_USER not in dump.context
    assert entries[14].message == ""
    assert entries[14].body == ""
    lines = [s.line for e in entries for s in [e.source]]
    assert lines == sorted(lines)
    assert entries[1].source.line == 8


def test_source_refs(jira: JiraFormat) -> None:
    data = EDGE_CASES.read_bytes()
    for entry in parse_file(EDGE_CASES, jira):
        assert entry.source.path == str(EDGE_CASES)
        assert data[entry.source.offset :].startswith(entry.raw.encode("utf-8"))


def test_orphans_and_bad_headers(tmp_path: Path, jira: JiraFormat) -> None:
    path = tmp_path / "x.log"
    path.write_text(
        "\n".join(
            [
                "\tat truncated.Frame.x(X.java:1)",
                "\t... 3 more",
                HEADER.format(1),
                "continuation",
                HEADER.format(2),
            ]
        ),
        encoding="utf-8",
    )
    entries = list(parse_file(path, jira))
    assert len(entries) == 3
    assert entries[0].parse_error == ORPHAN_ERROR
    assert entries[0].message == "\tat truncated.Frame.x(X.java:1)"
    assert entries[0].body == "\t... 3 more"
    assert entries[1].body == "continuation"
    assert entries[2].message == "message 2"


class _Strict(JiraFormat):
    """is_header on timestamp only, parse_header stricter: exercises the parse_error path."""

    def is_header(self, line: str) -> bool:
        return line[:2] == "20"


def test_header_like_line_is_own_entry() -> None:
    lines = [HEADER.format(1), "cont", "2026-10-29 02:19:41,415+0100 broken", "after"]
    from atlas_log_analyzer.core.reader import Line

    entries = list(parse_lines((Line(i + 1, 0, t) for i, t in enumerate(lines)), _Strict(), "p"))
    assert len(entries) == 2
    assert entries[0].body == "cont"
    assert entries[1].parse_error == "unrecognised header"
    assert entries[1].message == "2026-10-29 02:19:41,415+0100 broken"
    assert entries[1].body == "after"
    assert entries[1].source.line == 3


def test_empty_and_crlf(tmp_path: Path, jira: JiraFormat) -> None:
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    assert list(parse_file(empty, jira)) == []
    crlf = tmp_path / "crlf.log"
    crlf.write_bytes(f"{HEADER.format(1)}\r\nbody\r\n{HEADER.format(2)}\r\n".encode())
    entries = list(parse_file(crlf, jira))
    assert [e.message for e in entries] == ["message 1", "message 2"]
    assert entries[0].body == "body"


def test_parse_detects_and_orders(tmp_path: Path) -> None:
    (tmp_path / "x.log.1").write_text(HEADER.format("old") + "\n", encoding="utf-8")
    (tmp_path / "x.log").write_text(HEADER.format("new") + "\n", encoding="utf-8")
    assert [e.message for e in parse([tmp_path])] == ["message old", "message new"]
    assert [s.rotation for s in iter_sources([tmp_path])] == [1, 0]
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "y.log").write_text("plain text\n", encoding="utf-8")
    with pytest.raises(ValueError, match="detect"):
        list(parse([tmp_path / "other"]))


def test_memory_bounded(tmp_path: Path, jira: JiraFormat, monkeypatch: pytest.MonkeyPatch) -> None:
    import atlas_log_analyzer.core.reader as reader

    monkeypatch.setattr(reader, "_CHUNK", 64 * 1024)
    path = tmp_path / "big.log"
    chunk = "".join(f"{HEADER.format(i)}\n\tat a.B.c(B.java:{i})\n" for i in range(2000))
    path.write_text(chunk * 10, encoding="utf-8")
    for _ in parse_file(path, jira):  # warm caches outside the measurement
        break
    tracemalloc.start()
    count = 0
    for _ in parse_file(path, jira):
        count += 1
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == 20000
    assert peak < path.stat().st_size // 2


def test_identical_entries_in_one_millisecond_get_distinct_ids() -> None:
    from atlas_log_analyzer.core import formats
    from atlas_log_analyzer.core.parser import parse_lines
    from atlas_log_analyzer.core.reader import Line

    fmt = formats.get("jira")
    text = "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      [x.Y] *****"
    lines = [Line(i + 1, i * 100, text) for i in range(3)]
    lines.append(Line(4, 400, text.replace(",415", ",416")))
    ids = [e.id for e in parse_lines(lines, fmt, "a.log")]
    assert len(set(ids)) == 4
    # numbering is by order within the millisecond, so it is reproducible
    assert ids == [e.id for e in parse_lines(lines, fmt, "b.log")]
    assert ids[0] == next(parse_lines(lines[:1], fmt, "a.log")).id


def test_parse_file_gives_same_millisecond_duplicates_distinct_ids(tmp_path: Path) -> None:
    from atlas_log_analyzer.core import formats
    from atlas_log_analyzer.core.parser import parse_file

    text = "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      [x.Y] *****\n"
    p = tmp_path / "a.log"
    p.write_text(text * 3, encoding="utf-8")
    assert len({e.id for e in parse_file(p, formats.get("jira"))}) == 3


def _entries(texts: list[str], **caps: int) -> list[LogEntry]:
    from atlas_log_analyzer.core.reader import Line

    lines = (Line(i + 1, i * 1000, t) for i, t in enumerate(texts))
    return list(parse_lines(lines, JiraFormat(), "x.log", **caps))


def test_entry_lines_cap_keeps_counting() -> None:
    texts = [HEADER.format(1), *(f"\tat a.B.c{i}(B.java:{i})" for i in range(10)), HEADER.format(2)]
    full = _entries(texts)
    entries = _entries(texts, max_entry_lines=4)
    assert len(entries) == 2
    cut = entries[0]
    assert cut.body.splitlines() == texts[1:4]
    size = len("\n".join(texts[:11]).encode())
    assert cut.parse_error == f"entry truncated: 11 lines, {size} bytes"
    assert TRUNCATED_ERROR.format(lines=11, bytes=size) == cut.parse_error
    # the entry after a truncated one is complete, and its id did not change
    assert entries[1].parse_error is None and entries[1].id == full[1].id
    # truncation is deterministic and changes the id of the truncated entry only
    assert cut.id == _entries(texts, max_entry_lines=4)[0].id != full[0].id
    assert cut.source == full[0].source


def test_entry_chars_cap_counts_utf8_bytes() -> None:
    header = HEADER.format(1)
    texts = [header, "ab", "é" * 10, "cd"]
    (entry,) = _entries(texts, max_entry_chars=len(header) + 3)
    assert entry.body == "ab"
    size = len("\n".join(texts).encode())
    assert entry.parse_error == f"entry truncated: 4 lines, {size} bytes"
    (whole,) = _entries(texts, max_entry_chars=len("\n".join(texts)))
    assert whole.parse_error is None and whole.body == "\n".join(texts[1:])


def test_truncated_orphan_keeps_orphan_error() -> None:
    texts = ["orphan 1", "orphan 2", "orphan 3", HEADER.format(1)]
    first, second = _entries(texts, max_entry_lines=2)
    assert first.parse_error == f"{ORPHAN_ERROR}; entry truncated: 3 lines, 26 bytes"
    assert first.body == "orphan 2" and second.parse_error is None


def test_default_caps_bound_a_huge_entry(tmp_path: Path, jira: JiraFormat) -> None:
    from atlas_log_analyzer.core.parser import MAX_ENTRY_LINES

    path = tmp_path / "big.log"
    lines = MAX_ENTRY_LINES + 50
    path.write_bytes((HEADER.format(1) + "\n" + "\tat x.Y.z(Y.java:1)\n" * (lines - 1)).encode())
    (entry,) = parse_file(path, jira)
    assert entry.body.count("\n") + 2 == MAX_ENTRY_LINES
    size = path.stat().st_size - 1  # without the final newline
    assert entry.parse_error == f"entry truncated: {lines} lines, {size} bytes"


# --- entry ids --------------------------------------------------------------------------

ID_LINE = "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      [x.Y] *****"
ID_LINE_2 = ID_LINE.replace(",415", ",416")


def _ids(texts: list[str]) -> list[str]:
    from atlas_log_analyzer.core.reader import Line

    lines = [Line(i + 1, i * 100, t) for i, t in enumerate(texts)]
    return [e.id for e in parse_lines(lines, JiraFormat(), "a.log")]


@pytest.mark.parametrize(
    ("fixture", "count", "digest"),
    [
        (SAMPLE, 47, "6a4fd64fe087c840"),
        (EDGE_CASES, 15, "e07a5219cc18eaf6"),
        (Path(__file__).parents[1] / "cli" / "data" / "mini.log", 10, "e35f07bdb9e3e630"),
    ],
)
def test_fixture_ids_are_stable(fixture: Path, count: int, digest: str, jira: JiraFormat) -> None:
    """Ids of existing stores stay valid: these digests were computed before the ordinal LRU.

    Banners are attached after the ids are made unique, so the other entries keep theirs.
    """
    import hashlib

    class NoBanners(JiraFormat):
        banner_window_ms = 0

    ids = [e.id for e in parse_file(fixture, NoBanners())]
    assert len(ids) == len(set(ids)) == count
    assert hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16] == digest
    kept = [e.id for e in parse_file(fixture, jira)]
    assert set(kept) <= set(ids) and sorted(kept, key=ids.index) == kept


def test_run_ordinal_ids_are_stable() -> None:
    assert _ids([ID_LINE, ID_LINE, ID_LINE, ID_LINE_2]) == [
        "d06d1865ae0f9295",
        "7e1103d0f5d38e90",
        "c737f0e78c4217a3",
        "06f971bdf304312b",
    ]


def test_non_adjacent_repeat_in_one_millisecond_gets_a_distinct_id() -> None:
    import xxhash

    ids = _ids([ID_LINE, ID_LINE_2, ID_LINE, ID_LINE, ID_LINE_2])
    assert len(set(ids)) == 5
    # entries that did not collide keep the ids of earlier versions (where ids[2] == ids[0])
    assert [ids[0], ids[1], ids[3]] == ["d06d1865ae0f9295", "06f971bdf304312b", "7e1103d0f5d38e90"]
    assert ids[2] == xxhash.xxh3_64_hexdigest(f"{ids[0]}\x1e1".encode())
    assert ids == _ids([ID_LINE, ID_LINE_2, ID_LINE, ID_LINE, ID_LINE_2])


def test_repeat_ids_are_remembered_for_recent_milliseconds_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import atlas_log_analyzer.core.parser as parser

    other = [ID_LINE.replace(",415", f",{ms}") for ms in (500, 501)]
    texts = [ID_LINE, *other, ID_LINE]
    assert len(set(_ids(texts))) == 4
    monkeypatch.setattr(parser, "RECENT_TIMESTAMPS", 2)
    ids = _ids(texts)
    assert ids[3] == ids[0]  # forgotten: the store keeps one of them
    monkeypatch.setattr(parser, "RECENT_TIMESTAMPS", 4096)
    monkeypatch.setattr(parser, "IDS_PER_MILLISECOND", 1)
    texts = [ID_LINE, ID_LINE, ID_LINE_2, ID_LINE, ID_LINE]
    ids = _ids(texts)
    assert ids[3] != ids[0] and ids[4] == ids[1]  # only the first id of a millisecond is kept


def test_with_id_copies_every_field(jira: JiraFormat) -> None:
    import dataclasses

    from atlas_log_analyzer.core.parser import _with_id

    entry = next(e for e in parse_file(SAMPLE, jira) if e.exceptions and e.context and e.body)
    entry = dataclasses.replace(entry, parse_error="x")
    assert len(dataclasses.fields(LogEntry)) == 13, "update _with_id for the new field"
    assert _with_id(entry, "0123456789abcdef") == dataclasses.replace(entry, id="0123456789abcdef")


def test_a_line_repeated_past_the_id_cap_keeps_every_entry() -> None:
    from atlas_log_analyzer.core.parser import IDS_PER_MILLISECOND, parse_lines
    from atlas_log_analyzer.core.reader import Line

    # an unreadable time is stored at the epoch: every repeat recurs in that millisecond,
    # with other entries between them
    bad = "2026-02-30 10:00:00,000+0100 exec-1 ERROR [x.Y] same text"
    texts = []
    for i in range(IDS_PER_MILLISECOND * 2 + 44):
        texts += [bad, f"2026-10-29 10:00:{i // 1000:02d},{i % 1000:03d}+0100 exec-2 INFO [x.Z] n"]
    lines = [Line(i + 1, i * 80, t) for i, t in enumerate(texts)]
    ids = [e.id for e in parse_lines(lines, JiraFormat(), "x.log")]
    assert len(set(ids)) == len(texts)
    # ids below the cap are those of earlier versions: only collisions past it are new
    head = [e.id for e in parse_lines(lines[:IDS_PER_MILLISECOND], JiraFormat(), "x.log")]
    assert ids[:IDS_PER_MILLISECOND] == head
