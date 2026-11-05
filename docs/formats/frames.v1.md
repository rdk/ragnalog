# frames.v1

An entry's exception chain, application frames first: `frames`.

Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; JSON Schema: [`frames.v1.schema.json`](frames.v1.schema.json), also `atlas-log schema frames.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"frames.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `entry` | `string` | yes | Entry id. |
| `signature` | `string` | yes | The entry's group. |
| `ts` | `string \| null` | yes | Entry time, UTC. |
| `level_name` | `string` | yes | Entry level. |
| `logger` | `string \| null` | yes | Entry logger. |
| `message` | `string \| null` | yes | First message line. |
| `app_packages` | `list<string> \| null` | yes | Echo of ATLAS_LOG_APP_PACKAGES. |
| `count` | `integer` | yes | Exceptions in the chain. |
| `items` | `list<ChainException>` | yes | The chain, outermost first. |
| `items[].type` | `string` | yes | Exception class. |
| `items[].message` | `string \| null` | yes | Exception message. |
| `items[].app_frames` | `list<Frame>` | yes | Application frames, at most --limit. |
| `items[].app_frames[].location` | `string` | yes | Class and method, e.g. com.acme.Foo.bar. |
| `items[].app_frames[].file` | `string \| null` | yes | Source file, or Native Method / Unknown Source. |
| `items[].app_frames[].line` | `integer \| null` | yes | Line number. |
| `items[].app_frames[].app` | `boolean` | no | Application frame (only with --all). |
| `items[].app_frames_total` | `integer` | yes | All application frames. |
| `items[].frames_total` | `integer` | yes | Frames printed in the log. |
| `items[].omitted_frames` | `integer` | yes | The "... N more" count. |
| `items[].frames` | `list<Frame>` | no | Every frame with its app flag (--all). |
| `items[].frames[].location` | `string` | yes | Class and method, e.g. com.acme.Foo.bar. |
| `items[].frames[].file` | `string \| null` | yes | Source file, or Native Method / Unknown Source. |
| `items[].frames[].line` | `integer \| null` | yes | Line number. |
| `items[].frames[].app` | `boolean` | no | Application frame (only with --all). |
