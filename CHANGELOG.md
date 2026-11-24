# Changelog

All notable changes to Ragnalog (called atlas-log-analyzer up to 0.5.0; the
entries below keep the names of their time). Versions follow
[semantic versioning](https://semver.org/) (before 1.0 a minor version may
change the CLI or stored formats; the notes say when).

Stored sets record the version that built them (`tool_version`); `ragnalog
sets` marks a set `stale` when its signatures are not comparable with today's,
and `ragnalog upgrade --all` rebuilds those from the stored entries.

## [Unreleased]

Masking changes (`mask4`): sets built before are `stale` and not comparable with new ones
until `ragnalog upgrade --all` re-signs them from their stored entries (no log files
needed).

### Added

- Set report: zoom the top chart into the time window (*Zoom in* or `+`; a drag on the
  zoomed chart zooms further, `−` zooms out, `[`/`]` move it) with finer bins, and pick the
  bin size (*Bins*: automatic, or from 1 s to 7 d, finer than the automatic one down to
  2 px a bar). Bins under an hour come from the server, so the web app has them and the
  standalone HTML file stays at an hour or more. Zoom and bin size are in the URL (`z=`,
  `bin=`).
- `ragnalog timeline SET --by-level` and `GET /api/sets/{ref}/timeline?by_level=true`:
  each bucket's entries per level (`levels`), the series of the report's chart.
- Cluster view: zoom and bins on the timeline, as on the set report (bins under the
  payload's bucket from `GET /api/captures/{ref}/timeline`; `urls.timeline`).
- Zoom is a toggle on both charts, on by default: a drag zooms into the selection; off,
  it selects the window. **Undo** next to it steps back through window, zoom and bin
  changes, also after Clear (`u`).
- Grave errors: out of memory, stack overflow, other JVM errors, exhausted resources
  (open files, disk) and FATAL entries are found per set (`report/grave.py`; `grave` in
  `report.v1` and, per node, `cluster-report.v1`) and drawn as red lines, bands and ⚠
  badges on the report's chart and the cluster timeline, with a red legend button and
  a red pill on the group's row.

### Changed

- Pages show times in the browser's time zone by default (for example CET) instead of UTC.
  The zone button or `z` still switches to UTC, and a viewer who chose UTC keeps it. Links,
  windows in the URL and `/api` parameters stay UTC.
- New logo and tab icon: the serpent Jörmungandr around three log lines.

### Fixed

- Masking: a date written by Java's `Date.toString()` (`Tue Nov 24 00:00:00 CET 2026`, also
  with a zone such as `GMT+01:00` or none) is one `<TS>`. Its weekday and month names
  stayed in the template, so a message holding one got a new signature every day and
  `triage` and `diff` reported it as a new group each morning.

## [0.6.1] - 2026-11-21

The repository is public, and the container image now runs natively on arm64 too. No change
to the CLI or the stored formats: sets built by 0.6.0 need no `upgrade`.

### Changed

- The repository is public: install with `git+https://github.com/rdk/ragnalog.git@vX.Y.Z`
  or the release wheel's URL, and pull the image without signing in. CI runs again on
  pushes to `main` and on pull requests.
- Test data re-masked: a JQL project key, workflow names, internal addresses, a user
  name and a custom field id that came from a real log are replaced by invented ones
  throughout the history. The hygiene test now checks every text file for private
  addresses outside 10.0.0.0/16 and log fixtures for JQL project keys.
- Container image: built for linux/arm64 as well as linux/amd64 (no emulation on Apple
  silicon), on Debian 13 (trixie) instead of 12, with OCI labels (source, description,
  licence, version, revision) that the package page shows.
- README, Docker: the port is published on localhost only (`-p 127.0.0.1:8321:8321`), with
  notes on PowerShell, serving other machines, file permissions of mounted logs, upgrading,
  and ingesting with Docker Compose. The README screenshots are framed with a shadow; the
  first one is cropped and has no caption.

## [0.6.0] - 2026-11-19

The project is renamed from atlas-log-analyzer to **Ragnalog**. No stored-format change: sets
built by 0.4.x and 0.5.0 stay comparable and need no `upgrade`; only the names around them
change.

### Changed

- Renamed everywhere, with no aliases for the old names:
  - the repository `rdk/atlas-log-analyzer` is `rdk/ragnalog` (GitHub redirects the old URLs),
    the distribution `atlas-log-analyzer` is `ragnalog` (`pip install 'ragnalog[web]'`) and the
    image is `ghcr.io/rdk/ragnalog`. `ghcr.io/rdk/atlas-log-analyzer` keeps the 0.5.0 image
    and gets no new ones;
  - the command `atlas-log` is `ragnalog`, the import package `atlas_log_analyzer` is `ragnalog`
    and the format entry-point group `atlas_log_analyzer.formats` is `ragnalog.formats`;
  - the environment variables `ATLAS_LOG_*` are `RAGNALOG_*` (`RAGNALOG_STORE`,
    `RAGNALOG_TOKEN`, …): rename them in your shell, service and compose files;
  - the default data directory `./.atlas-log` is `./.ragnalog`; pass `--store` or set
    `RAGNALOG_STORE` to keep using an existing `./.atlas-log`, or rename it;
  - the web app's token header `X-Atlas-Token` is `X-Ragnalog-Token` (`Authorization: Bearer`
    is unchanged) and its cookie `atlas_log_token` is `ragnalog_token`, so browsers sign in
    once more.
- The pages move the theme, skin, width, time zone and panel width saved under the old
  `atlas-log-*` browser storage keys to `ragnalog-*` on first load.
- Aurora is the default skin. The palette button beside the theme toggle offers **Classic**,
  **Aurora** (violet/cyan) and **Mineral** (ivory/sage); the choice is remembered across pages,
  works in light and dark mode and is also available in exported HTML reports.
- A refreshed UI: compact layouts and progressive disclosure on the report, cluster, triage and
  log-set pages. The log-set list is searchable by name, node, cluster or ID, and matching
  captures keep their nodes together.

### Development

- Dependabot is removed again (its update PRs got no CI).
- README screenshots are taken in high DPI with the Aurora skin; click one for full resolution.

## [0.5.0] - 2026-11-12

No stored-format change: sets built by 0.4.x stay comparable.

### Changed

- Docs, help, messages and the web app call the folder that keeps the log sets the "data directory"
  instead of "the store"; `--store`, `ATLAS_LOG_STORE`, `store_error` and the `store` keys are unchanged.
- `timeline` of a capture (and `GET /api/captures/{ref}/timeline`) takes the filters of
  `entries` and `--node`, like a set's; the entries page's chart applies them for captures
  too and no longer says "not filtered by …".

### Development

- CI no longer runs on pushes and pull requests (by hand, and before each release, it still
  does); `pytest` gained browser smoke tests of every page on the demo logs and a check that
  every quoted version matches the package's; `RELEASING.md` describes a release;
  `scripts/screenshots.mjs` refreshes the README images; Dependabot proposes monthly updates.

## [0.4.1] - 2026-11-11

No stored-format change: sets built by 0.4.0 stay comparable.

### Changed

- The report's group panel is laid out like the cluster page's: Mute and Copy in its top
  bar, one line of facts, a short hourly chart above the examples, *Who* as three top-5
  columns.
- "Open raw ↗" opens a page with the entry's exact text highlighted
  (`/sets/{set}/entries/{id}/raw`; the plain text stays at the API route).
- Tooltips on the view tabs, the atlas-log menu, the breadcrumb, the cluster tabs and the
  panel's section links.
- The entries page's group chip shows the template with its placeholders, its header time
  range uses the time style.
- The agent manifest's budget is 16 KB (it is about 10 KB).

### Fixed

- The mouse wheel scrolls the page again over the group panel.
- Uploads finishing at the same moment could push the ingest queue past its 8 jobs; the
  cap is now checked when the job is added (503 with `Retry-After`).

## [0.4.0] - 2026-11-10

Parsing, masking and signatures change (format `jira@4`, signatures `sig2`, masking
`mask3`): sets built before are `stale` and not comparable with new ones until
`atlas-log upgrade --all` rebuilds them from their stored entries (no log files
needed).

### Fixed

- A header whose request URL holds a `[` (`url: /a/[b]/c`) was glued onto the previous
  entry; `upgrade` splits such entries again.
- Exception chains after the JVM's `Exception in thread "main"` prefix, and chains with a
  blank line before `Caused by:`, are parsed whole.
- The same message with and without trailing white space is one group.
- Masking: IPv6 addresses (`<IP>`), JSON Web Tokens (`<TOKEN>`), the user name in home
  paths (`<USER>`), emails with `'` and `user@localhost`; an IPv4 address needs numbers
  up to 255.
- Rotated files with a date stamp (`x.log.20261101`) are read oldest first.
- Store: an overwrite interrupted between its two renames is restored on the next start;
  a long ingest's temporary folder is not swept; a set with an unreadable manifest is
  listed as a warning (`sets`, the web index) and can be removed.
- A damaged Parquet file of a set is `store_error` (exit 3); `timeline` of an unknown
  group exits 2; a time window that runs backwards, `context --window` below 0, an empty
  or multi-statement `query`, and an `ingest --name` with `:`, `/` or `\` are refused.
- Re-ingesting a split upload under another `--capture` no longer fails.
- Web app: server paths are cut from error messages, the access log prints
  `token=***`, request bodies other than uploads are capped at 64 KiB, uploads are
  refused when the disk is nearly full (507) or 8 jobs are waiting (503), finished jobs
  are kept up to 100, the token redirect is relative, long upload names are shortened.
- UI: keyboard focus stays on pagers and header buttons after re-renders, `/` with the
  stuck narrow filter bar, `c` on a merged row, Esc closes a tooltip first, the cluster
  merged log's time header follows the zone, the entries page handles relative times.

### Added

- `timeline` takes the filters of `entries` (`--thread`, `--user`, `--contains`, …) for a
  set, and the entries page's chart applies them.
- `groups.v1` has `next_offset`, like `entries.v1`.
- Entries page: `?` keyboard help and `z` for the time zone; sortable column headers are
  buttons (keyboard, `aria-sort`).

### Changed

- The "in window" count reads `N / total` on one line; the cluster drawer is more compact.
- `diff --level` names the flag you typed in its errors; it means "this level and above".
- A blank `--store ""` is ignored, like a blank `ATLAS_LOG_STORE`.

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

[Unreleased]: https://github.com/rdk/ragnalog/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/rdk/ragnalog/releases/tag/v0.6.1
[0.6.0]: https://github.com/rdk/ragnalog/releases/tag/v0.6.0
[0.5.0]: https://github.com/rdk/ragnalog/releases/tag/v0.5.0
[0.4.1]: https://github.com/rdk/ragnalog/releases/tag/v0.4.1
[0.4.0]: https://github.com/rdk/ragnalog/releases/tag/v0.4.0
[0.3.0]: https://github.com/rdk/ragnalog/releases/tag/v0.3.0
[0.2.1]: https://github.com/rdk/ragnalog/releases/tag/v0.2.1
[0.2.0]: https://github.com/rdk/ragnalog/releases/tag/v0.2.0
