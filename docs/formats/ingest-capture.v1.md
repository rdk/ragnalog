# ingest-capture.v1

A split ingest, one set per node: `ingest` (export ZIP, --split), the job capture.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`ingest-capture.v1.schema.json`](ingest-capture.v1.schema.json), also `ragnalog schema ingest-capture.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"ingest-capture.v1"` | yes |  |
| `capture` | `CaptureItem` | yes | The capture after the ingest (all its member sets). |
| `capture.capture` | `string` | yes | Capture id: an export's jobId or a hash of the set ids. |
| `capture.cluster` | `string \| null` | yes | Cluster of the member sets. |
| `capture.first_ts` | `string \| null` | yes | Earliest first entry of the members. |
| `capture.last_ts` | `string \| null` | yes | Latest last entry of the members. |
| `capture.entry_count` | `integer` | yes | Entries of all members. |
| `capture.nodes` | `list<CaptureNode>` | yes | Member sets, sorted by node. |
| `capture.nodes[].node` | `string` | yes | Node id of the set (its name or id when it has none). |
| `capture.nodes[].id` | `string` | yes | Set id. |
| `capture.nodes[].name` | `string \| null` | yes | Set name. |
| `capture.nodes[].first_ts` | `string \| null` | no | First entry, as in the manifest. Not with --brief. |
| `capture.nodes[].last_ts` | `string \| null` | no | Last entry, as in the manifest. Not with --brief. |
| `capture.nodes[].entry_count` | `integer` | no | Entries of the set. Not with --brief. |
| `sets` | `list<logset.v1>` | yes | One logset.v1 with ingest per node of this ingest. |
| `warnings` | `list<string>` | yes | job.json and split notes, then each set's ingest.warnings prefixed with its node. |

### `MergeRow`

One Drain3 merge proposal (ragnalog schema merges). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `cluster_id` | `integer` | Drain3 cluster id. |
| `drain_template` | `string \| null` | Template proposed by Drain3 (<*> wildcards). |
| `signatures` | `list<string> \| null` | Signatures proposed to be merged. |
