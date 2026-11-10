"""Commands that compare sets: diff, triage, and the HTML report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from atlas_log_analyzer.cli import context as ctx
from atlas_log_analyzer.cli import params, render
from atlas_log_analyzer.service import compare as service_compare
from atlas_log_analyzer.service import ingest as service_ingest
from atlas_log_analyzer.service.errors import OpError
from atlas_log_analyzer.service.params import blank

# polars, DuckDB, rich and the ingest pipeline are imported by the code that needs them.

__all__ = ["diff", "report", "triage"]


def diff(
    set_a: str,
    set_b: str,
    /,
    *,
    min_level: str | None = None,
    level: str | None = None,
    coarse: bool = False,
    force: bool = False,
    limit: int = 50,
    rate: bool = False,
) -> None:
    """Compare the groups of two log sets (A = before, B = after).

    "new": signatures present only in B; "vanished": only in A; "changed": in
    both with a different count (ratio = count_b / count_a). Only the stored
    group tables are compared, never the raw logs.

    Output (json):
      {"schema": "diff.v1", "a": id_a, "b": id_b,
       "new": [row], "vanished": [row], "changed": [row], "unchanged_count": int,
       "totals": {"new": int, "vanished": int, "changed": int}, "truncated": bool,
       "warnings": [str]}
    Each section holds at most --limit rows (default 50, 0 = all); "totals"
    are the section sizes before --limit and "truncated" is true when any
    section was cut.
    Each row has signature, template, level, level_name, logger,
    exception_type, count_a, count_b, first_seen_a, last_seen_a, first_seen_b,
    last_seen_b (counts are 0 on the missing side); "changed" rows also have
    ratio. "new" is sorted by count_b, "vanished" by count_a, "changed" by
    |log(ratio)|, all descending. With --rate "changed" rows also have rate_a
    and rate_b (count per hour of each set's span, first to last entry) and
    rate_ratio = rate_b / rate_a, and "changed" is sorted by rate_ratio
    descending: use it when the sets cover different lengths of time.
    "warnings" is present only when the spans of A and B differ by more than
    4x either way: one sentence saying that new/vanished groups are
    unreliable (groups that occur only at certain times of day). In table and
    jsonl mode the sentence goes to stderr. In jsonl mode one row per line
    with an extra "change" key (new / vanished / changed).

    Exit codes: 0 ok; 1 the sets were built with different signature/mask-rule
    versions or specs (use --force); 3 unknown set or store error.

    Args:
        set_a: Baseline log set (id, id prefix or name).
        set_b: Log set to compare against the baseline.
        min_level: Only groups of this level or more severe (e.g. ERROR).
        level: Alias of --min-level: this level and above (unlike `entries --level`,
            which matches exactly).
        coarse: Compare by coarse signature (exception chain ignored).
        force: Compare even if signature versions or specs differ.
        limit: Maximum rows per section (new, vanished, changed); 0 = all.
        rate: Add hourly rates (rate_a, rate_b, rate_ratio) to "changed" and sort it by
            rate_ratio, for sets of different length.

    """
    if blank(level) is not None:
        if blank(min_level) is not None:
            raise OpError("usage", "give --min-level or its alias --level, not both")
        min_level = params.level(level, "level")
    doc = service_compare.diff(
        ctx.store(),
        set_a,
        set_b,
        min_level=min_level,
        coarse=coarse,
        force=force,
        limit=params.limit(limit),
        rate=rate,
        front="cli",
    )
    out = ctx.out()
    if out.mode != "json":
        for warning in doc.get("warnings", ()):
            out.warn(warning)
    jsonl = [{"change": name, **row} for name in service_compare.DIFF_SECTIONS for row in doc[name]]
    out.document(
        doc,
        items=None,
        jsonl_rows=jsonl,
        table=lambda console: render.diff(console, doc, color=out.color),
    )


def triage(
    logset: str,
    /,
    *,
    baseline: str = "auto",
    min_level: str = "WARN",
    grow: float = 2.0,
    limit: int = 50,
) -> None:
    """Answer "are there new kinds of errors since the baseline?" in one document.

    Compares SET with a baseline (as `diff BASELINE SET --min-level L`) and
    sorts the result for a decision: new groups (most severe first), new
    groups of a lower level that carry an exception, groups whose hourly rate
    grew, vanished groups. --baseline auto (default) ranks the other sets of
    the same format (the web app's rule): it rejects a set signed with
    another recipe_key (reason "stale", hint `upgrade`; or "recipe"), whose
    sources are a superset or subset of SET's ("superset"/"subset"; partial
    overlap is fine), that ends after SET ("later"), that has under 1% of
    SET's entries ("tiny") or a span more than 16x longer or shorter
    ("span"); of the rest it prefers a set that ends before SET starts, then
    the span closest to SET's, then the newest. With none left the verdict
    is no_baseline and "warnings" lists the rejected sets. An explicit
    --baseline is used as given, with a warning when it is stale, contained
    or tiny.

    Output (json):
      {"schema": "triage.v1",
       "set": {"id", "name", "first_ts", "last_ts", "entries"},
       "baseline": {"id", "name", "first_ts", "last_ts",
                    "resolved_by": "auto" | "param", "reason": str,
                    "entries": int, "shared_sources": int,
                    "candidates": [{"id", "name", "reason", "detail",
                                    "hint"?}, ...]} | null,
       "min_level": "WARN",
       "verdict": "new_errors" | "new_warnings" | "new_exceptions" | "clean"
                  | "no_baseline",
       "summary": {"new", "new_by_level": {"ERROR": n, ...}, "new_exceptions",
                   "grew", "vanished", "changed", "unchanged"} | null,
       "coverage": {"hours_baseline", "hours_set", "ratio"},
       "warnings": [str, ...],
       "new": [<diff row> + "rate_per_hour"],
       "new_exceptions": [<diff row> + "rate_per_hour"],
       "grew": [<diff row> + "ratio", "rate_a", "rate_b", "rate_ratio"],
       "vanished": [<diff row>]}
    Diff rows are those of `diff` (signature, template, level, level_name,
    logger, exception_type, count_a, count_b, first/last_seen_a/b); set and
    baseline timestamps are written as in their manifests.
    "new": groups at/above --min-level only in SET; "new_exceptions": new
    groups of ANY level with an exception that are not in "new" (a new DEBUG
    stack trace); "grew": changed groups whose hourly rate rose at least --grow
    times (rates as `diff --rate`, which normalise different spans), highest
    rate_ratio first; "vanished": groups at
    /above --min-level only in the baseline. "verdict": new_errors (a new
    ERROR/FATAL group), new_warnings (other new groups), new_exceptions (no
    new group but a new exception at a lower level), clean, no_baseline.
    "coverage" gives both spans in hours and ratio = hours_set /
    hours_baseline; when they differ by more than 4x either way "warnings"
    says that "new"/"vanished" are unreliable (a nightly set lacks daytime
    groups). "baseline.reason" says why the baseline was chosen,
    "shared_sources" counts the source files both sets share and
    "candidates" lists up to 5 rejected sets (auto only). Lists hold at most
    --limit rows; "summary" counts all.
    jsonl mode prints the document on one line. Table mode prints the
    verdict, the summary and one table per list; the warnings go to stderr.

    SET is one set: a capture reference is a usage error, as `capture REF
    --triage` gives the verdict of each node against its own previous set.

    Exit codes: 0 ok, whatever the verdict (also no_baseline); 1 bad option,
    invalid level, a capture reference, or the sets were built with different
    signature/mask-rule versions or specs; 3 unknown set or store error.

    Args:
        logset: The log set to judge (id, id prefix or name).
        baseline: Baseline set (id, id prefix or name), auto, or none (verdict no_baseline).
        min_level: Only groups of this level or more severe count as new/grew/vanished.
        grow: Minimum hourly-rate factor for "grew" (> 1).
        limit: Maximum rows per list; 0 = all.

    """
    doc = service_compare.triage(
        ctx.store(),
        logset,
        baseline=baseline,
        min_level=min_level,
        grow=grow,
        limit=params.limit(limit),
        front="cli",
    )
    out = ctx.out()
    if out.mode == "table":
        for warning in doc["warnings"]:
            out.warn(warning)
    out.document(
        doc, items=None, table=lambda console: render.triage(console, doc, color=out.color)
    )


def report(
    logset: str,
    /,
    *,
    out: Path | None = None,
    compare: str | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int | None = None,
    window: Literal["overlap", "union"] | None = None,
    open_: Annotated[bool, Parameter(name="--open")] = False,
) -> None:
    """Write a self-contained HTML report of a set or capture (the cluster page).

    The page needs no network access (CSS, JS and data are inlined) and works
    from a file share or an e-mail attachment: overview tiles, a per-hour
    timeline by level (drag to select a time window), the filterable group
    table and a detail drawer per group with example entries and the
    equivalent CLI commands. The embedded data is the "report.v1" payload
    (docs/formats/report.v1.md). With --compare SET_B the report shows the
    groups of SET relative to SET_B (SET_B = baseline, as `diff SET_B SET`).
    A capture (capture:ID, cluster:NAME[@YYYY-MM-DD]) gives the cluster page
    (the "cluster-report.v1" payload, docs/cluster.md): node cards with each
    node's triage verdict, a timeline band per node with its events, the
    groups by node, interactions and the example entries of all nodes, within
    --window (overlap by default, or union). The default file is
    <cluster>_<capture id>.html; --compare and --min-level apply to sets only.

    Output (json):
      {"schema": "report-file.v1", "path": str, "bytes": int, "groups": int,
       "entries": int, "compare": bool, "capture"?: str | null, "nodes"?: int}
    "groups" is the number of groups in the report, "entries" the number of
    embedded example entries; a capture adds "capture" (its id) and "nodes".
    Table mode prints one line.

    Exit codes: 0 ok; 1 bad option, invalid level, --out is a directory, or the
    sets were built with different signature/mask-rule versions or specs; 2 the
    output directory does not exist or unknown capture; 3 unknown set or store
    error.

    Args:
        logset: Log set id, unique id prefix or name; or a capture (capture:ID,
            cluster:NAME[@YYYY-MM-DD]).
        out: Output file; default <name or id>.html in the current directory.
        compare: Baseline log set to compare with (id, id prefix or name).
        min_level: Only groups of this level or more severe (e.g. WARN); the
            overview always covers the whole set.
        examples: Example entries per group (a capture: per group and node); 0 = none.
        max_raw: Truncate each example's raw text to this many characters; 0 = no limit.
        max_groups: Only the N largest groups; 0 = all. Default: all (a capture: 500).
        window: overlap or union (a capture only; default overlap).
        open_: Open the report in the default web browser.

    """
    from atlas_log_analyzer.service.capture_queries import names_capture

    output = ctx.out()
    if names_capture(ctx.store(), logset):
        if compare is not None or min_level is not None:
            raise OpError(
                "usage",
                "--compare and --min-level apply to the report of a set, not of a capture",
                hint="each node is triaged against its own previous set on the cluster page",
            )
        from atlas_log_analyzer.service import capture_queries

        doc = capture_queries.report_file(
            ctx.store(),
            logset,
            capture_queries.CaptureWindow(window=window or "overlap"),
            out=out,
            examples=examples,
            max_raw=max_raw,
            max_groups=500 if max_groups is None else max_groups,
            front="cli",
        )
        extra = f", {doc['nodes']} nodes"
    else:
        if window is not None:
            raise OpError(
                "usage",
                "--window applies to a capture",
                hint="pass a capture (capture:ID, cluster:NAME)",
            )
        doc = service_ingest.report_file(
            ctx.store(),
            logset,
            out=out,
            compare=compare,
            min_level=min_level,
            examples=examples,
            max_raw=max_raw,
            max_groups=max_groups or 0,
            front="cli",
        )
        extra = f", compared with {compare}" if compare is not None else ""
    line = (
        f"wrote {Path(doc['path'])} ({doc['bytes']:,} bytes, {doc['groups']:,} groups, "
        f"{doc['entries']:,} example entries{extra})"
    )
    output.document(doc, items=None, table=lambda _: output.write_line(line))
    if open_:
        import webbrowser

        webbrowser.open(Path(doc["path"]).as_uri())
