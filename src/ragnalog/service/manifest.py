"""The agent manifest documents, read from the generated ``manifest.json``.

``manifest.json`` (next to this module) holds ``manifest.v1`` and one
``manifest-command.v1`` per command. It is generated from the cyclopts app by
:func:`ragnalog.cli.manifest.package_data` (``uv run python
docs/formats/generate.py``), and ``tests/test_docs.py`` fails when it is out of
date. Reading the file keeps the web app independent of the ``cli`` package and
spares the CLI importing every command module to describe them. ``version`` is
stored empty and filled in from the running package.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any, Final

from ragnalog import __version__
from ragnalog.service.errors import OpError

__all__ = ["MANIFEST_FILE", "command_document", "command_names", "manifest_document"]

MANIFEST_FILE: Final = "manifest.json"
"""Generated file in the ``ragnalog.service`` package."""


@cache
def _data() -> dict[str, Any]:
    text = files("ragnalog.service").joinpath(MANIFEST_FILE).read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def _versioned(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["version"] = __version__
    return out


def manifest_document() -> dict[str, Any]:
    """``manifest.v1``: workflow, commands, documents, presets and environment."""
    return _versioned(_data()["manifest"])


def command_names() -> list[str]:
    """Names of the commands the manifest describes, in ``--help`` order."""
    return list(_data()["commands"])


def command_document(name: str) -> dict[str, Any]:
    """``manifest-command.v1``: one command with every parameter and its full help.

    Raises:
        OpError: ``not_found`` for an unknown command.

    """
    commands = _data()["commands"]
    if name not in commands:
        raise OpError(
            "not_found",
            f"no command {name!r}",
            hint="commands: " + ", ".join(commands),
        )
    return _versioned(commands[name])
