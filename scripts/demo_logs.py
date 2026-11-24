"""Write synthetic Jira Data Center logs for demos and the README screenshots.

Usage::

    uv run python scripts/demo_logs.py OUT          # OUT/week, OUT/prev, OUT/incident
    ragnalog --store DEMO ingest OUT/prev --cluster demo-jira --name prev
    ragnalog --store DEMO ingest OUT/week --cluster demo-jira --name week
    ragnalog --store DEMO ingest OUT/incident --cluster demo-dc --name incident
    ragnalog --store DEMO serve

Everything is invented: users ``user1``…, issue keys ``ABC-n``, ``jira.example.com``,
``10.0.x.x`` addresses, ``org.example`` plugins. ``week`` holds three days of two nodes
with everyday noise, scheduled jobs, a directory-sync outage on node2 (with cache
replication warnings on node1 at the same time) and two error types that ``prev`` (the
week before) does not have; ``prev`` has one that ``week`` lost. ``incident`` is one day
of a four-node cluster (``demo-dc``) with a failure that spreads: heavy REST searches fill
node3's heap until it fails with OutOfMemoryError; while it hangs the other nodes' cache
replication to it backs up and is abandoned, their lock waits time out and health checks
fail; node3 is restarted, then node2 runs out of memory too (GC overhead) and is
restarted. Seeded: the same output every run.
"""

from __future__ import annotations

import argparse
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

TZ = "+0100"
NODES = ("node1", "node2")
JOBS = (
    "mail-queue-flush",
    "index-snapshot",
    "health-check",
    "user-directory-sync",
    "audit-cleanup",
)
PATHS = (
    "/browse/ABC-{n}",
    "/secure/Dashboard.jspa",
    "/rest/api/2/search",
    "/secure/RapidBoard.jspa",
    "/rest/greenhopper/1.0/xboard/work/allData",
    "/secure/CreateIssue.jspa",
)
RESOURCES = ("org.example.timesheet:report-resources", "com.atlassian.jira.gadgets:common-lite")
DIRS = ("Corporate LDAP", "Partner AD", "Contractors")


@dataclass(frozen=True)
class Pattern:
    """One kind of entry: its header, message and how often it is logged per hour."""

    level: str
    logger: str
    thread: Callable[[random.Random], str]
    message: Callable[[random.Random, str], str]
    rate: Callable[[str, datetime], float]
    trace: Callable[[random.Random], list[str]] | None = None
    http: bool = False


def day_factor(t: datetime) -> float:
    """Return the activity of the hour: office hours are busy, nights quiet."""
    h = t.hour + t.minute / 60
    return 0.12 + 0.88 * max(0.0, math.sin(math.pi * (h - 7) / 12)) if 7 <= h <= 19 else 0.12


def http_thread(r: random.Random) -> str:
    """Return a Tomcat request thread name."""
    return f"http-nio-8080-exec-{r.randint(1, 60)}"


def caesium(r: random.Random) -> str:
    """Return a scheduler (Caesium) thread name."""
    return f"Caesium-1-{r.randint(1, 4)}"


def frames(r: random.Random, top: list[str], more: int) -> list[str]:
    """Return stack frames: ``top``, then the servlet frames every web request has."""
    lines = [f"\tat {f}" for f in top]
    lines += [
        "\tat com.atlassian.jira.web.dispatcher.JiraWebworkActionDispatcher.service"
        "(JiraWebworkActionDispatcher.java:179)",
        "\tat javax.servlet.http.HttpServlet.service(HttpServlet.java:764)",
        "\tat org.apache.catalina.core.ApplicationFilterChain.internalDoFilter"
        "(ApplicationFilterChain.java:227)",
    ]
    return [*lines, f"\t... {more + r.randint(0, 9)} more"]


def npe_trace(r: random.Random) -> list[str]:
    """Return a NullPointerException trace from a report plugin."""
    return [
        'java.lang.NullPointerException: Cannot invoke "com.atlassian.jira.user.ApplicationUser'
        '.getKey()" because "user" is null',
        *frames(
            r,
            [
                "org.example.jira.timesheet.TimesheetReport.collectWorklogs(TimesheetReport.java:212)",
                "org.example.jira.timesheet.TimesheetReport.generateReportHtml(TimesheetReport.java:96)",
                "com.atlassian.jira.web.action.browser.ConfigureReport.doExecute(ConfigureReport.java:148)",
            ],
            61,
        ),
    ]


def connect_trace(r: random.Random) -> list[str]:
    """Return the trace of a directory sync that cannot connect (with a cause)."""
    return [
        "org.example.jira.sync.SyncException: Directory did not answer within 30000 ms",
        "\tat org.example.jira.sync.DirectoryClient.fetchChanges(DirectoryClient.java:311)",
        "\tat org.example.jira.sync.DirectorySyncJob.runOnce(DirectorySyncJob.java:87)",
        "\tat com.atlassian.scheduler.core.JobLauncher.runJob(JobLauncher.java:153)",
        "Caused by: java.net.ConnectException: Connection refused",
        "\tat java.base/sun.nio.ch.Net.connect0(Native Method)",
        "\tat java.base/sun.nio.ch.NioSocketImpl.connect(NioSocketImpl.java:592)",
        f"\t... {r.randint(14, 19)} more",
    ]


def mail_trace(r: random.Random) -> list[str]:
    """Return the trace of an SMTP failure while sending a filter subscription."""
    return [
        "javax.mail.MessagingException: Could not connect to SMTP host: smtp.example.com, port: 25",
        "\tat com.sun.mail.smtp.SMTPTransport.openServer(SMTPTransport.java:2209)",
        "\tat com.atlassian.mail.server.impl.SMTPMailServerImpl.send(SMTPMailServerImpl.java:164)",
        "\tat com.atlassian.jira.mail.SubscriptionMailQueueItem.send"
        "(SubscriptionMailQueueItem.java:127)",
        f"\t... {r.randint(20, 26)} more",
    ]


def window(start: str, end: str) -> Callable[[datetime], bool]:
    """Return a test whether a time is in ``[start, end)`` (ISO 8601 local times)."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return lambda t: a <= t < b


OUTAGE = window("2026-11-03T13:40", "2026-11-03T14:55")
WEEK = window("2026-11-02T00:00", "2026-11-06T00:00")


def patterns() -> list[Pattern]:
    """Return every kind of entry the demo logs hold."""
    u = lambda r: f"user{r.randint(1, 48)}"  # noqa: E731
    return [
        Pattern(
            "WARN",
            "c.a.j.plugin.webresource.JiraWebResourceIntegration",
            http_thread,
            lambda r, n: f"Unable to find web resource for key '{r.choice(RESOURCES)}'",
            lambda n, t: 95 * day_factor(t),
            http=True,
        ),
        Pattern(
            "WARN",
            "c.a.j.issue.search.providers.LuceneSearchProvider",
            http_thread,
            lambda r, n: (
                f"Slow JQL query took {r.randint(1200, 9800)} ms for {u(r)}"
                f" (filter {r.randint(10100, 10400)})"
            ),
            lambda n, t: (48 if WEEK(t) else 18) * day_factor(t),
            http=True,
        ),  # grew
        Pattern(
            "INFO",
            "c.a.j.service.ServiceRunner",
            caesium,
            lambda r, n: f"Job {r.choice(JOBS)} finished in {r.randint(0, 40)}s",
            lambda n, t: 10.0,
        ),
        Pattern(
            "INFO",
            "c.a.j.issue.index.DefaultIndexManager",
            caesium,
            lambda r, n: (
                f"Index snapshot of {r.randint(180000, 182000)} issues"
                f" written in {r.randint(20, 90)}s"
            ),
            lambda n, t: 1.0 if n == "node1" else 0.3,
        ),
        Pattern(
            "DEBUG",
            "c.a.j.util.velocity.VelocityRequestContextFactory",
            http_thread,
            lambda r, n: "Base URL resolved to https://jira.example.com",
            lambda n, t: 30 * day_factor(t),
            http=True,
        ),
        Pattern(
            "WARN",
            "c.a.j.issue.managers.DefaultIssueManager",
            http_thread,
            lambda r, n: f"Issue ABC-{r.randint(1, 9000)} not found for {u(r)}",
            lambda n, t: 9 * day_factor(t),
            http=True,
        ),
        Pattern(
            "ERROR",
            "c.a.j.web.dispatcher.JiraWebworkActionDispatcher",
            http_thread,
            lambda r, n: (
                "Exception thrown while processing request for /secure/ConfigureReport.jspa"
            ),
            lambda n, t: 2.5 * day_factor(t),
            trace=npe_trace,
            http=True,
        ),
        Pattern(
            "ERROR",
            "org.example.jira.sync.DirectorySyncJob",
            caesium,
            lambda r, n: (
                f"Sync of directory '{r.choice(DIRS)}' failed after {r.randint(3, 5)} retries"
            ),
            lambda n, t: 420.0 if n == "node2" and OUTAGE(t) else 0.15,
            trace=connect_trace,
        ),
        Pattern(
            "WARN",
            "c.a.j.cluster.distribution.localq.LocalQCacheManager",
            lambda r: f"localq-reader-{r.randint(0, 3)}",
            lambda r, n: (
                f"Cache replication queue for node {'node2' if n == 'node1' else 'node1'}"
                f" is {r.randint(70, 99)}% full"
            ),
            lambda n, t: 90.0 if n == "node1" and OUTAGE(t) else 0.0,
        ),
        # new in the demo week: two error types the week before did not have
        Pattern(
            "ERROR",
            "c.a.j.mail.SubscriptionMailQueueItem",
            caesium,
            lambda r, n: (
                f"Failed to send subscription mail for filter {r.randint(10100, 10400)} to {u(r)}"
            ),
            lambda n, t: 0.8 if WEEK(t) else 0.0,
            trace=mail_trace,
        ),
        Pattern(
            "ERROR",
            "org.example.jira.automation.RuleExecutor",
            caesium,
            lambda r, n: (
                f"Rule {r.randint(1, 40)} failed on ABC-{r.randint(1, 9000)}: field"
                f" customfield_{r.choice((10010, 10020, 10031))} is not on the screen"
            ),
            lambda n, t: (6.0 if n == "node1" else 1.0) * day_factor(t) if WEEK(t) else 0.0,
        ),
        # only the week before: gone in the demo week
        Pattern(
            "ERROR",
            "c.a.j.upgrade.PluginUpgradeManager",
            caesium,
            lambda r, n: (
                f"Upgrade task {r.randint(10, 40)} of org.example.charts failed:"
                " table AO_E8B6CC_CHART missing"
            ),
            lambda n, t: 0.0 if WEEK(t) else 1.5,
        ),
    ]


INCIDENT = datetime(2026, 11, 4)
"""The day of the incident capture (local time, +0100)."""
INCIDENT_NODES = ("node1", "node2", "node3", "node4")


def at(hhmm: str) -> datetime:
    """Return a local time of the incident day (``"09:02"``)."""
    h, m = hhmm.split(":")
    return INCIDENT.replace(hour=int(h), minute=int(m))


def ramp(t: datetime, start: str, peak: str, end: str, top: float) -> float:
    """Return a rate rising linearly from ``start`` to ``top`` at ``peak``, zero after ``end``."""
    a, p, e = at(start), at(peak), at(end)
    if not a <= t < e:
        return 0.0
    return top * min(1.0, (t - a) / (p - a)) if t < p else top


def oom_trace(r: random.Random, cause: str) -> list[str]:
    """Return the trace of an OutOfMemoryError in a REST search."""
    return [
        f"java.lang.OutOfMemoryError: {cause}",
        "\tat java.base/java.util.Arrays.copyOf(Arrays.java:3537)",
        "\tat com.atlassian.jira.issue.search.providers.LuceneSearchProvider.search"
        "(LuceneSearchProvider.java:432)",
        "\tat com.atlassian.jira.rest.v2.search.SearchResource.search(SearchResource.java:157)",
        f"\t... {r.randint(60, 70)} more",
    ]


def incident_patterns() -> list[Pattern]:
    """Return the entries of the incident day on top of the everyday ones."""
    u = lambda r: f"user{r.randint(1, 48)}"  # noqa: E731
    others = lambda n, sick: n != sick  # noqa: E731
    return [
        *patterns(),
        Pattern(
            "WARN",
            "c.a.j.rest.v2.search.SearchResource",
            http_thread,
            lambda r, n: (
                f"Search for {u(r)} returned {r.randint(40000, 90000)} issues"
                f" in {r.randint(8000, 41000)} ms (maxResults=1000, expand=changelog)"
            ),
            lambda n, t: ramp(t, "08:05", "08:55", "09:06", 220.0) if n == "node3" else 0.0,
            http=True,
        ),
        Pattern(
            "WARN",
            "c.a.j.instrumentation.GcMonitor",
            lambda r: "GC-monitor",
            lambda r, n: (
                f"GC pause of {r.randint(900, 9000)} ms; heap {r.randint(91, 99)}% used after"
                " the collection"
            ),
            lambda n, t: (
                ramp(t, "08:30", "09:00", "09:06", 160.0)
                if n == "node3"
                else ramp(t, "09:55", "10:20", "10:26", 160.0)
                if n == "node2"
                else 0.0
            ),
        ),
        Pattern(
            "ERROR",
            "c.a.j.web.dispatcher.JiraWebworkActionDispatcher",
            http_thread,
            lambda r, n: "Exception thrown while processing request for /rest/api/2/search",
            lambda n, t: 700.0 if n == "node3" and at("09:02") <= t < at("09:07") else 0.0,
            trace=lambda r: oom_trace(r, "Java heap space"),
            http=True,
        ),
        Pattern(
            "ERROR",
            "c.a.j.issue.index.DefaultIndexManager",
            caesium,
            lambda r, n: "Indexing failed for a batch of issues",
            lambda n, t: 500.0 if n == "node2" and at("10:21") <= t < at("10:25") else 0.0,
            trace=lambda r: oom_trace(r, "GC overhead limit exceeded"),
        ),
        Pattern(
            "WARN",
            "c.a.j.cluster.distribution.localq.LocalQCacheManager",
            lambda r: f"localq-reader-{r.randint(0, 3)}",
            lambda r, n: (
                f"Cache replication queue for node {'node3' if r.random() < 0.8 else 'node2'}"
                f" is {r.randint(70, 99)}% full"
            ),
            lambda n, t: (
                ramp(t, "09:04", "09:12", "09:26", 260.0) * others(n, "node3")
                + ramp(t, "10:22", "10:26", "10:38", 140.0) * others(n, "node2")
            ),
        ),
        Pattern(
            "ERROR",
            "c.a.j.cluster.distribution.localq.LocalQCacheOpSender",
            lambda r: f"localq-reader-{r.randint(0, 3)}",
            lambda r, n: (
                "Abandoning sending: LocalQCacheOp{cacheName='com.atlassian.jira.issue."
                f"{r.choice(('IssueCache', 'ProjectCache', 'WorklogCache'))}', action=PUT}}"
                f" to node node3 after {r.randint(3, 9)} attempts"
            ),
            lambda n, t: 180.0 if n != "node3" and at("09:10") <= t < at("09:24") else 0.0,
        ),
        Pattern(
            "ERROR",
            "c.a.j.cluster.lock.WaitingClusterLock",
            http_thread,
            lambda r, n: f"Wait attempt timed out - waited {r.randint(29900, 30100)} milliseconds",
            lambda n, t: (
                (120.0 if n != "node3" and at("09:08") <= t < at("09:23") else 0.0)
                + (60.0 if n in ("node1", "node4") and at("10:23") <= t < at("10:33") else 0.0)
            ),
            http=True,
        ),
        Pattern(
            "WARN",
            "c.a.troubleshooting.healthcheck.concurrent.SupportHealthCheckProcess",
            lambda r: "HealthCheck:thread-1",
            lambda r, n: (
                "Health check 'Cluster Cache Replication' failed with severity 'critical':"
                f" node {'node3' if n != 'node3' else 'node2'} does not replicate"
            ),
            lambda n, t: 12.0 if n != "node3" and at("09:10") <= t < at("09:30") else 0.0,
        ),
    ]


def restarts(node: str) -> list[tuple[datetime, str]]:
    """Return the stop and start entries of a restarted node (node3, then node2)."""
    when = {"node3": ("09:14", "09:19"), "node2": ("10:28", "10:33")}.get(node)
    if when is None:
        return []
    stop, start = (at(x) + timedelta(seconds=17) for x in when)

    def line(t: datetime, logger: str, msg: str) -> str:
        ts = t.strftime("%Y-%m-%d %H:%M:%S,") + f"{t.microsecond // 1000:03d}{TZ}"
        return f"{ts} localhost-startStop-1 INFO      [{logger}] {msg}"

    return [
        (
            stop,
            line(stop, "c.a.j.startup.DefaultJiraLauncher", "Stopping Jira: shutdown requested"),
        ),
        (start, line(start, "c.a.j.startup.JiraStartupLogger", "Jira started in 268 seconds")),
    ]


def poisson(r: random.Random, lam: float) -> int:
    """Draw a Poisson-distributed count with mean ``lam`` (normal approximation above 50)."""
    if lam <= 0:
        return 0
    if lam > 50:
        return max(0, round(r.gauss(lam, math.sqrt(lam))))
    k, p, limit = 0, 1.0, math.exp(-lam)
    while (p := p * r.random()) > limit:
        k += 1
    return k


def entry(r: random.Random, p: Pattern, node: str, t: datetime) -> str:
    """Return one entry of ``p`` at ``t``: header line and stack trace, as Jira writes them."""
    ts = t.strftime("%Y-%m-%d %H:%M:%S,") + f"{t.microsecond // 1000:03d}{TZ}"
    thread = p.thread(r)
    msg = p.message(r, node)
    if p.http:
        user = f"user{r.randint(1, 48)}"
        path = r.choice(PATHS).format(n=r.randint(1, 9000))
        req = f"{t.hour * 60 + t.minute}x{r.randint(100000, 999999)}x{r.randint(1, 4)}"
        sess = "".join(r.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(7))
        ip = f"10.0.{r.randint(1, 30)}.{r.randint(2, 250)}"
        head = (
            f"{ts} {thread} url: {path}; user: {user} {p.level} {user} {req} {sess} {ip}"
            f" {path} [{p.logger}] {msg}"
        )
    else:
        head = f"{ts} {thread} {p.level} anonymous     [{p.logger}] {msg}"
    return "\n".join([head, *(p.trace(r) if p.trace else [])])


def write_node(
    out: Path,
    node: str,
    start: datetime,
    days: int,
    seed: int,
    kinds: Callable[[], list[Pattern]] = patterns,
    step: timedelta = timedelta(hours=1),
    extra: Sequence[tuple[datetime, str]] = (),
) -> int:
    """Write ``days`` days of one node's log into ``out``; return the number of entries.

    ``kinds`` gives the patterns, whose rates are per hour and read at the start of each
    ``step``; ``extra`` adds entries at fixed times.
    """
    r = random.Random(seed)
    rows: list[tuple[datetime, str]] = list(extra)
    span = step // timedelta(milliseconds=1)
    for i in range(int(timedelta(days=days) / step)):
        t0 = start + i * step
        for p in kinds():
            for _ in range(poisson(r, p.rate(node, t0) * step / timedelta(hours=1))):
                t = t0 + timedelta(milliseconds=r.randint(0, span - 1))
                rows.append((t, entry(r, p, node, t)))
    rows.sort(key=lambda x: x[0])
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{node}_atlassian-jira.log").write_text(
        "\n".join(text for _, text in rows) + "\n", encoding="utf-8", newline="\n"
    )
    return len(rows)


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    for name, start, seed in (
        ("prev", datetime(2026, 10, 26), 1),
        ("week", datetime(2026, 11, 2), 2),
    ):
        for i, node in enumerate(NODES):
            n = write_node(args.out / name, node, start, 3, seed * 10 + i)
            print(f"{name}/{node}: {n} entries")
    for i, node in enumerate(INCIDENT_NODES):
        n = write_node(
            args.out / "incident",
            node,
            INCIDENT,
            1,
            30 + i,
            kinds=incident_patterns,
            step=timedelta(minutes=1),
            extra=restarts(node),
        )
        print(f"incident/{node}: {n} entries")


if __name__ == "__main__":
    main()
