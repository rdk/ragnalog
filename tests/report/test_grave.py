"""Grave errors: which entries are marked, how they fold, and what the payloads carry."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragnalog.report import build_payload
from ragnalog.report.grave import grave_markers
from ragnalog.store import Store
from ragnalog.store.pipeline import ingest_paths

# invented lines in Jira's layout: the startup banner names OutOfMemoryError in its JVM options
# (not grave), then an out-of-memory storm, a later one, a stack overflow, FATAL, a full file
# table and an ordinary error


def _line(clock: str, thread: str, level: str, logger: str, message: str) -> str:
    return f"2026-10-29 {clock}+0100 {thread} {level}      [{logger}] {message}\n"


IDX = "c.a.jira.index.DefaultIndexManager"
WEB = "c.a.jira.web.Servlet"
OOM = (
    "java.lang.RuntimeException: indexing failed\n\tat com.acme.Foo.bar(Foo.java:1)\n"
    "Caused by: java.lang.OutOfMemoryError: Java heap space\n\tat com.acme.Foo.baz(Foo.java:2)\n"
)
JVM_OPTIONS = (
    "JVM Input Arguments : -Xmx2g -XX:+HeapDumpOnOutOfMemoryError -XX:OnOutOfMemoryError=x"
)
LOG = "".join(
    [
        _line(
            "08:00:00,000", "localhost-startStop-1", "INFO", "c.a.jira.startup.Logger", JVM_OPTIONS
        ),
        _line("09:57:00,100", "http-nio-8080-exec-1", "ERROR", IDX, "Indexing failed") + OOM,
        _line("09:57:20,000", "http-nio-8080-exec-2", "ERROR", IDX, "Indexing failed") + OOM,
        _line("09:58:10,000", "http-nio-8080-exec-3", "ERROR", IDX, "Indexing failed") + OOM,
        _line(
            "10:05:00,000",
            "Caesium-1-1",
            "ERROR",
            "c.a.scheduler.core.JobLauncher",
            "Job failed: GC overhead limit exceeded",
        ),
        _line("10:10:00,000", "http-nio-8080-exec-4", "ERROR", WEB, "Request failed")
        + "java.lang.StackOverflowError\n\tat com.acme.Rec.go(Rec.java:3)\n",
        _line("10:20:00,000", "main", "FATAL", "c.a.jira.startup.Launcher", "Jira cannot start"),
        _line(
            "10:30:00,000",
            "http-nio-8080-exec-5",
            "ERROR",
            "c.a.jira.attachment.Store",
            "Cannot save the attachment: Too many open files",
        ),
        _line("10:40:00,000", "http-nio-8080-exec-6", "ERROR", WEB, "Ordinary failure")
        + "java.lang.IllegalStateException: boom\n\tat com.acme.Foo.bar(Foo.java:1)\n",
        # INFO naming OutOfMemoryError is not grave; a job failing on the GC limit is
        _line(
            "10:50:00,000",
            "main",
            "INFO",
            "c.a.jira.startup.Logger",
            "Heap dump on OutOfMemoryError: on",
        ),
        _line(
            "10:55:00,000", "Caesium-1-2", "ERROR", "c.a.scheduler.core.JobLauncher", "Job failed"
        )
        + "java.lang.IllegalStateException: GC overhead limit exceeded\n"
        + "\tat com.acme.J.run(J.java:9)\n",
    ]
)


@pytest.fixture
def logset(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "atlassian-jira.log"
    path.write_text(LOG, encoding="utf-8")
    return ingest_paths(Store(tmp_path / "store"), [path], name="grave").logset


def test_kinds_fold_and_text(logset) -> None:  # type: ignore[no-untyped-def]
    marks = grave_markers(logset)
    assert [m["kind"] for m in marks] == [
        "oom",
        "oom",
        "stack_overflow",
        "fatal",
        "resource",
        "oom",
    ]
    storm = marks[0]
    # three entries, each within 60 s of the previous: one marker from the first to the last
    assert storm["count"] == 3
    assert storm["ts"] == "2026-10-29T08:57:00.100Z"  # milliseconds, so strings sort by time
    assert storm["last_ts"].startswith("2026-10-29T08:58:10")
    # the grave exception's own type and message, not the wrapper's
    assert storm["text"] == "java.lang.OutOfMemoryError: Java heap space"
    assert marks[1]["count"] == 1 and "GC overhead limit exceeded" in marks[1]["text"]
    assert marks[2]["text"] == "java.lang.StackOverflowError"
    assert all(m["signature"] and m["entry_id"] for m in marks)
    # the exception whose message matched, not the outermost or the template
    assert marks[5]["text"] == "java.lang.IllegalStateException: GC overhead limit exceeded"


def test_window_and_limit(logset) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    since = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)  # 10:00 CET
    assert [m["kind"] for m in grave_markers(logset, since=since)] == [
        "oom",
        "stack_overflow",
        "fatal",
        "resource",
        "oom",
    ]
    # both ends inclusive, as a capture's window; a naive time is UTC
    at = datetime(2026, 10, 29, 9, 10)  # the stack overflow, 10:10 CET
    assert [m["kind"] for m in grave_markers(logset, since=at, until=at)] == ["stack_overflow"]
    top = grave_markers(logset, limit=1)
    assert len(top) == 1 and top[0]["count"] == 3


def test_report_payload_carries_them(logset) -> None:  # type: ignore[no-untyped-def]
    payload = build_payload(logset)
    assert [g["kind"] for g in payload["grave"]] == [
        "oom",
        "oom",
        "stack_overflow",
        "fatal",
        "resource",
        "oom",
    ]
