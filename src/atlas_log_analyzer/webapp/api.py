"""JSON API under ``/api``: the same documents as ``atlas-log --output json``."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from starlette.responses import Response

from atlas_log_analyzer import __version__
from atlas_log_analyzer.core.formats import registry
from atlas_log_analyzer.core.java import app_packages_from_env
from atlas_log_analyzer.core.masking import MASK_RULES_VERSION
from atlas_log_analyzer.core.signature import SIGNATURE_VERSION
from atlas_log_analyzer.report import example_entries
from atlas_log_analyzer.store.logset import LogSet, LogSetManifest, to_records
from atlas_log_analyzer.store.query import diff as store_diff
from atlas_log_analyzer.store.query import span_hours, with_rates
from atlas_log_analyzer.store.schema import SCHEMA_VERSION
from atlas_log_analyzer.store.triage import auto_baseline as pick_baseline
from atlas_log_analyzer.store.triage import triage as store_triage
from atlas_log_analyzer.store.views import (
    DEFAULT_MAX_RAW,
    ENTRY_BRIEF,
    ENTRY_FIELDS,
    GROUP_BRIEF,
    GROUP_FIELDS,
    cap_texts,
    diff_warning,
    frames_view,
    select_columns,
)
from atlas_log_analyzer.webapp.errors import ApiError
from atlas_log_analyzer.webapp.jobs import safe_filename
from atlas_log_analyzer.webapp.responses import JSON
from atlas_log_analyzer.webapp.state import AppState, get_state

__all__ = ["auto_baseline", "manifest_item", "parse_time", "router"]


def _auth(request: Request) -> None:
    token = request.app.state.atlas.settings.token
    if not token:
        return
    header = request.headers.get("authorization", "")
    given = (
        header[7:]
        if header.lower().startswith("bearer ")
        else request.headers.get("x-atlas-token", "")
    )
    import hmac

    if not hmac.compare_digest(given.encode(), token.encode()):
        raise ApiError("unauthorized", "missing or wrong API token", 401)


router = APIRouter(prefix="/api", default_response_class=JSON, dependencies=[Depends(_auth)])
StateDep = Annotated[AppState, Depends(get_state)]

LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")
_RELATIVE = re.compile(r"^(\d+)\s*([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_time(value: str | None, name: str, anchor: datetime | None) -> datetime | None:
    """Parse ISO 8601 (naive = UTC), ``YYYY-MM-DD`` or ``30m/2h/1d`` before ``anchor``."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if m := _RELATIVE.match(text.lower()):
        base = anchor or datetime.now(UTC)
        base = base if base.tzinfo else base.replace(tzinfo=UTC)
        return base - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ApiError(
            "invalid_value",
            f"{name}={value!r} is not a time; use ISO 8601, YYYY-MM-DD or 30m/2h/1d",
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _levels(value: str | None, name: str) -> str | None:
    if value is None or not value.strip():
        return None
    out = []
    for part in value.split(","):
        level = part.strip().upper()
        level = "WARN" if level == "WARNING" else level
        if level not in LEVELS:
            raise ApiError(
                "invalid_level", f"{name}: unknown level {part!r}; one of {', '.join(LEVELS)}"
            )
        out.append(level)
    return ",".join(out)


def manifest_item(m: LogSetManifest) -> dict[str, Any]:
    """Return a manifest as a listing item (``logset.v1`` without ``schema``)."""
    d = dict(m.to_dict())
    d.pop("schema", None)
    return d


def auto_baseline(state: AppState, ls: LogSet) -> LogSet | None:
    """Pick the default baseline of ``ls`` (``store.triage.auto_baseline``).

    That is the newest other set that ends before ``ls`` starts (an earlier snapshot),
    else the newest other set that ends no later than ``ls`` (an overlapping snapshot);
    ``None`` when there is none.
    """
    best = pick_baseline(state.manifests(), ls.manifest)
    return state.open(best.id) if best is not None else None


def _baseline(state: AppState, ls: LogSet, ref: str | None) -> LogSet | None:
    if ref is None or ref in ("", "none"):
        return None
    if ref == "auto":
        return auto_baseline(state, ls)
    return state.open(ref)


@router.get("/health")
def health(state: StateDep) -> dict[str, Any]:
    """Liveness and store readability (``health.v1``)."""
    return {"schema": "health.v1", "status": "ok", "sets": len(state.manifests())}


@router.get("/version")
def version() -> dict[str, Any]:
    """Tool and format versions (``version.v1``)."""
    import sys

    return {
        "schema": "version.v1",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "signature_version": SIGNATURE_VERSION,
        "mask_rules_version": MASK_RULES_VERSION,
        "python": sys.version.split()[0],
    }


@router.get("/formats")
def formats() -> dict[str, Any]:
    """List the registered log formats (``formats.v1``)."""
    items = []
    for fmt_name in registry.names():
        fmt = registry.get(fmt_name)
        items.append({"name": fmt.name, "description": getattr(fmt, "description", "")})
    return {"schema": "formats.v1", "count": len(items), "items": items}


@router.get("/sets")
def list_sets(state: StateDep) -> dict[str, Any]:
    """All sets, newest first (``sets.v1``); each item adds ``summary`` (errors/warnings)."""
    items = []
    for m in state.manifests():
        item = manifest_item(m)
        try:
            item["summary"] = state.summary(state.open(m.id))
        except Exception:
            item["summary"] = None
        items.append(item)
    return {
        "schema": "sets.v1",
        "store": state.store.root.name,
        "count": len(items),
        "items": items,
    }


@router.get("/sets/{ref}")
def get_set(ref: str, state: StateDep) -> dict[str, Any]:
    """One manifest (``logset.v1``) plus ``summary``."""
    ls = state.open(ref)
    doc = dict(ls.manifest.to_dict())
    doc["summary"] = state.summary(ls)
    return doc


@router.delete("/sets/{ref}")
def delete_set(ref: str, state: StateDep) -> dict[str, Any]:
    """Remove a set (``remove.v1``)."""
    ls = state.remove(ref)
    return {"schema": "remove.v1", "removed": ls.manifest.id, "name": ls.manifest.name}


@router.get("/sets/{ref}/stats")
def stats(ref: str, state: StateDep) -> dict[str, Any]:
    """Statistics of a set (``stats.v1``)."""
    ls = state.open(ref)
    return {"schema": "stats.v1", "logset": ls.manifest.id, **ls.stats()}


def _columns(
    columns: list[str] | None, brief: bool, valid: Sequence[str], preset: Sequence[str]
) -> list[str] | None:
    """Columns chosen by ``columns`` (comma-separated and/or repeated) or ``brief``."""
    if brief and columns:
        raise ApiError(
            "usage", "brief and columns cannot be combined", hint=f"brief is {','.join(preset)}"
        )
    if brief:
        return list(preset)
    chosen, unknown = select_columns(columns, valid)
    if unknown:
        raise ApiError(
            "invalid_value",
            f"unknown column(s) {', '.join(map(repr, unknown))} in columns",
            hint=f"valid columns: {', '.join(valid)}",
        )
    return chosen


def _max_raw(value: int) -> int | None:
    return value or None


MAX_RAW_HOW = "use max_raw=0"
"""How to get the full text, in the marker of a cut ``raw``/``body``."""


@router.get("/sets/{ref}/groups")
def groups(
    ref: str,
    state: StateDep,
    level: str | None = None,
    min_level: str | None = None,
    logger: str | None = None,
    since: str | None = None,
    until: str | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    contains: str | None = None,
    sort: Literal["count", "last_seen", "first_seen", "signature", "template"] = "count",
    limit: Annotated[int, Query(ge=0, le=100_000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    coarse: bool = False,
    columns: Annotated[list[str] | None, Query()] = None,
    brief: bool = False,
) -> dict[str, Any]:
    """List the groups of a set (``groups.v1``), as ``atlas-log groups``.

    ``limit=0`` returns all; ``total`` counts the matches before paging.
    ``with_exception`` keeps groups with an exception at any level, ``exception``
    matches a substring of ``exception_type``, ``contains`` one of ``template``
    (both case-insensitive). ``columns`` (comma-separated and/or repeated) or
    ``brief`` select the columns.
    """
    valid = (*GROUP_FIELDS, "signatures") if coarse else GROUP_FIELDS
    cols = _columns(columns, brief, valid, GROUP_BRIEF)
    ls = state.open(ref)
    last = ls.manifest.last_ts
    filters: dict[str, Any] = {
        "level": _levels(level, "level"),
        "min_level": _levels(min_level, "min_level"),
        "logger": logger,
        "since": parse_time(since, "since", last),
        "until": parse_time(until, "until", last),
        "coarse": coarse,
        "with_exception": with_exception,
        "exception": exception,
        "contains": contains,
    }
    df = ls.groups(**filters, sort=sort, limit=limit or None, offset=offset)
    total = ls.count_groups(**filters)
    items = to_records(df if cols is None else df.select(cols))
    return {
        "schema": "groups.v1",
        "logset": ls.manifest.id,
        "count": len(items),
        "total": total,
        "offset": offset,
        "truncated": offset + len(items) < total,
        "items": items,
    }


@router.get("/sets/{ref}/groups/{signature}")
def group(
    ref: str,
    signature: str,
    state: StateDep,
    limit: Annotated[int, Query(ge=0, le=10_000)] = 20,
    coarse: bool = False,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """One group with its hourly histogram and first entries (``group.v1``).

    ``raw`` and ``body`` of the entries are cut to ``max_raw`` characters (0 = all),
    marked as in ``entries``.
    """
    ls = state.open(ref)
    row = ls.group(signature, coarse=coarse)
    if row is None:
        raise ApiError(
            "not_found",
            f"no group {signature!r} in log set {ls.manifest.id}",
            404,
            hint=f"list signatures with GET /api/sets/{ls.manifest.id}/groups",
        )
    if coarse:
        df = ls.entries(coarse_signature=signature, limit=limit or None)
    else:
        df = ls.entries(signature=signature, limit=limit or None)
    entries = cap_texts(to_records(df), _max_raw(max_raw), how=MAX_RAW_HOW)
    return {
        "schema": "group.v1",
        "logset": ls.manifest.id,
        "group": row,
        "histogram": [] if coarse else ls.histogram("1h", signature=signature),
        "count": len(entries),
        "items": entries,
    }


@router.get("/sets/{ref}/groups/{signature}/examples")
def group_examples(
    ref: str,
    signature: str,
    state: StateDep,
    max_raw: Annotated[int, Query(ge=0)] = 4000,
) -> dict[str, Any]:
    """Return the example entries of a group in the ``report.v1`` entry shape, keyed by id.

    The report page loads these lazily when its payload was built without examples.
    """
    ls = state.open(ref)
    row = ls.group(signature)
    if row is None:
        raise ApiError("not_found", f"no group {signature!r} in log set {ls.manifest.id}", 404)
    ids = [str(i) for i in row.get("examples") or []]
    return {
        "schema": "report-entries.v1",
        "logset": ls.manifest.id,
        "signature": signature,
        "items": example_entries(ls, ids, max_raw=max_raw),
    }


@router.get("/sets/{ref}/entries")
def entries(
    ref: str,
    state: StateDep,
    signature: str | None = None,
    coarse_signature: str | None = None,
    level: str | None = None,
    min_level: str | None = None,
    logger: str | None = None,
    user: str | None = None,
    thread: str | None = None,
    request_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    with_exception: bool = False,
    exception: str | None = None,
    columns: Annotated[list[str] | None, Query()] = None,
    brief: bool = False,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """Entries of a set (``entries.v1``), paged with ``offset``/``next_offset``.

    Filters as ``atlas-log entries`` (``request_id`` exact, ``with_exception`` /
    ``exception`` on ``exception_type``). ``raw`` and ``body`` are cut to
    ``max_raw`` characters (default 4000, 0 = all); a cut row gets
    ``body_truncated`` and ``raw_chars``.
    """
    cols = _columns(columns, brief, ENTRY_FIELDS, ENTRY_BRIEF)
    ls = state.open(ref)
    last = ls.manifest.last_ts
    filters: dict[str, Any] = {
        "signature": signature,
        "coarse_signature": coarse_signature,
        "level": _levels(level, "level"),
        "min_level": _levels(min_level, "min_level"),
        "logger": logger,
        "user": user,
        "thread": thread,
        "request_id": request_id,
        "since": parse_time(since, "since", last),
        "until": parse_time(until, "until", last),
        "contains": contains,
        "with_exception": with_exception,
        "exception": exception,
    }
    df = ls.entries(**filters, limit=limit, offset=offset, columns=cols)
    total = ls.count_entries(**filters)
    items = cap_texts(to_records(df), _max_raw(max_raw), how=MAX_RAW_HOW)
    nxt = offset + len(items)
    return {
        "schema": "entries.v1",
        "logset": ls.manifest.id,
        "count": len(items),
        "total": total,
        "offset": offset,
        "next_offset": nxt if nxt < total else None,
        "truncated": nxt < total,
        "items": items,
    }


@router.get("/sets/{ref}/entries/{entry_id}/context")
def context(
    ref: str,
    entry_id: str,
    state: StateDep,
    before: Annotated[int, Query(ge=0, le=10_000)] = 5,
    after: Annotated[int, Query(ge=0, le=10_000)] = 5,
    any_thread: bool = False,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """Return the entries around one entry in time order (``entries.v1``), as ``context``.

    ``before``/``after`` entries of the anchor's thread (all threads with
    ``any_thread``), the anchor included; ``raw``/``body`` cut as in ``entries``.
    """
    ls = state.open(ref)
    df = ls.context(entry_id, before=before, after=after, same_thread=not any_thread)
    if df is None:
        raise ApiError(
            "not_found",
            f"no entry with id {entry_id!r} in log set {ls.manifest.id}",
            404,
            hint=f"entry ids are the id column of GET /api/sets/{ls.manifest.id}/entries",
        )
    items = cap_texts(to_records(df), _max_raw(max_raw), how=MAX_RAW_HOW)
    anchor = next((row for row in items if row["id"] == entry_id), None)
    return {
        "schema": "entries.v1",
        "logset": ls.manifest.id,
        "anchor": entry_id,
        "thread": anchor.get("thread") if anchor else None,
        "any_thread": any_thread,
        "count": len(items),
        "items": items,
    }


@router.get("/sets/{ref}/frames/{item}")
def frames(
    ref: str,
    item: str,
    state: StateDep,
    limit: Annotated[int, Query(ge=0)] = 10,
    all: bool = False,
) -> dict[str, Any]:
    """Return an entry's or group's exception chain, application frames first (``frames.v1``).

    ``item`` is an entry id or a group signature (its first example entry), as
    ``atlas-log frames``; ``limit`` application frames per exception (0 = all),
    ``all`` adds every frame with an ``app`` flag. ``ATLAS_LOG_APP_PACKAGES`` of the
    server process defines application code when set.
    """
    ls = state.open(ref)
    doc = frames_view(
        ls, item, limit=limit or None, all_frames=all, app_packages=app_packages_from_env()
    )
    if doc is None:
        raise ApiError(
            "not_found",
            f"no entry or group {item!r} in log set {ls.manifest.id}",
            404,
            hint=f"pass an entry id or a signature from GET /api/sets/{ls.manifest.id}/groups"
            "?with_exception=true",
        )
    return {"schema": "frames.v1", **doc}


@router.get("/sets/{ref}/timeline")
def timeline(
    ref: str,
    state: StateDep,
    bucket: str = "1h",
    signature: str | None = None,
    level: str | None = None,
    min_level: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Entry counts per time bucket (``timeline.v1``)."""
    ls = state.open(ref)
    last = ls.manifest.last_ts
    try:
        items = ls.histogram(
            bucket,
            signature=signature,
            level=_levels(level, "level"),
            min_level=_levels(min_level, "min_level"),
            since=parse_time(since, "since", last),
            until=parse_time(until, "until", last),
        )
    except ValueError as exc:
        raise ApiError("invalid_value", str(exc)) from exc
    return {
        "schema": "timeline.v1",
        "logset": ls.manifest.id,
        "bucket": bucket,
        "count": len(items),
        "items": items,
    }


@router.get("/sets/{ref}/report")
async def report(
    ref: str,
    state: StateDep,
    compare: str | None = None,
    min_level: str | None = None,
    examples: Annotated[int, Query(ge=0, le=50)] = 3,
    max_raw: Annotated[int, Query(ge=0)] = 4000,
    max_groups: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Return the ``report.v1`` payload that drives the report page.

    ``compare`` is a set reference, ``auto`` (the default baseline) or omitted.
    Payloads are cached per set version and options.
    """

    def build() -> bytes:
        ls = state.open(ref)
        base = _baseline(state, ls, compare)
        try:
            return state.report_bytes(
                ls,
                compare=base,
                min_level=_levels(min_level, "min_level"),
                examples=examples,
                max_raw=max_raw,
                max_groups=max_groups or None,
                entries_url=f"/api/sets/{ls.manifest.id}/groups/{{sig}}/examples",
            )
        except ValueError as exc:
            raise ApiError("incompatible_sets", str(exc), 409) from exc

    data = await run_in_threadpool(build)
    return Response(data, media_type="application/json")


@router.get("/diff")
def diff(
    a: str,
    b: str,
    state: StateDep,
    min_level: str | None = None,
    coarse: bool = False,
    force: bool = False,
    limit: Annotated[int, Query(ge=0)] = 0,
    rate: bool = False,
) -> dict[str, Any]:
    """Compare baseline ``a`` with ``b`` (``diff.v1``, as ``atlas-log diff A B``).

    ``rate`` adds ``rate_a``, ``rate_b`` and ``rate_ratio`` (counts per hour of each
    set's span) to ``changed`` and sorts it by ``rate_ratio``. ``warnings`` is
    present when the spans differ by more than 4x (new/vanished are unreliable).
    """
    sa, sb = state.open(a), state.open(b)
    try:
        result = store_diff(
            sa, sb, min_level=_levels(min_level, "min_level"), coarse=coarse, force=force
        )
    except ValueError as exc:
        raise ApiError(
            "incompatible_sets", str(exc), 409, hint="pass force=true to compare anyway"
        ) from exc
    changed = with_rates(result.changed, span_hours(sa), span_hours(sb)) if rate else result.changed
    parts = {"new": result.new, "vanished": result.vanished, "changed": changed}
    sections = {k: to_records(f if not limit else f.head(limit)) for k, f in parts.items()}
    totals = {k: f.height for k, f in parts.items()}
    doc: dict[str, Any] = {
        "schema": "diff.v1",
        "a": result.a,
        "b": result.b,
        **sections,
        "unchanged_count": result.unchanged_count,
        "totals": totals,
        "truncated": any(len(sections[k]) < totals[k] for k in totals),
    }
    warning = diff_warning(sa, sb)
    if warning is not None:
        doc["warnings"] = [warning]
    return doc


@router.get("/sets/{ref}/triage")
def triage(
    ref: str,
    state: StateDep,
    baseline: str = "auto",
    min_level: str = "WARN",
    grow: Annotated[float, Query(gt=1)] = 2.0,
    limit: Annotated[int, Query(ge=0)] = 50,
) -> dict[str, Any]:
    """Answer "are there new kinds of errors since the baseline?" in one call (``triage.v1``).

    ``verdict`` is ``new_errors`` (new ERROR/FATAL groups), ``new_warnings``,
    ``new_exceptions`` (only lower-level new groups carrying an exception), ``clean``
    or ``no_baseline``. ``grew`` lists groups whose hourly rate rose at least ``grow``
    times (rates normalise sets of different length); ``coverage``/``warnings`` flag
    sets whose spans differ by more than 4x. The document is
    :func:`atlas_log_analyzer.store.triage.triage` plus ``report_url`` links.
    """
    ls = state.open(ref)
    lvl = _levels(min_level, "min_level")
    base = _baseline(state, ls, baseline)
    try:
        doc = store_triage(
            ls,
            base,
            min_level=lvl,
            grow=grow,
            limit=limit,
            resolved_by="auto" if baseline == "auto" else "param",
        )
    except ValueError as exc:
        raise ApiError("incompatible_sets", str(exc), 409) from exc
    me = ls.manifest
    doc["report_url"] = f"/sets/{me.id}"
    if base is not None:
        for key in ("new", "new_exceptions", "grew"):
            for row in doc[key]:
                row["report_url"] = (
                    f"/sets/{me.id}?compare={base.manifest.id}#sig={row['signature']}"
                )
    return doc


@router.post("/ingest", status_code=202)
async def ingest(
    request: Request,
    files: list[UploadFile],
    state: StateDep,
    name: Annotated[str | None, Query(max_length=128)] = None,
    format: str | None = None,
    mine: bool = False,
    overwrite: bool = False,
) -> Response:
    """Upload log files and ingest them in the background; returns ``job.v1`` (202).

    Poll ``GET /api/jobs/{id}`` until ``state`` is ``succeeded`` (``result`` holds the
    ``logset.v1`` manifest) or ``failed``/``cancelled``. Options may also be sent as
    form fields ``name``, ``format``, ``mine``, ``overwrite``.
    """
    form = await request.form()
    name = name or _form_str(form.get("name"))
    format = format or _form_str(form.get("format"))
    mine = mine or _form_str(form.get("mine")) in ("1", "true", "on", "yes")
    overwrite = overwrite or _form_str(form.get("overwrite")) in ("1", "true", "on", "yes")
    if not files:
        raise ApiError("usage", "send one or more log files as multipart field 'files'")
    limit = state.settings.max_upload_mb * 1024 * 1024
    job_id, folder = state.jobs.new_upload_dir()
    paths: list[Path] = []
    written = 0
    try:
        for upload in files:
            target = folder / safe_filename(upload.filename or "upload.log")
            while target in paths:
                target = target.with_name(f"_{target.name}")
            with target.open("wb") as fh:
                while chunk := await upload.read(1 << 20):
                    written += len(chunk)
                    if written > limit:
                        raise ApiError(
                            "payload_too_large",
                            f"upload exceeds {state.settings.max_upload_mb} MiB",
                            413,
                        )
                    fh.write(chunk)
            paths.append(target)
    except BaseException:
        state.jobs.discard_upload(job_id)
        raise
    job = await run_in_threadpool(
        state.jobs.submit,
        job_id,
        paths,
        name=name or None,
        format=format,
        mine=mine,
        overwrite=overwrite,
    )
    return JSON(job.to_dict(), status_code=202, headers={"Location": f"/api/jobs/{job.id}"})


def _form_str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


@router.get("/jobs")
def jobs(state: StateDep) -> dict[str, Any]:
    """Ingest jobs of this server process, newest first (``jobs.v1``)."""
    items = [j.to_dict() for j in state.jobs.all()]
    return {"schema": "jobs.v1", "count": len(items), "items": items}


@router.get("/jobs/{job_id}")
def job(job_id: str, state: StateDep) -> dict[str, Any]:
    """One job (``job.v1``)."""
    found = state.jobs.get(job_id)
    if found is None:
        raise ApiError("not_found", f"no job {job_id!r}", 404)
    return found.to_dict()


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str, state: StateDep) -> dict[str, Any]:
    """Cancel a queued or running job (``job.v1``)."""
    found = state.jobs.cancel(job_id)
    if found is None:
        raise ApiError("not_found", f"no job {job_id!r}", 404)
    return found.to_dict()
