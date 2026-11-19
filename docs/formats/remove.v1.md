# remove.v1

A removed set: `remove`, `DELETE /api/sets/{ref}`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`remove.v1.schema.json`](remove.v1.schema.json), also `ragnalog schema remove.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"remove.v1"` | yes |  |
| `removed` | `string` | yes | Id of the removed set. |
| `name` | `string \| null` | yes | Its name. |
