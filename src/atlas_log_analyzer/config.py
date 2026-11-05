"""Settings of the CLI and the web app: flags win over ``ATLAS_LOG_*`` variables, then defaults.

Standard library only: the CLI loads this module on every start. One
:class:`Settings` holds every option that can come from the environment;
:data:`ENV_VARS` lists the variables (``docs/cli.md`` carries the same table,
checked by ``tests/test_config.py``). A blank variable counts as unset.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple

__all__ = ["DEFAULT_STORE", "ENV_VARS", "EnvVar", "Settings", "parse_packages"]

DEFAULT_STORE = Path(".atlas-log")
"""Store used when neither ``--store`` nor ``ATLAS_LOG_STORE`` is given."""


class EnvVar(NamedTuple):
    """One environment variable read by :meth:`Settings.load`."""

    name: str
    field: str
    """The :class:`Settings` field it sets."""
    default: str
    """The default, as the docs show it."""
    help: str
    short: str
    """At most about 40 characters, for the agent manifest (``atlas-log manifest``)."""


ENV_VARS: Final[tuple[EnvVar, ...]] = (
    EnvVar(
        "ATLAS_LOG_STORE",
        "store",
        "`./.atlas-log`",
        "Store directory holding the log sets (`--store` wins).",
        "default of --store",
    ),
    EnvVar(
        "ATLAS_LOG_APP_PACKAGES",
        "app_packages",
        "none",
        "Comma-separated package prefixes of application code for `frames` and the "
        "highlighting of raw entries (`com.acme.,org.example.`); default: every frame "
        "outside the library packages.",
        "frames: application package prefixes",
    ),
    EnvVar(
        "ATLAS_LOG_DEBUG",
        "debug",
        "off",
        "Any value: an unexpected exception shows its traceback instead of `internal_error`.",
        "traceback of an internal_error",
    ),
    EnvVar(
        "ATLAS_LOG_TOKEN",
        "token",
        "none",
        "`serve`: API token (`--token` wins).",
        "default of serve --token",
    ),
    EnvVar(
        "ATLAS_LOG_MAX_UPLOAD_MB",
        "max_upload_mb",
        "2048",
        "`serve`: largest upload in MiB (`--max-upload-mb` wins).",
        "default of serve --max-upload-mb",
    ),
    EnvVar(
        "ATLAS_LOG_REPORT_CACHE",
        "report_cache",
        "8",
        "`serve`: report payloads kept in memory.",
        "serve: reports kept in memory",
    ),
)
"""Every ``ATLAS_LOG_*`` variable, in the order of the docs' table."""


def parse_packages(value: str | None) -> tuple[str, ...] | None:
    """Comma-separated package prefixes; ``None`` when missing or blank."""
    prefixes = tuple(p.strip() for p in (value or "").split(",") if p.strip())
    return prefixes or None


def _int(name: str, text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{name}={text!r} is not an integer") from None


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration of one CLI invocation or web app instance.

    Attributes:
        store: Store directory (``ATLAS_LOG_STORE``).
        token: When set, every ``/api`` request must send ``Authorization: Bearer <token>``
            or ``X-Atlas-Token: <token>`` (``ATLAS_LOG_TOKEN``); ``None`` = no auth.
        max_upload_mb: Upper bound of one ingest upload in MiB (``ATLAS_LOG_MAX_UPLOAD_MB``).
        report_cache: Number of built report payloads kept in memory
            (``ATLAS_LOG_REPORT_CACHE``).
        app_packages: Package prefixes of application code (``ATLAS_LOG_APP_PACKAGES``);
            ``None`` = frames outside the library packages.
        debug: Let unexpected exceptions propagate (``ATLAS_LOG_DEBUG``).
        sync_jobs: Run ingest jobs in the request thread (tests; not in the environment).
        implicit_store: Neither a flag nor ``ATLAS_LOG_STORE`` named the store (it is
            :data:`DEFAULT_STORE`); not compared.

    """

    store: Path = DEFAULT_STORE
    token: str | None = None
    max_upload_mb: int = 2048
    report_cache: int = 8
    app_packages: tuple[str, ...] | None = None
    debug: bool = False
    sync_jobs: bool = False
    implicit_store: bool = field(default=False, compare=False)

    @classmethod
    def load(
        cls, flags: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None
    ) -> Settings:
        """Build settings from ``flags``, then ``env`` (the ``ATLAS_LOG_*`` names), then defaults.

        ``flags`` maps field names to values; ``None`` (and an empty token) means not
        given. Blank variables are unset.

        Raises:
            ValueError: A number that does not parse, or one below 1.

        """
        given = {k: v for k, v in (flags or {}).items() if v is not None and v != ""}
        values: dict[str, Any] = {}
        for var in ENV_VARS:
            text = (env or {}).get(var.name, "").strip()
            if var.field in given or not text:
                continue
            match var.field:
                case "store":
                    values["store"] = Path(text)
                case "max_upload_mb" | "report_cache":
                    values[var.field] = _int(var.name, text)
                case "app_packages":
                    values["app_packages"] = parse_packages(text)
                case "debug":
                    values["debug"] = True
                case _:
                    values[var.field] = text
        values.update(given)
        if isinstance(values.get("app_packages"), str):
            values["app_packages"] = parse_packages(values["app_packages"])
        if "store" in values:
            values["store"] = Path(values["store"])
        values["implicit_store"] = "store" not in values
        for name in ("max_upload_mb", "report_cache"):
            if name in values and int(values[name]) < 1:
                raise ValueError(f"{name} must be >= 1")
        return cls(**values)

    @classmethod
    def from_env(
        cls,
        *,
        store: Path | None = None,
        max_upload_mb: int | None = None,
        token: str | None = None,
    ) -> Settings:
        """Build settings from explicit values, then ``os.environ``, then defaults."""
        flags = {"store": store, "max_upload_mb": max_upload_mb, "token": token}
        return cls.load(flags, os.environ)

    def to_env(self) -> dict[str, str]:
        """Return the ``ATLAS_LOG_*`` variables from which :meth:`load` rebuilds these settings.

        ``serve --reload`` runs the app in a child process that only sees the
        environment; without these the child would drop ``--token`` (no auth) and
        ``--max-upload-mb``. ``sync_jobs`` is a test switch and is not passed on.
        Unset values are passed as empty variables, which mean unset.
        """
        return {
            "ATLAS_LOG_STORE": str(self.store),
            "ATLAS_LOG_APP_PACKAGES": ",".join(self.app_packages or ()),
            "ATLAS_LOG_DEBUG": "1" if self.debug else "",
            "ATLAS_LOG_TOKEN": self.token or "",
            "ATLAS_LOG_MAX_UPLOAD_MB": str(self.max_upload_mb),
            "ATLAS_LOG_REPORT_CACHE": str(self.report_cache),
        }
