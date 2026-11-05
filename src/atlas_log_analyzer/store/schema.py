"""Versioned Arrow schemas for the store files.

``SCHEMA_VERSION`` is ``"<major>.<minor>"``; readers refuse a newer major.
"""

from __future__ import annotations

import pyarrow as pa

__all__ = [
    "ENTRIES_SCHEMA",
    "GROUPS_SCHEMA",
    "LOGSET_FIELDS",
    "MERGES_SCHEMA",
    "SCHEMA_NAMES",
    "SCHEMA_VERSION",
    "schema_json",
    "schema_major",
]

SCHEMA_VERSION = "1.1"
"""1.1: the manifest gains ``node``, ``cluster`` and ``capture`` (nullable; 1.0 sets lack them)."""

SCHEMA_NAMES: tuple[str, ...] = ("entries", "groups", "merges", "logset")
"""Names accepted by :func:`schema_json`."""


def schema_major(version: str) -> int:
    """Major component of a ``"<major>.<minor>"`` version string."""
    return int(str(version).split(".", 1)[0])


def _f(name: str, type_: pa.DataType, description: str, *, nullable: bool = True) -> pa.Field:
    return pa.field(name, type_, nullable=nullable, metadata={"description": description})


DICT_STRING = pa.dictionary(pa.int32(), pa.string())
TIMESTAMP = pa.timestamp("ms", tz="UTC")

FRAME_TYPE = pa.struct(
    [
        pa.field("location", pa.string(), nullable=False),
        pa.field("file", pa.string()),
        pa.field("line", pa.int32()),
        pa.field("module", pa.string()),
    ]
)
EXCEPTION_TYPE = pa.struct(
    [
        pa.field("type", pa.string(), nullable=False),
        pa.field("message", pa.string()),
        pa.field("frames", pa.list_(FRAME_TYPE)),
        pa.field("omitted_frames", pa.int32()),
    ]
)
VALUE_COUNT_TYPE = pa.struct(
    [
        pa.field("value", pa.string(), nullable=False),
        pa.field("count", pa.int64(), nullable=False),
    ]
)

ENTRIES_SCHEMA: pa.Schema = pa.schema(
    [
        _f(
            "id",
            pa.string(),
            "Entry id: xxh3-64 hex of (format, timestamp, thread, logger, raw).",
            nullable=False,
        ),
        _f("ts", TIMESTAMP, "Timestamp in UTC, millisecond precision.", nullable=False),
        _f("tz_offset", pa.int16(), "Original UTC offset of the timestamp in minutes."),
        _f(
            "level",
            pa.uint8(),
            "Numeric level: TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60 UNKNOWN=0.",
            nullable=False,
        ),
        _f(
            "level_name",
            DICT_STRING,
            "Level name: TRACE, DEBUG, INFO, WARN, ERROR, FATAL or UNKNOWN.",
            nullable=False,
        ),
        _f("thread", pa.string(), "Thread name."),
        _f("logger", DICT_STRING, "Logger (category) name."),
        _f("message", pa.string(), "First line of the message (header remainder)."),
        _f("body", pa.string(), "Continuation lines joined with LF; empty when single-line."),
        _f("user", pa.string(), "User from the request context."),
        _f("request_id", pa.string(), "Request id from the request context."),
        _f("session", pa.string(), "Session id from the request context."),
        _f("ip", pa.string(), "Client IP address(es) from the request context."),
        _f("url", pa.string(), "URL(s) from the thread segment."),
        _f("request_url", pa.string(), "URL from the request context."),
        _f(
            "context",
            pa.map_(pa.string(), pa.string()),
            "Other format-specific header fields (keys without a dedicated column).",
        ),
        _f("exception_type", pa.string(), "Type of the outermost exception, if any."),
        _f("exception_message", pa.string(), "Message of the outermost exception, if any."),
        _f(
            "exception_chain",
            pa.list_(EXCEPTION_TYPE),
            "Parsed exception chain, outermost first; empty list when none.",
        ),
        _f(
            "signature",
            pa.string(),
            "Primary signature (16 hex chars); key into groups.",
            nullable=False,
        ),
        _f(
            "coarse_signature",
            pa.string(),
            "Coarse signature (format, level, logger, masked message).",
            nullable=False,
        ),
        _f("template", pa.string(), "Masked first message line with <PLACEHOLDERS>."),
        _f("variables", pa.list_(pa.string()), "Values replaced by placeholders, in order."),
        _f("source_path", DICT_STRING, "Path of the source file."),
        _f("source_line", pa.uint32(), "1-based line number of the header line."),
        _f("source_offset", pa.uint64(), "Byte offset of the header line in the source file."),
        _f("raw", pa.string(), "Exact original text (header and continuation lines)."),
        _f("parse_error", pa.string(), "Set when the header line was not recognised."),
    ],
    metadata={"schema": "entries.v1", "version": SCHEMA_VERSION},
)
"""entries.v1 — one row per entry. See docs/formats/entries.v1.md."""

GROUPS_SCHEMA: pa.Schema = pa.schema(
    [
        _f("signature", pa.string(), "Primary signature (16 hex chars).", nullable=False),
        _f(
            "coarse_signature",
            pa.string(),
            "Coarse signature shared by the group's entries.",
            nullable=False,
        ),
        _f("template", pa.string(), "Masked message template."),
        _f("level", pa.uint8(), "Numeric level (see entries.level).", nullable=False),
        _f("level_name", DICT_STRING, "Level name.", nullable=False),
        _f("logger", DICT_STRING, "Logger name."),
        _f("exception_type", pa.string(), "Outermost exception type, if any."),
        _f("count", pa.int64(), "Number of entries.", nullable=False),
        _f("first_seen", TIMESTAMP, "Timestamp of the earliest entry (UTC)."),
        _f("last_seen", TIMESTAMP, "Timestamp of the latest entry (UTC)."),
        _f("user_count", pa.int32(), "Number of distinct users.", nullable=False),
        _f("thread_count", pa.int32(), "Number of distinct threads.", nullable=False),
        _f("source_count", pa.int32(), "Number of distinct source files.", nullable=False),
        _f(
            "top_users",
            pa.list_(VALUE_COUNT_TYPE),
            "Up to 10 most frequent users with counts, descending.",
        ),
        _f(
            "top_threads",
            pa.list_(VALUE_COUNT_TYPE),
            "Up to 10 most frequent threads with counts, descending.",
        ),
        _f("sources", pa.list_(VALUE_COUNT_TYPE), "All source files with counts, descending."),
        _f("examples", pa.list_(pa.string()), "Ids of representative entries."),
    ],
    metadata={"schema": "groups.v1", "version": SCHEMA_VERSION},
)
"""groups.v1 — one row per signature. See docs/formats/groups.v1.md."""

MERGES_SCHEMA: pa.Schema = pa.schema(
    [
        _f("cluster_id", pa.int64(), "Drain3 cluster id.", nullable=False),
        _f("drain_template", pa.string(), "Template proposed by Drain3 (<*> wildcards)."),
        _f("signatures", pa.list_(pa.string()), "Signatures proposed to be merged."),
    ],
    metadata={"schema": "merges.v1", "version": SCHEMA_VERSION},
)
"""merges.v1 — Drain3 merge proposals: cluster_id, signatures (list), drain_template."""

_SOURCE_TYPE = (
    "list<struct<path: string, size: int64, xxh3: string, first_ts: string, "
    "last_ts: string, entries: int64>>"
)

LOGSET_FIELDS: tuple[tuple[str, str, bool, str], ...] = (
    ("schema", "string", False, 'Always "logset.v1".'),
    ("id", "string", False, "xxh3-64 hex of the LF-joined sorted source xxh3 digests."),
    ("name", "string", True, "Human alias given at ingest."),
    ("format", "string", False, "Format registry key, e.g. jira."),
    (
        "sources",
        _SOURCE_TYPE,
        False,
        "Ingested files in ingest order; timestamps ISO 8601 or null; "
        "entries = rows kept after deduplication.",
    ),
    ("first_ts", "string", True, "ISO 8601 timestamp (UTC) of the earliest entry."),
    ("last_ts", "string", True, "ISO 8601 timestamp (UTC) of the latest entry."),
    ("entry_count", "int64", False, "Rows in entries.parquet."),
    ("group_count", "int64", False, "Rows in groups.parquet."),
    ("created_at", "string", False, "ISO 8601 time (UTC) the set was written."),
    ("tool_version", "string", False, "atlas-log-analyzer version that wrote the set."),
    ("schema_version", "string", False, "Store schema version <major>.<minor>."),
    ("signature_version", "int64", False, "Signature algorithm version."),
    ("mask_rules_version", "int64", False, "Built-in masking rules version."),
    ("signature_spec", "string", False, "SignatureSpec.key used for the primary signature."),
    (
        "recipe_key",
        "string",
        False,
        "Recipe the set was signed with: format@version, signature spec, sig<N>, mask<N> "
        "and rules:<xxh3> of extra masking rules (if any), joined by vertical bars. Sets are "
        "comparable when it is equal; derived from the fields above for older manifests.",
    ),
    ("node", "string", True, "Cluster node id the logs come from (NODE1); null when unknown."),
    ("cluster", "string", True, "Name of the instance the node belongs to; null when unknown."),
    (
        "capture",
        "string",
        True,
        "Id of the capture the set was collected in (an export job id or a hash of the "
        "member set ids); sets with the same capture are viewed together.",
    ),
)
"""logset.v1 manifest fields as ``(name, type, nullable, description)``."""


def type_name(t: pa.DataType) -> str:
    """Compact type name, e.g. ``dictionary<string>`` or ``list<struct<value: string>>``."""
    if pa.types.is_dictionary(t):
        return f"dictionary<{type_name(t.value_type)}>"
    if pa.types.is_map(t):
        return f"map<{type_name(t.key_type)}, {type_name(t.item_type)}>"
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return f"list<{type_name(t.value_type)}>"
    if pa.types.is_struct(t):
        inner = ", ".join(
            f"{t.field(i).name}: {type_name(t.field(i).type)}" for i in range(t.num_fields)
        )
        return f"struct<{inner}>"
    if pa.types.is_timestamp(t):
        return f"timestamp[{t.unit}, {t.tz}]" if t.tz else f"timestamp[{t.unit}]"
    return str(t)


def _arrow_fields(schema: pa.Schema) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for field in schema:
        meta = field.metadata or {}
        out.append(
            {
                "name": field.name,
                "type": type_name(field.type),
                "nullable": field.nullable,
                "description": meta.get(b"description", b"").decode(),
            }
        )
    return out


def schema_json(name: str) -> dict[str, object]:
    """JSON-serialisable description of ``entries``/``groups``/``merges``/``logset`` for agents.

    Args:
        name: One of :data:`SCHEMA_NAMES`.

    Returns:
        ``{"schema": "<name>.v1", "version": SCHEMA_VERSION, "fields": [...]}`` where each
        field is ``{"name", "type", "nullable", "description"}``.

    Raises:
        ValueError: ``name`` is unknown.

    """
    match name:
        case "entries":
            fields = _arrow_fields(ENTRIES_SCHEMA)
        case "groups":
            fields = _arrow_fields(GROUPS_SCHEMA)
        case "merges":
            fields = _arrow_fields(MERGES_SCHEMA)
        case "logset":
            fields = [
                {"name": n, "type": t, "nullable": nl, "description": d}
                for n, t, nl, d in LOGSET_FIELDS
            ]
        case _:
            raise ValueError(f"unknown schema {name!r}; expected one of {', '.join(SCHEMA_NAMES)}")
    return {"schema": f"{name}.v1", "version": SCHEMA_VERSION, "fields": fields}
