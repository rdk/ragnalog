# formats.v1

The supported log formats: `formats`, `GET /api/formats`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`formats.v1.schema.json`](formats.v1.schema.json), also `atlas-log schema formats.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"formats.v1"` | yes |  |
| `count` | `integer` | yes | Number of items. |
| `items` | `list<FormatInfo>` | yes | The formats (sorted by confidence with --sample). |
| `items[].name` | `string` | yes | Format name (--format). |
| `items[].description` | `string` | yes | What the format reads. |
| `items[].signature_spec` | `string` | no | CLI: default signature spec. |
| `items[].mask_rules` | `list<string>` | no | CLI: masking rule names. |
| `items[].confidence` | `number` | no | With --sample: 0..1. |
