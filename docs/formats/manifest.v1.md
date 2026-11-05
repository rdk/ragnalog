# manifest.v1

How an agent uses atlas-log: `manifest`, bare `atlas-log` piped, `/api/manifest`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`manifest.v1.schema.json`](manifest.v1.schema.json), also `atlas-log schema manifest.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"manifest.v1"` | yes |  |
| `tool` | `string` | yes | Command name. |
| `version` | `string` | yes | Tool version. |
| `usage` | `string` | yes | Command line shape. |
| `notes` | `list<string>` | yes | Conventions every command follows. |
| `workflow` | `list<ManifestStep>` | yes | The usual order of commands. |
| `workflow[].run` | `string` | yes | Command line, e.g. triage SET. |
| `workflow[].purpose` | `string` | yes | What it answers. |
| `workflow[].output` | `string \| null` | no | Document printed. |
| `when` | `list<ManifestStep>` | yes | Commands for other situations. |
| `when[].run` | `string` | yes | Command line, e.g. triage SET. |
| `when[].purpose` | `string` | yes | What it answers. |
| `when[].output` | `string \| null` | no | Document printed. |
| `commands` | `map<string, ManifestCommand>` | yes | Every command by name. |
| `commands.*.summary` | `string` | yes | First line of --help. |
| `commands.*.args` | `list<string>` | no | Positional arguments; help in common. |
| `commands.*.params` | `list<ManifestParam>` | no | Options. |
| `commands.*.output` | `string \| null` | no | Document printed. |
| `commands.*.exit` | `list<integer>` | no | Possible exit codes. |
| `common` | `map<string, string>` | yes | Help of arguments and options shared by commands. |
| `global_options` | `list<ManifestParam>` | yes | Options of every command. |
| `exit_codes` | `map<string, string>` | yes | Meaning of each exit code. |
| `documents` | `ManifestDocuments` | yes | The output documents and their schemas. |
| `documents.get` | `string` | yes | How to get a schema. |
| `documents.names` | `list<string>` | yes | Document names. |
| `presets` | `map<string, list<string>>` | yes | Columns of the --brief presets. |
| `env` | `map<string, string>` | yes | Environment variables. |
