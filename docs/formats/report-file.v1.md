# report-file.v1

A written HTML report: `report`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`report-file.v1.schema.json`](report-file.v1.schema.json), also `atlas-log schema report-file.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"report-file.v1"` | yes |  |
| `path` | `string` | yes | Absolute path of the HTML file. |
| `bytes` | `integer` | yes | File size. |
| `groups` | `integer` | yes | Groups in the report. |
| `entries` | `integer` | yes | Embedded example entries. |
| `compare` | `boolean` | yes | Compared with --compare. |
| `capture` | `string \| null` | no | report CAPTURE: the capture id (the cluster page). |
| `nodes` | `integer` | no | report CAPTURE: nodes on the page. |
