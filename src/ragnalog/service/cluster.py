"""Cluster analytics: the sets of the nodes of one capture, viewed together.

A :class:`Capture` is a view over member sets, one per node (docs/cluster.md).
:func:`open_capture` checks that they were signed alike, :func:`resolve_window`
picks the time window, and the builders return the documents of the capture
views as msgspec Structs (``service/documents.py``): ``capture.v1``,
``cluster-timeline.v1``, ``matrix.v1``, ``events.v1``, ``interactions.v1``,
``entries.v1`` with ``node`` and ``set`` on each row, and ``cluster-report.v1``.

The builders run DuckDB over ``read_parquet`` of the member sets' files, with the
node added as a literal column and the session time zone UTC; only aggregates,
event entries and the requested rows reach Python. Merged entries use polars, as
:class:`~ragnalog.store.filters.EntryFilter` is a polars predicate.

Rules and thresholds (docs/cluster.md, sections 4, 6, 7):

- Window: ``overlap`` = latest first entry to earliest last entry of the nodes,
  ``union`` = earliest first to latest last, ``explicit`` = ``since``/``until``.
  Both ends are inclusive. Coverage of a node = its first to its last entry,
  cut to the window; gaps = runs of at least 15 empty minutes inside it; rates
  are per hour of coverage.
- Matrix: ``only`` = on one node; ``skewed`` = missing from a node that has logs in
  the window, or max/min of ``count / node entries in the window`` (over the nodes
  that have the group) at least ``skew_min``; ``all`` otherwise.
- Events: group templates matched by the format's event rules
  (:mod:`ragnalog.core.events`); entries of one node, kind and subject
  within 60 s of an event's first entry fold into it (job_run 120 s, start and
  stop 10 min); in ``events.v1`` items, repeats of health_failed (2 h) and job_run
  (30 min) fold further. Pairs: events of kind b on another node at most 60 s after
  an event a, at least 3 times more often than chance; job_run is left out (see
  concurrent jobs).
- Clock skew: pairs of index, re-index and cluster lock events less than 5 s
  apart; median offset per node pair.
- Mentions: entries whose message names another node id (whole word,
  case-sensitive).
- Co-bursts: minute counts per node and group (WARN and above, or behind an
  event that is not health_failed or job_run, which recur by schedule). A burst
  starts at a minute with at least the group's usual count (the median of its
  non-empty minutes) after at least 10 minutes below it; the 200 groups with most
  bursts per node are kept. Groups of one node that burst together (80% of the
  bursts of each within ``delta_s`` of the other) are one family, shown by the
  member with most bursts. Pairs (g on A, h on B) count the bursts of g followed by
  a burst of h within ``delta_s``; lift = hits / (bursts of g x P(a burst of h in a
  window of delta_s)); reported when hits >= 3, hits >= half the bursts of g and
  lift >= 3, by hits then lift, each group at most once on each side (a
  same-minute pair only one way round).
- Concurrent jobs: job_run events with the same job key on two or more nodes
  within 60 s. Sessions: a ``session`` seen on two nodes within an hour.
"""

from __future__ import annotations

import bisect
import dataclasses
import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Self, cast

from ragnalog.service.documents import (
    BurstSide,
    ClusterBaseline,
    ClusterCapture,
    ClusterCaptureBody,
    ClusterEvent,
    ClusterEvents,
    ClusterEventsBody,
    ClusterInteractions,
    ClusterInteractionsBody,
    ClusterMatrix,
    ClusterNodeSummary,
    ClusterNodeTimeline,
    ClusterReport,
    ClusterReportGroup,
    ClusterReportOptions,
    ClusterSetRef,
    ClusterSkew,
    ClusterSpan,
    ClusterTimeline,
    ClusterTimelineBody,
    ClusterTopError,
    ClusterWindow,
    CoBurst,
    ConcurrentJob,
    Entries,
    EventPair,
    EventSide,
    JobRuns,
    MatrixNodeCount,
    MatrixRow,
    MentionEdge,
    SessionPair,
    Verdict,
)
from ragnalog.service.errors import Front, OpError, flag, hint
from ragnalog.service.params import (
    DEFAULT_MAX_RAW,
    TextCap,
    hex_ref,
    parse_columns,
    parse_time,
)

if TYPE_CHECKING:
    import duckdb
    import polars as pl

    from ragnalog.core.events import EventRule
    from ragnalog.service.sets import Sets
    from ragnalog.store.filters import EntryFilter, GroupFilter
    from ragnalog.store.logset import LogSet

__all__ = [
    "MATRIX_SORTS",
    "SPREADS",
    "Capture",
    "ClusterNode",
    "Window",
    "capture_summary",
    "cluster_payload",
    "cluster_timeline",
    "context_all_nodes",
    "events",
    "interactions",
    "matrix",
    "merged_entries",
    "open_capture",
    "resolve_signature",
    "resolve_signatures",
    "resolve_window",
]

type WindowMode = Literal["overlap", "union", "explicit"]
type Spread = Literal["all", "only", "skewed"]

SPREADS: tuple[Spread, ...] = ("all", "only", "skewed")
"""Values of ``matrix.v1`` ``spread`` (and of the ``spread`` filter)."""
MATRIX_SORTS = ("count", "skew", "lead")
"""Values of ``matrix(sort=...)``: total entries, skew, lead time; all descending."""
WINDOW_MODES = ("overlap", "union")
"""Values of ``resolve_window(mode=...)``; ``explicit`` comes from since/until."""

GAP_MINUTES = 15
"""Shortest run of empty minutes reported as a gap."""
MAX_GAPS = 100
"""Gaps listed per node."""
SKEW_MIN = 4.0
"""Default ``skew_min`` of :func:`matrix`."""
PAIR_DELTA_MS = 60_000
"""An event pair: b at most this long after a."""
MAX_PAIRS = 50
MAX_EXAMPLES = 3
"""Examples per event pair and per concurrent job."""
SKEW_MAX_LAG_MS = 5_000
SKEW_KINDS = frozenset({"index_snapshot", "index_replay", "reindex", "cluster_lock"})
"""Kinds whose cross-node pairs are taken as simultaneous for the clock skew estimate."""
FOLD_MS = {"job_run": 120_000, "start": 600_000, "stop": 600_000}
"""How long after an event's first entry entries of the same kind and subject fold into it."""
DEFAULT_FOLD_MS = 60_000
STREAK_MS = {"health_failed": 2 * 3_600_000, "job_run": 30 * 60_000}
"""Scheduled kinds: in ``events.v1`` items, an event that repeats one of the same node, kind
and subject at most this long after it is folded into it (a check failing every hour for
a week is one item)."""
SCHEDULED_KINDS = frozenset(STREAK_MS)
"""Kinds whose groups are left out of co-bursts: they recur by schedule."""
PAIR_MIN_LIFT = 3.0
"""Event pairs are reported when they occur this many times more often than by chance."""
EVENT_TEXT_CHARS = 300
JOB_SLOT_MS = 60_000
MAX_JOBS = 100
SESSION_HOURS = 1
BURST_QUIET_MINUTES = 10
BURST_MAX_GROUPS = 200
"""Groups per node considered for co-bursts (most bursts first)."""
BURST_MIN_HITS = 3
BURST_MIN_LIFT = 3.0
BURST_MIN_SHARE = 0.5
"""A co-burst needs a burst of b after at least this share of the bursts of a."""
FAMILY_SHARE = 0.8
"""Groups of one node whose bursts coincide this often (both ways) are one co-burst family."""
MAX_CO_BURSTS = 50
MAX_MENTION_GROUPS = 20
CONTEXT_MAX_ROWS = 2_000
"""Rows of :func:`context_all_nodes`; the ones nearest the anchor are kept."""
SHORT_WINDOW_MS = 12 * 3_600_000
"""cluster-report.v1 uses 15 minute buckets for windows shorter than this, else 1 hour."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)
_NO_TIME = datetime(1971, 1, 1, tzinfo=UTC)
"""Timestamps before this are the parser's placeholder for lines without one."""
_HEX = re.compile(r"^[0-9a-f]{4,16}$")


# --- capture and window


@dataclass(frozen=True, slots=True)
class ClusterNode:
    """One node of a capture and its log set."""

    node: str
    logset: LogSet


@dataclass(frozen=True, slots=True)
class Capture:
    """The sets of the nodes of one capture (a view; nothing is stored)."""

    id: str | None
    cluster: str | None
    nodes: tuple[ClusterNode, ...]
    """Sorted by node id."""

    @property
    def node_ids(self) -> list[str]:
        """Node ids in order."""
        return [m.node for m in self.nodes]

    @property
    def format(self) -> str:
        """Format of the member sets (they all share it)."""
        return self.nodes[0].logset.manifest.format

    @property
    def ref(self) -> str:
        """``capture:<id>``, or ``capture:`` and the member set ids when it has no id."""
        return "capture:" + (self.id or ",".join(sorted(m.logset.id for m in self.nodes)))


@dataclass(frozen=True, slots=True)
class Window:
    """A time window of the capture views; both ends inclusive, UTC."""

    mode: WindowMode
    since: datetime
    until: datetime

    def doc(self) -> ClusterWindow:
        """Return the ``window`` part of the documents."""
        return ClusterWindow(mode=self.mode, since=_iso(self.since), until=_iso(self.until))

    @property
    def ms(self) -> tuple[int, int]:
        """``(since, until)`` in epoch milliseconds."""
        return _ms(self.since), _ms(self.until)


def open_capture(
    nodes: Sequence[tuple[str, LogSet]],
    *,
    capture_id: str | None = None,
    cluster: str | None = None,
    front: Front = "api",
) -> Capture:
    """Build a capture from ``(node id, set)`` pairs; the nodes are sorted by id.

    Raises:
        OpError: ``invalid_value`` for no nodes or a node or set given twice; reason
            ``incompatible_sets`` (API ``incompatible_sets``, CLI ``invalid_value``)
            when the sets differ in format or ``recipe_key``.

    """
    if not nodes:
        raise OpError("invalid_value", "a capture needs at least one node")
    members = tuple(sorted((ClusterNode(str(n), ls) for n, ls in nodes), key=lambda m: m.node))
    for kind, values in (
        ("node", [m.node for m in members]),
        ("set", [m.logset.id for m in members]),
    ):
        twice = sorted({v for v in values if values.count(v) > 1})
        if twice and kind == "node":
            sets = [m.logset.manifest.name or m.logset.id for m in members if m.node in twice]
            raise OpError(
                "invalid_value",
                f"node {', '.join(twice)} given twice in the capture (sets {', '.join(sets)})",
                hint="give one of the sets its own node with `ragnalog label SET --node NODE`, "
                "or move it to another capture with `--capture`",
            )
        if twice:
            raise OpError("invalid_value", f"{kind} {', '.join(twice)} given twice in the capture")
    first = members[0]
    for other in members[1:]:
        a, b = first.logset.manifest, other.logset.manifest
        if a.format == b.format and a.recipe_key == b.recipe_key:
            continue
        from ragnalog.core.pipeline import recipe_differences

        problems = recipe_differences(a.recipe_key, b.recipe_key)
        if a.format != b.format:
            problems = [f"format: {a.format} != {b.format}", *problems]
        raise OpError.because(
            "incompatible_sets",
            front,
            f"the sets of nodes {first.node} and {other.node} are not comparable "
            f"({'; '.join(problems)})",
            hint="rebuild the older set with `ragnalog upgrade SET` (same spec) or "
            "`ragnalog regroup SET` (another spec)",
        )
    return Capture(id=capture_id, cluster=cluster, nodes=members)


def _node_range(ls: LogSet) -> tuple[datetime, datetime] | None:
    """First and last entry of a set (manifest; the data when the manifest has no times)."""
    m = ls.manifest
    if m.first_ts is not None and m.last_ts is not None and m.first_ts >= _NO_TIME:
        return m.first_ts.astimezone(UTC), m.last_ts.astimezone(UTC)
    import polars as pl

    ms = pl.col("ts").dt.epoch("ms")
    row = (
        ls.entries_lazy()
        .filter(pl.col("ts") >= _NO_TIME)
        .select(ms.min().alias("a"), ms.max().alias("b"))
        .collect()
        .row(0)
    )
    if row[0] is None:
        return None
    return _dt(int(row[0])), _dt(int(row[1]))


def resolve_window(
    capture: Capture,
    mode: str = "overlap",
    since: datetime | str | None = None,
    until: datetime | str | None = None,
    *,
    front: Front = "api",
) -> Window:
    """Return the window of the capture views.

    ``overlap`` runs from the latest first entry of the nodes to the earliest last
    entry, ``union`` from the earliest first to the latest last. With ``since`` or
    ``until`` the mode is ``explicit`` and ``mode`` does not apply: a missing end is the
    union's (the first or the last entry of the capture). Times are
    datetimes (naive = UTC) or the strings of ``--since`` (ISO 8601, a date, or
    ``30m``/``2h``/``1d`` before the capture's last entry). An explicit window is
    clamped to the union of the logs.

    Raises:
        OpError: ``invalid_value`` for an unknown mode, a bad time, ``since`` after
            ``until``, an explicit window outside the logs, no entries at all, or an
            empty overlap (hint: union).

    """
    if mode not in WINDOW_MODES:
        raise OpError(
            "invalid_value",
            f"{flag('window', front)} {mode!r} is not a window; expected overlap or union",
        )
    ranges = [r for m in capture.nodes if (r := _node_range(m.logset)) is not None]
    if not ranges:
        raise OpError("invalid_value", "the capture has no entries with a timestamp")
    first, last = min(r[0] for r in ranges), max(r[1] for r in ranges)
    if mode == "overlap":
        start, end = max(r[0] for r in ranges), min(r[1] for r in ranges)
    else:
        start, end = min(r[0] for r in ranges), last
    t0 = _time(since, "since", last, front)
    t1 = _time(until, "until", last, front)
    if t0 is not None or t1 is not None:
        if t0 is not None and t1 is not None and t0 > t1:
            raise OpError(
                "invalid_value",
                f"{flag('since', front)} {_iso(t0)} is after {flag('until', front)} {_iso(t1)}",
            )
        start = t0 if t0 is not None else first
        end = t1 if t1 is not None else last
        if start > last or end < first:
            raise OpError(
                "invalid_value",
                f"the window {_iso(start)} → {_iso(end)} holds no entries of the capture "
                f"({_iso(first)} → {_iso(last)})",
            )
        # clamped to the logs: an open end (until=9999-12-31) must not make empty buckets
        return Window("explicit", max(start, first), min(end, last))
    if start > end:
        raise OpError(
            "invalid_value",
            f"the nodes' logs do not overlap: the latest first entry {_iso(start)} is after "
            f"the earliest last entry {_iso(end)}",
            hint=f"use {flag('window', front)} union, or give {flag('since', front)} and "
            f"{flag('until', front)}",
        )
    return Window(cast(WindowMode, mode), start, end)


def _time(value: datetime | str | None, name: str, last: datetime, front: Front) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    parsed = parse_time(value, name, last, front=front)
    return parsed.astimezone(UTC) if parsed is not None else None


# --- time helpers


def _ms(ts: datetime) -> int:
    return (ts - _EPOCH) // _MS


def _dt(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


def _iso(value: datetime | int) -> str:
    """ISO 8601 UTC with milliseconds, as rows are written (``…T09:00:00.000+00:00``)."""
    ms = value if isinstance(value, int) else _ms(value)
    return f"{_dt(ms):%Y-%m-%dT%H:%M:%S}.{ms % 1000:03d}+00:00"


def _bucket_iso(ms: int) -> str:
    return f"{_dt(ms):%Y-%m-%dT%H:%M:%S}Z"


def _hours(span_ms: int) -> float:
    return max(span_ms / 3_600_000, 1 / 60)


# --- DuckDB over the member sets


def _sql_text(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class _Db:
    """DuckDB views ``entries`` and ``groups`` over a capture's sets, with ``node`` and ``set_id``.

    Also caches what several builders need: the group attributes and the events.
    """

    def __init__(self, capture: Capture) -> None:
        import duckdb

        self.capture = capture
        self.con: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
        self.con.execute("SET TimeZone = 'UTC'")
        for view, attr in (("entries", "entries_path"), ("groups", "groups_path")):
            parts = [
                f"SELECT {_sql_text(m.node)} AS node, {_sql_text(m.logset.id)} AS set_id, * "
                f"FROM read_parquet({_sql_text(getattr(m.logset, attr).resolve().as_posix())})"
                for m in capture.nodes
            ]
            self.con.execute(f"CREATE VIEW {view} AS " + " UNION ALL BY NAME ".join(parts))
        self._attrs: dict[str, tuple[str | None, int, str, str | None, str | None]] | None = None
        self._events: dict[tuple[int, int], _Events] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.con.close()

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[tuple[Any, ...]]:
        """Run ``sql`` and return all rows."""
        return self.con.execute(sql, list(params)).fetchall()

    def attrs(self) -> dict[str, tuple[str | None, int, str, str | None, str | None]]:
        """Group attributes by signature: ``(template, level, level_name, logger, exception)``."""
        if self._attrs is None:
            found = self.rows(
                "SELECT signature, any_value(template), any_value(level), "
                "any_value(level_name::VARCHAR), any_value(logger::VARCHAR), "
                "any_value(exception_type) FROM groups GROUP BY signature"
            )
            self._attrs = {r[0]: (r[1], int(r[2]), str(r[3]), r[4], r[5]) for r in found}
        return self._attrs

    def events(self, window: Window) -> _Events:
        """Return the events of the window (computed once per window)."""
        key = window.ms
        if key not in self._events:
            self._events[key] = _collect_events(self, window)
        return self._events[key]


_WINDOW = "ts BETWEEN ? AND ?"


def _wp(window: Window) -> list[object]:
    return [window.since, window.until]


# --- events


@dataclass(slots=True)
class _Event:
    node: str
    ts: int
    kind: str
    subject: str | None
    subject_node: str | None
    signature: str
    entry_id: str
    text: str
    last: int = 0
    count: int = 1

    def doc(self) -> ClusterEvent:
        return ClusterEvent(
            node=self.node,
            ts=_iso(self.ts),
            last_ts=_iso(self.last),
            kind=self.kind,
            subject=self.subject,
            subject_node=self.subject_node,
            signature=self.signature,
            entry_id=self.entry_id,
            text=self.text,
            count=self.count,
        )


@dataclass(slots=True)
class _Events:
    items: list[_Event]
    """Events with entries folded per :data:`FOLD_MS`, by time."""
    kinds: dict[str, str] = field(default_factory=dict)
    """Kind of each signature behind an event."""

    def streaks(self) -> list[_Event]:
        """Return the items of ``events.v1``: scheduled repeats folded (:data:`STREAK_MS`)."""
        out: list[_Event] = []
        open_items: dict[tuple[str, str, str | None], _Event] = {}
        for e in self.items:
            gap = STREAK_MS.get(e.kind)
            key = (e.node, e.kind, e.subject)
            current = open_items.get(key)
            if current is not None and gap is not None and e.ts - current.last <= gap:
                current.count += e.count
                current.last = max(current.last, e.last)
                continue
            item = _Event(
                node=e.node,
                ts=e.ts,
                kind=e.kind,
                subject=e.subject,
                subject_node=e.subject_node,
                signature=e.signature,
                entry_id=e.entry_id,
                text=e.text,
                last=e.last,
                count=e.count,
            )
            open_items[key] = item
            out.append(item)
        return out


def _rules(capture: Capture) -> tuple[EventRule, ...]:
    from ragnalog.core import formats
    from ragnalog.core.events import event_rules

    try:
        return event_rules(formats.get(capture.format))
    except LookupError:
        return ()


def _node_of(subject: str | None, nodes: dict[str, str]) -> str | None:
    if subject is None:
        return None
    return nodes.get(subject) or nodes.get(subject.casefold())


def _collect_events(db: _Db, window: Window) -> _Events:
    """Match the group templates with the event rules, then fold the matching entries."""
    from ragnalog.core.events import classify

    rules = _rules(db.capture)
    matched: dict[str, tuple[EventRule, re.Match[str]]] = {}
    titles: dict[str, str] = {}
    for sig, (template, _, _, logger, _) in db.attrs().items():
        found = classify(rules, logger, template)
        if found is not None:
            matched[sig] = found
            titles[sig] = template or ""
    if not matched:
        return _Events([])
    rows = db.rows(
        "SELECT node, epoch_ms(ts), signature, id, logger::VARCHAR, message FROM entries "
        f"WHERE {_WINDOW} AND signature IN (SELECT unnest(?)) ORDER BY node, ts, id",
        [*_wp(window), sorted(matched)],
    )
    nodes = {n: n for n in db.capture.node_ids} | {n.casefold(): n for n in db.capture.node_ids}
    open_events: dict[tuple[str, str, str | None], _Event] = {}
    items: list[_Event] = []
    for node, ts, sig, entry_id, logger, message in rows:
        rule, match = matched[sig]
        subject = rule.subject(logger, match, message)
        key = (node, rule.kind, subject)
        current = open_events.get(key)
        if current is not None and ts - current.ts <= FOLD_MS.get(rule.kind, DEFAULT_FOLD_MS):
            current.count += 1
            current.last = int(ts)
            continue
        event = _Event(
            node=node,
            ts=int(ts),
            kind=rule.kind,
            subject=subject,
            subject_node=_node_of(subject, nodes),
            signature=sig,
            entry_id=entry_id,
            # a blank message (text on the lines below it): the group's title instead
            text=(message if message and not message.isspace() else titles[sig])[:EVENT_TEXT_CHARS],
            last=int(ts),
        )
        open_events[key] = event
        items.append(event)
    items.sort(key=lambda e: (e.ts, e.node, e.kind, e.entry_id))
    return _Events(items, {sig: rule.kind for sig, (rule, _) in matched.items()})


@dataclass(slots=True)
class _Pairs:
    pairs: list[EventPair]
    skew: list[ClusterSkew]


def _event_pairs(items: Sequence[_Event], node_order: Sequence[str], span_ms: int) -> _Pairs:
    """Kind pairs on different nodes within :data:`PAIR_DELTA_MS`, and the skew estimate.

    A pair is listed when its count is at least :data:`PAIR_MIN_LIFT` times the count
    expected by chance: ``of x P(an event b within 60 s)``, the probability being
    ``events b x 60 s / span_ms`` (at most 1).
    """
    series: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in items:
        series[(e.node, e.kind)].append(e.ts)
    for values in series.values():
        values.sort()
    keys = sorted(series)
    order = {n: i for i, n in enumerate(node_order)}
    pairs: list[tuple[tuple[int, int, str, str, str, str], EventPair]] = []
    offsets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for a in keys:
        times_a = series[a]
        for b in keys:
            if a[0] == b[0] or "job_run" in (a[1], b[1]):
                continue
            times_b = series[b]
            lags: list[int] = []
            examples: list[tuple[str, str]] = []
            for t in times_a:
                i = bisect.bisect_left(times_b, t)
                if i < len(times_b) and times_b[i] - t <= PAIR_DELTA_MS:
                    lags.append(times_b[i] - t)
                    if len(examples) < MAX_EXAMPLES:
                        examples.append((_iso(t), _iso(times_b[i])))
            if not lags:
                continue
            chance = min(1.0, len(times_b) * PAIR_DELTA_MS / max(span_ms, PAIR_DELTA_MS))
            lift = len(lags) / (len(times_a) * chance)
            if lift >= PAIR_MIN_LIFT:
                pair = EventPair(
                    a=EventSide(node=a[0], kind=a[1]),
                    b=EventSide(node=b[0], kind=b[1]),
                    count=len(lags),
                    of=len(times_a),
                    median_lag_ms=int(statistics.median(lags)),
                    lift=round(lift, 1),
                    examples=examples,
                )
                pairs.append(((-len(lags), len(times_a), *a, *b), pair))
            if a[1] in SKEW_KINDS and b[1] in SKEW_KINDS:
                near = [lag for lag in lags if lag < SKEW_MAX_LAG_MS]
                if order.get(a[0], 0) < order.get(b[0], 0):
                    offsets[(a[0], b[0])].extend(near)
                else:
                    offsets[(b[0], a[0])].extend(-lag for lag in near)
    pairs.sort(key=lambda p: p[0])
    skew = [
        ClusterSkew(a=x, b=y, offset_ms=int(statistics.median(v)), pairs=len(v))
        for (x, y), v in sorted(offsets.items())
        if v
    ]
    return _Pairs([p for _, p in pairs[:MAX_PAIRS]], skew)


def _events_body(db: _Db, window: Window, kinds: Iterable[str] | None = None) -> ClusterEventsBody:
    from ragnalog.core.events import EVENT_RULES_VERSION

    found = db.events(window)
    fine, items = found.items, found.streaks()
    if kinds is not None:
        wanted = set(kinds)
        fine = [e for e in fine if e.kind in wanted]
        items = [e for e in items if e.kind in wanted]
    since, until = window.ms
    pairs = _event_pairs(fine, db.capture.node_ids, until - since).pairs
    return ClusterEventsBody(
        rules_version=EVENT_RULES_VERSION,
        count=len(items),
        items=[e.doc() for e in items],
        pairs=pairs,
    )


def events(
    capture: Capture,
    *,
    window: Window,
    kinds: Sequence[str] | None = None,
    front: Front = "api",
) -> ClusterEvents:
    """``events.v1``: the events of every node in the window and the pairs across nodes.

    ``kinds`` keeps only these kinds (pairs are computed among them).

    Raises:
        OpError: ``invalid_value`` for an unknown kind.

    """
    from ragnalog.core.events import EVENT_KINDS

    if kinds is not None:
        unknown = sorted(set(kinds) - set(EVENT_KINDS))
        if unknown:
            raise OpError(
                "invalid_value",
                f"{flag('kind', front)}: unknown kind {', '.join(unknown)}",
                hint=f"kinds: {', '.join(EVENT_KINDS)}",
            )
    with _Db(capture) as db:
        body = _events_body(db, window, kinds)
    return ClusterEvents(**_fields(body))


def _fields(struct: Any) -> dict[str, Any]:
    """Return the fields of a Struct as keyword arguments (to wrap a body as its document)."""
    import msgspec

    return msgspec.structs.asdict(struct)


# --- capture summary


@dataclass(slots=True)
class _NodeStats:
    entries: int = 0
    errors: int = 0
    warnings: int = 0
    coverage: tuple[int, int] | None = None
    minutes: list[int] = field(default_factory=list)
    top_error: tuple[str, int] | None = None


def _node_stats(db: _Db, window: Window) -> dict[str, _NodeStats]:
    since, until = window.ms
    stats = {m.node: _NodeStats() for m in db.capture.nodes}
    for m in db.capture.nodes:
        r = _node_range(m.logset)
        if r is not None:
            a, b = max(_ms(r[0]), since), min(_ms(r[1]), until)
            if a <= b:
                stats[m.node].coverage = (a, b)
    for node, n, errors, warnings in db.rows(
        "SELECT node, count(*), count(*) FILTER (WHERE level >= 50), "
        f"count(*) FILTER (WHERE level = 40) FROM entries WHERE {_WINDOW} GROUP BY node",
        _wp(window),
    ):
        s = stats[node]
        s.entries, s.errors, s.warnings = int(n), int(errors), int(warnings)
    for node, minute in db.rows(
        f"SELECT DISTINCT node, epoch_ms(ts) // 60000 FROM entries WHERE {_WINDOW} ORDER BY 1, 2",
        _wp(window),
    ):
        stats[node].minutes.append(int(minute))
    for node, sig, n in db.rows(
        f"SELECT node, signature, count(*) FROM entries WHERE {_WINDOW} AND level >= 50 "
        "GROUP BY ALL ORDER BY node, count(*) DESC, signature",
        _wp(window),
    ):
        if stats[node].top_error is None:
            stats[node].top_error = (sig, int(n))
    return stats


def _gaps(coverage: tuple[int, int], minutes: Sequence[int]) -> list[tuple[str, str]]:
    """Return the runs of at least :data:`GAP_MINUTES` empty minutes inside ``coverage``."""
    since, until = coverage
    found: list[tuple[int, int]] = []
    edge = since // 60_000 - 1  # the minute before the coverage counts as not empty
    for minute in [*minutes, until // 60_000 + 1]:
        if minute - edge - 1 >= GAP_MINUTES:
            found.append((max((edge + 1) * 60_000, since), min(minute * 60_000, until)))
        edge = minute
    return [(_iso(a), _iso(b)) for a, b in found[:MAX_GAPS]]


def _triage(sets: Sets, capture: Capture, node: ClusterNode) -> tuple[str, ClusterBaseline | None]:
    """Triage one node against its own baseline (never another member of the capture)."""
    from ragnalog.store.triage import rank_baselines
    from ragnalog.store.triage import triage as store_triage

    members = {m.logset.id for m in capture.nodes}
    me = node.logset.manifest
    choice = rank_baselines([m for m in sets.manifests() if m.id not in members], me)
    if choice.manifest is None:
        return "no_baseline", None
    base = sets.open(choice.manifest.id)
    doc = store_triage(node.logset, base, choice=choice, limit=1)
    baseline = ClusterBaseline(id=base.id, name=base.manifest.name, reason=choice.reason)
    return str(doc["verdict"]), baseline


def _summary(db: _Db, window: Window, sets: Sets | None) -> ClusterCaptureBody:
    capture = db.capture
    stats = _node_stats(db, window)
    found = db.events(window)
    items = found.streaks()
    attrs = db.attrs()
    nodes: list[ClusterNodeSummary] = []
    for m in capture.nodes:
        s = stats[m.node]
        hours = _hours(s.coverage[1] - s.coverage[0]) if s.coverage else None
        verdict: str | None = None
        baseline: ClusterBaseline | None = None
        if sets is not None:
            verdict, baseline = _triage(sets, capture, m)
        counts: dict[str, int] = defaultdict(int)
        for e in items:
            if e.node == m.node:
                counts[e.kind] += 1
        top = None
        if s.top_error is not None:
            sig, n = s.top_error
            top = ClusterTopError(signature=sig, template=attrs.get(sig, (None,))[0], count=n)
        manifest = m.logset.manifest
        nodes.append(
            ClusterNodeSummary(
                node=m.node,
                set=ClusterSetRef(id=m.logset.id, name=manifest.name),
                first_ts=_iso(manifest.first_ts) if manifest.first_ts else None,
                last_ts=_iso(manifest.last_ts) if manifest.last_ts else None,
                coverage=(
                    ClusterSpan(since=_iso(s.coverage[0]), until=_iso(s.coverage[1]))
                    if s.coverage
                    else None
                ),
                gaps=_gaps(s.coverage, s.minutes) if s.coverage else [],
                entries=s.entries,
                errors=s.errors,
                warnings=s.warnings,
                errors_per_hour=round(s.errors / hours, 3) if hours else 0.0,
                warnings_per_hour=round(s.warnings / hours, 3) if hours else 0.0,
                top_error=top,
                verdict=cast(Verdict | None, verdict),
                baseline=baseline,
                events=dict(sorted(counts.items())),
            )
        )
    since, until = window.ms
    skew = _event_pairs(found.items, capture.node_ids, until - since).skew
    return ClusterCaptureBody(
        capture=capture.id, cluster=capture.cluster, window=window.doc(), nodes=nodes, skew=skew
    )


def capture_summary(
    capture: Capture, *, window: Window, store: Sets | None = None
) -> ClusterCapture:
    """``capture.v1``: per node coverage, gaps, volume, top error and events; the clock skew.

    With ``store`` (a :class:`~ragnalog.service.sets.Sets`), each node is
    triaged against its own automatic baseline (other members of the capture are
    never one) and gets ``verdict`` and ``baseline``; this reads more sets.
    """
    with _Db(capture) as db:
        body = _summary(db, window, store)
    return ClusterCapture(**_fields(body))


# --- timeline


def _bucket(bucket: str, front: Front) -> int:
    from ragnalog.store.logset import _bucket_ms

    try:
        return _bucket_ms(bucket)
    except ValueError as exc:
        raise OpError("invalid_value", f"{flag('bucket', front)}: {exc}") from exc


def _resolve_signature(db: _Db, signature: str, front: Front) -> str:
    signature = hex_ref(signature)
    sigs = db.attrs()
    if signature in sigs:
        return signature
    found = sorted(s for s in sigs if _HEX.match(signature) and s.startswith(signature))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise OpError.because(
            "ambiguous_signature",
            front,
            f"signature prefix {signature!r} is ambiguous in the capture: {', '.join(found[:5])}",
            hint=hint("ambiguous_signature", front),
            extra={"candidates": found[:5]},
        )
    raise OpError.because(
        "group_not_found", front, f"no group {signature!r} on any node of the capture"
    )


def resolve_signature(capture: Capture, signature: str, *, front: Front = "api") -> str:
    """Return the full signature of ``signature`` (or of a unique prefix) in any member set.

    Raises:
        OpError: Reasons ``ambiguous_signature`` (``candidates``) and ``group_not_found``.

    """
    with _Db(capture) as db:
        return _resolve_signature(db, signature, front)


def resolve_signatures(
    capture: Capture, signatures: Sequence[str], *, front: Front = "api"
) -> list[str]:
    """Return the full signatures of ``signatures`` (or unique prefixes), duplicates dropped.

    Raises:
        OpError: Reasons ``ambiguous_signature`` (``candidates``) and ``group_not_found``.

    """
    if not signatures:
        return []
    with _Db(capture) as db:
        return list(dict.fromkeys(_resolve_signature(db, s, front) for s in signatures))


def _level_case() -> str:
    return (
        "CASE WHEN level >= 50 THEN 'ERROR' WHEN level >= 40 THEN 'WARN' "
        "WHEN level >= 30 THEN 'INFO' WHEN level >= 10 THEN 'DEBUG' ELSE 'OTHER' END"
    )


def _filtered_rows(
    db: _Db, window: Window, size: int, signature: str | None, where: EntryFilter
) -> list[tuple[Any, ...]]:
    """``(node, bucket, name, count)`` of the entries ``where`` keeps (the polars filters)."""
    import polars as pl

    from ragnalog.store.levels import InvalidLevel

    if signature is not None:
        where = dataclasses.replace(where, signature=signature)
    try:
        predicate = where.predicate()
    except InvalidLevel as exc:
        raise OpError("invalid_level", str(exc)) from exc
    lvl = pl.col("level")
    name = (
        pl.when(lvl >= 50)
        .then(pl.lit("ERROR"))
        .when(lvl >= 40)
        .then(pl.lit("WARN"))
        .when(lvl >= 30)
        .then(pl.lit("INFO"))
        .when(lvl >= 10)
        .then(pl.lit("DEBUG"))
        .otherwise(pl.lit("OTHER"))
    )
    cond = pl.col("ts").is_between(window.since, window.until, closed="both")
    if predicate is not None:
        cond = cond & predicate
    rows: list[tuple[Any, ...]] = []
    for m in db.capture.nodes:
        df = (
            m.logset.entries_lazy()
            .filter(cond)
            .select(
                (pl.col("ts").dt.epoch("ms") // size).alias("b"),
                pl.lit("count").alias("n") if signature is not None else name.alias("n"),
            )
            .group_by("b", "n")
            .agg(pl.len().alias("c"))
            .collect()
        )
        rows.extend((m.node, b, n, c) for b, n, c in df.iter_rows())
    return rows


def _timeline(
    db: _Db,
    window: Window,
    bucket: str,
    size: int,
    signature: str | None,
    front: Front,
    where: EntryFilter | None = None,
) -> ClusterTimelineBody:
    from ragnalog.report.payload import LEVEL_BUCKETS
    from ragnalog.store.logset import MAX_HISTOGRAM_BUCKETS

    since, until = window.ms
    first, last = since // size, until // size
    n = last - first + 1
    if n > MAX_HISTOGRAM_BUCKETS:
        raise OpError(
            "invalid_value",
            f"{flag('bucket', front)} {bucket!r} gives {n} buckets; use a larger one",
        )
    names = ("count",) if signature is not None else LEVEL_BUCKETS
    levels = {node: {name: [0] * n for name in names} for node in db.capture.node_ids}
    if where is not None:
        rows = _filtered_rows(db, window, size, signature, where)
    elif signature is not None:
        rows = db.rows(
            f"SELECT node, epoch_ms(ts) // {size}, 'count', count(*) FROM entries "
            f"WHERE {_WINDOW} AND signature = ? GROUP BY ALL",
            [*_wp(window), signature],
        )
    else:
        rows = db.rows(
            f"SELECT node, epoch_ms(ts) // {size}, {_level_case()}, count(*) FROM entries "
            f"WHERE {_WINDOW} GROUP BY ALL",
            _wp(window),
        )
    for node, b, name, count in rows:
        levels[node][name][int(b) - first] += int(count)
    nodes: dict[str, ClusterNodeTimeline] = {}
    for m in db.capture.nodes:
        r = _node_range(m.logset)
        cover: tuple[int, int] | None = None
        if r is not None:
            a, b = max(_ms(r[0]), since), min(_ms(r[1]), until)
            if a <= b:
                cover = (a // size - first, b // size - first)
        nodes[m.node] = ClusterNodeTimeline(levels=levels[m.node], coverage=cover)
    body = ClusterTimelineBody(
        bucket=bucket, buckets=[_bucket_iso((first + i) * size) for i in range(n)], nodes=nodes
    )
    if signature is not None:
        body.signature = signature
    return body


def cluster_timeline(
    capture: Capture,
    *,
    window: Window,
    bucket: str = "1h",
    signature: str | None = None,
    where: EntryFilter | None = None,
    front: Front = "api",
) -> ClusterTimeline:
    """``cluster-timeline.v1``: entries per bucket and node, by level or of one group.

    ``bucket`` is ``<n><unit>`` as for ``ragnalog timeline`` (``15m``, ``1h``, ``1d``),
    aligned to the epoch in UTC; buckets run contiguously over the window.
    ``signature`` (or a unique prefix) counts one group instead of the levels. ``where``
    keeps only the entries matching its filters (as ``entries``; its ``signature``,
    ``since`` and ``until`` are ignored: the window and ``signature`` give those).

    Raises:
        OpError: ``invalid_value`` for a bad bucket or too many buckets; reasons
            ``ambiguous_signature``, ``group_not_found``.

    """
    size = _bucket(bucket, front)
    with _Db(capture) as db:
        sig = _resolve_signature(db, signature, front) if signature is not None else None
        if where is not None:
            from ragnalog.store.filters import EntryFilter as Filter

            where = dataclasses.replace(where, signature=None, since=None, until=None)
            if where == Filter():
                where = None
        body = _timeline(db, window, bucket, size, sig, front, where)
    return ClusterTimeline(**_fields(body))


# --- matrix


@dataclass(slots=True)
class _Row:
    signature: str
    attrs: tuple[str | None, int, str, str | None, str | None]
    per_node: dict[str, tuple[int, int, int]]
    """node -> (count, first ms, last ms) for the nodes that have the group."""


def _matrix_rows(db: _Db, window: Window) -> list[_Row]:
    attrs = db.attrs()
    by_sig: dict[str, dict[str, tuple[int, int, int]]] = defaultdict(dict)
    for node, sig, n, first, last in db.rows(
        "SELECT node, signature, count(*), epoch_ms(min(ts)), epoch_ms(max(ts)) FROM entries "
        f"WHERE {_WINDOW} GROUP BY ALL",
        _wp(window),
    ):
        by_sig[sig][node] = (int(n), int(first), int(last))
    unknown = (None, 0, "UNKNOWN", None, None)
    return [_Row(sig, attrs.get(sig, unknown), nodes) for sig, nodes in by_sig.items()]


def _filter_rows(rows: list[_Row], where: GroupFilter) -> list[_Row]:
    """Rows whose group passes ``where`` (first/last seen over the nodes, in the window)."""
    import polars as pl

    predicate = where.predicate()
    if predicate is None or not rows:
        return rows
    frame = pl.DataFrame(
        {
            "i": list(range(len(rows))),
            "template": [r.attrs[0] for r in rows],
            "level": [r.attrs[1] for r in rows],
            "level_name": [r.attrs[2] for r in rows],
            "logger": [r.attrs[3] for r in rows],
            "exception_type": [r.attrs[4] for r in rows],
            "first_seen": [min(v[1] for v in r.per_node.values()) for r in rows],
            "last_seen": [max(v[2] for v in r.per_node.values()) for r in rows],
        },
        schema_overrides={"template": pl.String, "logger": pl.String, "exception_type": pl.String},
    ).with_columns(
        pl.from_epoch("first_seen", time_unit="ms").dt.replace_time_zone("UTC"),
        pl.from_epoch("last_seen", time_unit="ms").dt.replace_time_zone("UTC"),
    )
    keep = frame.filter(predicate)["i"].to_list()
    return [rows[i] for i in keep]


def _matrix_item(
    row: _Row,
    node_ids: Sequence[str],
    totals: dict[str, int],
    hours: dict[str, float | None],
    skew_min: float,
) -> MatrixRow:
    template, level, level_name, logger, exception = row.attrs
    per_node: dict[str, MatrixNodeCount] = {}
    for node in node_ids:
        found = row.per_node.get(node)
        h = hours.get(node)
        if found is None:
            per_node[node] = MatrixNodeCount(
                count=0, rate_per_hour=0.0, first_seen=None, last_seen=None
            )
            continue
        n, first, last = found
        per_node[node] = MatrixNodeCount(
            count=n,
            rate_per_hour=round(n / h, 3) if h else 0.0,
            first_seen=_iso(first),
            last_seen=_iso(last),
        )
    present = [n for n in node_ids if n in row.per_node]
    covered = [n for n in node_ids if totals.get(n)]
    firsts = sorted((row.per_node[n][1], n) for n in present)
    first_node = firsts[0][1] if firsts else None
    spread: Spread
    if len(present) <= 1:
        spread, skew, lead = "only", None, None
    else:
        rates = [row.per_node[n][0] / totals[n] for n in present if totals.get(n)]
        skew = round(max(rates) / min(rates), 3) if rates and min(rates) > 0 else None
        missing = any(n not in row.per_node for n in covered)
        spread = "skewed" if missing or (skew is not None and skew >= skew_min) else "all"
        lead = firsts[1][0] - firsts[0][0]
    return MatrixRow(
        signature=row.signature,
        template=template,
        level=level,
        level_name=level_name,
        logger=logger,
        exception_type=exception,
        total=sum(v[0] for v in row.per_node.values()),
        per_node=per_node,
        spread=spread,
        skew=skew,
        first_node=first_node,
        lead_ms=lead,
    )


def _matrix_items(
    db: _Db,
    window: Window,
    skew_min: float,
    where: GroupFilter | None = None,
    node: str | None = None,
) -> list[MatrixRow]:
    """Return the matrix rows of the window (groups passing ``where``, on ``node``) by total."""
    stats = _node_stats_light(db, window)
    rows = _matrix_rows(db, window)
    if where is not None:
        rows = _filter_rows(rows, where)
    if node is not None:
        rows = [r for r in rows if node in r.per_node]
    node_ids = db.capture.node_ids
    totals = {n: s[0] for n, s in stats.items()}
    hours = {n: s[1] for n, s in stats.items()}
    items = [_matrix_item(r, node_ids, totals, hours, skew_min) for r in rows]
    items.sort(key=lambda r: (-r.total, r.signature))
    return items


def _node_stats_light(db: _Db, window: Window) -> dict[str, tuple[int, float | None]]:
    """Entries in the window and hours of coverage per node."""
    since, until = window.ms
    out: dict[str, tuple[int, float | None]] = {}
    totals = dict(
        db.rows(f"SELECT node, count(*) FROM entries WHERE {_WINDOW} GROUP BY node", _wp(window))
    )
    for m in db.capture.nodes:
        r = _node_range(m.logset)
        h: float | None = None
        if r is not None:
            a, b = max(_ms(r[0]), since), min(_ms(r[1]), until)
            h = _hours(b - a) if a <= b else None
        out[m.node] = (int(totals.get(m.node, 0)), h)
    return out


def matrix(
    capture: Capture,
    *,
    window: Window,
    where: GroupFilter | None = None,
    spread: str | None = None,
    node: str | None = None,
    sort: str = "count",
    limit: int | None = 50,
    offset: int = 0,
    skew_min: float = SKEW_MIN,
    signature: str | None = None,
    front: Front = "api",
) -> ClusterMatrix:
    """``matrix.v1``: one row per group with its count, rate and first/last entry per node.

    Args:
        capture: The capture.
        window: Counts are restricted to it.
        where: The filters of ``groups`` (level, min_level, logger, contains, exception,
            with_exception, since/until on the group's first/last entry in the window).
        spread: Only rows with this spread (``all``, ``only``, ``skewed``).
        node: Only groups present on this node.
        sort: ``count`` (total), ``skew`` or ``lead`` (``lead_ms``), descending.
        limit: Rows per page (``None`` or 0 = all).
        offset: Rows to skip.
        skew_min: Skew from which a group present on all nodes is ``skewed``.
        signature: Only this group (a signature or a unique prefix of one).
        front: The front end the errors are for.

    Raises:
        OpError: ``invalid_value`` for an unknown spread, node or sort, a negative
            limit/offset or ``skew_min`` under 1; ``invalid_level`` from ``where``;
            reasons ``ambiguous_signature`` and ``group_not_found`` for ``signature``.

    """
    if spread is not None and spread not in SPREADS:
        raise OpError(
            "invalid_value", f"{flag('spread', front)} {spread!r}: expected all, only or skewed"
        )
    if node is not None and node not in capture.node_ids:
        raise OpError(
            "invalid_value",
            f"{flag('node', front)} {node!r} is not a node of the capture",
            hint=f"nodes: {', '.join(capture.node_ids)}",
        )
    if sort not in MATRIX_SORTS:
        raise OpError(
            "invalid_value", f"{flag('sort', front)} {sort!r}: expected count, skew or lead"
        )
    if (limit is not None and limit < 0) or offset < 0:
        raise OpError(
            "invalid_value", f"{flag('limit', front)} and {flag('offset', front)} must be >= 0"
        )
    if not skew_min >= 1:
        raise OpError("invalid_value", f"{flag('skew_min', front)} must be >= 1")
    from ragnalog.store.levels import InvalidLevel

    try:
        with _Db(capture) as db:
            sig = _resolve_signature(db, signature, front) if signature is not None else None
            items = _matrix_items(db, window, skew_min, where, node)
            if sig is not None:
                items = [r for r in items if r.signature == sig]
    except InvalidLevel as exc:
        raise OpError("invalid_level", str(exc)) from exc
    if spread is not None:
        items = [r for r in items if r.spread == spread]
    match sort:
        case "skew":
            items.sort(key=lambda r: (r.skew is None, -(r.skew or 0.0), -r.total, r.signature))
        case "lead":
            items.sort(key=lambda r: (r.lead_ms is None, -(r.lead_ms or 0), -r.total, r.signature))
    total = len(items)
    page = items[offset : offset + limit if limit else None]
    return ClusterMatrix(
        window=window.doc(),
        nodes=capture.node_ids,
        count=len(page),
        total=total,
        offset=offset,
        truncated=offset + len(page) < total,
        items=page,
    )


# --- interactions


def _mentions(db: _Db, window: Window) -> list[MentionEdge]:
    out: list[tuple[int, MentionEdge]] = []
    for target in db.capture.node_ids:
        pattern = r"(^|[^A-Za-z0-9_])" + re.escape(target) + r"([^A-Za-z0-9_]|$)"
        by_node: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for node, sig, n in db.rows(
            f"SELECT node, signature, count(*) FROM entries WHERE {_WINDOW} AND node <> ? "
            "AND contains(message, ?) AND regexp_matches(message, ?) GROUP BY ALL",
            [*_wp(window), target, target, pattern],
        ):
            by_node[node].append((int(n), sig))
        for node, groups in by_node.items():
            groups.sort(key=lambda g: (-g[0], g[1]))
            count = sum(n for n, _ in groups)
            edge = MentionEdge(
                from_=node,
                to=target,
                count=count,
                groups=[s for _, s in groups[:MAX_MENTION_GROUPS]],
            )
            out.append((count, edge))
    out.sort(key=lambda e: (-e[0], e[1].from_, e[1].to))
    return [e for _, e in out]


def _burst_starts(series: Sequence[tuple[int, int]], start: int) -> list[int]:
    """Burst onsets of one minute series (``(minute, count)``, sorted, non-empty minutes)."""
    threshold = max(1, statistics.median_low(n for _, n in series))
    out: list[int] = []
    active = start - 1  # the window's first minutes need 10 quiet minutes too
    for minute, n in series:
        if n < threshold:
            continue
        if minute - active - 1 >= BURST_QUIET_MINUTES:
            out.append(minute)
        active = minute
    return out


def _bursts(db: _Db, window: Window) -> dict[str, dict[str, list[int]]]:
    """Burst onsets (minutes) per node and group, of the groups co-bursts consider."""
    first_minute = window.ms[0] // 60_000
    kinds = db.events(window).kinds
    scheduled = {sig for sig, kind in kinds.items() if kind in SCHEDULED_KINDS}
    event_sigs = sorted(sig for sig in kinds if sig not in scheduled)
    behind = "OR signature IN (SELECT unnest(?))" if event_sigs else ""
    series: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for node, sig, minute, n in db.rows(
        "SELECT node, signature, epoch_ms(ts) // 60000 AS m, count(*) FROM entries "
        f"WHERE {_WINDOW} AND (level >= 40 {behind}) GROUP BY ALL ORDER BY node, signature, m",
        [*_wp(window), event_sigs] if event_sigs else _wp(window),
    ):
        series[(node, sig)].append((int(minute), int(n)))
    bursts: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for (node, sig), values in series.items():
        if sig in scheduled:
            continue
        starts = _burst_starts(values, first_minute)
        if starts:
            bursts[node][sig] = starts
    for node, groups in bursts.items():
        if len(groups) > BURST_MAX_GROUPS:
            top = sorted(groups.items(), key=lambda g: (-len(g[1]), g[0]))[:BURST_MAX_GROUPS]
            bursts[node] = dict(top)
    return bursts


def _follows(
    groups_a: dict[str, list[int]], groups_b: dict[str, list[int]], before: int, after: int
) -> dict[tuple[str, str], list[int]]:
    """Return the lags from bursts of groups g to the next bursts of groups h, by ``(g, h)``.

    For each burst of g in ``groups_a``, the first burst of each h in ``groups_b`` from
    ``before`` to ``after`` minutes later counts once.
    """
    timeline = sorted((m, sig) for sig, starts in groups_b.items() for m in starts)
    times = [m for m, _ in timeline]
    hits: dict[tuple[str, str], list[int]] = defaultdict(list)
    for g, starts in groups_a.items():
        for m in starts:
            seen: set[str] = set()
            i = bisect.bisect_left(times, m + before)
            while i < len(times) and times[i] - m <= after:
                h = timeline[i][1]
                if h not in seen:
                    seen.add(h)
                    hits[(g, h)].append(times[i] - m)
                i += 1
    return hits


def _families(groups: dict[str, list[int]], delta: int) -> dict[str, list[str]]:
    """Return the groups of one node that burst together, by the member with most bursts.

    Two groups belong together when at least :data:`FAMILY_SHARE` of the bursts of each
    have a burst of the other within ``delta`` minutes either way.
    """
    parent = {g: g for g in groups}

    def root(g: str) -> str:
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    together = _follows(groups, groups, -delta, delta)
    for (g, h), lags in together.items():
        if g < h and len(lags) >= FAMILY_SHARE * len(groups[g]):
            back = together.get((h, g), [])
            if len(back) >= FAMILY_SHARE * len(groups[h]):
                parent[root(g)] = root(h)
    members: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        members[root(g)].append(g)
    out: dict[str, list[str]] = {}
    for group in members.values():
        best = min(group, key=lambda g: (-len(groups[g]), g))
        out[best] = group
    return out


def _co_bursts(db: _Db, window: Window, delta_s: int) -> list[CoBurst]:
    since, until = window.ms
    minutes = max(1, (until - since) // 60_000 + 1)
    delta = max(0, delta_s // 60)
    p_window = (delta + 1) / minutes
    bursts = _bursts(db, window)
    families = {node: _families(groups, delta) for node, groups in bursts.items()}
    heads = {node: {g: bursts[node][g] for g in fam} for node, fam in families.items()}
    attrs = db.attrs()

    def side(node: str, sig: str) -> BurstSide:
        size = len(families[node][sig])
        return BurstSide(node=node, signature=sig, template=attrs.get(sig, (None,))[0], groups=size)

    found: list[tuple[tuple[int, float, str, str, str, str], CoBurst]] = []
    for node_a, groups_a in heads.items():
        for node_b, groups_b in heads.items():
            if node_a == node_b:
                continue
            for (g, h), lags in _follows(groups_a, groups_b, 0, delta).items():
                of = len(groups_a[g])
                if len(lags) < max(BURST_MIN_HITS, BURST_MIN_SHARE * of):
                    continue
                expected = of * min(1.0, len(groups_b[h]) * p_window)
                lift = len(lags) / expected if expected else 0.0
                if lift < BURST_MIN_LIFT:
                    continue
                co = CoBurst(
                    a=side(node_a, g),
                    b=side(node_b, h),
                    hits=len(lags),
                    of=of,
                    median_lag_ms=int(statistics.median(lags)) * 60_000,
                    lift=round(lift, 1),
                )
                found.append(((-len(lags), -lift, node_a, g, node_b, h), co))
    found.sort(key=lambda f: f[0])
    return _strongest(c for _, c in found)


def _strongest(ordered: Iterable[CoBurst]) -> list[CoBurst]:
    """Keep each group's strongest pair: a group appears at most once as a and once as b.

    Bursts in the same minute pair both ways; only the first of the two is kept.
    """
    used_a: set[tuple[str, str]] = set()
    used_b: set[tuple[str, str]] = set()
    kept: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    out: list[CoBurst] = []
    for co in ordered:
        a, b = (co.a.node, co.a.signature), (co.b.node, co.b.signature)
        if a in used_a or b in used_b or (co.median_lag_ms == 0 and (b, a) in kept):
            continue
        used_a.add(a)
        used_b.add(b)
        kept.add((a, b))
        out.append(co)
        if len(out) == MAX_CO_BURSTS:
            break
    return out


def _jobs(items: Sequence[_Event]) -> tuple[list[ConcurrentJob], list[JobRuns]]:
    runs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for e in items:
        if e.kind == "job_run" and e.subject is not None:
            runs[e.subject].append((e.ts, e.node))
    concurrent: list[ConcurrentJob] = []
    owners: list[tuple[tuple[int, int, str], JobRuns]] = []
    for job, values in runs.items():
        values.sort()
        per_node: dict[str, int] = defaultdict(int)
        for _, node in values:
            per_node[node] += 1
        owners.append(((-len(per_node), -len(values), job), JobRuns(job=job, runs=dict(per_node))))
        slots = 0
        nodes: set[str] = set()
        examples: list[str] = []
        i = 0
        while i < len(values):
            start = values[i][0]
            j = i
            here: set[str] = set()
            while j < len(values) and values[j][0] - start <= JOB_SLOT_MS:
                here.add(values[j][1])
                j += 1
            if len(here) > 1:
                slots += 1
                nodes |= here
                if len(examples) < MAX_EXAMPLES:
                    examples.append(_iso(start))
            i = j
        if slots:
            concurrent.append(
                ConcurrentJob(job=job, nodes=sorted(nodes), slots=slots, examples=examples)
            )
    concurrent.sort(key=lambda c: (-c.slots, c.job))
    owners.sort(key=lambda o: o[0])
    return concurrent, [o for _, o in owners[:MAX_JOBS]]


def _sessions(db: _Db, window: Window) -> list[SessionPair]:
    hours: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for session, node, hour in db.rows(
        "SELECT session, node, epoch_ms(ts) // 3600000 FROM entries "
        f"WHERE {_WINDOW} AND session IS NOT NULL AND session <> '' GROUP BY ALL",
        _wp(window),
    ):
        hours[session][node].add(int(hour))
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for per_node in hours.values():
        if len(per_node) < 2:
            continue
        names = sorted(per_node)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if any(
                    h + d in per_node[b]
                    for h in per_node[a]
                    for d in range(-SESSION_HOURS, SESSION_HOURS + 1)
                ):
                    pairs[(a, b)] += 1
    ordered = sorted(pairs.items(), key=lambda p: (-p[1], p[0]))
    return [SessionPair(nodes=k, count=n) for k, n in ordered]


def _interactions(db: _Db, window: Window, delta_s: int) -> ClusterInteractionsBody:
    concurrent, jobs = _jobs(db.events(window).items)
    return ClusterInteractionsBody(
        delta_s=delta_s,
        mentions=_mentions(db, window),
        co_bursts=_co_bursts(db, window, delta_s),
        concurrent_jobs=concurrent,
        sessions=_sessions(db, window),
        jobs=jobs,
    )


def interactions(
    capture: Capture, *, window: Window, delta_s: int = 300, front: Front = "api"
) -> ClusterInteractions:
    """``interactions.v1``: mentions, co-bursts, concurrent jobs, sessions, job ownership.

    ``delta_s`` is the co-burst window (rounded down to whole minutes).

    Raises:
        OpError: ``invalid_value`` when ``delta_s`` is negative.

    """
    if delta_s < 0:
        raise OpError("invalid_value", f"{flag('delta', front)} must be >= 0 (seconds)")
    with _Db(capture) as db:
        body = _interactions(db, window, delta_s)
    return ClusterInteractions(**_fields(body))


# --- merged entries and context


def _entry_columns(columns: Sequence[str] | None, front: Front) -> list[str] | None:
    from ragnalog.service.views import ENTRY_FIELDS

    return parse_columns(columns, ENTRY_FIELDS, front=front)


def _rows_by_index(
    capture: Capture, wanted: dict[int, list[int]], columns: list[str] | None
) -> dict[tuple[int, int], dict[str, Any]]:
    """Rows ``(node index, row index)`` of the member sets, serialised (``to_records``)."""
    import polars as pl

    from ragnalog.store.logset import to_records

    out: dict[tuple[int, int], dict[str, Any]] = {}
    for i, rows in wanted.items():
        m = capture.nodes[i]
        lf = (
            m.logset.entries_lazy()
            .with_row_index("_row")
            .filter(pl.col("_row").is_in(pl.Series(rows, dtype=pl.UInt32).implode()))
        )
        if columns is not None:
            lf = lf.select("_row", *columns)
        for rec in to_records(lf.collect()):
            row = int(rec.pop("_row"))
            out[(i, row)] = {**rec, "node": m.node, "set": m.logset.id}
    return out


def _keys(capture: Capture, cond: Callable[[str], pl.Expr]) -> pl.DataFrame:
    """``ts, _n, _row`` of the entries of every node matching ``cond(node)``, by ts, node, file."""
    import polars as pl

    frames = [
        m.logset.entries_lazy()
        .with_row_index("_row")
        .filter(cond(m.node))
        .select("ts", pl.lit(i, dtype=pl.UInt16).alias("_n"), "_row")
        for i, m in enumerate(capture.nodes)
    ]
    return pl.concat(frames).sort(["ts", "_n", "_row"]).collect()


def merged_entries(
    capture: Capture,
    *,
    window: Window,
    where: EntryFilter | None = None,
    limit: int | None = 100,
    offset: int = 0,
    max_raw: int | None = DEFAULT_MAX_RAW,
    columns: Sequence[str] | None = None,
    signatures: Sequence[str] | None = None,
    node_signatures: Mapping[str, Sequence[str]] | None = None,
    front: Front = "api",
) -> Entries:
    """``entries.v1`` of all nodes interleaved by time (then node); rows add ``node``, ``set``.

    ``logset`` is the capture reference (``capture:<id>``). ``where`` filters as
    ``entries`` (exact signatures); ``signatures`` keeps the entries of any of these
    (exact) signatures besides, ``node_signatures`` also those of a node's own (a group
    on one node only); ``columns`` selects entries.v1 columns; ``raw`` and
    ``body`` are cut to ``max_raw`` characters (0 = all).

    Raises:
        OpError: ``invalid_value`` for an unknown column or a negative limit, offset
            or max_raw; ``invalid_level`` from ``where``.

    """
    import polars as pl

    from ragnalog.store.levels import InvalidLevel

    if (limit is not None and limit < 0) or offset < 0:
        raise OpError(
            "invalid_value", f"{flag('limit', front)} and {flag('offset', front)} must be >= 0"
        )
    cap = TextCap.parse(max_raw, default=DEFAULT_MAX_RAW, front=front)
    cols = _entry_columns(columns, front)
    cond = pl.col("ts").is_between(window.since, window.until, closed="both")
    try:
        predicate = where.predicate() if where is not None else None
    except InvalidLevel as exc:
        raise OpError("invalid_level", str(exc)) from exc
    if predicate is not None:
        cond = cond & predicate

    def node_cond(node: str) -> pl.Expr:
        if signatures is None and node_signatures is None:
            return cond
        sigs = [*(signatures or ()), *(node_signatures or {}).get(node, ())]
        return cond & pl.col("signature").is_in(sigs)

    keys = _keys(capture, node_cond)
    total = keys.height
    page = keys.slice(offset, limit or None)
    wanted: dict[int, list[int]] = defaultdict(list)
    order = [(int(n), int(r)) for n, r in zip(page["_n"], page["_row"], strict=True)]
    for n, r in order:
        wanted[n].append(r)
    found = _rows_by_index(capture, wanted, cols)
    items = cap.rows([found[k] for k in order if k in found])
    nxt = offset + len(items)
    return Entries(
        logset=capture.ref,
        count=len(items),
        items=items,
        total=total,
        offset=offset,
        truncated=nxt < total,
        next_offset=nxt if nxt < total else None,
    )


def context_all_nodes(
    capture: Capture,
    set_id: str,
    entry_id: str,
    *,
    window_s: float = 30,
    max_raw: int | None = DEFAULT_MAX_RAW,
    columns: Sequence[str] | None = None,
    front: Front = "api",
) -> Entries:
    """``entries.v1`` with ``anchor``: what every node logged within ``window_s`` of one entry.

    ``set_id`` is the anchor's set (id or name of a member). Rows are in ``ts``, node,
    file order and add ``node`` and ``set``; at most :data:`CONTEXT_MAX_ROWS`, the ones
    nearest the anchor (``truncated`` then says so). ``columns`` selects entries.v1
    columns, as in :func:`merged_entries`.

    Raises:
        OpError: reason ``set_not_found`` when the set is not a member,
            ``entry_not_found`` when it has no such entry; ``invalid_value`` for a
            negative window or an unknown column.

    """
    import polars as pl

    if window_s < 0:
        raise OpError("invalid_value", f"{flag('window', front)} must be >= 0")
    member = next(
        (m for m in capture.nodes if set_id in (m.logset.id, m.logset.manifest.name)), None
    )
    if member is None:
        raise OpError.because(
            "set_not_found",
            front,
            f"set {set_id!r} is not a member of the capture",
            hint=f"members: {', '.join(m.logset.id for m in capture.nodes)}",
        )
    anchor = member.logset.entry(entry_id)
    if anchor is None:
        ref = member.logset.manifest.name or member.logset.id
        raise OpError.because(
            "entry_not_found",
            front,
            f"no entry with id {entry_id!r} in log set {member.logset.id}",
            hint=hint("entry_not_found", front, set=ref, id=member.logset.id),
        )
    cap = TextCap.parse(max_raw, default=DEFAULT_MAX_RAW, front=front)
    cols = _entry_columns(columns, front)
    at = anchor["ts"]
    span = timedelta(seconds=window_s)
    near = pl.col("ts").is_between(at - span, at + span, closed="both")
    keys = _keys(capture, lambda _node: near)
    total = keys.height
    if total > CONTEXT_MAX_ROWS:
        distance = (pl.col("ts").dt.epoch("ms") - _ms(at)).abs()
        keys = keys.with_row_index("_i").sort([distance, "_i"]).head(CONTEXT_MAX_ROWS)
        keys = keys.sort("_i").drop("_i")
    wanted: dict[int, list[int]] = defaultdict(list)
    order = [(int(n), int(r)) for n, r in zip(keys["_n"], keys["_row"], strict=True)]
    for n, r in order:
        wanted[n].append(r)
    found = _rows_by_index(capture, wanted, cols)
    items = cap.rows([found[k] for k in order if k in found])
    return Entries(
        logset=member.logset.id,
        count=len(items),
        items=items,
        anchor=entry_id,
        thread=anchor.get("thread"),
        any_thread=True,
        total=total,
        truncated=len(items) < total,
    )


# --- the cluster page


def _hourly(
    db: _Db, window: Window, size: int, signatures: list[str]
) -> dict[str, dict[str, list[tuple[int, int]]]]:
    first = window.ms[0] // size
    out: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for sig, node, b, n in db.rows(
        f"SELECT signature, node, epoch_ms(ts) // {size} AS b, count(*) FROM entries "
        f"WHERE {_WINDOW} AND signature IN (SELECT unnest(?)) GROUP BY ALL ORDER BY 1, 2, 3",
        [*_wp(window), signatures],
    ):
        out[sig][node].append((int(b) - first, int(n)))
    return out


def _example_ids(
    db: _Db, window: Window, signatures: list[str], examples: int
) -> dict[str, dict[str, list[str]]]:
    """Per group and node: the first ``examples - 1`` entries in the window and the last."""
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    if examples <= 0 or not signatures:
        return out
    head = max(1, examples - 1)
    key = "struct_pack(t := ts, i := id)"
    for sig, node, firsts, last in db.rows(
        f"SELECT signature, node, min_by(id, {key}, {head}), max_by(id, {key}) FROM entries "
        f"WHERE {_WINDOW} AND signature IN (SELECT unnest(?)) GROUP BY ALL",
        [*_wp(window), signatures],
    ):
        ids = list(firsts)
        if examples >= 2 and last not in ids:
            ids.append(last)
        out[sig][node] = ids[:examples]
    return out


def cluster_payload(
    capture: Capture,
    *,
    window: Window,
    examples: int = 3,
    max_raw: int = DEFAULT_MAX_RAW,
    max_groups: int = 500,
    store: Sets | None = None,
    front: Front = "api",
) -> ClusterReport:
    """``cluster-report.v1``: the payload of the cluster page (docs/cluster.md, 11.3).

    Args:
        capture: The capture.
        window: The window of every part.
        examples: Example entries per group and node (first ones and the last); 0 = none.
        max_raw: Characters of ``raw`` kept per example; 0 = all.
        max_groups: Largest groups kept, 0 = all (``options.truncated_groups`` counts the
            rest).
        store: When given, each node is triaged against its own baseline (``capture``).
        front: The front end the errors are for.

    Raises:
        OpError: ``invalid_value`` for a negative option.

    """
    import msgspec

    from ragnalog import __version__
    from ragnalog.report import example_entries

    for name, value in (("examples", examples), ("max_raw", max_raw), ("max_groups", max_groups)):
        if value < 0:
            raise OpError("invalid_value", f"{flag(name, front)} must be >= 0")
    since, until = window.ms
    bucket = "15m" if until - since < SHORT_WINDOW_MS else "1h"
    size = _bucket(bucket, front)
    with _Db(capture) as db:
        summary = _summary(db, window, store)
        timeline = _timeline(db, window, bucket, size, None, front)
        rows = _matrix_items(db, window, SKEW_MIN)
        keep = max_groups or len(rows)
        truncated = max(0, len(rows) - keep)
        rows = rows[:keep]
        signatures = [r.signature for r in rows]
        hourly = _hourly(db, window, size, signatures)
        ids = _example_ids(db, window, signatures, examples)
        events_body = _events_body(db, window)
        interactions_body = _interactions(db, window, 300)
    entries: dict[str, dict[str, Any]] = {}
    for m in capture.nodes:
        wanted = [i for per_node in ids.values() for i in per_node.get(m.node, [])]
        for entry_id, entry in example_entries(m.logset, wanted, max_raw=max_raw).items():
            # an identical line in the same millisecond has one id on every node: keep the first
            entries.setdefault(entry_id, {**entry, "node": m.node, "set": m.logset.id})
    groups = [
        ClusterReportGroup(
            **msgspec.structs.asdict(r),
            hourly={n: v for n, v in hourly.get(r.signature, {}).items()},
            examples={
                n: [i for i in v if entries.get(i, {}).get("node") == n]
                for n, v in ids.get(r.signature, {}).items()
            },
        )
        for r in rows
    ]
    return ClusterReport(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tool_version=__version__,
        capture=summary,
        nodes=capture.node_ids,
        timeline=timeline,
        groups=groups,
        entries=entries,
        events=events_body,
        interactions=interactions_body,
        options=ClusterReportOptions(
            window=window.mode,
            examples=examples,
            max_raw=max_raw,
            max_groups=max_groups,
            truncated_groups=truncated,
        ),
        urls=None,
    )
