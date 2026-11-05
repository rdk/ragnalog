# query.v1

Rows of a DuckDB query: `query`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`query.v1.schema.json`](query.v1.schema.json), also `atlas-log schema query.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"query.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `columns` | `list<string>` | yes | Result columns, in order. |
| `count` | `integer` | yes | Rows in items. |
| `total` | `integer` | yes | Equals count (never truncated). |
| `truncated` | `boolean` | yes | Always false. |
| `items` | `list<object>` | yes | One object per result row. |
