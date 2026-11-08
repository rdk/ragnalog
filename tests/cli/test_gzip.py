"""Gzipped rotations (``x.log.1.gz``): read directly, the same source as the plain file."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

from .conftest import Invoke

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jira"


def _logs(root: Path, *, gz: bool) -> Path:
    """``atlassian-jira.log`` (mini) and ``.log.1`` (sample), the rotation gzipped or not."""
    root.mkdir(parents=True)
    shutil.copy(Path(__file__).parent / "data" / "mini.log", root / "atlassian-jira.log")
    older = (FIXTURES / "sample.log").read_bytes()
    if gz:
        with gzip.open(root / "atlassian-jira.log.1.gz", "wb") as f:
            f.write(older)
    else:
        (root / "atlassian-jira.log.1").write_bytes(older)
    return root


def test_a_gzipped_rotation_is_the_same_source(jcli: Invoke, tmp_path: Path) -> None:
    plain = jcli("ingest", _logs(tmp_path / "plain", gz=False), "--name", "plain").json()
    packed = jcli("ingest", _logs(tmp_path / "packed", gz=True), "--name", "packed")
    # the same content: the same set, so the second ingest adds nothing
    doc = packed.json()
    assert doc["id"] == plain["id"]
    assert doc["entry_count"] == plain["entry_count"] > 0
    sizes = {Path(s["path"]).name.removesuffix(".gz"): s["size"] for s in doc["sources"]}
    assert sizes == {Path(s["path"]).name: s["size"] for s in plain["sources"]}
    assert jcli("sets").json()["count"] == 1


def test_a_gzipped_rotation_parses_like_the_plain_file(jcli: Invoke, tmp_path: Path) -> None:
    plain = jcli("ingest", _logs(tmp_path / "plain", gz=False), "--name", "plain").json()
    jcli("remove", "plain", "--yes")
    packed = jcli("ingest", _logs(tmp_path / "packed", gz=True), "--name", "packed").json()
    a = jcli("groups", plain["id"], "--limit", "0", "--brief").json()["items"]
    assert [(g["signature"], g["count"]) for g in a] == [
        (g["signature"], g["count"])
        for g in jcli("groups", packed["id"], "--limit", "0", "--brief").json()["items"]
    ]


def test_a_damaged_gzip_is_named(jcli: Invoke, tmp_path: Path) -> None:
    logs = _logs(tmp_path / "logs", gz=True)
    whole = (logs / "atlassian-jira.log.1.gz").read_bytes()
    (logs / "atlassian-jira.log.1.gz").write_bytes(whole[: len(whole) // 2])
    res = jcli("ingest", logs, "--name", "cut")
    assert res.code == 2
    err = res.json()["error"]
    assert err["code"] == "not_in_format" and "atlassian-jira.log.1.gz" in err["message"]
    assert str(tmp_path) not in err["message"]
