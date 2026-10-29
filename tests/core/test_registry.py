from __future__ import annotations

from collections.abc import Sequence

import pytest

from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats import registry
from atlas_log_analyzer.core.formats.jira import JiraFormat

from .conftest import SAMPLE


class _Dummy(JiraFormat):
    name = "dummy"

    def sniff(self, lines: Sequence[str]) -> float:
        return 0.0


def test_builtin() -> None:
    assert "jira" in formats.names()
    assert isinstance(formats.get("jira"), JiraFormat)


def test_unknown() -> None:
    with pytest.raises(registry.UnknownFormatError, match="nope"):
        formats.get("nope")
    assert issubclass(registry.UnknownFormatError, LookupError)


def test_register_and_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_formats", dict(registry._formats))
    formats.register(_Dummy())
    assert "dummy" in formats.names()
    assert formats.names() == sorted(formats.names())
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()[:50]
    detected = formats.detect(lines)
    assert detected is not None and detected.name == "jira"
    assert formats.detect(["plain text", "nothing to see"]) is None


def test_broken_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EP:
        name = "broken"
        value = "nowhere:Nothing"

        def load(self) -> object:
            raise ImportError("boom")

    monkeypatch.setattr(registry, "_formats", {})
    monkeypatch.setattr(registry, "_loaded", False)
    monkeypatch.setattr(registry.importlib.metadata, "entry_points", lambda group: [_EP()])
    with pytest.warns(RuntimeWarning, match="broken"):
        names = formats.names()
    assert names == ["jira"]
