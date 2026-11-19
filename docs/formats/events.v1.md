# events.v1

What every node did: restarts, health checks, index and cache events, jobs.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`events.v1.schema.json`](events.v1.schema.json), also `ragnalog schema events.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"events.v1"` | yes |  |
| `rules_version` | `integer` | yes | EVENT_RULES_VERSION of the rules used. |
| `count` | `integer` | yes | Rows in items. |
| `items` | `list<ClusterEvent>` | yes | Events by time, then node. |
| `items[].node` | `string` | yes | Node id. |
| `items[].ts` | `string` | yes | Time of its first entry. |
| `items[].last_ts` | `string` | yes | Time of its last entry (= ts when count is 1). |
| `items[].kind` | `string` | yes | start, stop, health_failed, cache_replication, … job_run. |
| `items[].subject` | `string \| null` | yes | What it is about: a node id, a health check, a lock, a job key. |
| `items[].subject_node` | `string \| null` | yes | subject when it is a node of the capture. |
| `items[].signature` | `string` | yes | Group signature of the first entry. |
| `items[].entry_id` | `string` | yes | Id of the first entry (in the node's set). |
| `items[].text` | `string` | yes | Message of the first entry (its group's template when the message is blank), at most 300 characters. |
| `items[].count` | `integer` | yes | Entries folded into the event: same node, kind and subject within 60 s of the first (job_run 120 s, start and stop 10 min); health_failed and job_run also fold repeats at most 2 h (job_run 30 min) after the previous one. |
| `pairs` | `list<EventPair>` | yes | Kind pairs on different nodes with lift >= 3, by count; without job_run (see interactions.concurrent_jobs); at most 50. Computed before scheduled repeats are folded. |
| `pairs[].a` | `EventSide` | yes | The earlier event. |
| `pairs[].a.node` | `string` | yes | Node id. |
| `pairs[].a.kind` | `string` | yes | Event kind. |
| `pairs[].b` | `EventSide` | yes | The later event, on another node. |
| `pairs[].b.node` | `string` | yes | Node id. |
| `pairs[].b.kind` | `string` | yes | Event kind. |
| `pairs[].count` | `integer` | yes | Events a followed by an event b. |
| `pairs[].of` | `integer` | yes | Events a in the window. |
| `pairs[].median_lag_ms` | `integer` | yes | Median time from a to the first b after it. |
| `pairs[].lift` | `number` | yes | count / expected, expected = of x min(1, events b x 60 s / window). |
| `pairs[].examples` | `list<[string, string]>` | yes | Up to 3 [ts a, ts b]. |
