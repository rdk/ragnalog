# job.v1

A background ingest: `POST /api/ingest`, `GET /api/jobs/{id}`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`job.v1.schema.json`](job.v1.schema.json), also `atlas-log schema job.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"job.v1"` | yes |  |
| `id` | `string` | yes | Job id. |
| `kind` | `"ingest"` | yes | Always ingest. |
| `state` | `"cancelled" \| "failed" \| "queued" \| "running" \| "succeeded"` | yes | queued, running, then succeeded, failed or cancelled. |
| `name` | `string \| null` | yes | Set name requested. |
| `files` | `list<string>` | yes | Uploaded file names. |
| `options` | `object` | yes | Ingest options (format, frames, mine, overwrite, force, node, cluster, capture, split). |
| `created_at` | `string` | yes | ISO 8601 UTC. |
| `started_at` | `string \| null` | yes | ISO 8601 UTC. |
| `finished_at` | `string \| null` | yes | ISO 8601 UTC. |
| `progress` | `JobProgress` | yes | Progress so far. |
| `progress.entries` | `integer` | yes | Entries parsed. |
| `progress.bytes_read` | `integer` | yes | Bytes read. |
| `progress.bytes_total` | `integer` | yes | Bytes uploaded. |
| `progress.fraction` | `number` | yes | 0..1. |
| `progress.current_file` | `string \| null` | yes | File being read. |
| `result` | `logset.v1 \| null` | yes | The manifest once succeeded; of a split ingest, its first set's. |
| `error` | `ErrorInfo \| null` | yes | Set once failed. |
| `error.code` | `string` | yes | Stable error code, e.g. usage, invalid_value, not_found. |
| `error.message` | `string` | yes | What went wrong. |
| `error.hint` | `string` | no | Suggested next step. |
| `error.reason` | `string` | no | The same in the CLI and the API where their codes differ, e.g. set_not_found (CLI store_error, API not_found); see service/errors.py REASONS. |
| `error.store` | `string` | no | The data directory searched (CLI, unknown set). |
| `error.candidates` | `list<string>` | no | Matching signatures of an ambiguous prefix (up to 5). |
| `capture` | `ingest-capture.v1` | no | A split ingest once succeeded: the ingest-capture.v1 document. |

### `MergeRow`

One Drain3 merge proposal (atlas-log schema merges). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `cluster_id` | `integer` | Drain3 cluster id. |
| `drain_template` | `string \| null` | Template proposed by Drain3 (<*> wildcards). |
| `signatures` | `list<string> \| null` | Signatures proposed to be merged. |
