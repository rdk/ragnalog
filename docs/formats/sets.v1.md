# sets.v1

The log sets of the data directory, newest first: `sets`, `GET /api/sets`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`sets.v1.schema.json`](sets.v1.schema.json), also `atlas-log schema sets.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"sets.v1"` | yes |  |
| `store` | `string` | yes | CLI: the data directory path; API: the data directory's name. |
| `count` | `integer` | yes | Number of items. |
| `items` | `list<SetItem>` | yes | The sets; with --brief only id, name, first_ts, last_ts, counts, summary, stale, node, capture. |
| `items[].id` | `string` | yes | xxh3-64 hex of the LF-joined sorted source xxh3 digests. |
| `items[].name` | `string \| null` | yes | Human alias given at ingest. |
| `items[].first_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the earliest entry. |
| `items[].last_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the latest entry. |
| `items[].entry_count` | `integer` | yes | Rows in entries.parquet. |
| `items[].group_count` | `integer` | yes | Rows in groups.parquet. |
| `items[].summary` | `SetSummary \| null` | yes | null when the set cannot be read. |
| `items[].summary.errors` | `integer` | yes | Entries at ERROR or FATAL. |
| `items[].summary.warnings` | `integer` | yes | Entries at WARN. |
| `items[].summary.error_groups` | `integer` | yes | Groups at ERROR or FATAL. |
| `items[].format` | `string` | no | Not with --brief. |
| `items[].sources` | `list<Source>` | no | Not with --brief. |
| `items[].sources[].path` | `string` | yes | Path of the file as given at ingest. |
| `items[].sources[].size` | `integer` | yes | Size in bytes. |
| `items[].sources[].xxh3` | `string` | yes | xxh3-64 hex digest of the content. |
| `items[].sources[].first_ts` | `string \| null` | yes | ISO 8601 time of the file's earliest entry. |
| `items[].sources[].last_ts` | `string \| null` | yes | ISO 8601 time of the file's latest entry. |
| `items[].sources[].entries` | `integer` | yes | Entries of the file kept after deduplication. |
| `items[].created_at` | `string` | no | Not with --brief. |
| `items[].tool_version` | `string` | no | Not with --brief. |
| `items[].schema_version` | `string` | no | Not with --brief. |
| `items[].signature_version` | `integer` | no | Not with --brief. |
| `items[].mask_rules_version` | `integer` | no | Not with --brief. |
| `items[].signature_spec` | `string` | no | Not with --brief. |
| `items[].recipe_key` | `string` | no | Recipe the set was signed with: format@version, signature spec, sig<N>, mask<N> and rules:<xxh3> of extra masking rules (if any), joined by vertical bars. Sets are comparable when it is equal; derived from the fields above for older manifests. Not with --brief. |
| `items[].stale` | `boolean` | no | recipe_key differs from the recipe this version signs the set's format and spec with: not comparable with new sets, no triage baseline; rebuild with upgrade. Missing only from older tool versions. |
| `items[].node` | `string \| null` | no | Cluster node id the logs come from (NODE1); null when unknown. Missing only from older tool versions. |
| `items[].cluster` | `string \| null` | no | Name of the instance the node belongs to; null when unknown. Not with --brief. |
| `items[].capture` | `string \| null` | no | Id of the capture the set was collected in (an export job id or a hash of the member set ids); sets with the same capture are viewed together. Missing only from older tool versions. |
