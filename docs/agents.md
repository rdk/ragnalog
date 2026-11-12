# atlas-log for agents

How a coding or ops agent drives `atlas-log`: the workflow, the JSON conventions and a
cookbook of common questions. The command reference is [`cli.md`](cli.md); every document's
JSON Schema is in [`formats/`](formats/).

## Workflow

Start with `atlas-log manifest` (bare `atlas-log` prints the same when stdout is
not a terminal): about 10 KB of JSON with this workflow, every command's usage,
output document and exit codes, one-line help per parameter, the `--brief`
presets and where the document schemas are (`atlas-log schema triage.v1`).
`atlas-log manifest COMMAND` gives one command in full (every parameter with its
type, default and help) when you are about to run it.

```sh
atlas-log manifest                                          # 0. how to use the tool (manifest.v1)
atlas-log ingest LOGS/ --name today                         # 1. build the set (JSON manifest)
atlas-log triage today                                      # 2. verdict vs the previous set
atlas-log groups today --min-level ERROR --limit 20 --brief # 3. groups: check "total"/"truncated"
atlas-log groups today --with-exception --brief             #    stack traces at any level
atlas-log show today SIGNATURE --brief --limit 5            # 4. drill into one group
atlas-log frames today SIGNATURE                            #    its stack trace, plugin frames first
atlas-log context today ENTRY_ID --before 10 --brief        #    what the thread did around it
atlas-log entries today --request-id REQUEST_ID --brief     #    the whole HTTP request
atlas-log entries today --signature SIGNATURE --since 2h --raw   #    the raw text
atlas-log diff yesterday today --rate --limit 20            # 5. compare with another set
```

Repeat 3–4; on an error, read `error.hint` for the next step. `SIGNATURE`
may be a unique prefix (4+ hex characters); `atlas-log sets --brief` lists the
sets with their error and warning counts, `atlas-log set SET` shows one.

Every call pays the start-up of the CLI (about 0.45 s). For several commands in
a row, `atlas-log batch` reads one JSON array of arguments per line on stdin
and writes one JSON document per line (`error.v1` for a failed line, which
never stops the batch):

```sh
printf '%s\n' '["groups", "today", "--brief", "--limit", "5"]' '["show", "today", "83d1"]' \
  | atlas-log --store "$ATLAS_LOG_STORE" batch
```

## Conventions

- Set `ATLAS_LOG_STORE` (or pass `--store DIR`) to a directory outside any
  repository; the implicit `./.atlas-log` is refused inside a git work tree.
- Output is JSON whenever stdout is not a terminal; force it with
  `--output json` (one document) or `--output jsonl` (one row per line).
- Every document has `"schema": "<name>.v1"`; listings put rows in `"items"`.
- `groups` and `entries` page with `--limit N --offset M`: `"truncated": true`
  means more rows follow; fetch the next page with `--offset <offset + count>`.
- Keep rows small: `--brief` (a documented column preset) or
  `--columns a,b,c` on `groups`, `entries`, `show` and `context`.
- Level filters hide DEBUG stack traces; `--with-exception` (any level with an
  exception) and `--exception TEXT` (substring of `exception_type`) do not.
- `frames SET SIGNATURE|ENTRY_ID` reduces a stack trace to its application
  frames (JDK and Jira platform packages skipped; set `ATLAS_LOG_APP_PACKAGES=
  com.acme.,org.example.` to name your own packages instead).
- Errors: `{"schema": "error.v1", "error": {"code": ..., "message": ...}}` on
  stdout (`"reason"`, when present, is the same in the CLI and the web API, e.g.
  `set_not_found`); exit codes `0` ok, `1` usage, `2` nothing found / parse failure,
  `3` store error. Diagnostics go to stderr (`--quiet` silences them).
- Long entries are cut in JSON: `raw` and `body` keep 4000 characters
  (`--max-raw N`, `0` = all); cut rows carry `"body_truncated": true` and
  `"raw_chars"`. `--raw` (plain text) is never cut unless `--max-raw` is given.
- `atlas-log schema` describes the stored tables (`entries`, `groups`,
  `merges`, `logset`) field by field; `atlas-log schema NAME.v1` prints the JSON
  Schema of a document (`triage.v1`, `error.v1`, …; also
  `docs/formats/NAME.v1.schema.json`); `atlas-log COMMAND --help` documents the
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

`query --help` lists the columns of the `entries`, `groups` and `merges`
views; a misspelt column gets the candidate columns in `error.hint`.

## Cookbook

Five common questions and the commands that answer them. `before`/`after` are
set names given at `ingest --name`; `SIG` is a 16-hex group signature from
`groups` (or a unique prefix of 4+ characters), `ENTRY` an entry id from
`entries`/`show`, `REQ` a Jira request id.

**1. What broke after the deploy?**

```sh
atlas-log triage after --baseline before            # verdict, new types, new exceptions, growth
atlas-log diff before after --min-level WARN --rate --limit 20   # top 20 per section, changed by hourly rate
```

Without `--baseline`, triage picks one itself: another set of the same format
and recipe that is not a superset or subset of this one's files, not later,
not tiny and of comparable span (`baseline.reason` says why it was chosen,
`baseline.candidates` which sets were not and why). After a tool update that
changes signatures, `atlas-log sets` marks older sets `"stale": true`;
`atlas-log upgrade --all` rebuilds them (about 5 s per 700k entries).

Read `verdict`, then `new` and `new_exceptions` (stack traces below the level
filter). If `warnings` is not empty the two sets cover very different spans
and `new`/`vanished` are unreliable: compare sets of similar length.

**2. What is in this stack trace?**

```sh
atlas-log frames after SIG                          # the chain with application frames first
atlas-log frames after SIG --all                    # every frame, with an "app" flag
atlas-log show after SIG --raw --limit 1 --max-raw 0   # the full original text of one entry
```

**3. Which request or thread triggered it?**

```sh
atlas-log entries after --signature SIG --columns id,ts,thread,user,request_id --limit 5
atlas-log entries after --request-id REQ --brief    # everything that request logged
atlas-log context after ENTRY --before 20           # what the thread did just before
atlas-log context after ENTRY --any-thread --before 5 --after 5   # what else happened then
```

**4. Is it gone after the fix?**

```sh
atlas-log ingest logs-after-fix/ --name fixed
atlas-log triage fixed --baseline after             # SIG should be in "vanished"
atlas-log diff after fixed --rate --limit 0 | jq '[.vanished[], .changed[]] | map(select(.signature == "SIG"))'
atlas-log groups fixed --exception NullPointer --brief  # any group with that exception left?
```

Let the fixed set cover a comparable span (see `coverage` / `warnings`);
a group that only occurs at night is "vanished" from a daytime-only set.

**5. Which issues or users are affected?**

```sh
atlas-log show after SIG --limit 20 | jq '.items[] | {ts, user, variables}'
atlas-log query after "select unnest(variables) v, count(*) n from entries
                       where signature = 'SIG' group by 1 order by 2 desc"
atlas-log query after "select user, count(*) n from entries
                       where signature = 'SIG' group by 1 order by 2 desc"
atlas-log groups after --contains 'issue' --columns signature,count,top_users
```

`show` returns each entry's `variables` (the values masking replaced: issue
keys, numbers, ids); the first `query` counts them over the whole group, the
second counts users; `top_users` in `groups` is the top-10 list per group.

The store's Parquet files (layout in the [README](../README.md#how-it-works)) can also be
read directly with DuckDB, polars or pandas.
