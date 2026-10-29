from __future__ import annotations

import tracemalloc
from collections import Counter
from pathlib import Path

import pytest

from atlas_log_analyzer.core.formats.base import CTX_USER
from atlas_log_analyzer.core.formats.jira import JiraFormat
from atlas_log_analyzer.core.models import LogLevel
from atlas_log_analyzer.core.parser import ORPHAN_ERROR, parse, parse_file, parse_lines
from atlas_log_analyzer.core.reader import iter_lines, iter_sources

from .conftest import EDGE_CASES, SAMPLE

HEADER = "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      [x.Y] message {}"


def test_sample_counts(jira: JiraFormat) -> None:
    entries = list(parse_file(SAMPLE, jira))
    assert len(entries) == 47
    assert [e for e in entries if e.parse_error] == []
    assert Counter(e.level for e in entries) == {
        LogLevel.WARN: 18,
        LogLevel.INFO: 15,
        LogLevel.ERROR: 13,
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
    # every line of the file is accounted for exactly once
    total_lines = sum(1 for _ in iter_lines(SAMPLE))
    assert sum(1 + (e.body.count("\n") + 1 if e.body else 0) for e in entries) == total_lines
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
