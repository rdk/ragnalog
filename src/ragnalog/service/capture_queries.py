"""Capture views for both front ends: a reference → its capture, the window, each document.

The builders of :mod:`ragnalog.service.cluster` take an open
:class:`~ragnalog.service.cluster.Capture`; the functions here take the
user's reference and options, as ``queries`` does for one set, and return the JSON
documents (docs/cluster.md, section 6):

- ``capture.v1`` (:func:`capture`), ``cluster-timeline.v1`` (:func:`timeline`),
  ``matrix.v1`` (:func:`matrix`), ``events.v1`` (:func:`events`),
  ``interactions.v1`` (:func:`interactions`);
- ``entries.v1`` of all nodes (:func:`entries`) and around one entry on all nodes
  (:func:`context`);
- ``query.v1`` of SQL over all nodes (:func:`query`);
- ``cluster-report.v1`` (:func:`report_payload`) and the static cluster page
  (:func:`report_file`, ``report-file.v1``).

A reference (:func:`capture_from_ref`) is ``capture:PREFIX``, ``cluster:NAME`` or
``cluster:NAME@YYYY-MM-DD`` (:func:`~ragnalog.service.captures.resolve_capture`),
or a set: the capture the set belongs to, else a capture of that one node.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ragnalog.core.paths import safe_name
from ragnalog.service.captures import (
    CaptureInfo,
    get_capture,
    is_capture_ref,
    resolve_capture,
)
from ragnalog.service.errors import Front, OpError, flag, hint
from ragnalog.service.params import (
    DEFAULT_MAX_RAW,
    MAX_COUNT,
    EntryQuery,
    GroupQuery,
    Page,
    Select,
    TextCap,
    blank,
    check_count,
    check_out,
)
from ragnalog.service.sets import Sets, open_set

if TYPE_CHECKING:
    from ragnalog.service.cluster import Capture, Window
    from ragnalog.service.documents import ClusterReport
    from ragnalog.store.logset import SqlSource

__all__ = [
    "NODE_COLUMNS",
    "CaptureWindow",
    "capture",
    "capture_from_ref",
    "context",
    "entries",
    "events",
    "interactions",
    "matrix",
    "names_capture",
    "query",
    "raw_entries",
    "report_file",
    "report_filename",
    "report_payload",
    "timeline",
    "users",
]

type Doc = dict[str, Any]
type WindowMode = Literal["overlap", "union"]

NODE_COLUMNS = ("node", "set")
"""Columns every row of several nodes has; ``columns`` may name them (they are always kept)."""


def _columns(sel: Select, preset: Sequence[str], *, front: Front) -> list[str] | None:
    """Entry columns of ``sel`` (``node`` and ``set`` allowed), without ``node``/``set``."""
    from ragnalog.service.views import ENTRY_FIELDS

    columns = sel.resolve((*ENTRY_FIELDS, *NODE_COLUMNS), preset, front=front)
    if columns is None:
        return None
    return [c for c in columns if c not in NODE_COLUMNS] or ["id"]


@dataclass(frozen=True, kw_only=True)
class CaptureWindow:
    """The time window of a capture view (``capture``; ``GET /api/captures/{ref}`` …).

    Attributes:
        window: overlap or union (overlap: the latest first entry to the earliest last entry
            of the nodes, the time all of them have logs for; union: earliest to latest).
        since: From this time: ISO 8601 (naive = UTC), YYYY-MM-DD, or 30m/2h/1d before the
            capture's last entry (an explicit window instead of --window).
        until: Up to this time (same forms as --since; given alone, either one leaves the
            other end at the capture's first or last entry).

    """

    window: WindowMode = "overlap"
    since: str | None = None
    until: str | None = None

    def resolve(self, capture: Capture, *, front: Front) -> Window:
        """Return the window of ``capture`` (``resolve_window``).

        Raises:
            OpError: ``invalid_value`` for a bad time or an empty overlap.

        """
        from ragnalog.service.cluster import resolve_window

        since, until = blank(self.since), blank(self.until)
        return resolve_window(capture, self.window, since, until, front=front)


def names_capture(sets: Sets, ref: str) -> bool:
    """Whether ``ref`` names a capture rather than a set (commands that take either).

    True for a capture reference (``capture:…``, ``cluster:…``) and for a bare capture
    id that no set has as id, name or unique id prefix (:func:`capture_from_ref`).
    An ambiguous set reference is not a capture: opening it reports the ambiguity.
    """
    if is_capture_ref(ref):
        return True
    from ragnalog.store.store import AmbiguousLogSet, LogSetNotFound

    try:
        sets.open(ref)
    except LogSetNotFound:
        return get_capture(sets, ref) is not None
    except AmbiguousLogSet:
        return False
    return False


def capture_from_ref(sets: Sets, ref: str, *, front: Front) -> Capture:
    """Open the capture of ``ref``: a capture reference, a bare capture id, or a set.

    A set opens its capture; a set without a ``capture`` becomes a capture of one node
    (its ``node``, else its name, else its id) without an id. A ``ref`` that names no set
    but is a capture id or a unique prefix of one (as ``captures`` and the pages print
    it) is that capture.

    Raises:
        OpError: Reasons ``capture_not_found``, ``ambiguous_capture``, ``set_not_found``,
            ``ambiguous_ref``; ``incompatible_sets`` when the members were signed apart.

    """
    from ragnalog.service.cluster import open_capture

    info: CaptureInfo | None
    if is_capture_ref(ref):
        info = resolve_capture(sets, ref, front=front)
    else:
        try:
            ls = open_set(sets, ref, front=front)
        except OpError as exc:
            if exc.reason != "set_not_found":
                raise
            info = get_capture(sets, ref)
            if info is None:
                # a capture id prefix, as capture:PREFIX takes it (ambiguous: say so)
                try:
                    info = resolve_capture(sets, f"capture:{ref}", front=front)
                except OpError as cap_exc:
                    if cap_exc.reason == "capture_not_found":
                        raise exc from None
                    raise
        else:
            m = ls.manifest
            info = get_capture(sets, m.capture) if m.capture is not None else None
            if info is None:
                node = m.node or m.name or m.id
                return open_capture([(node, ls)], cluster=m.cluster, front=front)
    pairs = [(n.node, open_set(sets, n.id, front=front)) for n in info.nodes]
    return open_capture(pairs, capture_id=info.capture, cluster=info.cluster, front=front)


def _doc(struct: Any) -> Doc:
    import msgspec

    out: Doc = msgspec.to_builtins(struct)
    return out


def capture(sets: Sets, ref: str, w: CaptureWindow, *, triage: bool, front: Front) -> Doc:
    """``capture.v1``: per node coverage, gaps, volume, top error, events; the clock skew.

    With ``triage`` each node gets ``verdict`` and ``baseline``: triage against that
    node's own previous set (never another member of the capture).
    """
    from ragnalog.service.cluster import capture_summary

    cap = capture_from_ref(sets, ref, front=front)
    window = w.resolve(cap, front=front)
    return _doc(capture_summary(cap, window=window, store=sets if triage else None))


def timeline(
    sets: Sets,
    ref: str,
    w: CaptureWindow,
    *,
    bucket: str = "1h",
    signature: str | None = None,
    q: EntryQuery | None = None,
    front: Front,
) -> Doc:
    """``cluster-timeline.v1``: entries per bucket and node, by level or of one group.

    ``q`` filters as ``entries`` (level, logger, user, thread, request_id, contains,
    exception, with_exception, coarse_signature, one ``signature``); ``node`` keeps those
    nodes only (the window is still that of the whole capture). Its ``since``/``until`` are
    the window's: pass them in ``w``. ``signature`` is the group, as ``q.signature``.

    Raises:
        OpError: ``usage`` for several signatures or ``NODE:SIGNATURE``; ``invalid_value`` for
            a node not in the capture; the errors of :func:`entries`.

    """
    from ragnalog.service.cluster import cluster_timeline

    q = q or EntryQuery()
    q.check(front=front)
    sigs = q.signatures
    if signature is not None and blank(signature) is not None:
        sigs = [blank(signature) or ""]
    if len(sigs) > 1 or any(":" in s for s in sigs):
        raise OpError(
            "usage",
            f"{flag('signature', front)} takes one group of the capture",
            hint="use --node to keep one node",
        )
    cap = capture_from_ref(sets, ref, front=front)
    window = w.resolve(cap, front=front)
    plain = dataclasses.replace(q, since=None, until=None, signature=None, node=None)
    where = plain.resolve(cap.nodes[0].logset.manifest, front=front)
    if q.nodes:
        cap = _only_nodes(cap, q.nodes, front=front)
    return _doc(
        cluster_timeline(
            cap,
            window=window,
            bucket=bucket,
            signature=sigs[0] if sigs else None,
            where=where,
            front=front,
        )
    )


def _group_filter(q: GroupQuery, cap: Capture, *, front: Front) -> Any:
    """Return the group filters of ``q``; its ``since``/``until`` are the window instead."""
    q.check(front=front)
    plain = dataclasses.replace(q, since=None, until=None)
    return plain.resolve(cap.nodes[0].logset.manifest, front=front)


def matrix(
    sets: Sets,
    ref: str,
    q: GroupQuery,
    page: Page,
    *,
    window: WindowMode = "overlap",
    node: str | None = None,
    spread: str | None = None,
    sort: str = "count",
    skew_min: float = 4.0,
    signature: str | None = None,
    front: Front,
) -> Doc:
    """``matrix.v1``: one row per group with its count, rate, first/last entry per node.

    ``q`` filters as ``groups``; its ``since``/``until`` give the window (explicit),
    else ``window``.
    """
    from ragnalog.service.cluster import matrix as build

    cap = capture_from_ref(sets, ref, front=front)
    where = _group_filter(q, cap, front=front)
    win = CaptureWindow(window=window, since=q.since, until=q.until).resolve(cap, front=front)
    return _doc(
        build(
            cap,
            window=win,
            where=where,
            spread=blank(spread),
            node=blank(node),
            sort=sort,
            limit=page.limit,
            offset=page.offset,
            skew_min=skew_min,
            signature=blank(signature),
            front=front,
        )
    )


def _kinds(values: Sequence[str] | None) -> list[str] | None:
    """Event kinds, comma-separated and/or repeated; ``None`` when none are given."""
    kinds = [k.strip() for v in values or () for k in v.split(",") if k.strip()]
    return kinds or None


def events(
    sets: Sets, ref: str, w: CaptureWindow, *, kinds: Sequence[str] | None, front: Front
) -> Doc:
    """``events.v1``: the events of every node in the window and the pairs across nodes."""
    from ragnalog.service.cluster import events as build

    cap = capture_from_ref(sets, ref, front=front)
    window = w.resolve(cap, front=front)
    return _doc(build(cap, window=window, kinds=_kinds(kinds), front=front))


def interactions(sets: Sets, ref: str, w: CaptureWindow, *, delta_s: int, front: Front) -> Doc:
    """``interactions.v1``: mentions, co-bursts, concurrent jobs, sessions, job ownership."""
    from ragnalog.service.cluster import interactions as build

    check_count(delta_s, "delta", front=front, zero=" (seconds)")
    cap = capture_from_ref(sets, ref, front=front)
    window = w.resolve(cap, front=front)
    return _doc(build(cap, window=window, delta_s=delta_s, front=front))


def entries(
    sets: Sets,
    ref: str,
    q: EntryQuery,
    page: Page,
    sel: Select,
    cap: TextCap,
    *,
    window: WindowMode = "overlap",
    front: Front,
) -> Doc:
    """``entries.v1`` of all nodes of a capture, by time then node; rows add ``node``, ``set``.

    ``q`` filters as ``entries``: ``signature`` may be a unique prefix in any member, or
    several (comma-separated: entries of any of them), each also ``NODE:SIGNATURE`` (that
    group on that node only: both sides of a co-burst); ``node`` keeps the entries of
    those nodes. Its ``since``/``until`` give the window (explicit; of all nodes, also
    with ``node``), else ``window``. ``sel`` may name ``node`` and ``set``, which every
    row has.

    Raises:
        OpError: ``invalid_value`` for a node not in the capture; the errors of
            :func:`~ragnalog.service.cluster.resolve_signatures`.

    """
    from ragnalog.service.cluster import merged_entries, resolve_signatures
    from ragnalog.service.views import ENTRY_BRIEF

    q.check(front=front)
    columns = _columns(sel, ENTRY_BRIEF, front=front)
    capture_ = capture_from_ref(sets, ref, front=front)
    win = CaptureWindow(window=window, since=q.since, until=q.until).resolve(capture_, front=front)
    plain = dataclasses.replace(q, since=None, until=None, signature=None, node=None)
    where = plain.resolve(capture_.nodes[0].logset.manifest, front=front)
    sigs = resolve_signatures(capture_, [s for s in q.signatures if ":" not in s], front=front)
    by_node = _node_signatures(capture_, q.signatures, front=front)
    if len(sigs) == 1 and not by_node:
        where = dataclasses.replace(where, signature=sigs[0])
    if q.nodes:
        capture_ = _only_nodes(capture_, q.nodes, front=front)
    return _doc(
        merged_entries(
            capture_,
            window=win,
            where=where,
            signatures=sigs if len(sigs) > 1 or by_node else None,
            node_signatures=by_node or None,
            limit=page.limit,
            offset=page.offset,
            max_raw=cap.limit or 0,
            columns=columns,
            front=front,
        )
    )


def users(
    sets: Sets,
    ref: str,
    q: EntryQuery,
    page: Page,
    *,
    top: int = 3,
    window: WindowMode = "overlap",
    front: Front,
) -> Doc:
    """``users.v1`` of all nodes of a capture; each row adds ``nodes`` (entries per node).

    The entries are those :func:`entries` lists for ``q`` and ``window`` (a group:
    ``signature``, also ``NODE:SIGNATURE``; ``node``; ``since``/``until``); ``logset``
    is ``capture:ID``. As ``users`` of a set otherwise.

    Raises:
        OpError: ``invalid_value`` for ``top`` outside 1-1000 or a node not in the
            capture; the errors of :func:`~ragnalog.service.cluster.resolve_signatures`.

    """
    from ragnalog.service.cluster import merged_users, resolve_signatures

    q.check(front=front)
    if not 1 <= top <= 1000:
        raise OpError("invalid_value", f"{flag('top', front)} must be between 1 and 1000")
    capture_ = capture_from_ref(sets, ref, front=front)
    win = CaptureWindow(window=window, since=q.since, until=q.until).resolve(capture_, front=front)
    plain = dataclasses.replace(q, since=None, until=None, signature=None, node=None)
    where = plain.resolve(capture_.nodes[0].logset.manifest, front=front)
    sigs = resolve_signatures(capture_, [s for s in q.signatures if ":" not in s], front=front)
    by_node = _node_signatures(capture_, q.signatures, front=front)
    if len(sigs) == 1 and not by_node:
        where = dataclasses.replace(where, signature=sigs[0])
    if q.nodes:
        capture_ = _only_nodes(capture_, q.nodes, front=front)
    rows, anonymous = merged_users(
        capture_,
        window=win,
        where=where,
        signatures=sigs if len(sigs) > 1 or by_node else None,
        node_signatures=by_node or None,
        top=top,
    )
    end = None if page.limit is None else page.offset + page.limit
    items = rows[page.offset : end]
    for r in items:
        for k in ("first_ts", "last_ts"):
            r[k] = None if r[k] is None else r[k].isoformat(timespec="milliseconds")
    nxt = page.offset + len(items)
    return {
        "schema": "users.v1",
        "logset": capture_.ref,
        "count": len(items),
        "total": len(rows),
        "offset": page.offset,
        "next_offset": nxt if nxt < len(rows) else None,
        "truncated": nxt < len(rows),
        "entries": sum(r["entries"] for r in rows),
        "without_user": anonymous,
        "items": items,
    }


def _capture_views(capture_: Capture, prefix: str = "") -> dict[str, list[SqlSource]]:
    """``query`` views of a capture: each member's file of the view, with ``node`` and ``set``."""
    from ragnalog.store.logset import SqlSource

    views: dict[str, list[SqlSource]] = {}
    for member in capture_.nodes:
        consts = (("node", member.node), ("set", member.logset.id))
        for view, sources in member.logset.sql_views().items():
            views.setdefault(prefix + view, []).extend(
                SqlSource(s.path, consts + s.columns) for s in sources
            )
    return views


def query(
    sets: Sets,
    ref: str,
    sql: str,
    *,
    front: Front,
    baseline: str | None = None,
    max_rows: int | None = None,
    timeout_s: float | None = None,
    memory_limit: str | None = None,
    threads: int | None = None,
    max_bytes: int | None = None,
) -> Doc:
    """``query.v1`` of DuckDB ``sql`` over all nodes of a capture; ``logset`` is ``capture:ID``.

    The views are those of a set's ``query`` (``entries``, ``groups``, and ``merges`` of
    the members that have one), each the rows of every member with leading columns
    ``node`` and ``set`` (the member's set id). ``baseline`` (a capture reference, a bare
    capture id, or a set: its capture) adds ``baseline_entries``, ``baseline_groups``
    (and ``baseline_merges``) of that capture, ``"baseline"`` is its ``capture:ID``. The
    rules and limits are those of :func:`ragnalog.service.queries.query`.

    Raises:
        OpError: ``usage`` for anything but one ``SELECT``; ``sql_error``; the errors of
            :func:`capture_from_ref`.

    """
    from ragnalog.service.queries import (
        BASELINE_PREFIX,
        check_select,
        query_doc,
        query_view_columns,
        run_select,
    )

    check_select(sql)
    capture_ = capture_from_ref(sets, ref, front=front)
    views = _capture_views(capture_)
    base_ref = None
    if baseline is not None:
        base = capture_from_ref(sets, baseline, front=front)
        base_ref = base.ref
        views |= _capture_views(base, BASELINE_PREFIX)
    df, truncated = run_select(
        views,
        sql,
        columns=query_view_columns(list(views), lead=NODE_COLUMNS),
        max_rows=max_rows,
        timeout_s=timeout_s,
        memory_limit=memory_limit,
        threads=threads,
        max_bytes=max_bytes,
    )
    return query_doc(capture_.ref, df, truncated, baseline=base_ref)


def _node_signatures(
    capture_: Capture, given: Sequence[str], *, front: Front
) -> dict[str, list[str]]:
    """Resolve the ``NODE:SIGNATURE`` items of ``given``: the signatures kept per node.

    Raises:
        OpError: ``invalid_value`` for a node not in the capture; the errors of
            :func:`~ragnalog.service.cluster.resolve_signatures`.

    """
    from ragnalog.service.cluster import resolve_signatures

    pairs = [tuple(s.split(":", 1)) for s in given if ":" in s]
    unknown = [n for n, _ in pairs if n not in capture_.node_ids]
    if unknown:
        raise OpError(
            "invalid_value",
            f"node {unknown[0]!r} of {flag('signature', front)} is not in the capture",
            hint=f"its nodes: {', '.join(capture_.node_ids)}",
        )
    out: dict[str, list[str]] = {}
    for node, sig in pairs:
        (whole,) = resolve_signatures(capture_, [sig], front=front)
        kept = out.setdefault(node, [])
        if whole not in kept:
            kept.append(whole)
    return out


def _only_nodes(capture_: Capture, nodes: Sequence[str], *, front: Front) -> Capture:
    """Return the capture with only ``nodes`` (its id and reference stay the capture's).

    Raises:
        OpError: ``invalid_value`` naming a node the capture does not have.

    """
    unknown = [n for n in nodes if n not in capture_.node_ids]
    if unknown:
        raise OpError(
            "invalid_value",
            f"{flag('node', front)}: no node {', '.join(map(repr, unknown))} in {capture_.ref}",
            hint=f"its nodes: {', '.join(capture_.node_ids)}",
        )
    keep = tuple(m for m in capture_.nodes if m.node in nodes)
    return dataclasses.replace(capture_, nodes=keep)


def raw_entries(
    sets: Sets,
    ref: str,
    q: EntryQuery,
    page: Page,
    cap: TextCap,
    *,
    window: WindowMode = "overlap",
    front: Front,
) -> list[tuple[str, str | None]]:
    """Return the raw texts of a page of merged entries as ``(raw, "NODE file:line")``."""
    doc = entries(
        sets,
        ref,
        q,
        page,
        Select(columns=["raw", "source_path", "source_line"]),
        cap,
        window=window,
        front=front,
    )
    pairs: list[tuple[str, str | None]] = []
    for row in doc["items"]:
        path, line = row.get("source_path"), row.get("source_line")
        where = f"{Path(path).name}:{line}" if path else ""
        pairs.append((str(row["raw"]), f"{row['node']} {where}".strip()))
    return pairs


def context(
    sets: Sets,
    ref: str,
    entry_id: str,
    *,
    window_s: float,
    sel: Select,
    cap: TextCap,
    front: Front,
) -> tuple[Doc, int]:
    """``entries.v1`` of every node within ``window_s`` seconds of one entry of set ``ref``.

    ``ref`` may also name a capture (:func:`names_capture`); the anchor's set is then
    the member that has ``entry_id``.

    Returns the document and the anchor's index in ``items`` (``-1`` when cut away).

    Raises:
        OpError: ``invalid_value`` when the set belongs to no capture or for a bad
            window; reasons ``set_not_found``, ``entry_not_found``, ``capture_not_found``.

    """
    from ragnalog.service.cluster import context_all_nodes, open_capture
    from ragnalog.service.views import DETAIL_BRIEF

    columns = _columns(sel, DETAIL_BRIEF, front=front)
    if not window_s >= 0:
        raise OpError("invalid_value", f"{flag('window', front)} must be >= 0 (seconds)")
    if window_s > MAX_COUNT:
        raise OpError("invalid_value", f"{flag('window', front)} must be <= {MAX_COUNT} (seconds)")
    if names_capture(sets, ref):
        capture_ = capture_from_ref(sets, ref, front=front)
        member = next(
            (n.logset for n in capture_.nodes if n.logset.entry(entry_id) is not None), None
        )
        if member is None:
            raise OpError.because(
                "entry_not_found",
                front,
                f"no entry with id {entry_id!r} on any node of {capture_.ref}",
                hint=hint("entry_not_found", front, set=ref, id=ref),
            )
        m = member.manifest
    else:
        m = open_set(sets, ref, front=front).manifest
        info = get_capture(sets, m.capture) if m.capture is not None else None
        if info is None:
            name = m.name or m.id
            raise OpError(
                "invalid_value",
                f"log set {name} belongs to no capture, so there are no other nodes",
                hint=(
                    f"label the sets of one capture (`ragnalog label {name} --node N "
                    "--capture ID`), or leave out --all-nodes"
                    if front == "cli"
                    else "label the sets of one capture (POST /api/sets/{ref}/label), "
                    "or leave out all_nodes"
                ),
            )
        pairs = [(n.node, open_set(sets, n.id, front=front)) for n in info.nodes]
        capture_ = open_capture(pairs, capture_id=info.capture, cluster=info.cluster, front=front)
    keep = None if columns is None else list(dict.fromkeys(["id", *columns]))
    doc = _doc(
        context_all_nodes(
            capture_,
            m.id,
            entry_id,
            window_s=window_s,
            max_raw=cap.limit or 0,
            columns=keep,
            front=front,
        )
    )
    at = next(
        (
            i
            for i, row in enumerate(doc["items"])
            if row.get("id") == entry_id and row.get("set") == m.id
        ),
        -1,
    )
    if columns is not None and "id" not in columns:
        for row in doc["items"]:
            row.pop("id", None)
    return doc, at


def report_payload(
    sets: Sets,
    ref: str | Capture,
    w: CaptureWindow,
    *,
    examples: int = 3,
    max_raw: int = DEFAULT_MAX_RAW,
    max_groups: int = 500,
    triage: bool = True,
    front: Front,
) -> ClusterReport:
    """``cluster-report.v1`` of the capture of ``ref`` (``urls`` null: the web app sets them).

    ``ref`` may be an open capture. ``max_groups`` 0 = all; with ``triage`` each node is
    triaged against its own previous set.
    """
    from ragnalog.service.cluster import cluster_payload

    capture_ = capture_from_ref(sets, ref, front=front) if isinstance(ref, str) else ref
    window = w.resolve(capture_, front=front)
    return cluster_payload(
        capture_,
        window=window,
        examples=examples,
        max_raw=max_raw,
        max_groups=max_groups,
        store=sets if triage else None,
        front=front,
    )


def report_filename(capture_: Capture) -> Path:
    """Default file of a cluster page: ``<cluster>_<capture id>.html`` (or the set's name)."""
    if capture_.id is None:
        m = capture_.nodes[0].logset.manifest
        stem = m.name or m.id
    else:
        stem = f"{capture_.cluster}_{capture_.id}" if capture_.cluster else capture_.id
    return Path(safe_name(stem, "capture") + ".html")


def report_file(
    sets: Sets,
    ref: str,
    w: CaptureWindow,
    *,
    out: Path | None = None,
    examples: int = 3,
    max_raw: int = DEFAULT_MAX_RAW,
    max_groups: int = 500,
    front: Front,
) -> Doc:
    """Write the static cluster page of a capture; ``report-file.v1`` with ``capture``, ``nodes``.

    ``max_groups`` 0 = all. Each node is triaged against its own previous set. ``path``
    is absolute.
    """
    import msgspec

    from ragnalog.report import write_cluster_report

    check_out(out, front=front)
    capture_ = capture_from_ref(sets, ref, front=front)
    payload = report_payload(
        sets, capture_, w, examples=examples, max_raw=max_raw, max_groups=max_groups, front=front
    )
    path = out if out is not None else report_filename(capture_)
    info = write_cluster_report(msgspec.to_builtins(payload), path)
    return {
        "schema": "report-file.v1",
        "path": info.path.resolve().as_posix(),
        "bytes": info.bytes,
        "groups": info.groups,
        "entries": info.entries,
        "compare": False,
        "capture": capture_.id,
        "nodes": info.nodes,
    }
