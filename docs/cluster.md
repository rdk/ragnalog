# Clusters: logs of several nodes

Status: implemented 2026-11-05 (identity, capture views, interactions, cluster
page) and wired into the CLI and the web app (commands `capture`, `timeline`,
`matrix`, `events`, `interactions`, `entries`/`context --all-nodes`/`report` of
a capture; `/api/captures/{ref}…`, `/captures/{ref}`). §11 is the contract the
parts were built to; §11.1, §11.5, §11.6 and `report.md` §5 record where the
result differs from the text above.
Builds on `design.md` (store, service layer), `report.md` (the page and
its payload) and `webapp.md`; the commands are in `cli.md`.

A Jira Data Center cluster writes one `atlassian-jira.log*` family per node. A
cluster log export (one ZIP with `job.json` and `<NODE>_atlassian-jira.log*`
files) or a support zip per node is the usual input. Today ragnalog can only
ingest each node as an unrelated set, or mix the nodes into one set where
nothing says which line came from which node. This design makes the node a
first-class dimension. You see the nodes side by side, find what happens on
only one of them, and see how they affect each other.

## 1. Questions it must answer

1. Is a problem cluster-wide or local to one node? Is it on all nodes, only one,
   or much more often on one?
2. What did every node do around this moment? Show restarts, health-check
   failures, index snapshots and restores, cluster locks and cache replication,
   on one time axis.
3. Do the nodes interact? Look for:
   - one node logging about another ("node X does not seem to replicate its
     cache");
   - events on one node followed by errors on the other;
   - a scheduled job that should run on one node but runs on all of them;
   - a user session that jumps between nodes.
4. Since the previous capture, is anything new on any node? Compare each node
   with its own past, not with the other node.
5. Which node saw it first, and how much later did the others see it?

## 2. Concepts

| Term | Meaning | Where it lives |
|---|---|---|
| node | Node id as Jira knows it (`NODE1`) | `logset.v1` field `node` |
| cluster | Name of the Jira instance the node belongs to (`jira-test`) | `logset.v1` field `cluster` |
| capture | Sets collected together: one export job, or one `ingest` call that split nodes | `logset.v1` field `capture` |

A set stays **one node's logs**. A capture is a view over its member sets, not
a new kind of stored set. Reasons:

- Everything that works on a set keeps working per node: triage, diff,
  context, frames, upgrade, the report.
- Thread names repeat on every node (`http-nio-8080-exec-12`). With one set
  per node, `context` and thread filters never mix nodes by accident.
- Entry ids stay unique per set. A cross-node reference is
  `(set id, entry id)`, so the id hash needs no node component and needs no
  rebuild.
- Signatures are the same across sets with the same `recipe_key`. Joining
  nodes by signature is what `diff` already does.
- Baseline ranking needs the node on the manifest (§5). A sidecar file would
  not be enough.

The three fields are additive and nullable (`logset.v1`, schema 1.1). Readers
treat them as missing in old manifests. The set id (the digest of its sources)
does not change, so labelling an existing set only rewrites its manifest.

## 3. Ingest

```
ragnalog ingest export.zip --cluster jira-test        # export ZIP: one set per node, one capture
ragnalog ingest node1/ node2/ --split dir            # one directory per node
ragnalog ingest logs/ --split prefix                 # NODE_atlassian-jira.log* in one folder
ragnalog ingest logs/ --node NODE1 --cluster jira-test    # a single node
ragnalog label SET --node NODE1 --cluster jira-test [--capture ID]    # existing set
```

- **Splitting.** `--split auto|prefix|dir|none`. The default `auto` recognises
  the export ZIP (a `job.json` file and `<NODE>_` prefixes), then the prefixes,
  then one sub-directory per node (its logs in it or one level down:
  `node1/application-logs/`). Any other input stays one set, as today; a
  directory without log files of its own is searched two levels down (a support
  ZIP's `application-logs/`, an export's `logs/` beside `job.json`). ZIPs are
  extracted into the data directory's spool and removed afterwards.
- **Node id**, first match wins:
  1. `--node`;
  2. the node names in `job.json`;
  3. the file prefix;
  4. the directory name.

  A format hook, `LogFormat.node_hints(lines)`, reads lines in which a node
  names itself (Jira: `nodeId: X`, `on node X because …`). When the hints
  name a different node, the ingest output gets a warning; when nothing else
  gave a node, the hints suggest one.
- **Cluster.** `--cluster`, else the cluster of an earlier set of the same
  node (node ids are remembered), else none.
- **Capture id.** `--capture`, else `job.json`'s `jobId`, else a hash of the
  member set ids. Each member set is named `<prefix>_<node>` (§11.1: prefix
  `--name`, else the cluster, else the start of the capture id); a name another
  set has already gets `_2`, `_3`, ….
- **Output.** `ingest-capture.v1`: the capture (`captures.v1` item) and the
  member sets' `logset.v1` with `ingest`, plus warnings. The web upload accepts
  the same ZIP and creates the same capture (`job.v1` gains `capture`).

## 4. Time

- All times stay UTC. Each node keeps its `tz_offset` per entry. The cluster
  page can show them in the browser's time zone (a viewer preference; data and
  links stay UTC).
- **Coverage.** Nodes rotate logs independently. In a 7-day export, one
  node's oldest kept line can be 12 hours later than the other's because its
  logs filled up faster. A group that seems to exist "only on NODE2" can
  simply be older than NODE1's oldest file. So every capture view has a
  window:
  - `overlap` (default): from the latest first entry to the earliest last
    entry;
  - `union`;
  - explicit `--since` / `--until`, clamped to the union of the logs (a window
    outside them is `invalid_value`); with only one of them the other end is
    the union's (the capture's first or last entry).

  Rates are per hour of each node's coverage. Charts hatch the time a node
  has no logs for. Hours inside the coverage that have no entries are
  reported as gaps (§6).
- **Clock skew.** Clocks are assumed to be NTP-synchronised. The capture
  document shows an estimate from paired events (§7; for example, a cluster
  lock released on one node and the index replay on the other within the
  same second). The estimate is only shown, never applied to the data.

## 5. Triage and diff per node

- `rank_baselines` gets one more rule, after the format and recipe rules.
  When both sets have a `node`, a candidate from another node is rejected
  with reason `other_node`. An explicit `--baseline` still wins. This stops a
  node being compared with its neighbour's logs, which is what happens today.
  When the set has a node, a candidate of the same node is preferred to one
  without a node (after "ends before", before the span rule).
- Per-node triage: each node is triaged against that node's previous set.
  As built, this is part of `capture.v1` (`verdict` and `baseline` per node;
  `ragnalog capture REF`, on by default, `--no-triage` to skip;
  `GET /api/captures/{ref}?triage=true`) and of the cluster page, not a
  `triage.v1` variant: `triage CAPTURE` is a `usage` error whose hint is
  `ragnalog capture REF --triage`. One triage document per node already says
  what is new on that node; a merged `triage.v1` with a `nodes` list per `new`
  row was left out (the matrix's `spread` answers "on one node or on all").
  It costs a few tens of milliseconds per node on two nodes of about 700k
  entries, so it is on by default.
- `diff` between two nodes of the same capture already works. The matrix
  (§6) is its N-node form, restricted to the overlap window.

## 6. Service, documents, CLI and API

A new `service/cluster.py` resolves a capture reference to
`Capture(nodes=[(node, LogSet)])`. It refuses members with a different
`recipe_key`, with an `upgrade` hint. It builds the documents below with
DuckDB over `read_parquet([...])` of the member entries, with the node added
as a column. Two sets of about 700k entries each take well under a second per
query.

References:
- `capture:ID`, a unique capture id prefix;
- `cluster:NAME`, its newest capture;
- `cluster:NAME@2026-11-05`, its capture of that day.

The CLI and the API accept a capture reference wherever this section says
`CAPTURE`. Set references are unchanged. As built, `capture`, `timeline`
(`--by-node`), `matrix`, `events`, `interactions` and `/api/captures/{ref}…`
also take a set: the capture it belongs to, else the set as a capture of one
node; `entries` and `report` take a capture only through a capture reference
(a set stays a set there).

| Document | CLI | API | Content |
|---|---|---|---|
| `captures.v1` | `captures` | `GET /api/captures` | `CaptureInfo` items (§11.1), newest first; built (phase A) from the manifests alone |
| `capture.v1` | `capture CAPTURE [--no-triage]` | `GET /api/captures/{ref}` | cluster, capture, window, and per node: set, coverage, gaps ≥ 15 min, entries, errors and warnings per hour, top error group, triage verdict against its own baseline, event counts, clock skew estimate |
| `cluster-timeline.v1` | `timeline CAPTURE`, `timeline SET --by-node` | `GET /api/captures/{ref}/timeline` | buckets, and per node counts by level; with `signature`, that group per node; coverage per node; takes the entry filters of `entries` and `node` |
| `matrix.v1` | `matrix CAPTURE` | `GET /api/captures/{ref}/matrix` | one row per signature: group fields, and per node `count, rate_per_hour, first_seen, last_seen`; `spread` (`all`, `only`, `skewed`), `skew`, `first_node`, `lead_ms`. Filters as `groups`, plus `node`, `spread`, `sort=count\|skew\|lead` |
| `events.v1` | `events CAPTURE\|SET` | `GET /api/captures/{ref}/events` | `items` `{node, ts, kind, subject_node?, signature, entry_id, text}`; `pairs` of events on different nodes that follow each other (§7) |
| `interactions.v1` | `interactions CAPTURE` | `GET /api/captures/{ref}/interactions` | mention matrix, co-bursts, concurrent jobs, sessions on several nodes (§7) |
| `entries.v1` + `node` | `entries CAPTURE --since … --until … [--signature A,B] [--node N1,N2]` | `GET /api/captures/{ref}/entries` | entries of all nodes interleaved by time; rows add `node`, `set`; several groups and a node filter (a capture only) |
| `entries.v1` | `context SET ENTRY --all-nodes [--window 30]` | `…/context?all_nodes=1&window=30` | what every node logged within the window (seconds) around one entry |
| (`capture.v1` verdicts) | `capture CAPTURE` (`triage CAPTURE`: usage error) | (no route) | §5 as built |
| `cluster-report.v1` | `report CAPTURE` | `GET /api/captures/{ref}/report` | payload of the cluster page (§8) |

`spread` is computed on rates normalised by each node's total volume in the
window:
- `only`: the group appears on one node;
- `skewed`: the max/min ratio is at least 4;
- `all`: otherwise.

The thresholds are parameters with documented defaults. All documents have
typed Structs, schemas and an OpenAPI entry, and appear in the manifest, as
today.

## 7. Detecting interactions

All of the following match against **group templates**. A rule scans
`groups.parquet`, typically 1–3k rows per set. Only then does it read the
entries of the matching signatures. Rules therefore need no re-ingest and
can change without rebuilding sets. They are versioned
(`EVENT_RULES_VERSION`) and cached per set version.

- **Event rules**, per format (`core/formats/jira_events.py`): a table of
  `EventRule(kind, logger_prefix, template_re, subject)`. Kinds for Jira:
  - `start` and `stop`;
  - `health_failed`, carrying the check name;
  - `cache_replication` (`ClusterReplicationHealthCheck`, which names the
    subject node);
  - `index_replication`, `index_snapshot`, `index_restore` and
    `index_replay` (`DefaultNodeReindexService`, `IndexUtils`), and `reindex`
    (a full re-index starting or ending);
  - `cluster_lock`, acquired or refused;
  - `cache_peer` (`CachingRMICachePeerManager` re-creating a peer of node X);
  - `job_run`, carrying the job key.

  A user rules file (`RAGNALOG_EVENT_RULES`) can add rules later.
- **Mentions.** An entry on node A whose message or variables contain the id
  of another member node B is an edge A→B. Node ids are known from the
  capture, so this needs no rule. The matrix counts edges per pair, with the
  groups behind each edge.
- **Event pairs.** For events on different nodes, list kind pairs that follow
  each other within Δ (default 60 s), with how often and the median lag. For
  example: `index_snapshot` on NODE1 followed by `index_replay` on NODE2 at
  +0 s, 1 of 1.
- **Co-bursts.** These run per node and per group, at level WARN or above, or
  for groups behind an event, with minute buckets.
  - A burst begins at the first minute at or above the threshold after at
    least 10 quiet minutes.
  - For pairs (g on A, h on B), count the bursts of g followed by a burst of h
    within Δ (default 5 min).
  - Report `hits/of`, the median lag, and the lift over chance
    (`expected = bursts_h × Δ / window`).
  - Pairs with fewer than 3 hits or a lift under 3 are not reported.

  The output is a sentence a person can check: "when g burst on NODE1, h
  burst on NODE2 within 5 min, 7 of 8 times, median +40 s".
- **Concurrent jobs.** `job_run` events with the same job key on two or more
  nodes in the same slot (±1 min). A cluster-wide job that runs on every node
  is either intended or a misconfiguration; either way it shows.
- **Sessions across nodes.** A `session` seen on two or more nodes within an
  hour means a sticky session was lost or a failover happened. The document
  reports counts per node pair and a few examples.

## 8. UI

The page is `/captures/{ref}`, with a static twin from `ragnalog report
CAPTURE`. It consumes `cluster-report.v1`, as the report consumes `report.v1`:
vanilla JS, no build, state in `location.hash`, light and dark.

```
┌ header ─────────────────────────────────────────────────────────────────────┐
│ jira-test · capture 2026-11-05 · [N1 NODE1] [N2 NODE2] · window: overlap ▾  │
├ node cards ─────────────────────────────────────────────────────────────────┤
│ N1  615k entries  49k ERR  7.0/h  2 events  verdict: new_errors (vs N1 10-29)│
│ N2  747k entries  16k ERR  2.3/h  15 events verdict: clean                  │
├ timeline: small multiples, shared time axis (UTC or local) ─────────────────┤
│ events N1 ·····◆·········▲····························◆·················    │
│ N1  ░░░░░░▆▅▆▇▆▅▆▆▅▆▇▇▆▅▆▆▅▆  (stacked by level; ░ = no logs from this node)│
│            ↑ mention arrow N2→N1 ("does not seem to replicate its cache")   │
│ events N2 ◆ ◆ ◆ ◆ ◆ ◆ ◆ ◆ ◆ ◆▲········································       │
│ N2  ▅▆▅▆▆▅▆▇▆▅▆▆▅▆▆▅▆▆▇▆▅▆▆                                            │
│ y: [shared | per node | % of node]   brush selects the window for all rows  │
├ tabs: [Groups by node] [Interactions] [Merged log] ─────────────────────────┤
│ Groups by node                                                    drawer ▸ │
│ lvl  template              total   N1 ▇▇▇ 8.2k   N2 ▇▇▇▇ 11k  spread  lead  │
│ ERR  LDAP group sync …      19k   ▇▇▇           ▇▇▇▇          all    N2 +19h│
│ ERR  Lucene lock …             5   ▇             ·            only N1       │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Timeline.** One band per node with a shared time axis; the report's
  `drawColumns` is reused per band.
  - Bands are hatched outside a node's coverage, and the overlap window is
    marked.
  - An event lane above each band shows glyphs by kind. Hover shows the text,
    and a click opens the merged log at that moment (at the event's own entry).
  - A thin "together" lane above the bands marks cross-node moments: timeline
    buckets in which two or more nodes log more than 3× their usual errors; a mark opens
    "±30 s on all nodes" around the moment's busiest 10 s.
  - y-scale: shared, per node, % of node, or errors only (one ERROR series per
    node on one scale, spikes clipped and labelled).
  - Mention edges are drawn as arrows from the reporting node's lane to the
    subject's lane.
  - Selecting a group draws its per-node counts as a line over each band.
  - The brush on any band sets the window for every view, as the report's
    brush does now.
- **Groups by node.** The report's table, filters and keyboard model, with
  one count-and-bar column per node; past 8 nodes, a heat strip. There are
  `spread` and `lead` chips, and filters `spread=only|skewed|all` and `node=`.
  The drawer shows per-node hourly lines, first and last seen per node,
  examples per node in tabs, co-bursting groups on other nodes, and the CLI
  equivalents (`ragnalog matrix … --signature`).
- **Interactions.**
  - An N×N mention matrix; a click on a cell lists its entries.
  - Co-bursts and event pairs as compact rows (co-bursts strongest first, event
    pairs most often first): source → target (template on up to two lines, node
    badge), a hits meter, lag, lift (capped at `>×100` when shown), and "show in
    log" or example times into the merged log.
  - Concurrent jobs and sessions across nodes as compact tables.
  - An events table with kind chips (job runs follow the lanes' toggle) and
    same-second bursts of one kind on one node folded into one row.
- **Merged log.** Entries of every node in the window, interleaved by time,
  fetched a page at a time with every filter (level, groups, nodes, search)
  applied by the server, the next page loaded on scrolling to the end. Each
  row has a node badge (colour plus text, `N1`) and a level chip. There is a
  filter by minimum level and node. Each row has "±30 s on all nodes"
  (`context --all-nodes`).
- **Node colours.** Use a categorical series palette that is separate from
  the level status colours and always carries the text label. Node order and
  colour are fixed, by node id, in every chart. Check the palette with the
  dataviz method for both themes and colour-vision deficiencies.
- **Home page.** The sets list groups a capture's sets under one row (cluster,
  capture, node count, worst verdict), with a link to the capture page. The
  report's navigation bar gets a node switcher (sibling sets of the capture,
  keeping the hash) and "open cluster view".

## 9. Phases

Each phase ships with tests, docs and parity cases. Fixtures are two invented
nodes, `NODE1` and `NODE2`, a few dozen masked lines each. They are built into
an export-style ZIP inside the test, so no real ZIP is ever committed.

| Phase | Content | Size |
|---|---|---|
| A. Identity | manifest fields (schema 1.1); `label`; ZIP/prefix/dir splitting; node hints; `capture.v1`, `captures`; same-node baseline rule; capture rows on the home page | M |
| B. Side by side | `service/cluster.py`, overlap window, `cluster-timeline.v1`, `matrix.v1`; capture page with timeline bands, coverage hatching, the groups-by-node table and drawer | M |
| C. Interactions | event rules, `events.v1`, event lanes and mention arrows; merged log and `context --all-nodes`; mentions, concurrent jobs, sessions (`interactions.v1`); `triage CAPTURE` | M |
| D. Correlation and export | co-bursts with lift, lead/lag in the matrix, the clock skew estimate, `cluster-report.v1` and the static `report CAPTURE` | M |

The acceptance check for each phase runs on the two-node sample capture in
`../ragnalog-data`. Its expected findings are listed in that
capture's `NOTES.md`, kept outside the repository because they quote real
node names.

## 10. Not planned

- Correcting timestamps by an estimated clock skew. The estimate is shown,
  not applied.
- Live tailing of nodes, or pulling logs from them. The export is done by the
  ScriptRunner helper.
- Other kinds of logs per node (access, GC, `atlassian-jira-gc`) in the same
  views. They stay separate formats with their own kind of record.
- Any meaning of "cluster" beyond a set of nodes of one instance. Comparing
  instances (test against production) remains `diff` between sets.

## 11. Interfaces (implementation contract)

Fixed before implementation so that identity (A), analytics (S) and the page
(U) can be built in parallel. Names below are binding; internals are not.

### 11.1 Identity (A)

- `LogSetManifest` gains `node: str | None = None`, `cluster: str | None = None`,
  `capture: str | None = None`; `to_dict` always writes the three keys (null when
  unset); `SCHEMA_VERSION = "1.1"`. The `Logset` and `SetItem` Structs gain the
  same optional fields.
- `Store.label(ref, *, node=UNSET, cluster=UNSET, capture=UNSET) -> LogSetManifest`
  rewrites one manifest under the write lock; `None` clears a field.
- Split ingest names each set `<prefix>_<node>` with `node` lower-cased, where
  `prefix` is `--name`, else `cluster`, else the first 8 characters of the
  capture id. Capture id: `job.json` `jobId`, else the first 16 hex characters
  of xxh3-64 over the sorted member set ids.
- `service/captures.py`:
  - `CaptureInfo(Struct)`: `capture: str, cluster: str | None, first_ts, last_ts,
    entry_count: int, nodes: list[CaptureNodeRef]`, where
    `CaptureNodeRef(Struct)` is `node: str, id: str, name: str | None,
    first_ts, last_ts, entry_count`; nodes are sorted by `node`.
  - `list_captures(store) -> list[CaptureInfo]`, newest `last_ts` first (the
    `captures.v1` items).
  - `resolve_capture(store, ref: str) -> CaptureInfo`. Accepted refs:
    `capture:PREFIX`; `cluster:NAME` (the newest capture of the cluster);
    `cluster:NAME@YYYY-MM-DD` (the capture whose `last_ts` falls on that UTC
    day). `OpError` reasons: `capture_not_found` (404, CLI exit 2) and
    `ambiguous_capture` (409, CLI exit 1).
  - `is_capture_ref(text) -> bool`: the text starts with `capture:` or
    `cluster:`.

  As built: `first_ts`/`last_ts` are ISO 8601 strings as in the manifests;
  `resolve_capture` takes an optional keyword `front` (the hint of an error),
  answers `invalid_value` for text that is no capture reference, and
  `capture:PREFIX` prefers an exact id. Also there: `get_capture(store, id)`
  (exact id or `None`), `capture_id(set_ids)`, `captures_doc(store, brief=)`.
  A member without a node is listed under its name. `captures --brief` keeps
  only `node`, `id`, `name` per node. Split ingest prints `ingest-capture.v1`
  `{capture, sets, warnings}` (not `capture.v1`, which phase B builds).

### 11.2 Analytics (S): `service/cluster.py`

```python
@dataclass(frozen=True, slots=True)
class ClusterNode:
    node: str
    logset: LogSet

@dataclass(frozen=True, slots=True)
class Capture:
    id: str | None
    cluster: str | None
    nodes: tuple[ClusterNode, ...]          # sorted by node

def open_capture(nodes: Sequence[tuple[str, LogSet]], *, capture_id: str | None = None,
                 cluster: str | None = None) -> Capture   # OpError incompatible_sets on recipe_key mismatch

@dataclass(frozen=True, slots=True)
class Window:
    mode: Literal["overlap", "union", "explicit"]
    since: datetime
    until: datetime
def resolve_window(capture, mode="overlap", since=None, until=None) -> Window
```

Builders return msgspec Structs (registered in `documents.py`). Each takes the
`Capture` first and keyword options:

| Function | Document |
|---|---|
| `capture_summary(capture, *, window, store=None)` | `capture.v1`: `capture, cluster, window {mode, since, until}, nodes[{node, set {id, name}, first_ts, last_ts, coverage {since, until}, gaps [[since, until]], entries, errors, warnings, errors_per_hour, warnings_per_hour, top_error {signature, template, count} \| null, verdict \| null, baseline \| null, events}], skew [{a, b, offset_ms, pairs}]`. `verdict`/`baseline` are filled only when `store` is given (per-node triage) |
| `cluster_timeline(capture, *, window, bucket="1h", signature=None, where=None)` | `cluster-timeline.v1`: `bucket, buckets [iso], nodes {NODE: {levels {LEVEL: [n…]}, coverage [first_idx, last_idx] \| null}}`; with `signature`, `levels` has only `"count"` |
| `matrix(capture, *, window, where=None, spread=None, node=None, sort="count", limit=50, offset=0, skew_min=4.0)` | `matrix.v1`: `total, offset, truncated, nodes [..], items [{signature, template, level, level_name, logger, exception_type, total, per_node {NODE: {count, rate_per_hour, first_seen, last_seen}}, spread: "all"\|"only"\|"skewed", skew: float \| null, first_node, lead_ms \| null}]` |
| `events(capture, *, window, kinds=None)` | `events.v1`: `items [{node, ts, kind, subject_node, signature, entry_id, text}]`, `pairs [{a {node, kind}, b {node, kind}, count, of, median_lag_ms, examples [[ts_a, ts_b]]}]` |
| `interactions(capture, *, window, delta_s=300)` | `interactions.v1`: `mentions [{from, to, count, groups [sig]}], co_bursts [{a {node, signature, template}, b {node, signature, template}, hits, of, median_lag_ms, lift}], concurrent_jobs [{job, nodes [..], slots, examples [ts]}], sessions [{nodes [a, b], count}]` |
| `merged_entries(capture, *, window, where=None, limit=100, offset=0, max_raw=4000, columns=None)` | `entries.v1` rows with `node` and `set` added, ordered by `ts`, then `node` |
| `context_all_nodes(capture, set_id, entry_id, *, window_s=30, max_raw=4000)` | `entries.v1` with `anchor`; the rows of every node within ±`window_s` |
| `cluster_payload(capture, *, window, examples=3, max_raw=4000, max_groups=500)` | `cluster-report.v1` (§11.3) |

Event rules live in `core/formats/jira_events.py` as
`EventRule(kind, logger_prefix, template_re, subject_group: str | None)`, and a
format exposes them as the attribute `LogFormat.event_rules` (default `()`).
They match group templates, never raw text.

### 11.3 `cluster-report.v1` payload (S builds it, U renders it)

```jsonc
{
  "schema": "cluster-report.v1", "generated_at": "…Z", "tool_version": "…",
  "capture": { /* capture.v1 without "schema" */ },
  "nodes": ["NODE1", "NODE2"],                 // fixed order = colour order
  "timeline": { /* cluster-timeline.v1 without "schema", bucket 1h (15m when the window < 12 h) */ },
  "groups": [ {                                // matrix rows, count desc, ≤ max_groups
      /* matrix.v1 item */,
      "hourly": {"NODE1": [[bucket_idx, n], …], …},   // sparse, index into timeline.buckets
      "examples": {"NODE1": ["<entry id>", …], …}
  } ],
  "entries": { "<entry id>": { /* report.v1 entry shape */, "node": "NODE1", "set": "<set id>" } },
  "events": { /* events.v1 without "schema" */ },
  "interactions": { /* interactions.v1 without "schema" */ },
  "options": { "window": "overlap", "examples": 3, "max_raw": 4000, "max_groups": 500, "truncated_groups": 0 },
  "urls": null | { "entries": "/api/captures/{ref}/entries", "context": "/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30s",
                   "set_report": "/sets/{set}" }
}
```

### 11.4 Page (U)

- Files: `report/assets/cluster.html`, `cluster.js` and `cluster.css`. They
  reuse the CSS custom properties of `report.css`; `cluster.html` inlines
  `report.css` before `cluster.css`.
- `report/render.py` gains `render_cluster_html(payload | None, *, data_url=None,
  asset_base=None) -> str`, mirroring `render_html`: static with the payload
  embedded, or web with `data-report-url`.
- A sample payload `tests/report/data/cluster_payload.json` uses two invented
  nodes, `NODE1` and `NODE2`, and is the fixture of the page tests.
- Implemented; what the page does and reads is in `report.md` §5.

### 11.5 Analytics as built (S)

Additions to §11.2, all optional keyword arguments or extra keys:

- Every builder, `open_capture` and `resolve_window` take `front="api"`, which
  picks the error codes and how messages spell parameters, as in the other
  builders. `cluster_payload` also takes `store=None` (node verdicts, as in
  `capture_summary`). `Capture.ref` is `capture:<id>` (without an id:
  `capture:` and the member set ids); merged entries carry it as `logset`.
- `resolve_window(since=, until=)` takes datetimes or the strings of `--since`
  (relative times count back from the capture's last entry); a missing end comes
  from `mode`. Both ends of a window are inclusive.
- `EventRule` has a fifth field, `logger_re` (regex searched in the logger), and
  `subject_group="@logger"` means the logger's class name. `logger_prefix` is
  compared with packages abbreviated to their initials, as Jira shortens loggers
  (`com.atlassian.jira.index.ha.X` matches `c.a.j.index.ha.X`). The rules match
  the template; a subject captured with a placeholder in it (`NODE<NUM>`: digits
  at the end of a node id are masked) is taken from the entry's message.
  `EVENT_RULES_VERSION` lives in `core/events.py`.
- Documents: `matrix.v1` adds `window` and `count`; `events.v1` adds
  `rules_version` and `count`, items add `subject` (node id, check, lock or job
  key), `last_ts` and `count` (folded entries), pairs add `lift`;
  `interactions.v1` adds `delta_s` and `jobs` (job ownership: `job_run` events per
  node), co-burst sides add `groups`; entry rows of a capture add `node` and `set`.
  A tuple in a document (`gaps`, `coverage`, `hourly` cells, examples) is a JSON
  array of fixed length.

Rules and thresholds (constants in `service/cluster.py`):

- Gaps: runs of at least 15 empty minutes inside the coverage (at most 100 per
  node). Rates are per hour of the node's coverage in the window.
- Matrix: `skewed` also when a group is missing from a node that has logs in the
  window while present on two or more; `skew` is then taken over the nodes that
  have it. `lead_ms` is the gap between the first two nodes' `first_seen`.
- Jira event rules (`core/formats/jira_events.py`, first match wins):
  `index_replay` (`DefaultNodeReindexService` `[INDEX-REPLAY]`, subject
  `sourceNodeId`), `index_restore`, `reindex` (`Reindex All`, `ReindexAll took`,
  `Re-indexing started/finished/failed`, `Reindex took`), `index_snapshot` (any
  `index snapshot` line of `com.atlassian.jira`, `ArchiveUtils` `Creating
  archive`), `index_replication`, `cache_replication`
  (`ClusterReplicationHealthCheck`, subject the node), `cache_peer`
  (`CachingRMICachePeerManager`, subject the node), `health_failed`
  (`SupportHealthCheckProcess`, subject the check), `cluster_lock` (any logger,
  subject the lock), `stop` and `start` (`JiraStartupLogger`,
  `LauncherContextListener`, `DefaultJiraLauncher`), `job_run` (a `JobRunner`
  logger: its class; else a `jobId`; else job start/finish/skip lines: the
  logger's class).
- Folding: entries of one node, kind and subject within 60 s of an event's first
  entry are one event (`job_run` 120 s, `start`/`stop` 10 min). In `events.v1`
  items, the scheduled kinds fold further: a `health_failed` repeat at most 2 h
  and a `job_run` repeat at most 30 min after the previous one join it (a check
  failing hourly for a week is one item with its `count` and `last_ts`).
- Event pairs use the events before that second fold: b on another node at most
  60 s after a, listed when `count / (of × min(1, events b × 60 s / window))` is
  at least 3; `job_run` takes no part (concurrent jobs cover it). The clock skew
  uses pairs among `index_snapshot`, `index_replay`, `reindex` and `cluster_lock`
  less than 5 s apart.
- Co-bursts: minute counts of the groups at WARN and above or behind an event,
  except `health_failed` and `job_run` groups (they recur by schedule). A burst
  starts at a minute with at least the group's usual count (median of its
  non-empty minutes) after 10 minutes below it. The 200 groups with most bursts
  per node are kept; groups of one node whose bursts coincide within `delta_s`
  80% of the time both ways form one family, shown by its member with most
  bursts (`groups` counts them). A pair needs at least 3 hits, hits for at least
  half the bursts of a, and lift at least 3; each group appears at most once on
  each side (a same-minute pair only one way round), at most 50 pairs.
- Concurrent jobs: `job_run` events with one job key on two or more nodes within
  60 s of a slot's first run. Sessions: a session seen on two nodes within an
  hour (counts per node pair only).
- `cluster-report.v1`: an entry id that two nodes share (an identical line in the
  same millisecond) is listed once, as the first node's; the other node's
  examples omit it.

On the sample capture (two nodes, about 615k and 747k entries) every builder
takes under 1 s and the payload about 0.7 s and 5 MB. There, the index replay on
the second node comes 22 s *before* the first node's `index_snapshot` event (its
cluster lock release) and 11 s after its re-index ended: the pairs found are
`reindex` → `index_replay` (+11 s) and `index_replay` → `index_snapshot` (+22 s),
too far apart for a skew estimate.

### 11.6 Front ends as built (I)

- `service/capture_queries.py` turns a reference and the request options into the
  documents, for both front ends: `capture_from_ref(sets, ref, front=)` (capture
  reference → `resolve_capture` → member sets → `open_capture`; a set with
  `capture` → that capture; a set without → one node, its `node`, else its name,
  without a capture id), `CaptureWindow(window, since, until)` (the flattened
  `--window/--since/--until` and the API's query parameters), and `capture`,
  `timeline`, `matrix`, `events`, `interactions`, `entries`, `raw_entries`,
  `context`, `report_payload`, `report_file`.
- `matrix` and merged `entries` reuse `GroupQuery`/`EntryQuery`; their
  `since`/`until` are the window (explicit), not filters on the group or entry
  (both would cut to the same time anyway). `--signature` is a unique prefix
  resolved in any member's groups (`cluster.resolve_signature`; `matrix(...,
  signature=)` was added). `--columns` of a capture's entries may name `node`
  and `set` (always present); `context_all_nodes` gained `columns`.
- Merged `entries` take several groups and a node filter (2026-11-08, for the
  page's merged log, whose group, node and search filters had applied only to
  the rows already loaded): `EntryQuery.signature` may be comma-separated (each
  resolved by `cluster.resolve_signatures`, then `merged_entries(signatures=)`),
  and `EntryQuery.node` (`--node`, `node=`) keeps those nodes; the window is
  resolved over all nodes first. With a set both are usage errors
  (`EntryQuery.resolve`); a node not in the capture is `invalid_value`.
- `timeline` is a new CLI command for sets too (`timeline.v1`, the twin of the
  existing `GET /api/sets/{ref}/timeline`): the contract's `timeline CAPTURE
  --by-node` needed a `timeline` command, and "otherwise unchanged" meant the
  set's document. `--level`/`--min-level` with a capture and `--window` with a
  set are usage errors (as `--window` for `entries`/`report` of a set).
- `context --all-nodes --window N` and `?all_nodes=1&window=N` take seconds
  (a number; §11.3 wrote `30s`). A set in no capture is `invalid_value` with a
  hint to label it. `--before/--after/--any-thread` do not apply.
- `report CAPTURE` writes the cluster page; `report-file.v1` gained the optional
  keys `capture` and `nodes`. `--max-groups` defaults to 500 for a capture and
  to all for a set; `cluster_payload(max_groups=0)` now means all groups (it
  meant none). `--compare`/`--min-level` with a capture are usage errors.
- The web app fills `urls`: `entries` = `/api/captures/capture:ID/entries`
  (the set id for a one-node capture without id), `context` =
  `/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30`, `set_report` =
  `/sets/{set}`. The payload is cached as bytes per member set versions, nodes
  and options (`RAGNALOG_REPORT_CACHE` entries) and the whole cache is
  dropped whenever any set changes (members and baselines may change).
  `/captures/{ref}` is the page, with a navigation bar (sets, node reports);
  the report of a member set gets a node switcher and "Open cluster view"; a
  split upload opens the capture page. At start the server deletes `.spool`
  entries older than 6 hours (left by a crashed ingest).
- The agent manifest lists `capture`, `matrix`, `events` and `interactions` in
  `when`; with the new commands it is about 10 KB (its budget, first 10 KB, is now 16 KB).
