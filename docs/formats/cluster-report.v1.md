# cluster-report.v1

The cluster page's payload: a capture, its timeline, groups, events and interactions.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`cluster-report.v1.schema.json`](cluster-report.v1.schema.json), also `atlas-log schema cluster-report.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"cluster-report.v1"` | yes |  |
| `generated_at` | `string` | yes | Build time, YYYY-MM-DDTHH:MM:SSZ. |
| `tool_version` | `string` | yes | Version that built the payload. |
| `capture` | `ClusterCaptureBody` | yes | capture.v1 without schema. |
| `capture.capture` | `string \| null` | yes | Capture id; null when not known. |
| `capture.cluster` | `string \| null` | yes | Cluster name. |
| `capture.window` | `ClusterWindow` | yes | The window of the counts. |
| `capture.window.mode` | `"explicit" \| "overlap" \| "union"` | yes | overlap: latest first entry to earliest last entry of the nodes; union: earliest first to latest last; explicit: since/until given. |
| `capture.window.since` | `string` | yes | Start, ISO 8601 UTC. |
| `capture.window.until` | `string` | yes | End, ISO 8601 UTC. |
| `capture.nodes` | `list<ClusterNodeSummary>` | yes | The nodes, sorted by node id. |
| `capture.nodes[].node` | `string` | yes | Node id. |
| `capture.nodes[].set` | `ClusterSetRef` | yes | The node's log set. |
| `capture.nodes[].set.id` | `string` | yes | Set id. |
| `capture.nodes[].set.name` | `string \| null` | yes | Set name. |
| `capture.nodes[].first_ts` | `string \| null` | yes | The set's first entry (whole set). |
| `capture.nodes[].last_ts` | `string \| null` | yes | The set's last entry (whole set). |
| `capture.nodes[].coverage` | `ClusterSpan \| null` | yes | The node's logs inside the window; null when it has none there. |
| `capture.nodes[].coverage.since` | `string` | yes | ISO 8601 UTC. |
| `capture.nodes[].coverage.until` | `string` | yes | ISO 8601 UTC. |
| `capture.nodes[].gaps` | `list<[string, string]>` | yes | [since, until] of runs of at least 15 empty minutes inside the coverage, oldest first, at most 100. |
| `capture.nodes[].entries` | `integer` | yes | Entries in the window. |
| `capture.nodes[].errors` | `integer` | yes | ERROR and FATAL entries in the window. |
| `capture.nodes[].warnings` | `integer` | yes | WARN entries in the window. |
| `capture.nodes[].errors_per_hour` | `number` | yes | errors per hour of coverage. |
| `capture.nodes[].warnings_per_hour` | `number` | yes | warnings per hour of coverage. |
| `capture.nodes[].top_error` | `ClusterTopError \| null` | yes | Largest error group; null without errors. |
| `capture.nodes[].top_error.signature` | `string` | yes | Group signature. |
| `capture.nodes[].top_error.template` | `string \| null` | yes | Group template. |
| `capture.nodes[].top_error.count` | `integer` | yes | Its entries on the node in the window. |
| `capture.nodes[].verdict` | `"clean" \| "new_errors" \| "new_exceptions" \| "new_warnings" \| "no_baseline" \| null` | yes | Triage against the node's own baseline; null unless asked for. |
| `capture.nodes[].baseline` | `ClusterBaseline \| null` | yes | The baseline of verdict; null without one or unless asked. |
| `capture.nodes[].baseline.id` | `string` | yes | Baseline set id. |
| `capture.nodes[].baseline.name` | `string \| null` | yes | Baseline set name. |
| `capture.nodes[].baseline.reason` | `string \| null` | yes | Why it was picked, in one line. |
| `capture.nodes[].events` | `map<string, integer>` | yes | Events in the window by kind. |
| `capture.skew` | `list<ClusterSkew>` | yes | Clock offset estimates from simultaneous event pairs (index, re-index and cluster lock events less than 5 s apart); [] when there are none. |
| `capture.skew[].a` | `string` | yes | Node id. |
| `capture.skew[].b` | `string` | yes | Node id, after a in node order. |
| `capture.skew[].offset_ms` | `integer` | yes | Median of ts on b minus ts on a over the pairs. |
| `capture.skew[].pairs` | `integer` | yes | Event pairs the estimate rests on. |
| `nodes` | `list<string>` | yes | Node ids; their order is the colour order. |
| `timeline` | `ClusterTimelineBody` | yes | cluster-timeline.v1, bucket 1h (15m when the window < 12 h). |
| `timeline.bucket` | `string` | yes | Bucket size, e.g. 1h. |
| `timeline.buckets` | `list<string>` | yes | Bucket starts, ISO 8601 UTC, contiguous over the window. |
| `timeline.nodes` | `map<string, ClusterNodeTimeline>` | yes | Counts by node id. |
| `timeline.nodes.*.levels` | `map<string, list<integer>>` | yes | Entries per bucket by ERROR (with FATAL), WARN, INFO, DEBUG (with TRACE), OTHER; only "count" with a signature. |
| `timeline.nodes.*.coverage` | `[integer, integer] \| null` | yes | Indices of the buckets of the node's first and last entry; null outside. |
| `timeline.signature` | `string` | no | The group counted, when one was asked for. |
| `groups` | `list<ClusterReportGroup>` | yes | Matrix rows by count, at most max_groups. |
| `groups[].signature` | `string` | yes | Group signature. |
| `groups[].template` | `string \| null` | yes | Masked message template. |
| `groups[].level` | `integer` | yes | Numeric level. |
| `groups[].level_name` | `string` | yes | Level name. |
| `groups[].logger` | `string \| null` | yes | Logger. |
| `groups[].exception_type` | `string \| null` | yes | Outermost exception type. |
| `groups[].total` | `integer` | yes | Entries on all nodes in the window. |
| `groups[].per_node` | `map<string, MatrixNodeCount>` | yes | Every node of the capture by id. |
| `groups[].per_node.*.count` | `integer` | yes | Entries; 0 when absent. |
| `groups[].per_node.*.rate_per_hour` | `number` | yes | Entries per hour of the node's coverage. |
| `groups[].per_node.*.first_seen` | `string \| null` | yes | First entry; null when absent. |
| `groups[].per_node.*.last_seen` | `string \| null` | yes | Last entry; null when absent. |
| `groups[].spread` | `"all" \| "only" \| "skewed"` | yes | only: on one node; skewed: missing from a node that has logs in the window, or skew >= skew_min; all: otherwise. |
| `groups[].skew` | `number \| null` | yes | Max/min, over the nodes that have the group, of count / the node's entries in the window; null when only. |
| `groups[].first_node` | `string \| null` | yes | Node with the earliest first_seen. |
| `groups[].lead_ms` | `integer \| null` | yes | first_seen of the next node minus that of first_node; null when only. |
| `groups[].hourly` | `map<string, list<[integer, integer]>>` | yes | Per node, sparse [bucket index into timeline.buckets, count]. |
| `groups[].examples` | `map<string, list<string>>` | yes | Per node, entry ids present in entries. |
| `entries` | `map<string, object>` | yes | Example entries by id: report.v1 entries plus node and set (an id two nodes share, an identical line in the same millisecond, is the first node's). |
| `events` | `ClusterEventsBody` | yes | events.v1 without schema. |
| `events.rules_version` | `integer` | yes | EVENT_RULES_VERSION of the rules used. |
| `events.count` | `integer` | yes | Rows in items. |
| `events.items` | `list<ClusterEvent>` | yes | Events by time, then node. |
| `events.items[].node` | `string` | yes | Node id. |
| `events.items[].ts` | `string` | yes | Time of its first entry. |
| `events.items[].last_ts` | `string` | yes | Time of its last entry (= ts when count is 1). |
| `events.items[].kind` | `string` | yes | start, stop, health_failed, cache_replication, … job_run. |
| `events.items[].subject` | `string \| null` | yes | What it is about: a node id, a health check, a lock, a job key. |
| `events.items[].subject_node` | `string \| null` | yes | subject when it is a node of the capture. |
| `events.items[].signature` | `string` | yes | Group signature of the first entry. |
| `events.items[].entry_id` | `string` | yes | Id of the first entry (in the node's set). |
| `events.items[].text` | `string` | yes | Message of the first entry, at most 300 characters. |
| `events.items[].count` | `integer` | yes | Entries folded into the event: same node, kind and subject within 60 s of the first (job_run 120 s, start and stop 10 min); health_failed and job_run also fold repeats at most 2 h (job_run 30 min) after the previous one. |
| `events.pairs` | `list<EventPair>` | yes | Kind pairs on different nodes with lift >= 3, by count; without job_run (see interactions.concurrent_jobs); at most 50. Computed before scheduled repeats are folded. |
| `events.pairs[].a` | `EventSide` | yes | The earlier event. |
| `events.pairs[].a.node` | `string` | yes | Node id. |
| `events.pairs[].a.kind` | `string` | yes | Event kind. |
| `events.pairs[].b` | `EventSide` | yes | The later event, on another node. |
| `events.pairs[].b.node` | `string` | yes | Node id. |
| `events.pairs[].b.kind` | `string` | yes | Event kind. |
| `events.pairs[].count` | `integer` | yes | Events a followed by an event b. |
| `events.pairs[].of` | `integer` | yes | Events a in the window. |
| `events.pairs[].median_lag_ms` | `integer` | yes | Median time from a to the first b after it. |
| `events.pairs[].lift` | `number` | yes | count / expected, expected = of x min(1, events b x 60 s / window). |
| `events.pairs[].examples` | `list<[string, string]>` | yes | Up to 3 [ts a, ts b]. |
| `interactions` | `ClusterInteractionsBody` | yes | interactions.v1 without schema. |
| `interactions.delta_s` | `integer` | yes | Echo of delta_s (co-bursts). |
| `interactions.mentions` | `list<MentionEdge>` | yes | By count. |
| `interactions.mentions[].from` | `string` | yes | The node whose entries name the other. |
| `interactions.mentions[].to` | `string` | yes | The node named (whole word, case-sensitive). |
| `interactions.mentions[].count` | `integer` | yes | Entries in the window. |
| `interactions.mentions[].groups` | `list<string>` | yes | Their signatures, most entries first, at most 20. |
| `interactions.co_bursts` | `list<CoBurst>` | yes | Pairs with hits >= 3, hits >= of / 2 and lift >= 3, by hits then lift; each group at most once as a and once as b; at most 50. |
| `interactions.co_bursts[].a` | `BurstSide` | yes | The group that bursts first. |
| `interactions.co_bursts[].a.node` | `string` | yes | Node id. |
| `interactions.co_bursts[].a.signature` | `string` | yes | Group signature. |
| `interactions.co_bursts[].a.template` | `string \| null` | yes | Group template. |
| `interactions.co_bursts[].a.groups` | `integer` | yes | Groups of the node that burst together with it (itself included); it stands for them as the one with most bursts. |
| `interactions.co_bursts[].b` | `BurstSide` | yes | The group that follows, on another node. |
| `interactions.co_bursts[].b.node` | `string` | yes | Node id. |
| `interactions.co_bursts[].b.signature` | `string` | yes | Group signature. |
| `interactions.co_bursts[].b.template` | `string \| null` | yes | Group template. |
| `interactions.co_bursts[].b.groups` | `integer` | yes | Groups of the node that burst together with it (itself included); it stands for them as the one with most bursts. |
| `interactions.co_bursts[].hits` | `integer` | yes | Bursts of a followed by a burst of b. |
| `interactions.co_bursts[].of` | `integer` | yes | Bursts of a. |
| `interactions.co_bursts[].median_lag_ms` | `integer` | yes | Median lag from a to b, whole minutes in ms. |
| `interactions.co_bursts[].lift` | `number` | yes | hits / expected, expected = of x P(a burst of b in delta_s). |
| `interactions.concurrent_jobs` | `list<ConcurrentJob>` | yes | By slots. |
| `interactions.concurrent_jobs[].job` | `string` | yes | Job key (job_run subject). |
| `interactions.concurrent_jobs[].nodes` | `list<string>` | yes | Nodes it ran on together. |
| `interactions.concurrent_jobs[].slots` | `integer` | yes | Slots of 60 s in which it ran on 2 or more nodes. |
| `interactions.concurrent_jobs[].examples` | `list<string>` | yes | Start of up to 3 such slots. |
| `interactions.sessions` | `list<SessionPair>` | yes | By count; session ids are not listed. |
| `interactions.sessions[].nodes` | `[string, string]` | yes | The two nodes. |
| `interactions.sessions[].count` | `integer` | yes | Sessions. |
| `interactions.jobs` | `list<JobRuns>` | yes | Job ownership: job_run events per node, most nodes first; at most 100. |
| `interactions.jobs[].job` | `string` | yes | Job key. |
| `interactions.jobs[].runs` | `map<string, integer>` | yes | job_run events by node. |
| `options` | `ClusterReportOptions` | yes | Build options. |
| `options.window` | `"explicit" \| "overlap" \| "union"` | yes | Window mode. |
| `options.examples` | `integer` | yes | Example entries per group and node. |
| `options.max_raw` | `integer` | yes | Characters of raw kept; 0 = all. |
| `options.max_groups` | `integer` | yes | Largest groups kept. |
| `options.truncated_groups` | `integer` | yes | Groups cut by max_groups. |
| `urls` | `ClusterReportUrls \| null` | yes | null; the web app fills it. |
| `urls.entries` | `string` | yes | Merged entries of the capture. |
| `urls.context` | `string` | yes | Context on all nodes of one entry (template). |
| `urls.set_report` | `string` | yes | Report page of one node's set (template). |
