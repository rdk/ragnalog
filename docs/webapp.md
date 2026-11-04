# Web app

`atlas-log serve` runs a FastAPI app over one store. It answers the main
question, "compared with the previous snapshot, are there new kinds of
errors?", on one page, opens any set in the interactive report, and ingests
uploaded log files in the background. Every page has a JSON twin under `/api`
with the same documents as `atlas-log --output json`, so a human and an agent
can work from the same data.

```sh
uv sync --extra web                      # or: uv tool install 'atlas-log-analyzer[web]'
atlas-log --store ~/atlas-log-store serve   # http://127.0.0.1:8321/
atlas-log serve --host 0.0.0.0 --token "$(openssl rand -hex 16)"   # on a network
```

| Flag | Env | Default |
|---|---|---|
| `--store DIR` (global) | `ATLAS_LOG_STORE` | `./.atlas-log` (not created inside a git work tree) |
| `--host` | | `127.0.0.1` |
| `--port` | | `8321` |
| `--token` | `ATLAS_LOG_TOKEN` | none: no auth |
| `--max-upload-mb` | `ATLAS_LOG_MAX_UPLOAD_MB` | `2048` |
| | `ATLAS_LOG_REPORT_CACHE` | `8` report payloads kept in memory |
| `--reload` | | off (development) |

Flags win over the environment. With `--reload` uvicorn runs the app in a child
process that sees only the environment, so `serve` exports every setting
(`ATLAS_LOG_STORE`, `ATLAS_LOG_TOKEN`, `ATLAS_LOG_MAX_UPLOAD_MB`,
`ATLAS_LOG_REPORT_CACHE`) before it starts: a reloading server keeps its token.

## Pages

| Path | Page |
|---|---|
| `/` | Sets, newest first: time range, entries, groups, error and warning counts, a triage verdict against the previous set, and an upload form with live job progress. |
| `/sets/{ref}` | The interactive report (`docs/report.md`). `?compare=auto` or `?compare=SET` adds the comparison; `min_level`, `examples` pass through. A thin navigation bar sits above it with a set switcher that keeps the URL hash, so filters survive switching sets. |
| `/sets/{ref}/triage` | Verdict, a coverage warning when the spans differ by more than 4x, then new types (most severe first), new exceptions below the level filter, groups whose hourly rate grew at least ×2, vanished groups. Each row links into the report with that group open. |
| `/docs` | OpenAPI (Swagger UI) for the JSON API. |

`{ref}` is a set id, a unique id prefix or a name, as in the CLI. Unknown sets
and paths render a 404 page; under `/api` they return `error.v1`.

The report page is the same `template.html`, `report.js` and `report.css` as the
static file. The page is rendered without an embedded payload and
`data-report-url` points at `/api/sets/{id}/report`; the assets are served from
`/assets/report/`. Example entries missing from the payload are fetched per group
from `/api/sets/{id}/groups/{sig}/examples`.

## JSON API

All responses carry `"schema": "<name>.v1"`; listings put rows under `"items"`.
Errors are `{"schema": "error.v1", "error": {"code", "message", "hint"?}}` with
the CLI's codes (an ambiguous signature prefix adds `"candidates"`). Status codes: 400 `usage`/`invalid_value`/`invalid_level`,
401 `unauthorized`, 404 `not_found`, 409 `ambiguous`/`incompatible_sets`,
413 `payload_too_large`, 500 `store_error`.

| Method and path | Parameters | Document |
|---|---|---|
| `GET /api/health` | | `health.v1` |
| `GET /api/version` | | `version.v1` |
| `GET /api/formats` | | `formats.v1` |
| `GET /api/sets` | `brief` (as `sets --brief`: `id, name, first_ts, last_ts, entry_count, group_count, summary`) | `sets.v1`; items are manifests plus `summary {errors, warnings, error_groups}` |
| `GET /api/sets/{ref}` | | `logset.v1` plus `summary` (as `atlas-log set SET`) |
| `DELETE /api/sets/{ref}` | | `remove.v1` |
| `GET /api/sets/{ref}/stats` | | `stats.v1` |
| `GET /api/sets/{ref}/groups` | `level`, `min_level`, `logger`, `since`, `until`, `with_exception`, `exception`, `contains`, `sort`, `limit` (50; 0 = all), `offset`, `coarse`, `columns` (comma-separated and/or repeat), `brief` | `groups.v1` with `total`, `offset`, `truncated` |
| `GET /api/sets/{ref}/groups/{sig}` | `limit` (20), `coarse`, `max_raw` (4000; 0 = all), `columns`, `brief` (as `show --columns/--brief`; `brief` also cuts `group`) | `group.v1`: `group`, hourly `histogram`, first entries in `items` |
| `GET /api/sets/{ref}/groups/{sig}/examples` | `max_raw` | `report-entries.v1`: the group's examples keyed by id, in the report's entry shape |
| `GET /api/sets/{ref}/entries` | `signature`, `coarse_signature`, `level`, `min_level`, `logger`, `user`, `thread`, `request_id`, `since`, `until`, `contains`, `with_exception`, `exception`, `columns` (comma-separated and/or repeat), `brief`, `limit` (100, max 10000), `offset`, `max_raw` (4000; 0 = all) | `entries.v1` with `total`, `next_offset`; cut rows get `body_truncated`, `raw_chars` |
| `GET /api/sets/{ref}/entries/{id}/context` | `before` (5), `after` (5), `any_thread`, `max_raw` (4000), `columns`, `brief` | `entries.v1` with `anchor`, `thread`, `any_thread` (as `atlas-log context`) |
| `GET /api/sets/{ref}/frames/{id-or-sig}` | `limit` (10; 0 = all), `all` | `frames.v1` (as `atlas-log frames`; `ATLAS_LOG_APP_PACKAGES` of the server) |
| `GET /api/sets/{ref}/timeline` | `bucket` (`1h`, `15m`, `1d` …), `signature`, `level`, `min_level`, `since`, `until` | `timeline.v1`: `[{bucket, count}]` |
| `GET /api/sets/{ref}/report` | `compare` (`auto` or a set), `min_level`, `examples` (3), `max_raw`, `max_groups` | `report.v1` (cached per set version and options) |
| `GET /api/sets/{ref}/triage` | `baseline` (`auto` or a set), `min_level` (`WARN`), `grow` (2.0), `limit` (50; 0 = all) | `triage.v1` |
| `GET /api/diff` | `a`, `b`, `min_level`, `coarse`, `force`, `limit` (rows per section, default 50, 0 = all), `rate` | `diff.v1` (as `atlas-log diff A B`; `warnings` when the spans differ by more than 4x) |
| `POST /api/ingest` | multipart `files` (repeat); `name`, `format`, `mine`, `overwrite`, `force` as form fields or query | `job.v1`, status 202 and `Location` |
| `GET /api/jobs` | | `jobs.v1` |
| `GET /api/jobs/{id}` | | `job.v1` |
| `POST /api/jobs/{id}/cancel` | | `job.v1` |

`{sig}` in `/groups/{sig}`, `{id-or-sig}` in `/frames/…` and `entries?signature=`
accept a unique signature prefix of at least 4 hex characters (409 `ambiguous`
with up to 5 `candidates` when several groups match), as in the CLI.

`since` and `until` accept ISO 8601 (naive = UTC), `YYYY-MM-DD`, or `30m`/`2h`/`1d`
before the set's last entry; blank means unset. `level` and `min_level` take
the CLI's level names (`TRACE` … `FATAL`, `WARNING` = `WARN`, `UNKNOWN` for
`parse_error` rows; blank = unset; anything else is `invalid_level`).

Differences from the CLI: `GET /api/sets/{ref}/entries` pages with
`1 <= limit <= 10000` (no `limit=0` for "all"; follow `next_offset` instead),
while `atlas-log entries --limit 0` returns every entry.

### Triage

`GET /api/sets/{ref}/triage` answers the question in one call. The document
is built by `store/triage.py` and is the same as `atlas-log triage` prints
(`docs/cli.md`), plus the `report_url` links:

```jsonc
{"schema": "triage.v1",
 "set": {"id", "name", "first_ts", "last_ts", "entries"},
 "baseline": {"id", "name", "first_ts", "last_ts", "resolved_by": "auto|param"} | null,
 "min_level": "WARN",
 "verdict": "new_errors | new_warnings | new_exceptions | clean | no_baseline",
 "summary": {"new", "new_by_level": {"ERROR": n, ...}, "new_exceptions", "grew", "vanished",
             "changed", "unchanged"} | null,
 "coverage": {"hours_baseline", "hours_set", "ratio"},
 "warnings": ["The set covers 31 min but the baseline covers 6.9 h (13.5x shorter): …"],
 "new":            [<diff row> + rate_per_hour + report_url, ...],   // ERROR first, then by count
 "new_exceptions": [<diff row> + rate_per_hour + report_url, ...],   // any level, not in "new"
 "grew":           [<diff row> + ratio + rate_a + rate_b + rate_ratio + report_url, ...],
 "vanished":       [<diff row>, ...],
 "report_url": "/sets/{id}"}
```

The automatic baseline is the newest other set that ends before this one starts
(an earlier snapshot). Failing that, it is the newest other set that ends no later
than this one (an overlapping snapshot). Growth compares hourly rates, so sets of
different lengths compare fairly. `new_exceptions` lists new groups of any level
that carry an exception and are not in `new` (a DEBUG stack trace below
`min_level`); when `new` is empty but `new_exceptions` is not, the verdict is
`new_exceptions`. `coverage` gives both spans in hours (`ratio = hours_set /
hours_baseline`); when they differ by more than 4x either way, `warnings` says
that new/vanished groups are unreliable (a nightly set lacks daytime groups).
`baseline=none` gives `no_baseline`.

### Ingest jobs

Uploads stream to `<store>/.uploads/<job>/` in 1 MiB chunks and are rejected with
413 above `--max-upload-mb`. Jobs run one at a time in a worker thread. The job
reports parsed entries, bytes read and the current file, and `fraction` goes
from 0 to 1. A successful job's `result` is the `logset.v1` manifest with the
`ingest` block, the same as `atlas-log ingest` prints. A failed job's `error`
holds `code` and `message` (`no_sources`, `format_not_detected`,
`unknown_format`, `not_in_format`, `store_error`, `invalid_value`,
`missing_dependency`) and, for `not_in_format` (an upload over 64 MiB that is
not in the format), a `hint`: send `force=true` to ingest it anyway. Cancelling stops the job
at its next progress callback; the store discards the partial set. Upload
directories are removed when the job ends, when the server stops (jobs still
queued are cancelled) and, for spools left by a crashed server, when the next
server starts. Jobs live in memory, so a restart forgets them; the sets they
created remain. Pages and `/api` keep answering while a job runs.

One server per store: the server holds an OS lock on `<store>/.server.lock`
(released when the process ends, also after a crash), and a second
`atlas-log serve` on the same store exits with code 3 ("another atlas-log
server is already using the store"). CLI commands, including `ingest`, may
run next to the server.

## Design notes

- **Dependencies:** `webapp` imports `core`, `store` and `report`, never `cli`.
  The CLI imports FastAPI and uvicorn only inside `serve`, and fails with exit 1
  and an install hint when the `web` extra is missing.
- **Concurrency:** request threads and the ingest worker share one `Store`.
  It locks only its manifest cache (loading happens outside the lock) and, for
  a write, the final name check and rename (also across processes, through
  `<store>/.write.lock`), so an ingest never blocks reads. Log sets are
  immutable Parquet once written, so readers do not lock. On Windows a set
  directory cannot be renamed or deleted while a reader has a file open, so
  `--overwrite` and delete retry for about 1.5 s. Set summaries and report
  payloads are cached per set id and `created_at`, and dropped on delete or
  re-ingest.
- **Encoding:** large documents are encoded with msgspec. The report payload is
  cached as encoded bytes, so a repeat request for the 712k-entry sample set
  takes about 20 ms instead of about 200 ms.
- **Security:** binds to loopback by default; `--token` guards `/api`. Uploaded
  file names are reduced to a safe basename. Error messages do not include
  server paths. Arbitrary SQL (`atlas-log query`) is not exposed over HTTP.

## Not built yet

Acknowledging a group as "known" so it drops out of future "new" lists, and
tagging sets as release baselines, were designed in the review but are not
implemented. Jobs are not persisted across restarts.

## Code

```
src/atlas_log_analyzer/webapp/
  __init__.py   create_app, Settings
  app.py        create_app(): routers, static files, HTML/JSON error handling
  settings.py   Settings (flags > ATLAS_LOG_* env > defaults)
  state.py      AppState: store, server lock, summary and report caches, JobManager
  api.py        /api routes; triage and the auto baseline come from store/triage.py
  pages.py      HTML routes (Jinja2), report page assembly
  jobs.py       JobManager, Job (job.v1), upload spool
  errors.py     ApiError, error.v1 handlers
  responses.py  msgspec JSON response
  templates/    base, index, triage, 404
  static/       webapp.css, webapp.js
tests/webapp/   TestClient tests over the masked fixtures
```
