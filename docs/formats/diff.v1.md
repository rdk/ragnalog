# diff.v1

Groups of B compared with baseline A: `diff`, `GET /api/diff`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`diff.v1.schema.json`](diff.v1.schema.json), also `atlas-log schema diff.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"diff.v1"` | yes |  |
| `a` | `string` | yes | Baseline set id. |
| `b` | `string` | yes | Compared set id. |
| `new` | `list<DiffRow>` | yes | Only in B, by count_b. |
| `vanished` | `list<DiffRow>` | yes | Only in A, by count_a. |
| `changed` | `list<DiffRow>` | yes | In both with another count. |
| `unchanged_count` | `integer` | yes | Groups in both with the same count. |
| `totals` | `DiffTotals` | yes | Section sizes before --limit. |
| `totals.new` | `integer` | yes | Groups only in B. |
| `totals.vanished` | `integer` | yes | Groups only in A. |
| `totals.changed` | `integer` | yes | Groups in both with another count. |
| `truncated` | `boolean` | yes | A section was cut by --limit. |
| `warnings` | `list<string>` | no | Spans differ by more than 4x. |

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
| `report_url` | `string` | API triage: report page of the group (vanished: the baseline's report). |
