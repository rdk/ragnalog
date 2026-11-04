"""Web app settings: CLI flags win over environment variables, which win over defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_STORE", "Settings"]

DEFAULT_STORE = Path(".atlas-log")


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration of one web app instance.

    Attributes:
        store: Store directory (``ATLAS_LOG_STORE``).
        max_upload_mb: Upper bound of one ingest upload in MiB (``ATLAS_LOG_MAX_UPLOAD_MB``).
        token: When set, every ``/api`` request must send ``Authorization: Bearer <token>``
            or ``X-Atlas-Token: <token>`` (``ATLAS_LOG_TOKEN``); ``None`` = no auth.
        report_cache: Number of built report payloads kept in memory
            (``ATLAS_LOG_REPORT_CACHE``).
        sync_jobs: Run ingest jobs in the request thread (tests).

    """

    store: Path = DEFAULT_STORE
    max_upload_mb: int = 2048
    token: str | None = None
    report_cache: int = 8
    sync_jobs: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        store: Path | None = None,
        max_upload_mb: int | None = None,
        token: str | None = None,
    ) -> Settings:
        """Build settings from explicit values, then ``ATLAS_LOG_*`` variables, then defaults."""
        env = os.environ
        return cls(
            store=store or Path(env.get("ATLAS_LOG_STORE", "").strip() or DEFAULT_STORE),
            max_upload_mb=max_upload_mb or int(env.get("ATLAS_LOG_MAX_UPLOAD_MB") or 2048),
            token=token or env.get("ATLAS_LOG_TOKEN") or None,
            report_cache=int(env.get("ATLAS_LOG_REPORT_CACHE") or 8),
        )

    def to_env(self) -> dict[str, str]:
        """Return the ``ATLAS_LOG_*`` variables from which :meth:`from_env` rebuilds these settings.

        ``serve --reload`` runs the app in a child process that only sees the
        environment; without these the child would drop ``--token`` (no auth) and
        ``--max-upload-mb``. ``sync_jobs`` is a test switch and is not passed on.
        An empty ``ATLAS_LOG_TOKEN`` means no token.
        """
        return {
            "ATLAS_LOG_STORE": str(self.store),
            "ATLAS_LOG_MAX_UPLOAD_MB": str(self.max_upload_mb),
            "ATLAS_LOG_TOKEN": self.token or "",
            "ATLAS_LOG_REPORT_CACHE": str(self.report_cache),
        }
