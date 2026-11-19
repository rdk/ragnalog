# Requirements

Status: agreed 2026-10-29. Decisions recorded at the bottom.

## Context

`ragnalog` analyses application log files, starting with
`atlassian-jira.log` produced by Jira Data Center. The immediate consumers are
humans at a terminal and, more importantly, coding/ops agents driving the CLI.
A FastAPI web application for browsing log sets and comparing them with earlier
ones will be added to this repository later; it is deliberately *not* designed
yet, but the layout must leave an obvious slot for it.

Sample data lives outside the repository in `../ragnalog-data/`
(rotated `atlassian-jira.log*` files, ~20 MB and ~250k lines each, up to 11
files per set; layout and conventions in that folder's `README.md`).

## R1 — Core library (`ragnalog.core`)

- **R1.1 Streaming parse.** Parse files of hundreds of MB and rotated file sets
  with bounded memory. Multi-line entries (Java stack traces with `Caused by:`
  chains, Groovy compiler output, free-text continuations) are assembled into
  one entry. Garbage or truncated lines at file boundaries must not abort the
  parse; they are kept as "unparsed" entries with a reason.
- **R1.2 Pluggable formats.** A `LogFormat` abstraction with one implementation
  for Jira DC. Confluence/Bitbucket/generic log4j/logback can be added without
  touching the core. Formats are discovered through a registry.
- **R1.3 Entry model.** A `LogEntry` with typed fields: timestamp (tz-aware),
  level, thread, logger, user, request id, session, client ip, request url,
  message, exception chain (class, message, frames) and provenance
  (file, byte offset, line number). Unknown header shapes degrade gracefully.
- **R1.4 Grouping / fingerprinting.** Every entry gets a stable *signature* so
  that entries that differ only in variable parts (issue keys, users, ids,
  numbers, timestamps, object hashes, IPs…) share one group. A group carries a
  human-readable *template* (`<*>` placeholders) and statistics (count,
  first/last seen, distinct users/threads/loggers, examples). Signatures are
  deterministic across runs, files and machines so that sets can be compared.
  The exception chain participates in the signature (a dispatcher error with
  different root causes must not collapse into one group).
- **R1.5 Composable.** Iterators and pure functions; no I/O hidden inside the
  models; no CLI or web framework dependency in the core.

## R2 — Command line (`ragnalog`)

- **R2.1 Commands.** `ingest` (files/dirs → stored log set), `groups`
  (templates + stats), `show` (entries of one group), `entries`/`query`
  (filtering, SQL for agents), `stats`, `diff` (which signatures are new /
  gone / changed in count between two log sets), `parse` (one-shot streaming
  to JSONL without a store), `formats`, `schema`.
- **R2.2 Agent-first output.** Every command supports `--output json|jsonl`
  with a stable, documented schema; exit codes are meaningful; no decoration
  on stdout in machine mode; logs/diagnostics go to stderr. Human mode renders
  tables. `--help` is complete enough for an agent to use the tool blind.

## R3 — Intermediate & storage formats

- **R3.1 Versioned schemas** for entries, groups and log-set manifests,
  independent of the source log format.
- **R3.2 Log set convention.** A directory of (rotated) files is one *log set*
  with an identity and a manifest (sources, hashes, time range, tool and schema
  versions). Entries overlapping between sets (snapshots of the same instance)
  are deduplicated by a deterministic entry id.
- **R3.3 Parse once.** Ingestion writes a columnar store; analysis reads the
  store, never the raw logs again.

## R4 — Project

uv-managed, Python 3.14, `src/` layout, private GitHub repository, tests with
masked fixtures, ruff + type checking, CI. A future `webapp/` package sits next
to `cli/` and reuses `core/` and `store/`.

## R5 — Research

Survey template-mining libraries (Drain3, LogPAI logparser), parsing
strategies, storage (Parquet/DuckDB/SQLite/JSONL) and CLI frameworks, and
recommend. See `research.md`.

## Decisions (2026-10-29)

| Topic | Decision |
|---|---|
| Grouping | Layered: deterministic masking + hash signature as primary key; Drain3-style mining as an optional second pass that *proposes* merges. |
| Storage | Parquet for entries and groups, JSON manifest, DuckDB/polars for queries and diffs. |
| Agent interface | JSON-output CLI now; command/output schemas shaped so an MCP server is a thin wrapper later. |
| Test fixtures | Small excerpts of real logs with usernames/IPs/hostnames masked. |
| Names | repo `rdk/ragnalog`, package `ragnalog`, CLI `ragnalog`. |
