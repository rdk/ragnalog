# cluster-timeline.v1

Entries per time bucket and node, by level or of one group.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`cluster-timeline.v1.schema.json`](cluster-timeline.v1.schema.json), also `atlas-log schema cluster-timeline.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"cluster-timeline.v1"` | yes |  |
| `bucket` | `string` | yes | Bucket size, e.g. 1h. |
| `buckets` | `list<string>` | yes | Bucket starts, ISO 8601 UTC, contiguous over the window. |
| `nodes` | `map<string, ClusterNodeTimeline>` | yes | Counts by node id. |
| `nodes.*.levels` | `map<string, list<integer>>` | yes | Entries per bucket by ERROR (with FATAL), WARN, INFO, DEBUG (with TRACE), OTHER; only "count" with a signature. |
| `nodes.*.coverage` | `[integer, integer] \| null` | yes | Indices of the buckets of the node's first and last entry; null outside. |
| `signature` | `string` | no | The group counted, when one was asked for. |
