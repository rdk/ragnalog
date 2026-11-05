# groups.v1

Schema version 1.1. Generated from `atlas_log_analyzer.store.schema` by `docs/formats/generate.py`; machine-readable form: `atlas-log schema groups`.

File `groups.parquet`: one row per primary signature, sorted by `count` descending.
Computed at ingest (or by `regroup`) from the same entries as `entries.parquet`;
`sum(count)` equals the number of entries.

`top_users`, `top_threads` and `sources` are lists of `{value, count}` sorted by count
descending; `examples` are entry ids (join to `entries.id`).

`LogSet.groups(coarse=True)` aggregates these rows by `coarse_signature`; it returns the
same columns (with `signature` = `coarse_signature`) plus `signatures` (member list), and
`user_count`/`thread_count` become lower bounds (maximum over members).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `signature` | `string` | no | Primary signature (16 hex chars). |
| `coarse_signature` | `string` | no | Coarse signature shared by the group's entries. |
| `template` | `string` | yes | Masked message template. |
| `level` | `uint8` | no | Numeric level (see entries.level). |
| `level_name` | `dictionary<string>` | no | Level name. |
| `logger` | `dictionary<string>` | yes | Logger name. |
| `exception_type` | `string` | yes | Outermost exception type, if any. |
| `count` | `int64` | no | Number of entries. |
| `first_seen` | `timestamp[ms, UTC]` | yes | Timestamp of the earliest entry (UTC). |
| `last_seen` | `timestamp[ms, UTC]` | yes | Timestamp of the latest entry (UTC). |
| `user_count` | `int32` | no | Number of distinct users. |
| `thread_count` | `int32` | no | Number of distinct threads. |
| `source_count` | `int32` | no | Number of distinct source files. |
| `top_users` | `list<struct<value: string, count: int64>>` | yes | Up to 10 most frequent users with counts, descending. |
| `top_threads` | `list<struct<value: string, count: int64>>` | yes | Up to 10 most frequent threads with counts, descending. |
| `sources` | `list<struct<value: string, count: int64>>` | yes | All source files with counts, descending. |
| `examples` | `list<string>` | yes | Ids of representative entries. |

## JSON document `groups.v1`

One page of groups: `groups`, `GET /api/sets/{ref}/groups`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`groups.v1.schema.json`](groups.v1.schema.json), also `atlas-log schema groups.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"groups.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `count` | `integer` | yes | Rows in items. |
| `total` | `integer` | yes | Matching groups before offset/limit. |
| `offset` | `integer` | yes | Echo of --offset. |
| `truncated` | `boolean` | yes | More rows follow (offset + count < total). |
| `items` | `list<GroupRow>` | yes | groups.v1 rows (all columns unless --columns). |

### `GroupRow`

Rows hold the columns above (`--columns`/`--brief` select them, so every column is optional in JSON), plus:

| Column | Type | Description |
|---|---|---|
| `signatures` | `list<string>` | --coarse only: the member signatures. |
