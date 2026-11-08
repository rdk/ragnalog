# Changelog

All notable changes to atlas-log-analyzer. Versions follow
[semantic versioning](https://semver.org/) (before 1.0 a minor version may
change the CLI or stored formats; the notes say when).

Stored sets record the version that built them (`tool_version`); `atlas-log
sets` marks a set `stale` when its signatures are not comparable with today's,
and `atlas-log upgrade --all` rebuilds those from the stored entries.

## [Unreleased]

## [0.2.0] - 2026-11-08

The first tagged release. Install it with
`uv tool install "atlas-log-analyzer[web] @ git+https://github.com/rdk/atlas-log-analyzer@v0.2.0"`
or run the container image (see the README).

### Ingest and store

- Jira Data Center logs (`atlassian-jira.log*`, rotated and gzipped rotations
  `x.log.3.gz`) into log sets: Parquet entries and groups with stable signatures
  (level, logger, masked message, exception chain), variables kept per entry.
- Cluster exports: a support ZIP or folder of several nodes splits into one set
  per node (`--split`), labelled with node, cluster and capture. Files from a ZIP
  are named `export.zip/<member>`. A Jira home's side logs (`-security`,
  `-outgoing-mail`, `-slow-queries`) are skipped, not mixed into the main set.
- Duplicate files are skipped; a cut-short `.gz` is a clear error naming the file.
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

[Unreleased]: https://github.com/rdk/atlas-log-analyzer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rdk/atlas-log-analyzer/releases/tag/v0.2.0
