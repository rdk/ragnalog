"""The sets index names set directories whose manifest cannot be read."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_index_lists_unreadable_sets(client: TestClient, store_dir: Path) -> None:
    bad = store_dir / "badbadbadbad0001"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")

    page = client.get("/")

    assert page.status_code == 200
    assert 'id="unreadable-sets"' in page.text
    assert "badbadbadbad0001" in page.text
    assert "badbadbadbad0001" not in client.get("/api/sets").text


def test_index_has_no_notice_when_all_sets_are_readable(client: TestClient) -> None:
    assert "unreadable-sets" not in client.get("/").text
