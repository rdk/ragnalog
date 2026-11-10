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
  "tool_version": "0.3.0",
  "logset": { /* logset.v1 manifest incl. sources */ },
  "stats": { /* LogSet.stats(): entry_count, group_count, parse_errors, levels, loggers, hours, time_range */ },
  "timeline": {                               // per-hour counts by level for the overview chart
    "hours": ["2026-10-24T03:00:00Z", ...],   // contiguous, first..last hour of the set
    "levels": { "ERROR": [n, ...], "WARN": [...], "INFO": [...], "DEBUG": [...], "OTHER": [...] },
    "clipped": 2                              // only when > 0: entries outside the kept span
  },                                          // (at most 400 days, the densest: a clock years off)
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

One file: `template.html` with `report.css`, `entry-card.js`, `tips.js` and `report.js` inlined and the
payload embedded as `<script type="application/json" id="report-data">`
(`</` escaped as `<\/`). Vanilla JS and CSS, no framework, no build step; the
assets live in `src/atlas_log_analyzer/report/assets/` and are read with
`importlib.resources`.

### Layout

```
<name>                                             [UTC] [theme] [width] [?] [⋯]
712,674 entries · 2,953 groups · ● 146,548 errors · ● 507,600 warnings · jira · 10-24 03:44 → 10-29 02:45 UTC 5 d
┌ overview card ────────────────────────────────────────────────────────────┐
│ ENTRIES PER HOUR  ERROR WARN INFO DEBUG        Drag to select · Last 1h … │
│ timeline: stacked bars per hour by level (status colors), hover tooltip,  │
│           drag or "Last 1h/6h/24h/7d" for a time window: the table counts │
│           only that window                                                │
└───────────────────────────────────────────────────────────────────────────┘
 filters (sticky; shows <name> and the scope once it sticks)
 [search] [ERROR ✓][WARN ✓][INFO][DEBUG] [logger ▾] [exception ▾] [has exception]
 [coarse] [merged] [all | new | vanished | changed]                  [sort] [reset]
 1-100 of 100 groups · N entries                                      [‹ Prev] Page 1 of 1 [Next ›]
┌ groups table card (sticky header) ──┬ detail drawer ──────────────────────┐
│ count▁bar lvl logger template spark │ ↑ ↓ 3 of 100 · level · template     │
│ ...                                 │ stats, top users/threads/sources,   │
│ (windowed rendering, 100 rows/page) │ hourly chart, merge siblings,       │
│                                     │ compare delta, example entries,     │
│                                     │ ▸ Identifiers & CLI (collapsed)     │
└─────────────────────────────────────┴─────────────────────────────────────┘
```

Spacing and sizes come from the shared layout tokens (`:root` in `report.css`: a 4/8/12/16/24/32
spacing scale, one page edge `--pad-x`, 28 px toolbar controls and 22 px controls inside cards,
tables, the drawer and dialogs; table rows about 30 px); the report restyles no primitive, it only
colours it. The page header, filter-bar controls, drawer and IDs & CLI dialog are shared with the
cluster page. Its right side holds the settings as ghost icon buttons (time zone with its name,
theme as a moon or sun that shows what a click switches to, full screen width, help) and last a
"⋯" menu, *For scripts and agents*: *Identifiers & CLI commands…* and *Download the data*
(the payload as JSON) — kept out of a human reader's way. In the web app a navigation row above the header carries the views; the
standalone file has only the header.
The page header sits on the page background: the name and the actions, then one meta line
with the key figures (each with its tooltip; with a window it adds "N in window" notes), the
set's format, time range and source count. The legend and the window controls share the
overview card's head. The top is kept short (in the web app at 1500 x 1000 the table header
is about 430 px down): hashes and the CLI are not shown by default. The "⋯" menu's
*Identifiers & CLI commands…* opens a dialog with the set id, format, node, source files, the open group's signatures and the
commands to reproduce the view (each with a copy button); in the drawer the same sits in a
collapsed *Identifiers & CLI* part at the end, which also reveals the signatures in member lists.
Orientation: the sticky filter bar adds the set name and the number of groups listed once it
sticks (until then the header says it), the table header sticks under it (the stuck bar stays one
row: below 1500 px it drops the view options and Reset, below 1200 px the facets, below 800 px
the search; the scope line still names every active filter), the open row keeps a
blue bar and tint (and is scrolled into view when the drawer opens or closes), and the drawer
bar (40 px) names the open group and its position. A handle on the drawer's left edge resizes
it (drag, or arrow keys when focused; double-click or Home resets; the width is remembered,
the table keeps at least about 440 px). In the drawer the group's template is the
headline (16 px), its logger and exception the subtitle, and the figures (entries, first and
last seen, users, threads, sources; on the cluster page entries, in window, nodes) a quiet panel
with the label above each value; section heads are a label, a muted count and an action at the
right ("All N entries ↗"). Reset is dimmed and disabled until a filter, the sort
order, the view or an open group differs from the default (the cluster page also counts the log
filters; after it is used, focus moves to the search field); clicking the stuck bar's name and scope line scrolls back to the full filter bar. An
empty table says that no group matches all filters and offers each active filter as a chip to
remove, or "Remove all filters".

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
  - the window bar states its range; the figures in the header add "N in window"
    (entries, groups, errors, warnings);
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
  With no group open, a search, logger, exception or has-exception filter draws
  the groups the table lists the same way ("17 listed groups"): when a search's
  matches happen. Matches of the search are marked in the table's templates.
- The timeline tooltip lists, under the level counts, the three largest groups of
  the hovered bar (muted groups and hidden levels left out): what a spike is made of.
- *First* lane (under the timeline's axis): a dot per hour in which groups were first
  seen (the set's first hour left out, everything starts there), its area by how
  many, its color the highest level among them. A deploy or an incident shows as a
  cluster of new groups; clicking a dot narrows the table to that hour sorted by
  first seen, the groups that started in it listed first. With a window the label
  says how many groups started in it (a button sorts by first seen).
- Time zone: the header button switches every printed time, axis label and tooltip
  between UTC and the browser's time zone (shown by its short name, e.g. CET). It
  is a viewer preference like the theme (kept in `localStorage`, shared with the
  cluster page; `tz=local|utc` in a link wins); the window in the hash stays UTC.
- Sort *Spike*: with a window, the group's rate in it over its rate outside it
  ("what started at 14:00?"); without one, its busiest hour over its average
  hour over the whole set (a group seen in one hour only is a spike). The ratio shows under the count (`×37`).
- *Moves with* (drawer, *Related*): up to six signature groups whose entries per
  hour rise and fall with the open group's (Pearson's r >= 0.6 over every hour of
  the set, groups of 5+ entries in 3+ hours), with r and a sparkline: a job's
  start and end, an error and the warning it causes, groups split by an unmasked
  value. Computed in the browser from the payload's hourly counts.
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
  `Esc` closes the drawer (focus returns to its row), `w` toggles the full screen width,
  `c` copies the signature,
  `m` mutes the selected group in the chart, `z` switches UTC / local time,
  `[`/`]` move the time window.
- Every drawer has the equivalent CLI commands
  (`atlas-log show <set> <signature>`, `atlas-log entries … --signature`) in its
  collapsed *Identifiers & CLI* part, so a human can hand a finding to an agent verbatim.
- Full screen width: the header's ⤢ toggle (key `w`) drops the centred 1680 px frame, narrows
  the page edge to 16 px and lets the drawer grow (up to half the screen, 1200 px). One setting
  for every page of the app (`localStorage` `atlas-log-wide`), applied before the first paint.
- Theme: follows `prefers-color-scheme`, toggle persisted in `localStorage`;
  all colors are CSS custom properties on `:root`.
- Print: drawer and filters collapse; the table prints.

### Entry cards

Every card has **Raw**: it switches the card to the entry's exact text as in the log file
(the banner lines it carries first, then every line, character for character, with the
same token colours as the formatted view) and back with **Formatted**.
In the web app, **Open raw ↗** opens the full entry as plain text in a new tab
(`/api/sets/{set}/entries/{id}/raw`; `urls.raw` on the cluster page). **Copy raw** copies the
same text.

An example entry (the drawer's examples; on the cluster page also the merged
log and the ±30 s context) is a card in three parts. The header strip is metadata, quiet:
level, time (date and milliseconds muted), then label and value pairs for thread, user,
request, IP and URL, each value in its log colour, and the logger in brackets as the log
file writes it (`[c.a.j.issue.index.DefaultIndexManager]`: package muted, class name in
the class colour). The message is the content, in its own block (ink, mono; the first line
is the headline, continuation lines are quieter) with the masked variables highlighted, and
its body as a stack trace (frames tokenised, library frames dimmed, nothing folded; in the
drawer a long trace scrolls inside the card). Stack traces and the raw view are code: a little
smaller than the message, never wrapped, a long line scrolls sideways. The footer is tooling behind a hairline: the
file and line, then Raw, Open raw and Copy raw. Every kind of small text has one treatment on
every page (`report.css`, "text kinds"; markup from `AtlasEntryCard.loggerHtml`, `excHtml`,
`tsHtml`, `kvHtml`, `nilHtml`): a logger has its package muted and its class name in ink, an
exception its class name in the exception colour, a time its date and milliseconds muted,
identifiers (thread, signatures) are mono and muted, template placeholders are dashed italic
chips, an empty value is a faint dash.

Log text is formatted the same way on every page (`logInline`, `frameHtml` in
`entry-card.js`: `AtlasEntryCard.create(hooks)`, which each page calls once with
its own helpers: badges, the shown time zone, where an entry is looked up, the raw
link and extra buttons such as the cluster page's "±30 s on all nodes"). A body with a stack trace
is a dark code panel in both themes. In a frame the module, package (dimmed), class,
method, file and line have their own colours, and so do the exception header's
`Caused by:`, package and class. Platform and framework frames (JDK, Tomcat, Spring,
OSGi, Atlassian plumbing such as `com.atlassian.plugin`, `.event`, `.jira.web`) are
dimmed to one colour. Atlassian code and the libraries Jira ships stay plain. Any
other package is taken for an app's own code (a third-party plugin) and marked with
an accent bar. The message, the banner lines, the trace's other lines, the raw view
and the group templates get the same tokens: quoted strings; URLs, host names and IP
addresses, file paths (Unix, Windows, file names with a known extension) and
`File.java:123` in one colour; qualified class names with the package dimmed,
`Class.method(` calls and exception names; numbers with their unit (`1234 ms`,
`12.5 MB`, `98%`, versions); timestamps, UUIDs, hex and request ids and issue keys in
a quiet slate; `true`/`false`/`null`; the names in `key=value` dimmed; level words;
SQL keywords when the text holds a statement. Numbers inside names (`exec-12`,
`UTF-8`) and template placeholders stay as they are. Templates in tables and tooltips
only dim the package.

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

`tests/report/test_entry_card.py` runs the cards' code (`entry-card.js`) in node
on entries of a real payload.

In the web app the drawer's "Example entries" head carries a right-aligned link
**All N entries ↗** (a new tab) to the entries page (`docs/webapp.md`) with the
group's signature (a coarse group: `coarse_signature`; merged Drain3 rows have none);
with a time window selected the link adds `since`/`until` and reads "All N entries in
window ↗". On the cluster page it opens the capture's page with the signature
(all nodes). A standalone report file has no server, so it shows no link (its
"Identifiers & CLI" part gives `atlas-log entries SET --signature SIG`).

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
    ├── report.css    colour tokens (light/dark), shared by every page
    ├── tips.js       rich tooltips (data-tip, data-tip-k, titles), shared by every page
    ├── entry-card.js entry cards and log text formatting, shared by report, cluster and entries pages
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
`entry-card.js`, `tips.js` and `cluster.js` under `asset_base` (the web app serves them via `asset_text`).
`write_cluster_report(payload, path)` writes the static file. `cluster.js` shares the entry cards and log formatting with `report.js`
(`entry-card.js`); its column-chart helpers are copies.

**Layout.**

- Header, one row: cluster, window mode and range, group counts, the clock-skew
  estimate ("estimate, not applied"), time zone toggle (UTC or
  the browser's zone by its short name, as on the set report: every printed time, axis
  tick and tooltip follows it; same `localStorage` key, `tz=local|utc` in a link wins,
  the window in the hash stays UTC), theme toggle, full width, help, and the "⋯" menu
  (identifiers & CLI, JSON download; for scripts and agents). A capture across a DST
  change names both zones (`CEST/CET`), and a range names each end's zone when they
  differ (`02:00 CEST – 02:00 CET`, the repeated hour); axis ticks follow the zone's
  wall clock (a skipped hour has no tick, a repeated hour one, dates only at midnight).
- Hashes and the CLI are not shown by default. The "⋯" menu's *Identifiers & CLI
  commands…* opens a dialog with the
  capture id, the cluster, each node's log set (name and id), the capture's commands
  and, when a group is open, its signature and commands, each with Copy. In the drawer
  they are a collapsed *Identifiers & CLI* part (its section link opens it); `c` still
  copies the open group's signature.
- Node rows (one slim row per node, in the overview card above the timeline): colour
  swatch and `N1 NODE1`, entries, errors, warnings, errors/h (totals, or within the
  brushed window from the timeline), events with how many are job runs on wide pages,
  triage verdict chip and the set report link (`urls.set_report`). The node name toggles
  "groups on this node" (not with one node); its tooltip has coverage with gap count and
  length, top error and the baseline.
- Timeline: one SVG, one row per node on a shared time axis (ticks on the shown zone's
  hours and midnights). Each row is an event lane
  (a glyph per kind in ink colour; legend under the chart) above a band of columns
  stacked by level in the report's status colours. Hatching marks time outside a node's
  coverage and its gaps; the window (`capture.window`) is a ruler above the rows with
  hairlines at its edges. Arrows go from the lane of the node that logs a mention to
  the lane of the node it names: one per event with `subject_node`, plus fainter ones
  per bucket for `interactions.mentions` groups (from their `hourly` on the reporting
  node) where no event arrow exists; "Arrows" hides them. Job runs (`job_run`) are left out of the lanes (and the events table) unless "Job runs" is on (`#jobs=1`): they repeat on a schedule and would hide the rare events. `reindex` has its own glyph (a circular arrow), apart from `index_replay`'s dot. y-scale: shared, per node,
  % of node (counts over the node's entries in the range), or *Errors* (`y=err`: only the
  ERROR column of every node on one scale, so a flood of WARN does not flatten them; a
  spike above 4× the 95th percentile is clipped and labelled `↑ 18.3K`). The y-scale and
  the two toggles wrap as one group on a narrow page. With two or more nodes a thin
  *together* lane above the first node marks cross-node moments: the drawn buckets
  holding a timeline bucket (an hour, or 15 min) in which two or more nodes log more
  than 3× their own usual errors (usual: the median of that node's timeline buckets with
  errors; found at that resolution, so a narrow chart marks the same moments). Each mark is a button (Tab, Enter) with a tooltip
  naming the nodes, their errors and the factor; it sets the window to that bucket and
  opens "±30 s on all nodes" at its busiest moment (web: the 10 s with the most errors on
  two or more nodes, from `urls.entries` with `min_level=ERROR`; static: the merged log
  of the window at ERROR on those nodes). Without a timeline the y-scale, Job runs and
  Arrows controls are hidden. A selected group is drawn
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
- The sticky bar: the tabs and the scope (chips for the window, the node filter and the
  open group, each with × to clear it; "Whole capture, all nodes" when nothing narrows
  the views), the filters in a second row; once it sticks it also names the capture. The
  open row is marked with a blue edge. The drawer bar holds prev/next, the position
  ("3 of 417"), the open group's level and template, and Close; the
  section links are a row below it.
- Tabs in the sticky bar:
  - *Groups by node*: level, template, total, one count-and-bar column per node in node
    colours (log scale; past 8 nodes a heat strip of one cell per node), `spread` chip
    (`all`, `only N1`, `×35 N1` for the highest volume-normalised rate) and `lead` chip
    (`N2 first, +3 h`). Filters: search (`sig:` prefix), levels, spread, node; sort by
    count, skew, lead, level, template or the count on one node (click a node header).
    100 rows per page. The drawer has per-node counts, rates and first/last seen,
    per-node lines over the timeline buckets (no line where a node has no logs), example
    entries per node in tabs (collapsible stack traces, "±30 s on all nodes"), the
    co-bursts, mentions and events involving the group, and, collapsed, the CLI commands
    (`atlas-log matrix CAPTURE --signature SIG`, `timeline --by-node`, `entries`,
    `show SET SIG` per node). With one node there are no spread or lead columns, no
    spread or node filter and no per-node sorts. The drawer bar stays on one line (its
    section links scroll).
  - *Interactions*: co-bursts and event pairs in one wide card, one row each; co-bursts
    strongest first (share of hits, then lift), event pairs most often first (a pair seen
    once has a huge lift and tells little), each table with a one-line plain reading of a row: source (node badge, template on up to two
    lines, full text in its title, `+N` other groups that burst with it) → target, a hits
    meter ("7 / 8"), median lag, lift chip (above ×100 shown as `>×100`, the exact lift in
    its title), and "show in log" (co-bursts: each group on its own node in the merged
    log, `signature=NODE:SIG,...`) or example times (event pairs, those in the window). Below, side by side when
    each card gets 540 px (else stacked):
    the N×N mention matrix (a cell lists that edge's entries in the merged log) with the
    groups behind each edge (a group outside the payload's `groups`, which `max_groups`
    caps, shows a short signature and the text of one of its events, with "entries" to
    list it in the merged log), and concurrent jobs and sessions across nodes as compact
    tables (job, nodes, same-minute slots with a bar, example times). Last, full width,
    an events table (the table view of the lanes, filtered by the window): one chip per
    kind with its count toggles the kind (`ek` in the hash; the job run chip is the "Job
    runs" toggle, so the table shows job runs exactly when the lanes do); events of one
    kind on one node in the same second fold into one row ("15 health checks failed",
    "show all" lists them). The time of each row is a button (keyboard) and the row a
    click target: both open the merged log at that event.
  - *Merged log*: entries of all nodes interleaved by time; node badge, level chip,
    logger, message; a click, or Enter / Space on the focused row (a button with
    `aria-expanded`; the row with the cursor is the Tab stop, ↑/↓ move), expands the
    entry (raw, trace, "±30 s on all nodes", open group, copy raw). Filters: minimum
    level, nodes, search, and groups (set from the matrix, "entries", "show in log" or
    "show me"); its first page shows a spinner and "Loading the log…" until it arrives. Static file: the payload's example entries, 200 rows at a time, with the
    equivalent `atlas-log entries CAPTURE --since … --until …`. Web (`urls.entries` set):
    pages of 200 fetched from that URL with `since`, `until` (the window, or from 2 s
    before a clicked event, inside the window), `min_level`, `signature` (the groups,
    comma-separated), `node` (comma-separated), `contains` (the search, on the raw text),
    `limit=200` and `offset` (the `next_offset` of the `entries.v1` answer); every page is
    appended as it arrives. Scrolling near the end of the list, "Load 200 more" or `j` on
    the last row loads the next page (the cursor then moves on). An event opens the log at
    its own entry (`entry_id`, `ae` in the hash), else at the first entry at or after its
    time; while the loaded rows end before it, the next pages are fetched (at most 25).
    Setting a window that does not contain that moment drops it. Rows are redrawn only
    when what they show changes (range, filters, zone, the moment); new pages are
    appended. "±30 s" fetches `urls.context` with `{set}` and `{id}` filled in; without it
    the page uses the entries it has.

**State** in `location.hash`: `tab` (`g`, `i`, `l`), `q`, `lv`, `sp`, `nd`, `sort`, `p`,
`sig`, `ex` (example tab), `t` (the time window, as on the set report), `y` (`node`, `pct`,
`err`), `fit=0`, `arrows=0`, `jobs=1`, `ml`, `mn`, `mq`, `ms` (signatures), `ctx`/`cts`
(context anchor), `at` (log moment) and `ae` (its entry id), `ek` (event kinds hidden
from the events table), `theme`, `tz`. Keyboard as in the report, plus `1`/`2`/`3` for
the tabs; in the merged log `j`/`k` move (past the last loaded row: the next page) and
`Enter` expands. Theme: the report's bootstrap and `localStorage` key. Print: light
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
