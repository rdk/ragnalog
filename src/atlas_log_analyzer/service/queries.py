"""Read documents: sets, set, stats, groups, group/show, entries, context, frames, timeline, query.

Every builder takes the :class:`~atlas_log_analyzer.service.sets.Sets` to read from,
the set reference and the request models of :mod:`~atlas_log_analyzer.service.params`,
and returns the JSON document (a dict with ``"schema"`` first). ``front`` says which
front end asks: it picks error codes and how messages spell parameters. Rows are
serialised once, by :func:`~atlas_log_analyzer.store.logset.to_records` (timestamps
``…T09:00:00.000+00:00``, naive values taken as UTC).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atlas_log_analyzer.service.errors import Front, OpError, flag, hint
from atlas_log_analyzer.service.params import (
    EntryQuery,
    GroupQuery,
    Page,
    Select,
    TextCap,
    check_count,
    parse_time,
)
from atlas_log_analyzer.service.sets import Sets, open_set
from atlas_log_analyzer.service.views import (
    DETAIL_BRIEF,
    ENTRY_BRIEF,
    ENTRY_FIELDS,
    GROUP_BRIEF,
    GROUP_FIELDS,
    frames_view,
    resolve_signature,
    set_item,
    set_summary,
)
from atlas_log_analyzer.store.filters import EntryFilter

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSet
    from atlas_log_analyzer.store.manifest import LogSetManifest

__all__ = [
    "Doc",
    "context",
    "entries",
    "entry_raw",
    "frames",
    "group",
    "group_examples",
    "groups",
    "query",
    "raw_entries",
    "set_doc",
    "sets_doc",
    "show",
    "show_raw",
    "sql_hint",
    "stats",
    "timeline",
    "view_fields",
]

type Doc = dict[str, Any]
"""A JSON document: ``"schema"`` first, then its keys."""

type Summary = Callable[[LogSet], dict[str, int]]
"""Computes the ``summary`` of a set (the web app passes its cached version)."""
type ManifestSummary = Callable[[LogSetManifest], dict[str, int]]
"""Computes the ``summary`` of a listed set from its manifest (the CLI reads the groups
file with pyarrow only; the web app uses its cache)."""


def _records(df: Any) -> list[dict[str, Any]]:
    from atlas_log_analyzer.store.logset import to_records

    return to_records(df)


def _name(ls: LogSet) -> str:
    return ls.manifest.name or ls.manifest.id


def sets_doc(sets: Sets, *, brief: bool, store: str, summary: ManifestSummary) -> Doc:
    """``sets.v1``: every set, newest first, each with its ``summary`` and ``stale`` flag.

    ``store`` is how the front end names the store (the CLI its path, the API only
    the directory name). A set whose summary cannot be computed gets ``null``.
    """
    items = []
    for m in sets.manifests():
        found: dict[str, int] | None
        try:
            found = summary(m)
        except Exception:  # a damaged set must not break the listing
            found = None
        items.append(set_item(m.to_dict(), found, brief=brief))
    return {"schema": "sets.v1", "store": store, "count": len(items), "items": items}


def set_doc(sets: Sets, ref: str, *, front: Front, summary: Summary = set_summary) -> Doc:
    """``logset.v1`` of one set plus its ``summary``."""
    ls = open_set(sets, ref, front=front)
    return {**ls.manifest.to_dict(), "summary": summary(ls)}


def stats(sets: Sets, ref: str, *, front: Front) -> Doc:
    """``stats.v1``: counts per level, logger and hour, time range and sources."""
    ls = open_set(sets, ref, front=front)
    data = {k: v for k, v in ls.stats().items() if k != "schema"}
    return {"schema": "stats.v1", "logset": ls.manifest.id, **data}


def groups(
    sets: Sets,
    ref: str,
    q: GroupQuery,
    page: Page,
    sel: Select,
    *,
    sort: str = "count",
    coarse: bool = False,
    front: Front,
) -> Doc:
    """``groups.v1``: one page of the matching groups with ``total``, ``offset``, ``truncated``."""
    q.check(front=front)
    valid = (*GROUP_FIELDS, "signatures") if coarse else GROUP_FIELDS
    columns = sel.resolve(valid, GROUP_BRIEF, front=front)
    ls = open_set(sets, ref, front=front)
    where = q.resolve(ls.manifest, front=front)
    df = ls.groups(where=where, coarse=coarse, sort=sort, limit=page.limit, offset=page.offset)
    total = df.height if page.complete(df.height) else ls.count_groups(where=where, coarse=coarse)
    items = _records(df if columns is None else df.select(columns))
    return {
        "schema": "groups.v1",
        "logset": ls.manifest.id,
        "count": len(items),
        "total": total,
        "offset": page.offset,
        "truncated": page.offset + len(items) < total,
        "items": items,
    }


def _where(ls: LogSet, q: EntryQuery, front: Front) -> EntryFilter:
    where = q.resolve(ls.manifest, front=front)
    if where.signature is not None:
        where = replace(where, signature=resolve_signature(ls, where.signature))
    return where


def entries(
    sets: Sets, ref: str, q: EntryQuery, page: Page, sel: Select, cap: TextCap, *, front: Front
) -> Doc:
    """``entries.v1``: one page of the matching entries in time order.

    ``total`` counts all matches, ``next_offset`` is where the next page starts
    (``null`` on the last page) and ``truncated`` says whether one follows; ``raw``
    and ``body`` are cut by ``cap``.
    """
    q.check(front=front)
    columns = sel.resolve(ENTRY_FIELDS, ENTRY_BRIEF, front=front)
    ls = open_set(sets, ref, front=front)
    where = _where(ls, q, front)
    df = ls.entries(where=where, limit=page.limit, offset=page.offset, columns=columns)
    total = df.height if page.complete(df.height) else ls.count_entries(where=where)
    items = cap.rows(_records(df))
    nxt = page.offset + len(items)
    return {
        "schema": "entries.v1",
        "logset": ls.manifest.id,
        "count": len(items),
        "total": total,
        "offset": page.offset,
        "next_offset": nxt if nxt < total else None,
        "truncated": nxt < total,
        "items": items,
    }


def _raw_pairs(df: Any, cap: TextCap) -> list[tuple[str, str | None]]:
    pairs = []
    for row in df.select("raw", "source_path", "source_line").iter_rows(named=True):
        path, line = row["source_path"], row["source_line"]
        pairs.append((cap.text(str(row["raw"])), f"{Path(path).name}:{line}" if path else None))
    return pairs


def raw_entries(
    sets: Sets, ref: str, q: EntryQuery, page: Page, cap: TextCap, *, front: Front
) -> list[tuple[str, str | None]]:
    """Return the raw texts of a page of entries as ``(raw, "file:line")`` (``entries --raw``)."""
    q.check(front=front)
    ls = open_set(sets, ref, front=front)
    columns = ["raw", "source_path", "source_line"]
    where = _where(ls, q, front)
    df = ls.entries(where=where, limit=page.limit, offset=page.offset, columns=columns)
    return _raw_pairs(df, cap)


def _group_entries(
    ls: LogSet, signature: str, limit: int | None, columns: list[str] | None, front: Front
) -> Any:
    df = ls.entries(where=EntryFilter(signature=signature), limit=limit, columns=columns)
    if df.height == 0:
        raise OpError.because(
            "group_not_found",
            front,
            f"no entries with signature {signature!r} in log set {ls.manifest.id}",
            hint=hint("group_not_found", front, set=_name(ls), id=ls.manifest.id),
        )
    return df


def show(
    sets: Sets,
    ref: str,
    signature: str,
    *,
    limit: int | None,
    sel: Select,
    cap: TextCap,
    front: Front,
) -> Doc:
    """``entries.v1`` of one group (``show``): its first ``limit`` entries plus ``group``.

    ``signature`` may be a unique prefix (the document echoes the full one); a group
    without entries is reason ``group_not_found``. ``brief`` also cuts ``group``.
    """
    columns = sel.resolve(ENTRY_FIELDS, DETAIL_BRIEF, front=front)
    ls = open_set(sets, ref, front=front)
    signature = resolve_signature(ls, signature)
    df = _group_entries(ls, signature, limit, columns, front)
    row = ls.group(signature)
    if row is not None and sel.brief:
        row = {k: row[k] for k in GROUP_BRIEF}
    items = cap.rows(_records(df))
    return {
        "schema": "entries.v1",
        "logset": ls.manifest.id,
        "signature": signature,
        "group": row,
        "count": len(items),
        "items": items,
    }


def show_raw(
    sets: Sets, ref: str, signature: str, *, limit: int | None, cap: TextCap, front: Front
) -> list[tuple[str, str | None]]:
    """Return the raw texts of one group's first ``limit`` entries (``show --raw``)."""
    ls = open_set(sets, ref, front=front)
    signature = resolve_signature(ls, signature)
    return _raw_pairs(_group_entries(ls, signature, limit, None, front), cap)


def group(
    sets: Sets,
    ref: str,
    signature: str,
    *,
    limit: int | None,
    coarse: bool,
    sel: Select,
    cap: TextCap,
    front: Front,
) -> Doc:
    """``group.v1``: one group row, its hourly ``histogram`` and its first ``limit`` entries.

    The web API's group page (the CLI's ``show`` is :func:`show`). With ``coarse``
    the signature is a coarse one and the histogram is empty.
    """
    columns = sel.resolve(ENTRY_FIELDS, DETAIL_BRIEF, front=front)
    ls = open_set(sets, ref, front=front)
    signature = resolve_signature(ls, signature, coarse=coarse)
    row = ls.group(signature, coarse=coarse)
    if row is None:
        raise OpError.because(
            "group_not_found",
            front,
            f"no group {signature!r} in log set {ls.manifest.id}",
            hint=hint("group_not_found", front, set=_name(ls), id=ls.manifest.id),
        )
    where = EntryFilter(coarse_signature=signature) if coarse else EntryFilter(signature=signature)
    items = cap.rows(_records(ls.entries(where=where, limit=limit, columns=columns)))
    if sel.brief:
        row = {k: row[k] for k in (*GROUP_BRIEF, "signatures") if k in row}
    return {
        "schema": "group.v1",
        "logset": ls.manifest.id,
        "group": row,
        "histogram": [] if coarse else ls.histogram("1h", where=where),
        "count": len(items),
        "items": items,
    }


def context(
    sets: Sets,
    ref: str,
    entry_id: str,
    *,
    before: int,
    after: int,
    any_thread: bool,
    sel: Select,
    cap: TextCap,
    front: Front,
) -> tuple[Doc, int]:
    """``entries.v1`` around one entry, and the anchor's index in ``items``.

    ``before``/``after`` entries of the anchor's thread (all threads with
    ``any_thread``) in time order, the anchor included.
    """
    for name, value in (("before", before), ("after", after)):
        check_count(value, name, front=front)
    columns = sel.resolve(ENTRY_FIELDS, DETAIL_BRIEF, front=front)
    ls = open_set(sets, ref, front=front)
    df = ls.context(entry_id, before=before, after=after, same_thread=not any_thread)
    if df is None:
        raise OpError.because(
            "entry_not_found",
            front,
            f"no entry with id {entry_id!r} in log set {ls.manifest.id}",
            hint=hint("entry_not_found", front, set=_name(ls), id=ls.manifest.id),
        )
    at = df["id"].to_list().index(entry_id)
    thread = df["thread"][at]
    items = cap.rows(_records(df if columns is None else df.select(columns)))
    doc = {
        "schema": "entries.v1",
        "logset": ls.manifest.id,
        "anchor": entry_id,
        "thread": thread,
        "any_thread": any_thread,
        "count": len(items),
        "items": items,
    }
    return doc, at


def frames(
    sets: Sets,
    ref: str,
    item: str,
    *,
    limit: int | None,
    all_frames: bool,
    app_packages: tuple[str, ...] | None,
    front: Front,
) -> Doc:
    """``frames.v1``: the exception chain of an entry or group, application frames first.

    ``item`` is an entry id, or a signature or unique prefix (its first example
    entry); ``app_packages`` (from the settings) defines application code when given.
    """
    ls = open_set(sets, ref, front=front)
    doc = frames_view(ls, item, limit=limit, all_frames=all_frames, app_packages=app_packages)
    if doc is None:
        raise OpError.because(
            "ref_not_found",
            front,
            f"no entry or group {item!r} in log set {ls.manifest.id}",
            hint=hint("ref_not_found", front, set=_name(ls), id=ls.manifest.id),
        )
    return {"schema": "frames.v1", **doc}


def timeline(sets: Sets, ref: str, q: EntryQuery, *, bucket: str, front: Front) -> Doc:
    """``timeline.v1``: entry counts per ``bucket`` (``1h``, ``15m``, ``1d`` …)."""
    q.check(front=front)
    ls = open_set(sets, ref, front=front)
    where = _where(ls, q, front)
    try:
        items = ls.histogram(bucket, where=where)
    except ValueError as exc:  # filters are valid: the bucket is not
        raise OpError("invalid_value", f"{flag('bucket', front)}: {exc}") from exc
    return {
        "schema": "timeline.v1",
        "logset": ls.manifest.id,
        "bucket": bucket,
        "count": len(items),
        "items": items,
    }


def entry_raw(sets: Sets, ref: str, entry_id: str, *, front: Front) -> str:
    """Return one entry's exact text as in its log file: its banner lines, then ``raw``.

    Raises:
        OpError: ``entry_not_found`` for an unknown id.

    """
    ls = open_set(sets, ref, front=front)
    row = ls.entry(entry_id)
    if row is None:
        raise OpError.because(
            "entry_not_found", front, f"no entry {entry_id!r} in log set {ls.manifest.id}"
        )
    context = row.get("context") or {}
    preamble = context.get("preamble") if isinstance(context, dict) else None
    raw = str(row.get("raw") or "")
    return f"{preamble}\n{raw}" if preamble else raw


def group_examples(
    sets: Sets,
    ref: str,
    signature: str,
    *,
    max_raw: int,
    front: Front,
    since: str | None = None,
    until: str | None = None,
    limit: int = 3,
) -> Doc:
    """``report-entries.v1``: a group's example entries in the report's entry shape.

    Without ``since``/``until`` the items are the group's stored examples. With either,
    they are the group's first ``limit`` entries in ``[since, until)`` (forms as
    ``--since``; relative times count back from the set's end): the report page's
    examples of a time window.

    Raises:
        OpError: ``group_not_found``, ``invalid_value``.

    """
    from atlas_log_analyzer.report import example_entries

    ls = open_set(sets, ref, front=front)
    signature = resolve_signature(ls, signature)
    row = ls.group(signature)
    if row is None:
        raise OpError.because(
            "group_not_found", front, f"no group {signature!r} in log set {ls.manifest.id}"
        )
    if since is None and until is None:
        ids = [str(i) for i in row.get("examples") or []]
    else:
        last = ls.manifest.last_ts
        where = EntryFilter(
            signature=signature,
            since=parse_time(since, "since", last, front=front),
            until=parse_time(until, "until", last, front=front),
        )
        found = ls.entries(where=where, limit=limit, columns=["id"])
        ids = [str(i) for i in found.get_column("id").to_list()]
    return {
        "schema": "report-entries.v1",
        "logset": ls.manifest.id,
        "signature": signature,
        "items": example_entries(ls, ids, max_raw=max_raw),
    }


_CANDIDATES_RE = re.compile(r"Candidate bindings: (.+)")
_COLUMN_ERRORS = ("Binder Error", "ColumnNotFound", "not found in FROM clause", "column named")


def view_fields() -> dict[str, tuple[str, ...]]:
    """Columns of the views of ``query`` (``entries``, ``groups``, ``merges``)."""
    from atlas_log_analyzer.store.schema import MERGES_SCHEMA

    return {"entries": ENTRY_FIELDS, "groups": GROUP_FIELDS, "merges": tuple(MERGES_SCHEMA.names)}


def sql_hint(message: str, views: list[str]) -> str | None:
    """Next step for a failed query: candidate columns, the views' columns, or the views."""
    if any(marker in message for marker in _COLUMN_ERRORS):
        if m := _CANDIDATES_RE.search(message):
            return (
                f"candidate columns: {m.group(1).strip()}; all columns: "
                "`atlas-log query --help` or `atlas-log schema entries|groups`"
            )
        fields = view_fields()
        return "; ".join(f"{v} columns: {', '.join(fields[v])}" for v in views)
    if "Catalog Error" in message and "does not exist" in message:
        return f"available views: {', '.join(views)}"
    return None


def query(sets: Sets, ref: str, sql: str, *, front: Front) -> Doc:
    """``query.v1``: the rows of DuckDB ``sql`` over the set's views (never truncated).

    A rejected query is ``sql_error``; its hint lists candidate columns, the views'
    columns or the available views.
    """
    import duckdb
    import polars as pl

    ls = open_set(sets, ref, front=front)
    try:
        df = ls.sql(sql)
    except (duckdb.Error, pl.exceptions.PolarsError) as exc:
        message = str(exc).strip()
        views = [v for v in view_fields() if v != "merges" or ls.merges_path.exists()]
        raise OpError("sql_error", message, hint=sql_hint(message, views)) from exc
    items = _records(df)
    return {
        "schema": "query.v1",
        "logset": ls.manifest.id,
        "columns": list(df.columns),
        "count": len(items),
        "total": len(items),
        "truncated": False,
        "items": items,
    }
