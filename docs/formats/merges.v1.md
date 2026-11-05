# merges.v1

Schema version 1.1. Generated from `atlas_log_analyzer.store.schema` by `docs/formats/generate.py`; machine-readable form: `atlas-log schema merges`.

Optional file `merges.parquet`, written by the `mine` step: Drain3 proposals to treat
several signatures as one. It never changes `groups.parquet`; apply it as a view.
Dropped by `regroup` because it refers to the old signatures.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `cluster_id` | `int64` | no | Drain3 cluster id. |
| `drain_template` | `string` | yes | Template proposed by Drain3 (<*> wildcards). |
| `signatures` | `list<string>` | yes | Signatures proposed to be merged. |

In JSON (`logset.v1` `merges` after `ingest --mine`, `query` over the `merges` view) a row is an object with these columns: `MergeRow` in the documents' JSON Schemas (`atlas-log schema logset.v1`).
