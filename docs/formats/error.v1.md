# error.v1

A failure (CLI: on stdout in json/jsonl mode, with exit code 1-3; API: 4xx/5xx).

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`error.v1.schema.json`](error.v1.schema.json), also `atlas-log schema error.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"error.v1"` | yes |  |
| `error` | `ErrorInfo` | yes | The error. |
| `error.code` | `string` | yes | Stable error code, e.g. usage, invalid_value, not_found. |
| `error.message` | `string` | yes | What went wrong. |
| `error.hint` | `string` | no | Suggested next step. |
| `error.reason` | `string` | no | The same in the CLI and the API where their codes differ, e.g. set_not_found (CLI store_error, API not_found); see service/errors.py REASONS. |
| `error.store` | `string` | no | The data directory searched (CLI, unknown set). |
| `error.candidates` | `list<string>` | no | Matching signatures of an ambiguous prefix (up to 5). |
