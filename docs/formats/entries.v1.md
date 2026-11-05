# entries.v1

Schema version 1.1. Generated from `atlas_log_analyzer.store.schema` by `docs/formats/generate.py`; machine-readable form: `atlas-log schema entries`.

File `entries.parquet` in a log-set directory: one row per log entry, deduplicated
on `id`. Parquet, zstd compression, row groups of about 64k rows, rows in ingest order
(sources oldest-first, then file order).

- `level` is the numeric `LogLevel`; filter with `level >= 40` for WARN and above
  (`UNKNOWN` = 0 marks unparsed lines, see `parse_error`).
- Dictionary-encoded columns (`level_name`, `logger`, `source_path`) read as
  categoricals in polars and as `VARCHAR` in DuckDB.
- `context` holds header fields without a dedicated column; the well-known keys
  `user, request_id, session, ip, url, request_url` have their own columns.
- `exception_chain` element: `{type, message, frames: [{location, file, line, module}],
  omitted_frames}`; outermost exception first, then each `Caused by:`.
- `signature` joins to `groups.signature`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | `string` | no | Entry id: xxh3-64 hex of (format, timestamp, thread, logger, raw). |
| `ts` | `timestamp[ms, UTC]` | no | Timestamp in UTC, millisecond precision. |
| `tz_offset` | `int16` | yes | Original UTC offset of the timestamp in minutes. |
| `level` | `uint8` | no | Numeric level: TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60 UNKNOWN=0. |
| `level_name` | `dictionary<string>` | no | Level name: TRACE, DEBUG, INFO, WARN, ERROR, FATAL or UNKNOWN. |
| `thread` | `string` | yes | Thread name. |
| `logger` | `dictionary<string>` | yes | Logger (category) name. |
| `message` | `string` | yes | First line of the message (header remainder). |
| `body` | `string` | yes | Continuation lines joined with LF; empty when single-line. |
| `user` | `string` | yes | User from the request context. |
| `request_id` | `string` | yes | Request id from the request context. |
| `session` | `string` | yes | Session id from the request context. |
| `ip` | `string` | yes | Client IP address(es) from the request context. |
| `url` | `string` | yes | URL(s) from the thread segment. |
| `request_url` | `string` | yes | URL from the request context. |
| `context` | `map<string, string>` | yes | Other format-specific header fields (keys without a dedicated column). |
| `exception_type` | `string` | yes | Type of the outermost exception, if any. |
| `exception_message` | `string` | yes | Message of the outermost exception, if any. |
| `exception_chain` | `list<struct<type: string, message: string, frames: list<struct<location: string, file: string, line: int32, module: string>>, omitted_frames: int32>>` | yes | Parsed exception chain, outermost first; empty list when none. |
| `signature` | `string` | no | Primary signature (16 hex chars); key into groups. |
| `coarse_signature` | `string` | no | Coarse signature (format, level, logger, masked message). |
| `template` | `string` | yes | Masked first message line with <PLACEHOLDERS>. |
| `variables` | `list<string>` | yes | Values replaced by placeholders, in order. |
| `source_path` | `dictionary<string>` | yes | Path of the source file. |
| `source_line` | `uint32` | yes | 1-based line number of the header line. |
| `source_offset` | `uint64` | yes | Byte offset of the header line in the source file. |
| `raw` | `string` | yes | Exact original text (header and continuation lines). |
| `parse_error` | `string` | yes | Set when the header line was not recognised. |

## JSON document `entries.v1`

Entries: `entries` (a page), `show` (one group), `context` (around an entry).

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`entries.v1.schema.json`](entries.v1.schema.json), also `atlas-log schema entries.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"entries.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `count` | `integer` | yes | Rows in items. |
| `items` | `list<EntryRow>` | yes | entries.v1 rows (all columns unless --columns). |
| `total` | `integer` | no | entries: matches before offset/limit. |
| `offset` | `integer` | no | entries: echo of --offset. |
| `truncated` | `boolean` | no | entries: more rows follow. |
| `next_offset` | `integer \| null` | no | entries: where the next page starts; null on the last. |
| `signature` | `string` | no | show: the full signature. |
| `group` | `GroupRow \| null` | no | show: the groups.v1 row. |
| `anchor` | `string` | no | context: the anchor entry id. |
| `thread` | `string \| null` | no | context: the anchor's thread. |
| `any_thread` | `boolean` | no | context: echo of --any-thread. |

### `EntryRow`

Rows hold the columns above (`--columns`/`--brief` select them, so every column is optional in JSON), plus:

| Column | Type | Description |
|---|---|---|
| `body_truncated` | `true` | Only on rows whose raw or body was cut (--max-raw). |
| `raw_chars` | `integer` | Original length of raw (of body without raw); cut rows only. |

### `GroupRow`

One group; the groups.v1 columns (atlas-log schema groups). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `signature` | `string` | Primary signature (16 hex chars). |
| `coarse_signature` | `string` | Coarse signature shared by the group's entries. |
| `template` | `string \| null` | Masked message template. |
| `level` | `integer` | Numeric level (see entries.level). |
| `level_name` | `string` | Level name. |
| `logger` | `string \| null` | Logger name. |
| `exception_type` | `string \| null` | Outermost exception type, if any. |
| `count` | `integer` | Number of entries. |
| `first_seen` | `string (date-time) \| null` | Timestamp of the earliest entry (UTC). |
| `last_seen` | `string (date-time) \| null` | Timestamp of the latest entry (UTC). |
| `user_count` | `integer` | Number of distinct users. |
| `thread_count` | `integer` | Number of distinct threads. |
| `source_count` | `integer` | Number of distinct source files. |
| `top_users` | `list<object> \| null` | Up to 10 most frequent users with counts, descending. |
| `top_threads` | `list<object> \| null` | Up to 10 most frequent threads with counts, descending. |
| `sources` | `list<object> \| null` | All source files with counts, descending. |
| `examples` | `list<string> \| null` | Ids of representative entries. |
| `signatures` | `list<string>` | --coarse only: the member signatures. |
