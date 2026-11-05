# AGENTS.md — working on atlas-log-analyzer

Python 3.14 project managed with uv; CLI `atlas-log`. Read `docs/design.md`
before changing behaviour; `docs/cli.md` documents the command contract.

## Layout

```
src/atlas_log_analyzer/
  config.py  Settings + ENV_VARS (stdlib only): flags > ATLAS_LOG_* > defaults; core reads no env
  core/      pure library: models, formats (registry, jira), reader, parser,
             java stack traces, masking, signatures, grouping, pipeline (Recipe:
             format + spec + rules -> masker/signer/grouper and the recipe key),
             mining (drain3, optional)
  store/     Parquet + manifest persistence (polars/pyarrow; manifest.py needs neither),
             typed filters (filters.py), DuckDB queries, diff, regroup (column-wise
             re-signing), triage (triage.v1 and rank_baselines, shared by the CLI and the
             web app)
  report/    HTML report: payload.py (report.v1), render.py, assets/ (template, css, js)
  service/   builds every JSON document for both front ends (no cyclopts/fastapi/rich):
             queries.py, compare.py, ingest.py, meta.py (the builders), params.py (request
             models: GroupQuery, EntryQuery, Page, Select, TextCap), errors.py (OpError,
             SPEC, REASONS), sets.py (Sets protocol), views.py (presets, capping, frames),
             documents.py (one msgspec Struct per document, their JSON Schemas)
  cli/       app.py (root app, global options, run/main, error boundary, lazy command
             registration), commands/{ingest,browse,compare,admin}.py (the commands),
             context.py (output, settings, store of one invocation), params.py (option
             defaults), render.py (table renderers), output.py (json/jsonl/table, error
             envelopes), manifest.py (manifest.v1, the agent manifest)
  webapp/    FastAPI app (`atlas-log serve`): api.py (/api JSON), pages.py + templates/
             (HTML), jobs.py (background ingest), state.py (store lock, caches),
             openapi.py (document schemas in /openapi.json, GET /api/manifest)
tests/
  core/  store/  service/  cli/  report/  webapp/   one directory per package
  test_config.py             settings and the docs' environment table
  contract/test_parity.py    CLI <-> API parity contract (docs/design.md, "CLI and API parity")
  contract/test_schemas.py   every document matches its Struct and JSON Schema
  golden/signatures.json     golden signatures of the fixtures (`pytest --update-golden`)
  fixtures/jira/*.log        masked Jira log excerpts shared by all tests
  fixtures/foreign/*.log     non-Jira text (access, GC logs) no format may claim
  cli/data/mini.log          10-entry Jira log used by the CLI end-to-end tests
docs/        requirements, research, design, cli reference,
             formats/*.v1.md and *.v1.schema.json (generated)
```

## Dependency direction

`core` ← `store` ← `report` ← `service` ← `cli` / `webapp` (`config` is stdlib
and used by `cli`/`webapp`). `core` uses only the standard library and `xxhash`
and reads no environment; it must never import `store`, `service`, `cli` or
`webapp`. `store` must not import `service`, `cli` or `webapp` (the old
`store/views.py` is only a re-export shim). `service` must not import `cli`,
`webapp`, cyclopts, FastAPI or rich. **`cli` and `webapp` call `service` and
render: they don't build documents or call store writers.** A new document or
key is built in `service/` once, for both front ends; front-end differences are
explicit arguments (`front`, `TextCap.how`, the `report_url` the API adds after
building). Optional dependencies (`drain3`) are imported lazily and raise a
clear `ImportError`.

## Commands

```sh
uv sync --all-extras --dev           # environment
uv run ruff check .                  # lint (rules incl. D docstrings, ANN annotations)
uv run ruff format .                 # format (CI runs `ruff format --check .`)
uv run mypy                          # strict type check of the package
uv run pytest                        # all tests; `uv run pytest tests/cli -q` for one package
uv run atlas-log --help              # the CLI from the working tree
uv run python docs/formats/generate.py   # regenerate docs/formats (md + JSON Schema) and docs/cli.md lists
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy and pytest on
ubuntu-latest and windows-latest.

## Conventions

- Full type hints, `mypy --strict` clean; Google-style docstrings; line length 100;
  sparse, factual comments. LF line endings (`.gitattributes`).
- Core models are frozen, slotted dataclasses; keep core functions streaming
  (iterators), not list-building.
- Stored schemas live only in `store/schema.py`; bump `SCHEMA_VERSION` on a
  breaking change and regenerate `docs/formats/`. Changing masking rules or the
  signature algorithm requires bumping `MASK_RULES_VERSION` / `SIGNATURE_VERSION`
  (a format's own rules: its `version`); all are part of `Recipe.key`, which the
  manifest stores as `recipe_key` and which decides comparability (`diff`, triage
  baselines, `stale` in `sets`, `atlas-log upgrade`). Build maskers, signers and
  groupers through `core.pipeline.Recipe`, not by hand.
- CLI commands are thin adapters over `service`: a new command goes in
  `cli/commands/*.py` and is registered by import path in `cli/app.py` `COMMANDS`
  (keep the module free of heavy imports at the top). Errors are `OpError` built
  for one front end (`front="cli"`/`"api"`); never rename an error code, add a
  `reason` (`service/errors.py` `REASONS`) when the front ends' codes differ, and
  keep the tables in `docs/cli.md`/`docs/webapp.md` equal to `SPEC`/`REASONS`
  (`tests/service/test_errors.py`). Settings and `ATLAS_LOG_*` variables are
  `config.Settings`/`ENV_VARS` only. Every JSON document has a
  `"schema": "<name>.v1"` key; listings use `"items"`. Documents are typed only in
  `service/documents.py`: a new document or key gets its Struct field there
  (optional when only some commands or modes add it), then regenerate
  `docs/formats/`; a new `/api` route gets its document in `webapp/openapi.py`.
  `atlas-log manifest` is generated from the commands; only its workflow/`when`
  table in `cli/manifest.py` is written by hand, and it must stay under 8 KB. Exit codes: 0 ok,
  1 usage, 2 nothing found / parse failure, 3 store error. Results go to stdout,
  diagnostics to stderr. A command's docstring is its `--help`: keep the output
  shape and exit codes in it accurate, and update `docs/cli.md` with it.
- Startup time: `cli/`, `store/__init__.py` and `service/` import polars,
  DuckDB, rich, the ingest pipeline and the Parquet writer inside the functions
  that use them (annotations via `TYPE_CHECKING`); `tests/cli/test_startup.py`
  checks it. Keep `_version.py` equal to `project.version`.
- Changing signatures on purpose: bump the version, then `uv run pytest --update-golden`.
- Don't call `DataFrame.to_dicts()` on timezone-aware columns in code that runs
  on Windows without `tzdata`; use `store.logset.rows`/`to_records` or
  `cli.output.jsonable_rows`.
- Rows go through one serialiser, `store.logset.to_records` (also behind
  `cli.output.jsonable_rows`): ISO 8601 UTC with milliseconds, naive = UTC.
- Tests: pytest, no network. CLI tests run the app in-process via
  `atlas_log_analyzer.cli.app.run(argv)` (fixtures in `tests/cli/conftest.py`).
  Web app tests use FastAPI's `TestClient` over a temp store (`tests/webapp/conftest.py`).

## Data hygiene

**Never commit real logs.** Fixtures must be small, hand-checked excerpts with
user names, issue keys, hosts, IPs and URLs masked or invented. Stores
(`.atlas-log/`) and any customer data stay out of the repository: the CLI
refuses to create the implicit `./.atlas-log` inside a git work tree, so pass
`--store DIR` (a temp dir) or set `ATLAS_LOG_STORE` when trying commands.
