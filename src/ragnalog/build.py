"""The build of this installation: its number, git commit and time.

A container image records them when it is built: the Dockerfile writes two build
arguments (the number and the hash; ``docs/install.md``) and the build time into the
file that ``RAGNALOG_BUILD_FILE`` names. A source checkout reads its git (the number
and hash of HEAD; no build time). An installed wheel knows none of them.

The build number is the commit count of the built commit (``git rev-list --count``):
it grows along main and is the same wherever that commit is built. ``version.v1`` has
the build, and the web app's pages show it next to the version.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

__all__ = ["Build", "build"]

_HEX = re.compile(r"[0-9a-f]{7,40}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@dataclass(frozen=True, slots=True)
class Build:
    """Number, git commit and time of a build (``None`` where unknown)."""

    number: int | None = None
    """Commit count of the built commit."""
    git: str | None = None
    """The commit's hash, its first 7 characters."""
    time: str | None = None
    """When the image was built, ISO 8601 UTC (``2026-11-24T19:58:12Z``)."""
    source: Literal["image", "checkout", "unknown"] = "unknown"
    """Where the facts come from: a built image, a git checkout, or nowhere."""

    def doc(self) -> dict[str, Any]:
        """Return ``version.v1``'s ``build`` object."""
        return {"number": self.number, "git": self.git, "time": self.time, "source": self.source}

    def label(self) -> str:
        """Return the short form shown next to the version (``build 412``; empty: unknown)."""
        if self.number is not None:
            return f"build {self.number}"
        return f"build {self.git}" if self.git else ""

    def title(self) -> str:
        """Return the details shown on hover: build time and git hash."""
        parts = []
        if self.time:
            parts.append(f"built {self.time[:10]} {self.time[11:19]} UTC")
        elif self.source == "checkout":
            parts.append("a source checkout")
        if self.git:
            parts.append(f"git {self.git}")
        return " · ".join(parts)


def _from_file(path: str) -> Build | None:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    number, git, time = (str(data.get(k) or "").strip() for k in ("number", "git", "time"))
    return Build(
        number=int(number) if number.isdigit() else None,
        git=git[:7].lower() if _HEX.fullmatch(git.lower()) else None,
        time=time if _TIME.fullmatch(time) else None,
        source="image",
    )


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5, check=True
    )
    return out.stdout.strip()


def _from_checkout(root: Path) -> Build | None:
    if not (root / ".git").exists():
        return None
    try:
        count, git = _git(root, "rev-list", "--count", "HEAD"), _git(root, "rev-parse", "HEAD")
    except OSError, subprocess.SubprocessError:
        return None
    if not count.isdigit() or not _HEX.fullmatch(git):
        return None
    return Build(number=int(count), git=git[:7], source="checkout")


@cache
def build() -> Build:
    """Return this installation's build (read once per process)."""
    path = os.environ.get("RAGNALOG_BUILD_FILE", "").strip()
    found = _from_file(path) if path else None
    # src/ragnalog/build.py: the work tree is two levels above the package
    return found or _from_checkout(Path(__file__).resolve().parents[2]) or Build()
