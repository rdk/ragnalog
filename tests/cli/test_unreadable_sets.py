"""``sets`` warns on stderr about set directories whose manifest cannot be read."""

from __future__ import annotations

from pathlib import Path

from .conftest import MINI_LOG, Invoke


def test_sets_warns_about_a_corrupt_manifest(jcli: Invoke, store_dir: Path) -> None:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    bad = store_dir / "badbadbadbad0001"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")

    res = jcli("sets")

    assert res.code == 0
    doc = res.json()
    assert doc["count"] == 1 and [i["name"] for i in doc["items"]] == ["mini"]
    assert "badbadbadbad0001" in res.stderr
    assert str(store_dir / "badbadbadbad0001") in res.stderr
    assert res.stderr.count("warning:") == 1


def test_sets_is_quiet_without_unreadable_sets(jcli: Invoke) -> None:
    assert jcli("ingest", MINI_LOG).code == 0
    assert "warning" not in jcli("sets").stderr
