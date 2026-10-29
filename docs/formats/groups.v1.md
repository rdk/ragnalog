# groups.v1

Schema version 1.0. Generated from `atlas_log_analyzer.store.schema` by `docs/formats/generate.py`; machine-readable form: `atlas-log schema groups`.

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
