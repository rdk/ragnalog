from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ragnalog.core.formats.base import (
    CTX_IP,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    LogFormat,
)
from ragnalog.core.formats.jira import (
    CTX_SERVICE,
    HEADER_RE,
    UNKNOWN_TIMESTAMP,
    JiraFormat,
    _iso,
    match_header,
    parse_timestamp,
)
from ragnalog.core.models import LogLevel, SourceRef

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
        "2026-10-29 02:20:00,001+0100 Caesium-1-3 INFO ServiceRunner     "
        "[c.a.jira.servicedesk.sla.SlaUpdater] Recalculated SLA",
        "Caesium-1-3",
        LogLevel.INFO,
        "c.a.jira.servicedesk.sla.SlaUpdater",
        "Recalculated SLA",
        {CTX_SERVICE: "ServiceRunner"},
    ),
    (
        "2026-10-29 03:00:00,002+0100 Caesium-1-2 INFO ServiceRunner    Backup Service "
        "[c.a.jira.bc.dataimport.X] Backup started",
        "Caesium-1-2",
        LogLevel.INFO,
        "c.a.jira.bc.dataimport.X",
        "Backup started",
        {CTX_SERVICE: "Backup Service"},
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
    (
        "2026-10-29 03:06:01,002+0100 http-nio-8080-exec-3 url: /rest/api/2/issue WARN "
        "Automation Robot 186x77x1 1q2w3e 10.0.0.9 /rest/api/2/issue [c.c.Y] Rejected",
        "http-nio-8080-exec-3",
        LogLevel.WARN,
        "c.c.Y",
        "Rejected",
        {
            CTX_URL: "/rest/api/2/issue",
            CTX_USER: "Automation Robot",
            CTX_REQUEST_ID: "186x77x1",
            CTX_SESSION: "1q2w3e",
            CTX_IP: "10.0.0.9",
            CTX_REQUEST_URL: "/rest/api/2/issue",
        },
    ),
    (
        "2026-10-29 03:06:01,003+0100 http-nio-8080-exec-4 url: /x WARN "
        "jdoe - - 10.0.0.8 /x [c.c.Y] Anonymous request",
        "http-nio-8080-exec-4",
        LogLevel.WARN,
        "c.c.Y",
        "Anonymous request",
        {CTX_URL: "/x", CTX_USER: "jdoe", CTX_IP: "10.0.0.8", CTX_REQUEST_URL: "/x"},
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


def _regex_groups(line: str) -> tuple[str | None, ...] | None:
    m = HEADER_RE.match(line)
    return None if m is None else m.groups()


TS = "2026-10-29 02:19:41,415+0100"
TRICKY = [
    *(case[0] for case in HEADERS),
    f"{TS} t url: INFO [c.a.X] m",  # " url: " runs into the level token
    f"{TS} t url: INFO x WARN [c.a.X] m",
    f"{TS} t url:  INFO [c.a.X] m",
    f"{TS} t url: /a INFO u\t[c.a.X] m",
    f"{TS} t url: /a INFO \tu [c.a.X] m",  # ctx must not start with a tab
    f"{TS} t INFO [a b] x WARN [c.a.X] m",
    f"{TS} t INFO x[c.a.X] WARN [c.b.Y] m",
    f"{TS} t INFO [] WARN [c.a.X] m",
    f"{TS} t INFO [c.a.X]m",
    f"{TS} t INFO [c.a.X]",
    f"{TS}  INFO [c.a.X] empty thread",
    f"{TS} INFO [c.a.X] no thread",
    f"{TS} t INFO\xa0[c.a.X] m",
    f"{TS} t url: a url: b ERROR [c.a.X] m",
    f"{TS}t INFO [c.a.X] m",
    "2026-10-29 02:19:41,415 t INFO [c.a.X] m",
    "",
    TS,
]


@pytest.mark.parametrize("line", TRICKY)
def test_match_header_equals_regex(line: str) -> None:
    assert match_header(line) == _regex_groups(line)


def test_match_header_equals_regex_on_random_lines() -> None:
    import random

    tokens = [" ", "  ", " url: ", "url:", " INFO", " ERROR ", " WARN", "WARN ", " DEBUG ",
              "[", "]", "[c.a.X]", "[a b]", "x", "\t", " -", "; user: u", "]x", " FATAL",
              "\xa0"]  # fmt: skip
    rng = random.Random(1104)
    for _ in range(20_000):
        line = f"{TS} " + "".join(rng.choice(tokens) for _ in range(rng.randint(0, 12)))
        assert match_header(line) == _regex_groups(line), line


@pytest.mark.parametrize(
    "line",
    [
        f"{TS} t" + " INFO a" * 30_000,  # no [logger]: quadratic for the regex
        f"{TS} t url: x" + " url: x INFO a" * 15_000,
        f"{TS} t" + " INFO a[x]" * 20_000 + " INFO [c.a.X] m",
    ],
)
def test_long_non_header_lines_are_fast(jira: JiraFormat, line: str) -> None:
    import time

    started = time.perf_counter()
    jira.is_header(line)
    assert time.perf_counter() - started < 0.5


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
    assert 0.5 <= score < 1
    assert jira.sniff(["hello", "world"]) == 0
    stack = [lines[0], "java.lang.IllegalStateException: boom", "	at org.acme.A.b(A.java:1)"]
    assert jira.sniff(stack) == 1
    assert jira.sniff([]) == 0


def test_node_hints(jira: JiraFormat) -> None:
    from collections import Counter

    from ragnalog.core.formats.base import node_hints

    lines = [
        "2026-11-05 10:00:02,000+0000 main INFO      [org.acme.Node] Node info, nodeId: NODE1",
        "2026-11-05 10:00:03,000+0000 main INFO      [org.acme.Node] cluster nodeId: NODE1,",
        "2026-11-05 10:20:00,000+0000 jobs ERROR      [org.acme.Jobs] Job did not run on node "
        "NODE1 because it is busy",
        "2026-11-05 10:21:00,000+0000 jobs INFO      [org.acme.Jobs] no node named here",
        "\tat org.acme.Node.start(Node.java:1)",
    ]
    assert jira.node_hints(lines) == Counter({"NODE1": 3})
    assert node_hints(jira, lines) == Counter({"NODE1": 3})
    assert jira.node_hints(["nodeId: NODE1", "nodeId: NODE2", "nodeId: NODE2"]) == Counter(
        {"NODE2": 2, "NODE1": 1}
    )

    class Plain:
        name = "plain"

    assert node_hints(Plain(), lines) == Counter()  # type: ignore[arg-type]
