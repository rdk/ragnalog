from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_log_analyzer.core.formats.base import (
    CTX_IP,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    LogFormat,
)
from atlas_log_analyzer.core.formats.jira import (
    CTX_SERVICE,
    UNKNOWN_TIMESTAMP,
    JiraFormat,
    _iso,
    parse_timestamp,
)
from atlas_log_analyzer.core.models import LogLevel, SourceRef

from .conftest import EDGE_CASES

CET = timezone(timedelta(hours=1))

# (line, thread, level, logger, message, context)
HEADERS = [
    (
        "2026-10-29 02:19:41,415+0100 IssueIndexer:thread-15 ERROR      "
        "[c.o.scriptrunner.customfield.GroovyCustomField] msg",
        "IssueIndexer:thread-15",
        LogLevel.ERROR,
        "c.o.scriptrunner.customfield.GroovyCustomField",
        "msg",
        {},
    ),
    (
        "2026-10-29 03:05:49,991+0100 IssueIndexer:thread-2 ERROR anonymous     [logger] msg",
        "IssueIndexer:thread-2",
        LogLevel.ERROR,
        "logger",
        "msg",
        {CTX_USER: "anonymous"},
    ),
    (
        "2026-10-24 05:44:40,049+0200 http-nio-8080-exec-8 url: /secure/404.jspa WARN   -   "
        "[c.a.j.w.action.util.Error404] msg",
        "http-nio-8080-exec-8",
        LogLevel.WARN,
        "c.a.j.w.action.util.Error404",
        "msg",
        {CTX_URL: "/secure/404.jspa"},
    ),
    (
        "2026-10-28 03:27:30,738+0100 http-nio-8080-exec-68 url: /login.jsp, /plugins/servlet/login"
        " WARN anonymous 207x2193299x2 dk6u2n 10.0.0.24 /login.jsp [velocity] msg",
        "http-nio-8080-exec-68",
        LogLevel.WARN,
        "velocity",
        "msg",
        {
            CTX_URL: "/login.jsp, /plugins/servlet/login",
            CTX_USER: "anonymous",
            CTX_REQUEST_ID: "207x2193299x2",
            CTX_SESSION: "dk6u2n",
            CTX_IP: "10.0.0.24",
            CTX_REQUEST_URL: "/login.jsp",
        },
    ),
    (
        "2026-10-29 01:00:08,082+0100 RMI TCP Connection(123)-10.0.1.2 ERROR      [c.a.X] m",
        "RMI TCP Connection(123)-10.0.1.2",
        LogLevel.ERROR,
        "c.a.X",
        "m",
        {},
    ),
    (
        "2026-10-29 00:42:43,544+0100 JFR Event Stream 1 WARN      [c.a.X] m",
        "JFR Event Stream 1",
        LogLevel.WARN,
        "c.a.X",
        "m",
        {},
    ),
    (
        "2026-10-29 00:41:03,650+0100 bigbrassband-gitplugin-reindex-queue:thread - 1 ERROR      "
        "[c.b.X] Error updating the repository 'R' (repoId=83)",
        "bigbrassband-gitplugin-reindex-queue:thread - 1",
        LogLevel.ERROR,
        "c.b.X",
        "Error updating the repository 'R' (repoId=83)",
        {},
    ),
    (
        "2026-10-28 05:18:07,477+0100 ThreadPoolAsyncTaskExecutor::Thread 12 WARN      [o.s.X] m",
        "ThreadPoolAsyncTaskExecutor::Thread 12",
        LogLevel.WARN,
        "o.s.X",
        "m",
        {},
    ),
    (
        "2026-10-28 03:24:23,940+0100 ClusterMessageHandlerServiceThread:thread-1 INFO "
        "Anonymous user     [c.a.plugin.loaders.ScanningPluginLoader] No plugins found",
        "ClusterMessageHandlerServiceThread:thread-1",
        LogLevel.INFO,
        "c.a.plugin.loaders.ScanningPluginLoader",
        "No plugins found",
        {CTX_USER: "Anonymous user"},
    ),
    (
        "2026-10-28 09:13:00,000+0100 JiraTaskExecutionThread-1 INFO e_user7     [c.a.X] m",
        "JiraTaskExecutionThread-1",
        LogLevel.INFO,
        "c.a.X",
        "m",
        {CTX_USER: "e_user7"},
    ),
    (
        "2026-10-29 00:40:52,681+0100 http-nio-8080-exec-21 url: /rest/a; user: jdoe INFO jdoe "
        "40x109522x1 11vv7yi 10.0.0.55,10.0.0.103 /rest/a [c.a.j.util.log.LogMarker] ",
        "http-nio-8080-exec-21",
        LogLevel.INFO,
        "c.a.j.util.log.LogMarker",
        "",
        {
            CTX_URL: "/rest/a",
            CTX_USER: "jdoe",
            CTX_REQUEST_ID: "40x109522x1",
            CTX_SESSION: "11vv7yi",
            CTX_IP: "10.0.0.55,10.0.0.103",
            CTX_REQUEST_URL: "/rest/a",
        },
    ),
    (
        "2026-10-24 06:00:00,301+0200 Sending mailitem com.atlassian.jira.mail.X owner: "
        "'jdoe(jdoe)' ERROR anonymous    Mail Queue Service [c.a.jira.mail.X] jqlQuery",
        "Sending mailitem com.atlassian.jira.mail.X owner: 'jdoe(jdoe)'",
        LogLevel.ERROR,
        "c.a.jira.mail.X",
        "jqlQuery",
        {CTX_USER: "anonymous", CTX_SERVICE: "Mail Queue Service"},
    ),
    (
        "2026-10-24 07:00:29,106+0200 automation-rule-executor:thread-8 WARN Automation Robot     "
        "[c.c.X] Slow validation (923ms)",
        "automation-rule-executor:thread-8",
        LogLevel.WARN,
        "c.c.X",
        "Slow validation (923ms)",
        {CTX_USER: "Automation Robot"},
    ),
    (
        "2026-10-29 03:05:57,395+0100 JIRA-EventThread-607 ERROR      "
        "[c.a.event.internal.AsynchronousAbleEventDispatcher] There was an exception thrown "
        "trying to dispatch event [com.X@36f1da1b] from the invoker [Y]",
        "JIRA-EventThread-607",
        LogLevel.ERROR,
        "c.a.event.internal.AsynchronousAbleEventDispatcher",
        "There was an exception thrown trying to dispatch event [com.X@36f1da1b] "
        "from the invoker [Y]",
        {},
    ),
]


@pytest.mark.parametrize(("line", "thread", "level", "logger", "message", "context"), HEADERS)
def test_header_shapes(
    jira: JiraFormat,
    line: str,
    thread: str,
    level: LogLevel,
    logger: str,
    message: str,
    context: dict[str, str],
) -> None:
    assert jira.is_header(line)
    h = jira.parse_header(line)
    assert h is not None
    assert h.thread == thread
    assert h.level is level
    assert h.logger == logger
    assert h.message == message
    assert dict(h.context) == context
    assert h.timestamp.tzinfo is not None


def test_parse_header_without_is_header_call(jira: JiraFormat) -> None:
    line = HEADERS[1][0]
    other = JiraFormat()
    other.is_header(HEADERS[0][0])  # cached match of another line must not leak
    h = other.parse_header(line)
    assert h is not None
    assert h.thread == "IssueIndexer:thread-2"
    assert jira.parse_header(line) == h


@pytest.mark.parametrize(
    "line",
    [
        "2026-10-28 09:59:59,123+0100 Sending mailitem queued without level or logger",
        "2026-10-28 09:59:59,123+0100 thread ERROR no logger here",
        "\tat com.foo.Bar.baz(Bar.java:1)",
        "",
        "20",
        "2026-13-45 99:99:99,123+0100 t ERROR      [x] bad date",
    ],
)
def test_non_headers(jira: JiraFormat, line: str) -> None:
    if line.startswith("2026-13"):
        assert jira.parse_header(line) is None
        return
    assert not jira.is_header(line)
    assert jira.parse_header(line) is None


def test_timestamp() -> None:
    ts = parse_timestamp("2026-10-29 02:19:41,415+0100")
    assert ts == datetime(2026, 10, 29, 2, 19, 41, 415000, tzinfo=CET)
    for text in ("2026-10-29 02:19:41,415+0100", "2026-10-29 02:19:41,000-0530"):
        assert _iso(text) == parse_timestamp(text).isoformat()


def test_build_entry(jira: JiraFormat) -> None:
    line = HEADERS[0][0]
    cont = ["java.lang.IllegalStateException: boom", "\tat a.B.c(B.java:1)"]
    src = SourceRef("x.log", 10, 1234)
    entry = jira.build(jira.parse_header(line), line, cont, src)
    assert entry.format == "jira"
    assert entry.body == "java.lang.IllegalStateException: boom\n\tat a.B.c(B.java:1)"
    assert entry.raw == line + "\n" + entry.body
    assert entry.exception_type == "java.lang.IllegalStateException"
    assert entry.source == src
    assert entry.parse_error is None
    assert len(entry.id) == 16
    again = jira.build(jira.parse_header(line), line, list(cont), SourceRef("y.log", 1, 0))
    assert again.id == entry.id  # id does not depend on provenance
    single = jira.build(jira.parse_header(line), line, [], src)
    assert single.body == "" and single.raw == line and single.exceptions == ()
    assert single.id != entry.id
    with pytest.raises(TypeError):
        entry.context["user"] = "x"  # type: ignore[index]


def test_build_parse_error(jira: JiraFormat) -> None:
    src = SourceRef("x.log", 1, 0)
    entry = jira.build(None, "garbage", ["more"], src)
    assert entry.parse_error
    assert entry.message == "garbage"
    assert entry.level is LogLevel.UNKNOWN
    assert entry.timestamp == UNKNOWN_TIMESTAMP
    line = "2026-10-28 09:59:59,123+0100 not a header"
    assert jira.build(None, line, [], src).timestamp == parse_timestamp(line)


def test_sniff_and_protocol(jira: JiraFormat) -> None:
    assert isinstance(jira, LogFormat)
    lines = EDGE_CASES.read_text(encoding="utf-8").splitlines()
    score = jira.sniff(lines)
    assert 0.3 < score < 1
    assert jira.sniff(["hello", "world"]) == 0
    assert jira.sniff([]) == 0
