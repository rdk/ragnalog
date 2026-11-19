# capture.v1

The nodes of a capture side by side: coverage, gaps, volume, events, verdicts.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`capture.v1.schema.json`](capture.v1.schema.json), also `ragnalog schema capture.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"capture.v1"` | yes |  |
| `capture` | `string \| null` | yes | Capture id; null when not known. |
| `cluster` | `string \| null` | yes | Cluster name. |
| `window` | `ClusterWindow` | yes | The window of the counts. |
| `window.mode` | `"explicit" \| "overlap" \| "union"` | yes | overlap: latest first entry to earliest last entry of the nodes; union: earliest first to latest last; explicit: since/until given. |
| `window.since` | `string` | yes | Start, ISO 8601 UTC. |
| `window.until` | `string` | yes | End, ISO 8601 UTC. |
| `nodes` | `list<ClusterNodeSummary>` | yes | The nodes, sorted by node id. |
| `nodes[].node` | `string` | yes | Node id. |
| `nodes[].set` | `ClusterSetRef` | yes | The node's log set. |
| `nodes[].set.id` | `string` | yes | Set id. |
| `nodes[].set.name` | `string \| null` | yes | Set name. |
| `nodes[].first_ts` | `string \| null` | yes | The set's first entry (whole set). |
| `nodes[].last_ts` | `string \| null` | yes | The set's last entry (whole set). |
| `nodes[].coverage` | `ClusterSpan \| null` | yes | The node's logs inside the window; null when it has none there. |
| `nodes[].coverage.since` | `string` | yes | ISO 8601 UTC. |
| `nodes[].coverage.until` | `string` | yes | ISO 8601 UTC. |
| `nodes[].gaps` | `list<[string, string]>` | yes | [since, until] of runs of at least 15 empty minutes inside the coverage, oldest first, at most 100. |
| `nodes[].entries` | `integer` | yes | Entries in the window. |
| `nodes[].errors` | `integer` | yes | ERROR and FATAL entries in the window. |
| `nodes[].warnings` | `integer` | yes | WARN entries in the window. |
| `nodes[].errors_per_hour` | `number` | yes | errors per hour of coverage. |
| `nodes[].warnings_per_hour` | `number` | yes | warnings per hour of coverage. |
| `nodes[].top_error` | `ClusterTopError \| null` | yes | Largest error group; null without errors. |
| `nodes[].top_error.signature` | `string` | yes | Group signature. |
| `nodes[].top_error.template` | `string \| null` | yes | Group template. |
| `nodes[].top_error.count` | `integer` | yes | Its entries on the node in the window. |
| `nodes[].verdict` | `"clean" \| "new_errors" \| "new_exceptions" \| "new_warnings" \| "no_baseline" \| null` | yes | Triage against the node's own baseline; null unless asked for. |
| `nodes[].baseline` | `ClusterBaseline \| null` | yes | The baseline of verdict; null without one or unless asked. |
| `nodes[].baseline.id` | `string` | yes | Baseline set id. |
| `nodes[].baseline.name` | `string \| null` | yes | Baseline set name. |
| `nodes[].baseline.reason` | `string \| null` | yes | Why it was picked, in one line. |
| `nodes[].events` | `map<string, integer>` | yes | Events in the window by kind. |
| `skew` | `list<ClusterSkew>` | yes | Clock offset estimates from simultaneous event pairs (index, re-index and cluster lock events less than 5 s apart); [] when there are none. |
| `skew[].a` | `string` | yes | Node id. |
| `skew[].b` | `string` | yes | Node id, after a in node order. |
| `skew[].offset_ms` | `integer` | yes | Median of ts on b minus ts on a over the pairs. |
| `skew[].pairs` | `integer` | yes | Event pairs the estimate rests on. |
