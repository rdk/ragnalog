"""Captures: the sets of several nodes collected together, read from the manifests only.

A capture is a view over its member sets (docs/cluster.md, §2): the sets whose
manifests carry the same ``capture`` id. Listing and resolving read manifests only,
so they load no polars. References (:func:`resolve_capture`):

- ``capture:PREFIX``: the capture whose id is or starts with ``PREFIX``;
- ``cluster:NAME``: the newest capture of the cluster ``NAME``;
- ``cluster:NAME@YYYY-MM-DD``: the cluster's capture whose ``last_ts`` falls on that UTC day.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import msgspec
import xxhash
from msgspec import Struct

from atlas_log_analyzer.service.errors import Front, OpError, hint

if TYPE_CHECKING:
    from atlas_log_analyzer.service.sets import Sets
    from atlas_log_analyzer.store.manifest import LogSetManifest

__all__ = [
    "CAPTURE_PREFIX",
    "CLUSTER_PREFIX",
    "CaptureInfo",
    "CaptureNodeRef",
    "capture_id",
    "capture_item",
    "captures_doc",
    "get_capture",
    "is_capture_ref",
    "list_captures",
    "resolve_capture",
]

CAPTURE_PREFIX = "capture:"
CLUSTER_PREFIX = "cluster:"


class CaptureNodeRef(Struct, kw_only=True):
    """One member set of a capture."""

    node: str
    """The set's node id (its name, or its id, for a member without one)."""
    id: str
    """Set id."""
    name: str | None
    """Set name."""
    first_ts: str | None
    """ISO 8601 time of the set's first entry, as in its manifest."""
    last_ts: str | None
    """ISO 8601 time of the set's last entry."""
    entry_count: int
    """Entries of the set."""


class CaptureInfo(Struct, kw_only=True):
    """A capture and its member sets (the items of ``captures.v1``)."""

    capture: str
    """Capture id: the export's ``jobId`` or a hash of the member set ids."""
    cluster: str | None
    """Cluster of the members (the most common one when they disagree)."""
    first_ts: str | None
    """Earliest first entry of the members."""
    last_ts: str | None
    """Latest last entry of the members."""
    entry_count: int
    """Entries of all members."""
    nodes: list[CaptureNodeRef]
    """Members, sorted by ``node`` (then ``last_ts``)."""


def capture_id(set_ids: Iterable[str]) -> str:
    """Capture id of a split ingest without an export job id: xxh3-64 hex of the sorted set ids."""
    return xxhash.xxh3_64_hexdigest("\n".join(sorted(set_ids)).encode())[:16]


def is_capture_ref(text: str) -> bool:
    """Whether ``text`` is a capture reference (``capture:…`` or ``cluster:…``), not a set."""
    return text.startswith((CAPTURE_PREFIX, CLUSTER_PREFIX))


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat()


def _info(capture: str, members: list[LogSetManifest]) -> CaptureInfo:
    clusters = Counter(m.cluster for m in members if m.cluster is not None)
    cluster = min(clusters, key=lambda c: (-clusters[c], c)) if clusters else None
    firsts = [m.first_ts for m in members if m.first_ts is not None]
    lasts = [m.last_ts for m in members if m.last_ts is not None]
    nodes = sorted(
        (
            CaptureNodeRef(
                node=m.node or m.name or m.id,
                id=m.id,
                name=m.name,
                first_ts=_iso(m.first_ts),
                last_ts=_iso(m.last_ts),
                entry_count=m.entry_count,
            )
            for m in members
        ),
        key=lambda n: (n.node, n.last_ts or "", n.id),
    )
    return CaptureInfo(
        capture=capture,
        cluster=cluster,
        first_ts=_iso(min(firsts)) if firsts else None,
        last_ts=_iso(max(lasts)) if lasts else None,
        entry_count=sum(m.entry_count for m in members),
        nodes=nodes,
    )


_NEVER = datetime.min.replace(tzinfo=UTC)


def _last(info: CaptureInfo) -> datetime:
    return datetime.fromisoformat(info.last_ts) if info.last_ts else _NEVER


def list_captures(store: Sets) -> list[CaptureInfo]:
    """Every capture of the store, newest ``last_ts`` first (then by id).

    Reads the (cached) manifests only. Sets without a ``capture`` belong to none.
    """
    members: dict[str, list[LogSetManifest]] = {}
    for m in store.manifests():
        if m.capture is not None:
            members.setdefault(m.capture, []).append(m)
    infos = [_info(capture, ms) for capture, ms in members.items()]
    infos.sort(key=lambda i: i.capture)
    infos.sort(key=_last, reverse=True)
    return infos


def get_capture(store: Sets, capture: str) -> CaptureInfo | None:
    """Return the capture with exactly this id, or ``None``."""
    found = [m for m in store.manifests() if m.capture == capture]
    return _info(capture, found) if found else None


def _not_found(ref: str, front: Front | None) -> OpError:
    return OpError.because(
        "capture_not_found",
        front or "api",
        f"no capture {ref!r}",
        hint=hint("capture_not_found", front or "api"),
    )


def _ambiguous(ref: str, found: list[CaptureInfo], front: Front | None) -> OpError:
    ids = ", ".join(i.capture for i in found[:5])
    more = f" and {len(found) - 5} more" if len(found) > 5 else ""
    return OpError.because(
        "ambiguous_capture",
        front or "api",
        f"capture reference {ref!r} is ambiguous: {ids}{more}",
        hint=hint("ambiguous_capture", front or "api"),
    )


def _day(text: str, ref: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise OpError(
            "invalid_value", f"capture reference {ref!r}: the day must be YYYY-MM-DD"
        ) from exc


def resolve_capture(store: Sets, ref: str, *, front: Front | None = None) -> CaptureInfo:
    """Resolve ``capture:PREFIX``, ``cluster:NAME`` or ``cluster:NAME@YYYY-MM-DD``.

    ``capture:PREFIX`` takes the capture with exactly that id, else the one whose id
    starts with it. ``cluster:NAME`` is the newest capture (by ``last_ts``) of the
    cluster, ``cluster:NAME@DAY`` the one whose ``last_ts`` falls on that UTC day.
    ``front`` picks the hint of an error (default: the API's).

    Raises:
        OpError: Reason ``capture_not_found`` (code ``not_found``) or
            ``ambiguous_capture`` (code ``ambiguous``); ``invalid_value`` for text that
            is not a capture reference or a bad day.

    """
    captures = list_captures(store)
    if ref.startswith(CAPTURE_PREFIX):
        prefix = ref.removeprefix(CAPTURE_PREFIX)
        exact = [i for i in captures if i.capture == prefix]
        found = exact or [i for i in captures if prefix and i.capture.startswith(prefix)]
    elif ref.startswith(CLUSTER_PREFIX):
        name, at, day_text = ref.removeprefix(CLUSTER_PREFIX).partition("@")
        found = [i for i in captures if i.cluster == name]
        if not at:
            found = found[:1]  # newest first
        else:
            day = _day(day_text, ref)
            found = [i for i in found if i.last_ts and _last(i).astimezone(UTC).date() == day]
    else:
        raise OpError(
            "invalid_value",
            f"{ref!r} is not a capture reference: use capture:ID or cluster:NAME[@YYYY-MM-DD]",
        )
    if not found:
        raise _not_found(ref, front)
    if len(found) > 1:
        raise _ambiguous(ref, found, front)
    return found[0]


def capture_item(info: CaptureInfo, *, brief: bool = False) -> dict[str, Any]:
    """One ``captures.v1`` item; ``brief`` keeps only ``node``, ``id`` and ``name`` per node."""
    item: dict[str, Any] = msgspec.to_builtins(info)
    if brief:
        item["nodes"] = [{k: n[k] for k in ("node", "id", "name")} for n in item["nodes"]]
    return item


def captures_doc(store: Sets, *, brief: bool = False) -> dict[str, Any]:
    """``captures.v1``: every capture, newest first (``captures``, ``GET /api/captures``)."""
    items = [capture_item(i, brief=brief) for i in list_captures(store)]
    return {"schema": "captures.v1", "count": len(items), "items": items}
