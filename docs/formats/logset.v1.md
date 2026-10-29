# logset.v1

Schema version 1.0. Generated from `atlas_log_analyzer.store.schema` by `docs/formats/generate.py`; machine-readable form: `atlas-log schema logset`.

File `manifest.json` in `<store>/<id>/`, UTF-8 JSON. Timestamps are ISO 8601 with offset.
The id is `xxh3_64` (hex) of the source files' xxh3 digests, sorted and joined with
`\n`, so ingesting the same files again yields the same id (idempotent).

Readers refuse a manifest whose `schema` (`logset.vN`) or `schema_version` major is
newer than they support. Directories in the store whose names start with `.` are
in-progress writes and are ignored.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `schema` | `string` | no | Always "logset.v1". |
| `id` | `string` | no | xxh3-64 hex of the LF-joined sorted source xxh3 digests. |
| `name` | `string` | yes | Human alias given at ingest. |
| `format` | `string` | no | Format registry key, e.g. jira. |
| `sources` | `list<struct<path: string, size: int64, xxh3: string, first_ts: string, last_ts: string, entries: int64>>` | no | Ingested files in ingest order; timestamps ISO 8601 or null; entries = rows kept after deduplication. |
| `first_ts` | `string` | yes | ISO 8601 timestamp (UTC) of the earliest entry. |
| `last_ts` | `string` | yes | ISO 8601 timestamp (UTC) of the latest entry. |
| `entry_count` | `int64` | no | Rows in entries.parquet. |
| `group_count` | `int64` | no | Rows in groups.parquet. |
| `created_at` | `string` | no | ISO 8601 time (UTC) the set was written. |
| `tool_version` | `string` | no | atlas-log-analyzer version that wrote the set. |
| `schema_version` | `string` | no | Store schema version <major>.<minor>. |
| `signature_version` | `int64` | no | Signature algorithm version. |
| `mask_rules_version` | `int64` | no | Built-in masking rules version. |
| `signature_spec` | `string` | no | SignatureSpec.key used for the primary signature. |
