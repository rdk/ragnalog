"""Typed JSON documents: one msgspec Struct per `{"schema": "<name>.v1", ...}` envelope.

These are the documents of `ragnalog --output json` and of the web API
(`/api`). The JSON Schemas in `docs/formats/` (`ragnalog schema NAME.v1`),
the OpenAPI components of the web app and the document list of `manifest.v1`
are generated from the Structs here (:func:`document_schema`, :func:`components`).

Rows (`items` of `groups.v1` and `entries.v1`, the diff and triage lists)
stay plain dicts: `--columns` and `--brief` make them partial. Their JSON
Schemas come from the Arrow schemas in :mod:`ragnalog.store.schema`;
every row property is optional.

A key that only some commands, modes or front ends add is optional (`UNSET`
when absent); `T | None` means the key is always there and may be `null`.
Unknown keys are ignored when decoding, so a reader keeps working when a v1
document gains keys.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, get_args

import msgspec
from msgspec import UNSET, Meta, Struct, UnsetType

from ragnalog.store.schema import (
    ENTRIES_SCHEMA,
    GROUPS_SCHEMA,
    LOGSET_FIELDS,
    MERGES_SCHEMA,
)

__all__ = [
    "DOCUMENTS",
    "ROW_COMPONENTS",
    "Document",
    "components",
    "convert",
    "decode",
    "document_names",
    "document_schema",
    "row_schemas",
]


def _d(text: str) -> Meta:
    return Meta(description=text)


_LOGSET: Final = {name: text for name, _, _, text in LOGSET_FIELDS}
_OLDER: Final = " Missing only from older tool versions."


# --- rows: plain dicts, schema from store/schema.py (the "x-rows" marker is replaced)

_ROW = "x-rows"
EntryRow = Annotated[dict[str, Any], Meta(extra_json_schema={_ROW: "entries"})]
GroupRow = Annotated[dict[str, Any], Meta(extra_json_schema={_ROW: "groups"})]
MergeRow = Annotated[dict[str, Any], Meta(extra_json_schema={_ROW: "merges"})]
DiffRow = Annotated[dict[str, Any], Meta(extra_json_schema={_ROW: "diff"})]

ROW_COMPONENTS: Final = {
    "entries": "EntryRow",
    "groups": "GroupRow",
    "merges": "MergeRow",
    "diff": "DiffRow",
}
"""Component name of each row schema (`$defs` / OpenAPI `components.schemas`)."""


# --- shared parts


class Source(Struct, kw_only=True):
    """One ingested file of a log set."""

    path: Annotated[str, _d("Path of the file as given at ingest.")]
    size: Annotated[int, _d("Size in bytes.")]
    xxh3: Annotated[str, _d("xxh3-64 hex digest of the content.")]
    first_ts: Annotated[str | None, _d("ISO 8601 time of the file's earliest entry.")]
    last_ts: Annotated[str | None, _d("ISO 8601 time of the file's latest entry.")]
    entries: Annotated[int, _d("Entries of the file kept after deduplication.")]


class SetSummary(Struct, kw_only=True):
    """Entry counts by severity of a set, from its groups table."""

    errors: Annotated[int, _d("Entries at ERROR or FATAL.")]
    warnings: Annotated[int, _d("Entries at WARN.")]
    error_groups: Annotated[int, _d("Groups at ERROR or FATAL.")]


class IngestInfo(Struct, kw_only=True):
    """What one ingest did (`ragnalog ingest`, `job.v1` `result`)."""

    parsed_entries: Annotated[int, _d("Entries parsed from the files.")]
    parse_errors: Annotated[int, _d("Entries whose header was not recognised.")]
    elapsed_s: Annotated[float, _d("Seconds the ingest took.")]
    existing: Annotated[bool, _d("The set existed already and was reused.")]
    skipped: Annotated[list[str], _d("Files found in a directory that are not in the format.")]
    warnings: Annotated[list[str], _d("E.g. that --name was ignored for an existing set.")]


class ErrorInfo(Struct, kw_only=True):
    """The `error` object of `error.v1` (also `job.v1` `error`)."""

    code: Annotated[str, _d("Stable error code, e.g. usage, invalid_value, not_found.")]
    message: Annotated[str, _d("What went wrong.")]
    hint: Annotated[str, _d("Suggested next step.")] | UnsetType = UNSET
    reason: (
        Annotated[
            str,
            _d(
                "The same in the CLI and the API where their codes differ, e.g. set_not_found "
                "(CLI store_error, API not_found); see service/errors.py REASONS."
            ),
        ]
        | UnsetType
    ) = UNSET
    store: Annotated[str, _d("The data directory searched (CLI, unknown set).")] | UnsetType = UNSET
    candidates: (
        Annotated[list[str], _d("Matching signatures of an ambiguous prefix (up to 5).")]
        | UnsetType
    ) = UNSET


class Frame(Struct, kw_only=True):
    """One stack frame."""

    location: Annotated[str, _d("Class and method, e.g. com.acme.Foo.bar.")]
    file: Annotated[str | None, _d("Source file, or Native Method / Unknown Source.")]
    line: Annotated[int | None, _d("Line number.")]
    app: Annotated[bool, _d("Application frame (only with --all).")] | UnsetType = UNSET


class ChainException(Struct, kw_only=True):
    """One exception of a chain, outermost first."""

    type: Annotated[str, _d("Exception class.")]
    message: Annotated[str | None, _d("Exception message.")]
    app_frames: Annotated[list[Frame], _d("Application frames, at most --limit.")]
    app_frames_total: Annotated[int, _d("All application frames.")]
    frames_total: Annotated[int, _d("Frames printed in the log.")]
    omitted_frames: Annotated[int, _d('The "... N more" count.')]
    frames: Annotated[list[Frame], _d("Every frame with its app flag (--all).")] | UnsetType = UNSET


class BucketCount(Struct, kw_only=True):
    """Entries in one time bucket."""

    bucket: Annotated[str, _d("Bucket start, ISO 8601 UTC.")]
    count: Annotated[int, _d("Entries in the bucket.")]


# --- log sets


class Logset(Struct, kw_only=True, tag_field="schema", tag="logset.v1"):
    """A log set's manifest: `ingest`, `set`, `regroup`, `GET /api/sets/{ref}`."""

    id: Annotated[str, _d(_LOGSET["id"])]
    name: Annotated[str | None, _d(_LOGSET["name"])]
    format: Annotated[str, _d(_LOGSET["format"])]
    sources: Annotated[list[Source], _d(_LOGSET["sources"])]
    first_ts: Annotated[str | None, _d(_LOGSET["first_ts"])]
    last_ts: Annotated[str | None, _d(_LOGSET["last_ts"])]
    entry_count: Annotated[int, _d(_LOGSET["entry_count"])]
    group_count: Annotated[int, _d(_LOGSET["group_count"])]
    created_at: Annotated[str, _d(_LOGSET["created_at"])]
    tool_version: Annotated[str, _d(_LOGSET["tool_version"])]
    schema_version: Annotated[str, _d(_LOGSET["schema_version"])]
    signature_version: Annotated[int, _d(_LOGSET["signature_version"])]
    mask_rules_version: Annotated[int, _d(_LOGSET["mask_rules_version"])]
    signature_spec: Annotated[str, _d(_LOGSET["signature_spec"])]
    recipe_key: (
        Annotated[str, _d(_LOGSET["recipe_key"] + " Missing only from older tool versions.")]
        | UnsetType
    ) = UNSET
    node: Annotated[str | None, _d(_LOGSET["node"] + _OLDER)] | UnsetType = UNSET
    cluster: Annotated[str | None, _d(_LOGSET["cluster"] + _OLDER)] | UnsetType = UNSET
    capture: Annotated[str | None, _d(_LOGSET["capture"] + _OLDER)] | UnsetType = UNSET
    ingest: Annotated[IngestInfo, _d("Only from ingest (CLI and job result).")] | UnsetType = UNSET
    merges: Annotated[list[MergeRow], _d("Drain3 merge proposals (ingest --mine).")] | (
        UnsetType
    ) = UNSET
    summary: Annotated[SetSummary, _d("Only from set and GET /api/sets/{ref}.")] | UnsetType = UNSET


class SetItem(Struct, kw_only=True):
    """One set of `sets.v1`: its manifest without `schema` plus `summary` and `stale`."""

    id: Annotated[str, _d(_LOGSET["id"])]
    name: Annotated[str | None, _d(_LOGSET["name"])]
    first_ts: Annotated[str | None, _d(_LOGSET["first_ts"])]
    last_ts: Annotated[str | None, _d(_LOGSET["last_ts"])]
    entry_count: Annotated[int, _d(_LOGSET["entry_count"])]
    group_count: Annotated[int, _d(_LOGSET["group_count"])]
    summary: Annotated[SetSummary | None, _d("null when the set cannot be read.")]
    format: Annotated[str, _d("Not with --brief.")] | UnsetType = UNSET
    sources: Annotated[list[Source], _d("Not with --brief.")] | UnsetType = UNSET
    created_at: Annotated[str, _d("Not with --brief.")] | UnsetType = UNSET
    tool_version: Annotated[str, _d("Not with --brief.")] | UnsetType = UNSET
    schema_version: Annotated[str, _d("Not with --brief.")] | UnsetType = UNSET
    signature_version: Annotated[int, _d("Not with --brief.")] | UnsetType = UNSET
    mask_rules_version: Annotated[int, _d("Not with --brief.")] | UnsetType = UNSET
    signature_spec: Annotated[str, _d("Not with --brief.")] | UnsetType = UNSET
    recipe_key: Annotated[str, _d(_LOGSET["recipe_key"] + " Not with --brief.")] | (
        UnsetType
    ) = UNSET
    stale: (
        Annotated[
            bool,
            _d(
                "recipe_key differs from the recipe this version signs the set's format and "
                "spec with: not comparable with new sets, no triage baseline; rebuild with "
                "upgrade. Missing only from older tool versions."
            ),
        ]
        | UnsetType
    ) = UNSET
    node: Annotated[str | None, _d(_LOGSET["node"] + _OLDER)] | UnsetType = UNSET
    cluster: Annotated[str | None, _d(_LOGSET["cluster"] + " Not with --brief.")] | (
        UnsetType
    ) = UNSET
    capture: Annotated[str | None, _d(_LOGSET["capture"] + _OLDER)] | UnsetType = UNSET


class Sets(Struct, kw_only=True, tag_field="schema", tag="sets.v1"):
    """The log sets of the data directory, newest first: `sets`, `GET /api/sets`."""

    store: Annotated[str, _d("CLI: the data directory path; API: the data directory's name.")]
    count: Annotated[int, _d("Number of items.")]
    items: Annotated[
        list[SetItem],
        _d(
            "The sets; with --brief only id, name, first_ts, last_ts, counts, summary, stale, "
            "node, capture."
        ),
    ]


class Remove(Struct, kw_only=True, tag_field="schema", tag="remove.v1"):
    """A removed set: `remove`, `DELETE /api/sets/{ref}`."""

    removed: Annotated[str, _d("Id of the removed set.")]
    name: Annotated[str | None, _d("Its name.")]


class UpgradeItem(Struct, kw_only=True):
    """One set of `upgrade.v1`: its manifest (after the call) plus the outcome."""

    id: Annotated[str, _d(_LOGSET["id"])]
    name: Annotated[str | None, _d(_LOGSET["name"])]
    format: Annotated[str, _d(_LOGSET["format"])]
    sources: Annotated[list[Source], _d(_LOGSET["sources"])]
    first_ts: Annotated[str | None, _d(_LOGSET["first_ts"])]
    last_ts: Annotated[str | None, _d(_LOGSET["last_ts"])]
    entry_count: Annotated[int, _d(_LOGSET["entry_count"])]
    group_count: Annotated[int, _d(_LOGSET["group_count"])]
    created_at: Annotated[str, _d(_LOGSET["created_at"])]
    tool_version: Annotated[str, _d(_LOGSET["tool_version"])]
    schema_version: Annotated[str, _d(_LOGSET["schema_version"])]
    signature_version: Annotated[int, _d(_LOGSET["signature_version"])]
    mask_rules_version: Annotated[int, _d(_LOGSET["mask_rules_version"])]
    signature_spec: Annotated[str, _d(_LOGSET["signature_spec"])]
    recipe_key: Annotated[str, _d("Recipe key after the call.")]
    node: Annotated[str | None, _d(_LOGSET["node"] + _OLDER)] | UnsetType = UNSET
    cluster: Annotated[str | None, _d(_LOGSET["cluster"] + _OLDER)] | UnsetType = UNSET
    capture: Annotated[str | None, _d(_LOGSET["capture"] + _OLDER)] | UnsetType = UNSET
    status: Annotated[Literal["upgraded", "skipped"], _d("What happened to the set.")]
    reason: Annotated[
        str | None,
        _d('null when upgraded; "current" or the unknown format/spec when skipped.'),
    ]
    previous_recipe_key: Annotated[str, _d("Recipe key before the call.")]
    renamed_sources: (
        Annotated[
            int,
            _d(
                "Source paths renamed from a removed ZIP spool (sets ingested from a ZIP by "
                "older versions) to <zip>/<member>; only when some were."
            ),
        ]
        | UnsetType
    ) = UNSET


class Upgrade(Struct, kw_only=True, tag_field="schema", tag="upgrade.v1"):
    """Stale sets rebuilt at the current recipe: `upgrade [SET...] [--all]`."""

    count: Annotated[int, _d("Number of items.")]
    upgraded: Annotated[int, _d("Items with status upgraded.")]
    items: Annotated[list[UpgradeItem], _d("One per set, in the order of the SET arguments.")]


# --- captures


class CaptureNode(Struct, kw_only=True):
    """One member set of a capture (service.captures.CaptureNodeRef)."""

    node: Annotated[str, _d("Node id of the set (its name or id when it has none).")]
    id: Annotated[str, _d("Set id.")]
    name: Annotated[str | None, _d("Set name.")]
    first_ts: Annotated[str | None, _d("First entry, as in the manifest. Not with --brief.")] | (
        UnsetType
    ) = UNSET
    last_ts: Annotated[str | None, _d("Last entry, as in the manifest. Not with --brief.")] | (
        UnsetType
    ) = UNSET
    entry_count: Annotated[int, _d("Entries of the set. Not with --brief.")] | UnsetType = UNSET


class CaptureItem(Struct, kw_only=True):
    """A capture: the sets collected together (service.captures.CaptureInfo)."""

    capture: Annotated[str, _d("Capture id: an export's jobId or a hash of the set ids.")]
    cluster: Annotated[str | None, _d("Cluster of the member sets.")]
    first_ts: Annotated[str | None, _d("Earliest first entry of the members.")]
    last_ts: Annotated[str | None, _d("Latest last entry of the members.")]
    entry_count: Annotated[int, _d("Entries of all members.")]
    nodes: Annotated[list[CaptureNode], _d("Member sets, sorted by node.")]


class Captures(Struct, kw_only=True, tag_field="schema", tag="captures.v1"):
    """The captures of the data directory, newest last_ts first: `captures`, `GET /api/captures`."""

    count: Annotated[int, _d("Number of items.")]
    items: Annotated[
        list[CaptureItem], _d("The captures; with --brief only node, id, name per node.")
    ]


class IngestCapture(Struct, kw_only=True, tag_field="schema", tag="ingest-capture.v1"):
    """A split ingest, one set per node: `ingest` (export ZIP, --split), the job capture."""

    capture: Annotated[CaptureItem, _d("The capture after the ingest (all its member sets).")]
    sets: Annotated[list[Logset], _d("One logset.v1 with ingest per node of this ingest.")]
    warnings: Annotated[
        list[str],
        _d("job.json and split notes, then each set's ingest.warnings prefixed with its node."),
    ]


class LoggerCount(Struct, kw_only=True):
    """Entries of one logger."""

    logger: Annotated[str | None, _d("Logger name.")]
    count: Annotated[int, _d("Entries.")]


class HourCount(Struct, kw_only=True):
    """Entries in one hour."""

    hour: Annotated[str, _d("Hour start, ISO 8601 UTC.")]
    count: Annotated[int, _d("Entries.")]


class TimeRange(Struct, kw_only=True):
    """Time of the first and the last entry."""

    first: Annotated[str | None, _d("First entry, ISO 8601 UTC.")]
    last: Annotated[str | None, _d("Last entry, ISO 8601 UTC.")]


class Stats(Struct, kw_only=True, tag_field="schema", tag="stats.v1"):
    """Summary statistics of a set: `stats`, `GET /api/sets/{ref}/stats`."""

    logset: Annotated[str, _d("Set id.")]
    id: Annotated[str, _d("Set id.")]
    name: Annotated[str | None, _d("Set name.")]
    format: Annotated[str, _d("Format name.")]
    entry_count: Annotated[int, _d("Entries.")]
    group_count: Annotated[int, _d("Groups.")]
    parse_errors: Annotated[int, _d("Entries whose header was not recognised.")]
    levels: Annotated[dict[str, int], _d("Entries per level name.")]
    loggers: Annotated[list[LoggerCount], _d("Top 20 loggers.")]
    hours: Annotated[list[HourCount], _d("Entries per hour (UTC).")]
    time_range: Annotated[TimeRange, _d("Time of the first and the last entry.")]
    sources: Annotated[list[Source], _d("The ingested files.")]


# --- groups and entries


class Groups(Struct, kw_only=True, tag_field="schema", tag="groups.v1"):
    """One page of groups: `groups`, `GET /api/sets/{ref}/groups`."""

    logset: Annotated[str, _d("Set id.")]
    count: Annotated[int, _d("Rows in items.")]
    total: Annotated[int, _d("Matching groups before offset/limit.")]
    offset: Annotated[int, _d("Echo of --offset.")]
    truncated: Annotated[bool, _d("More rows follow (offset + count < total).")]
    next_offset: Annotated[int | None, _d("Where the next page starts; null on the last.")]
    items: Annotated[list[GroupRow], _d("groups.v1 rows (all columns unless --columns).")]


class Group(Struct, kw_only=True, tag_field="schema", tag="group.v1"):
    """One group with its histogram and first entries: `GET /api/sets/{ref}/groups/{sig}`."""

    logset: Annotated[str, _d("Set id.")]
    group: Annotated[GroupRow, _d("The groups.v1 row.")]
    histogram: Annotated[list[BucketCount], _d("Entries per hour; [] for coarse groups.")]
    count: Annotated[int, _d("Rows in items.")]
    items: Annotated[list[EntryRow], _d("The group's first entries, by time.")]


class Entries(Struct, kw_only=True, tag_field="schema", tag="entries.v1"):
    """Entries: `entries` (a page), `show` (one group), `context` (around an entry)."""

    logset: Annotated[
        str, _d("Set id; capture:<id> for the merged entries of a capture (rows add node, set).")
    ]
    count: Annotated[int, _d("Rows in items.")]
    items: Annotated[list[EntryRow], _d("entries.v1 rows (all columns unless --columns).")]
    total: Annotated[int, _d("entries: matches before offset/limit.")] | UnsetType = UNSET
    offset: Annotated[int, _d("entries: echo of --offset.")] | UnsetType = UNSET
    truncated: Annotated[bool, _d("entries: more rows follow.")] | UnsetType = UNSET
    next_offset: (
        Annotated[int | None, _d("entries: where the next page starts; null on the last.")]
        | UnsetType
    ) = UNSET
    signature: Annotated[str, _d("show: the full signature.")] | UnsetType = UNSET
    group: Annotated[GroupRow | None, _d("show: the groups.v1 row.")] | UnsetType = UNSET
    anchor: Annotated[str, _d("context: the anchor entry id.")] | UnsetType = UNSET
    thread: Annotated[str | None, _d("context: the anchor's thread.")] | UnsetType = UNSET
    any_thread: Annotated[bool, _d("context: echo of --any-thread.")] | UnsetType = UNSET


class Frames(Struct, kw_only=True, tag_field="schema", tag="frames.v1"):
    """An entry's exception chain, application frames first: `frames`."""

    logset: Annotated[str, _d("Set id.")]
    entry: Annotated[str, _d("Entry id.")]
    signature: Annotated[str, _d("The entry's group.")]
    ts: Annotated[str | None, _d("Entry time, UTC.")]
    level_name: Annotated[str, _d("Entry level.")]
    logger: Annotated[str | None, _d("Entry logger.")]
    message: Annotated[str | None, _d("First message line.")]
    app_packages: Annotated[list[str] | None, _d("Echo of RAGNALOG_APP_PACKAGES.")]
    count: Annotated[int, _d("Exceptions in the chain.")]
    items: Annotated[list[ChainException], _d("The chain, outermost first.")]


class TimelineBucket(BucketCount, kw_only=True):
    """Entries in one time bucket of `timeline`, by level with --by-level."""

    levels: (
        Annotated[
            dict[str, int],
            _d(
                "With --by-level (by_level=true): the bucket's entries per chart series, "
                "ERROR (with FATAL), WARN, INFO, DEBUG (with TRACE) and OTHER (unknown level)."
            ),
        ]
        | UnsetType
    ) = UNSET


class Timeline(Struct, kw_only=True, tag_field="schema", tag="timeline.v1"):
    """Entry counts per time bucket: `GET /api/sets/{ref}/timeline`."""

    logset: Annotated[str, _d("Set id.")]
    bucket: Annotated[str, _d("Bucket size, e.g. 1h.")]
    count: Annotated[int, _d("Rows in items.")]
    items: Annotated[list[TimelineBucket], _d("Contiguous buckets.")]


class UserGroup(Struct, kw_only=True):
    """One of a user's largest groups."""

    signature: Annotated[str, _d("Group signature.")]
    count: Annotated[int, _d("The user's entries in the group.")]


class UserRow(Struct, kw_only=True):
    """One user: how many of the matching entries are theirs, and of what."""

    user: Annotated[str, _d("User name, as logged.")]
    entries: Annotated[int, _d("Matching entries of the user.")]
    errors: Annotated[int, _d("Of them ERROR or FATAL.")]
    warnings: Annotated[int, _d("Of them WARN.")]
    groups: Annotated[int, _d("Distinct signatures.")]
    threads: Annotated[int, _d("Distinct threads.")]
    first_ts: Annotated[str | None, _d("First entry with a timestamp.")]
    last_ts: Annotated[str | None, _d("Last entry with a timestamp.")]
    top: Annotated[
        list[UserGroup], _d("The user's largest groups (--top, default 3), most entries first.")
    ]


class Users(Struct, kw_only=True, tag_field="schema", tag="users.v1"):
    """Entries per user: `users`, `GET /api/sets/{ref}/users`."""

    logset: Annotated[str, _d("Set id.")]
    count: Annotated[int, _d("Rows in items.")]
    total: Annotated[int, _d("Users with matching entries.")]
    offset: Annotated[int, _d("Index of the first row.")]
    next_offset: Annotated[int | None, _d("offset of the next page; null on the last.")]
    truncated: Annotated[bool, _d("Whether more users follow.")]
    entries: Annotated[int, _d("Matching entries with a user.")]
    without_user: Annotated[int, _d("Matching entries without one (blank or missing).")]
    items: Annotated[list[UserRow], _d("Users, most entries first, ties by name.")]


class Query(Struct, kw_only=True, omit_defaults=True, tag_field="schema", tag="query.v1"):
    """Rows of a DuckDB query: `query`."""

    logset: Annotated[str, _d("Set id; capture:<id> for a query over all nodes of a capture.")]
    columns: Annotated[list[str], _d("Result columns, in order.")]
    count: Annotated[int, _d("Rows in items.")]
    total: Annotated[
        int | None, _d("Rows of the result: equals count; null when the API cut it (truncated).")
    ]
    truncated: Annotated[
        bool,
        _d("True when the API cut the result at its row cap (more rows follow); CLI: never."),
    ]
    items: Annotated[list[dict[str, Any]], _d("One object per result row.")]
    baseline: Annotated[
        str | None,
        _d(
            "With --baseline: its set id (capture:<id> for a capture), whose views are "
            "baseline_entries, baseline_groups (and baseline_merges)."
        ),
    ] = None


# --- comparison


class DiffTotals(Struct, kw_only=True):
    """Section sizes before --limit."""

    new: Annotated[int, _d("Groups only in B.")]
    vanished: Annotated[int, _d("Groups only in A.")]
    changed: Annotated[int, _d("Groups in both with another count.")]


class Diff(Struct, kw_only=True, tag_field="schema", tag="diff.v1"):
    """Groups of B compared with baseline A: `diff`, `GET /api/diff`."""

    a: Annotated[str, _d("Baseline set id.")]
    b: Annotated[str, _d("Compared set id.")]
    new: Annotated[list[DiffRow], _d("Only in B, by count_b.")]
    vanished: Annotated[list[DiffRow], _d("Only in A, by count_a.")]
    changed: Annotated[list[DiffRow], _d("In both with another count.")]
    unchanged_count: Annotated[int, _d("Groups in both with the same count.")]
    totals: Annotated[DiffTotals, _d("Section sizes before --limit.")]
    truncated: Annotated[bool, _d("A section was cut by --limit.")]
    warnings: Annotated[list[str], _d("Spans differ by more than 4x.")] | UnsetType = UNSET


class TriageSet(Struct, kw_only=True):
    """The judged set."""

    id: Annotated[str, _d("Set id.")]
    name: Annotated[str | None, _d("Set name.")]
    first_ts: Annotated[str | None, _d("First entry, as in the manifest.")]
    last_ts: Annotated[str | None, _d("Last entry, as in the manifest.")]
    entries: Annotated[int, _d("Entries of the set.")]


class BaselineCandidate(Struct, kw_only=True):
    """A set rejected as the automatic baseline."""

    id: Annotated[str, _d("Set id.")]
    name: Annotated[str | None, _d("Set name.")]
    reason: Annotated[
        Literal[
            "stale",
            "recipe",
            "other_node",
            "other_cluster",
            "superset",
            "subset",
            "later",
            "tiny",
            "span",
        ],
        _d(
            "stale/recipe: another recipe_key; other_node: both sets have a node and it "
            "differs; other_cluster: both have a cluster and it differs; superset/subset: "
            "its sources contain or are part of the set's; later: ends after the set; tiny: "
            "under 1% of the entries; span: span more than 16x off."
        ),
    ]
    detail: Annotated[str, _d("The reason in one sentence.")]
    hint: Annotated[str, _d("A command that would make it usable.")] | UnsetType = UNSET


class TriageBaseline(Struct, kw_only=True):
    """The baseline set and how it was chosen."""

    id: Annotated[str, _d("Baseline id.")]
    name: Annotated[str | None, _d("Baseline name.")]
    first_ts: Annotated[str | None, _d("First entry, as in the manifest.")]
    last_ts: Annotated[str | None, _d("Last entry, as in the manifest.")]
    resolved_by: Annotated[Literal["auto", "param"], _d("auto = picked, param = given.")]
    reason: Annotated[str, _d("Why this baseline was picked, in one line.")] | UnsetType = UNSET
    entries: Annotated[int, _d("Entries of the baseline.")] | UnsetType = UNSET
    shared_sources: (
        Annotated[int, _d("Number of source files (by digest) shared with the set.")] | UnsetType
    ) = UNSET
    candidates: (
        Annotated[
            list[BaselineCandidate],
            _d("Up to 5 sets rejected as the automatic baseline, newest first; [] when given."),
        ]
        | UnsetType
    ) = UNSET


class TriageSummary(Struct, kw_only=True):
    """Counts of all rows (the lists are cut to --limit)."""

    new: Annotated[int, _d("Rows of new.")]
    new_by_level: Annotated[dict[str, int], _d("New groups per level name.")]
    new_exceptions: Annotated[int, _d("Rows of new_exceptions.")]
    grew: Annotated[int, _d("Rows of grew.")]
    vanished: Annotated[int, _d("Rows of vanished.")]
    changed: Annotated[int, _d("Groups in both sets with another count.")]
    unchanged: Annotated[int, _d("Groups in both sets with the same count.")]


class Coverage(Struct, kw_only=True):
    """Spans of both sets in hours."""

    hours_baseline: Annotated[float | None, _d("Baseline span; null without a baseline.")]
    hours_set: Annotated[float, _d("Set span.")]
    ratio: Annotated[float | None, _d("hours_set / hours_baseline.")]


Verdict = Literal["new_errors", "new_warnings", "new_exceptions", "clean", "no_baseline"]


class Triage(Struct, kw_only=True, tag_field="schema", tag="triage.v1"):
    """New kinds of errors since the baseline? `triage`, `GET /api/sets/{ref}/triage`."""

    set: Annotated[TriageSet, _d("The judged set.")]
    baseline: Annotated[TriageBaseline | None, _d("null: no baseline found (verdict no_baseline).")]
    min_level: Annotated[str | None, _d("Echo of --min-level.")]
    verdict: Annotated[
        Verdict, _d("new_errors > new_warnings > new_exceptions > clean; no_baseline.")
    ]
    summary: Annotated[TriageSummary | None, _d("null without a baseline.")]
    coverage: Annotated[Coverage, _d("Spans of both sets in hours.")]
    warnings: Annotated[
        list[str],
        _d(
            "Rejected baselines when there is none; a stale, contained or tiny given "
            "baseline; spans that differ by more than 4x."
        ),
    ]
    new: Annotated[list[DiffRow], _d("New groups at/above min_level, most severe first.")]
    new_exceptions: Annotated[list[DiffRow], _d("New lower-level groups with an exception.")]
    grew: Annotated[list[DiffRow], _d("Hourly rate rose at least --grow times.")]
    vanished: Annotated[list[DiffRow], _d("Groups at/above min_level only in the baseline.")]
    report_url: Annotated[str, _d("API: the set's report page.")] | UnsetType = UNSET


# --- metadata


class FormatInfo(Struct, kw_only=True):
    """One registered log format."""

    name: Annotated[str, _d("Format name (--format).")]
    description: Annotated[str, _d("What the format reads.")]
    signature_spec: Annotated[str, _d("Default signature spec.")] | UnsetType = UNSET
    mask_rules: Annotated[list[str], _d("Masking rule names.")] | UnsetType = UNSET
    confidence: Annotated[float, _d("With --sample: 0..1.")] | UnsetType = UNSET


class Formats(Struct, kw_only=True, tag_field="schema", tag="formats.v1"):
    """The supported log formats: `formats`, `GET /api/formats`."""

    count: Annotated[int, _d("Number of items.")]
    items: Annotated[list[FormatInfo], _d("The formats (sorted by confidence with --sample).")]


class VersionBuild(Struct, kw_only=True):
    """The build of the running ragnalog."""

    number: Annotated[
        int | None, _d("Build number: the commit count of the built commit (null: unknown).")
    ]
    git: Annotated[str | None, _d("The built commit's hash, 7 characters (null: unknown).")]
    time: Annotated[
        str | None, _d("When the container image was built, ISO 8601 UTC (null: not an image).")
    ]
    source: Annotated[
        Literal["image", "checkout", "unknown"],
        _d("image: recorded when the image was built; checkout: a git checkout's HEAD; unknown."),
    ]


class Version(Struct, kw_only=True, tag_field="schema", tag="version.v1"):
    """Tool, schema and signature versions: `version`, `GET /api/version`."""

    version: Annotated[str, _d("ragnalog version.")]
    schema_version: Annotated[str, _d("Store schema version <major>.<minor>.")]
    signature_version: Annotated[int, _d("Signature algorithm version.")]
    mask_rules_version: Annotated[int, _d("Built-in masking rules version.")]
    python: Annotated[str, _d("Python version.")]
    build: Annotated[VersionBuild, _d("The build: number, git hash and build time.")]


class Health(Struct, kw_only=True, tag_field="schema", tag="health.v1"):
    """Liveness of the web app: `GET /api/health`."""

    status: Annotated[Literal["ok"], _d("Always ok.")]
    sets: Annotated[int, _d("Sets in the data directory.")]


class Schema(Struct, kw_only=True, tag_field="schema", tag="schema.v1"):
    """Schema descriptions: `schema [NAME]`."""

    definitions: Annotated[
        dict[str, dict[str, Any]],
        _d("Stored tables: {schema, version, fields}; documents: a JSON Schema."),
    ]
    documents: Annotated[list[str], _d("Without NAME: the document names.")] | UnsetType = UNSET


class Error(Struct, kw_only=True, tag_field="schema", tag="error.v1"):
    """A failure (CLI: on stdout in json/jsonl mode, with exit code 1-3; API: 4xx/5xx)."""

    error: Annotated[ErrorInfo, _d("The error.")]


# --- web app jobs

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobProgress(Struct, kw_only=True):
    """Progress of a job."""

    entries: Annotated[int, _d("Entries parsed.")]
    bytes_read: Annotated[int, _d("Bytes read.")]
    bytes_total: Annotated[int, _d("Bytes uploaded.")]
    fraction: Annotated[float, _d("0..1.")]
    current_file: Annotated[str | None, _d("File being read.")]


class Job(Struct, kw_only=True, tag_field="schema", tag="job.v1"):
    """A background ingest: `POST /api/ingest`, `GET /api/jobs/{id}`."""

    id: Annotated[str, _d("Job id.")]
    kind: Annotated[Literal["ingest"], _d("Always ingest.")]
    state: Annotated[JobState, _d("queued, running, then succeeded, failed or cancelled.")]
    name: Annotated[str | None, _d("Set name requested.")]
    files: Annotated[list[str], _d("Uploaded file names.")]
    options: Annotated[
        dict[str, Any],
        _d(
            "Ingest options (format, frames, mine, overwrite, force, node, cluster, capture, "
            "split)."
        ),
    ]
    created_at: Annotated[str, _d("ISO 8601 UTC.")]
    started_at: Annotated[str | None, _d("ISO 8601 UTC.")]
    finished_at: Annotated[str | None, _d("ISO 8601 UTC.")]
    progress: Annotated[JobProgress, _d("Progress so far.")]
    result: Annotated[
        Logset | None, _d("The manifest once succeeded; of a split ingest, its first set's.")
    ]
    error: Annotated[ErrorInfo | None, _d("Set once failed.")]
    capture: (
        Annotated[
            IngestCapture, _d("A split ingest once succeeded: the ingest-capture.v1 document.")
        ]
        | UnsetType
    ) = UNSET


class Jobs(Struct, kw_only=True, tag_field="schema", tag="jobs.v1"):
    """Jobs of this server process, newest first: `GET /api/jobs`."""

    count: Annotated[int, _d("Number of items.")]
    items: Annotated[list[Job], _d("job.v1 documents.")]


# --- reports


class ReportFile(Struct, kw_only=True, tag_field="schema", tag="report-file.v1"):
    """A written HTML report: `report`."""

    path: Annotated[str, _d("Absolute path of the HTML file.")]
    bytes: Annotated[int, _d("File size.")]
    groups: Annotated[int, _d("Groups in the report.")]
    entries: Annotated[int, _d("Embedded example entries.")]
    compare: Annotated[bool, _d("Compared with --compare.")]
    capture: (
        Annotated[str | None, _d("report CAPTURE: the capture id (the cluster page).")] | UnsetType
    ) = UNSET
    nodes: Annotated[int, _d("report CAPTURE: nodes on the page.")] | UnsetType = UNSET


class ReportEntries(Struct, kw_only=True, tag_field="schema", tag="report-entries.v1"):
    """A group's examples in the report.v1 entry shape: `GET .../groups/{sig}/examples`."""

    logset: Annotated[str, _d("Set id.")]
    signature: Annotated[str, _d("Group signature.")]
    items: Annotated[dict[str, dict[str, Any]], _d("Entries by id (report.v1 entries).")]


class ReportOptions(Struct, kw_only=True):
    """Build options of a report payload."""

    min_level: Annotated[str | None, _d("Level filter or null.")]
    examples: Annotated[int, _d("Example entries per group.")]
    max_raw: Annotated[int, _d("Characters of raw kept; 0 = all.")]
    max_groups: Annotated[int | None, _d("Largest groups kept; null = all.")]
    truncated_groups: Annotated[int, _d("Groups cut by max_groups.")]


class GraveMarker(Struct, kw_only=True):
    """Grave errors of one kind close together: what a chart marks loudly (report/grave.py)."""

    kind: Annotated[
        str,
        _d(
            "oom (OutOfMemoryError, GC overhead limit, no native thread), stack_overflow, "
            "jvm_error (another VirtualMachineError), resource (too many open files, disk "
            "full) or fatal (any other FATAL entry)."
        ),
    ]
    ts: Annotated[str, _d("Time of its first entry.")]
    last_ts: Annotated[str, _d("Time of its last entry (= ts when count is 1).")]
    count: Annotated[
        int, _d("Entries folded into the marker: same kind, each within 60 s of the previous.")
    ]
    signature: Annotated[str, _d("Group signature of the first entry.")]
    entry_id: Annotated[str, _d("Id of the first entry.")]
    text: Annotated[
        str,
        _d(
            "The exception that made it grave (its type naming the kind, else its message "
            "matching, else the outermost): type and its own message; without a chain, the "
            "template. At most 200 characters."
        ),
    ]
    node: Annotated[str, _d("Node id (cluster-report.v1 only).")] | UnsetType = UNSET


class Report(Struct, kw_only=True, tag_field="schema", tag="report.v1"):
    """The HTML report's payload (envelope; docs/formats/report.v1.md has the parts)."""

    generated_at: Annotated[str, _d("Build time, YYYY-MM-DDTHH:MM:SSZ.")]
    tool_version: Annotated[str, _d("Version that built the payload.")]
    options: Annotated[ReportOptions, _d("Build options.")]
    logset: Annotated[Logset, _d("The set's manifest.")]
    stats: Annotated[dict[str, Any], _d("stats.v1 body without sources.")]
    timeline: Annotated[dict[str, Any], _d("{hours, levels, clipped?}.")]
    groups: Annotated[list[dict[str, Any]], _d("Compact group rows.")]
    entries: Annotated[dict[str, dict[str, Any]], _d("Example entries by id.")]
    merges: Annotated[list[dict[str, Any]], _d("Drain3 proposals within the groups.")]
    compare: Annotated[dict[str, Any] | None, _d("Comparison with --compare; null without.")]
    grave: Annotated[
        list[GraveMarker], _d("Grave errors by time (at most 500, those with most entries).")
    ]
    entries_url: Annotated[str, _d("Web app: examples URL template.")] | UnsetType = UNSET


# --- agent manifest


class ManifestStep(Struct, kw_only=True):
    """One step of the workflow, or one situation of `when`."""

    run: Annotated[str, _d("Command line, e.g. triage SET.")]
    purpose: Annotated[str, _d("What it is for.")]


class ManifestCommand(Struct, kw_only=True, omit_defaults=True):
    """One command."""

    summary: Annotated[str, _d("First line of --help; omitted for workflow/when steps.")] = ""
    usage: Annotated[
        str,
        _d(
            "Arguments (UPPERCASE, ... = several) and [--option TYPE[=DEFAULT]]: TYPE str, int, "
            "float, path or choices a|b|c, * = repeatable; [--flag] takes no value."
        ),
    ] = ""
    output: Annotated[str | None, _d("Document printed (ragnalog schema NAME).")] = None
    exit: Annotated[list[int], _d("Possible exit codes (see exit_codes).")] = []


class ManifestDocuments(Struct, kw_only=True):
    """Where the document schemas are."""

    get: Annotated[str, _d("How to get a schema.")]
    names: Annotated[list[str], _d("Document names.")]


class Manifest(Struct, kw_only=True, tag_field="schema", tag="manifest.v1"):
    """How an agent uses ragnalog: `manifest`, bare `ragnalog` piped, `GET /api/manifest`."""

    tool: Annotated[str, _d("Command name.")]
    version: Annotated[str, _d("Tool version.")]
    usage: Annotated[str, _d("Command line shape.")]
    notes: Annotated[list[str], _d("Conventions every command follows.")]
    workflow: Annotated[list[ManifestStep], _d("The usual order of commands.")]
    when: Annotated[list[ManifestStep], _d("Commands for other situations.")]
    commands: Annotated[dict[str, ManifestCommand], _d("Every command by name.")]
    help: Annotated[
        dict[str, str],
        _d(
            "One-line help by parameter name, as most commands give it (ragnalog COMMAND "
            "--help has each command's own): names several commands share and the "
            "parameters of the workflow and when commands."
        ),
    ]
    global_options: Annotated[dict[str, str], _d('"TYPE[=DEFAULT]: help" of each option.')]
    exit_codes: Annotated[dict[str, str], _d("Meaning of each exit code.")]
    documents: Annotated[ManifestDocuments, _d("The output documents and their schemas.")]
    presets: Annotated[dict[str, list[str]], _d("Columns of the --brief presets.")]
    env: Annotated[dict[str, str], _d("Environment variables.")]


class ManifestParam(Struct, kw_only=True, omit_defaults=True):
    """One argument or option of a command."""

    name: Annotated[str, _d("LOGSET (argument, ... = several) or --option.")]
    type: Annotated[str, _d("str, int, float, path, flag or choices a|b|c; * = repeatable.")]
    default: Annotated[str | None, _d("Default value, if any.")] = None
    help: Annotated[str, _d("Its --help text.")] = ""


class ManifestCommandDetail(
    Struct, kw_only=True, omit_defaults=True, tag_field="schema", tag="manifest-command.v1"
):
    """One command in full: `manifest COMMAND`, `GET /api/manifest/{command}`."""

    command: Annotated[str, _d("Command name.")]
    version: Annotated[str, _d("Tool version.")]
    usage: Annotated[str, _d("As in manifest.v1 commands.")]
    description: Annotated[
        str, _d("The command's --help text without the parameter list (output, exit codes).")
    ]
    output: Annotated[str | None, _d("Document printed (ragnalog schema NAME).")] = None
    exit: Annotated[list[int], _d("Possible exit codes (see manifest.v1 exit_codes).")] = []
    params: Annotated[list[ManifestParam], _d("Arguments, then options, in --help order.")] = []


# --- clusters: capture views of service/cluster.py (capture.v1, cluster-timeline.v1, matrix.v1,
# events.v1, interactions.v1, cluster-report.v1). Times are ISO 8601 UTC with milliseconds,
# bucket starts end in Z. A "...Body" Struct is its document without "schema", as
# cluster-report.v1 embeds it.


class ClusterWindow(Struct, kw_only=True):
    """The time window of a capture view; both ends inclusive."""

    mode: Annotated[
        Literal["overlap", "union", "explicit"],
        _d(
            "overlap: latest first entry to earliest last entry of the nodes; union: earliest "
            "first to latest last; explicit: since/until given."
        ),
    ]
    since: Annotated[str, _d("Start, ISO 8601 UTC.")]
    until: Annotated[str, _d("End, ISO 8601 UTC.")]


class ClusterSpan(Struct, kw_only=True):
    """A node's logs inside the window: from its first to its last entry there."""

    since: Annotated[str, _d("ISO 8601 UTC.")]
    until: Annotated[str, _d("ISO 8601 UTC.")]


class ClusterSetRef(Struct, kw_only=True):
    """The log set of a node."""

    id: Annotated[str, _d("Set id.")]
    name: Annotated[str | None, _d("Set name.")]


class ClusterTopError(Struct, kw_only=True):
    """The ERROR/FATAL group with the most entries of a node in the window."""

    signature: Annotated[str, _d("Group signature.")]
    template: Annotated[str | None, _d("Group template.")]
    count: Annotated[int, _d("Its entries on the node in the window.")]


class ClusterBaseline(Struct, kw_only=True):
    """The node's own earlier set its triage compared with."""

    id: Annotated[str, _d("Baseline set id.")]
    name: Annotated[str | None, _d("Baseline set name.")]
    reason: Annotated[str | None, _d("Why it was picked, in one line.")]


class ClusterNodeSummary(Struct, kw_only=True):
    """One node of a capture."""

    node: Annotated[str, _d("Node id.")]
    set: Annotated[ClusterSetRef, _d("The node's log set.")]
    first_ts: Annotated[str | None, _d("The set's first entry (whole set).")]
    last_ts: Annotated[str | None, _d("The set's last entry (whole set).")]
    coverage: Annotated[
        ClusterSpan | None, _d("The node's logs inside the window; null when it has none there.")
    ]
    gaps: Annotated[
        list[tuple[str, str]],
        _d(
            "[since, until] of runs of at least 15 empty minutes inside the coverage, oldest "
            "first, at most 100."
        ),
    ]
    entries: Annotated[int, _d("Entries in the window.")]
    errors: Annotated[int, _d("ERROR and FATAL entries in the window.")]
    warnings: Annotated[int, _d("WARN entries in the window.")]
    errors_per_hour: Annotated[float, _d("errors per hour of coverage.")]
    warnings_per_hour: Annotated[float, _d("warnings per hour of coverage.")]
    top_error: Annotated[ClusterTopError | None, _d("Largest error group; null without errors.")]
    verdict: Annotated[
        Verdict | None, _d("Triage against the node's own baseline; null unless asked for.")
    ]
    baseline: Annotated[
        ClusterBaseline | None, _d("The baseline of verdict; null without one or unless asked.")
    ]
    events: Annotated[dict[str, int], _d("Events in the window by kind.")]


class ClusterSkew(Struct, kw_only=True):
    """Estimated clock offset of node b against node a (informational, never applied)."""

    a: Annotated[str, _d("Node id.")]
    b: Annotated[str, _d("Node id, after a in node order.")]
    offset_ms: Annotated[int, _d("Median of ts on b minus ts on a over the pairs.")]
    pairs: Annotated[int, _d("Event pairs the estimate rests on.")]


class ClusterCaptureBody(Struct, kw_only=True):
    """capture.v1 without its schema key."""

    capture: Annotated[str | None, _d("Capture id; null when not known.")]
    cluster: Annotated[str | None, _d("Cluster name.")]
    window: Annotated[ClusterWindow, _d("The window of the counts.")]
    nodes: Annotated[list[ClusterNodeSummary], _d("The nodes, sorted by node id.")]
    skew: Annotated[
        list[ClusterSkew],
        _d(
            "Clock offset estimates from simultaneous event pairs (index, re-index and cluster "
            "lock events less than 5 s apart); [] when there are none."
        ),
    ]


class ClusterCapture(ClusterCaptureBody, kw_only=True, tag_field="schema", tag="capture.v1"):
    """The nodes of a capture side by side: coverage, gaps, volume, events, verdicts."""


class ClusterNodeTimeline(Struct, kw_only=True):
    """One node's counts per bucket."""

    levels: Annotated[
        dict[str, list[int]],
        _d(
            "Entries per bucket by ERROR (with FATAL), WARN, INFO, DEBUG (with TRACE), OTHER; "
            'only "count" with a signature.'
        ),
    ]
    coverage: Annotated[
        tuple[int, int] | None,
        _d("Indices of the buckets of the node's first and last entry; null outside."),
    ]


class ClusterTimelineBody(Struct, kw_only=True):
    """cluster-timeline.v1 without its schema key."""

    bucket: Annotated[str, _d("Bucket size, e.g. 1h.")]
    buckets: Annotated[list[str], _d("Bucket starts, ISO 8601 UTC, contiguous over the window.")]
    nodes: Annotated[dict[str, ClusterNodeTimeline], _d("Counts by node id.")]
    signature: Annotated[str, _d("The group counted, when one was asked for.")] | UnsetType = UNSET


class ClusterTimeline(
    ClusterTimelineBody, kw_only=True, tag_field="schema", tag="cluster-timeline.v1"
):
    """Entries per time bucket and node, by level or of one group."""


class MatrixNodeCount(Struct, kw_only=True):
    """A group on one node in the window."""

    count: Annotated[int, _d("Entries; 0 when absent.")]
    rate_per_hour: Annotated[float, _d("Entries per hour of the node's coverage.")]
    first_seen: Annotated[str | None, _d("First entry; null when absent.")]
    last_seen: Annotated[str | None, _d("Last entry; null when absent.")]


class MatrixRow(Struct, kw_only=True):
    """One group across the nodes."""

    signature: Annotated[str, _d("Group signature.")]
    template: Annotated[str | None, _d("Masked message template.")]
    level: Annotated[int, _d("Numeric level.")]
    level_name: Annotated[str, _d("Level name.")]
    logger: Annotated[str | None, _d("Logger.")]
    exception_type: Annotated[str | None, _d("Outermost exception type.")]
    total: Annotated[int, _d("Entries on all nodes in the window.")]
    per_node: Annotated[dict[str, MatrixNodeCount], _d("Every node of the capture by id.")]
    spread: Annotated[
        Literal["all", "only", "skewed"],
        _d(
            "only: on one node; skewed: missing from a node that has logs in the window, or "
            "skew >= skew_min; all: otherwise."
        ),
    ]
    skew: Annotated[
        float | None,
        _d(
            "Max/min, over the nodes that have the group, of count / the node's entries in the "
            "window; null when only."
        ),
    ]
    first_node: Annotated[str | None, _d("Node with the earliest first_seen.")]
    lead_ms: Annotated[
        int | None, _d("first_seen of the next node minus that of first_node; null when only.")
    ]


class ClusterMatrix(Struct, kw_only=True, tag_field="schema", tag="matrix.v1"):
    """Groups by node: where each group occurs, how often, and which node saw it first."""

    window: Annotated[ClusterWindow, _d("The window of the counts.")]
    nodes: Annotated[list[str], _d("Node ids, in order.")]
    count: Annotated[int, _d("Rows in items.")]
    total: Annotated[int, _d("Matching groups before offset/limit.")]
    offset: Annotated[int, _d("Echo of offset.")]
    truncated: Annotated[bool, _d("More rows follow.")]
    items: Annotated[list[MatrixRow], _d("Rows by the sort (count, skew or lead), descending.")]


class ClusterEvent(Struct, kw_only=True):
    """One event: a group matched by an event rule, repeats within a short time folded in."""

    node: Annotated[str, _d("Node id.")]
    ts: Annotated[str, _d("Time of its first entry.")]
    last_ts: Annotated[str, _d("Time of its last entry (= ts when count is 1).")]
    kind: Annotated[str, _d("start, stop, health_failed, cache_replication, … job_run.")]
    subject: Annotated[
        str | None, _d("What it is about: a node id, a health check, a lock, a job key.")
    ]
    subject_node: Annotated[str | None, _d("subject when it is a node of the capture.")]
    signature: Annotated[str, _d("Group signature of the first entry.")]
    entry_id: Annotated[str, _d("Id of the first entry (in the node's set).")]
    text: Annotated[
        str,
        _d(
            "Message of the first entry (its group's template when the message is blank), "
            "at most 300 characters."
        ),
    ]
    count: Annotated[
        int,
        _d(
            "Entries folded into the event: same node, kind and subject within 60 s of the "
            "first (job_run 120 s, start and stop 10 min); health_failed and job_run also "
            "fold repeats at most 2 h (job_run 30 min) after the previous one."
        ),
    ]


class EventSide(Struct, kw_only=True):
    """One side of an event pair."""

    node: Annotated[str, _d("Node id.")]
    kind: Annotated[str, _d("Event kind.")]


class EventPair(Struct, kw_only=True):
    """Events of kind a on node a followed within 60 s by events of kind b on node b."""

    a: Annotated[EventSide, _d("The earlier event.")]
    b: Annotated[EventSide, _d("The later event, on another node.")]
    count: Annotated[int, _d("Events a followed by an event b.")]
    of: Annotated[int, _d("Events a in the window.")]
    median_lag_ms: Annotated[int, _d("Median time from a to the first b after it.")]
    lift: Annotated[
        float, _d("count / expected, expected = of x min(1, events b x 60 s / window).")
    ]
    examples: Annotated[list[tuple[str, str]], _d("Up to 3 [ts a, ts b].")]


class ClusterEventsBody(Struct, kw_only=True):
    """events.v1 without its schema key."""

    rules_version: Annotated[int, _d("EVENT_RULES_VERSION of the rules used.")]
    count: Annotated[int, _d("Rows in items.")]
    items: Annotated[list[ClusterEvent], _d("Events by time, then node.")]
    pairs: Annotated[
        list[EventPair],
        _d(
            "Kind pairs on different nodes with lift >= 3, by count; without job_run (see "
            "interactions.concurrent_jobs); at most 50. Computed before scheduled repeats "
            "are folded."
        ),
    ]


class ClusterEvents(ClusterEventsBody, kw_only=True, tag_field="schema", tag="events.v1"):
    """What every node did: restarts, health checks, index and cache events, jobs."""


class MentionEdge(Struct, kw_only=True):
    """Entries of one node naming another node of the capture."""

    from_: Annotated[str, _d("The node whose entries name the other.")] = msgspec.field(name="from")
    to: Annotated[str, _d("The node named (whole word, case-sensitive).")]
    count: Annotated[int, _d("Entries in the window.")]
    groups: Annotated[list[str], _d("Their signatures, most entries first, at most 20.")]


class BurstSide(Struct, kw_only=True):
    """A group on one node."""

    node: Annotated[str, _d("Node id.")]
    signature: Annotated[str, _d("Group signature.")]
    template: Annotated[str | None, _d("Group template.")]
    groups: Annotated[
        int,
        _d(
            "Groups of the node that burst together with it (itself included); it stands "
            "for them as the one with most bursts."
        ),
    ]


class CoBurst(Struct, kw_only=True):
    """When group a burst on node a, group b burst on node b within delta_s."""

    a: Annotated[BurstSide, _d("The group that bursts first.")]
    b: Annotated[BurstSide, _d("The group that follows, on another node.")]
    hits: Annotated[int, _d("Bursts of a followed by a burst of b.")]
    of: Annotated[int, _d("Bursts of a.")]
    median_lag_ms: Annotated[int, _d("Median lag from a to b, whole minutes in ms.")]
    lift: Annotated[float, _d("hits / expected, expected = of x P(a burst of b in delta_s).")]


class ConcurrentJob(Struct, kw_only=True):
    """A job run on several nodes in the same slot."""

    job: Annotated[str, _d("Job key (job_run subject).")]
    nodes: Annotated[list[str], _d("Nodes it ran on together.")]
    slots: Annotated[int, _d("Slots of 60 s in which it ran on 2 or more nodes.")]
    examples: Annotated[list[str], _d("Start of up to 3 such slots.")]


class SessionPair(Struct, kw_only=True):
    """Sessions seen on both nodes within an hour."""

    nodes: Annotated[tuple[str, str], _d("The two nodes.")]
    count: Annotated[int, _d("Sessions.")]


class JobRuns(Struct, kw_only=True):
    """Which nodes run a job: job_run events per node."""

    job: Annotated[str, _d("Job key.")]
    runs: Annotated[dict[str, int], _d("job_run events by node.")]


class ClusterInteractionsBody(Struct, kw_only=True):
    """interactions.v1 without its schema key."""

    delta_s: Annotated[int, _d("Echo of delta_s (co-bursts).")]
    mentions: Annotated[list[MentionEdge], _d("By count.")]
    co_bursts: Annotated[
        list[CoBurst],
        _d(
            "Pairs with hits >= 3, hits >= of / 2 and lift >= 3, by hits then lift; each "
            "group at most once as a and once as b; at most 50."
        ),
    ]
    concurrent_jobs: Annotated[list[ConcurrentJob], _d("By slots.")]
    sessions: Annotated[list[SessionPair], _d("By count; session ids are not listed.")]
    jobs: Annotated[
        list[JobRuns], _d("Job ownership: job_run events per node, most nodes first; at most 100.")
    ]


class ClusterInteractions(
    ClusterInteractionsBody, kw_only=True, tag_field="schema", tag="interactions.v1"
):
    """How the nodes affect each other: mentions, co-bursts, concurrent jobs, sessions."""


class ClusterReportGroup(MatrixRow, kw_only=True):
    """A matrix row with its counts per bucket and examples per node."""

    hourly: Annotated[
        dict[str, list[tuple[int, int]]],
        _d("Per node, sparse [bucket index into timeline.buckets, count]."),
    ]
    examples: Annotated[dict[str, list[str]], _d("Per node, entry ids present in entries.")]


class ClusterReportOptions(Struct, kw_only=True):
    """Build options of a cluster report payload."""

    window: Annotated[Literal["overlap", "union", "explicit"], _d("Window mode.")]
    examples: Annotated[int, _d("Example entries per group and node.")]
    max_raw: Annotated[int, _d("Characters of raw kept; 0 = all.")]
    max_groups: Annotated[int, _d("Largest groups kept; 0 = all.")]
    truncated_groups: Annotated[int, _d("Groups cut by max_groups.")]


class ClusterReportUrls(Struct, kw_only=True):
    """Links the web app adds."""

    entries: Annotated[str, _d("Merged entries of the capture.")]
    context: Annotated[str, _d("Context on all nodes of one entry (template).")]
    set_report: Annotated[str, _d("Report page of one node's set (template).")]
    examples: Annotated[
        str | None, _d("A group's example entries on one node's set, in full (template).")
    ] = None
    raw: Annotated[str | None, _d("One entry's exact text as text/plain (template).")] = None
    timeline: Annotated[
        str | None, _d("The capture's timeline API: the page's bins under the payload's bucket.")
    ] = None


class ClusterReport(Struct, kw_only=True, tag_field="schema", tag="cluster-report.v1"):
    """The cluster page's payload: a capture, its timeline, groups, events and interactions."""

    generated_at: Annotated[str, _d("Build time, YYYY-MM-DDTHH:MM:SSZ.")]
    tool_version: Annotated[str, _d("Version that built the payload.")]
    capture: Annotated[ClusterCaptureBody, _d("capture.v1 without schema.")]
    nodes: Annotated[list[str], _d("Node ids; their order is the colour order.")]
    timeline: Annotated[
        ClusterTimelineBody, _d("cluster-timeline.v1, bucket 1h (15m when the window < 12 h).")
    ]
    groups: Annotated[list[ClusterReportGroup], _d("Matrix rows by count, at most max_groups.")]
    entries: Annotated[
        dict[str, dict[str, Any]],
        _d(
            "Example entries by id: report.v1 entries plus node and set (an id two nodes "
            "share, an identical line in the same millisecond, is the first node's)."
        ),
    ]
    events: Annotated[ClusterEventsBody, _d("events.v1 without schema.")]
    interactions: Annotated[ClusterInteractionsBody, _d("interactions.v1 without schema.")]
    grave: Annotated[
        list[GraveMarker],
        _d("Grave errors of every node in the window, by time, then node (at most 500 a node)."),
    ]
    options: Annotated[ClusterReportOptions, _d("Build options.")]
    urls: Annotated[ClusterReportUrls | None, _d("null; the web app fills it.")]


# --- end of clusters


Document = (
    Sets
    | Logset
    | Captures
    | IngestCapture
    | Remove
    | Upgrade
    | Stats
    | Groups
    | Group
    | Entries
    | Frames
    | Timeline
    | Users
    | Query
    | Diff
    | Triage
    | Formats
    | Version
    | Health
    | Schema
    | Error
    | Job
    | Jobs
    | ReportFile
    | ReportEntries
    | Report
    | Manifest
    | ManifestCommandDetail
    | ClusterCapture
    | ClusterTimeline
    | ClusterMatrix
    | ClusterEvents
    | ClusterInteractions
    | ClusterReport
)
"""Any document; `schema` selects the Struct."""

DOCUMENTS: Final[dict[str, type[Struct]]] = {
    str(cls.__struct_config__.tag): cls for cls in get_args(Document)
}
"""Struct of each document by name (`"triage.v1"`)."""


def document_names() -> list[str]:
    """Names of all documents, sorted."""
    return sorted(DOCUMENTS)


def convert(doc: dict[str, Any]) -> Struct:
    """Validate a decoded document against the Struct its `schema` names.

    Raises:
        KeyError: Unknown `schema`.
        msgspec.ValidationError: The document does not match.

    """
    return msgspec.convert(doc, DOCUMENTS[doc["schema"]])


def decode(raw: bytes | str) -> Struct:
    """Decode and validate one JSON document (any :data:`Document`)."""
    result: Struct = msgspec.json.decode(raw, type=Document)
    return result


# --- JSON Schema


def _arrow_json(t: Any) -> dict[str, Any]:
    """JSON Schema of an Arrow type as it appears in the JSON documents."""
    import pyarrow as pa

    if pa.types.is_dictionary(t):
        return _arrow_json(t.value_type)
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return {"type": "string"}
    if pa.types.is_integer(t):
        return {"type": "integer"}
    if pa.types.is_floating(t):
        return {"type": "number"}
    if pa.types.is_boolean(t):
        return {"type": "boolean"}
    if pa.types.is_timestamp(t):
        return {"type": "string", "format": "date-time"}
    if pa.types.is_map(t):
        return {"type": "object", "additionalProperties": _arrow_json(t.item_type)}
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return {"type": "array", "items": _arrow_json(t.value_type)}
    if pa.types.is_struct(t):
        fields = [t.field(i) for i in range(t.num_fields)]
        return {
            "type": "object",
            "properties": {f.name: _field_json(f) for f in fields},
            "required": [f.name for f in fields if not f.nullable],
        }
    raise TypeError(f"no JSON Schema for Arrow type {t}")


def _field_json(field: Any, description: str | None = None) -> dict[str, Any]:
    schema = _arrow_json(field.type)
    if field.nullable:
        schema = {"anyOf": [schema, {"type": "null"}]}
    meta = field.metadata or {}
    text = description or meta.get(b"description", b"").decode()
    return {"description": text, **schema} if text else schema


def _row(title: str, description: str, fields: list[Any], extra: dict[str, Any]) -> dict[str, Any]:
    props = {f.name: _field_json(f) for f in fields}
    props.update(extra)
    return {"title": title, "description": description, "type": "object", "properties": props}


_INT, _NUM, _STR = {"type": "integer"}, {"type": "number"}, {"type": "string"}
_TS = {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}
_NULL_NUM = {"anyOf": [_NUM, {"type": "null"}]}


def row_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schemas of the row objects, by component name (:data:`ROW_COMPONENTS`).

    Generated from the Arrow schemas of `store/schema.py`; no property is
    required because `--columns`/`--brief` select columns.
    """
    group_cols = {f.name: f for f in GROUPS_SCHEMA}
    diff_fields = [
        group_cols[c]
        for c in ("signature", "template", "level", "level_name", "logger", "exception_type")
    ]
    return {
        "EntryRow": _row(
            "entries.v1 row",
            "One entry; the entries.v1 columns (ragnalog schema entries).",
            list(ENTRIES_SCHEMA),
            {
                "body_truncated": {
                    "description": "Only on rows whose raw or body was cut (--max-raw).",
                    "const": True,
                },
                "raw_chars": {
                    "description": "Original length of raw (of body without raw); cut rows only.",
                    **_INT,
                },
                "node": {"description": "Entries of a capture: the entry's node.", **_STR},
                "set": {"description": "Entries of a capture: the id of the node's set.", **_STR},
            },
        ),
        "GroupRow": _row(
            "groups.v1 row",
            "One group; the groups.v1 columns (ragnalog schema groups).",
            list(GROUPS_SCHEMA),
            {
                "signatures": {
                    "description": "--coarse only: the member signatures.",
                    "type": "array",
                    "items": _STR,
                }
            },
        ),
        "MergeRow": _row(
            "merges.v1 row",
            "One Drain3 merge proposal (ragnalog schema merges).",
            list(MERGES_SCHEMA),
            {},
        ),
        "DiffRow": _row(
            "diff row",
            "One group compared between baseline A and set B (counts are 0 on the missing side).",
            diff_fields,
            {
                "count_a": {"description": "Entries in A.", **_INT},
                "count_b": {"description": "Entries in B.", **_INT},
                "first_seen_a": {"description": "First entry in A (UTC).", **_TS},
                "last_seen_a": {"description": "Last entry in A (UTC).", **_TS},
                "first_seen_b": {"description": "First entry in B (UTC).", **_TS},
                "last_seen_b": {"description": "Last entry in B (UTC).", **_TS},
                "ratio": {"description": "changed: count_b / count_a.", **_NUM},
                "rate_a": {"description": "--rate, triage grew: count_a per hour.", **_NULL_NUM},
                "rate_b": {"description": "--rate, triage grew: count_b per hour.", **_NULL_NUM},
                "rate_ratio": {"description": "rate_b / rate_a.", **_NULL_NUM},
                "rate_per_hour": {"description": "triage new: count_b per hour.", **_NULL_NUM},
                "report_url": {
                    "description": "API triage: report page of the group (vanished: the "
                    "baseline's report).",
                    **_STR,
                },
            },
        ),
    }


def _rewrite(node: Any, ref: str, rename: dict[str, str]) -> Any:
    """Replace row markers by references and point references at renamed components."""
    if isinstance(node, list):
        return [_rewrite(v, ref, rename) for v in node]
    if not isinstance(node, dict):
        return node
    if _ROW in node:
        row = {"$ref": ref.format(name=ROW_COMPONENTS[node[_ROW]])}
        return {"description": node["description"], **row} if "description" in node else row
    out = {k: _rewrite(v, ref, rename) for k, v in node.items()}
    target = out.get("$ref")
    if isinstance(target, str) and target.startswith(ref.format(name="")):
        name = target[len(ref.format(name="")) :]
        out["$ref"] = ref.format(name=rename.get(name, name))
    return out


def components(ref: str = "#/components/schemas/{name}") -> dict[str, dict[str, Any]]:
    """JSON Schemas of every document, its parts and the rows, by component name.

    Documents are named after their `schema` (`"triage.v1"`), parts after their
    Struct (`"TriageBaseline"`), rows as in :data:`ROW_COMPONENTS`; references
    use `ref` (OpenAPI by default).
    """
    _, found = msgspec.json.schema_components(DOCUMENTS.values(), ref_template=ref)
    rename = {cls.__name__: tag for tag, cls in DOCUMENTS.items()}
    out: dict[str, dict[str, Any]] = {}
    for name, schema in found.items():
        if name in rename:
            schema = {**schema, "title": rename[name]}
        out[rename.get(name, name)] = _rewrite(schema, ref, rename)
    out.update(row_schemas())
    return out


def _refs(node: Any, prefix: str) -> set[str]:
    if isinstance(node, list):
        return set().union(*(_refs(v, prefix) for v in node)) if node else set()
    if not isinstance(node, dict):
        return set()
    found = set().union(*(_refs(v, prefix) for v in node.values())) if node else set()
    target = node.get("$ref")
    if isinstance(target, str) and target.startswith(prefix):
        found.add(target[len(prefix) :])
    return found


def document_schema(name: str) -> dict[str, Any]:
    """Standalone JSON Schema (draft 2020-12) of one document, e.g. `"triage.v1"`.

    Raises:
        KeyError: Unknown document name.

    """
    if name not in DOCUMENTS:
        raise KeyError(name)
    prefix = "#/$defs/"
    every = components(prefix + "{name}")
    wanted: set[str] = set()
    todo = _refs(every[name], prefix)
    while todo:
        part = todo.pop()
        if part not in wanted and part != name:
            wanted.add(part)
            todo |= _refs(every[part], prefix)
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{name}.schema.json",
        **every[name],
    }
    if wanted:
        schema["$defs"] = {k: every[k] for k in sorted(wanted)}
    return schema
