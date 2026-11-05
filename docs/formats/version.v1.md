# version.v1

Tool, schema and signature versions: `version`, `GET /api/version`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`version.v1.schema.json`](version.v1.schema.json), also `atlas-log schema version.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"version.v1"` | yes |  |
| `version` | `string` | yes | atlas-log-analyzer version. |
| `schema_version` | `string` | yes | Store schema version <major>.<minor>. |
| `signature_version` | `integer` | yes | Signature algorithm version. |
| `mask_rules_version` | `integer` | yes | Built-in masking rules version. |
| `python` | `string` | yes | Python version. |
