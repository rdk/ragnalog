# manifest-command.v1

One command in full: `manifest COMMAND`, `GET /api/manifest/{command}`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`manifest-command.v1.schema.json`](manifest-command.v1.schema.json), also `ragnalog schema manifest-command.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"manifest-command.v1"` | yes |  |
| `command` | `string` | yes | Command name. |
| `version` | `string` | yes | Tool version. |
| `usage` | `string` | yes | As in manifest.v1 commands. |
| `description` | `string` | yes | The command's --help text without the parameter list (output, exit codes). |
| `output` | `string \| null` | no | Document printed (ragnalog schema NAME). |
| `exit` | `list<integer>` | no | Possible exit codes (see manifest.v1 exit_codes). |
| `params` | `list<ManifestParam>` | no | Arguments, then options, in --help order. |
| `params[].name` | `string` | yes | LOGSET (argument, ... = several) or --option. |
| `params[].type` | `string` | yes | str, int, float, path, flag or choices a\|b\|c; * = repeatable. |
| `params[].default` | `string \| null` | no | Default value, if any. |
| `params[].help` | `string` | no | Its --help text. |
