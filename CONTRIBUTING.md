# Contributing to ragnalog

Issues and pull requests are welcome. [`AGENTS.md`](AGENTS.md) is the guide for coding agents
and humans working on the code: layout, conventions and data hygiene.

## Never share real logs

Logs carry user names, issue keys, host names, addresses and URLs, and ragnalog keeps the
raw text in its data directory and in HTML reports. Do not attach logs, data directories,
reports or screenshots of real data to an issue or pull request.

To report a line or entry that is parsed or grouped wrongly, cut a sanitised excerpt from a
source checkout:

```sh
uv run python scripts/make_fixture.py excerpt.log --window path/to/atlassian-jira.log 120345 20 \
  --host-domain your-company.example
```

It keeps the entry at or before line 120345 and at least 20 lines, and replaces user names,
IP and e-mail addresses, issue-key projects and hosts under `--host-domain` (the script's
docstring lists every rule). Read the output before you share it: it cannot know your
in-house plugin names, packages or paths. Add `ragnalog --version` and what you expected.

## Set up

You need [uv](https://docs.astral.sh/uv/getting-started/installation/); it fetches
Python 3.14 itself.

```sh
git clone https://github.com/rdk/ragnalog.git && cd ragnalog
uv sync --all-extras --dev
```

## Checks

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

`pytest` includes browser smoke tests of every page on the demo logs (`tests/browser`; they
skip without Node 22+ and Edge or Chrome). CI (`.github/workflows/ci.yml`) runs the same
checks on Ubuntu and Windows on pushes to `main` and on pull requests, and builds the
container image.

Tests never need real logs: fixtures are small, hand-masked excerpts, and
`tests/test_fixture_hygiene.py` checks every text file for private addresses.

## UI changes

Refresh the README screenshots from the synthetic demo logs with `scripts/screenshots.mjs`
(its header has the commands), never from real logs.

## Releases

Maintainers release by tag; [`RELEASING.md`](RELEASING.md) has the steps.
