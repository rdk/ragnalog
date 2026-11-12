"""Write synthetic Jira Data Center logs for demos and the README screenshots.

Usage::

    uv run python scripts/demo_logs.py OUT          # OUT/week and OUT/prev, two nodes each
    atlas-log --store DEMO ingest OUT/prev --cluster demo-jira --name prev
    atlas-log --store DEMO ingest OUT/week --cluster demo-jira --name week
    atlas-log --store DEMO serve

Everything is invented: users ``user1``…, issue keys ``ABC-n``, ``jira.example.com``,
``10.0.x.x`` addresses, ``org.example`` plugins. ``week`` holds three days of two nodes
with everyday noise, scheduled jobs, a directory-sync outage on node2 (with cache
replication warnings on node1 at the same time) and two error types that ``prev`` (the
week before) does not have; ``prev`` has one that ``week`` lost. Seeded: the same output
every run.
"""

from __future__ import annotations

import argparse
import math
import random
from collections.abc import Callable
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


def write_node(out: Path, node: str, start: datetime, days: int, seed: int) -> int:
    """Write ``days`` days of one node's log into ``out``; return the number of entries."""
    r = random.Random(seed)
    rows: list[tuple[datetime, str]] = []
    for hour in range(days * 24):
        t0 = start + timedelta(hours=hour)
        for p in patterns():
            for _ in range(poisson(r, p.rate(node, t0))):
                t = t0 + timedelta(milliseconds=r.randint(0, 3_599_999))
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


if __name__ == "__main__":
    main()
