# report-entries.v1

A group's examples in the report.v1 entry shape: `GET .../groups/{sig}/examples`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`report-entries.v1.schema.json`](report-entries.v1.schema.json), also `ragnalog schema report-entries.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"report-entries.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `signature` | `string` | yes | Group signature. |
| `items` | `map<string, object>` | yes | Entries by id (report.v1 entries). |
