"""Comparison, metadata and writing documents of the service layer (no front end)."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from ragnalog.service import compare, meta
from ragnalog.service import ingest as service_ingest
from ragnalog.service.errors import OpError
from ragnalog.store.manifest import LogSetManifest
from ragnalog.store.pipeline import ingest_paths
from ragnalog.store.query import IncompatibleSets
from ragnalog.store.query import diff as store_diff
from ragnalog.store.schema import SCHEMA_NAMES
from ragnalog.store.store import Store

MINI = Path(__file__).resolve().parents[1] / "cli" / "data" / "mini.log"
SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "jira" / "sample.log"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    ingest_paths(store, [MINI], name="mini")
    ingest_paths(store, [SAMPLE], name="sample")
    return store


def _other_recipe(store: Store, ref: str, set_id: str) -> None:
    """Copy a set under ``set_id`` as if it were signed with another spec."""
    src = store.open(ref)
    target = store.root / set_id
    shutil.copytree(src.path, target)
    m = LogSetManifest.read(target)
    key = (m.recipe_key or "").replace("|L+G+E+F0|", "|L+G+E+F1|")
    dataclasses.replace(m, id=set_id, name="other", recipe_key=key).write(target)


def _error(call: object) -> OpError:
    with pytest.raises(OpError) as info:
        call()  # type: ignore[operator]
    return info.value


def test_store_diff_raises_typed_incompatible_sets(store: Store) -> None:
    _other_recipe(store, "mini", "cccc000000000001")
    with pytest.raises(IncompatibleSets):
        store_diff(store.open("mini"), store.open("other"))
    assert issubclass(IncompatibleSets, ValueError)


@pytest.mark.parametrize(
    ("front", "code", "hint"),
    [("cli", "invalid_value", "--force"), ("api", "incompatible_sets", "force=true")],
)
def test_diff_incompatible_per_front_end(store: Store, front: str, code: str, hint: str) -> None:
    _other_recipe(store, "mini", "cccc000000000001")
    err = _error(lambda: compare.diff(store, "mini", "other", front=front))  # type: ignore[arg-type]
    assert (err.code, err.reason) == (code, "incompatible_sets")
    assert err.hint is not None and hint in err.hint
    forced = compare.diff(store, "mini", "other", force=True, front="cli")
    assert forced["schema"] == "diff.v1"


def test_diff_limits_sections(store: Store) -> None:
    doc = compare.diff(store, "mini", "sample", limit=1, rate=True, front="api")
    assert all(len(doc[k]) <= 1 for k in compare.DIFF_SECTIONS)
    assert doc["truncated"] == any(doc["totals"][k] > 1 for k in compare.DIFF_SECTIONS)
    assert all("rate_ratio" in row for row in doc["changed"])


def test_triage_baselines(store: Store) -> None:
    none = compare.triage(store, "sample", baseline="none", front="cli")
    assert none["verdict"] == "no_baseline" and none["baseline"] is None
    blank = compare.triage(store, "sample", baseline=" ", front="api")
    assert blank["verdict"] == "no_baseline"
    given = compare.triage(store, "sample", baseline="mini", front="cli")
    assert given["baseline"]["id"] == store.open("mini").manifest.id
    err = _error(lambda: compare.triage(store, "sample", grow=1.0, front="api"))
    assert (err.code, err.message) == ("invalid_value", "grow must be > 1")


def test_meta_documents() -> None:
    assert meta.TABLES == SCHEMA_NAMES
    fmts = meta.formats()
    assert fmts["schema"] == "formats.v1" and fmts["count"] == len(fmts["items"])
    assert {"name", "description", "signature_spec", "mask_rules"} <= set(fmts["items"][0])
    sniffed = meta.formats(SAMPLE)
    assert sniffed["items"][0]["confidence"] > 0.5
    assert meta.version()["schema"] == "version.v1"
    assert set(meta.schema(None, front="cli")["definitions"]) == set(SCHEMA_NAMES)
    err = _error(lambda: meta.schema("nope", front="api"))
    assert err.code == "usage" and err.hint is None


def test_remove_and_report_file(store: Store, tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    doc = service_ingest.report_file(store, "sample", out=out, compare="mini", front="cli")
    assert doc["schema"] == "report-file.v1" and doc["compare"] and out.exists()
    assert doc["path"] == out.resolve().as_posix()
    removed = service_ingest.remove(store, "mini", front="api")
    assert removed == {"schema": "remove.v1", "removed": removed["removed"], "name": "mini"}
    assert _error(lambda: service_ingest.remove(store, "mini", front="api")).status == 404


def test_upgrade_needs_sets_or_all(store: Store) -> None:
    both = _error(lambda: service_ingest.upgrade(store, ["mini"], all_sets=True, front="cli"))
    assert both.message == "give SET arguments or --all, not both"
    assert _error(lambda: service_ingest.upgrade(store, [], front="api")).code == "invalid_value"
    doc = service_ingest.upgrade(store, [], all_sets=True, front="cli")
    assert doc["count"] == 2 and doc["upgraded"] == 0
    assert {i["reason"] for i in doc["items"]} == {"current"}


def test_ingest_document_and_frames_check(tmp_path: Path) -> None:
    store = Store(tmp_path / "s")
    selection = service_ingest.select([MINI], None, front="cli")
    doc = service_ingest.ingest(store, selection, name="m", front="cli")
    assert doc["schema"] == "logset.v1" and doc["ingest"]["parsed_entries"] == 10
    assert "merges" not in doc
    err = _error(lambda: service_ingest.ingest(store, selection, frames=-1, front="api"))
    assert err.message == "frames must be >= 0"
    assert _error(lambda: service_ingest.select([], None, front="cli")).code == "usage"
    again = service_ingest.ingest_paths(store, [MINI], name="m", front="api")
    assert again["ingest"]["existing"] is True


def test_report_filename() -> None:
    class Fake:
        class manifest:
            name = "prod 2026/10"
            id = "abc"

    assert service_ingest.report_filename(Fake()) == Path("prod_2026_10.html")  # type: ignore[arg-type]
