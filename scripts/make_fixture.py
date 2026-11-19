"""Cut sanitised excerpts of real Jira logs into a test fixture.

Usage::

    uv run python scripts/make_fixture.py OUT --window PATH LINE COUNT [--window ...]
        [--host-domain DOMAIN ...]

Each window starts at the header at or before ``LINE`` and spans at least ``COUNT``
lines, extended to the end of the last entry. Windows are written in
the given order.

Sanitisation (applied to the whole excerpt):

* usernames (user slot of headers other than ``anonymous``/``Anonymous user``/
  ``Automation Robot``/``-``, ``; user: X`` in the url segment, ``owner: 'a(b)'``
  in mail-queue thread names) → ``user1``, ``user2`` … everywhere in the text,
  case-insensitively;
* IPv4 addresses → ``10.0.0.N``;
* host names under each ``--host-domain`` (the logs' own domain, given on the command
  line so it is never written into the repository) → ``jira.example.com``;
* e-mail addresses → ``userN@example.com``;
* issue keys ``PROJ-123`` → ``ABC-123`` (project key replaced, number kept;
  ``JRASERVER`` keys, which are public Atlassian bugs, are kept);
* SVN repository names ``/svn/<name>/`` → ``/svn/examplerepo/``.

Always review the output before committing it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ragnalog.core.formats.jira import HEADER_RE

SYSTEM_USERS = frozenset({"anonymous", "Anonymous user", "Automation Robot", "-", ""})
IP_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}\b")
URL_USER_RE = re.compile(r"; user: (\S+)")
OWNER_RE = re.compile(r"owner: '([^'(]+)\(([^')]+)\)'")
SPACES_RE = re.compile(r"\s{2,}")
ISSUE_RE = re.compile(r"\b(?!JRASERVER-|ABC-|XYZ-)[A-Z][A-Z0-9]{1,9}-(\d+)\b")
SVN_RE = re.compile(r"/svn/[^/\s'\"]+/")
KEEP_KEYS = frozenset({"UTF", "ISO", "SHA", "HTTP", "TLS", "SSL"})


def read_window(path: Path, line: int, count: int) -> list[str]:
    """Lines of one window, aligned to entry boundaries."""
    text = path.read_bytes().decode("utf-8", "replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]
    start = line - 1
    while start > 0 and not HEADER_RE.match(lines[start]):
        start -= 1
    end = min(start + max(count, 1), len(lines))
    while end < len(lines) and not HEADER_RE.match(lines[end]):
        end += 1
    return lines[start:end]


def collect_users(lines: list[str]) -> list[str]:
    """Usernames found in header user slots, url segments and mail-queue owners."""
    users: dict[str, None] = {}
    for line in lines:
        m = HEADER_RE.match(line)
        if m is None:
            continue
        ctx = m.group("ctx") or ""
        if ctx:
            first = SPACES_RE.split(ctx, maxsplit=1)[0]
            user = first if first in SYSTEM_USERS else first.split(" ")[0]
            if user not in SYSTEM_USERS:
                users[user] = None
        for um in URL_USER_RE.finditer(m.group("url") or ""):
            users[um.group(1)] = None
        for om in OWNER_RE.finditer(m.group("thread")):
            users[om.group(1)] = None
            users[om.group(2)] = None
    return [u for u in users if u.lower() not in {s.lower() for s in SYSTEM_USERS}]


def host_re(domains: list[str]) -> re.Pattern[str] | None:
    """Pattern of the host names under ``domains`` (``None`` without any)."""
    if not domains:
        return None
    alternation = "|".join(re.escape(d.strip(".")) for d in domains)
    return re.compile(rf"\b[\w.-]+\.(?:{alternation})\b", re.IGNORECASE)


def sanitise(lines: list[str], host_domains: list[str] | None = None) -> list[str]:
    """Apply the replacements described in the module docstring."""
    text = "\n".join(lines)
    users = collect_users(lines)
    user_map = {u.lower(): f"user{i}" for i, u in enumerate(users, 1)}
    if users:
        alternation = "|".join(re.escape(u) for u in sorted(users, key=len, reverse=True))
        user_re = re.compile(rf"(?<![\w.]){'(?:' + alternation + ')'}(?![\w])", re.IGNORECASE)
        text = user_re.sub(lambda m: user_map[m.group().lower()], text)

    emails: dict[str, str] = {}

    def email(m: re.Match[str]) -> str:
        return emails.setdefault(m.group().lower(), f"user{len(emails) + 1}@example.com")

    text = EMAIL_RE.sub(email, text)
    if (hosts := host_re(host_domains or [])) is not None:
        text = hosts.sub("jira.example.com", text)

    ips: dict[str, str] = {}

    def ip(m: re.Match[str]) -> str:
        return ips.setdefault(m.group(), f"10.0.0.{len(ips) + 1}")

    text = IP_RE.sub(ip, text)
    text = ISSUE_RE.sub(
        lambda m: m.group() if m.group().split("-")[0] in KEEP_KEYS else f"ABC-{m.group(1)}", text
    )
    text = SVN_RE.sub("/svn/examplerepo/", text)
    return text.split("\n")


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--window", action="append", nargs=3, required=True, metavar=("PATH", "LINE", "COUNT")
    )
    parser.add_argument(
        "--host-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="mask host names under this domain (repeatable)",
    )
    args = parser.parse_args()
    lines: list[str] = []
    for path_text, line_text, count_text in args.window:
        lines.extend(read_window(Path(path_text), int(line_text), int(count_text)))
    out_lines = sanitise(lines, args.host_domain)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(out_lines)} lines to {args.out}")


if __name__ == "__main__":
    main()
