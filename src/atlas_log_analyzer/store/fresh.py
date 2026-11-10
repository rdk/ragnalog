"""Keeping the directory of a long write fresh for the store's clean-up."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Self

__all__ = ["FRESH_INTERVAL_S", "Heartbeat", "keep_fresh"]

FRESH_INTERVAL_S = 300.0
"""Seconds between touches; far below the age at which ``Store`` sweeps a directory."""


class Heartbeat:
    """Touches ``path`` every ``interval`` seconds (default :data:`FRESH_INTERVAL_S`).

    Writing files inside a directory does not change the directory's own mtime, which
    is what ``Store``'s sweep of ``.tmp-*`` and ``.spool/<id>`` goes by; a write that
    outlives the sweep age would otherwise be deleted by another process.
    """

    def __init__(self, path: Path, interval: float | None = None) -> None:
        self.path = path
        self.interval = FRESH_INTERVAL_S if interval is None else interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="atlas-log-fresh", daemon=True)

    def _beat(self) -> None:
        while not self._stop.wait(self.interval):
            with suppress(OSError):
                os.utime(self.path)

    def start(self) -> Self:
        """Start touching; returns ``self``."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop touching."""
        self._stop.set()
        self._thread.join(timeout=2)


@contextmanager
def keep_fresh(path: Path, interval: float | None = None) -> Iterator[None]:
    """Run a :class:`Heartbeat` on ``path`` for the duration of the block."""
    beat = Heartbeat(path, interval).start()
    try:
        yield
    finally:
        beat.stop()
