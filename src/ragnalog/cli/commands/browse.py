"""Commands that read one set: sets, set, groups, show, entries, context, frames, query, stats.

``entries`` also reads all nodes of a capture, and ``context --all-nodes`` the nodes of
the set's capture (:mod:`ragnalog.service.capture_queries`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from cyclopts import Parameter

from ragnalog.cli import context as ctx
from ragnalog.cli import params, render
from ragnalog.service import queries
from ragnalog.service.errors import OpError
from ragnalog.service.params import EntryQuery, GroupQuery, Page, Select, TextCap
from ragnalog.service.views import (
    DETAIL_BRIEF,
    ENTRY_BRIEF,
    ENTRY_FIELDS,
    GROUP_BRIEF,
    GROUP_FIELDS,
    set_summary,
)
from ragnalog.store.schema import MERGES_SCHEMA

# polars, DuckDB, rich and the ingest pipeline are imported by the code that needs them.

__all__ = [
    "captures",
    "context",
    "entries",
    "frames",
    "groups",
    "query",
    "set_",
    "sets",
    "show",
    "stats",
]


def sets(*, brief: bool = False) -> None:
    """List the log sets in the data directory, newest first.

    Output (json):
      {"schema": "sets.v1", "store": str, "count": int,
       "items": [<logset.v1 manifest without "schema"> + "summary" + "stale"]}
    Each item has id, name, format, sources, first_ts, last_ts, entry_count,
    group_count, created_at, tool_version, schema_version, signature_version,
    mask_rules_version, signature_spec, recipe_key, node, cluster, capture,
    "summary": {"errors", "warnings", "error_groups"} (entries at ERROR/FATAL,
    entries at WARN, groups at ERROR/FATAL; null when the set cannot be read)
    and "stale": true when recipe_key differs from the recipe this version
    signs the set's format and spec with (rebuild with `upgrade`; triage
    does not use stale sets as baselines). node, cluster and capture say which
    cluster node a set holds and which capture it belongs to (null when
    unknown; see `label`, `captures`). --brief keeps only id, name, first_ts,
    last_ts, entry_count, group_count, summary, stale, node and capture. An
    empty or missing data directory gives count 0. A set directory whose manifest cannot be
    read is not listed; one warning per such directory goes to stderr (clear it
    by deleting its directory).

    Exit codes: 0 ok (also when empty); 3 store error.

    Args:
        brief: Only id, name, first_ts, last_ts, entry_count, group_count, summary, stale,
            node, capture.

    """
    if ctx.remote():
        doc = ctx.remote_or("sets", sets, locals(), dict)
        ctx.out().document(doc, columns=render.SET_COLUMNS, title="log sets")
        return
    store = ctx.store()
    doc = queries.sets_doc(
        store,
        brief=brief,
        store=ctx.current().store.as_posix(),
        summary=lambda m: set_summary(store.set_dir(m.id)),  # pyarrow only: no polars
    )
    ctx.out().document(doc, columns=render.SET_COLUMNS, title="log sets")
    for name, reason in store.unreadable():
        ctx.out().warn(
            f"set directory {store.set_dir(name)} is not listed: its manifest cannot be read "
            f"({reason}); `ragnalog remove {name}` clears this"
        )


def captures(*, brief: bool = False) -> None:
    """List the captures (the sets of several nodes collected together).

    A capture is the sets whose manifests share a "capture" id: one cluster log
    export or one `ingest` that split its input into one set per node (also
    `label --capture`). Newest last_ts first. Reads the manifests only.

    Output (json):
      {"schema": "captures.v1", "count": int,
       "items": [{"capture", "cluster", "first_ts", "last_ts", "entry_count",
                  "nodes": [{"node", "id", "name", "first_ts", "last_ts",
                             "entry_count"}]}]}
    "nodes" are the member sets sorted by node; "cluster" is the members'
    cluster. --brief keeps only node, id and name per node. A capture is
    referred to as capture:ID (a unique prefix of its id), cluster:NAME (the
    newest capture of the cluster) or cluster:NAME@YYYY-MM-DD.

    Exit codes: 0 ok (also when empty); 3 store error.

    Args:
        brief: Only node, id and name per node.

    """
    from ragnalog.service.captures import captures_doc

    doc = ctx.remote_or(
        "captures", captures, locals(), lambda: captures_doc(ctx.store(), brief=brief)
    )
    out = ctx.out()
    out.document(doc, table=lambda console: render.captures(console, doc, color=out.color))


def set_(logset: str, /) -> None:
    """Print one log set: its manifest plus the severity summary of `sets`.

    Output (json):
      {"schema": "logset.v1", <manifest keys, see `ingest`>,
       "summary": {"errors", "warnings", "error_groups"}}
    "summary" counts the entries at ERROR/FATAL, the entries at WARN and the
    groups at ERROR/FATAL, as in `sets` and GET /api/sets/{ref}. Table mode
    prints the manifest as tables.

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    doc = ctx.remote_or(
        "set", set_, locals(), lambda: queries.set_doc(ctx.store(), logset, front="cli")
    )
    ctx.out().document(doc, items=None, title=f"log set {doc['id']}")


def groups(
    logset: str,
    /,
    *,
    q: Annotated[GroupQuery, Parameter(name="*")] = params.ALL_GROUPS,
    sort: Literal["count", "last_seen", "first_seen", "signature", "template"] = "count",
    limit: int = 50,
    offset: int = 0,
    coarse: bool = False,
    columns: list[str] | None = None,
    brief: bool = False,
) -> None:
    """List the groups (one per message signature) of a log set.

    A group collects all entries whose level, logger, masked message and
    exception chain are equal; its "signature" (16 hex chars) is stable across
    sets, so it can be used with `show`, `entries --signature` and `diff`.

    Output (json):
      {"schema": "groups.v1", "logset": id, "count": int, "total": int,
       "offset": int, "next_offset": int | null, "truncated": bool, "items": [
        {"signature", "coarse_signature", "template", "level", "logger",
         "level_name", "exception_type", "count", "first_seen", "last_seen",
         ...}, ...]}
    "level" is numeric (ERROR=50), "level_name" the name. "items" holds the
    groups.v1 columns (`ragnalog schema groups`: also user_count, thread_count,
    source_count, top_users, top_threads, sources, examples), at most --limit
    rows starting at --offset (in --sort order). "total" is the number of
    matching groups before --offset/--limit, "offset" echoes --offset and
    "truncated" is true when more rows follow this page (offset + count <
    total) and "next_offset" is where it starts (null on the last page). With --coarse, "signature"
    is the coarse signature and "signatures" lists the member signatures.
    --columns keeps only the named columns (comma-separated and/or repeated;
    unknown name: error "invalid_value", "hint" lists the valid ones).
    --brief is the preset --columns signature,template,level_name,logger,
    exception_type,count,first_seen,last_seen; --brief with --columns is a
    usage error.
    --with-exception keeps groups that carry an exception (exception_type not
    null) at any level, so a DEBUG entry with a stack trace is found even
    though --min-level WARN would hide it. --exception TEXT matches a
    case-insensitive substring of exception_type (the outermost exception);
    --contains TEXT a case-insensitive substring of template. With --coarse
    all filters apply to the member groups.

    Exit codes: 0 ok (also when no group matches); 1 bad option, unknown
    column, or --brief with --columns; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        q: Group filters (the options --level to --contains, see GroupQuery).
        sort: count, last_seen (newest first), first_seen (oldest first), signature, template.
        limit: Maximum number of rows; 0 = all.
        offset: Skip this many rows first (paging: next offset = offset + count).
        coarse: Group by coarse signature (level + logger + masked message; no exceptions).
        columns: Columns to return: comma-separated (signature,count) and/or repeated.
        brief: Only signature, template, level_name, logger, exception_type, count,
            first_seen, last_seen.

    """
    page = Page.parse(limit, offset, front="cli")
    sel = Select(columns=columns, brief=brief)
    doc = ctx.remote_or(
        "groups",
        groups,
        locals(),
        lambda: queries.groups(
            ctx.store(), logset, q, page, sel, sort=sort, coarse=coarse, front="cli"
        ),
    )
    valid = (*GROUP_FIELDS, "signatures") if coarse else GROUP_FIELDS
    shown = sel.resolve(valid, GROUP_BRIEF, front="cli") or render.GROUP_COLUMNS
    ctx.out().document(doc, columns=shown, title=f"groups of {doc['logset']}")


def show(
    logset: str,
    signature: str,
    /,
    *,
    limit: int = 20,
    raw: bool = False,
    max_raw: int | None = None,
    columns: list[str] | None = None,
    brief: bool = False,
) -> int:
    """Show the entries of one group, with the variables extracted from each message.

    Output (json):
      {"schema": "entries.v1", "logset": id, "signature": str,
       "group": {<groups.v1 row>} | null, "count": int,
       "items": [{<entries.v1 row: id, ts, level, level_name, thread, logger,
                  message, body, user, ..., exception_type, signature,
                  template, variables, source_path, source_line, raw, ...>}, ...]}
    "variables" lists the values that masking replaced in the template, in
    order (issue keys, numbers, ids ...). Entries are ordered by time.
    SIGNATURE may be a unique prefix of at least 4 hex characters (resolved
    via the groups table; "signature" echoes the full one); a prefix that
    matches several groups is error "ambiguous" listing up to 5 "candidates".
    "raw" and "body" are cut to --max-raw characters (default 4000 in json/jsonl,
    no limit in table and --raw mode); a cut text ends with
    "… [truncated: N chars total, use --max-raw 0]" and its row gets
    "body_truncated": true and "raw_chars": <original length of raw> (both keys
    only on cut rows).
    With --raw the original log text of each entry is printed instead (any
    --output mode), entries separated by a blank line (or, when colour is on,
    highlighted and separated by a source:line rule).
    --columns keeps only the named entry columns (as `entries --columns`).
    --brief is the preset --columns id,ts,level_name,thread,user,request_id,
    logger,message,exception_type,signature,variables and also cuts "group"
    to signature,template,level_name,logger,exception_type,count,first_seen,
    last_seen; --brief with --columns is a usage error.

    Exit codes: 0 ok; 1 bad option, unknown column, --brief with --columns, or
    ambiguous signature prefix; 2 no entries with this signature; 3 unknown set
    or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        signature: Group signature (from `groups`) or a unique prefix of it (4+ hex chars).
        limit: Maximum number of entries; 0 = all.
        raw: Print the raw log text of the entries (highlighted when colour is on).
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table and --raw mode.
        columns: Entry columns to return: comma-separated (ts,message) and/or repeated.
        brief: Only id, ts, level_name, thread, user, request_id, logger, message,
            exception_type, signature, variables; "group" in its --brief form.

    """
    page = Page.parse(limit, front="cli")
    cap = params.cap(max_raw, text=raw)
    sel = Select(columns=columns, brief=brief)
    shown = sel.resolve(ENTRY_FIELDS, DETAIL_BRIEF, front="cli")
    out = ctx.out()
    if raw:
        ctx.local_only("--raw")
        pairs = queries.show_raw(
            ctx.store(), logset, signature, limit=page.limit, cap=cap, front="cli"
        )
        out.raw_entries(pairs)
        return 0
    doc = ctx.remote_or(
        "show",
        show,
        locals(),
        lambda: queries.show(
            ctx.store(), logset, signature, limit=page.limit, sel=sel, cap=cap, front="cli"
        ),
    )
    out.document(
        doc,
        columns=shown or render.ENTRY_COLUMNS,
        title=f"entries of {doc['signature']}",
        table=lambda console: render.show(console, doc, shown, color=out.color),
    )
    return 0


def entries(
    logset: str,
    /,
    *,
    q: Annotated[EntryQuery, Parameter(name="*")] = params.ALL_ENTRIES,
    columns: list[str] | None = None,
    brief: bool = False,
    limit: int = 100,
    offset: int = 0,
    raw: bool = False,
    max_raw: int | None = None,
    window: Literal["overlap", "union"] | None = None,
) -> None:
    """List entries of a log set (or of all nodes of a capture), filtered, ordered by time.

    Output (json):
      {"schema": "entries.v1", "logset": id, "count": int, "total": int,
       "offset": int, "next_offset": int | null, "truncated": bool,
       "items": [<entries.v1 row>, ...]}
    "total" counts the matching entries before --offset/--limit; "offset"
    echoes --offset; "truncated" is true when more entries follow this page
    and "next_offset" is where it starts (offset + count; null on the last
    page). --coarse-signature takes the signature of `groups --coarse`. With
    --raw the raw log text of the entries is printed instead (as `show --raw`;
    --offset/--limit apply).
    Rows hold all entries.v1 columns (`ragnalog schema entries`; ts is UTC,
    level numeric) unless --columns is given:
      <ENTRIES_COLUMNS>
    --columns takes comma-separated names and/or is repeated
    (--columns ts,level_name --columns message); an unknown name is error
    "invalid_value" whose "hint" lists the valid columns. --brief is the
    preset --columns id,ts,level_name,thread,user,request_id,logger,message,
    exception_type,signature; --brief with --columns is a usage error.
    --with-exception keeps entries with an exception (exception_type not
    null) at any level; --exception TEXT matches a case-insensitive substring
    of exception_type (the outermost exception).
    "raw" and "body" are cut to --max-raw characters (default 4000 in
    json/jsonl, no limit in table and --raw mode); a cut text ends with
    "… [truncated: N chars total, use --max-raw 0]" and its row gets
    "body_truncated": true and "raw_chars": <original length of raw> (of body
    when raw is not among --columns); both keys appear only on cut rows.
    A capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) gives the entries of all
    its nodes interleaved by time (then node): "logset" is capture:ID and each
    row adds "node" and "set" (the node's set id). They are taken from the
    capture's window: --since/--until, else --window (overlap, the time all
    nodes have logs for, by default; union); --signature is resolved in any
    node's groups and may name several, comma-separated (entries of any of
    them), each also NODE:SIGNATURE (that group on that node only: both
    sides of a co-burst); --node N1,N2 keeps the entries of those nodes (the window stays
    that of all nodes); --columns may name node and set. With --raw and
    colour on, each entry's rule names its node.

    Exit codes: 0 ok (also when nothing matches); 1 bad option, unknown
    column, --brief with --columns, --window, --node, several signatures or
    NODE:SIGNATURE with a set, a node not in the capture, or ambiguous
    --signature prefix;
    2 unknown capture or no such group in the capture; 3 unknown set or
    store error.

    Args:
        logset: Log set id, unique id prefix or name; or a capture (capture:ID,
            cluster:NAME[@YYYY-MM-DD]).
        q: Entry filters (the options --signature to --exception, see EntryQuery).
        columns: Columns to return: comma-separated (ts,level_name) and/or repeated.
        brief: Only id, ts, level_name, thread, user, request_id, logger, message,
            exception_type, signature.
        limit: Maximum number of rows; 0 = all.
        offset: Skip this many entries first (paging: next offset = offset + count).
        raw: Print the raw log text of the entries (highlighted when colour is on).
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table and --raw mode.
        window: overlap or union (a capture only; default overlap, unless --since/--until).

    """
    page = Page.parse(limit, offset, front="cli")
    sel = Select(columns=columns, brief=brief)
    cap = params.cap(max_raw, text=raw)
    if raw:
        ctx.local_only("--raw")
    if ctx.names_capture(logset):
        if ctx.remote():
            shown = sel.resolve((*ENTRY_FIELDS, *_capture_node_columns()), ENTRY_BRIEF, front="cli")
            doc = ctx.remote_or("entries-capture", entries, locals(), dict)
            ctx.out().document(doc, columns=shown or render.NODE_ENTRY_COLUMNS, title="entries")
            return
        _capture_entries(logset, q, page, sel, cap, raw=raw, window=window or "overlap")
        return
    shown = sel.resolve(ENTRY_FIELDS, ENTRY_BRIEF, front="cli")
    if window is not None:
        raise OpError(
            "usage",
            "--window applies to a capture",
            hint="pass a capture (capture:ID, cluster:NAME), or --since/--until",
        )
    if raw:
        ctx.out().raw_entries(queries.raw_entries(ctx.store(), logset, q, page, cap, front="cli"))
        return
    doc = ctx.remote_or(
        "entries",
        entries,
        locals(),
        lambda: queries.entries(ctx.store(), logset, q, page, sel, cap, front="cli"),
    )
    ctx.out().document(
        doc, columns=shown or render.ENTRY_COLUMNS, title=f"entries of {doc['logset']}"
    )


def _capture_entries(
    ref: str,
    q: EntryQuery,
    page: Page,
    sel: Select,
    cap: TextCap,
    *,
    raw: bool,
    window: Literal["overlap", "union"],
) -> None:
    """``entries CAPTURE``: the merged entries of all nodes."""
    from ragnalog.service import capture_queries

    store, out = ctx.store(), ctx.out()
    shown = sel.resolve((*ENTRY_FIELDS, *capture_queries.NODE_COLUMNS), ENTRY_BRIEF, front="cli")
    if raw:
        out.raw_entries(
            capture_queries.raw_entries(store, ref, q, page, cap, window=window, front="cli")
        )
        return
    doc = capture_queries.entries(store, ref, q, page, sel, cap, window=window, front="cli")
    columns = shown if shown is None or "node" in shown else [*shown, "node"]
    out.document(
        doc, columns=columns or render.NODE_ENTRY_COLUMNS, title=f"entries of {doc['logset']}"
    )


def context(
    logset: str,
    entry_id: str,
    /,
    *,
    before: int = 5,
    after: int = 5,
    any_thread: bool = False,
    max_raw: int | None = None,
    columns: list[str] | None = None,
    brief: bool = False,
    all_nodes: bool = False,
    window: float = 30,
) -> int:
    """Show the entries immediately around one entry, in time order (same thread by default).

    Answers "what did this request/thread do just before and after?": the
    --before entries preceding ENTRY_ID and the --after entries following it
    in time order (ties in file order), restricted to the anchor's thread
    unless --any-thread. The anchor itself is included. Entry ids come from
    the "id" column of `entries`, `show` or a group's "examples".

    Output (json):
      {"schema": "entries.v1", "logset": id, "anchor": <entry id>,
       "thread": <anchor's thread> | null, "any_thread": bool, "count": int,
       "items": [<entries.v1 row>, ...]}
    "items" holds at most --before + 1 + --after rows, the anchor among them.
    "raw" and "body" are cut as in `entries` (--max-raw, default 4000 in
    json/jsonl). In jsonl mode one row per line; the anchor row has an extra
    "anchor": true. The table marks the anchor row with ▶.
    --columns keeps only the named columns (as `entries --columns`); --brief
    is the preset --columns id,ts,level_name,thread,user,request_id,logger,
    message,exception_type,signature,variables; --brief with --columns is a
    usage error.
    --all-nodes answers "what did every node do at that moment?": the entries
    of all nodes of the set's capture within --window seconds before and
    after the anchor (all threads; --before/--after/--any-thread do not
    apply), in time then node order, at most 2000 (the nearest; "truncated"
    and "total" say so); rows add "node" and "set", "any_thread" is true.

    Exit codes: 0 ok; 1 bad option, unknown column, --brief with --columns, or
    --all-nodes for a set in no capture; 2 no entry with this id; 3 unknown
    set or store error.

    Args:
        logset: Log set id, unique id prefix or name; with --all-nodes also a capture
            (capture:ID, cluster:NAME[@YYYY-MM-DD]): the node that has the entry.
        entry_id: Id of the anchor entry (16 hex chars, from `entries`/`show`).
        before: Entries to show before the anchor.
        after: Entries to show after the anchor.
        any_thread: Consider entries of all threads, not only the anchor's thread.
        max_raw: Characters kept of raw and body per entry; 0 = no limit. Default: 4000
            in json/jsonl mode, no limit in table mode.
        columns: Columns to return: comma-separated (ts,message) and/or repeated.
        brief: Only id, ts, level_name, thread, user, request_id, logger, message,
            exception_type, signature, variables.
        all_nodes: What every node logged around the anchor (the set's capture).
        window: With --all-nodes: seconds before and after the anchor.

    """
    cap = params.cap(max_raw, text=False)
    sel = Select(columns=columns, brief=brief)
    out = ctx.out()
    if not window >= 0:  # checked with or without --all-nodes, as documented
        raise OpError("invalid_value", "--window must be >= 0 (seconds)")
    if all_nodes:
        from ragnalog.service import capture_queries

        valid = (*ENTRY_FIELDS, *capture_queries.NODE_COLUMNS)
        shown = sel.resolve(valid, DETAIL_BRIEF, front="cli")
        if ctx.remote():
            doc = ctx.remote_or("context", context, locals(), dict)
            at = _anchor(doc, entry_id)
        else:
            doc, at = capture_queries.context(
                ctx.store(), logset, entry_id, window_s=window, sel=sel, cap=cap, front="cli"
            )
        cols = [*shown, "node"] if shown and "node" not in shown else shown
        out.document(
            doc,
            jsonl_rows=[
                {**r, "anchor": True} if i == at else r for i, r in enumerate(doc["items"])
            ],
            table=lambda console: render.context(
                console, doc, at, cols or render.NODE_ENTRY_COLUMNS, color=out.color
            ),
        )
        return 0
    shown = sel.resolve(ENTRY_FIELDS, DETAIL_BRIEF, front="cli")
    if ctx.remote():
        doc = ctx.remote_or("context", context, locals(), dict)
        at = _anchor(doc, entry_id)
    else:
        doc, at = queries.context(
            ctx.store(),
            logset,
            entry_id,
            before=before,
            after=after,
            any_thread=any_thread,
            sel=sel,
            cap=cap,
            front="cli",
        )
    out.document(
        doc,
        jsonl_rows=[{**r, "anchor": True} if i == at else r for i, r in enumerate(doc["items"])],
        table=lambda console: render.context(console, doc, at, shown, color=out.color),
    )
    return 0


def frames(
    logset: str,
    ref: str,
    /,
    *,
    limit: int = 10,
    all_: Annotated[bool, Parameter(name="--all")] = False,
) -> int:
    """Show the exception chain of one entry with its application frames first.

    REF is an entry id or a group signature (16 hex chars; entry ids are tried
    first), or a unique signature prefix of at least 4 hex characters. For a
    signature the group's first example entry is used. Each
    exception of the chain (outermost first, then each "Caused by:") lists its
    first --limit application frames: frames whose class is outside the
    library packages (JDK, Groovy, Jira platform: com.atlassian., org.ofbiz.,
    webwork., org.apache., com.google., JDBC drivers, ...), so plugin code
    stands out. With the environment variable RAGNALOG_APP_PACKAGES set
    (comma-separated prefixes, e.g. com.acme.,org.example.) an application
    frame is one that starts with one of those prefixes instead.

    Output (json):
      {"schema": "frames.v1", "logset": id, "entry": <entry id>,
       "signature": str, "ts": str, "level_name": str, "logger": str,
       "message": str, "app_packages": [str] | null, "count": int,
       "items": [{"type", "message",
                  "app_frames": [{"location", "file", "line"}, ...],
                  "app_frames_total": int, "frames_total": int,
                  "omitted_frames": int,
                  "frames": [{"location", "file", "line", "app": bool}]}]}
    "items" is the chain (count = its length; empty when the entry has no
    parsed exception). "app_frames" holds at most --limit frames and
    "app_frames_total" counts all of them; "frames_total" is the number of
    frames printed in the log, "omitted_frames" the "... N more" count.
    "frames" (every frame with an "app" flag) and all application frames are
    returned only with --all.
    "app_packages" echoes RAGNALOG_APP_PACKAGES. In jsonl mode one exception
    per line; the table prints each exception and its frames as text.

    Exit codes: 0 ok (also when the entry has no exception); 1 bad option or
    ambiguous signature prefix (error "ambiguous" with "candidates"); 2 no
    entry or group REF; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        ref: Entry id, or group signature or unique prefix (its first example entry is used).
        limit: Application frames per exception; 0 = all.
        all_: Return every frame (with an "app" flag) and all application frames.

    """
    page = Page.parse(limit, front="cli")
    doc = ctx.remote_or(
        "frames",
        frames,
        locals(),
        lambda: queries.frames(
            ctx.store(),
            logset,
            ref,
            limit=page.limit,
            all_frames=all_,
            app_packages=ctx.current().settings.app_packages,
            front="cli",
        ),
    )
    out = ctx.out()
    if not doc["items"]:
        out.info(f"entry {doc['entry']} has no parsed exception")
    out.document(doc, table=lambda console: render.frames(console, doc, all_frames=all_))
    return 0


def query(logset: str, sql: str, /, *, baseline: str | None = None) -> None:
    """Run DuckDB SQL over a log set (or all nodes of a capture) and print the result rows.

    Views: "entries" (entries.v1), "groups" (groups.v1) and "merges" (merges.v1,
    only when present). Their columns (types and descriptions: `ragnalog
    schema entries|groups|merges`):
      entries:
        <ENTRIES_COLUMNS>
      groups:
        <GROUPS_COLUMNS>
      merges:
        <MERGES_COLUMNS>
    level is numeric (TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60),
    ts/first_seen/last_seen are UTC timestamps, variables a list of strings.
    The session time zone is UTC whatever the machine's zone: result
    timestamps are UTC (as in `entries`) and date_trunc('day', ts) buckets
    at UTC midnight. One SELECT statement only (also WITH, FROM, VALUES,
    DESCRIBE, SUMMARIZE; several statements, or COPY, EXPLAIN, CREATE, SET and
    the like, are a usage error).

    A capture (capture:ID, cluster:NAME[@YYYY-MM-DD], or a bare capture id)
    gives views over all its nodes: the rows of every member set, each
    with leading columns "node" and "set" (the member's set id); merges
    holds the members that have one; "logset" is capture:ID. Signatures
    compare across nodes, as the members share their recipe.
    --baseline adds another set's views (a capture's: another capture, or a
    set, which stands for its capture) as baseline_entries, baseline_groups
    and baseline_merges, for comparing in one query; "baseline" is its id.

    SQL examples:
      select level_name, count(*) n from entries group by 1 order by 2 desc
      select unnest(variables) v, count(*) n from entries
        where signature = '0123456789abcdef' group by 1 order by 2 desc
      select signature, count, template from groups where exception_type is not null
        order by count desc limit 20
      select node, count(*) n from entries where level >= 50 group by 1   (a capture)
      select signature, count, template from groups where signature not in
        (select signature from baseline_groups) order by count desc   (--baseline)

    Output (json):
      {"schema": "query.v1", "logset": id, "columns": [str, ...], "count": int,
       "total": int | null, "truncated": false, "items": [{<column>: value, ...}, ...]}
      (with --baseline also "baseline": id)
    A local run never truncates the result ("total" = "count"); with --server the
    server cuts it at 10,000 rows ("truncated": true, "total": null, and a warning on
    stderr in every output mode): page with LIMIT/OFFSET.

    Errors: "sql_error"; for an unknown column its "hint" lists DuckDB's
    candidate columns (or the columns of the views), for an unknown table the
    available views.

    Exit codes: 0 ok (also for an empty result); 1 SQL error; 2 unknown capture;
    3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name; or a capture (capture:ID,
            cluster:NAME[@YYYY-MM-DD]).
        sql: The SQL query (quote it as one argument).
        baseline: Another set (with a capture: another capture, or a set for its capture)
            whose views the SQL reads as baseline_entries, baseline_groups, baseline_merges.

    """
    if ctx.names_capture(logset):
        from ragnalog.service import capture_queries

        doc = ctx.remote_or(
            "query-capture",
            query,
            locals(),
            lambda: capture_queries.query(ctx.store(), logset, sql, baseline=baseline, front="cli"),
        )
    else:
        doc = ctx.remote_or(
            "query",
            query,
            locals(),
            lambda: queries.query(ctx.store(), logset, sql, baseline=baseline, front="cli"),
        )
    if doc.get("truncated"):  # the server's row cap (also visible in jsonl and table modes)
        ctx.out().warn(
            f"the server cut the result at {doc.get('count', 0):,} rows: page with LIMIT/OFFSET"
        )
    ctx.out().document(doc, title="query result")


def stats(logset: str, /) -> None:
    """Print summary statistics of a log set.

    Counts per level, per logger (top N) and per hour, the time range and the
    sources, as computed by the store.

    Output (json): {"schema": "stats.v1", "logset": id, <store statistics...>}
    Table mode shows the hours as one row per day with a sparkline of the
    hourly counts; JSON keeps the full "hours" list.

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    doc = ctx.remote_or(
        "stats", stats, locals(), lambda: queries.stats(ctx.store(), logset, front="cli")
    )
    out = ctx.out()
    out.document(
        doc,
        items=None,
        title=f"stats {doc['logset']}",
        table=lambda console: render.stats(console, doc),
    )


def users(
    logset: str,
    /,
    *,
    q: Annotated[EntryQuery, Parameter(name="*")] = params.ALL_ENTRIES,
    limit: int = 50,
    offset: int = 0,
    top: int = 3,
) -> None:
    """Count the entries of a log set per user: who logs what, most entries first.

    Takes the filters of `entries` (--signature: the users of one group;
    --since/--until: a window; --level, --logger, --exception …), so it counts
    what the listing shows. Each user row has the entries, how many are
    ERROR/FATAL and WARN, the distinct groups and threads, the first and last
    timestamp and the user's largest groups (--top, default 3; one user's groups:
    --user NAME --top 50).

    Output (json):
      {"schema": "users.v1", "logset": id, "count": int, "total": int,
       "offset": int, "next_offset": int | null, "truncated": bool,
       "entries": int, "without_user": int,
       "items": [{"user", "entries", "errors", "warnings", "groups", "threads",
                  "first_ts", "last_ts", "top": [{"signature", "count"}]}]}
    "total" counts the users, "entries" the matching entries with a user and
    "without_user" those without one. Table mode prints one row per user.

    Exit codes: 0 ok; 1 bad option, level, time or --top (1-1000), or an
    ambiguous signature prefix; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        q: Entry filters (the options --signature to --until, see EntryQuery).
        limit: Users per page (0 = all).
        offset: Users to skip.
        top: Largest groups listed per user (1-1000).

    """
    page = Page.parse(limit, offset, front="cli")
    doc = ctx.remote_or(
        "users",
        users,
        locals(),
        lambda: queries.users(ctx.store(), logset, q, page, top=top, front="cli"),
    )
    out = ctx.out()
    out.document(doc, table=lambda console: render.users(console, doc))


def _anchor(doc: dict[str, Any], entry_id: str) -> int:
    """Row index of a context document's anchor (remote mode: the API names it, not its row)."""
    anchor = str(doc.get("anchor") or entry_id)
    return next(
        (i for i, r in enumerate(doc.get("items") or []) if str(r.get("id", "")) == anchor), -1
    )


def _capture_node_columns() -> tuple[str, ...]:
    from ragnalog.service.capture_queries import NODE_COLUMNS

    return tuple(NODE_COLUMNS)


MERGE_FIELDS: tuple[str, ...] = tuple(MERGES_SCHEMA.names)
"""Columns of merges.v1."""

params.fill_doc(entries, ENTRIES_COLUMNS=ENTRY_FIELDS)
params.fill_doc(
    query, ENTRIES_COLUMNS=ENTRY_FIELDS, GROUPS_COLUMNS=GROUP_FIELDS, MERGES_COLUMNS=MERGE_FIELDS
)
