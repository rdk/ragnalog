# users.v1

Entries per user: `users`, `GET /api/sets/{ref}/users`.

Generated from `ragnalog.service.documents` by `docs/formats/generate.py`; JSON Schema: [`users.v1.schema.json`](users.v1.schema.json), also `ragnalog schema users.v1`. A key with Required = no appears only in some modes (see its description); readers ignore keys they do not know.

| Key | Type | Required | Description |
|---|---|---|---|
| `schema` | `"users.v1"` | yes |  |
| `logset` | `string` | yes | Set id. |
| `count` | `integer` | yes | Rows in items. |
| `total` | `integer` | yes | Users with matching entries. |
| `offset` | `integer` | yes | Index of the first row. |
| `next_offset` | `integer \| null` | yes | offset of the next page; null on the last. |
| `truncated` | `boolean` | yes | Whether more users follow. |
| `entries` | `integer` | yes | Matching entries with a user. |
| `without_user` | `integer` | yes | Matching entries without one (blank or missing). |
| `items` | `list<UserRow>` | yes | Users, most entries first, ties by name. |
| `items[].user` | `string` | yes | User name, as logged. |
| `items[].entries` | `integer` | yes | Matching entries of the user. |
| `items[].errors` | `integer` | yes | Of them ERROR or FATAL. |
| `items[].warnings` | `integer` | yes | Of them WARN. |
| `items[].groups` | `integer` | yes | Distinct signatures. |
| `items[].threads` | `integer` | yes | Distinct threads. |
| `items[].first_ts` | `string \| null` | yes | First entry with a timestamp. |
| `items[].last_ts` | `string \| null` | yes | Last entry with a timestamp. |
| `items[].top` | `list<UserGroup>` | yes | The user's largest groups (--top, default 3), most entries first. |
| `items[].top[].signature` | `string` | yes | Group signature. |
| `items[].top[].count` | `integer` | yes | The user's entries in the group. |
