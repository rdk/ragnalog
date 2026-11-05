# group.v1

One group with its histogram and first entries: `GET /api/sets/{ref}/groups/{sig}`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`group.v1.schema.json`](group.v1.schema.json), also `atlas-log schema group.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"group.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `group` | `GroupRow` | yes | The groups.v1 row. |
| `histogram` | `list<BucketCount>` | yes | Entries per hour; [] for coarse groups. |
| `histogram[].bucket` | `string` | yes | Bucket start, ISO 8601 UTC. |
| `histogram[].count` | `integer` | yes | Entries in the bucket. |
| `count` | `integer` | yes | Rows in items. |
| `items` | `list<EntryRow>` | yes | The group's first entries, by time. |

### `EntryRow`

One entry; the entries.v1 columns (atlas-log schema entries). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `id` | `string` | Entry id: xxh3-64 hex of (format, timestamp, thread, logger, raw). |
| `ts` | `string (date-time)` | Timestamp in UTC, millisecond precision. |
| `tz_offset` | `integer \| null` | Original UTC offset of the timestamp in minutes. |
| `level` | `integer` | Numeric level: TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60 UNKNOWN=0. |
| `level_name` | `string` | Level name: TRACE, DEBUG, INFO, WARN, ERROR, FATAL or UNKNOWN. |
| `thread` | `string \| null` | Thread name. |
| `logger` | `string \| null` | Logger (category) name. |
| `message` | `string \| null` | First line of the message (header remainder). |
| `body` | `string \| null` | Continuation lines joined with LF; empty when single-line. |
| `user` | `string \| null` | User from the request context. |
| `request_id` | `string \| null` | Request id from the request context. |
| `session` | `string \| null` | Session id from the request context. |
| `ip` | `string \| null` | Client IP address(es) from the request context. |
| `url` | `string \| null` | URL(s) from the thread segment. |
| `request_url` | `string \| null` | URL from the request context. |
| `context` | `map<string, string> \| null` | Other format-specific header fields (keys without a dedicated column); preamble: banner lines attached before the entry (their raw text, LF-joined), only on entries that have them. |
| `exception_type` | `string \| null` | Type of the outermost exception, if any. |
| `exception_message` | `string \| null` | Message of the outermost exception, if any. |
| `exception_chain` | `list<object> \| null` | Parsed exception chain, outermost first; empty list when none. |
| `signature` | `string` | Primary signature (16 hex chars); key into groups. |
| `coarse_signature` | `string` | Coarse signature (format, level, logger, masked message). |
| `template` | `string \| null` | Masked first message line with <PLACEHOLDERS>. |
| `variables` | `list<string> \| null` | Values replaced by placeholders, in order. |
| `source_path` | `string \| null` | Path of the source file. |
| `source_line` | `integer \| null` | 1-based line number of the header line. |
| `source_offset` | `integer \| null` | Byte offset of the header line in the source file. |
| `raw` | `string \| null` | Exact original text (header and continuation lines). |
| `parse_error` | `string \| null` | Set when the header line was not recognised. |
| `body_truncated` | `true` | Only on rows whose raw or body was cut (--max-raw). |
| `raw_chars` | `integer` | Original length of raw (of body without raw); cut rows only. |
| `node` | `string` | Entries of a capture: the entry's node. |
| `set` | `string` | Entries of a capture: the id of the node's set. |

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
