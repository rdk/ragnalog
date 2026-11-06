"""Request models: the user's raw input, shared by the CLI and the web API.

Both front ends bind the same dataclasses: the CLI flattens a
:class:`GroupQuery`/:class:`EntryQuery` into options
(``Annotated[EntryQuery, Parameter(name="*")]``), the web API reads the same fields
from the query string (``webapp.params.query_model``). The field docs below are the
``--help`` of those options and the OpenAPI descriptions of the parameters.

Values stay raw strings until :meth:`EntryQuery.resolve` turns them into a
:class:`~atlas_log_analyzer.store.filters.EntryFilter`: levels through
``store.levels``, times through :func:`parse_time` (relative times count back from
the set's last entry). A blank value means unset in both front ends. Errors are
:class:`~atlas_log_analyzer.service.errors.OpError` naming the parameter as the
front end spells it (``--max-raw`` or ``max_raw``).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from atlas_log_analyzer.service.errors import Front, OpError, flag
from atlas_log_analyzer.store.filters import EntryFilter, GroupFilter
from atlas_log_analyzer.store.levels import InvalidLevel, parse_levels

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import LogSetManifest

__all__ = [
    "DEFAULT_MAX_RAW",
    "SPLIT_MODES",
    "EntryQuery",
    "GroupQuery",
    "IngestTarget",
    "Page",
    "Select",
    "SplitMode",
    "TextCap",
    "blank",
    "check_label",
    "parse_columns",
    "parse_level",
    "parse_time",
]

DEFAULT_MAX_RAW = 4000
"""Characters of ``raw`` and ``body`` kept per entry in JSON documents by default."""

_RELATIVE = re.compile(r"^(\d+)\s*([smhd])$")
type SplitMode = Literal["auto", "prefix", "dir", "none"]
"""``--split``: detect the nodes (``auto``), force a kind, or keep one set (``none``)."""
SPLIT_MODES: tuple[SplitMode, ...] = ("auto", "prefix", "dir", "none")
_LABEL = re.compile(r"^\w[\w.-]{0,127}$")
"""A node id, cluster name or capture id: letters, digits, ``_``, ``.``, ``-`` (at most 128)."""
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def blank(value: str | None) -> str | None:
    """Return ``None`` for a missing or blank value, else the value unchanged."""
    return value if value is not None and value.strip() else None


def parse_time(
    value: str | None, name: str, anchor: datetime | None, *, front: Front
) -> datetime | None:
    """Parse a time parameter; ``None`` when missing or blank.

    Forms: ISO 8601 (naive = UTC), ``YYYY-MM-DD`` (midnight UTC), or ``Ns``/``Nm``/
    ``Nh``/``Nd`` before ``anchor`` (the set's last entry; now when it has none).

    Raises:
        OpError: ``invalid_value``.

    """
    text = blank(value)
    if text is None:
        return None
    text = text.strip()
    if m := _RELATIVE.match(text.lower()):
        base = anchor if anchor is not None else datetime.now(UTC)
        base = base if base.tzinfo is not None else base.replace(tzinfo=UTC)
        try:
            return base - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
        except OverflowError:
            raise OpError(
                "invalid_value", f"{flag(name, front)} {value!r} reaches before year 1"
            ) from None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise OpError(
            "invalid_value",
            f"{flag(name, front)} {value!r} is not a time; use ISO 8601 (2026-10-29T09:00, "
            "naive = UTC), a date (2026-10-29) or a duration before the last entry of the set "
            "(30m, 2h, 1d)",
        ) from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_level(value: str | None, name: str, *, front: Front) -> str | None:
    """Normalise a level parameter (``store.levels``); ``None`` when missing or blank.

    Raises:
        OpError: ``invalid_level``.

    """
    try:
        return parse_levels(value)
    except InvalidLevel as exc:
        raise OpError("invalid_level", f"{flag(name, front)}: {exc}") from None


def parse_columns(
    values: Sequence[str] | None, valid: Sequence[str], *, front: Front
) -> list[str] | None:
    """Parse column names (comma-separated and/or repeated); ``None`` when none are given.

    Duplicates are dropped and the order is kept.

    Raises:
        OpError: ``invalid_value`` naming each unknown column (with "did you mean"),
            the valid columns in the hint; also when values were given but name no
            column (``--columns ,``).

    """
    chosen: list[str] = []
    unknown: list[str] = []
    for value in values or ():
        for part in value.split(","):
            name = part.strip()
            if not name:
                continue
            if name not in valid:
                unknown.append(name)
            elif name not in chosen:
                chosen.append(name)
    if unknown:
        names = []
        for name in unknown:
            close = difflib.get_close_matches(name, valid, n=1)
            names.append(f"{name!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
        raise OpError(
            "invalid_value",
            f"unknown column{'s' if len(unknown) > 1 else ''} {', '.join(names)} "
            f"in {flag('columns', front)}",
            hint=f"valid columns: {', '.join(valid)}",
        )
    if values and not chosen:
        raise OpError(
            "invalid_value",
            f"{flag('columns', front)} is empty",
            hint=f"valid columns: {', '.join(valid)}",
        )
    return chosen or None


@dataclass(frozen=True, kw_only=True)
class GroupQuery:
    """Group filters as the user typed them (``groups``; ``GET /api/sets/{ref}/groups``).

    Attributes:
        level: Only these levels, e.g. ERROR or WARN,ERROR (TRACE DEBUG INFO WARN ERROR FATAL;
            UNKNOWN = entries whose header could not be parsed).
        min_level: Only this level or more severe, e.g. WARN.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        since: Only groups with an entry at/after this time (last_seen >= since): ISO 8601
            (naive = UTC), YYYY-MM-DD, or 30m/2h/1d before the set's last entry.
        until: Only groups first seen before this time (same forms as --since).
        with_exception: Only groups with an exception (exception_type not null), any level.
        exception: Only groups whose exception_type contains this text (case-insensitive).
        contains: Only groups whose template contains this text (case-insensitive).

    """

    level: str | None = None
    min_level: str | None = None
    logger: str | None = None
    since: str | None = None
    until: str | None = None
    with_exception: bool = False
    exception: str | None = None
    contains: str | None = None

    def check(self, *, front: Front) -> None:
        """Validate what can be checked before the set is opened (levels, absolute times).

        Raises:
            OpError: ``invalid_level`` or ``invalid_value``.

        """
        _check(self.level, self.min_level, self.since, self.until, front)

    def resolve(self, manifest: LogSetManifest, *, front: Front) -> GroupFilter:
        """Return the filter for the set of ``manifest`` (relative times count back from its end).

        Raises:
            OpError: ``invalid_level`` or ``invalid_value``.

        """
        last = manifest.last_ts
        return GroupFilter(
            level=parse_level(self.level, "level", front=front),
            min_level=parse_level(self.min_level, "min_level", front=front),
            logger=blank(self.logger),
            since=parse_time(self.since, "since", last, front=front),
            until=parse_time(self.until, "until", last, front=front),
            with_exception=self.with_exception,
            exception=blank(self.exception),
            contains=blank(self.contains),
        )


@dataclass(frozen=True, kw_only=True)
class EntryQuery:
    """Entry filters as the user typed them (``entries``; ``GET /api/sets/{ref}/entries``).

    Attributes:
        signature: Only entries of this group signature (or unique prefix, 4+ hex chars).
        coarse_signature: Only entries of this coarse signature (exact; the signature of
            groups --coarse).
        level: Only these levels, e.g. ERROR or WARN,ERROR.
        min_level: Only this level or more severe.
        logger: Logger name or package prefix (c.a.jira matches c.a.jira.Foo).
        user: Only this user (exact).
        thread: Only this thread (exact).
        request_id: Only this request id (exact; the Jira request context, e.g. 540x101x1).
        since: Only entries at/after this time: ISO 8601 (naive = UTC), YYYY-MM-DD, or
            30m/2h/1d before the set's last entry.
        until: Only entries before this time (same forms as --since).
        contains: Only entries whose raw text contains this text (case-insensitive).
        with_exception: Only entries with an exception (exception_type not null), any level.
        exception: Only entries whose exception_type contains this text (case-insensitive).

    """

    signature: str | None = None
    coarse_signature: str | None = None
    level: str | None = None
    min_level: str | None = None
    logger: str | None = None
    user: str | None = None
    thread: str | None = None
    request_id: str | None = None
    since: str | None = None
    until: str | None = None
    contains: str | None = None
    with_exception: bool = False
    exception: str | None = None

    def check(self, *, front: Front) -> None:
        """Validate what can be checked before the set is opened (levels, absolute times).

        Raises:
            OpError: ``invalid_level`` or ``invalid_value``.

        """
        _check(self.level, self.min_level, self.since, self.until, front)

    def resolve(self, manifest: LogSetManifest, *, front: Front) -> EntryFilter:
        """Return the filter for the set of ``manifest`` (relative times count back from its end).

        ``signature`` is passed on as given: resolving a prefix needs the set's groups
        (:func:`~atlas_log_analyzer.service.views.resolve_signature`).

        Raises:
            OpError: ``invalid_level`` or ``invalid_value``.

        """
        last = manifest.last_ts
        return EntryFilter(
            signature=blank(self.signature),
            coarse_signature=blank(self.coarse_signature),
            level=parse_level(self.level, "level", front=front),
            min_level=parse_level(self.min_level, "min_level", front=front),
            logger=blank(self.logger),
            user=blank(self.user),
            thread=blank(self.thread),
            request_id=blank(self.request_id),
            since=parse_time(self.since, "since", last, front=front),
            until=parse_time(self.until, "until", last, front=front),
            contains=blank(self.contains),
            with_exception=self.with_exception,
            exception=blank(self.exception),
        )


def _check(
    level: str | None, min_level: str | None, since: str | None, until: str | None, front: Front
) -> None:
    parse_level(level, "level", front=front)
    parse_level(min_level, "min_level", front=front)
    parse_time(since, "since", None, front=front)
    parse_time(until, "until", None, front=front)


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a listing: at most ``limit`` rows (``None`` = all) after ``offset``."""

    limit: int | None = None
    offset: int = 0

    @classmethod
    def parse(cls, limit: int, offset: int = 0, *, front: Front) -> Page:
        """Validate ``limit`` (0 = all) and ``offset``.

        Raises:
            OpError: ``invalid_value`` for a negative value.

        """
        if limit < 0:
            raise OpError("invalid_value", f"{flag('limit', front)} must be >= 0 (0 = no limit)")
        if offset < 0:
            raise OpError("invalid_value", f"{flag('offset', front)} must be >= 0")
        return cls(limit or None, offset)

    def complete(self, rows: int) -> bool:
        """Whether a page of ``rows`` rows holds every match (no count query needed)."""
        return self.offset == 0 and (self.limit is None or rows < self.limit)


@dataclass(frozen=True, kw_only=True)
class Select:
    """Column selection: ``columns`` (comma-separated and/or repeated) or the ``brief`` preset."""

    columns: list[str] | None = None
    brief: bool = False

    def resolve(
        self, valid: Sequence[str], preset: Sequence[str], *, front: Front
    ) -> list[str] | None:
        """Return the chosen columns (``None`` = all); ``preset`` is what ``brief`` stands for.

        Raises:
            OpError: ``usage`` for ``brief`` with ``columns``; ``invalid_value`` for an
                unknown column.

        """
        columns = parse_columns(self.columns, valid, front=front)
        if self.brief and columns:
            brief, cols = flag("brief", front), flag("columns", front)
            raise OpError(
                "usage",
                f"{brief} and {cols} cannot be combined",
                hint=f"{brief} is {cols} {','.join(preset)}",
            )
        return list(preset) if self.brief else columns


@dataclass(frozen=True, slots=True)
class TextCap:
    """How ``raw`` and ``body`` are cut: ``limit`` characters (``None`` = all).

    ``how`` ends the marker of a cut text and names the front end's option for the
    full text: ``… [truncated: N chars total, use --max-raw 0]`` (CLI) or
    ``use max_raw=0`` (API).
    """

    limit: int | None = DEFAULT_MAX_RAW
    how: str = "use --max-raw 0"

    @classmethod
    def parse(cls, value: int | None, *, default: int | None, front: Front) -> TextCap:
        """Build the cap from ``max_raw`` (0 = no limit, ``None`` = ``default``) for ``front``.

        Raises:
            OpError: ``invalid_value`` for a negative value.

        """
        how = "use --max-raw 0" if front == "cli" else "use max_raw=0"
        if value is None:
            return cls(default, how)
        if value < 0:
            raise OpError("invalid_value", f"{flag('max_raw', front)} must be >= 0 (0 = no limit)")
        return cls(value or None, how)

    def text(self, text: str) -> str:
        """Cut one text (see :func:`~atlas_log_analyzer.service.views.cap_text`)."""
        from atlas_log_analyzer.service.views import cap_text

        return text if self.limit is None else cap_text(text, self.limit, self.how)

    def rows(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cut ``raw``/``body`` of rows in place (see :func:`~.views.cap_texts`)."""
        from atlas_log_analyzer.service.views import cap_texts

        return cap_texts(items, self.limit, how=self.how)


def check_label(value: str | None, name: str, *, front: Front) -> str | None:
    """Validate a node id, cluster name or capture id; blank or missing gives ``None``.

    Raises:
        OpError: ``invalid_value`` for other characters or more than 128 of them.

    """
    text = blank(value)
    if text is None:
        return None
    text = text.strip()
    if _LABEL.match(text) is None:
        raise OpError(
            "invalid_value",
            f"{flag(name, front)} must be letters, digits, '_', '.' or '-' (at most 128, "
            f"starting with a letter or digit), not {text!r}",
        )
    return text


@dataclass(frozen=True, kw_only=True)
class IngestTarget:
    """Which node, cluster and capture an ingest belongs to (``ingest``; ``POST /api/ingest``).

    Attributes:
        node: Node (the cluster node id of the set, e.g. NODE1; only when the input is
            not split).
        cluster: Cluster (the name of the Jira instance of the nodes, e.g. jira-test;
            default: the cluster of the newest earlier set of the same node).
        capture: Capture (its id; default: the export's jobId, else a hash of the set ids
            when split, else none).
        split: Split (one set per node; auto: an export ZIP or folder with job.json,
            files named NODE_atlassian-jira.log* of two or more nodes, or one directory
            of node sub-directories; prefix and dir force a kind; none: one set).

    """

    node: str | None = None
    cluster: str | None = None
    capture: str | None = None
    split: SplitMode = "auto"

    @classmethod
    def parse(
        cls,
        *,
        node: str | None = None,
        cluster: str | None = None,
        capture: str | None = None,
        split: str | None = None,
        front: Front,
    ) -> IngestTarget:
        """Build a checked target from raw values (blank ``split`` = ``auto``).

        Raises:
            OpError: ``invalid_value`` for a bad label or split mode.

        """
        mode = blank(split) or "auto"
        for known in SPLIT_MODES:
            if mode == known:
                return cls(node=node, cluster=cluster, capture=capture, split=known).checked(
                    front=front
                )
        raise OpError(
            "invalid_value",
            f"{flag('split', front)} must be one of {', '.join(SPLIT_MODES)}, not {mode!r}",
        )

    def checked(self, *, front: Front) -> IngestTarget:
        """Return the target with blank values unset and the others validated.

        Raises:
            OpError: ``invalid_value`` for a bad label.

        """
        return IngestTarget(
            node=check_label(self.node, "node", front=front),
            cluster=check_label(self.cluster, "cluster", front=front),
            capture=check_label(self.capture, "capture", front=front),
            split=self.split,
        )
