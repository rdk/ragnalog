"""``atlas-log upgrade`` and the ``stale`` flag of ``sets`` (CLI and API alike)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke

OLD_KEY = "jira@1|L+G+E+F0|sig1|mask0"


def _age(store_dir: Path, set_id: str) -> None:
    """Make a set look as if built with an older masking-rule version."""
    path = store_dir / set_id / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(recipe_key=OLD_KEY, mask_rules_version=0)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def mini(jcli: Invoke) -> str:
    res = jcli("ingest", MINI_LOG, "--name", "mini")
    assert res.code == 0, res.stdout
    set_id: str = res.json()["id"]
    return set_id


def test_sets_flags_stale_sets(jcli: Invoke, store_dir: Path, mini: str) -> None:
    item = jcli("sets").json()["items"][0]
    assert item["stale"] is False and item["recipe_key"] == "jira@2|L+G+E+F0|sig1|mask1"
    _age(store_dir, mini)
    assert jcli("sets").json()["items"][0]["stale"] is True
    brief = jcli("sets", "--brief").json()["items"][0]
    assert brief["stale"] is True and "recipe_key" not in brief


def test_sets_stale_matches_the_api(jcli: Invoke, store_dir: Path, mini: str) -> None:
    from fastapi.testclient import TestClient

    from atlas_log_analyzer.webapp.app import create_app
    from atlas_log_analyzer.webapp.settings import Settings

    _age(store_dir, mini)
    with TestClient(create_app(Settings(store=store_dir))) as client:
        for brief in (False, True):
            api = client.get("/api/sets", params={"brief": brief}).json()["items"]
            cli = jcli("sets", *(["--brief"] if brief else [])).json()["items"]
            assert api == cli and api[0]["stale"] is True


def test_upgrade_rebuilds_stale_sets(jcli: Invoke, store_dir: Path, mini: str) -> None:
    _age(store_dir, mini)
    res = jcli("upgrade", "mini")
    assert res.code == 0, res.stdout
    doc = res.json()
    assert doc["schema"] == "upgrade.v1" and doc["count"] == 1 and doc["upgraded"] == 1
    item = doc["items"][0]
    assert item["id"] == mini and item["status"] == "upgraded" and item["reason"] is None
    assert item["previous_recipe_key"] == OLD_KEY
    assert item["recipe_key"] == "jira@2|L+G+E+F0|sig1|mask1" and item["mask_rules_version"] == 1
    assert jcli("sets").json()["items"][0]["stale"] is False
    again = jcli("upgrade", "--all").json()
    assert again["upgraded"] == 0 and again["items"][0]["reason"] == "current"
    assert again["items"][0]["status"] == "skipped"


def test_upgrade_keeps_the_spec(jcli: Invoke, store_dir: Path, mini: str) -> None:
    assert jcli("regroup", "mini", "--frames", "2").code == 0
    path = store_dir / mini / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["recipe_key"] = "jira@1|L+G+E+F2|sig0|mask1"
    path.write_text(json.dumps(data), encoding="utf-8")
    item = jcli("upgrade", mini[:6]).json()["items"][0]
    assert item["status"] == "upgraded" and item["signature_spec"] == "L+G+E+F2"
    assert item["recipe_key"] == "jira@2|L+G+E+F2|sig1|mask1"


def test_upgrade_usage_errors(jcli: Invoke, mini: str) -> None:
    res = jcli("upgrade")
    assert res.code == 1 and res.json()["error"]["code"] == "invalid_value"
    assert jcli("upgrade", "mini", "--all").code == 1
    assert jcli("upgrade", "nope").code == 3


def test_upgrade_table(cli: Invoke, store_dir: Path, mini: str) -> None:
    _age(store_dir, mini)
    res = cli("--output", "table", "--store", store_dir, "upgrade", "--all")
    assert res.code == 0 and "upgraded" in res.stdout
