# HTML report

Status: design 2026-10-29, implemented in `atlas_log_analyzer.report`.

`atlas-log report SET` renders one analysed log set (optionally compared with a
second one) into a **single self-contained HTML file**: no network requests, no
CDN, works from a file share or an e-mail attachment, in light and dark mode.
It is the static precursor of the future FastAPI app: the page consumes a
`report.v1` JSON payload that the web app will later serve from an endpoint, so
the front-end is written against the payload, never against the store.

## 1. Payload (`report.v1`)

Built by `report.payload.build_payload(logset, *, compare=None, min_level=None,
examples=3, max_raw=4000, max_groups=None, entries_url=None)` from a `LogSet`
(and `store.diff` for the comparison). JSON-serialisable; timestamps ISO 8601 UTC;
sizes bounded by the options (the 712k-entry sample set with a comparison yields
~6.6 MB with 3 examples per group, down from ~9.9 MB before the compaction below;
`examples`, `max_raw` and `max_groups` trade detail for size). The build options
are echoed under `"options"` (plus `truncated_groups`, the number of groups cut
by `max_groups`); `min_level` filters both the groups and the comparison, while
`stats`/`timeline` always cover the whole set.

Size-driven conventions (2026-10-29, still `report.v1`):

- Repeated values are indices or tuples: `entries[id].source` and
  `groups[].sources` index `logset.sources`; `top_users`/`top_threads`/`sources`
  are `[[value, count], ...]`; `compare.new` is `[[signature, count_b], ...]`
  and `compare.changed` `[[signature, count_a, count_b], ...]` (template, level
  etc. come from the group row).
- Empty values are omitted: entry keys `user, ip, url, request_id, variables,
  raw_truncated, preamble` only appear when non-null/non-empty/true; `coarse_signature`
  only when it differs from `signature`. Readers must default them.
- Entries carry no exception data (`exception_chain`/`exception_type` were
  dropped; the stack trace is in `raw`, the outermost type on the group).
- `compare.new`/`changed` list only signatures present in `groups`;
  `compare.counts` holds the true diff totals (`new, changed, vanished,
  unchanged`), so "shown vs total" is visible when `max_groups` trims.
  `compare.vanished` keeps full diff rows (those groups are not in `groups`).
- `entries_url` (only when given; the web app passes
  `/api/sets/ID/groups/{sig}/entries`) lets the page fetch more entries of a group.

```jsonc
{
  "schema": "report.v1",
  "generated_at": "2026-10-29T10:00:00Z",
  "tool_version": "0.1.0",
  "logset": { /* logset.v1 manifest incl. sources */ },
  "stats": { /* LogSet.stats(): entry_count, group_count, parse_errors, levels, loggers, hours, time_range */ },
  "timeline": {                               // per-hour counts by level for the overview chart
    "hours": ["2026-10-24T03:00:00Z", ...],   // contiguous, first..last hour of the set
    "levels": { "ERROR": [n, ...], "WARN": [...], "INFO": [...], "DEBUG": [...], "OTHER": [...] }
  },
  "groups": [                                 // groups.v1 rows, sorted by count desc, at most max_groups
    {
      "signature": "83d1828c6afb2ead", "coarse_signature": "...",   // only when != signature
      "template": "Script field failed on issue: <ISSUE>, field: Finished On",
      "level": 50, "level_name": "ERROR", "logger": "c.o.scriptrunner.customfield.GroovyCustomField",
      "exception_type": "org.codehaus.groovy.control.MultipleCompilationErrorsException",
      "count": 68180, "first_seen": "...", "last_seen": "...",
      "user_count": 12, "thread_count": 40, "source_count": 11,
      "top_users": [["user1", 10]], "top_threads": [["thread", 5]], "sources": [[0, 40], [1, 2]],
      "examples": ["<entry id>", ...],        // ids present in "entries"
      "hourly": [[hour_index, count], ...]    // sparse, index into timeline.hours
    }
  ],
  "entries": {                                // example entries only (<= examples per group)
    "<entry id>": {
      "ts": "...", "level_name": "ERROR", "thread": "...", "logger": "...", "message": "...",
      "user": "...", "request_id": "...", "ip": "...", "url": "...", "variables": ["ABC-123"],
                                              // ^ each omitted when null / empty
      "raw": "...",                           // truncated to max_raw chars; "raw_truncated": true only when cut
      "preamble": "...",                      // banner lines attached before the entry (context.preamble), when any
      "source": 0, "source_line": 123         // source = index into logset.sources
    }
  },
  "merges": [{"cluster_id": 1, "drain_template": "...", "signatures": ["..."]}],   // [] when none
  "compare": null | {
    "other": { /* logset.v1 manifest of the baseline, without sources */ },
    "min_level": null,
    "counts": {"new": n, "changed": n, "vanished": n, "unchanged": n},   // true diff totals
    "new": [["<sig>", count_b], ...], "changed": [["<sig>", count_a, count_b], ...],   // shown groups only
    "vanished": [ /* diff rows */ ]
  },
  "entries_url": "/api/sets/ID/groups/{sig}/entries"   // optional
}
```

The payload is documented in `docs/formats/report.v1.md`.

## 2. Page

One file: `template.html` with `report.css` and `report.js` inlined and the
payload embedded as `<script type="application/json" id="report-data">`
(`</` escaped as `<\/`). Vanilla JS and CSS, no framework, no build step; the
assets live in `src/atlas_log_analyzer/report/assets/` and are read with
`importlib.resources`.

### Layout

```
┌ header ───────────────────────────────────────────────────────────────────┐
│ <name> · <id>  jira · 2026-10-24 03:44 → 10-29 02:45 (5d)  [theme] [data]│
├ overview ─────────────────────────────────────────────────────────────────┤
│ [712,674 entries] [2,953 groups] [146,548 errors] [507,600 warns] [0 unparsed]│
│ timeline: stacked bars per hour by level (status colors), hover tooltip,  │
│           drag or "Last 1h/6h/24h/7d" for a time window: the table counts │
│           only that window                                                │
├ filters (one row) ────────────────────────────────────────────────────────┤
│ [search template/logger/exception] [ERROR ✓][WARN ✓][INFO][DEBUG]          │
│ [logger ▾] [exception ▾] [has exception] [sort: count ▾] [coarse] [merged] │
│ compare: [all | new | vanished | changed]                                 │
├ groups table ───────────────────────┬ detail drawer ──────────────────────┤
│ count▁bar lvl logger template spark │ template, signature (copy),         │
│ ...                                 │ stats, top users/threads/sources,   │
│ (windowed rendering, 100 rows/page) │ hourly chart, merge siblings,       │
│                                     │ compare delta, example entries with │
│                                     │ highlighted variables & collapsible │
│                                     │ stack traces, raw copy, CLI hint    │
└─────────────────────────────────────┴─────────────────────────────────────┘
```

### Behaviour

- Filters compose (AND); counts on chips update to the filtered set. Search is
  case-insensitive substring over template, logger and exception type; a
  leading `sig:` matches a signature prefix. Logger facet is a prefix tree
  (`c.a.j.*`) collapsed to the top 30 by count with "more…".
- "merged" view applies Drain merge proposals: groups in one cluster are shown
  as one row (sum of counts, cluster template) with members listed in the
  drawer. "coarse" groups by `coarse_signature` the same way.
- Time window: drag across the timeline, or pick "Last 1 h / 6 h / 24 h / 7 d"
  (those shorter than the set). `‹`/`›` and the keys `[`/`]` move the window by
  its own length; a click on the chart or "Clear" removes it. With a window the
  page answers "what happened in it":
  - the window bar states its range and its entries, errors and warnings; the
    tiles add "N in the window";
  - the table keeps the groups with entries in the window, and its count column
    becomes *In window* (the whole-set total under it, "of 68.2K"). Count bars,
    sorting (count, first/last seen), level chip counts and the summary use the
    window counts. *Last seen* is the last entry in the window: exact when the
    group's own last entry is in it, else the hour (`10-29 01:xx`). The hourly
    counts cover every entry, so window counts are exact;
  - sparklines draw the window's bars in full and the rest muted;
  - the drawer shows *In window*, *First/Last in window* and a whole-set line.
    In the web app its examples are the group's first entries inside the window
    (the examples URL with `since`/`until`). The static file has only the
    stored examples, so it lists the ones inside the window first.
  - The compare column stays whole-set (its tooltip says so).
- Chart controls: each level in the timeline legend is a toggle (Shift-click
  shows only that level; `hl=WARN,INFO` in the hash). **Mute** (drawer button, or
  `m` on the selected row) leaves a group out of the chart and the window totals,
  so one noisy group (a WARN that is 84% of a set) no longer flattens the errors;
  muted rows stay in the table, dimmed, and the legend says how many are muted,
  with "Unmute" (`mute=<sig>,…`).
- The open group on the timeline: a shaded lane under the axis shows its entries
  per hour on its own scale (darkest at its busiest hour), so even a small group
  shows *when* it happens; when it rises above the baseline it is also drawn as a
  stepped line over the (dimmed) level bars, on the chart's scale. The tooltip adds
  its count.
- The timeline tooltip lists, under the level counts, the three largest groups of
  the hovered bar (muted groups and hidden levels left out): what a spike is made of.
- *New* lane (under the timeline's axis): a dot per hour in which groups were first
  seen (the set's first hour left out, everything starts there), its area by how
  many, its color the highest level among them. A deploy or an incident shows as a
  cluster of new groups; clicking a dot narrows the table to that hour sorted by
  first seen.
- Time zone: the header button switches every printed time, axis label and tooltip
  between UTC and the browser's time zone (shown by its short name, e.g. CET). It
  is a viewer preference like the theme (kept in `localStorage`, shared with the
  cluster page; `tz=local|utc` in a link wins); the window in the hash stays UTC.
- Sort *Spike*: with a window, the group's rate in it over its rate outside it
  ("what started at 14:00?"); without one, its busiest hour over its average
  active hour. The ratio shows under the count (`×37`).
- *Copy for ticket* (drawer) copies the group as Jira wiki markup: template,
  level, logger, exception, counts, first and last seen (and the window), the set,
  the signature, the first example entry and the CLI command.
- Compare mode (payload has `compare`): every row shows `count_a → count_b` and
  a delta chip (new / vanished / ×ratio); the compare filter narrows to one
  class. Colors: increase = status critical, decrease = series blue, neutral
  gray — never hue alone, the chip carries text.
- State (filters, sort, selected group, page) lives in `location.hash` so a
  view can be bookmarked or pasted; back/forward work. The time window is kept as
  UTC times, `t=20261028T0200-20261029T0200` (end exclusive), so a link or a switch
  to another set or node keeps the same hours; a window outside a set's range is
  dropped. Older links with bucket indices (`t=95-118`) still open.
- Keyboard: `/` focuses search, `j`/`k` move the selection, `Enter` opens,
  `Esc` closes the drawer (focus returns to its row), `c` copies the signature,
  `m` mutes the selected group in the chart, `[`/`]` move the time window.
- Every drawer shows the equivalent CLI commands
  (`atlas-log show <set> <signature>`, `atlas-log entries … --signature`) so a
  human can hand a finding to an agent verbatim.
- Theme: follows `prefers-color-scheme`, toggle persisted in `localStorage`;
  all colors are CSS custom properties on `:root`.
- Print: drawer and filters collapse; the table prints.

### Entry cards

Every card has **Raw**: it switches the card to the entry's exact text as in the log file
(the banner lines it carries first, every line, no formatting) and back with **Formatted**.
In the web app, **Open raw ↗** opens the full entry as plain text in a new tab
(`/api/sets/{set}/entries/{id}/raw`; `urls.raw` on the cluster page). **Copy raw** copies the
same text.

An example entry (the drawer's examples; on the cluster page also the merged
log and the ±30 s context) is a card: level, time, thread, user, request, IP and
URL; the message with the masked variables highlighted; the body as a stack
trace (frames tokenised, library frames dimmed, nothing folded); the file and
line; "Copy raw".

- A body of more than 500 lines shows its first 500 and a "show all N lines"
  button, which renders the rest in place from the raw the page already holds
  (the web app loads cut examples in full, `max_raw=0`, before that). The choice
  is kept per entry while the page is open. "Copy raw" always copies the whole
  `raw`. Jira's startup report (one entry of about 12,000 lines) stays light
  until asked for.
- An entry with banner lines attached by the format (`preamble` of a report
  entry, `context.preamble` of an `entries.v1` row) shows them dimmed above the
  message, labelled "banner line before this entry". They are not part of
  `raw`.

`tests/report/test_entry_card.py` runs the cards' code
(`// @entry-card` … `// @entry-card-end` in `report.js` and `cluster.js`) in node
on entries of a real payload.

### Visual rules (from the dataviz method)

- Level colors are **status** colors with a text label, never color alone:
  FATAL/ERROR `#d03b3b`, WARN `#fab219` (dark ink label), INFO muted
  `#898781`, DEBUG/TRACE hairline gray `#c3c2b7`.
- Magnitude (count bars, sparklines, hourly chart) is one sequential hue,
  blue `#2a78d6` light / `#3987e5` dark. Count bars are 4px-rounded, thin, on a
  log scale with the number always printed (`tabular-nums`).
- Chrome: surface `#fcfcfb`/`#1a1a19`, page `#f9f9f7`/`#0d0d0d`, ink
  `#0b0b0b`/`#f0efec`, secondary `#52514e`/`#c3c2b7`, grid `#e1e0d9`/`#2c2c2a`,
  system sans. Hairline grid, no dashes, one axis, tooltips on hover with
  the value also readable in the table.

## 3. Code

```
src/atlas_log_analyzer/report/
├── __init__.py       build_report(logset, ...) -> str, write_report(logset, path, ...) -> ReportInfo,
│                     write_cluster_report(payload, path) -> ClusterReportInfo
├── payload.py        build_payload(...)  (polars/duckdb over the LogSet)
├── render.py         render_html(payload=None, *, data_url, inline_assets, asset_base), asset_text(name),
│                     render_cluster_html(payload=None, *, data_url, asset_base)
└── assets/
    ├── template.html
    ├── report.css
    ├── report.js
    ├── cluster.html  the cluster page (§5)
    ├── cluster.css
    └── cluster.js
```

Dependency direction unchanged: `report` → `store` → `core`; `cli` → `report`.
The future `webapp` serves `assets/` statically (`asset_text(name)`) and
`payload.py` output from an API route; its page is
`render_html(data_url=..., inline_assets=False)`: an empty `#report-data`
element, `data-report-url` on `<html>` and `<link>`/`<script src>` under
`asset_base` (default `/static/`).

## 4. CLI

`atlas-log report SET [--out FILE] [--compare SET_B] [--min-level L]
[--examples 3] [--max-raw 4000] [--max-groups N] [--open]`

Default `--out` is `<name or id>.html` in the current directory. JSON output:
`{"schema": "report-file.v1", "path", "bytes", "groups", "entries", "compare"}`.
Exit codes as for the other commands.

## 5. Cluster page

Status: page implemented 2026-11-05 (`docs/cluster.md` §8, contract §11.3/§11.4); the
`cluster-report.v1` payload comes from `service/cluster.py`. `atlas-log report CAPTURE`
writes the static page, the web app serves `/captures/{ref}` with `urls` filled
(`docs/cluster.md` §11.6). The sample payload
`tests/report/data/cluster_payload.json` (two invented nodes) is the page's fixture;
`tests/report/cluster_data.py` derives 5- and 10-node variants from it.

**Rendering.** `render_cluster_html(payload)` inlines `report.css`, then `cluster.css`
and `cluster.js` (placeholders `/*__REPORT_CSS__*/`, `/*__CLUSTER_CSS__*/`,
`/*__CLUSTER_JS__*/`) and embeds the payload in `#cluster-data` (`</` escaped): one
file that works from `file://`. The web page is
`render_cluster_html(None, data_url=…, asset_base="/assets/report/")`: `data-report-url`
on `<html>`, `<link>`s to `report.css` and `cluster.css` and `<script src>` to
`cluster.js` under `asset_base` (the web app must serve those three via `asset_text`).
`write_cluster_report(payload, path)` writes the static file. `cluster.js` shares no
code with `report.js`; its formatting, stack-trace and column-chart helpers are copies.

**Layout.**

- Header: cluster, capture id, one chip per node (colour swatch plus `N1 NODE1`; a
  "report ↗" link when `urls.set_report` is set), window mode and range, node and group
  counts, the clock-skew estimate ("estimate, not applied"), time zone toggle (UTC or
  the browser's zone by its short name, as on the set report: every printed time, axis
  tick and tooltip follows it; same `localStorage` key, `tz=local|utc` in a link wins,
  the window in the hash stays UTC), theme toggle, JSON download.
- Node cards: entries, errors, warnings, errors/h, events (totals, or within the
  brushed window from the timeline), coverage with gap count and length, top error
  (opens the group), triage verdict chip and baseline, events by kind, "Groups on N1".
- Timeline: one SVG, one row per node on a shared time axis (ticks on the shown zone's
  hours and midnights). Each row is an event lane
  (a glyph per kind in ink colour; legend under the chart) above a band of columns
  stacked by level in the report's status colours. Hatching marks time outside a node's
  coverage and its gaps; the window (`capture.window`) is a ruler above the rows with
  hairlines at its edges. Arrows go from the lane of the node that logs a mention to
  the lane of the node it names: one per event with `subject_node`, plus fainter ones
  per bucket for `interactions.mentions` groups (from their `hourly` on the reporting
  node) where no event arrow exists; "Arrows" hides them. Job runs (`job_run`) are left out of the lanes unless "Job runs" is on (`#jobs=1`): they repeat on a schedule and would hide the rare events. y-scale: shared, per node,
  or % of node (counts over the node's entries in the range). A selected group is drawn
  as a line on every band, on the same axis over faded columns; "Fit y to group" (on by
  default) scales the axis to the group and drops the columns. Hover shows the bucket's
  levels for that node and every node's total; hovering a lane lists its events (text,
  node, named node, time); clicking one opens the merged log at that moment. Dragging
  on any band sets the window of every view; a click clears it. The window bar has the
  report's presets, `‹`/`›` (`[`/`]`) and the window's entries and errors. With a window,
  the groups table counts only the window. Total becomes *In window* (capture total
  under it), the node columns, bars, sorting, spread filter and node filter use window
  counts, and `spread`/`skew` are recomputed from the window's per-node counts and
  volumes (the rule of `matrix`). `lead` stays the capture's and is dimmed, because
  buckets are too coarse to time a lead inside a window. The drawer adds an *In window*
  column per node, and in the web app its examples are each node's first entries in
  the window.
- Tabs in the sticky bar:
  - *Groups by node*: level, template, total, one count-and-bar column per node in node
    colours (log scale; past 8 nodes a heat strip of one cell per node), `spread` chip
    (`all`, `only N1`, `×35 N1` for the highest volume-normalised rate) and `lead` chip
    (`N2 first, +3 h`). Filters: search (`sig:` prefix), levels, spread, node; sort by
    count, skew, lead, level, template or the count on one node (click a node header).
    100 rows per page. The drawer has per-node counts, rates and first/last seen,
    per-node lines over the timeline buckets (no line where a node has no logs), example
    entries per node in tabs (collapsible stack traces, "±30 s on all nodes"), the
    co-bursts, mentions and events involving the group, and CLI commands
    (`atlas-log matrix CAPTURE --signature SIG`, `timeline --by-node`, `entries`,
    `show SET SIG` per node).
  - *Interactions*: co-bursts and event pairs in one wide card, one row each, strongest
    first (share of hits, then lift): source (node badge, template on one line, full text
    in its title, `+N` other groups that burst with it) → target, a hits meter ("7 / 8"),
    median lag, lift chip, and "show in log" (co-bursts: both groups on their nodes in the
    merged log) or example times (event pairs, those in the window). Below, side by side:
    the N×N mention matrix (a cell lists that edge's entries in the merged log) with the
    groups behind each edge (a group outside the payload's `groups`, which `max_groups`
    caps, shows a short signature and the text of one of its events, with "entries" to
    list it in the merged log), and concurrent jobs and sessions across nodes as compact
    tables (job, nodes, same-minute slots with a bar, example times). Last, full width,
    an events table (the table view of the lanes, filtered by the window).
  - *Merged log*: entries of all nodes interleaved by time; node badge, level chip,
    logger, message; a click expands the entry (raw, trace, "±30 s on all nodes", open
    group, copy raw). Filters: minimum level, nodes, search, and groups (set from the
    matrix or "show me"). 200 rows at a time. Static file: the payload's example
    entries, with the equivalent `atlas-log entries CAPTURE --since … --until …`. Web
    (`urls.entries` set): pages fetched from that URL with `since`, `until` (the window,
    or from 60 s before a clicked event), `min_level`, `limit=200` and `offset` (the
    `next_offset` of the `entries.v1` answer); node, search and group filters apply to
    the loaded rows. "±30 s" fetches `urls.context` with `{set}` and `{id}` filled in;
    without it the page uses the entries it has.

**State** in `location.hash`: `tab` (`g`, `i`, `l`), `q`, `lv`, `sp`, `nd`, `sort`, `p`,
`sig`, `ex` (example tab), `t` (the time window, as on the set report), `y`, `fit=0`, `arrows=0`, `ml`, `mn`,
`mq`, `ms` (signatures), `ctx`/`cts` (context anchor), `at` (log moment), `theme`, `tz`.
Keyboard as in the report, plus `1`/`2`/`3` for the tabs; in the merged log `j`/`k` move
and `Enter` expands. Theme: the report's bootstrap and `localStorage` key. Print: light
colours, controls and drawer hidden, charts scaled to the page.

**Node colours** (`--n1` … `--n8`; fixed order = `payload.nodes`; never a level colour;
always with the `N1` label; nodes past 8 are neutral grey with their label):

| Slot | Light | Dark |
|---|---|---|
| N1 blue | `#2a78d6` | `#3987e5` |
| N2 green | `#008300` | `#008300` |
| N3 magenta | `#e87ba4` | `#d55181` |
| N4 violet | `#6250d6` | `#9085e9` |
| N5 aqua | `#1baf7a` | `#199e70` |
| N6 orange | `#eb6834` | `#d95926` |
| N7 teal | `#0f8ea6` | `#1a9cb3` |
| N8 brown | `#a3601c` | `#b5772e` |

Red and yellow are left out (ERROR and WARN). Validated with the dataviz method
(`validate_palette.js`): adjacent pairs CVD ΔE ≥ 9.2 and normal-vision ΔE ≥ 19.5 in
both modes; the first three slots also pass all-pairs (CVD 13.0, normal 26.5). Light
N3 and N5 are below 3:1 on the surface, so the label is always printed.

**Payload tolerances** (beyond §11.3): any key may be missing, null or empty (no
events, interactions, gaps, verdict or urls; one node; no groups). A node's `events` is
a count or `{kind: count}`; `baseline` an object `{id, name}` or a string; `gaps` items
`[since, until]` or `{since, until}`; timeline level keys are the report's buckets
(`ERROR`, `WARN`, `INFO`, `DEBUG`, `OTHER`) or level names (mapped, `FATAL` → ERROR).
Entries show `source_path` (the cluster payload has no `sources` list) and get their
group from `signature` or the groups' `examples`. `interactions.delta_s` (default 300)
phrases co-bursts. `spread` is derived when missing. Payload text is escaped everywhere.
