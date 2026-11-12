<div align="center">

<img src="docs/img/logo.svg" width="72" height="72" alt="atlas-log logo">

# atlas-log-analyzer

**Turn gigabytes of Jira Data Center logs into a few hundred groups you can read,<br>
compare and triage, in the browser, the terminal or from an agent.**

[![Release](https://img.shields.io/badge/release-v0.5.0-2f6bc7?style=flat-square)](https://github.com/rdk/atlas-log-analyzer/releases/latest)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/built_with-uv-de5fe9?style=flat-square&logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![Docker image](https://img.shields.io/badge/image-ghcr.io-2496ed?style=flat-square&logo=docker&logoColor=white)](https://github.com/rdk/atlas-log-analyzer/pkgs/container/atlas-log-analyzer)
[![Jira Data Center](https://img.shields.io/badge/logs-Jira_Data_Center-0052cc?style=flat-square&logo=jira&logoColor=white)](docs/formats/)
[![Ruff](https://img.shields.io/badge/lint-ruff-d7ff64?style=flat-square&logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![mypy strict](https://img.shields.io/badge/types-mypy_strict-1f5082?style=flat-square)](https://mypy-lang.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-3da639?style=flat-square)](LICENSE)

[Quick start](#quick-start) · [Features](#features) · [Web app](#web-app) · [CLI](#cli) · [For agents](#for-agents) · [How it works](#how-it-works) · [Docs](#documentation)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/hero-dark.png">
  <img src="docs/img/hero-light.png" alt="The report of one node: entries per hour by level with an error spike, level filters and the table of groups" width="100%">
</picture>

<sub>All screenshots show synthetic demo logs (<code>scripts/demo_logs.py</code>), never real data.</sub>

</div>

## Features

- **Groups instead of lines.** Every entry gets a stable signature (level, logger, masked
  message, exception chain), so millions of lines become a few hundred groups with counts,
  time span, users and threads.
- **Triage in one look.** New kinds of errors, groups that grew and groups that vanished
  against the previous log set, with a verdict per set and per cluster node.
- **Cluster view.** The nodes of a cluster side by side on one time axis, with events, the
  moments several nodes fail together, groups by node and the merged log.
- **Variables kept.** Issue keys, numbers, ids and URLs are masked out of the template but
  stored per entry, so you can still ask *which* issues or users were hit.
- **Stack traces made readable.** Frames are tokenised and coloured, library frames dimmed
  and your application's own frames marked.
- **Every entry of a group.** A filterable, bookmarkable table of all entries of one group,
  with a time histogram to cut a window.
- **Works offline.** `report` writes one self-contained HTML file you can mail or keep.
- **JSON-first CLI.** Every command prints a documented JSON document when piped, with
  stable exit codes and a manifest written for agents.
- **SQL when you need it.** `query` runs DuckDB over the stored Parquet entries and groups.

<table>
  <tr>
    <td width="50%"><img src="docs/img/drawer.png" alt="Group panel with the hourly chart and an example entry"><br><sub><b>Group panel:</b> facts, hourly chart, example entries with stack traces.</sub></td>
    <td width="50%"><img src="docs/img/cluster.png" alt="Cluster view with two nodes on one time axis"><br><sub><b>Cluster view:</b> two nodes on one time axis, events and groups by node.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/img/triage.png" alt="Triage with new types, grown and vanished groups"><br><sub><b>Triage:</b> what is new, what grew and what vanished since the previous set.</sub></td>
    <td width="50%"><img src="docs/img/entries.png" alt="All entries of one group with a time histogram"><br><sub><b>Entries:</b> every entry of a group, histogram and the full entry inline.</sub></td>
  </tr>
</table>

## Quick start

Pick one: the **Docker image** runs the web app with nothing else installed; **without
Docker** you get the `atlas-log` command (CLI and web app) on your machine.

### With Docker

The image is published to the GitHub Container Registry with each release. The
repository is private, so sign in once with a token that has `read:packages`:

```sh
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

Run the web app on port 8321 with its data directory (where the log sets are kept) in a
named volume. It listens on all
interfaces in the container, so give it a token:

```sh
export ATLAS_LOG_TOKEN="$(openssl rand -hex 16)"
docker run -d --name atlas-log -p 8321:8321 -v atlas-log:/data \
  -e ATLAS_LOG_TOKEN ghcr.io/rdk/atlas-log-analyzer:0.5.0
echo "http://localhost:8321/?token=$ATLAS_LOG_TOKEN"     # open once; the token is kept
```

Upload log files on the start page, or ingest a folder with the CLI from the same image
(any `atlas-log` command works this way):

```sh
docker run --rm -v atlas-log:/data -v "$PWD/logs:/in:ro" \
  ghcr.io/rdk/atlas-log-analyzer:0.5.0 ingest /in --name prod-1030
```

<details>
<summary>Docker Compose</summary>

```yaml
services:
  atlas-log:
    image: ghcr.io/rdk/atlas-log-analyzer:0.5.0
    ports: ["8321:8321"]
    environment:
      ATLAS_LOG_TOKEN: ${ATLAS_LOG_TOKEN:?set a token}
    volumes: ["atlas-log:/data"]
    restart: unless-stopped
volumes:
  atlas-log:
```

</details>

To build the image yourself: `docker build -t atlas-log .`

### Without Docker

You need [uv](https://docs.astral.sh/uv/getting-started/installation/); it fetches
Python 3.14 itself. Install a release straight from GitHub (your git access to the
repository is used; `[web]` adds the web app, `[mine]` the optional Drain3 template
mining):

```sh
uv tool install "atlas-log-analyzer[web] @ git+ssh://git@github.com/rdk/atlas-log-analyzer.git@v0.5.0"
atlas-log --version
```

or install the wheel attached to the release:

```sh
gh release download v0.5.0 -R rdk/atlas-log-analyzer -p '*.whl'
uv tool install "atlas-log-analyzer[web] @ ./atlas_log_analyzer-0.5.0-py3-none-any.whl"
```

Then choose a data directory, the folder that keeps the log sets (outside any git
repository; the option and variable that name it are `--store` and `ATLAS_LOG_STORE`),
ingest some logs and open the web app:

```sh
export ATLAS_LOG_STORE=~/atlas-log-data
atlas-log ingest /var/atlassian/application-data/jira/log/ --name prod-1030
atlas-log serve                                  # http://127.0.0.1:8321/
```

### From source

```sh
git clone git@github.com:rdk/atlas-log-analyzer.git && cd atlas-log-analyzer
uv sync --all-extras --dev                       # .venv with every extra and the dev tools
uv run atlas-log --help
```

### Try it with demo logs

No logs at hand? Generate the synthetic two-node cluster the screenshots use:

```sh
uv run python scripts/demo_logs.py /tmp/demo     # two weeks, two nodes, invented data
uv run atlas-log --store /tmp/demo-data ingest /tmp/demo/prev --cluster demo-jira --name prev
uv run atlas-log --store /tmp/demo-data ingest /tmp/demo/week --cluster demo-jira --name week
uv run atlas-log --store /tmp/demo-data serve
```

## Web app

`atlas-log serve` (or the container) serves:

| Page | What it shows |
|---|---|
| **Log sets** `/` | every set with its error counts, activity and a verdict against the previous set; upload with progress |
| **Report** `/sets/SET` | entries per hour by level, filterable groups, the group panel with examples, stack traces and related groups |
| **Triage** `/sets/SET/triage` | new types (ERROR first), grown and vanished groups against a baseline it picks or you choose |
| **Entries** `/sets/SET/entries?signature=…` | all entries of a group or filter, with a time histogram, opened from the group panel |
| **Cluster view** `/captures/CAPTURE` | the nodes side by side, cross-node moments, interactions and the merged log |

Everything is also JSON under `/api` (OpenAPI at `/docs`), the same documents the CLI prints.
Light and dark themes, UTC or local time, keyboard shortcuts (`?`), and the view lives in
the URL, so a link reopens the same filters. See [`docs/webapp.md`](docs/webapp.md).

## CLI

```sh
atlas-log ingest logs/ --name prod-1030                        # build a log set
atlas-log triage prod-1030                                     # new kinds of errors vs the previous set?
atlas-log groups prod-1030 --min-level WARN --brief            # one row per group
atlas-log show prod-1030 83d1828c --brief                      # entries of a group (signature prefix)
atlas-log frames prod-1030 83d1828c                            # its stack trace, application frames first
atlas-log context prod-1030 ENTRY_ID --before 10               # what the thread did around one entry
atlas-log diff prod-1029 prod-1030 --rate --limit 20           # new / vanished / changed vs an earlier set
atlas-log report prod-1030 --compare prod-1029 --out r.html    # one self-contained HTML file
atlas-log ingest jira-logs_export.zip --cluster jira-prod      # cluster export: one set per node
atlas-log capture cluster:jira-prod                            # the nodes side by side, a verdict each
atlas-log query prod-1030 "select level_name, count(*) from entries group by 1"
```

A set is referred to by name, id or unique id prefix. Sets are kept in a data directory:
`--store DIR`, else `$ATLAS_LOG_STORE`, else `./.atlas-log` (never created inside a git
work tree, so real logs cannot end up in a repository). Full reference:
[`docs/cli.md`](docs/cli.md).

## For agents

Every command prints JSON when stdout is not a terminal (`"schema": "<name>.v1"`, rows in
`"items"`), with stable exit codes (`0` ok, `1` usage, `2` nothing found, `3` data directory
error) and an `error.v1` document with a `hint` on failure. Start with the manifest, about
10 KB of JSON describing the workflow and every command:

```sh
atlas-log manifest                                   # how to use the tool (manifest.v1)
atlas-log triage today                               # verdict vs the previous set
atlas-log groups today --min-level ERROR --brief     # check "total" / "truncated"
printf '%s\n' '["groups","today","--brief"]' '["show","today","83d1"]' | atlas-log batch
```

`batch` runs many commands in one process (one JSON document per line). The workflow,
conventions and a cookbook of common questions are in [`docs/agents.md`](docs/agents.md).

## How it works

`atlas-log` reads `atlassian-jira.log` files (rotated and gzipped ones too, ZIP exports,
Java stack traces) into a **log set**. Each entry is masked (issue keys, numbers, ids, URLs,
users, IPs become placeholders such as `<ISSUE>`) and gets a signature from its level,
logger, masked message and exception chain; entries with the same signature form a
**group**. Signatures are stable across sets, so two sets (before and after an upgrade, two
nodes, two days) compare without the raw logs.

```
<data directory>/<logset-id>/
├── manifest.json      logset.v1: sources, time range, counts, versions
├── entries.parquet    one row per entry (with its variables)
├── groups.parquet     one row per signature
└── merges.parquet     Drain3 merge proposals (optional)
```

The Parquet files read directly in DuckDB, polars or pandas. Design notes:
[`docs/design.md`](docs/design.md).

## Documentation

| | |
|---|---|
| [`docs/cli.md`](docs/cli.md) | command reference: options, JSON output, exit codes, environment variables |
| [`docs/webapp.md`](docs/webapp.md) | web app pages and the HTTP API |
| [`docs/report.md`](docs/report.md) | the report page, the HTML file and its `report.v1` payload |
| [`docs/cluster.md`](docs/cluster.md) | captures and the cluster view |
| [`docs/agents.md`](docs/agents.md) | agent workflow, conventions and cookbook |
| [`docs/design.md`](docs/design.md) | architecture, models, storage format |
| [`docs/formats/`](docs/formats/) | every document and stored table, with JSON Schemas |
| [`CHANGELOG.md`](CHANGELOG.md) | release notes |

## Development

```sh
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

`pytest` includes browser smoke tests of every page on the demo logs (`tests/browser`; they
skip without Node 22+ and Edge or Chrome). CI does not run on pushes; the release workflow
runs the same checks on Ubuntu and Windows before it publishes a tag (see
[`RELEASING.md`](RELEASING.md)), and `scripts/screenshots.mjs` refreshes the README images
from the demo logs. [`AGENTS.md`](AGENTS.md) is the guide for coding agents (and humans)
working on the code: layout, conventions and data hygiene. Fixtures are small, hand-masked
excerpts; real logs never enter the repository.

## License

[Apache License 2.0](LICENSE) © 2026 Radoslav Krivák
