# triage.v1

New kinds of errors since the baseline? `triage`, `GET /api/sets/{ref}/triage`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`triage.v1.schema.json`](triage.v1.schema.json), also `atlas-log schema triage.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"triage.v1"` | yes |  |
| `set` | `TriageSet` | yes | The judged set. |
| `set.id` | `string` | yes | Set id. |
| `set.name` | `string \| null` | yes | Set name. |
| `set.first_ts` | `string \| null` | yes | First entry, as in the manifest. |
| `set.last_ts` | `string \| null` | yes | Last entry, as in the manifest. |
| `set.entries` | `integer` | yes | Entries of the set. |
| `baseline` | `TriageBaseline \| null` | yes | null: no baseline found (verdict no_baseline). |
| `baseline.id` | `string` | yes | Baseline id. |
| `baseline.name` | `string \| null` | yes | Baseline name. |
| `baseline.first_ts` | `string \| null` | yes | First entry, as in the manifest. |
| `baseline.last_ts` | `string \| null` | yes | Last entry, as in the manifest. |
| `baseline.resolved_by` | `"auto" \| "param"` | yes | auto = picked, param = given. |
| `baseline.reason` | `string` | no | Why this baseline was picked, in one line. |
| `baseline.entries` | `integer` | no | Entries of the baseline. |
| `baseline.shared_sources` | `integer` | no | Number of source files (by digest) shared with the set. |
| `baseline.candidates` | `list<BaselineCandidate>` | no | Up to 5 sets rejected as the automatic baseline, newest first; [] when given. |
| `baseline.candidates[].id` | `string` | yes | Set id. |
| `baseline.candidates[].name` | `string \| null` | yes | Set name. |
| `baseline.candidates[].reason` | `"later" \| "recipe" \| "span" \| "stale" \| "subset" \| "superset" \| "tiny"` | yes | stale/recipe: another recipe_key; superset/subset: its sources contain or are part of the set's; later: ends after the set; tiny: under 1% of the entries; span: span more than 16x off. |
| `baseline.candidates[].detail` | `string` | yes | The reason in one sentence. |
| `baseline.candidates[].hint` | `string` | no | A command that would make it usable. |
| `min_level` | `string \| null` | yes | Echo of --min-level. |
| `verdict` | `"clean" \| "new_errors" \| "new_exceptions" \| "new_warnings" \| "no_baseline"` | yes | new_errors > new_warnings > new_exceptions > clean; no_baseline. |
| `summary` | `TriageSummary \| null` | yes | null without a baseline. |
| `summary.new` | `integer` | yes | Rows of new. |
| `summary.new_by_level` | `map<string, integer>` | yes | New groups per level name. |
| `summary.new_exceptions` | `integer` | yes | Rows of new_exceptions. |
| `summary.grew` | `integer` | yes | Rows of grew. |
| `summary.vanished` | `integer` | yes | Rows of vanished. |
| `summary.changed` | `integer` | yes | Groups in both sets with another count. |
| `summary.unchanged` | `integer` | yes | Groups in both sets with the same count. |
| `coverage` | `Coverage` | yes | Spans of both sets in hours. |
| `coverage.hours_baseline` | `number \| null` | yes | Baseline span; null without a baseline. |
| `coverage.hours_set` | `number` | yes | Set span. |
| `coverage.ratio` | `number \| null` | yes | hours_set / hours_baseline. |
| `warnings` | `list<string>` | yes | Rejected baselines when there is none; a stale, contained or tiny given baseline; spans that differ by more than 4x. |
| `new` | `list<DiffRow>` | yes | New groups at/above min_level, most severe first. |
| `new_exceptions` | `list<DiffRow>` | yes | New lower-level groups with an exception. |
| `grew` | `list<DiffRow>` | yes | Hourly rate rose at least --grow times. |
| `vanished` | `list<DiffRow>` | yes | Groups at/above min_level only in the baseline. |
| `report_url` | `string` | no | API: the set's report page. |

### `DiffRow`

One group compared between baseline A and set B (counts are 0 on the missing side). Every column is optional.

| Column | Type | Description |
|---|---|---|
| `signature` | `string` | Primary signature (16 hex chars). |
| `template` | `string \| null` | Masked message template. |
| `level` | `integer` | Numeric level (see entries.level). |
| `level_name` | `string` | Level name. |
| `logger` | `string \| null` | Logger name. |
| `exception_type` | `string \| null` | Outermost exception type, if any. |
| `count_a` | `integer` | Entries in A. |
| `count_b` | `integer` | Entries in B. |
| `first_seen_a` | `string (date-time) \| null` | First entry in A (UTC). |
| `last_seen_a` | `string (date-time) \| null` | Last entry in A (UTC). |
| `first_seen_b` | `string (date-time) \| null` | First entry in B (UTC). |
| `last_seen_b` | `string (date-time) \| null` | Last entry in B (UTC). |
| `ratio` | `number` | changed: count_b / count_a. |
| `rate_a` | `number \| null` | --rate, triage grew: count_a per hour. |
| `rate_b` | `number \| null` | --rate, triage grew: count_b per hour. |
| `rate_ratio` | `number \| null` | rate_b / rate_a. |
| `rate_per_hour` | `number \| null` | triage new: count_b per hour. |
| `report_url` | `string` | API triage: report page of the group. |
