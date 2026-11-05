"""Invented cluster logs for the tests: two nodes, an export ``job.json`` and its ZIP.

Everything is made up: nodes ``NODE1``/``NODE2``, packages ``org.acme.``, thread
names and messages. Tests write the files to ``tmp_path``; no ZIP is committed.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

NODES = ("NODE1", "NODE2")
JOB_ID = "2026-11-05_06-00-00-000_TST"
LOG = "atlassian-jira.log"


def node_lines(node: str, part: int, *, day: str = "2026-11-05", names: str | None = None) -> str:
    """Entries of one rotated file of ``node``: part 0 is the newest (``x.log``).

    The newest part names its node (``nodeId: ...``) unless ``names`` overrides it
    (``""`` names no node). Hours are earlier for older parts, so the files order by
    content as they do by rotation number.
    """
    hour = 10 - 2 * part
    named = node if names is None else names
    lines = [
        f"{day} {hour:02d}:00:01,000+0000 main INFO      "
        f"[org.acme.cluster.NodeService] Cluster node started, part {part}",
        f"{day} {hour:02d}:05:00,000+0000 http-nio-8080-exec-1 WARN      "
        f"[org.acme.cache.Replicator] Slow cache replication for {node.lower()}-cache",
        f"{day} {hour:02d}:10:00,000+0000 http-nio-8080-exec-2 ERROR      "
        f"[org.acme.index.Indexer] Index update failed on part {part}\n"
        "java.lang.IllegalStateException: index locked\n"
        "\tat org.acme.index.Indexer.update(Indexer.java:42)",
    ]
    if part == 0 and named:
        lines.insert(
            1,
            f"{day} {hour:02d}:00:02,000+0000 main INFO      "
            f"[org.acme.cluster.NodeService] Node info, nodeId: {named}",
        )
    if node == "NODE2":
        lines.append(
            f"{day} {hour:02d}:20:00,000+0000 scheduler-1 ERROR      "
            "[org.acme.jobs.Scheduler] Job sync did not run on node NODE2 because it is busy"
        )
    return "\n".join(lines) + "\n"


def write_node_files(
    directory: Path,
    node: str,
    *,
    parts: int = 3,
    prefix: bool = True,
    names: str | None = None,
) -> list[Path]:
    """Write ``[NODE_]atlassian-jira.log``, ``.1`` … ``.{parts-1}`` of one node."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{node}_{LOG}" if prefix else LOG
    paths = []
    for part in range(parts):
        path = directory / (stem if part == 0 else f"{stem}.{part}")
        path.write_text(node_lines(node, part, names=names), encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def job_json(nodes: Sequence[str] = NODES, *, job_id: str = JOB_ID, failed: str = "") -> str:
    """The ``job.json`` of a cluster log export of ``nodes`` (``failed``: one node's error)."""
    results = [
        {
            "node": n,
            "status": "FAILED" if n == failed else "OK",
            "data": {"files": 3, "logBytes": 1000, "zipBytes": 100},
            "errorMessage": "timed out" if n == failed else None,
            "ok": n != failed,
        }
        for n in nodes
    ]
    return json.dumps({"jobId": job_id, "scope": "cluster", "nodes": {"results": results}})


def write_export(directory: Path, nodes: Sequence[str] = NODES, **job: str) -> Path:
    """A folder as the export ZIP holds it: ``job.json`` and ``NODE_atlassian-jira.log*``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "job.json").write_text(job_json(nodes, **job), encoding="utf-8")
    for node in nodes:
        write_node_files(directory, node)
    return directory


def export_zip(path: Path, nodes: Sequence[str] = NODES, **job: str) -> Path:
    """The cluster log export ZIP of ``nodes`` (flat, as the ScriptRunner helper writes it)."""
    folder = write_export(path.with_suffix(".d"), nodes, **job)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder.iterdir()):
            zf.write(file, file.name)
    return path
