"""Synthetic Jira log entries for CLI tests.

Everything here is invented: users, request ids, issue keys (``ABC-``), IPs
(``10.0.0.0/8``) and URLs. Tests write the text to ``tmp_path`` and ingest it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class E:
    """One synthetic entry; ``ts`` is the time of day on 2026-10-29 (+01:00)."""

    ts: str
    thread: str
    level: str
    logger: str
    message: str
    user: str | None = None
    request_id: str | None = None
    body: str = ""
    day: str = "2026-10-29"


def render(e: E) -> str:
    """The entry in the Jira ``atlassian-jira.log`` layout."""
    head = f"{e.day} {e.ts}+0100 {e.thread}"
    if e.user is not None and e.request_id is not None:
        url = "/rest/acme-reports/1.0/report"
        head += (
            f" url: {url}; user: {e.user} {e.level} {e.user} {e.request_id} 1abcd2e 10.0.0.5 {url}"
        )
    else:
        head += f" {e.level}     "
    head += f" [{e.logger}] {e.message}"
    return head + ("\n" + e.body if e.body else "")


def write_log(path: Path, entries: Iterable[E]) -> Path:
    """Write ``entries`` to ``path`` (parent created) and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(render(e) for e in entries) + "\n").encode())
    return path


def long_body(lines: int = 200) -> str:
    """A long continuation text (about 80 characters per line)."""
    return "\n".join(f"    detail {i:05d} " + "y" * 64 for i in range(lines))


PLUGIN_TRACE = "\n".join(
    [
        "com.qoti.rf.exceptions.InternalException: Cannot compute stat type for customfield_10001",
        "\tat com.qoti.rf.stats.type.StatTypeHelper.resolve(StatTypeHelper.java:88)",
        "\tat com.atlassian.jira.issue.fields.CustomFieldImpl.getValue(CustomFieldImpl.java:412)",
        "\tat org.acme.jira.reports.ReportService.build(ReportService.java:57)",
        "\tat java.base/java.util.ArrayList.forEach(ArrayList.java:1596)",
        "\tat com.atlassian.plugin.osgi.hostcomponents.impl.DefaultComponentRegistrar.invoke"
        "(DefaultComponentRegistrar.java:129)",
        "\tat org.apache.catalina.core.ApplicationFilterChain.doFilter"
        "(ApplicationFilterChain.java:166)",
        "Caused by: java.lang.IllegalArgumentException: unknown stat type 'priority-x'",
        "\tat com.qoti.rf.stats.type.StatTypes.of(StatTypes.java:31)",
        "\tat com.google.common.collect.Iterators.transform(Iterators.java:800)",
        "\tat java.base/java.lang.Thread.run(Thread.java:1583)",
        "\t... 12 more",
    ]
)
"""A plugin exception whose frames mix plugin code (com.qoti., org.acme.) with platform code."""

SERVER_TRACE = "\n".join(
    [
        "java.lang.IllegalStateException: Search index is being rebuilt",
        "\tat com.atlassian.jira.issue.search.providers.LuceneSearchProvider.search"
        "(LuceneSearchProvider.java:301)",
    ]
)

EXCEPTION_MIX = [
    E("09:00:00,000", "Caesium-1-1", "INFO", "c.a.jira.Sched", "Job ran"),
    E(
        "09:00:01,000",
        "http-nio-8080-exec-1",
        "DEBUG",
        "c.q.r.stats.type.StatTypeHelper",
        "Stat type failed for field customfield_10001",
        user="alice",
        request_id="540x101x1",
        body=PLUGIN_TRACE,
    ),
    E("09:00:02,000", "http-nio-8080-exec-2", "WARN", "c.a.jira.Slow", "Slow query took 1500 ms"),
    E(
        "09:00:03,000",
        "http-nio-8080-exec-3",
        "ERROR",
        "c.a.j.rest.ExceptionInterceptor",
        "Returning internal server error in response",
        user="bob",
        request_id="540x102x1",
        body=SERVER_TRACE,
    ),
]
"""INFO, DEBUG with a plugin exception, WARN without exception, ERROR with an exception."""


REQ_A, REQ_B, REQ_C = "540x201x1", "540x202x1", "540x203x1"
EXEC_1, EXEC_2 = "http-nio-8080-exec-1", "http-nio-8080-exec-2"
REQUESTS = [
    E("09:00:00,000", EXEC_1, "INFO", "c.e.reports.Web", "Report started", "alice", REQ_A),
    E("09:00:00,500", EXEC_2, "INFO", "c.e.reports.Web", "Report started", "bob", REQ_B),
    E("09:00:01,000", EXEC_1, "DEBUG", "c.e.reports.Stat", "Computing stats", "alice", REQ_A),
    E("09:00:01,000", EXEC_1, "DEBUG", "c.e.reports.Stat", "Computing more stats", "alice", REQ_A),
    E("09:00:01,500", "Caesium-1-1", "INFO", "c.a.jira.Sched", "Job ran"),
    E("09:00:02,000", EXEC_2, "WARN", "c.e.reports.Stat", "Slow stat", "bob", REQ_B),
    E(
        "09:00:03,000",
        EXEC_1,
        "ERROR",
        "c.e.reports.Web",
        "Report failed",
        "alice",
        REQ_A,
        body=SERVER_TRACE,
    ),
    E("09:00:05,000", EXEC_1, "INFO", "c.e.reports.Web", "Report started", "alice", REQ_C),
]
"""Two interleaved requests on two threads, a scheduler entry, and a later request."""
