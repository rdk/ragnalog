# stats.v1

Summary statistics of a set: `stats`, `GET /api/sets/{ref}/stats`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`stats.v1.schema.json`](stats.v1.schema.json), also `ragnalog schema stats.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"stats.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `id` | `string` | yes | Set id. |
| `name` | `string \| null` | yes | Set name. |
| `format` | `string` | yes | Format name. |
| `entry_count` | `integer` | yes | Entries. |
| `group_count` | `integer` | yes | Groups. |
| `parse_errors` | `integer` | yes | Entries whose header was not recognised. |
| `levels` | `map<string, integer>` | yes | Entries per level name. |
| `loggers` | `list<LoggerCount>` | yes | Top 20 loggers. |
| `loggers[].logger` | `string \| null` | yes | Logger name. |
| `loggers[].count` | `integer` | yes | Entries. |
| `hours` | `list<HourCount>` | yes | Entries per hour (UTC). |
| `hours[].hour` | `string` | yes | Hour start, ISO 8601 UTC. |
| `hours[].count` | `integer` | yes | Entries. |
| `time_range` | `TimeRange` | yes | Time of the first and the last entry. |
| `time_range.first` | `string \| null` | yes | First entry, ISO 8601 UTC. |
| `time_range.last` | `string \| null` | yes | Last entry, ISO 8601 UTC. |
| `sources` | `list<Source>` | yes | The ingested files. |
| `sources[].path` | `string` | yes | Path of the file as given at ingest. |
| `sources[].size` | `integer` | yes | Size in bytes. |
| `sources[].xxh3` | `string` | yes | xxh3-64 hex digest of the content. |
| `sources[].first_ts` | `string \| null` | yes | ISO 8601 time of the file's earliest entry. |
| `sources[].last_ts` | `string \| null` | yes | ISO 8601 time of the file's latest entry. |
| `sources[].entries` | `integer` | yes | Entries of the file kept after deduplication. |
