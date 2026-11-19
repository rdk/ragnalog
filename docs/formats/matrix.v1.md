# matrix.v1

Groups by node: where each group occurs, how often, and which node saw it first.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`matrix.v1.schema.json`](matrix.v1.schema.json), also `ragnalog schema matrix.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"matrix.v1"` | yes |  |
| `window` | `ClusterWindow` | yes | The window of the counts. |
| `window.mode` | `"explicit" \| "overlap" \| "union"` | yes | overlap: latest first entry to earliest last entry of the nodes; union: earliest first to latest last; explicit: since/until given. |
| `window.since` | `string` | yes | Start, ISO 8601 UTC. |
| `window.until` | `string` | yes | End, ISO 8601 UTC. |
| `nodes` | `list<string>` | yes | Node ids, in order. |
| `count` | `integer` | yes | Rows in items. |
| `total` | `integer` | yes | Matching groups before offset/limit. |
| `offset` | `integer` | yes | Echo of offset. |
| `truncated` | `boolean` | yes | More rows follow. |
| `items` | `list<MatrixRow>` | yes | Rows by the sort (count, skew or lead), descending. |
| `items[].signature` | `string` | yes | Group signature. |
| `items[].template` | `string \| null` | yes | Masked message template. |
| `items[].level` | `integer` | yes | Numeric level. |
| `items[].level_name` | `string` | yes | Level name. |
| `items[].logger` | `string \| null` | yes | Logger. |
| `items[].exception_type` | `string \| null` | yes | Outermost exception type. |
| `items[].total` | `integer` | yes | Entries on all nodes in the window. |
| `items[].per_node` | `map<string, MatrixNodeCount>` | yes | Every node of the capture by id. |
| `items[].per_node.*.count` | `integer` | yes | Entries; 0 when absent. |
| `items[].per_node.*.rate_per_hour` | `number` | yes | Entries per hour of the node's coverage. |
| `items[].per_node.*.first_seen` | `string \| null` | yes | First entry; null when absent. |
| `items[].per_node.*.last_seen` | `string \| null` | yes | Last entry; null when absent. |
| `items[].spread` | `"all" \| "only" \| "skewed"` | yes | only: on one node; skewed: missing from a node that has logs in the window, or skew >= skew_min; all: otherwise. |
| `items[].skew` | `number \| null` | yes | Max/min, over the nodes that have the group, of count / the node's entries in the window; null when only. |
| `items[].first_node` | `string \| null` | yes | Node with the earliest first_seen. |
| `items[].lead_ms` | `integer \| null` | yes | first_seen of the next node minus that of first_node; null when only. |
