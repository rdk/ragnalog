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
examples=3, max_raw=4000, max_groups=None)` from a `LogSet` (and `store.diff`
for the comparison). JSON-serialisable; timestamps ISO 8601 UTC; sizes bounded
by the options (the 712k-entry sample set yields ~10 MB with 3 examples per
group; `examples`, `max_raw` and `max_groups` trade detail for size). The
build options are echoed under `"options"`; `min_level` filters both the groups
and the comparison, while `stats`/`timeline` always cover the whole set.

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
      "signature": "83d1828c6afb2ead", "coarse_signature": "...",
      "template": "Script field failed on issue: <ISSUE>, field: Finished On",
      "level": 50, "level_name": "ERROR", "logger": "c.o.scriptrunner.customfield.GroovyCustomField",
      "exception_type": "org.codehaus.groovy.control.MultipleCompilationErrorsException",
      "count": 68180, "first_seen": "...", "last_seen": "...",
      "user_count": 12, "thread_count": 40, "source_count": 11,
      "top_users": [{"value": "user1", "count": 10}], "top_threads": [...], "sources": [...],
      "examples": ["<entry id>", ...],        // ids present in "entries"
      "hourly": [[hour_index, count], ...]    // sparse, index into timeline.hours
    }
  ],
  "entries": {                                // example entries only (<= examples per group)
    "<entry id>": {
      "ts": "...", "level_name": "ERROR", "thread": "...", "logger": "...", "user": "...",
      "request_id": "...", "ip": "...", "url": "...", "message": "...", "variables": ["ABC-69517"],
      "exception_type": "...", "exception_chain": [{"type": "...", "message": "...", "frames": n, "omitted_frames": n}],
      "raw": "...",                           // truncated to max_raw chars; "raw_truncated": true when cut
      "source_path": "...", "source_line": 123
    }
  },
  "merges": [{"cluster_id": 1, "drain_template": "...", "signatures": ["..."]}],   // [] when none
  "compare": null | {
    "other": { /* logset.v1 manifest of the baseline, without sources */ },
    "min_level": null, "new": [ /* diff rows */ ], "vanished": [...], "changed": [...], "unchanged_count": n
  }
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
│           drag to select a time window (filters groups to that window)    │
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
- Timeline brush filters to groups with at least one hourly bucket in the
  window; the group sparkline shades the window.
- Compare mode (payload has `compare`): every row shows `count_a → count_b` and
  a delta chip (new / vanished / ×ratio); the compare filter narrows to one
  class. Colors: increase = status critical, decrease = series blue, neutral
  gray — never hue alone, the chip carries text.
- State (filters, sort, selected group, page) lives in `location.hash` so a
  view can be bookmarked or pasted; back/forward work.
- Keyboard: `/` focuses search, `j`/`k` move the selection, `Enter` opens,
  `Esc` closes the drawer, `c` copies the signature.
- Every drawer shows the equivalent CLI commands
  (`atlas-log show <set> <signature>`, `atlas-log entries … --signature`) so a
  human can hand a finding to an agent verbatim.
- Theme: follows `prefers-color-scheme`, toggle persisted in `localStorage`;
  all colors are CSS custom properties on `:root`.
- Print: drawer and filters collapse; the table prints.

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
├── __init__.py       build_report(logset, ...) -> str, write_report(logset, path, ...) -> ReportInfo
├── payload.py        build_payload(...)  (polars/duckdb over the LogSet)
├── render.py         render_html(payload) — inline assets, escape, embed
└── assets/
    ├── template.html
    ├── report.css
    └── report.js
```

Dependency direction unchanged: `report` → `store` → `core`; `cli` → `report`.
The future `webapp` serves `assets/` statically and `payload.py` output from
an API route.

## 4. CLI

`atlas-log report SET [--out FILE] [--compare SET_B] [--min-level L]
[--examples 3] [--max-raw 4000] [--max-groups N] [--open]`

Default `--out` is `<name or id>.html` in the current directory. JSON output:
`{"schema": "report-file.v1", "path", "bytes", "groups", "entries", "compare"}`.
Exit codes as for the other commands.
