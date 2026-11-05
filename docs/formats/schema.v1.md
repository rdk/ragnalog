# schema.v1

Schema descriptions: `schema [NAME]`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`schema.v1.schema.json`](schema.v1.schema.json), also `atlas-log schema schema.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"schema.v1"` | yes |  |
| `definitions` | `map<string, object>` | yes | Stored tables: {schema, version, fields}; documents: a JSON Schema. |
| `documents` | `list<string>` | no | Without NAME: the document names. |
