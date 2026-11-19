# health.v1

Liveness of the web app: `GET /api/health`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`health.v1.schema.json`](health.v1.schema.json), also `ragnalog schema health.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"health.v1"` | yes |  |
| `status` | `"ok"` | yes | Always ok. |
| `sets` | `integer` | yes | Sets in the data directory. |
