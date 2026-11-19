# manifest.v1

How an agent uses ragnalog: `manifest`, bare `ragnalog` piped, `GET /api/manifest`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`manifest.v1.schema.json`](manifest.v1.schema.json), also `ragnalog schema manifest.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"manifest.v1"` | yes |  |
| `tool` | `string` | yes | Command name. |
| `version` | `string` | yes | Tool version. |
| `usage` | `string` | yes | Command line shape. |
| `notes` | `list<string>` | yes | Conventions every command follows. |
| `workflow` | `list<ManifestStep>` | yes | The usual order of commands. |
| `workflow[].run` | `string` | yes | Command line, e.g. triage SET. |
| `workflow[].purpose` | `string` | yes | What it is for. |
| `when` | `list<ManifestStep>` | yes | Commands for other situations. |
| `when[].run` | `string` | yes | Command line, e.g. triage SET. |
| `when[].purpose` | `string` | yes | What it is for. |
| `commands` | `map<string, ManifestCommand>` | yes | Every command by name. |
| `commands.*.summary` | `string` | no | First line of --help; omitted for workflow/when steps. |
| `commands.*.usage` | `string` | no | Arguments (UPPERCASE, ... = several) and [--option TYPE[=DEFAULT]]: TYPE str, int, float, path or choices a\|b\|c, * = repeatable; [--flag] takes no value. |
| `commands.*.output` | `string \| null` | no | Document printed (ragnalog schema NAME). |
| `commands.*.exit` | `list<integer>` | no | Possible exit codes (see exit_codes). |
| `help` | `map<string, string>` | yes | One-line help by parameter name, as most commands give it (ragnalog COMMAND --help has each command's own): names several commands share and the parameters of the workflow and when commands. |
| `global_options` | `map<string, string>` | yes | "TYPE[=DEFAULT]: help" of each option. |
| `exit_codes` | `map<string, string>` | yes | Meaning of each exit code. |
| `documents` | `ManifestDocuments` | yes | The output documents and their schemas. |
| `documents.get` | `string` | yes | How to get a schema. |
| `documents.names` | `list<string>` | yes | Document names. |
| `presets` | `map<string, list<string>>` | yes | Columns of the --brief presets. |
| `env` | `map<string, string>` | yes | Environment variables. |
