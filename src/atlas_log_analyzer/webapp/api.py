"""JSON API under ``/api``: the same documents as ``atlas-log --output json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from starlette.responses import Response

from atlas_log_analyzer.service import compare, meta, queries
from atlas_log_analyzer.service import ingest as service_ingest
from atlas_log_analyzer.service.errors import OpError
from atlas_log_analyzer.service.params import (
    DEFAULT_MAX_RAW,
    EntryQuery,
    GroupQuery,
    Page,
    Select,
    TextCap,
    parse_level,
)
from atlas_log_analyzer.service.sets import open_set
from atlas_log_analyzer.store.logset import LogSet, LogSetManifest
from atlas_log_analyzer.webapp.jobs import safe_filename
from atlas_log_analyzer.webapp.params import query_model
from atlas_log_analyzer.webapp.responses import JSON
from atlas_log_analyzer.webapp.state import AppState, get_state

__all__ = ["manifest_item", "router"]


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
        raise OpError("unauthorized", "missing or wrong API token")


router = APIRouter(prefix="/api", default_response_class=JSON, dependencies=[Depends(_auth)])
StateDep = Annotated[AppState, Depends(get_state)]


def manifest_item(m: LogSetManifest) -> dict[str, Any]:
    """Return a manifest as a listing item (``logset.v1`` without ``schema``)."""
    d = dict(m.to_dict())
    d.pop("schema", None)
    return d


def _baseline(state: AppState, ls: LogSet, ref: str | None) -> LogSet | None:
    """Return the set a ``compare`` parameter names: ``auto``, ``none``/empty, or a set."""
    return compare.choose_baseline(state, ls, ref, front="api")[0]


@router.get("/health")
def health(state: StateDep) -> dict[str, Any]:
    """Liveness and store readability (``health.v1``)."""
    return meta.health(state)


@router.get("/version")
def version() -> dict[str, Any]:
    """Tool and format versions (``version.v1``)."""
    return meta.version()


@router.get("/formats")
def formats() -> dict[str, Any]:
    """List the registered log formats (``formats.v1``)."""
    return meta.formats()


@router.get("/sets")
def list_sets(state: StateDep, brief: bool = False) -> dict[str, Any]:
    """All sets, newest first (``sets.v1``); each item adds ``summary`` (errors/warnings).

    ``stale`` is true when the set's ``recipe_key`` is older than the recipe for its
    format and spec (rebuild with ``atlas-log upgrade``). ``brief`` keeps only ``id``,
    ``name``, ``first_ts``, ``last_ts``, ``entry_count``, ``group_count``, ``summary``
    and ``stale`` per item, as ``atlas-log sets --brief``.
    """
    return queries.sets_doc(
        state,
        brief=brief,
        store=state.store.root.name,
        summary=lambda m: state.summary(state.open(m.id)),
    )


@router.get("/sets/{ref}")
def get_set(ref: str, state: StateDep) -> dict[str, Any]:
    """One manifest (``logset.v1``) plus ``summary``."""
    return queries.set_doc(state, ref, front="api", summary=state.summary)


@router.delete("/sets/{ref}")
def delete_set(ref: str, state: StateDep) -> dict[str, Any]:
    """Remove a set (``remove.v1``)."""
    return service_ingest.remove(state, ref, front="api")


@router.get("/sets/{ref}/stats")
def stats(ref: str, state: StateDep) -> dict[str, Any]:
    """Statistics of a set (``stats.v1``)."""
    return queries.stats(state, ref, front="api")


@router.get("/sets/{ref}/groups")
def groups(
    ref: str,
    state: StateDep,
    q: Annotated[GroupQuery, Depends(query_model(GroupQuery))],
    sel: Annotated[Select, Depends(query_model(Select))],
    sort: Literal["count", "last_seen", "first_seen", "signature", "template"] = "count",
    limit: Annotated[int, Query(ge=0, le=100_000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    coarse: bool = False,
) -> dict[str, Any]:
    """List the groups of a set (``groups.v1``), as ``atlas-log groups``.

    ``limit=0`` returns all; ``total`` counts the matches before paging.
    ``with_exception`` keeps groups with an exception at any level, ``exception``
    matches a substring of ``exception_type``, ``contains`` one of ``template``
    (both case-insensitive). ``columns`` (comma-separated and/or repeated) or
    ``brief`` select the columns.
    """
    page = Page(limit or None, offset)
    return queries.groups(state, ref, q, page, sel, sort=sort, coarse=coarse, front="api")


@router.get("/sets/{ref}/groups/{signature}")
def group(
    ref: str,
    signature: str,
    state: StateDep,
    sel: Annotated[Select, Depends(query_model(Select))],
    limit: Annotated[int, Query(ge=0, le=10_000)] = 20,
    coarse: bool = False,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """One group with its hourly histogram and first entries (``group.v1``).

    ``signature`` may be a unique prefix of at least 4 hex characters (``ambiguous``,
    409, lists candidates when several groups match).

    ``raw`` and ``body`` of the entries are cut to ``max_raw`` characters (0 = all),
    marked as in ``entries``. ``columns`` or ``brief`` select the entry columns as
    ``atlas-log show --columns/--brief``; ``brief`` also cuts ``group`` to the
    ``groups`` brief columns.
    """
    cap = TextCap.parse(max_raw, default=DEFAULT_MAX_RAW, front="api")
    return queries.group(
        state, ref, signature, limit=limit or None, coarse=coarse, sel=sel, cap=cap, front="api"
    )


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
    return queries.group_examples(state, ref, signature, max_raw=max_raw, front="api")


@router.get("/sets/{ref}/entries")
def entries(
    ref: str,
    state: StateDep,
    q: Annotated[EntryQuery, Depends(query_model(EntryQuery))],
    sel: Annotated[Select, Depends(query_model(Select))],
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """Entries of a set (``entries.v1``), paged with ``offset``/``next_offset``.

    Filters as ``atlas-log entries`` (``request_id`` exact, ``with_exception`` /
    ``exception`` on ``exception_type``; ``signature`` may be a unique prefix of at
    least 4 hex characters). ``raw`` and ``body`` are cut to
    ``max_raw`` characters (default 4000, 0 = all); a cut row gets
    ``body_truncated`` and ``raw_chars``.
    """
    cap = TextCap.parse(max_raw, default=DEFAULT_MAX_RAW, front="api")
    return queries.entries(state, ref, q, Page(limit, offset), sel, cap, front="api")


@router.get("/sets/{ref}/entries/{entry_id}/context")
def context(
    ref: str,
    entry_id: str,
    state: StateDep,
    sel: Annotated[Select, Depends(query_model(Select))],
    before: Annotated[int, Query(ge=0, le=10_000)] = 5,
    after: Annotated[int, Query(ge=0, le=10_000)] = 5,
    any_thread: bool = False,
    max_raw: Annotated[int, Query(ge=0)] = DEFAULT_MAX_RAW,
) -> dict[str, Any]:
    """Return the entries around one entry in time order (``entries.v1``), as ``context``.

    ``before``/``after`` entries of the anchor's thread (all threads with
    ``any_thread``), the anchor included; ``raw``/``body`` cut as in ``entries``;
    ``columns`` or ``brief`` select the columns as ``atlas-log context``.
    """
    cap = TextCap.parse(max_raw, default=DEFAULT_MAX_RAW, front="api")
    doc, _ = queries.context(
        state,
        ref,
        entry_id,
        before=before,
        after=after,
        any_thread=any_thread,
        sel=sel,
        cap=cap,
        front="api",
    )
    return doc


@router.get("/sets/{ref}/frames/{item}")
def frames(
    ref: str,
    item: str,
    state: StateDep,
    limit: Annotated[int, Query(ge=0)] = 10,
    all: bool = False,
) -> dict[str, Any]:
    """Return an entry's or group's exception chain, application frames first (``frames.v1``).

    ``item`` is an entry id or a group signature or unique signature prefix (its
    first example entry), as ``atlas-log frames``; ``limit`` application frames per
    exception (0 = all), ``all`` adds every frame with an ``app`` flag.
    ``ATLAS_LOG_APP_PACKAGES`` of the server's settings defines application code when set.
    """
    return queries.frames(
        state,
        ref,
        item,
        limit=limit or None,
        all_frames=all,
        app_packages=state.settings.app_packages,
        front="api",
    )


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
    q = EntryQuery(signature=signature, level=level, min_level=min_level, since=since, until=until)
    return queries.timeline(state, ref, q, bucket=bucket, front="api")


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
    lvl = parse_level(min_level, "min_level", front="api")

    def build() -> bytes:
        ls = open_set(state, ref, front="api")
        base = _baseline(state, ls, compare)
        try:
            return state.report_bytes(
                ls,
                compare=base,
                min_level=lvl,
                examples=examples,
                max_raw=max_raw,
                max_groups=max_groups or None,
                entries_url=f"/api/sets/{ls.manifest.id}/groups/{{sig}}/examples",
            )
        except ValueError as exc:  # options are valid: the sets are not comparable
            raise OpError.because("incompatible_sets", "api", str(exc)) from exc

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
    limit: Annotated[int, Query(ge=0)] = 50,
    rate: bool = False,
) -> dict[str, Any]:
    """Compare baseline ``a`` with ``b`` (``diff.v1``, as ``atlas-log diff A B``).

    Each section holds at most ``limit`` rows (default 50, 0 = all); ``totals``
    counts them before the cut and ``truncated`` says whether any was cut.

    ``rate`` adds ``rate_a``, ``rate_b`` and ``rate_ratio`` (counts per hour of each
    set's span) to ``changed`` and sorts it by ``rate_ratio``. ``warnings`` is
    present when the spans differ by more than 4x (new/vanished are unreliable).
    """
    return compare.diff(
        state,
        a,
        b,
        min_level=min_level,
        coarse=coarse,
        force=force,
        limit=limit or None,
        rate=rate,
        front="api",
    )


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
    doc = compare.triage(
        state,
        ref,
        baseline=baseline,
        min_level=min_level,
        grow=grow,
        limit=limit or None,
        front="api",
    )
    me, base = doc["set"], doc["baseline"]
    doc["report_url"] = f"/sets/{me['id']}"
    if base is not None:
        for key in ("new", "new_exceptions", "grew"):
            for row in doc[key]:
                row["report_url"] = f"/sets/{me['id']}?compare={base['id']}#sig={row['signature']}"
    return doc


@router.post("/ingest", status_code=202)
async def ingest(
    request: Request,
    files: list[UploadFile],
    state: StateDep,
    name: Annotated[str | None, Query(max_length=128)] = None,
    format: str | None = None,
    frames: Annotated[int | None, Query(ge=0)] = None,
    mine: bool = False,
    overwrite: bool = False,
    force: bool = False,
) -> Response:
    """Upload log files and ingest them in the background; returns ``job.v1`` (202).

    Poll ``GET /api/jobs/{id}`` until ``state`` is ``succeeded`` (``result`` holds the
    ``logset.v1`` manifest) or ``failed``/``cancelled``. Options may also be sent as
    form fields ``name``, ``format``, ``frames`` (top stack frames in signatures, as
    ``atlas-log ingest --frames``), ``mine``, ``overwrite``, ``force`` (ingest a file
    over 64 MiB that is not in the format; otherwise the job fails with
    ``not_in_format``).
    """
    form = await request.form()
    name = name or _form_str(form.get("name"))
    format = format or _form_str(form.get("format"))
    if frames is None and (text := _form_str(form.get("frames"))) is not None:
        if not (text.isascii() and text.isdigit()):
            raise OpError("invalid_value", f"frames must be an integer >= 0, not {text!r}")
        frames = int(text)
    mine = mine or _form_str(form.get("mine")) in ("1", "true", "on", "yes")
    overwrite = overwrite or _form_str(form.get("overwrite")) in ("1", "true", "on", "yes")
    force = force or _form_str(form.get("force")) in ("1", "true", "on", "yes")
    if not files:
        raise OpError("usage", "send one or more log files as multipart field 'files'")
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
                        raise OpError(
                            "payload_too_large",
                            f"upload exceeds {state.settings.max_upload_mb} MiB",
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
        frames=frames,
        mine=mine,
        overwrite=overwrite,
        force=force,
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
        raise OpError("not_found", f"no job {job_id!r}")
    return found.to_dict()


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str, state: StateDep) -> dict[str, Any]:
    """Cancel a queued or running job (``job.v1``)."""
    found = state.jobs.cancel(job_id)
    if found is None:
        raise OpError("not_found", f"no job {job_id!r}")
    return found.to_dict()
