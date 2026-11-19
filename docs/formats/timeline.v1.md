# timeline.v1

Entry counts per time bucket: `GET /api/sets/{ref}/timeline`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`timeline.v1.schema.json`](timeline.v1.schema.json), also `ragnalog schema timeline.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"timeline.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `bucket` | `string` | yes | Bucket size, e.g. 1h. |
| `count` | `integer` | yes | Rows in items. |
| `items` | `list<BucketCount>` | yes | Contiguous buckets. |
| `items[].bucket` | `string` | yes | Bucket start, ISO 8601 UTC. |
| `items[].count` | `integer` | yes | Entries in the bucket. |
