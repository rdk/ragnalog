from __future__ import annotations

import builtins
from typing import Any

import pytest

from ragnalog.core import mining


def test_propose_merges() -> None:
    pytest.importorskip("drain3")
    proposals = mining.propose_merges(
        [
            ("s1", "Error updating the repository 'Alpha' (repoId=<NUM>)"),
            ("s2", "Error updating the repository 'Beta' (repoId=<NUM>)"),
            ("s2", "Error updating the repository 'Beta' (repoId=<NUM>)"),
            ("s3", "No extracts found in the database"),
        ]
    )
    assert len(proposals) == 1
    (p,) = proposals
    assert p.signatures == ("s1", "s2")
    assert "<*>" in p.drain_template
    assert mining.propose_merges([]) == []


def test_missing_drain3(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("drain3"):
            raise ImportError("no drain3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"ragnalog\[mine\]"):
        mining.propose_merges([("a", "b")])
