# interactions.v1

How the nodes affect each other: mentions, co-bursts, concurrent jobs, sessions.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`interactions.v1.schema.json`](interactions.v1.schema.json), also `ragnalog schema interactions.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"interactions.v1"` | yes |  |
| `delta_s` | `integer` | yes | Echo of delta_s (co-bursts). |
| `mentions` | `list<MentionEdge>` | yes | By count. |
| `mentions[].from` | `string` | yes | The node whose entries name the other. |
| `mentions[].to` | `string` | yes | The node named (whole word, case-sensitive). |
| `mentions[].count` | `integer` | yes | Entries in the window. |
| `mentions[].groups` | `list<string>` | yes | Their signatures, most entries first, at most 20. |
| `co_bursts` | `list<CoBurst>` | yes | Pairs with hits >= 3, hits >= of / 2 and lift >= 3, by hits then lift; each group at most once as a and once as b; at most 50. |
| `co_bursts[].a` | `BurstSide` | yes | The group that bursts first. |
| `co_bursts[].a.node` | `string` | yes | Node id. |
| `co_bursts[].a.signature` | `string` | yes | Group signature. |
| `co_bursts[].a.template` | `string \| null` | yes | Group template. |
| `co_bursts[].a.groups` | `integer` | yes | Groups of the node that burst together with it (itself included); it stands for them as the one with most bursts. |
| `co_bursts[].b` | `BurstSide` | yes | The group that follows, on another node. |
| `co_bursts[].b.node` | `string` | yes | Node id. |
| `co_bursts[].b.signature` | `string` | yes | Group signature. |
| `co_bursts[].b.template` | `string \| null` | yes | Group template. |
| `co_bursts[].b.groups` | `integer` | yes | Groups of the node that burst together with it (itself included); it stands for them as the one with most bursts. |
| `co_bursts[].hits` | `integer` | yes | Bursts of a followed by a burst of b. |
| `co_bursts[].of` | `integer` | yes | Bursts of a. |
| `co_bursts[].median_lag_ms` | `integer` | yes | Median lag from a to b, whole minutes in ms. |
| `co_bursts[].lift` | `number` | yes | hits / expected, expected = of x P(a burst of b in delta_s). |
| `concurrent_jobs` | `list<ConcurrentJob>` | yes | By slots. |
| `concurrent_jobs[].job` | `string` | yes | Job key (job_run subject). |
| `concurrent_jobs[].nodes` | `list<string>` | yes | Nodes it ran on together. |
| `concurrent_jobs[].slots` | `integer` | yes | Slots of 60 s in which it ran on 2 or more nodes. |
| `concurrent_jobs[].examples` | `list<string>` | yes | Start of up to 3 such slots. |
| `sessions` | `list<SessionPair>` | yes | By count; session ids are not listed. |
| `sessions[].nodes` | `[string, string]` | yes | The two nodes. |
| `sessions[].count` | `integer` | yes | Sessions. |
| `jobs` | `list<JobRuns>` | yes | Job ownership: job_run events per node, most nodes first; at most 100. |
| `jobs[].job` | `string` | yes | Job key. |
| `jobs[].runs` | `map<string, integer>` | yes | job_run events by node. |
