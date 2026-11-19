# jobs.v1

Jobs of this server process, newest first: `GET /api/jobs`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`jobs.v1.schema.json`](jobs.v1.schema.json), also `ragnalog schema jobs.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"jobs.v1"` | yes |  |
| `count` | `integer` | yes | Number of items. |
| `items` | `list<job.v1>` | yes | job.v1 documents. |

### `MergeRow`

One Drain3 merge proposal (ragnalog schema merges). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `cluster_id` | `integer` | Drain3 cluster id. |
| `drain_template` | `string \| null` | Template proposed by Drain3 (<*> wildcards). |
| `signatures` | `list<string> \| null` | Signatures proposed to be merged. |
