"""Commands over the nodes of a capture: capture, timeline, matrix, events, interactions.

``entries``, ``context --all-nodes`` and ``report`` take a capture too (``browse``,
``compare``); the documents are built by :mod:`ragnalog.service.capture_queries`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from cyclopts import Parameter

from ragnalog.cli import context as ctx
from ragnalog.cli import params, render
from ragnalog.service import capture_queries as cq
from ragnalog.service.capture_queries import CaptureWindow
from ragnalog.service.errors import OpError
from ragnalog.service.params import EntryQuery, GroupQuery, Page

# polars, DuckDB and rich are imported by the code that needs them.

__all__ = ["capture", "events", "interactions", "matrix", "timeline"]

OVERLAP = CaptureWindow()
"""Default of the flattened window options: the nodes' overlap."""


def capture(
    ref: str,
    /,
    *,
    w: Annotated[CaptureWindow, Parameter(name="*")] = OVERLAP,
    triage: Annotated[bool, Parameter(negative="--no-triage")] = True,
) -> None:
    """Show the nodes of a capture side by side: coverage, volume, top error, events, verdict.

    REF is a capture (capture:ID, a unique id prefix; cluster:NAME, its newest
    capture; cluster:NAME@YYYY-MM-DD) or a log set: the capture the set belongs
    to, else that set as a capture of one node. Every capture view counts
    within one window: --window overlap (default; the time all nodes have logs
    for, so a group is not "only on NODE2" because NODE1's files start later),
    union, or --since/--until. Each node is triaged against its own previous
    set (never another node); --no-triage skips it.

    Output (json):
      {"schema": "capture.v1", "capture": id | null, "cluster": str | null,
       "window": {"mode": "overlap" | "union" | "explicit", "since", "until"},
       "nodes": [{"node", "set": {"id", "name"}, "first_ts", "last_ts",
                  "coverage": {"since", "until"} | null, "gaps": [[since, until]],
                  "entries", "errors", "warnings", "errors_per_hour",
                  "warnings_per_hour",
                  "top_error": {"signature", "template", "count"} | null,
                  "verdict": str | null, "baseline": {"id", "name", "reason"} | null,
                  "events": {kind: count}}],
       "skew": [{"a", "b", "offset_ms", "pairs"}]}
    "coverage" is the node's first to last entry within the window, "gaps"
    runs of at least 15 minutes without entries inside it; rates are per hour
    of coverage. "verdict" is the node's triage verdict (as `triage`;
    no_baseline when it has no earlier set; null with --no-triage). "skew" is
    a clock skew estimate from paired events, shown, never applied.

    Exit codes: 0 ok; 1 bad option or time, or the nodes' logs do not
    overlap (use --window union); 2 unknown capture; 3 unknown set or store
    error.

    Args:
        ref: Capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) or log set.
        w: The window (--window, --since, --until).
        triage: Verdict per node vs its own previous set (triage).

    """
    doc = cq.capture(ctx.store(), ref, w, triage=triage, front="cli")
    out = ctx.out()
    out.document(doc, items=None, table=lambda console: render.capture(console, doc))


def timeline(
    ref: str,
    /,
    *,
    q: Annotated[EntryQuery, Parameter(name="*")] = params.ALL_ENTRIES,
    bucket: str = "1h",
    window: Literal["overlap", "union"] | None = None,
    by_node: bool = False,
) -> None:
    """Count entries per time bucket: of a log set, or per node of a capture.

    A log set gives timeline.v1 (as GET /api/sets/{ref}/timeline). A capture
    reference (capture:ID, cluster:NAME[@DAY]), or a set with --by-node, gives
    cluster-timeline.v1 of its capture: per node and bucket the entries by
    level, or with --signature the entries of that group; buckets run over the
    window (--window overlap|union, --since/--until, as `capture`).

    Output (json):
      {"schema": "timeline.v1", "logset": id, "bucket": str, "count": int,
       "items": [{"bucket", "count"}]}
      {"schema": "cluster-timeline.v1", "bucket": str, "buckets": [iso],
       "nodes": {NODE: {"levels": {"ERROR"|"WARN"|"INFO"|"DEBUG"|"OTHER": [n]},
                        "coverage": [first, last] | null}},
       "signature"?: str}
    Bucket times are UTC, aligned to the epoch. "coverage" gives the first and
    last bucket index in which the node has logs; with --signature "levels"
    has only "count". Table mode prints one row per bucket (a capture: one
    column per node).

    Exit codes: 0 ok; 1 bad option, bucket, level or time, --window with
    a set, --node with a set, several signatures of a capture, or an ambiguous
    signature prefix; 2 unknown capture or group; 3 unknown set or store error.

    Both take the filters of `entries` (--level, --min-level, --logger, --user,
    --thread, --request-id, --contains, --with-exception, --exception,
    --coarse-signature, --signature, --since, --until), so the chart of a
    filtered listing counts what the listing shows. A capture also takes
    --node (keep those nodes), and one --signature only (no NODE:SIGNATURE).

    Args:
        ref: Log set, or capture (capture:ID, cluster:NAME[@YYYY-MM-DD]).
        q: Entry filters (the options --signature to --node, see EntryQuery).
        bucket: Bucket size: <n>s, <n>m, <n>h or <n>d (15m, 1h, 1d).
        window: overlap or union (a capture only; default overlap).
        by_node: Per node of the set's capture (a set in no capture: that one node).

    """
    store, out = ctx.store(), ctx.out()
    if by_node or cq.names_capture(store, ref):
        w = CaptureWindow(window=window or "overlap", since=q.since, until=q.until)
        doc = cq.timeline(store, ref, w, bucket=bucket, q=q, front="cli")
        out.document(doc, items=None, table=lambda console: render.cluster_timeline(console, doc))
        return
    if window is not None:
        raise OpError(
            "usage",
            "--window applies to a capture",
            hint="pass a capture (capture:ID, cluster:NAME) or --by-node",
        )
    from ragnalog.service import queries

    doc = queries.timeline(store, ref, q, bucket=bucket, front="cli")
    out.document(doc, columns=("bucket", "count"), title=f"timeline of {doc['logset']}")


def matrix(
    ref: str,
    /,
    *,
    q: Annotated[GroupQuery, Parameter(name="*")] = params.ALL_GROUPS,
    window: Literal["overlap", "union"] = "overlap",
    node: str | None = None,
    spread: Literal["all", "only", "skewed"] | None = None,
    sort: Literal["count", "skew", "lead"] = "count",
    skew_min: float = 4.0,
    signature: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> None:
    """Compare the groups of the nodes of a capture: one row per group, a column per node.

    Answers "is it cluster-wide or one node's problem, and which node saw it
    first?". Counts are restricted to the window (--window, or --since/--until
    as the window); the other filters are those of `groups`. "spread" is only
    (on one node), skewed (missing from a node that has logs, or the max/min
    of count / node entries is at least --skew-min) or all; "lead_ms" is the
    gap between the first two nodes' first entries.

    Output (json):
      {"schema": "matrix.v1", "window": {"mode", "since", "until"},
       "nodes": [NODE], "count": int, "total": int, "offset": int,
       "truncated": bool,
       "items": [{"signature", "template", "level", "level_name", "logger",
                  "exception_type", "total",
                  "per_node": {NODE: {"count", "rate_per_hour", "first_seen",
                                      "last_seen"}},
                  "spread": "all" | "only" | "skewed", "skew": float | null,
                  "first_node": str | null, "lead_ms": int | null}]}
    Rows are sorted by --sort (count = total, skew, lead; descending), at most
    --limit from --offset; "total" counts the matching rows. Rates are per
    hour of each node's coverage.

    Exit codes: 0 ok (also when nothing matches); 1 bad option, level, time,
    node or ambiguous signature prefix; 2 unknown capture or group; 3 unknown
    set or store error.

    Args:
        ref: Capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) or log set.
        q: Group filters (--level to --contains, as `groups`; --since/--until set the window).
        window: overlap or union (--since/--until make the window explicit).
        node: Only groups present on this node.
        spread: Only rows with this spread: all, only (one node), skewed.
        sort: count (total), skew, lead (lead_ms); descending.
        skew_min: Rate ratio from which a group is skewed (rates per node entries).
        signature: Only this group (signature or unique prefix).
        limit: Maximum number of rows; 0 = all.
        offset: Skip this many rows first (paging: next offset = offset + count).

    """
    page = Page.parse(limit, offset, front="cli")
    doc = cq.matrix(
        ctx.store(),
        ref,
        q,
        page,
        window=window,
        node=node,
        spread=spread,
        sort=sort,
        skew_min=skew_min,
        signature=signature,
        front="cli",
    )
    out = ctx.out()
    out.document(doc, table=lambda console: render.matrix(console, doc, color=out.color))


def events(
    ref: str,
    /,
    *,
    w: Annotated[CaptureWindow, Parameter(name="*")] = OVERLAP,
    kind: list[str] | None = None,
) -> None:
    """List what every node of a capture did: restarts, index, locks, health checks, jobs.

    Events come from rules over the group templates of the format (Jira:
    start, stop, health_failed, cache_replication, index_replication,
    index_snapshot, index_restore, index_replay, reindex, cluster_lock,
    cache_peer, job_run). Entries of one node, kind and subject close in time
    fold into one event; "pairs" are kinds on different nodes that follow each
    other within 60 s more often than by chance.

    Output (json):
      {"schema": "events.v1", "rules_version": int, "count": int,
       "items": [{"node", "ts", "last_ts", "kind", "subject", "subject_node",
                  "signature", "entry_id", "count", "text"}],
       "pairs": [{"a": {"node", "kind"}, "b": {"node", "kind"}, "count", "of",
                  "median_lag_ms", "lift", "examples": [[ts_a, ts_b]]}]}
    "subject" is the node, check, lock or job the event names; "subject_node"
    the member node it names. Items are in time order.

    Exit codes: 0 ok (also when there are none); 1 bad option, kind or time;
    2 unknown capture; 3 unknown set or store error.

    Args:
        ref: Capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) or log set.
        w: The window (--window, --since, --until).
        kind: Only these kinds (comma-separated and/or repeated).

    """
    doc = cq.events(ctx.store(), ref, w, kinds=kind, front="cli")
    ctx.out().document(doc, table=lambda console: render.events(console, doc))


def interactions(
    ref: str,
    /,
    *,
    w: Annotated[CaptureWindow, Parameter(name="*")] = OVERLAP,
    delta: int = 300,
) -> None:
    """Show how the nodes of a capture affect each other.

    "mentions": entries on one node naming another node id, with the groups
    behind each edge. "co_bursts": a group bursting on one node followed by a
    group bursting on another within --delta seconds, at least 3 times and 3
    times more often than chance. "concurrent_jobs": one job key run on two or
    more nodes within a minute. "sessions": a session seen on two nodes within
    an hour (lost sticky session or failover). "jobs": job runs per node.

    Output (json):
      {"schema": "interactions.v1", "delta_s": int,
       "mentions": [{"from", "to", "count", "groups": [sig]}],
       "co_bursts": [{"a": {"node", "signature", "template", "groups"},
                      "b": {...}, "hits", "of", "median_lag_ms", "lift"}],
       "concurrent_jobs": [{"job", "nodes", "slots", "examples": [ts]}],
       "sessions": [{"nodes": [a, b], "count"}],
       "jobs": [{"job", "runs": {NODE: n}}]}

    Exit codes: 0 ok; 1 bad option or time; 2 unknown capture; 3 unknown set
    or store error.

    Args:
        ref: Capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) or log set.
        w: The window (--window, --since, --until).
        delta: Co-burst window in seconds (b bursts at most this long after a).

    """
    doc = cq.interactions(ctx.store(), ref, w, delta_s=delta, front="cli")
    ctx.out().document(doc, items=None, table=lambda console: render.interactions(console, doc))
