# Web app

`ragnalog serve` runs a FastAPI app over one data directory (the folder that keeps the log sets). It answers the main
question, "compared with the previous snapshot, are there new kinds of
errors?", on one page, opens any set in the interactive report, and ingests
uploaded log files in the background. Every page has a JSON twin under `/api`
with the same documents as `ragnalog --output json`, so a human and an agent
can work from the same data.

```sh
uv sync --extra web                      # or install a release: README, Quick start
ragnalog --store ~/ragnalog-data serve     # http://127.0.0.1:8321/
ragnalog serve --host 0.0.0.0 --token "$(openssl rand -hex 16)"    # on a network
```

| Flag | Env | Default |
|---|---|---|
| `--store DIR` (global) | `RAGNALOG_STORE` | `./.ragnalog` (not created inside a git work tree) |
| `--host` | | `127.0.0.1` |
| `--port` | | `8321` |
| `--token` | `RAGNALOG_TOKEN` | none: no auth |
| `--max-upload-mb` | `RAGNALOG_MAX_UPLOAD_MB` | `2048` |
| `--allowed-host` | `RAGNALOG_ALLOWED_HOSTS` | none: on loopback only loopback names |
| | `RAGNALOG_REPORT_CACHE` | `8` report payloads kept in memory (and as many cluster payloads) |
| `--reload` | | off (development) |

Flags win over the environment. With `--reload` uvicorn runs the app in a child
process that sees only the environment, so `serve` exports every setting
(`RAGNALOG_STORE`, `RAGNALOG_TOKEN`, `RAGNALOG_ALLOWED_HOSTS`, `RAGNALOG_MAX_UPLOAD_MB`,
`RAGNALOG_REPORT_CACHE`) before it starts: a reloading server keeps its token.

With `--token` every page and `/api` route needs it, checked before an upload body is
read (and an upload whose `Content-Length` is over `--max-upload-mb` is refused at
once). Clients send `Authorization: Bearer TOKEN` or `X-Ragnalog-Token: TOKEN`; a browser
opens any page once with `?token=TOKEN`, which sets an HttpOnly, SameSite=Strict
cookie and redirects (a relative `Location`: path and the other query parameters) to
the address without it. Pages without the token show a 401
page; `/static` and `/assets` (CSS, JS) need none.

## Pages

| Path | Page |
|---|---|
| `/` | Sets, newest first: time range with an **activity strip** (every row on one shared time axis: entries per hour up from a midline, errors down, so overlapping sets line up; hovering shows that hour on all rows, a click opens the row's report narrowed to it), entries, groups, error and warning counts, a triage verdict against the previous set, and an upload form with live job progress. The title line is compact (the data directory's path is in a tooltip) and the shell command for ingesting is folded under "Ingest from a shell". The sets of one capture (`captures`) are listed under one capture row (a tinted group head with an uppercase "capture" label above its indented node rows): cluster, capture id, node count, time range, summed counts and the worst verdict of its nodes (each against its own previous set), with a link to the capture page `/captures/capture:ID`; each member row shows its node. |
| `/sets/{ref}` | The interactive report (`docs/report.md`). `?compare=auto` or `?compare=SET` adds the comparison; `min_level`, `examples` pass through. Every page has a navigation row (40 px, a hairline below it): the breadcrumb on the left (ragnalog with its menu of Log sets, Ingest and API, then the set switcher and, for a set of a capture, the node switcher, then the page; the switchers keep the URL hash, so filters survive switching sets), the view tabs on the right as a segmented control with an icon each (Report, Triage, Entries, Cluster view, Compare with previous; each has a tooltip card saying what the view shows, e.g. what triage compares; the current one is raised; at 1100 px and below only the icons show, at 720 px the tabs wrap under the breadcrumb). The page header under it has the title and meta on the left and on the right the page actions where a page has them (triage: the Baseline and Level selects; entries: "Open in report" or "Open in cluster view" when the filter is one group), a hairline, then settings as ghost icon buttons (time zone, theme, full screen width key `w`, help on the report and cluster pages); the width setting is shared by every page. On the report and cluster pages the settings end with a "⋯" menu *For scripts and agents* (Identifiers & CLI commands, Download the data as JSON). The triage crumb has a set switcher (it opens that set's triage); the index, 404 and 401 rows have the breadcrumb only. A set of a capture also gets a node crumb (its sibling node sets, keeping the hash). |
| `/captures/{ref}` | The cluster page of a capture (`docs/cluster.md` §8, `docs/report.md` §5): `ref` is `capture:ID`, a bare capture id, `cluster:NAME[@YYYY-MM-DD]` or a set (its capture). When the nodes' logs do not overlap the page opens the `union` window instead of an error. The page fetches `/api/captures/{ref}/report`, its merged log `/api/captures/{ref}/entries` (with its level, group, node and search filters: `min_level`, `signature`, `node`, `contains`) and "±30 s on all nodes" `/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30`; `window=union` passes through; `since`/`until` are UTC whichever zone the page shows its times in (header toggle). The breadcrumb (ragnalog / cluster) carries the capture id in its tooltip; left of the view tabs (Cluster view, Entries) a small framed "Nodes" group links each node's report (a dot in the node's colour and its name). An unknown capture is the 404 page. After an upload that split into one set per node, the upload form opens this page. |
| `/sets/{ref}/triage` | Verdict, a coverage warning when the spans differ by more than 4x, then new types (most severe first), new exceptions below the level filter, groups whose hourly rate grew at least ×2, vanished groups. The verdict line names the baseline (its id, files and why it was picked are in a tooltip); there are no tiles: the sticky section bar (one chip per section) carries the counts (levels and the rest in tooltips) and jumps between the sections; once stuck it marks the section in view (`aria-current="location"`) and its page name scrolls back to the top. Each section folds (Vanished starts folded) and shows its first 25 rows with "Show all". Each row links into the report with that group open. |
| `/sets/{ref}/entries/{id}/raw` | One entry's exact text as in its log file (banner lines first), opened by the cards' "Open raw ↗": rendered by `entry-card.js`, so its stack trace is highlighted as in the cards (application frames marked, library frames dimmed, every frame shown), with Copy and "Plain text ↗" (`GET /api/sets/{ref}/entries/{id}/raw`). |
| `/sets/{ref}/entries`, `/captures/{ref}/entries` | A table of the entries behind a filter, opened in a new tab by the drawers' "All N entries ↗" link (the group's `signature`; with a time window selected also `since`/`until`, and "in window" in the label). The query is the one of `GET /api/.../entries` (`signature`, `coarse_signature`, `level`, `since`, `until`, `contains`, `thread`, `user`, `logger`, `exception`, `request_id`, `node`; a capture also takes `NODE:SIGNATURE`), so a link can carry any subfilter; the page embeds the first 100 rows (a bad filter is an error page, not an empty table) and loads more from the API. Header: the group's template (placeholders as chips), level, logger, exception, entry count and time range (separated by space, not by dots, so a wrapped line never ends in one) and "Open in report" / "Open in cluster view" with the group open. Under it a time histogram of the entries (`GET .../timeline`, one group: its level's colour; the chart applies all of the page's filters (group, node, level, thread, user, request, logger, text and exception), for a capture too, so it counts what the table lists; bucket from a minute to a day, about 200 bars at most; the bar height is linear, a non-empty bucket is at least 5 px high and its tooltip, which opens above the pointer so it never covers the filter chips, has the true count): drag over it to select a time range, or click a bar for its bucket; that sets `since`/`until`, the chart zooms to the window (finer buckets) and "Full range" or the chip's × returns. Not drawn for several groups or a coarse group, nor with one bucket. A sticky bar holds the search box (`contains`, debounced), a removable chip for every active filter (the group chip shows the level and a short template, the signature is in its tooltip) and Reset; the URL follows the filters, so a filtered view can be bookmarked or shared. A click on a thread, node or level value in a row, or on thread / user / request / node in an open card, adds it as a filter (`thread`, `user`, `request_id`, `node`, `level`); a column whose value every row shares is left out. No match: the empty state lists the filters as removable chips. Columns: time, node (capture), level (unless the table is one group), thread and the first line of the message (one line, ellipsis, the full text in the tooltip; the header row sticks under the bar). A click or Enter opens the full entry card under the row (several at once; the shared `entry-card.js`: syntax-coloured message, stack trace, Raw, Open raw, Copy raw; an entry cut at 4,000 characters is fetched whole when opened); `j`/`k` move, `Esc` closes, `/` searches. The entries page has the same keyboard help as the report: `?` opens a Keyboard dialog (also reachable from the "? keys" button) listing `/`, `j`/`k`/arrows, Home/End, Enter/Space, Esc, `w` and `z`; `z` switches UTC / local time exactly like the header's time-zone button, and the shortcuts are ignored while typing in a field or while a dialog is open. A capture's rows are its nodes interleaved by time over the union window. |
| `/docs` | OpenAPI (Swagger UI) for the JSON API; `/openapi.json` is the schema, with every response document as a component. |

The log-set list opens directly on its search toolbar and table. *Ingest logs*
reveals the upload form; it starts expanded for an empty data directory and for
a direct `/#upload` link. Merge proposals and rebuild options are under *Advanced
options*. Triage uses compact section links with counts; *Baseline details*
reveals coverage metadata and the explanation of the baseline choice, while
warnings remain visible. Report totals are under *Set details*, advanced report
filters under *More filters*, and secondary cluster controls under *Chart options*.

Pages print times in UTC; the header's zone button (and the report's and
cluster page's own) switches them to the browser's time zone. It is one viewer
preference for every page (`localStorage`, like the theme); links, windows in the
hash and `/api` parameters stay UTC.

The palette button beside the light/dark toggle switches between **Classic**, **Aurora**
and **Mineral** without changing the compact layout. Aurora is the default when no valid
preference can be read, including when storage is blocked. Explicitly saved Classic
or Mineral choices remain selected. The choice persists across pages
and reloads, independently of light/dark mode. The same skins work in standalone HTML
reports; the menu closes with Escape or a click outside it.

`{ref}` is a set id, a unique id prefix or a name, as in the CLI. Unknown sets
and paths render a 404 page, and any other error of a page (an ambiguous
reference, a bad parameter) an HTML page with its status and hint; under `/api`
they return `error.v1`. Pages also answer `HEAD`.

The report page is the same `template.html`, `report.js` and `report.css` as the
static file (the cluster page: `cluster.html`, `cluster.js`, `cluster.css`); every page,
the web app's own included, takes its colour tokens from `report.css` and its tooltips
from `tips.js`, and every entry card (the report's, the cluster page's, the entries
page's) is built by `entry-card.js`. The page is rendered without an embedded payload and
`data-report-url` points at `/api/sets/{id}/report`; the assets are served from
`/assets/report/`. The web app's payload carries each group's example ids but not
the entries (they were three quarters of its size): the drawer fetches them per
group from `/api/sets/{id}/groups/{sig}/examples` when it opens.

## JSON API

All responses carry `"schema": "<name>.v1"`; listings put rows under `"items"`.
Errors are `{"schema": "error.v1", "error": {"code", "message", "hint"?, "reason"?}}`
(an ambiguous signature prefix adds `"candidates"`). Codes and statuses come from
one table shared with the CLI (`service/errors.py`, `SPEC`); the exit column is the
CLI's:

| Code | Exit | HTTP | Meaning |
|---|---|---|---|
| `usage` | 1 | 400 | Bad option or argument (parse error, unexpected argument, `brief` with `columns`, a page over the API's cap). |
| `invalid_value` | 1 | 400 | A value that is not valid for its option or out of range (time, column, limit, bucket; a number option given no number is `usage`); in the CLI also sets that are not comparable. |
| `invalid_level` | 1 | 400 | Unknown level name. |
| `unknown_format` | 1 | 400 | Unknown format name. |
| `sql_error` | 1 | 400 | DuckDB rejected the SQL of `query`. |
| `ambiguous` | 1 | 409 | A signature prefix (or, in the API, a set reference) matches several; signature errors list `candidates`. |
| `incompatible_sets` | 1 | 409 | The API's code for sets built with different signature or mask-rule versions or specs. |
| `missing_dependency` | 1 | 500 | An optional extra is not installed (`mine`, `web`). |
| `unauthorized` | 1 | 401 | Missing or wrong API token. |
| `payload_too_large` | 1 | 413 | An upload over the size limit. |
| `file_not_found` | 2 | 404 | A named file or directory is missing. |
| `no_sources` | 2 | 422 | No log files in the given paths. |
| `format_not_detected` | 2 | 422 | No format recognises the files. |
| `not_in_format` | 2 | 422 | A named file over 64 MiB is not in the format (force reads it). |
| `no_entries` | 2 | 422 | No entries could be parsed. |
| `not_found` | 2 | 404 | No such group, entry or job; in the API also no such set. |
| `store_error` | 3 | 500 | Data directory failure: corrupt manifest or damaged set, newer schema, data directory refused or in use, a path the system refuses (permission, a file where a directory belongs); in the CLI also an unknown or ambiguous set. |
| `http` | 1 | 400 | Another HTTP error (the API sends its own status). |
| `internal_error` | 1 | 500 | Unexpected exception (set `RAGNALOG_DEBUG=1` for the traceback). |

Where the CLI and the API report one failure under different codes (an unknown
set is the CLI's `store_error` but the API's `not_found`), both carry the same
`"reason"`; branch on it when present:

| Reason | CLI code | API code | Meaning |
|---|---|---|---|
| `set_not_found` | `store_error` | `not_found` | No log set has this id, name or id prefix. |
| `ambiguous_ref` | `store_error` | `ambiguous` | Several log sets share this name or id prefix. |
| `ambiguous_signature` | `ambiguous` | `ambiguous` | A signature prefix matches several groups (`candidates`). |
| `incompatible_sets` | `invalid_value` | `incompatible_sets` | The sets were built with different signature or mask-rule versions or specs (`diff` takes force). |
| `group_not_found` | `not_found` | `not_found` | No group (or no entries) with this signature. |
| `entry_not_found` | `not_found` | `not_found` | No entry with this id. |
| `ref_not_found` | `not_found` | `not_found` | `frames`: neither an entry id nor a group signature. |
| `capture_not_found` | `not_found` | `not_found` | No capture matches this `capture:ID` prefix, `cluster:NAME` or `cluster:NAME@DAY`. |
| `ambiguous_capture` | `ambiguous` | `ambiguous` | Several captures match this capture id prefix or cluster day. |

| Method and path | Parameters | Document |
|---|---|---|
| `GET /api/health` | | `health.v1` |
| `GET /api/version` | | `version.v1` |
| `GET /api/manifest` | | `manifest.v1`, the agent manifest (as `ragnalog manifest`) |
| `GET /api/manifest/{command}` | | `manifest-command.v1`: one command in full (as `ragnalog manifest COMMAND`) |
| `GET /api/formats` | | `formats.v1` (as `ragnalog formats`: `name`, `description`, `signature_spec`, `mask_rules`) |
| `GET /api/sets` | `brief` (as `sets --brief`: `id, name, first_ts, last_ts, entry_count, group_count, summary, stale, node, capture`) | `sets.v1`; items are manifests plus `summary {errors, warnings, error_groups}` |
| `GET /api/sets/{ref}` | | `logset.v1` plus `summary` (as `ragnalog set SET`) |
| `DELETE /api/sets/{ref}` | | `remove.v1` |
| `POST /api/sets/{ref}/label` | `node`, `cluster`, `capture` as query or form fields; one not sent keeps its value, an empty one clears it (as `ragnalog label`) | `logset.v1` |
| `GET /api/captures` | `brief` (only `node`, `id`, `name` per node, as `captures --brief`) | `captures.v1`: the sets that share a capture id, newest first |
| `GET /api/captures/{ref}` | `window` (`overlap`, `union`), `since`, `until`, `triage` (default true: each node against its own previous set) | `capture.v1` (as `ragnalog capture`) |
| `GET /api/captures/{ref}/timeline` | `bucket` (`1h`), `window`, and the filters of `entries` (`signature` (one group), `coarse_signature`, `level`, `min_level`, `logger`, `user`, `thread`, `request_id`, `since`, `until`, `contains`, `with_exception`, `exception`, `node`) | `cluster-timeline.v1` (as `timeline CAPTURE`) |
| `GET /api/captures/{ref}/matrix` | the filters of `/groups` (`since`/`until` set the window), `window`, `node`, `spread` (`all`, `only`, `skewed`), `sort` (`count`, `skew`, `lead`), `skew_min` (4.0), `signature`, `limit` (50; 0 = all), `offset` | `matrix.v1` (as `ragnalog matrix`) |
| `GET /api/captures/{ref}/events` | window parameters, `kind` (comma-separated and/or repeat) | `events.v1` (as `ragnalog events`) |
| `GET /api/captures/{ref}/interactions` | window parameters, `delta` (300 s) | `interactions.v1` (as `ragnalog interactions`) |
| `GET /api/captures/{ref}/entries` | the filters of `/sets/{ref}/entries` (`since`/`until` set the window; `signature` may name several groups, comma-separated, each also `NODE:SIGNATURE`: that group on that node only), `node` (comma-separated: only those nodes, the window stays all nodes'; an unknown node is 400 `invalid_value`), `window`, `columns`, `brief`, `limit` (100, max 10000), `offset`, `max_raw` | `entries.v1` of all nodes by time; rows add `node`, `set` (as `entries CAPTURE`) |
| `GET /api/captures/{ref}/report` | window parameters, `examples` (3), `max_raw` (4000), `max_groups` (500; 0 = all), `triage` (true) | `cluster-report.v1` with `urls` (`entries`, `context`, `set_report`, `examples`, `raw`); cached per member set versions and options, dropped when any set changes |
| `GET /api/sets/{ref}/stats` | | `stats.v1` |
| `GET /api/sets/{ref}/groups` | `level`, `min_level`, `logger`, `since`, `until`, `with_exception`, `exception`, `contains`, `sort`, `limit` (50; 0 = all), `offset`, `coarse`, `columns` (comma-separated and/or repeat), `brief` | `groups.v1` with `total`, `offset`, `truncated` |
| `GET /api/sets/{ref}/groups/{sig}` | `limit` (20; 1..10000, no `0` for all), `coarse`, `max_raw` (4000; 0 = all), `columns`, `brief` (as `show --columns/--brief`; `brief` also cuts `group`) | `group.v1`: `group`, hourly `histogram`, first entries in `items` |
| `GET /api/sets/{ref}/groups/{sig}/examples` | `max_raw`; `since`, `until`, `limit` (3, max 50) | `report-entries.v1`: the group's examples keyed by id, in the report's entry shape; with `since`/`until` its first `limit` entries in that window instead (the report's time window) |
| `GET /api/sets/{ref}/entries` | `signature`, `coarse_signature`, `level`, `min_level`, `logger`, `user`, `thread`, `request_id`, `since`, `until`, `contains`, `with_exception`, `exception`, `columns` (comma-separated and/or repeat), `brief`, `limit` (100, max 10000), `offset`, `max_raw` (4000; 0 = all); `node`, several signatures and `NODE:SIGNATURE` are 400 `usage` (they apply to a capture) | `entries.v1` with `total`, `next_offset`; cut rows get `body_truncated`, `raw_chars` |
| `GET /api/sets/{ref}/entries/{id}/raw` | | `text/plain`: the exact text of the entry as in its log file, banner lines it carries first (the plain-text link of the entry text page; not in the OpenAPI schema) |
| `GET /api/sets/{ref}/entries/{id}/context` | `before` (5), `after` (5), `any_thread`, `max_raw` (4000), `columns`, `brief`; `all_nodes` with `window` (30 s): every node of the set's capture around the entry | `entries.v1` with `anchor`, `thread`, `any_thread` (as `ragnalog context [--all-nodes]`; with `all_nodes` rows add `node`, `set`) |
| `GET /api/sets/{ref}/frames/{id-or-sig}` | `limit` (10; 0 = all), `all` | `frames.v1` (as `ragnalog frames`; `RAGNALOG_APP_PACKAGES` of the server) |
| `GET /api/sets/{ref}/timeline` | `bucket` (`1h`, `15m`, `1d` …) and the filters of `entries` (`signature`, `coarse_signature`, `level`, `min_level`, `logger`, `user`, `thread`, `request_id`, `since`, `until`, `contains`, `with_exception`, `exception`) | `timeline.v1`: `[{bucket, count}]` |
| `GET /api/sets/{ref}/report` | `compare` (`auto` or a set), `min_level`, `examples` (3), `max_raw`, `max_groups` | `report.v1` (cached per set version and options) |
| `GET /api/sets/{ref}/triage` | `baseline` (`auto`, a set, or `none`/empty for no baseline), `min_level` (`WARN`), `grow` (2.0), `limit` (50; 0 = all) | `triage.v1` |
| `GET /api/diff` | `a`, `b`, `min_level`, `coarse`, `force`, `limit` (rows per section, default 50, 0 = all), `rate` | `diff.v1` (as `ragnalog diff A B`; `warnings` when the spans differ by more than 4x) |
| `POST /api/ingest` | multipart `files` (repeat; log files or ZIPs); `name`, `format`, `frames`, `mine`, `overwrite`, `force`, `node`, `cluster`, `capture`, `split` (`auto`, `prefix`, `dir`, `none`) as form fields or query (as the `ingest` options) | `job.v1`, status 202 and `Location` |
| `GET /api/jobs` | | `jobs.v1` |
| `GET /api/jobs/{id}` | | `job.v1` |
| `POST /api/jobs/{id}/cancel` | | `job.v1` |

`{sig}` in `/groups/{sig}`, `{id-or-sig}` in `/frames/…`, `entries?signature=` and `timeline?signature=`
accept a unique signature prefix of at least 4 hex characters (409 `ambiguous`
with up to 5 `candidates` when several groups match), as in the CLI.

The filters of `/groups` and `/entries` and the column selection are the
request models of the CLI (`service/params.py`: `GroupQuery`, `EntryQuery`,
`Select`), read from the query string by `webapp/params.py` (`query_model`), so
they parse as the CLI options do and their OpenAPI descriptions are the CLI's
`--help` texts. A blank value means unset. `since` and `until` accept ISO 8601
(naive = UTC), `YYYY-MM-DD`, or `30m`/`2h`/`1d` before the set's last entry.
`level` and `min_level` take the CLI's level names (`TRACE` … `FATAL`, `WARNING`
= `WARN`, `UNKNOWN` for `parse_error` rows; anything else is `invalid_level`).
`columns` takes comma-separated and/or repeated names; an unknown one is
`invalid_value` with "did you mean" and the valid columns in `hint`.

Each document's JSON Schema is in `docs/formats/NAME.schema.json` (and
`ragnalog schema NAME`); `/openapi.json` holds the same schemas as components
(`triage.v1`, `groups.v1`, … and the rows `EntryRow`, `GroupRow`, `DiffRow`,
`MergeRow`), each operation's success response refers to its document, and
every `/api` operation documents its errors as `error.v1` (status 400 for a bad
parameter, not FastAPI's 422: a number below its bound, such as `offset=-1`, or past
2147483647 (what the store counts) is `invalid_value` as in the CLI; a page cap and any
other bad parameter are `usage`). `webapp/openapi.py` maps the operations to
the documents; a new route needs an entry there (a test checks it).

`{ref}` under `/api/captures` is a capture reference (`capture:ID`, a unique
id prefix; `cluster:NAME`, its newest capture; `cluster:NAME@YYYY-MM-DD`) or a
set: the capture it belongs to, else that set as a capture of one node. The
window parameters are `window` (`overlap`, the default: the time all nodes
have logs for; `union`), `since` and `until` (an explicit window, the forms of
`since` above, relative times counting back from the capture's last entry).
An unknown capture is 404 with reason `capture_not_found`. Triage takes one
set: per-node verdicts are `GET /api/captures/{ref}?triage=true`.

Differences from the CLI: `GET /api/sets/{ref}/entries` pages with
`1 <= limit <= 10000` (no `limit=0` for "all"; follow `next_offset` instead),
while `ragnalog entries --limit 0` returns every entry.

### Triage

`GET /api/sets/{ref}/triage` answers the question in one call. The document
is built by `store/triage.py` and is the same as `ragnalog triage` prints
(`docs/cli.md`), plus the `report_url` links:

```jsonc
{"schema": "triage.v1",
 "set": {"id", "name", "first_ts", "last_ts", "entries"},
 "baseline": {"id", "name", "first_ts", "last_ts", "resolved_by": "auto|param",
              "reason", "entries", "shared_sources",
              "candidates": [{"id", "name", "reason", "detail", "hint"?}]} | null,
 "min_level": "WARN",
 "verdict": "new_errors | new_warnings | new_exceptions | clean | no_baseline",
 "summary": {"new", "new_by_level": {"ERROR": n, ...}, "new_exceptions", "grew", "vanished",
             "changed", "unchanged"} | null,
 "coverage": {"hours_baseline", "hours_set", "ratio"},
 "warnings": ["The set covers 31 min but the baseline covers 6.9 h (13.5x shorter): …"],
 "new":            [<diff row> + rate_per_hour + report_url, ...],   // ERROR first, then by count
 "new_exceptions": [<diff row> + rate_per_hour + report_url, ...],   // any level, not in "new"
 "grew":           [<diff row> + ratio + rate_a + rate_b + rate_ratio + report_url, ...],
 "vanished":       [<diff row> + report_url, ...],     // report_url: the baseline's report
 "report_url": "/sets/{id}"}
```

The automatic baseline (`baseline=auto`, also `compare=auto` of the report and
the verdicts of the index page) comes from `store.triage.rank_baselines`, the
rule of `ragnalog triage` (`docs/cli.md`): other sets of the same format,
without those signed with another recipe (`stale`, `recipe`), whose sources are
a superset or subset of this set's, that end later, that are tiny (under 1% of
the entries) or whose span differs more than 16x; then a set that ends before
this one starts, the closest span, the newest. With none left the verdict is
`no_baseline` and `warnings` lists the rejected sets; the triage page shows
them, and the chosen baseline's `reason`. Growth compares hourly rates, so sets of
different lengths compare fairly. `new_exceptions` lists new groups of any level
that carry an exception and are not in `new` (a DEBUG stack trace below
`min_level`); when `new` is empty but `new_exceptions` is not, the verdict is
`new_exceptions`. `coverage` gives both spans in hours (`ratio = hours_set /
hours_baseline`); when they differ by more than 4x either way, `warnings` says
that new/vanished groups are unreliable (a nightly set lacks daytime groups).
`baseline=none` gives `no_baseline`.

### Ingest jobs

Uploads stream to `<data dir>/.uploads/<job>/` in 1 MiB chunks and are rejected with
413 above `--max-upload-mb`. Before the body is read (from `Content-Length`) an upload
is refused with 503 and `Retry-After` when 8 jobs are queued or running, and with 507
when the data directory's disk has less free than twice the upload plus 256 MiB (error code
`http`; nothing is written). The 100 most recent finished jobs are kept; older ones are
forgotten (`GET /api/jobs/{id}` then answers `not_found`). Jobs run one at a time in a worker thread. The job
reports parsed entries, bytes read and the current file, and `fraction` goes
from 0 to 1. A successful job's `result` is the `logset.v1` manifest with the
`ingest` block, the same as `ragnalog ingest` prints (`ingest.warnings` says
when `name` was ignored because the set existed already). A failed job's `error`
holds `code` and `message` (`no_sources`, `format_not_detected`,
`unknown_format`, `not_in_format`, `store_error`, `invalid_value`,
`missing_dependency`) and, for `not_in_format` (an upload over 64 MiB that is
not in the format), a `hint`: send `force=true` to ingest it anyway. Cancelling stops the job
at its next progress callback (every 20,000 entries) and the store discards the partial set;
a job already past its last callback finishes (cancel is best-effort: the job document says
how it ended). Upload
directories are removed when the job ends, when the server stops (jobs still
queued are cancelled) and, for spools left by a crashed server, when the next
server starts. Jobs live in memory, so a restart forgets them; the sets they
created remain. Pages and `/api` keep answering while a job runs.

A ZIP upload is extracted as by `ragnalog ingest` (only `job.json` and
`*.log*`, safe paths, at most 32 GiB, removed afterwards). When the upload splits
into one set per node (a cluster log export; `split` as in `ingest`), the job
also carries `capture`, the `ingest-capture.v1` document, and `result` is its
first set; `progress` counts all nodes. The upload form then returns to the sets
page, which shows the new capture row. A split job that fails or is cancelled
removes the sets it had added (as `ingest` does), so no half capture remains.

One server per data directory: the server holds an OS lock on `<data dir>/.server.lock`
(released when the process ends, also after a crash), and a second
`ragnalog serve` on the same data directory exits with code 3 ("another ragnalog
server is already using the data directory"). CLI commands, including `ingest`, may
run next to the server.

## Design notes

- **Dependencies:** `webapp` imports `core`, `store`, `report` and `service`,
  never `cli` (`tests/test_layers.py`): `GET /api/manifest[/{command}]` serves the
  generated `service/manifest.json`.
  The CLI imports FastAPI and uvicorn only inside `serve`, and fails with exit 1
  and an install hint when the `web` extra is missing.
- **Concurrency:** request threads and the ingest worker share one `Store`.
  It locks only its manifest cache (loading happens outside the lock) and, for
  a write, the final name check and rename (also across processes, through
  `<data dir>/.write.lock`), so an ingest never blocks reads. Log sets are
  immutable Parquet once written, so readers do not lock. On Windows a set
  directory cannot be renamed or deleted while a reader has a file open, so
  `--overwrite` and delete retry for about 1.5 s. Set summaries and report
  payloads are cached per set id and `created_at`, and dropped on delete or
  re-ingest. Cluster payloads are cached per member set versions and options
  and all dropped when any set is ingested, removed or relabelled (that can
  change a capture's members and every node's triage baseline).
- **Spools:** at start the server deletes the upload spools of an earlier
  server (`<data dir>/.uploads`) and the entries of the ZIP spool
  (`<data dir>/.spool`) older than 6 hours, which a crashed ingest left; younger
  ones may belong to an ingest that is still running.
- **Encoding:** large documents are encoded with msgspec. The report payload is
  cached as encoded bytes, so a repeat request for the 712k-entry sample set
  takes about 20 ms instead of about 200 ms. Report payloads are sent gzipped
  (the compressed bytes are kept too; a 745k-entry set's payload is 170 KB on the
  wire) with an `ETag`, so a revisit is a `304`; other JSON over 4 KB is gzipped
  on the fly. Concurrent requests for one uncached payload wait for a single
  build. With `triage` a cluster payload is also keyed on every set's version,
  so a baseline added or removed by the CLI next to the server is noticed.
- **Warm start:** `ragnalog serve` builds the newest capture's cluster page payload
  in a background thread at startup (about half a second for two nodes of 700k
  entries each), so its first open is a cache hit.
- **Security:** binds to loopback by default; `--token` guards the pages and `/api`.
  Requests that change something (POST, DELETE) sent by a page of another site are
  refused with 403: a form on any web page could otherwise post to the local server.
  `Sec-Fetch-Site` decides when the browser sends it (only `same-origin` and `none`
  pass); otherwise `Origin` must name the host the browser addressed (`Host`, or a
  proxy's `X-Forwarded-Host`; default ports aside). On a loopback address only
  loopback `Host` names are answered (400 otherwise), so a page that rebinds its own
  DNS name to 127.0.0.1 cannot read the API. No header lifts that check (a
  rebinding page is same-origin and may send any): behind a reverse proxy that passes
  the public `Host` through, name it with `--allowed-host` (`RAGNALOG_ALLOWED_HOSTS`);
  a proxy that rewrites `Host` to the upstream needs nothing (`--reload` checks it
  per connection, by the address it arrived on). A `--host` off loopback without
  `--token` prints a warning at startup: anyone who reaches the port may upload and
  delete sets. An upload needs
  `Content-Length` (411 otherwise; browsers always send it), so its size is checked
  before the body is read; any other request body is capped at 64 KiB (413), and the
  label form takes fields only. Uploaded file names are reduced to an ASCII basename of
  at most 120 characters (a Windows device name such as `nul.log` or `COM¹.log` gets a
  `_` prefix). Set
  references are one plain name (no separators, `..` or drive). Error messages,
  job errors included, do not include server paths (a path is cut to its file name), and
  the access log prints `token=***` for the first visit's `?token=`. Arbitrary SQL (`ragnalog query`) is not exposed over HTTP.

## Not built yet

Acknowledging a group as "known" so it drops out of future "new" lists, and
tagging sets as release baselines, were designed in the review but are not
implemented. Jobs are not persisted across restarts.

## Code

```
src/ragnalog/webapp/
  __init__.py   create_app, Settings
  app.py        create_app(): routers, static files, HTML/JSON error handling
  settings.py   re-exports config.Settings (flags > RAGNALOG_* env > defaults), shared with the CLI
  state.py      AppState (a service Sets): store, server lock, summary, report and cluster
                payload caches, spool cleanup, JobManager
  api.py        /api routes: bind the request, call service/, add report_url
  params.py     query_model(): a service request dataclass read from the query string
  pages.py      HTML routes (Jinja2), report and cluster page assembly, breadcrumb lines and view links
  jobs.py       JobManager, Job (job.v1), upload spool; ingest via service.ingest
  errors.py     error.v1 handlers for OpError (service/errors.py) and library errors
  openapi.py    document schemas in /openapi.json, GET /api/manifest
  responses.py  msgspec JSON response
  templates/    base, index, triage, entries, 404
  static/       webapp.css, webapp.js, entries.js
tests/webapp/   TestClient tests over the masked fixtures
```
