# atlas-log-analyzer

Parse, group and compare application log files — Jira Data Center first.

`atlas-log` turns `atlassian-jira.log` files (including rotated ones and Java
stack traces) into a **log set**: Parquet tables of entries and of *groups*,
where a group collects all entries with the same level, logger, masked message
and exception chain. Variable parts (issue keys, numbers, ids, URLs …) are
masked out of the group template and kept per entry as `variables`. Group
signatures are stable hashes, so two log sets — before and after an upgrade,
two nodes, two days — can be compared without the raw logs.

Built for humans at a terminal and for coding/ops agents: every command
prints JSON when piped, with documented schemas and exit codes.

## Install

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                     # development environment (.venv)
uv run atlas-log --help

uv tool install .           # or: install the `atlas-log` command globally
uv tool install '.[mine]'   # with the optional Drain3 template mining
```

## Quick start (humans)

```sh
atlas-log ingest /var/atlassian/jira/log/ --name prod-1029     # build a log set
atlas-log groups prod-1029 --min-level WARN                    # one row per signature
atlas-log show prod-1029 83d1828c6afb2ead                      # entries of a group, with variables
atlas-log show prod-1029 83d1828c6afb2ead --raw --limit 1      # the original text
atlas-log ingest ./logs-after-upgrade/ --name prod-1030
atlas-log diff prod-1029 prod-1030 --level ERROR               # new / vanished / changed groups
```

Log sets live in `./.atlas-log` (override with `--store DIR` or
`ATLAS_LOG_STORE`). A set can be referred to by id, name or unique id prefix.

### HTML report

```sh
atlas-log report prod-1030 --compare prod-1029 --out prod-1030.html --open
```

writes one self-contained HTML file (no network access needed; open it from a
file share or send it by e-mail): timeline by level, filterable group table,
per-group drawer with example entries, stack traces and the matching CLI
commands, and — with `--compare` — new / vanished / changed groups against a
baseline. The view (filters, selected group) is kept in the URL, so a link
with its `#…` part reopens the same view. See `docs/report.md`.

## Quick start (agents)

- Output is JSON whenever stdout is not a terminal; force it with
  `--output json` (one document) or `--output jsonl` (one row per line).
- Every document has `"schema": "<name>.v1"`; listings put rows in `"items"`.
- Errors: `{"schema": "error.v1", "error": {"code": ..., "message": ...}}` on
  stdout; exit codes `0` ok, `1` usage, `2` nothing found / parse failure,
  `3` store error. Diagnostics go to stderr (`--quiet` silences them).
- `atlas-log schema` describes the stored tables (`entries`, `groups`,
  `merges`, `logset`) field by field; `atlas-log COMMAND --help` documents the
  JSON shape and exit codes of each command.

```sh
atlas-log --output json ingest atlassian-jira.log --name today
atlas-log groups today --min-level ERROR --limit 20 | jq '.items[] | {signature, count, template}'
atlas-log query today "select level_name, count(*) n from entries group by 1 order by 2 desc"
atlas-log query today "select unnest(variables) v, count(*) n from entries
                       where signature = '83d1828c6afb2ead' group by 1 order by 2 desc"
atlas-log query today "select hour(ts) h, count(*) from entries where level >= 50 group by 1 order by 1"
atlas-log parse atlassian-jira.log --limit 100      # JSONL entries, no store
```

Store layout:

```
.atlas-log/<logset-id>/
├── manifest.json      logset.v1 (sources, time range, counts, versions)
├── entries.parquet    entries.v1, one row per entry
├── groups.parquet     groups.v1, one row per signature
└── merges.parquet     merges.v1, Drain3 merge proposals (optional)
```

The Parquet files can be read directly with DuckDB, polars or pandas.

## Documentation

- [`docs/cli.md`](docs/cli.md) — command reference: options, JSON output, exit codes
- [`docs/design.md`](docs/design.md) — architecture, models, storage format
- [`docs/requirements.md`](docs/requirements.md), [`docs/research.md`](docs/research.md)
- [`AGENTS.md`](AGENTS.md) — guide for coding agents working on this repository
