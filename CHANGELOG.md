# Changelog

All notable changes to atlas-log-analyzer. Versions follow
[semantic versioning](https://semver.org/) (before 1.0 a minor version may
change the CLI or stored formats; the notes say when).

Stored sets record the version that built them (`tool_version`); `atlas-log
sets` marks a set `stale` when its signatures are not comparable with today's,
and `atlas-log upgrade --all` rebuilds those from the stored entries.

## [Unreleased]

## [0.3.0] - 2026-11-10

A redesign of the web UI and the HTML reports, and a new page listing all
entries of a group. The CLI, the JSON documents and the stored formats are
unchanged, so sets built by 0.2.x stay comparable.

### Added

- Entries page: `/sets/{ref}/entries` and `/captures/{ref}/entries` list every
  entry of a group (or any filter of `entries`) in a table, opened in a new tab
  from "All N entries ↗" in a group's drawer. A time histogram where dragging or
  clicking sets the window, a click on a thread, user, node or level adds it as a
  filter, rows open the full entry card; the URL holds the view.
- Full screen width toggle on every page (`w`; remembered): no centred frame and
  a wider drawer.
- Drawer: a drag handle to resize it (remembered; double-click resets), section
  links that follow scrolling.
- Entry cards show the logger in brackets as the log writes it.

### Changed

- One layout system on every page: compact controls and rows, consistent spacing.
- Header in two rows: a breadcrumb with the set's views (Report, Triage, Entries,
  Cluster view, Compare) as tabs, then the title with the settings (time zone,
  theme, width, help). IDs & CLI and the JSON download moved to a "⋯" menu.
- Text kinds: loggers (package muted), exceptions, times, thread and user values,
  identifiers and template placeholders each have one look on every page; the
  entry message is set apart from its metadata and tools.
- Stack traces and raw entries are not wrapped (long lines scroll) and use a
  slightly smaller font.
- Dark theme is lighter, with WCAG AA contrast for text and links; level colours
  are shared by badges, filter chips and charts, INFO is blue-tinted.
- Sticky filter bars and table headers work at every width; Reset is disabled
  until something is filtered; empty and loading states say what to do.
- CI and release workflows use Node 24 actions (`actions/checkout@v5`,
  `astral-sh/setup-uv@v7`).

## [0.2.1] - 2026-11-08

The first published release (0.2.0 was tagged, but its container image did not
start, so it has no release). Install it with
`uv tool install "atlas-log-analyzer[web] @ git+https://github.com/rdk/atlas-log-analyzer@v0.2.1"`
or run the container image (see the README). What it does is listed under 0.2.0
in CHANGELOG.md; 0.2.1 adds these fixes.

### Fixed

- Container image: the program starts (its environment is built where it runs).
- `groups --since T --until T` lists only groups with an entry in the window, not
  every group whose first-to-last span covers it.
- A missing output folder is named (`report --out missing/r.html`).
- `batch`: a line that starts with a global option says so.

## [0.2.0] - 2026-11-08 [no release]

Everything below; tagged only.

### Ingest and store

- Jira Data Center logs (`atlassian-jira.log*`, rotated and gzipped rotations
  `x.log.3.gz`) into log sets: Parquet entries and groups with stable signatures
  (level, logger, masked message, exception chain), variables kept per entry.
- Cluster exports: a support ZIP or folder of several nodes splits into one set
  per node (`--split`), labelled with node, cluster and capture. Files from a ZIP
  are named `export.zip/<member>`. A Jira home's side logs (`-security`,
  `-outgoing-mail`, `-slow-queries`) are skipped, not mixed into the main set.
- Duplicate files are skipped (a gzipped copy counts by its content, so it is the same
  file); a cut-short `.gz` is a clear error naming the file.
- `upgrade` rebuilds stale sets from their stored entries alone (no log files),
  equal to a fresh ingest; it also renames the spool paths that ZIP ingests of
  earlier development builds recorded.

### CLI

- JSON-first commands with documented schemas and exit codes: `groups`, `show`,
  `entries`, `frames`, `context`, `diff`, `triage`, `query` (DuckDB SQL), `report`,
  and for clusters `captures`, `capture`, `matrix`, `events`, `interactions`.
- `manifest` (under 10 KB) and `batch` for agents; the CLI and the HTTP API return
  the same documents (a parity contract test keeps them equal).
- The merged log of a capture takes several groups and `NODE:SIGNATURE` (a group
  on one node only).
- `--logger com.atlassian.jira` also matches the forms Jira abbreviates
  (`c.a.jira.index.X`, `c.a.j.issue.index.X`).
- Memory stays bounded on large sets: a page of entries reads only its row groups
  (about 110 MB for a 644k-entry set), and a whole set (`entries --limit 0`, `query`)
  is written a batch at a time (1.3 GB peak for 870 MB of JSON, was 14 GB).

### Report and cluster page

- One self-contained HTML report: timeline by level, group table, drawer with
  examples, stack traces and CLI commands, compare against a baseline.
- Time windows with spike sorting, a lane marking where new groups first appear,
  the open group drawn over the timeline, "moves with" (groups with the same
  hourly pattern), mute, search marks, and a view kept in the URL.
- Times in UTC or local time (`z`, one preference for every page).
- Cluster page: nodes side by side, cross-node "together" moments, an errors-only
  chart, the merged log of all nodes, events, co-bursts and event pairs.

### Web app (`atlas-log serve`, extra `web`)

- Set index with triage verdicts, triage pages, the report and cluster page,
  background uploads with progress, and the JSON API under `/api` (OpenAPI at
  `/docs`).
- Security: binds to loopback by default; `--token` guards pages and `/api`;
  changes posted from other sites are refused; on loopback only loopback host
  names are answered (DNS rebinding), plus `--allowed-host` names behind a
  reverse proxy; uploads need `Content-Length`; a warning when it listens on the
  network without a token.
- The newest capture's cluster page is built at startup, so its first open is fast.

[Unreleased]: https://github.com/rdk/atlas-log-analyzer/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/rdk/atlas-log-analyzer/releases/tag/v0.2.1
[0.2.0]: https://github.com/rdk/atlas-log-analyzer/releases/tag/v0.2.0
