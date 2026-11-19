"""Unit tests for cli/output.py (no core/store implementation needed)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from ragnalog.cli.output import (
    ExitCode,
    OpError,
    Output,
    default_mode,
    encode_json,
    entry_to_dict,
    error_from_exception,
    jsonable_rows,
)
from ragnalog.core.formats.registry import UnknownFormatError
from ragnalog.core.models import ExceptionInfo, Frame, LogEntry, LogLevel, SourceRef
from ragnalog.store.store import StoreError


def make_output(mode: str) -> tuple[Output, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return Output(mode, stdout=out, stderr=err), out, err  # type: ignore[arg-type]


ROWS = [
    {"signature": "a" * 16, "count": 3, "level": "ERROR", "template": "x <NUM>"},
    {"signature": "b" * 16, "count": 1, "level": "WARN", "template": "y"},
]


def test_json_document_has_schema_and_items() -> None:
    output, out, err = make_output("json")
    output.emit("groups.v1", {"logset": "id1", "count": 2, "items": ROWS})
    doc = json.loads(out.getvalue())
    assert doc == {"schema": "groups.v1", "logset": "id1", "count": 2, "items": ROWS}
    assert out.getvalue().count("\n") == 1  # compact when not a TTY
    assert err.getvalue() == ""


def test_jsonl_writes_one_row_per_line() -> None:
    output, out, _ = make_output("jsonl")
    output.emit("groups.v1", {"logset": "id1", "items": ROWS})
    lines = out.getvalue().splitlines()
    assert [json.loads(line) for line in lines] == ROWS


def test_jsonl_plain_document_is_one_line() -> None:
    output, out, _ = make_output("jsonl")
    output.emit("stats.v1", {"logset": "id1", "levels": {"ERROR": 1}}, items=None)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"schema": "stats.v1", "logset": "id1", "levels": {"ERROR": 1}}


def test_jsonl_rows_override() -> None:
    output, out, _ = make_output("jsonl")
    output.emit("diff.v1", {"new": ROWS}, items=None, jsonl_rows=[{"change": "new", "n": 1}])
    assert [json.loads(x) for x in out.getvalue().splitlines()] == [{"change": "new", "n": 1}]


def test_table_has_no_ansi_and_truncates() -> None:
    output, out, err = make_output("table")
    rows = [{"signature": "s1", "template": "word " * 100}]
    output.emit("groups.v1", {"logset": "id1", "items": rows}, columns=("signature", "template"))
    text = out.getvalue()
    assert "\x1b[" not in text
    assert "signature" in text and "template" in text
    assert "…" in text
    assert "word " * 30 not in text
    assert "logset=id1" in err.getvalue()  # scalar metadata goes to stderr


def test_table_columns_subset_and_order() -> None:
    output, out, _ = make_output("table")
    output.emit("groups.v1", {"items": ROWS}, columns=("count", "signature", "missing"))
    header = next(line for line in out.getvalue().splitlines() if "signature" in line)
    assert header.index("count") < header.index("signature")
    assert "template" not in out.getvalue()


def test_table_shortens_iso_timestamps() -> None:
    output, out, _ = make_output("table")
    rows = [{"a": "2026-10-28T23:42:03.555+00:00", "b": "2026-10-28T23:42:03+01:00"}]
    output.emit("x.v1", {"items": rows})
    assert "2026-10-28 23:42:03Z" in out.getvalue()
    assert "2026-10-28 23:42:03+01:00" in out.getvalue()


def test_table_for_plain_document() -> None:
    output, out, _ = make_output("table")
    output.emit(
        "logset.v1",
        {"id": "abc", "entry_count": 10, "sources": [{"path": "a.log", "size": 5}]},
        items=None,
    )
    text = out.getvalue()
    assert "entry_count" in text and "10" in text
    assert "a.log" in text and "sources" in text


def test_table_quiet_suppresses_info() -> None:
    output, _, err = make_output("table")
    output.quiet = True
    output.emit("groups.v1", {"logset": "x", "items": ROWS})
    output.info("progress")
    assert err.getvalue() == ""
    output.warn("careful")
    assert "warning: careful" in err.getvalue()


@pytest.mark.parametrize("mode", ["json", "jsonl"])
def test_error_envelope_on_stdout(mode: str) -> None:
    output, out, err = make_output(mode)
    code = output.error(OpError("store_error", "no such log set: x"))
    assert code == 3
    assert json.loads(out.getvalue()) == {
        "schema": "error.v1",
        "error": {"code": "store_error", "message": "no such log set: x"},
    }
    assert err.getvalue() == ""


def test_error_in_table_mode_goes_to_stderr() -> None:
    output, out, err = make_output("table")
    code = output.error(OpError("not_found", "nothing"))
    assert code == 2
    assert out.getvalue() == ""
    assert "nothing" in err.getvalue() and "not_found" in err.getvalue()


@pytest.mark.parametrize(
    ("exc", "code", "exit_code"),
    [
        (StoreError("missing"), "store_error", 3),
        (ValueError("bad"), "invalid_value", 1),
        (FileNotFoundError(2, "No such file", "x.log"), "file_not_found", 2),
        (UnknownFormatError("nope"), "unknown_format", 1),
        (OpError("not_found", "y"), "not_found", 2),
        (OpError("x", "y"), "x", 1),  # a code missing from SPEC exits 1
    ],
)
def test_error_from_exception(exc: Exception, code: str, exit_code: int) -> None:
    err = error_from_exception(exc)
    assert err is not None
    assert (err.code, int(err.exit_code)) == (code, exit_code)


def test_error_from_exception_unknown() -> None:
    assert error_from_exception(RuntimeError("boom")) is None
    assert error_from_exception(NotImplementedError()) is None


def test_unknown_format_message_unquoted() -> None:
    err = error_from_exception(UnknownFormatError("unknown format 'x'"))
    assert err is not None and err.message == "unknown format 'x'"


def test_exit_codes() -> None:
    assert [int(c) for c in ExitCode] == [0, 1, 2, 3]


def test_default_mode_non_tty() -> None:
    assert default_mode(io.StringIO()) == "json"


def test_encode_json_types() -> None:
    ts = datetime(2026, 10, 29, 2, 19, 41, 415000, tzinfo=timezone(timedelta(hours=1)))
    doc = json.loads(
        encode_json({"ts": ts, "level": LogLevel.ERROR, "path": Path("a") / "b.log", "t": (1, 2)})
    )
    assert doc == {
        "ts": "2026-10-29T02:19:41.415000+01:00",
        "level": "ERROR",
        "path": "a/b.log",
        "t": [1, 2],
    }


def test_jsonable_rows_from_dataframe() -> None:
    df = pl.DataFrame(
        {"n": [1, 2], "ts": [datetime(2026, 1, 1, tzinfo=UTC), None], "v": [["a"], []]}
    )
    rows = jsonable_rows(df)
    assert rows[0]["n"] == 1 and rows[1]["v"] == []
    doc = json.loads(encode_json({"items": rows}))
    assert doc["items"][0]["ts"].startswith("2026-01-01T00:00:00")
    assert doc["items"][1]["ts"] is None


def test_entry_to_dict() -> None:
    entry = LogEntry(
        id="0123456789abcdef",
        format="jira",
        timestamp=datetime(2026, 10, 29, 2, 19, 41, 415000, tzinfo=timezone(timedelta(hours=1))),
        level=LogLevel.ERROR,
        thread="IssueIndexer:thread-15",
        logger="c.o.scriptrunner.customfield.GroovyCustomField",
        message="Script field failed on issue: ABC-69517, field: Finished On",
        body="java.lang.NullPointerException: boom\n\tat A.b(A.java:1)",
        exceptions=(
            ExceptionInfo(
                "java.lang.NullPointerException", "boom", (Frame("A.b", "A.java", 1),), 0
            ),
        ),
        context={"user": "jdoe"},
        source=SourceRef("x.log", 1, 0),
        raw="...",
    )
    doc = json.loads(encode_json(entry_to_dict(entry)))
    assert doc["level"] == "ERROR"
    assert doc["timestamp"] == "2026-10-29T02:19:41.415+01:00"
    assert doc["exception_type"] == "java.lang.NullPointerException"
    assert doc["exceptions"][0]["frames"][0] == {
        "location": "A.b",
        "file": "A.java",
        "line": 1,
        "module": None,
    }
    assert doc["context"] == {"user": "jdoe"}
    assert doc["source"] == {"path": "x.log", "line": 1, "offset": 0}
    assert doc["parse_error"] is None
