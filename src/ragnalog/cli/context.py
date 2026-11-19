"""The context of one CLI invocation: its output and settings, the store, set references.

:func:`ragnalog.cli.app.run` sets it from the global options before a
command runs (``batch`` sets one per line); commands read it through
:func:`current`, :func:`out` and :func:`store`.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ragnalog.cli.output import OpError, Output, default_mode
from ragnalog.config import Settings

if TYPE_CHECKING:
    from ragnalog.store.logset import LogSet
    from ragnalog.store.store import Store

__all__ = ["STORE_ENV", "Context", "current", "open_logset", "out", "store", "use"]

STORE_ENV = "RAGNALOG_STORE"


@dataclasses.dataclass
class Context:
    """Output and settings of one invocation."""

    output: Output
    settings: Settings
    """``--store`` and the ``RAGNALOG_*`` variables (:mod:`ragnalog.config`)."""

    @property
    def store(self) -> Path:
        """Data directory."""
        return self.settings.store

    @property
    def implicit_store(self) -> bool:
        """True when neither --store nor RAGNALOG_STORE was given (``./.ragnalog``)."""
        return self.settings.implicit_store


_CURRENT: Context | None = None


def current() -> Context:
    """Return the context of the running command (defaults outside the CLI)."""
    if _CURRENT is None:
        return Context(Output(default_mode()), Settings.load(env=os.environ))
    return _CURRENT


def peek() -> Context | None:
    """Return the context set by :func:`use` or :func:`set_current`, if any."""
    return _CURRENT


def set_current(ctx: Context | None) -> None:
    """Make ``ctx`` the context of what follows (``None`` = defaults)."""
    global _CURRENT
    _CURRENT = ctx


@contextmanager
def use(ctx: Context) -> Iterator[Context]:
    """Run a block with ``ctx`` as the context, then restore the previous one."""
    previous = _CURRENT
    set_current(ctx)
    try:
        yield ctx
    finally:
        set_current(previous)


def out() -> Output:
    """Output of the running command."""
    return current().output


def _in_git_work_tree(path: Path) -> Path | None:
    """Return the first of ``path`` and its parents with a ``.git`` entry, or ``None``."""
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def store(*, create: bool = False) -> Store:
    """Return the store (data directory) of this invocation.

    With ``create`` (commands that may create the store directory) the implicit
    default ``./.ragnalog`` is refused inside a git work tree unless it exists
    already: a store there risks committing real logs.
    """
    ctx = current()
    if create and ctx.implicit_store and not ctx.store.exists():
        repo = _in_git_work_tree(Path.cwd().resolve())
        if repo is not None:
            raise OpError(
                "store_error",
                f"refusing to create the default data directory {ctx.store.as_posix()} inside "
                f"the git work tree {repo.as_posix()}: a data directory inside a repository "
                "risks committing real logs",
                hint=f"pass --store DIR outside the repository or set {STORE_ENV}",
            )
    if ctx.store.exists() and not ctx.store.is_dir():
        raise OpError(
            "store_error",
            f"the data directory {ctx.store.as_posix()} is a file, not a directory",
            hint=f"pass --store DIR (a directory) or set {STORE_ENV}",
        )
    from ragnalog.store.store import Store

    return Store(ctx.store)


def open_logset(ref: str) -> LogSet:
    """Open the set ``ref`` of this invocation's store (``OpError`` when unknown)."""
    from ragnalog.service.sets import open_set

    return open_set(store(), ref, front="cli")
