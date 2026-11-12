# captures.v1

The captures of the data directory, newest last_ts first: `captures`, `GET /api/captures`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`captures.v1.schema.json`](captures.v1.schema.json), also `atlas-log schema captures.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"captures.v1"` | yes |  |
| `count` | `integer` | yes | Number of items. |
| `items` | `list<CaptureItem>` | yes | The captures; with --brief only node, id, name per node. |
| `items[].capture` | `string` | yes | Capture id: an export's jobId or a hash of the set ids. |
| `items[].cluster` | `string \| null` | yes | Cluster of the member sets. |
| `items[].first_ts` | `string \| null` | yes | Earliest first entry of the members. |
| `items[].last_ts` | `string \| null` | yes | Latest last entry of the members. |
| `items[].entry_count` | `integer` | yes | Entries of all members. |
| `items[].nodes` | `list<CaptureNode>` | yes | Member sets, sorted by node. |
| `items[].nodes[].node` | `string` | yes | Node id of the set (its name or id when it has none). |
| `items[].nodes[].id` | `string` | yes | Set id. |
| `items[].nodes[].name` | `string \| null` | yes | Set name. |
| `items[].nodes[].first_ts` | `string \| null` | no | First entry, as in the manifest. Not with --brief. |
| `items[].nodes[].last_ts` | `string \| null` | no | Last entry, as in the manifest. Not with --brief. |
| `items[].nodes[].entry_count` | `integer` | no | Entries of the set. Not with --brief. |
