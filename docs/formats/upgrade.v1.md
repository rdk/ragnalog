# upgrade.v1

Stale sets rebuilt at the current recipe: `upgrade [SET...] [--all]`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`upgrade.v1.schema.json`](upgrade.v1.schema.json), also `atlas-log schema upgrade.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"upgrade.v1"` | yes |  |
| `count` | `integer` | yes | Number of items. |
| `upgraded` | `integer` | yes | Items with status upgraded. |
| `items` | `list<UpgradeItem>` | yes | One per set, in the order of the SET arguments. |
| `items[].id` | `string` | yes | xxh3-64 hex of the LF-joined sorted source xxh3 digests. |
| `items[].name` | `string \| null` | yes | Human alias given at ingest. |
| `items[].format` | `string` | yes | Format registry key, e.g. jira. |
| `items[].sources` | `list<Source>` | yes | Ingested files in ingest order; timestamps ISO 8601 or null; entries = rows kept after deduplication. |
| `items[].sources[].path` | `string` | yes | Path of the file as given at ingest. |
| `items[].sources[].size` | `integer` | yes | Size in bytes. |
| `items[].sources[].xxh3` | `string` | yes | xxh3-64 hex digest of the content. |
| `items[].sources[].first_ts` | `string \| null` | yes | ISO 8601 time of the file's earliest entry. |
| `items[].sources[].last_ts` | `string \| null` | yes | ISO 8601 time of the file's latest entry. |
| `items[].sources[].entries` | `integer` | yes | Entries of the file kept after deduplication. |
| `items[].first_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the earliest entry. |
| `items[].last_ts` | `string \| null` | yes | ISO 8601 timestamp (UTC) of the latest entry. |
| `items[].entry_count` | `integer` | yes | Rows in entries.parquet. |
| `items[].group_count` | `integer` | yes | Rows in groups.parquet. |
| `items[].created_at` | `string` | yes | ISO 8601 time (UTC) the set was written. |
| `items[].tool_version` | `string` | yes | atlas-log-analyzer version that wrote the set. |
| `items[].schema_version` | `string` | yes | Store schema version <major>.<minor>. |
| `items[].signature_version` | `integer` | yes | Signature algorithm version. |
| `items[].mask_rules_version` | `integer` | yes | Built-in masking rules version. |
| `items[].signature_spec` | `string` | yes | SignatureSpec.key used for the primary signature. |
| `items[].recipe_key` | `string` | yes | Recipe key after the call. |
| `items[].status` | `"skipped" \| "upgraded"` | yes | What happened to the set. |
| `items[].reason` | `string \| null` | yes | null when upgraded; "current" or the unknown format/spec when skipped. |
| `items[].previous_recipe_key` | `string` | yes | Recipe key before the call. |
