/* atlas-log entry cards: one entry (a report example, a cluster example or merged-log row, a row
 * of the entries page) as a card with its message, banner lines, stack trace, raw view and actions.
 *
 * Shared by report.js, cluster.js and the web app's entries page (entries.js); render.py inlines
 * it before the page script, the web app serves it as /assets/report/entry-card.js. A page calls
 * AtlasEntryCard.create(hooks) once and takes the functions it needs from the result. Hooks are
 * functions, called each time, so the page's state that is reassigned (the time zone) is read fresh:
 *   badge(name, level)   level badge markup            bucketOf(level)  "ERROR" | "WARN" | ...
 *   levelNum(name)       numeric level                 tsFull(iso)      time text in the shown zone
 *   tz()                 name of the shown zone        entry(id)        the entry object of a card
 *   nodeBadge(node)      optional, rows with a node    srcPath(e)       optional, the source file of e
 *   rawUrl(id, e)        optional, "Open raw" link     actions(id, e)   optional, extra buttons markup
 *   fullLoads(id, e)     optional, the page is fetching the cut entry in full (note "loading...")
 * AtlasEntryCard also has the text-kind helpers (no hooks): loggerHtml(name, short),
 * loggerTagHtml(name) ("[logger]" of the cards), excHtml(name, short), tsHtml(text),
 * kvHtml(label, value), nilHtml(): one markup per kind, drawn by report.css.
 * tests/report/test_entry_card.py runs this file with node.
 */
(function () {
  "use strict";
  if (window.AtlasEntryCard) return;

  const NF = new Intl.NumberFormat("en-US");
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c]));
  const fmt = (n) => NF.format(n);
  const str = (x) => (x == null ? "" : String(x));
  const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});
  const arr = (x) => (Array.isArray(x) ? x : []);
  const basename = (p) => (p ? String(p).split(/[\\/]/).pop() : "");

  // ---- text kinds: the markup of every kind of small text, the same on every page (report.css
  // draws them: .lg logger, .xc exception, .ts time, .kv label + value, .nil empty value) ----
  /** An empty or unknown value. */
  const nilHtml = () => `<span class="nil" title="none">—</span>`;
  /** "c.a.j.Foo" -> "c.a.j." + "Foo": the package recedes, the class name carries. `short`
   *  abbreviates the package parts to their first letter (as the groups tables do). */
  function loggerHtml(name, short) {
    name = str(name);
    if (!name) return nilHtml();
    const dot = name.lastIndexOf(".");
    if (dot < 0) return `<span class="lg"><span class="lg-c">${esc(name)}</span></span>`;
    const pkg = name.slice(0, dot + 1);
    const p = short ? pkg.split(".").slice(0, -1).map((x) => x.slice(0, 1)).join(".") + "." : pkg;
    return `<span class="lg"><span class="lg-p">${esc(p)}</span><span class="lg-c">${esc(name.slice(dot + 1))}</span></span>`;
  }
  /** An exception class: the package muted, the class name in the exception colour. */
  function excHtml(name, short) {
    name = str(name);
    if (!name) return nilHtml();
    const dot = name.lastIndexOf(".");
    return `<span class="xc">${dot < 0 || short ? "" : `<span class="xc-p">${esc(name.slice(0, dot + 1))}</span>`}` +
      `<span class="xc-c">${esc(name.slice(dot + 1))}</span></span>`;
  }
  /** "2026-10-29 00:34:06.331": the date and the milliseconds muted, the time of day in ink. */
  function tsHtml(text) {
    text = str(text);
    const m = /^(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})?(?:[ T]?(\d{2}:\d{2}(?::\d{2})?)(\.\d+)?)?(.*)$/.exec(text);
    if (!text || !m || !(m[1] || m[2])) return text ? `<span class="ts">${esc(text)}</span>` : nilHtml();
    return `<span class="ts">${m[1] ? `<span class="d">${esc(m[1])}</span> ` : ""}${m[2] ? `<span class="t">${esc(m[2])}</span>` : ""}` +
      `${m[3] ? `<span class="ms">${esc(m[3])}</span>` : ""}${m[4] ? esc(m[4]) : ""}</span>`;
  }
  /** A muted label word and its value (thread, user, request): the value is mono; `kv-<label>`
   *  lets a context colour one kind (the entry cards' header strip does). */
  const kvHtml = (k, v) =>
    `<span class="kv kv-${esc(str(k).toLowerCase().replace(/[^a-z0-9]+/g, "-"))}"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></span>`;
  /** The logger as the log file writes it, in brackets: "[c.a.j.Foo]", the package muted and the
   *  class name in the class colour (as the raw view colours it). */
  function loggerTagHtml(name) {
    name = str(name);
    if (!name) return "";
    const dot = name.lastIndexOf(".");
    return `<span class="e-lg" title="Logger ${esc(name)}"><span class="br">[</span>` +
      (dot > 0 ? `<span class="p">${esc(name.slice(0, dot + 1))}</span>` : "") +
      `<span class="c">${esc(name.slice(dot + 1))}</span><span class="br">]</span></span>`;
  }

  function create(h) {
    const badge = (name, level) => h.badge(name, level);
    const bucketOf = (level) => h.bucketOf(level);
    const levelNum = (name) => h.levelNum(name);
    const tsFull = (iso) => h.tsFull(iso);
    const entryOf = (id) => obj(h.entry(id));
    function msgRaw(message, vars) {
      message = str(message);
      const sql = SQL_SHAPE.test(message);
      let out = "", pos = 0;
      for (const v of arr(vars)) {
        if (!v) continue;
        const i = message.indexOf(v, pos);
        if (i < 0) continue;
        out += logInline(message.slice(pos, i), sql) + `<mark>${logInline(v, sql)}</mark>`;
        pos = i + String(v).length;
      }
      return out + logInline(message.slice(pos), sql);
    }
    /** The message as a headline (its first line) and quieter continuation lines. */
    function msgHtml(message, vars) {
      const html = msgRaw(message, vars);
      const i = html.indexOf("\n");
      if (i < 0) return html;
      const pre = html.slice(0, i);
      const open = (pre.match(/<(?:span|mark)\b/g) || []).length;
      if (open !== (pre.match(/<\/(?:span|mark)>/g) || []).length) return html; // a mark spans the break
      return `<span class="m-head">${pre}</span><span class="m-rest">${html.slice(i + 1)}</span>`;
    }
    // Java in log text, stack traces and messages: tokenize the plain text, escape each piece.
    // A frame is framework/platform code (fr-lib, dimmed), third-party app code (fr-app, marked)
    // or Atlassian and common libraries (plain); its module, package, class, method, file and line
    // get their own classes. report.css draws a trace as a dark code panel in both themes.
    const T_FRAME = /^(\s*)(at\s+)((?:[\w.-]+(?:@[\w.+-]+)?)?\/\/?)?((?:[\w$]+\.)*)([\w$]+(?:\/[\w$]+)?)\.([\w$<>-]+)\(([^)]*)\)(\s*(?:~?\[[^\]]*\]\s*)?)$/;
    const T_FRAME_ANY = /^(\s*)(at\s+)(\S+?)\((.*)\)(\s*(?:~?\[[^\]]*\]\s*)?)$/;
    const T_MORE = /^\s*\.\.\.\s*\d+\s+(more|common frames omitted)\b/;
    const T_HEAD = /^(\s*)((?:Caused by|Suppressed):\s*)?([A-Za-z_$][\w$]*(?:\.[\w$]+)*\.[A-Z][\w$]*|[A-Z][\w$]*(?:Exception|Error|Throwable))(?:(:\s?)(.*))?$/;
    /** Platform and framework packages: dimmed, the code that matters is rarely there. */
    const J_LIB = /^(?:java\.|javax\.|jakarta\.|jdk\.|sun\.|com\.sun\.|org\.apache\.(?:catalina|coyote|tomcat|jasper|felix|log4j|logging)\.|org\.springframework\.|oracle\.|com\.oracle\.|com\.atlassian\.(?:jira\.(?:web|startup|config|security|ofbiz|entity)|plugin|event|sal|seraph|crowd|cache|scheduler|util|instrumentation)\.|org\.ofbiz\.|io\.atlassian\.|org\.eclipse\.gemini\.|org\.osgi\.|org\.slf4j\.|com\.google\.common\.|webwork\.|\$Proxy|jdk\.proxy)/;
    /** Atlassian and the libraries Jira ships: plain. Anything else is an app's own code. */
    const J_CORE = /^(?:com\.atlassian\.|io\.atlassian\.|org\.apache\.|net\.java\.ao\.|com\.opensymphony\.|org\.picocontainer\.|com\.google\.|org\.hibernate\.|com\.fasterxml\.|io\.micrometer\.|org\.quartz\.|com\.codahale\.|org\.postgresql\.|com\.microsoft\.sqlserver\.|com\.mysql\.|org\.h2\.|org\.hsqldb\.|com\.zaxxer\.|org\.jboss\.|org\.glassfish\.|org\.eclipse\.|io\.netty\.|reactor\.|kotlin\.|scala\.|groovy\.|org\.codehaus\.|org\.bouncycastle\.|org\.json\.|com\.querydsl\.|net\.sf\.|bucket\.|org\.joda\.|com\.thoughtworks\.|org\.dom4j\.|org\.w3c\.|org\.xml\.|net\.minidev\.|com\.nimbusds\.|org\.owasp\.|com\.github\.benmanes\.|io\.reactivex\.|org\.terracotta\.|org\.ehcache\.|org\.aopalliance\.|org\.objectweb\.|net\.bytebuddy\.|org\.mozilla\.|org\.jsoup\.|org\.tuckey\.|org\.ofbiz\.)/;
    const FOLD_KEEP = Infinity; // every line of an entry is shown; library frames are only dimmed
    /** " fr-lib", "" or " fr-app" for a frame's qualified name. */
    function javaKind(q) {
      return J_LIB.test(q) ? " fr-lib" : J_CORE.test(q) || !q.includes(".") ? "" : " fr-app";
    }
    /** Tokens in running log text (messages, trace lines, banner lines, the raw view, templates),
     *  each piece escaped: quoted strings, URLs, host names and IP addresses, file paths, Java
     *  (qualified class names with the package dimmed, Class.method( calls, exception names,
     *  File.java:123), timestamps, UUIDs, hex and request ids, issue keys, level words,
     *  true/false/null, key= names, numbers with their unit, and SQL keywords when the text holds
     *  a statement. <NAME> placeholders of templates are left as they are. */
    const SQL_SHAPE = /\bSELECT\b[\s\S]*\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b[\s\S]*\bSET\b|\bDELETE\s+FROM\b/;
    const INLINE_PARTS = [
      String.raw`(?<ph><(?:[A-Z][A-Z0-9_]*|\*)>)`,
      String.raw`(?<str>"(?:[^"\\\n]|\\.){0,400}"|(?<![\w$])'[^'\n]{1,200}'(?![\w$]))`,
      String.raw`(?<url>\b[a-z][a-z0-9+.-]*(?::[a-z0-9+.-]+)*:\/\/[^\s"'<>()\[\]{}]*[^\s"'<>()\[\]{},.;:!?])`,
      String.raw`(?<ts>\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d{1,9})?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}|UTC|GMT))?)?(?![\w:])|\b\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?![\w:]))`,
      String.raw`(?<uuid>\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b)`,
      String.raw`\b(?<pkg>(?:[a-z_][\w$]*\.)+)(?<cls>[A-Z][\w$]*)(?:(?<sep>[.#])(?<mth>[a-z_$][\w$]*)(?=\())?`,
      String.raw`\b(?<fl>[\w$-]+\.(?:java|groovy|kt|scala|jsp|vm|js)):(?<ln>\d+)\b`,
      String.raw`\b(?<exc>[A-Z][\w$]*(?:Exception|Error|Throwable))\b`,
      String.raw`\b(?<ccls>[A-Z][\w$]*)\.(?<cmth>[a-z_$][\w$]*)(?=\()`,
      String.raw`(?<path>(?<![\w\\])[A-Za-z]:\\(?:Program Files(?: \(x86\))?\\|[^\s"'<>|*?;,])*|\\\\[\w.$-]+\\[^\s"'<>|*?;,]*|(?<![\w.\/:~-])(?:~|\.{1,2})?\/[\w.$@%+~-]+(?:\/[\w.$@%+~-]*)*|\b[\w-]+(?:\.[\w-]+)*\.(?:log|xml|properties|json|ya?ml|jar|obr|war|txt|cfg|conf|ini|sh|bat|cmd|gz|zip|tar|sql|html?|css|class|pid|lock|bak|tmp|csv|pdf|png|jpe?g|gif|svg)\b)`,
      String.raw`(?<ip>\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b|\[[0-9a-fA-F]*:[0-9a-fA-F:.]+\](?::\d{1,5})?)`,
      String.raw`(?<host>\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|net|org|io|cz|sk|de|at|eu|uk|local|localdomain|lan|internal|corp|intra)(?::\d{1,5})?(?![\w.-]*[\w-]))`,
      String.raw`(?<key>\b(?!(?:UTF|ISO|SHA|AES|MD|TLS|SSL|HTTP|UCS|EUC|KOI|CP|RFC|X|JSR|JEP)-)[A-Z][A-Z0-9]{1,9}-\d{1,7}\b)`,
      String.raw`(?<hex>\b0x[0-9a-fA-F]+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b|\b\d+x\d+x\d+\b)`,
      String.raw`(?<lv>\b(?:FATAL|SEVERE|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b)`,
      String.raw`(?<kw>\b(?:true|false|null|TRUE|FALSE|NULL|NaN)\b)`,
      String.raw`(?<attr>\b[A-Za-z_][\w.-]{0,40}(?==(?!=)))`,
      String.raw`(?<num>(?<![\w.$]|\w-)[-+]?\d+(?:,\d{3})*(?:\.\d+)*(?:[eE][-+]?\d+)?(?:\s?(?:ms|ns|µs|us|s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?|days?|[kKMGTP]i?B|bytes?|b|%))?(?![\w$]))`,
    ];
    const SQL_PART = String.raw`(?<sql>\b(?:SELECT|FROM|WHERE|AND|OR|NOT|IN|IS|AS|ON|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|GROUP|ORDER|BY|HAVING|LIMIT|OFFSET|FETCH|FIRST|ROWS|ONLY|INSERT|INTO|VALUES|UPDATE|SET|DELETE|DISTINCT|UNION|ALL|EXISTS|LIKE|BETWEEN|CASE|WHEN|THEN|ELSE|END|ASC|DESC|COUNT|MAX|MIN|SUM|AVG|WITH|FOR|LOCK|NOWAIT)\b)`;
    const INLINE = new RegExp(INLINE_PARTS.join("|"), "g");
    const INLINE_SQL = new RegExp([...INLINE_PARTS.slice(0, -2), SQL_PART, ...INLINE_PARTS.slice(-2)].join("|"), "g");
    const LV_WORD = { FATAL: "e", SEVERE: "e", ERROR: "e", WARN: "w", WARNING: "w", INFO: "i", DEBUG: "d", TRACE: "d" };
    const span = (cls, s) => `<span class="${cls}">${esc(s)}</span>`;
    /** `sql`: colour SQL keywords (default: when the text itself holds a statement). */
    function logInline(text, sql) {
      text = text == null ? "" : String(text);
      if (!text) return "";
      const re = (sql == null ? SQL_SHAPE.test(text) : sql) ? INLINE_SQL : INLINE;
      re.lastIndex = 0;
      let out = "", pos = 0, m;
      while ((m = re.exec(text))) {
        const g = m.groups;
        out += esc(text.slice(pos, m.index));
        pos = m.index + m[0].length;
        if (g.ph) out += esc(m[0]);
        else if (g.str) out += span("j-str", m[0]);
        else if (g.url || g.ip || g.host) out += span("j-url", m[0]);
        else if (g.ts) out += span("j-ts", m[0]);
        else if (g.uuid || g.hex || g.key) out += span("j-id", m[0]);
        else if (g.pkg) {
          out += span("j-pkg", g.pkg) + span(/(?:Exception|Error|Throwable)$/.test(g.cls) ? "j-exc" : "j-cls", g.cls) +
            (g.mth ? esc(g.sep) + span("j-mth", g.mth) : "");
        } else if (g.fl) out += span("j-fl", g.fl) + ":" + span("j-ln", g.ln);
        else if (g.exc) out += span("j-exc", g.exc);
        else if (g.ccls) out += span("j-cls", g.ccls) + "." + span("j-mth", g.cmth);
        else if (g.path) {
          const p = /^(.*?)([.,:;!?]*)$/.exec(m[0]);
          out += span("j-path", p[1]) + esc(p[2]);
        } else if (g.lv) out += span("j-lv j-lv-" + LV_WORD[g.lv], m[0]);
        else if (g.kw || g.sql) out += span("j-kw", m[0]);
        else if (g.attr) out += span("j-attr", m[0]);
        else out += span("j-num", m[0]);
      }
      return out + esc(text.slice(pos));
    }
    function frameHtml(ln) {
      const m = T_FRAME.exec(ln);
      if (!m) {
        const a = T_FRAME_ANY.exec(ln);
        const kind = javaKind(a[3].replace(/^[^/]*\/\/?/, ""));
        const html = `<div class="fr${kind}">${esc(a[1])}<span class="t-at">${esc(a[2])}</span><span class="t-cm">${esc(a[3])}</span>` +
          `<span class="t-loc">(<span class="t-fl">${esc(a[4])}</span>)</span>${a[5] ? `<span class="t-jar">${esc(a[5])}</span>` : ""}</div>`;
        return { html, app: kind === " fr-app" };
      }
      const [, ind, at, mod, pkg, cls, meth, loc, rest] = m;
      const kind = javaKind(pkg + cls);
      const lm = /^(.*?):(\d+)$/.exec(loc);
      const where = lm ? `<span class="t-fl">${esc(lm[1])}</span>:<span class="t-ln">${lm[2]}</span>` : `<span class="t-fl">${esc(loc)}</span>`;
      const html = `<div class="fr${kind}">${esc(ind)}<span class="t-at">${esc(at)}</span>` +
        (mod ? `<span class="t-mod">${esc(mod)}</span>` : "") +
        `<span class="t-pkg">${esc(pkg)}</span><span class="t-cm"><span class="t-cls">${esc(cls)}</span>.<span class="t-mth">${esc(meth)}</span></span>` +
        `<span class="t-loc">(${where})</span>${rest ? `<span class="t-jar">${esc(rest)}</span>` : ""}</div>`;
      return { html, app: kind === " fr-app" };
    }
    function traceHtml(lines) {
      let html = "", frames = [];
      const sql = SQL_SHAPE.test(lines.join("\n"));
      const flush = () => {
        if (!frames.length) return;
        const hidden = frames.filter((f, i) => i >= FOLD_KEEP && !f.app).length;
        const body = frames.map((f, i) => (i >= FOLD_KEEP && !f.app ? f.html.replace('class="fr', 'class="fr fr-x') : f.html)).join("");
        html += hidden
          ? `<div class="fold">${body}<button type="button" class="unfold" data-unfold="${hidden}">show ${fmt(hidden)} more frame${hidden === 1 ? "" : "s"}</button></div>`
          : body;
        frames = [];
      };
      for (const ln of lines) {
        if (T_FRAME_ANY.test(ln)) { frames.push(frameHtml(ln)); continue; }
        if (T_MORE.test(ln)) {
          frames.push({ html: `<div class="fr t-more">${esc(ln)}</div>`, app: true });
          continue;
        }
        flush();
        const h = T_HEAD.exec(ln);
        if (h) {
          const dot = h[3].lastIndexOf(".");
          html += `<div class="t-head">${esc(h[1])}${h[2] ? `<span class="t-kw">${esc(h[2])}</span>` : ""}` +
            `<span class="t-exc">${dot > 0 ? `<span class="t-pkg">${esc(h[3].slice(0, dot + 1))}</span>` : ""}` +
            `<span class="t-ecls">${esc(h[3].slice(dot + 1))}</span></span>` +
            `${h[4] != null ? `<span class="t-msg">${esc(h[4])}${logInline(h[5], sql)}</span>` : ""}</div>`;
        } else {
          html += `<div class="ln">${logInline(ln, sql) || "&nbsp;"}</div>`;
        }
      }
      flush();
      return html;
    }
    const BODY_LINES = 500; // a longer body shows its first BODY_LINES lines and a "show all" button
    const FULL_BODIES = new Set(); // ids of the entries whose every line is shown
    const RAW_VIEW = new Set(); // ids of the entries shown as their exact text
    /** The exact text of an entry as in the log file: banner lines before it, then its raw. */
    const fullText = (e) => {
      const pre = str(e.preamble) || str(obj(e.context).preamble);
      return (pre ? pre + "\n" : "") + str(e.raw);
    };
    /** The raw view: the exact text, line by line, its tokens coloured as in the formatted view. */
    function rawHtml(e) {
      const raw = String(e.raw || "");
      const text = fullText(e);
      const pre = text.length > raw.length ? text.slice(0, text.length - raw.length - 1).split("\n") : [];
      const nl = raw.indexOf("\n");
      const head = nl < 0 ? raw : raw.slice(0, nl);
      const body = nl < 0 ? [] : raw.slice(nl + 1).replace(/\n$/, "").split("\n");
      return pre.map((ln) => `<div class="ln r-pre">${logInline(ln) || "&nbsp;"}</div>`).join("") +
        `<div class="ln r-head">${logInline(head) || "&nbsp;"}</div>` + traceHtml(body);
    }

    const FULL = (id, e) => !!(h.fullLoads && h.fullLoads(id, e));
    const rawUrl = (id, e) => (h.rawUrl ? str(h.rawUrl(id, e)) : "");
    /** Lines after the header line of a raw entry, trailing white space dropped. */
    function bodyLines(raw) {
      const nl = raw.indexOf("\n");
      return nl < 0 ? [] : raw.slice(nl + 1).trimEnd().split("\n");
    }
    /** Banner lines attached before the entry (report.v1 `preamble`, entries.v1 `context.preamble`). */
    function preambleHtml(e) {
      const text = str(e.preamble) || str(obj(e.context).preamble);
      if (!text) return "";
      const lines = text.split("\n");
      return `<div class="e-pre"><span class="k">banner line${lines.length === 1 ? "" : "s"} before this entry</span>` +
        lines.map((ln) => `<div class="ln">${logInline(ln) || "&nbsp;"}</div>`).join("") + `</div>`;
    }
    /** One entry (report.v1 entry shape plus node/set, or an entries.v1 row) with its actions. */
    function entryHtml(id, e) {
      e = obj(e);
      id = str(id);
      const raw = str(e.raw);
      const all = bodyLines(raw);
      const cut = all.length > BODY_LINES && !FULL_BODIES.has(id);
      const body = cut ? all.slice(0, BODY_LINES) : all;
      const meta = [["thread", e.thread], ["user", e.user], ["request", e.request_id], ["ip", e.ip], ["url", e.url]].filter((x) => x[1]);
      const src = h.srcPath ? str(h.srcPath(e)) : str(e.source_path) || (typeof e.source === "string" ? e.source : "");
      const node = str(e.node);
      const rawOn = RAW_VIEW.has(id);
      return `<article class="entry lvb-${bucketOf(levelNum(e.level_name))}"><div class="e-meta">${node && h.nodeBadge ? h.nodeBadge(node) : ""}${badge(e.level_name, levelNum(e.level_name))}` +
        `<span class="e-ts">${tsHtml(tsFull(e.ts))} <span class="tz">${esc(h.tz())}</span></span>` +
        meta.map(([k, v]) => kvHtml(k, v)).join("") + loggerTagHtml(e.logger) +
        `</div>` + (rawOn ? `<pre class="e-raw trace">${rawHtml(e)}</pre>` : `${preambleHtml(e)}<div class="e-msg">${msgHtml(e.message, e.variables)}</div>` +
        (body.length && body.some((l) => l.trim()) ? `<pre class="e-body trace">${traceHtml(body)}</pre>` : "") +
        (cut ? `<div class="e-more"><button type="button" class="btn small" data-all-lines="${esc(id)}">show all ${fmt(all.length)} lines</button><span class="muted">first ${fmt(BODY_LINES)} shown</span></div>` : "")) +
        `<div class="e-foot">` + (src ? `<span class="e-src" title="${esc(src)}">${esc(basename(src))}${e.source_line != null ? ":" + esc(e.source_line) : ""}</span>` : "") +
        (e.raw_truncated ? `<span class="muted">${FULL(id, e) ? "loading the full entry…" : `cut to ${fmt(raw.length)} characters in this file; all of it: atlas-log context SET ${esc(id)} --before 0 --after 0 --max-raw 0`}</span>` : "") +
        `<span class="grow"></span>` + (h.actions ? str(h.actions(id, e)) : "") +
        `<button type="button" class="btn small" data-raw-view="${esc(id)}" aria-pressed="${rawOn}" title="Show the exact text of the entry as in the log file, every line">${rawOn ? "Formatted" : "Raw"}</button>` +
        (rawUrl(id, e) ? `<a class="btn small" href="${esc(rawUrl(id, e))}" target="_blank" rel="noopener" title="The exact text of the entry in a new tab, its stack trace highlighted (plain text from there)">Open raw ↗</a>` : "") +
        `<button type="button" class="btn small" data-copy-raw="${esc(id)}">Copy raw</button></div></article>`;
    }
    /** "Raw" / "Formatted": redraw one card in the other view. */
    function toggleRaw(btn) {
      const id = btn.dataset.rawView;
      if (RAW_VIEW.has(id)) RAW_VIEW.delete(id); else RAW_VIEW.add(id);
      const card = btn.closest(".entry");
      if (!card) return null;
      const tpl = document.createElement("template");
      tpl.innerHTML = entryHtml(id, entryOf(id));
      const fresh = tpl.content.firstElementChild;
      const hadFocus = card.contains(document.activeElement);
      card.replaceWith(tpl.content);
      // keep the keyboard on the toggle (focus on body would re-arm the page's shortcuts)
      const again = hadFocus && fresh && fresh.querySelector("[data-raw-view]");
      if (again) again.focus({ preventScroll: true });
      return fresh; // the new card, for pages that decorate it
    }
    /** "show all N lines": render the rest of the entry's body in place (the raw is already loaded). */
    function showAllLines(btn) {
      const id = btn.dataset.allLines;
      FULL_BODIES.add(id);
      const card = btn.closest(".entry");
      const pre = card && card.querySelector(".e-body");
      if (pre) pre.innerHTML = traceHtml(bodyLines(str(entryOf(id).raw)));
      const more = btn.closest(".e-more");
      if (more) more.remove();
    }
    return { logInline, msgHtml, traceHtml, rawHtml, fullText, entryHtml, toggleRaw, showAllLines, bodyLines };
  }

  window.AtlasEntryCard = { create, loggerHtml, loggerTagHtml, excHtml, tsHtml, kvHtml, nilHtml };
})();
