# query.v1

Rows of a DuckDB query: `query`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`query.v1.schema.json`](query.v1.schema.json), also `ragnalog schema query.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"query.v1"` | yes |  |
| `logset` | `string` | yes | Set id; capture:<id> for a query over all nodes of a capture. |
| `columns` | `list<string>` | yes | Result columns, in order. |
| `count` | `integer` | yes | Rows in items. |
| `total` | `integer \| null` | yes | Rows of the result: equals count; null when the API cut it (truncated). |
| `truncated` | `boolean` | yes | True when the API cut the result at its row cap (more rows follow); CLI: never. |
| `items` | `list<object>` | yes | One object per result row. |
| `baseline` | `string \| null` | no | With --baseline: its set id (capture:<id> for a capture), whose views are baseline_entries, baseline_groups (and baseline_merges). |
