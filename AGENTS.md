# AGENTS.md — working on atlas-log-analyzer

Python 3.14 project managed with uv; CLI `atlas-log`. Read `docs/design.md`
before changing behaviour; `docs/cli.md` documents the command contract.

## Layout

```
src/atlas_log_analyzer/
  core/      pure library: models, formats (registry, jira), reader, parser,
             java stack traces, masking, signatures, grouping, mining (drain3, optional)
  store/     Parquet + manifest persistence (polars/pyarrow), DuckDB queries, diff,
             triage (triage.v1, shared by the CLI and the web app)
  report/    HTML report: payload.py (report.v1), render.py, assets/ (template, css, js)
  service/   documents.py: one msgspec Struct per JSON document, their JSON Schemas
  cli/       app.py (cyclopts commands), output.py (json/jsonl/table, errors, exit codes),
             manifest.py (manifest.v1, the agent manifest)
  webapp/    FastAPI app (`atlas-log serve`): api.py (/api JSON), pages.py + templates/
             (HTML), jobs.py (background ingest), state.py (store lock, caches),
             openapi.py (document schemas in /openapi.json, GET /api/manifest)
tests/
  core/  store/  cli/  report/  webapp/   one directory per package
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

`core` ← `store` ← `cli` / `webapp`. `core` uses only the standard library and
`xxhash`; it must never import `store`, `cli` or `webapp`. `store` must not
import `cli` or `webapp`. Optional dependencies (`drain3`) are imported lazily
and raise a clear `ImportError`.

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
  signature algorithm requires bumping `MASK_RULES_VERSION` / `SIGNATURE_VERSION`.
- CLI commands are thin wrappers over `core`/`store`. Every JSON document has a
  `"schema": "<name>.v1"` key; listings use `"items"`. Documents are typed only in
  `service/documents.py`: a new document or key gets its Struct field there
  (optional when only some commands or modes add it), then regenerate
  `docs/formats/`; a new `/api` route gets its document in `webapp/openapi.py`.
  `atlas-log manifest` is generated from the commands; only its workflow/`when`
  table in `cli/manifest.py` is written by hand, and it must stay under 8 KB. Exit codes: 0 ok,
  1 usage, 2 nothing found / parse failure, 3 store error. Results go to stdout,
  diagnostics to stderr. A command's docstring is its `--help`: keep the output
  shape and exit codes in it accurate, and update `docs/cli.md` with it.
- Startup time: `cli/`, `store/__init__.py` and `store/views.py` import polars,
  DuckDB, rich, the ingest pipeline and the Parquet writer inside the functions
  that use them (annotations via `TYPE_CHECKING`); `tests/cli/test_startup.py`
  checks it. Keep `_version.py` equal to `project.version`.
- Changing signatures on purpose: bump the version, then `uv run pytest --update-golden`.
- Don't call `DataFrame.to_dicts()` on timezone-aware columns in code that runs
  on Windows without `tzdata`; use `store.logset.rows`/`to_records` or
  `cli.output.jsonable_rows`.
- Tests: pytest, no network. CLI tests run the app in-process via
  `atlas_log_analyzer.cli.app.run(argv)` (fixtures in `tests/cli/conftest.py`).
  Web app tests use FastAPI's `TestClient` over a temp store (`tests/webapp/conftest.py`).

## Data hygiene

**Never commit real logs.** Fixtures must be small, hand-checked excerpts with
user names, issue keys, hosts, IPs and URLs masked or invented. Stores
(`.atlas-log/`) and any customer data stay out of the repository: the CLI
refuses to create the implicit `./.atlas-log` inside a git work tree, so pass
`--store DIR` (a temp dir) or set `ATLAS_LOG_STORE` when trying commands.
