# version.v1

Tool, schema and signature versions: `version`, `GET /api/version`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`version.v1.schema.json`](version.v1.schema.json), also `ragnalog schema version.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"version.v1"` | yes |  |
| `version` | `string` | yes | ragnalog version. |
| `schema_version` | `string` | yes | Store schema version <major>.<minor>. |
| `signature_version` | `integer` | yes | Signature algorithm version. |
| `mask_rules_version` | `integer` | yes | Built-in masking rules version. |
| `python` | `string` | yes | Python version. |
| `build` | `VersionBuild` | yes | The build: number, git hash and build time. |
| `build.number` | `integer \| null` | yes | Build number: the commit count of the built commit (null: unknown). |
| `build.git` | `string \| null` | yes | The built commit's hash, 7 characters (null: unknown). |
| `build.time` | `string \| null` | yes | When the container image was built, ISO 8601 UTC (null: not an image). |
| `build.source` | `"checkout" \| "image" \| "unknown"` | yes | image: recorded when the image was built; checkout: a git checkout's HEAD; unknown. |
