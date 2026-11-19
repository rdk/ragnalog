"""Log text formatting: stack-trace frames and message tokens (``entry-card.js``, every page).

The script runs in node with stub hooks; every trace, message and log line is invented.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ragnalog.report.render import asset_text

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

HARNESS = r"""
const fs = require("fs");
const win = {};
new Function("window", fs.readFileSync(process.argv[2], "utf8"))(win);
const page = win.RagnalogEntryCard.create({
  badge: () => "", tsFull: (t) => String(t), bucketOf: () => "ERROR", levelNum: () => 50,
  tz: () => "UTC", entry: () => ({}),
});
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify({
  trace: page.traceHtml(input.trace),
  msg: page.msgHtml(input.message, input.variables),
  inline: input.lines.map((ln) => page.logInline(ln)),
  raw: page.rawHtml(input.entry),
}));
"""

TRACE = [
    "org.acme.store.StoreException: cannot load 'item' for key \"K-1\"",
    "\tat com.atlassian.jira.issue.DefaultIssueManager.getIssue(DefaultIssueManager.java:212)",
    "\tat org.acme.plugin.ItemLoader.load(ItemLoader.java:98) ~[?:?]",
    "\tat java.base/java.lang.reflect.Method.invoke(Method.java:566)",
    "\tat app//org.apache.catalina.core.FilterChain.doFilter(FilterChain.java:166)",
    "\tat jdk.proxy2/jdk.proxy2.$Proxy130.getIssue(Unknown Source)",
    "Caused by: java.lang.IllegalStateException: no <value>",
    "\tat org.acme.plugin.Item.<init>(Item.java:10)",
    "\t... 42 more",
    "  at least one (1) item was skipped",
]
MESSAGE = (
    "Failed in com.atlassian.jira.issue.IssueManager.getIssue() for 'alice': "
    "can't load IllegalStateException at Item.java:12"
)
LINES = [
    "GET https://jira.example.test/rest/api/2/issue?x=1 from 10.1.2.3:8080 took 1234 ms (98%)",
    r"read /opt/app/logs/app.log, C:\Program Files\App\conf\server.xml and dbconfig.xml.",
    "id 123e4567-e89b-12d3-a456-426614174000 ABC-42 req 600x1234x1 timeout=30 enabled=true",
    "at 2026-10-29 10:00:00,000+0100 on http-nio-8080-exec-12 with UTF-8, version 8.20.1",
    "SELECT ID FROM jiraissue WHERE PROJECT IN (10000)",
    "Found IN the list <NUM> times at <IP>",
    "WARN level words: ERROR and DEBUG",
]
ENTRY = {
    "preamble": "**** banner ****",
    "raw": "\n".join(
        [
            "2026-10-29 10:00:00,000+0100 exec-1 ERROR [org.acme.Dump] took 12 ms",
            *TRACE[:3],
        ]
    ),
}


def _render(tmp_path: Path) -> dict[str, Any]:
    (tmp_path / "block.js").write_text(asset_text("entry-card.js"), encoding="utf-8")
    (tmp_path / "harness.cjs").write_text(HARNESS, encoding="utf-8")
    data = {
        "trace": TRACE,
        "message": MESSAGE,
        "variables": ["alice"],
        "lines": LINES,
        "entry": ENTRY,
    }
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    argv = ["node", *(str(tmp_path / n) for n in ("harness.cjs", "block.js", "data.json"))]
    done = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def _lines(html: str) -> list[str]:
    return re.findall(r'<div class="(?:fr|t-head|ln)[^>]*>.*?</div>(?=<div|$)', html)


def _tok(cls: str, text: str) -> str:
    return f'<span class="{cls}">{text}</span>'


def test_frames_are_tokenised_and_classified(tmp_path: Path) -> None:
    html = _render(tmp_path)["trace"]
    lines = _lines(html)
    assert len(lines) == len(TRACE)
    head, core, app, jdk, tomcat, proxy, cause, init, more, prose = lines
    assert '<span class="t-pkg">org.acme.store.</span>' in head
    assert '<span class="t-ecls">StoreException</span>' in head
    assert _tok("j-str", "&#39;item&#39;") in head and _tok("j-str", "&quot;K-1&quot;") in head
    # Atlassian code: plain; the class, method, file and line each have a span
    assert core.startswith('<div class="fr">')
    cls, mth = "DefaultIssueManager", "getIssue"
    assert f'<span class="t-cls">{cls}</span>.<span class="t-mth">{mth}</span>' in core
    assert f'<span class="t-fl">{cls}.java</span>:<span class="t-ln">212</span>' in core
    # an app's own code is marked; the jar suffix keeps it a frame
    assert app.startswith('<div class="fr fr-app">')
    assert '<span class="t-jar"> ~[?:?]</span>' in app
    # platform and framework frames are dimmed, module and class-loader prefixes kept apart
    for frame in (jdk, tomcat, proxy):
        assert frame.startswith('<div class="fr fr-lib">')
    assert '<span class="t-mod">java.base/</span>' in jdk
    assert '<span class="t-mod">app//</span>' in tomcat
    assert '<span class="t-kw">Caused by: </span>' in cause and "no &lt;value&gt;" in cause
    assert '<span class="t-mth">&lt;init&gt;</span>' in init and "fr-app" in init
    assert "t-more" in more
    # prose that starts with "at" is not a frame; its number is still a token
    assert prose.startswith('<div class="ln">') and _tok("j-num", "1") in prose


def test_message_tokens(tmp_path: Path) -> None:
    msg = _render(tmp_path)["msg"]
    assert _tok("j-pkg", "com.atlassian.jira.issue.") in msg
    assert _tok("j-cls", "IssueManager") + "." + _tok("j-mth", "getIssue") + "()" in msg
    assert "&#39;<mark>alice</mark>&#39;" in msg  # variables stay marks, their quotes plain
    assert "can&#39;t" in msg  # an apostrophe does not open a string
    assert _tok("j-exc", "IllegalStateException") in msg
    assert _tok("j-fl", "Item.java") + ":" + _tok("j-ln", "12") in msg


def test_general_tokens(tmp_path: Path) -> None:
    url, paths, ids, when, sql, prose, levels = _render(tmp_path)["inline"]
    assert _tok("j-url", "https://jira.example.test/rest/api/2/issue?x=1") in url
    assert _tok("j-url", "10.1.2.3:8080") in url
    assert _tok("j-num", "1234 ms") in url and _tok("j-num", "98%") in url
    assert _tok("j-path", "/opt/app/logs/app.log") in paths
    assert _tok("j-path", r"C:\Program Files\App\conf\server.xml") in paths
    assert paths.endswith(_tok("j-path", "dbconfig.xml") + ".")  # the full stop is not the path's
    assert _tok("j-id", "123e4567-e89b-12d3-a456-426614174000") in ids
    assert _tok("j-id", "ABC-42") in ids and _tok("j-id", "600x1234x1") in ids
    assert _tok("j-attr", "timeout") + "=" + _tok("j-num", "30") in ids
    assert _tok("j-attr", "enabled") + "=" + _tok("j-kw", "true") in ids
    assert _tok("j-ts", "2026-10-29 10:00:00,000+0100") in when
    assert _tok("j-num", "8.20.1") in when
    # numbers inside names and charset names stay plain
    assert "http-nio-8080-exec-12" in when and "UTF-8" in when
    assert _tok("j-kw", "SELECT") in sql and _tok("j-kw", "WHERE") in sql
    assert _tok("j-kw", "IN") in sql and _tok("j-num", "10000") in sql
    # SQL keywords only in a statement; template placeholders untouched
    assert "j-kw" not in prose and "&lt;NUM&gt;" in prose and "&lt;IP&gt;" in prose
    assert _tok("j-lv j-lv-w", "WARN") in levels and _tok("j-lv j-lv-e", "ERROR") in levels


def test_raw_view_keeps_every_line_and_colours_it(tmp_path: Path) -> None:
    html = _render(tmp_path)["raw"]
    lines = _lines(html)
    assert len(lines) == 1 + ENTRY["raw"].count("\n") + 1  # banner line, header, body
    banner, header, head, core, app = lines
    assert banner.startswith('<div class="ln r-pre">') and "**** banner ****" in banner
    assert header.startswith('<div class="ln r-head">')
    assert _tok("j-ts", "2026-10-29 10:00:00,000+0100") in header
    assert _tok("j-lv j-lv-e", "ERROR") in header and _tok("j-num", "12 ms") in header
    assert _tok("j-pkg", "org.acme.") + _tok("j-cls", "Dump") in header
    assert head.startswith('<div class="t-head">') and core.startswith('<div class="fr">')
    assert app.startswith('<div class="fr fr-app">')
    # the text is unchanged: tags removed and entities decoded, it is the entry again
    text = "\n".join(re.sub(r"<[^>]+>", "", ln) for ln in lines)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&#39;", "'").replace("&amp;", "&")
    assert text == ENTRY["preamble"] + "\n" + ENTRY["raw"]
