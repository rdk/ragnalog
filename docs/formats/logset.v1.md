# logset.v1

Schema version 1.1. Generated from `ragnalog.store.schema` by `docs/formats/generate.py`; machine-readable form: `ragnalog schema logset`.

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
| `tool_version` | `string` | no | ragnalog version that wrote the set. |
| `schema_version` | `string` | no | Store schema version <major>.<minor>. |
| `signature_version` | `int64` | no | Signature algorithm version. |
| `mask_rules_version` | `int64` | no | Built-in masking rules version. |
| `signature_spec` | `string` | no | SignatureSpec.key used for the primary signature. |
| `recipe_key` | `string` | no | Recipe the set was signed with: format@version, signature spec, sig<N>, mask<N> and rules:<xxh3> of extra masking rules (if any), joined by vertical bars. Sets are comparable when it is equal; derived from the fields above for older manifests. |
| `node` | `string` | yes | Cluster node id the logs come from (NODE1); null when unknown. |
| `cluster` | `string` | yes | Name of the instance the node belongs to; null when unknown. |
| `capture` | `string` | yes | Id of the capture the set was collected in (an export job id or a hash of the member set ids); sets with the same capture are viewed together. |

## JSON document `logset.v1`

A log set's manifest: `ingest`, `set`, `regroup`, `GET /api/sets/{ref}`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`logset.v1.schema.json`](logset.v1.schema.json), also `ragnalog schema logset.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"logset.v1"` | yes |  |
| `id` | `string` | yes | xxh3-64 hex of the LF-joined sorted source xxh3 digests. |
| `name` | `string \| null` | yes | Human alias given at ingest. |
| `format` | `string` | yes | Format registry key, e.g. jira. |
| `sources` | `list<Source>` | yes | Ingested files in ingest order; timestamps ISO 8601 or null; entries = rows kept after deduplication. |
| `sources[].path` | `string` | yes | Path of the file as given at ingest. |
| `sources[].size` | `integer` | yes | Size in bytes. |
| `sources[].xxh3` | `string` | yes | xxh3-64 hex digest of the content. |
| `sources[].first_ts` | `string \| null` | yes | ISO 8601 time of the file's earliest entry. |
| `sources[].last_ts` | `string \| null` | yes | ISO 8601 time of the file's latest entry. |
| `sources[].entries` | `integer` | yes | Entries of the file kept after deduplication. |
| `first_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the earliest entry. |
| `last_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the latest entry. |
| `entry_count` | `integer` | yes | Rows in entries.parquet. |
| `group_count` | `integer` | yes | Rows in groups.parquet. |
| `created_at` | `string` | yes | ISO 8601 time (UTC) the set was written. |
| `tool_version` | `string` | yes | ragnalog version that wrote the set. |
| `schema_version` | `string` | yes | Store schema version <major>.<minor>. |
| `signature_version` | `integer` | yes | Signature algorithm version. |
| `mask_rules_version` | `integer` | yes | Built-in masking rules version. |
| `signature_spec` | `string` | yes | SignatureSpec.key used for the primary signature. |
| `recipe_key` | `string` | no | Recipe the set was signed with: format@version, signature spec, sig<N>, mask<N> and rules:<xxh3> of extra masking rules (if any), joined by vertical bars. Sets are comparable when it is equal; derived from the fields above for older manifests. Missing only from older tool versions. |
| `node` | `string \| null` | no | Cluster node id the logs come from (NODE1); null when unknown. Missing only from older tool versions. |
| `cluster` | `string \| null` | no | Name of the instance the node belongs to; null when unknown. Missing only from older tool versions. |
| `capture` | `string \| null` | no | Id of the capture the set was collected in (an export job id or a hash of the member set ids); sets with the same capture are viewed together. Missing only from older tool versions. |
| `ingest` | `IngestInfo` | no | Only from ingest (CLI and job result). |
| `ingest.parsed_entries` | `integer` | yes | Entries parsed from the files. |
| `ingest.parse_errors` | `integer` | yes | Entries whose header was not recognised. |
| `ingest.elapsed_s` | `number` | yes | Seconds the ingest took. |
| `ingest.existing` | `boolean` | yes | The set existed already and was reused. |
| `ingest.skipped` | `list<string>` | yes | Files found in a directory that are not in the format. |
| `ingest.warnings` | `list<string>` | yes | E.g. that --name was ignored for an existing set. |
| `merges` | `list<MergeRow>` | no | Drain3 merge proposals (ingest --mine). |
| `summary` | `SetSummary` | no | Only from set and GET /api/sets/{ref}. |
| `summary.errors` | `integer` | yes | Entries at ERROR or FATAL. |
| `summary.warnings` | `integer` | yes | Entries at WARN. |
| `summary.error_groups` | `integer` | yes | Groups at ERROR or FATAL. |

### `MergeRow`

One Drain3 merge proposal (ragnalog schema merges). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `cluster_id` | `integer` | Drain3 cluster id. |
| `drain_template` | `string \| null` | Template proposed by Drain3 (<*> wildcards). |
| `signatures` | `list<string> \| null` | Signatures proposed to be merged. |
