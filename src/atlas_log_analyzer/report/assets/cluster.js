/* atlas-log cluster page: renders a cluster-report.v1 payload, embedded in #cluster-data or fetched
   from <html data-report-url> (webapp). Vanilla ES2022; the standalone file makes no network requests.
   Formatting, stack-trace and column-chart helpers follow report.js (the files share no code). */
"use strict";
(() => {
  const T0 = performance.now();
  const $ = (id) => document.getElementById(id);

  function fatal(err) {
    const el = $("fatal");
    if (!el) return;
    el.hidden = false;
    el.textContent += "Cluster page error: " + (err && err.stack ? err.stack : String(err)) + "\n";
  }
  window.addEventListener("error", (e) => fatal(e.error || e.message));
  window.addEventListener("unhandledrejection", (e) => fatal(e.reason));
  /** Wrap one part of the page: a failure is reported and does not stop the other parts. */
  const guard = (fn) => (...args) => {
    try {
      return fn(...args);
    } catch (err) {
      fatal(err);
      return undefined;
    }
  };

  function boot(D) {

  // ---------------------------------------------------------------- formatting
  const NF = new Intl.NumberFormat("en-US");
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  const num = (x) => (isNum(x) ? x : 0);
  const fmt = (n) => (isNum(n) ? NF.format(n) : "—");
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c]));
  const str = (x) => (x == null ? "" : String(x));
  const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});
  const arr = (x) => (Array.isArray(x) ? x : []);

  function compact(n) {
    const a = Math.abs(n);
    if (a < 1000) return String(Math.round(n * 100) / 100);
    for (const [v, u] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (a >= v) {
        const x = n / v;
        return (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, "")) + u;
      }
    }
    return String(n);
  }
  const ts16 = (iso) => (iso ? str(iso).slice(0, 10) + " " + str(iso).slice(11, 16) : "");
  const tsShort = (iso) => (iso ? str(iso).slice(5, 10) + " " + str(iso).slice(11, 16) : "");
  const tsSec = (iso) => (iso ? str(iso).slice(5, 10) + " " + str(iso).slice(11, 19) : "");
  const tsMs = (iso) => (iso ? str(iso).slice(5, 10) + " " + str(iso).slice(11, 23) : "");
  const tsFull = (iso) => (iso ? str(iso).slice(0, 10) + " " + str(iso).slice(11, 23) : "");
  const parseTs = (iso) => (iso == null || iso === "" ? NaN : Date.parse(String(iso).replace(/(\.\d{3})\d+/, "$1")));
  const isoOf = (ms) => (isNum(ms) ? new Date(ms).toISOString() : "");
  function spanText(ms) {
    if (!(ms >= 0)) return "";
    if (ms < 60000) return Math.round(ms / 1000) + " s";
    const h = ms / 3.6e6;
    if (h < 1) return Math.round(ms / 60000) + " min";
    if (h < 48) return (h < 10 ? h.toFixed(1).replace(/\.0$/, "") : h.toFixed(0)) + " h";
    return (h / 24).toFixed(1).replace(/\.0$/, "") + " d";
  }
  const lagText = (ms) => (isNum(ms) ? (ms < 0 ? "−" : "+") + spanText(Math.abs(ms)) : "");
  function ratioText(r) {
    if (r >= 10) return r.toFixed(0);
    if (r >= 1) return r.toFixed(1).replace(/\.0$/, "");
    return r.toPrecision(2);
  }
  const PH = /&lt;([A-Z][A-Z0-9_]*|\*)&gt;/g;
  const REPEATED = /^\s*([^\w\s]){2,}\s*$|^\s*[^\w\s]{6,}\s*$/;
  const tplHtml = (t) =>
    t && REPEATED.test(t)
      ? `<span class="rep" title="${esc(t)}">(repeated characters)</span>`
      : esc(t).replace(PH, (m, n) => `<span class="ph">${n.toLowerCase()}</span>`);
  function shortLogger(l) {
    if (!l) return "";
    const parts = String(l).split(".");
    if (parts.length <= 2) return String(l);
    return parts.slice(0, -1).map((p) => p.slice(0, 1)).join(".") + "." + parts[parts.length - 1];
  }
  const shortExc = (t) => (t ? String(t).slice(String(t).lastIndexOf(".") + 1) : "");
  const basename = (p) => (p ? String(p).split(/[\\/]/).pop() : "");
  const shellArg = (s) => (/^[\w.:@%+=,/-]+$/.test(s) ? s : "'" + String(s).replace(/'/g, "'\\''") + "'");
  const trunc = (s, n) => (str(s).length > n ? str(s).slice(0, n - 1) + "…" : str(s));
  const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many || one + "s"}`;

  // ---------------------------------------------------------------- levels
  const BUCKETS = ["ERROR", "WARN", "INFO", "DEBUG", "OTHER"];
  const LEVEL_NUM = { TRACE: 10, DEBUG: 20, INFO: 30, WARN: 40, WARNING: 40, ERROR: 50, FATAL: 60 };
  const levelNum = (name) => LEVEL_NUM[str(name).toUpperCase()] || 0;
  const bucketOf = (level) =>
    level >= 50 ? "ERROR" : level >= 40 ? "WARN" : level >= 30 ? "INFO" : level >= 10 ? "DEBUG" : "OTHER";
  const badge = (name, level) => `<span class="lv lv-${bucketOf(level)}">${esc(name || "?")}</span>`;
  const LV_TITLE = { ERROR: "ERROR and FATAL", DEBUG: "DEBUG and TRACE", OTHER: "Unknown level / unparsed", WARN: "WARN", INFO: "INFO" };

  // ---------------------------------------------------------------- data
  if (!D || typeof D !== "object") throw new Error("no cluster payload");
  if (D.schema !== "cluster-report.v1") throw new Error("unsupported payload schema: " + D.schema);
  const CAP = obj(D.capture);
  const CNODES = arr(CAP.nodes).filter((n) => n && n.node != null);
  const NODES = [...new Set((arr(D.nodes).length ? arr(D.nodes) : CNODES.map((n) => n.node)).map(String))];
  const NIDX = new Map(NODES.map((n, i) => [n, i]));
  const NLABEL = (n) => (NIDX.has(n) ? "N" + (NIDX.get(n) + 1) : str(n));
  const ncls = (n) => {
    const i = NIDX.get(n);
    return i == null || i >= 8 ? "ncx" : "nc" + i;
  };
  const nodeBadge = (n) => `<span class="nb ${ncls(n)}" title="${esc(n)}"><i></i>${esc(NLABEL(n))}</span>`;
  const CN = new Map(CNODES.map((n) => [String(n.node), n]));
  const OPTS = obj(D.options);
  const URLS = D.urls && typeof D.urls === "object" ? D.urls : null;
  const WEB_LOG = !!(URLS && URLS.entries);
  const CAPREF = shellArg(CAP.capture ? "capture:" + CAP.capture : CAP.cluster ? "cluster:" + CAP.cluster : "CAPTURE");
  const setOf = (n) => obj(obj(CN.get(n)).set);
  const setRef = (n) => shellArg(setOf(n).name || setOf(n).id || n);
  const urlFill = (tpl, map) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in map ? encodeURIComponent(map[k]) : m));

  // timeline
  const TLD = obj(D.timeline);
  const BKS = arr(TLD.buckets);
  function bucketMs(b) {
    const m = /^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|min|h|d)\s*$/.exec(str(b));
    if (!m) return 0;
    return +m[1] * { ms: 1, s: 1e3, m: 6e4, min: 6e4, h: 3.6e6, d: 864e5 }[m[2]];
  }
  const NB = BKS.length;
  const BMS = bucketMs(TLD.bucket) || (NB > 1 ? parseTs(BKS[1]) - parseTs(BKS[0]) : 3.6e6) || 3.6e6;
  const TA = NB ? parseTs(BKS[0]) : NaN;
  const TZ = NB ? parseTs(BKS[NB - 1]) + BMS : NaN;
  const TLN = obj(TLD.nodes);
  const LEV = {}, NTOT = {}, NSUM = {}, COVT = {}, GAPS = {}, HASTL = {};
  for (const n of NODES) {
    const t = obj(TLN[n]);
    const lv = {};
    for (const b of BUCKETS) lv[b] = new Float64Array(NB);
    const tot = new Float64Array(NB);
    let sum = 0;
    for (const [name, values] of Object.entries(obj(t.levels))) {
      const b = BUCKETS.includes(name) ? name : bucketOf(levelNum(name));
      const a = arr(values);
      for (let i = 0; i < NB && i < a.length; i++) {
        const v = num(a[i]);
        lv[b][i] += v;
        tot[i] += v;
        sum += v;
      }
    }
    LEV[n] = lv;
    NTOT[n] = tot;
    NSUM[n] = sum;
    HASTL[n] = Object.keys(obj(t.levels)).length > 0;
    const c = obj(CN.get(n));
    const cov = obj(c.coverage);
    let a0 = parseTs(cov.since ?? c.first_ts), a1 = parseTs(cov.until ?? c.last_ts);
    const ci = arr(t.coverage);
    if (!(a0 <= a1) && ci.length === 2 && isNum(ci[0]) && isNum(ci[1]) && NB) {
      a0 = TA + ci[0] * BMS;
      a1 = TA + (ci[1] + 1) * BMS;
    }
    COVT[n] = a0 <= a1 ? [a0, a1] : null;
    GAPS[n] = arr(c.gaps).map((g) => (Array.isArray(g) ? [parseTs(g[0]), parseTs(g[1])] : [parseTs(obj(g).since), parseTs(obj(g).until)]))
      .filter(([x, y]) => x < y);
  }
  /** Whether node n has logs in bucket i (entries, or inside its coverage when the payload has no timeline for it). */
  const hasLogs = (n, i) => (HASTL[n] ? NTOT[n][i] > 0 : !!COVT[n] && TA + (i + 1) * BMS > COVT[n][0] && TA + i * BMS < COVT[n][1]);
  const WINO = obj(CAP.window);
  const WMODE = str(WINO.mode || OPTS.window);
  const WIN = (() => {
    const a = parseTs(WINO.since), b = parseTs(WINO.until);
    return a <= b ? [a, b] : null;
  })();
  const WIN_NAME = WMODE === "overlap" ? "overlap window" : WMODE === "union" ? "union window" : "window";

  // groups
  function denseOf(pairs) {
    const out = new Float64Array(NB);
    for (const p of arr(pairs)) {
      if (Array.isArray(p) && isNum(p[0]) && p[0] >= 0 && p[0] < NB) out[p[0]] += num(p[1]);
    }
    return out;
  }
  const NODE_VOL = Object.fromEntries(NODES.map((n) => [n, num(obj(CN.get(n)).entries) || NSUM[n] || 0]));
  function mkRow(g) {
    const pn = obj(g.per_node);
    const counts = NODES.map((n) => num(obj(pn[n]).count));
    const total = isNum(g.total) ? g.total : counts.reduce((a, b) => a + b, 0);
    const level = isNum(g.level) ? g.level : levelNum(g.level_name);
    const hourly = {};
    for (const n of NODES) {
      hourly[n] = arr(obj(g.hourly)[n]).filter((p) => Array.isArray(p) && isNum(p[0])).sort((a, b) => a[0] - b[0]);
    }
    const present = NODES.filter((n, i) => counts[i] > 0);
    let spread = str(g.spread);
    const skew = isNum(g.skew) ? g.skew : null;
    if (!["all", "only", "skewed"].includes(spread)) spread = present.length === 1 ? "only" : skew != null && skew >= 4 ? "skewed" : "all";
    // the node with the highest rate normalised by its volume (the one a skew points at)
    let top = null, topV = -1;
    for (const n of present) {
      const i = NIDX.get(n);
      const v = NODE_VOL[n] ? counts[i] / NODE_VOL[n] : num(obj(pn[n]).rate_per_hour) || counts[i];
      if (v > topV) { topV = v; top = n; }
    }
    const tpl = str(g.template);
    const logger = str(g.logger);
    const exc = str(g.exception_type);
    return {
      id: str(g.signature), sig: str(g.signature), tpl, level, lvl: g.level_name || bucketOf(level), bucket: bucketOf(level),
      logger, exc, total, counts, pn, present, spread, skew, top,
      first: g.first_node != null ? str(g.first_node) : null, lead: isNum(g.lead_ms) ? g.lead_ms : null,
      hourly, examples: obj(g.examples), key: (tpl + "\n" + logger + "\n" + exc).toLowerCase(),
    };
  }
  const ROWS = arr(D.groups).filter((g) => g && g.signature != null).map(mkRow);
  const GBY = new Map(ROWS.map((r) => [r.id, r]));
  let CMAX = 1, CMIN = Infinity;
  for (const r of ROWS) for (const c of r.counts) {
    if (c > CMAX) CMAX = c;
    if (c > 0 && c < CMIN) CMIN = c;
  }
  if (!(CMIN < Infinity)) CMIN = 1;
  const PRESENT = new Set(ROWS.map((r) => r.bucket));
  const DEFAULT_LV = (() => {
    const s = new Set(["ERROR", "WARN"].filter((b) => PRESENT.has(b)));
    return s.size ? s : new Set(PRESENT);
  })();

  // entries, events, interactions
  const ENTRIES = obj(D.entries);
  const ESIG = new Map();
  for (const r of ROWS) for (const ids of Object.values(r.examples)) for (const id of arr(ids)) ESIG.set(String(id), r.sig);
  function normEntry(id, e) {
    e = obj(e);
    const ms = parseTs(e.ts);
    const lvl = levelNum(e.level_name);
    return {
      id: str(id ?? e.id), node: str(e.node), set: str(e.set), ts: e.ts, ms, level_name: e.level_name, lvl,
      bucket: bucketOf(lvl), logger: str(e.logger), thread: str(e.thread), message: str(e.message),
      sig: str(e.signature) || ESIG.get(str(id ?? e.id)) || "", e,
      key: (str(e.message) + "\n" + str(e.logger) + "\n" + str(e.thread)).toLowerCase(),
    };
  }
  const nodeOrder = (n) => (NIDX.has(n) ? NIDX.get(n) : 999);
  const byTs = (a, b) => (a.ms - b.ms) || nodeOrder(a.node) - nodeOrder(b.node) || cmpStr(a.id, b.id);
  const STATIC_LOG = Object.entries(ENTRIES).map(([id, e]) => normEntry(id, e)).filter((r) => isNum(r.ms)).sort(byTs);

  const EVD = obj(D.events);
  const EVENTS = arr(EVD.items).map((e) => {
    e = obj(e);
    return {
      node: str(e.node), ts: e.ts, ms: parseTs(e.ts), kind: str(e.kind) || "event",
      subject: e.subject_node != null && e.subject_node !== "" ? str(e.subject_node) : null,
      sig: str(e.signature), entry: e.entry_id != null ? str(e.entry_id) : "", text: str(e.text),
    };
  }).filter((e) => isNum(e.ms)).sort((a, b) => a.ms - b.ms);
  const EVN = Object.fromEntries(NODES.map((n) => [n, EVENTS.filter((e) => e.node === n)]));
  const KINDS = [...new Set(EVENTS.map((e) => e.kind))];
  const PAIRS = arr(EVD.pairs).filter((p) => p && typeof p === "object");
  const IX = obj(D.interactions);
  const MENT = arr(IX.mentions).filter((m) => m && m.from != null && m.to != null);
  const COB = arr(IX.co_bursts).filter((c) => c && typeof c === "object");
  const JOBS = arr(IX.concurrent_jobs).filter((j) => j && typeof j === "object");
  const SESS = arr(IX.sessions).filter((s) => s && typeof s === "object");
  const DELTA_S = isNum(IX.delta_s) ? IX.delta_s : isNum(OPTS.delta_s) ? OPTS.delta_s : 300;

  const KIND_LABEL = {
    start: "start", stop: "stop", health_failed: "health check failed", cache_replication: "cache replication",
    cache_peer: "cache peer re-created", index_replication: "index replication", index_snapshot: "index snapshot",
    index_restore: "index restore", index_replay: "index replay", cluster_lock: "cluster lock", job_run: "job run",
  };
  const kindLabel = (k) => KIND_LABEL[k] || str(k).replace(/_/g, " ");

  // ---------------------------------------------------------------- state <-> hash
  const Y_MODES = ["shared", "node", "pct"];
  const SPREADS = ["any", "all", "only", "skewed"];
  const TABS = ["g", "i", "l"];
  const LOG_LEVELS = ["", "DEBUG", "INFO", "WARN", "ERROR"];
  const SORTS = {
    count: ["Count", (a, b) => b.total - a.total || cmpStr(a.id, b.id)],
    skew: ["Skew", (a, b) => skewKey(b) - skewKey(a) || b.total - a.total || cmpStr(a.id, b.id)],
    lead: ["Lead", (a, b) => (b.lead ?? -1) - (a.lead ?? -1) || b.total - a.total || cmpStr(a.id, b.id)],
    level: ["Level", (a, b) => b.level - a.level || b.total - a.total || cmpStr(a.id, b.id)],
    template: ["Template", (a, b) => cmpStr(a.tpl, b.tpl) || b.total - a.total],
  };
  const skewKey = (r) => (r.spread === "only" ? Infinity : r.skew ?? -1);
  for (const n of NODES) {
    const i = NIDX.get(n);
    SORTS["node:" + n] = [`Count on ${NLABEL(n)}`, (a, b) => b.counts[i] - a.counts[i] || b.total - a.total || cmpStr(a.id, b.id)];
  }
  function parseWindow(s) {
    const m = /^(\d+)-(\d+)$/.exec(s || "");
    if (!m || !NB) return null;
    const a = Math.min(+m[1], NB - 1), b = Math.min(+m[2], NB - 1);
    return a <= b ? [a, b] : [b, a];
  }
  const nodeList = (s) => (s ? s.split(",").filter((n) => NIDX.has(n)) : []);
  function parseHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    const get = (k) => p.get(k) || "";
    const tab = get("tab");
    const sort = get("sort");
    const mn = nodeList(get("mn"));
    return {
      tab: TABS.includes(tab) ? tab : "g",
      q: get("q"),
      lv: p.has("lv") ? new Set(get("lv").split(",").filter((x) => BUCKETS.includes(x))) : new Set(DEFAULT_LV),
      sp: SPREADS.includes(get("sp")) ? get("sp") : "any",
      nd: NIDX.has(get("nd")) ? get("nd") : "",
      sort: SORTS[sort] ? sort : "count",
      p: Math.max(0, (parseInt(get("p"), 10) || 1) - 1),
      hasPage: p.has("p"),
      sig: get("sig"),
      ex: get("ex"),
      t: parseWindow(get("t")),
      y: Y_MODES.includes(get("y")) ? get("y") : "shared",
      fit: get("fit") !== "0",
      arrows: get("arrows") !== "0",
      jobs: get("jobs") === "1",
      ml: LOG_LEVELS.includes(get("ml")) ? get("ml") : "",
      mn: mn.length ? new Set(mn) : null,
      mq: get("mq"),
      ms: get("ms") ? get("ms").split(",").filter(Boolean) : [],
      ctx: get("ctx"),
      cts: get("cts"),
      at: get("at"),
      theme: get("theme"),
    };
  }
  const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
  function hashOf(s) {
    const p = new URLSearchParams();
    if (s.tab !== "g") p.set("tab", s.tab);
    if (s.q) p.set("q", s.q);
    if (!sameSet(s.lv, DEFAULT_LV)) p.set("lv", [...s.lv].join(",") || "none");
    if (s.sp !== "any") p.set("sp", s.sp);
    if (s.nd) p.set("nd", s.nd);
    if (s.sort !== "count") p.set("sort", s.sort);
    if (s.t) p.set("t", s.t[0] + "-" + s.t[1]);
    if (s.y !== "shared") p.set("y", s.y);
    if (!s.fit) p.set("fit", "0");
    if (!s.arrows) p.set("arrows", "0");
    if (s.jobs) p.set("jobs", "1");
    if (s.p > 0) p.set("p", String(s.p + 1));
    if (s.sig) p.set("sig", s.sig);
    if (s.ex) p.set("ex", s.ex);
    if (s.ml) p.set("ml", s.ml);
    if (s.mn) p.set("mn", [...s.mn].join(","));
    if (s.mq) p.set("mq", s.mq);
    if (s.ms.length) p.set("ms", s.ms.join(","));
    if (s.ctx) p.set("ctx", s.ctx);
    if (s.cts) p.set("cts", s.cts);
    if (s.at) p.set("at", s.at);
    if (s.theme) p.set("theme", s.theme);
    return p.toString();
  }
  let S = parseHash();
  let CUR = "";

  function commit(patch, opts = {}) {
    Object.assign(S, patch);
    if (opts.keepPage !== true && !("p" in patch)) S.p = 0;
    const h = hashOf(S);
    if (h !== location.hash.replace(/^#/, "")) {
      try {
        if (opts.replace) history.replaceState(null, "", "#" + h);
        else history.pushState(null, "", "#" + h);
      } catch {
        location.hash = h;
      }
    }
    update();
  }
  let qTimer = 0;
  function onNav() {
    clearTimeout(qTimer);
    const prevY = S.y + S.fit + S.arrows + S.jobs + S.sig + S.t;
    S = parseHash();
    $("f-search").value = S.q;
    $("l-search").value = S.mq;
    update(prevY !== S.y + S.fit + S.arrows + S.jobs + S.sig + S.t);
  }
  window.addEventListener("popstate", onNav);
  window.addEventListener("hashchange", () => {
    if (hashOf(parseHash()) !== hashOf(S)) onNav();
  });
  /** The time window [since, until) in ms: the brushed buckets, else null. */
  const tRange = () => (S.t && NB ? [TA + S.t[0] * BMS, TA + (S.t[1] + 1) * BMS] : null);
  const inRange = (ms, rg) => !rg || (ms >= rg[0] && ms < rg[1]);

  // ---------------------------------------------------------------- groups: filtering
  let Q = { text: "", sig: "" };
  function parseQ(q) {
    q = q.trim();
    const m = /^sig:\s*(\S*)/i.exec(q);
    return m ? { text: "", sig: m[1].toLowerCase() } : { text: q.toLowerCase(), sig: "" };
  }
  function inWindow(r) {
    const [a, b] = S.t;
    for (const n of S.nd ? [S.nd] : NODES) {
      for (const [i] of r.hourly[n]) {
        if (i > b) break;
        if (i >= a) return true;
      }
    }
    return false;
  }
  function match(r, skip) {
    if (skip !== "lv" && !S.lv.has(r.bucket)) return false;
    if (Q.sig) {
      if (!r.id.toLowerCase().startsWith(Q.sig)) return false;
    } else if (Q.text && !r.key.includes(Q.text)) return false;
    if (skip !== "sp" && S.sp !== "any" && r.spread !== S.sp) return false;
    if (skip !== "nd" && S.nd && !(r.counts[NIDX.get(S.nd)] > 0)) return false;
    if (S.t && skip !== "t" && !inWindow(r)) return false;
    return true;
  }
  let LIST = [];
  function compute() {
    Q = parseQ(S.q);
    LIST = ROWS.filter((r) => match(r, ""));
    LIST.sort(SORTS[S.sort][1]);
  }

  // ---------------------------------------------------------------- charts
  const GAP = 2;
  const r1 = (x) => Math.round(x * 10) / 10;
  function chooseBin(n, plotW) {
    for (const k of [1, 2, 3, 4, 6, 8, 12, 24, 48, 96, 168, 336]) if (Math.ceil(n / k) * 5 <= plotW) return k;
    return Math.max(1, Math.ceil((n * 5) / Math.max(1, plotW)));
  }
  function niceTicks(max, parts = 3) {
    if (!(max > 0)) return [0, 1];
    const raw = max / parts;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
    const out = [];
    for (let v = 0; v < max + step * 0.999; v += step) out.push(Math.round(v * 1e6) / 1e6);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  }
  function binArr(src, k, nb) {
    const out = new Float64Array(nb);
    for (let i = 0; i < NB && i < src.length; i++) if (src[i]) out[Math.floor(i / k)] += src[i];
    return out;
  }
  function binHas(n, k, nb) {
    const out = new Uint8Array(nb);
    for (let i = 0; i < NB; i++) if (hasLogs(n, i)) out[Math.floor(i / k)] = 1;
    return out;
  }
  function binLabel(k) {
    const ms = BMS * k;
    if (ms === 3.6e6) return "hour";
    if (ms === 864e5) return "day";
    if (ms < 3.6e6) return Math.round(ms / 60000) + " min";
    if (ms % 864e5 === 0) return ms / 864e5 + " days";
    return r1(ms / 3.6e6) + " hours";
  }
  function binRange(b, k) {
    const a = TA + b * k * BMS;
    const e = Math.min(TZ, a + k * BMS);
    const s = isoOf(a), t = isoOf(e);
    return s.slice(0, 10) + " " + s.slice(11, 16) + " – " + (t.slice(0, 10) === s.slice(0, 10) ? "" : t.slice(5, 10) + " ") + t.slice(11, 16) + " UTC";
  }
  /** x-axis ticks: the smallest UTC-aligned step whose labels are >= 64 px apart. */
  function xTicks(xOfMs, pxPerMs, W, padR) {
    const steps = [9e5, 18e5, 36e5, 72e5, 108e5, 216e5, 432e5, 864e5, 1728e5, 6048e5, 12096e5];
    const step = steps.find((s) => s * pxPerMs >= 64) || 12096e5;
    const out = [];
    for (let t = Math.ceil(TA / step) * step; t <= TZ; t += step) {
      const x = xOfMs(t);
      if (x > W - padR - 14) continue;
      const iso = isoOf(t);
      out.push([x, iso.slice(11, 16) === "00:00" ? iso.slice(5, 10) : iso.slice(11, 16)]);
    }
    return out;
  }
  const rectPath = (x, top, w, bottom) => `M${r1(x)} ${r1(bottom)}V${r1(top)}H${r1(x + w)}V${r1(bottom)}Z`;
  function roundTopPath(x, top, w, bottom) {
    const r = Math.min(4, w / 2, bottom - top);
    if (r < 1) return rectPath(x, top, w, bottom);
    return `M${r1(x)} ${r1(bottom)}V${r1(top + r)}Q${r1(x)} ${r1(top)} ${r1(x + r)} ${r1(top)}` +
      `H${r1(x + w - r)}Q${r1(x + w)} ${r1(top)} ${r1(x + w)} ${r1(top + r)}V${r1(bottom)}Z`;
  }
  /** Path of a line through points [x, y | null]; null breaks the line. Lone points are returned separately. */
  function linePath(pts) {
    let d = "";
    const dots = [];
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      if (!p) continue;
      const prev = i > 0 && pts[i - 1];
      const next = i + 1 < pts.length && pts[i + 1];
      if (!prev && !next) dots.push(p);
      d += (prev ? "L" : "M") + r1(p[0]) + " " + r1(p[1]);
    }
    return { d, dots };
  }
  function glyph(kind, x, y, extra) {
    x = r1(x);
    y = r1(y);
    switch (kind) {
      case "start": return `<path class="ev${extra}" d="M${x} ${y - 5}L${x + 5} ${y + 4}H${x - 5}Z"/>`;
      case "stop": return `<path class="ev${extra}" d="M${x - 5} ${y - 4}H${x + 5}L${x} ${y + 5}Z"/>`;
      case "health_failed": return `<path class="ev-x${extra}" d="M${x - 4} ${y - 4}L${x + 4} ${y + 4}M${x + 4} ${y - 4}L${x - 4} ${y + 4}"/>`;
      case "cache_replication": return `<path class="ev${extra}" d="M${x} ${y - 5.5}L${x + 5.5} ${y}L${x} ${y + 5.5}L${x - 5.5} ${y}Z"/>`;
      case "cache_peer": return `<path class="ev-o${extra}" d="M${x} ${y - 5}L${x + 5} ${y}L${x} ${y + 5}L${x - 5} ${y}Z"/>`;
      case "index_snapshot": return `<rect class="ev${extra}" x="${x - 4.5}" y="${y - 4.5}" width="9" height="9" rx="1"/>`;
      case "index_restore": return `<rect class="ev-o${extra}" x="${x - 4}" y="${y - 4}" width="8" height="8" rx="1"/>`;
      case "index_replay": return `<circle class="ev${extra}" cx="${x}" cy="${y}" r="4.5"/>`;
      case "index_replication": return `<circle class="ev-o${extra}" cx="${x}" cy="${y}" r="4"/>`;
      case "cluster_lock": return `<rect class="ev${extra}" x="${x - 1.75}" y="${y - 6}" width="3.5" height="12" rx="1"/>`;
      case "job_run": return `<path class="ev-x${extra}" d="M${x - 4.5} ${y}H${x + 4.5}M${x} ${y - 4.5}V${y + 4.5}"/>`;
      default: return `<circle class="ev${extra}" cx="${x}" cy="${y}" r="3.5"/>`;
    }
  }
  const glyphKey = (kind) => `<svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">${glyph(kind, 0, 0, "")}</svg>`;

  const tip = $("tooltip");
  function placeTip(e) {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 4) x = Math.max(4, e.clientX - pad - w);
    if (y + h > window.innerHeight - 4) y = Math.max(4, e.clientY - pad - h);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function showTip(e, html) {
    tip.innerHTML = html;
    tip.hidden = false;
    placeTip(e);
  }
  window.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true, capture: true });

  // ---------------------------------------------------------------- timeline (small multiples)
  let TLG = null;
  const renderTimeline = guard(function renderTimeline() {
    const el = $("c-timeline");
    if (!NB || !NODES.length || !isNum(TA)) {
      el.innerHTML = `<div class="empty">No entries in this capture's time range.</div>`;
      $("tl-legend").innerHTML = "";
      $("tl-events").innerHTML = "";
      TLG = null;
      return;
    }
    const W = Math.max(360, Math.floor(el.clientWidth || 900));
    const N = NODES.length;
    const padL = 116, padR = 12;
    const plotW = W - padL - padR;
    const k = chooseBin(NB, plotW);
    const nb = Math.ceil(NB / k);
    const slot = plotW / nb;
    const bw = Math.min(24, Math.max(2, slot - GAP));
    const RULER = WIN ? 26 : 6, LANE = 24, ROWGAP = 14, AXIS = 22;
    const BAND = N <= 2 ? 116 : N <= 4 ? 88 : N <= 8 ? 64 : 48;
    const rowTop = (i) => RULER + i * (LANE + BAND + ROWGAP);
    const bottomAll = rowTop(N - 1) + LANE + BAND;
    const H = bottomAll + AXIS;
    const pxPerMs = slot / (BMS * k);
    const xOfMs = (ms) => padL + (ms - TA) * pxPerMs;
    const xEnd = Math.min(padL + plotW, xOfMs(TZ));
    const clampX = (x) => Math.max(padL, Math.min(xEnd, x));
    const sel = S.sig ? GBY.get(S.sig) || null : null;
    const fit = !!sel && S.fit;
    const rows = NODES.map((n, i) => {
      const f = S.y === "pct" ? 100 / Math.max(1, NSUM[n]) : 1;
      return {
        n, i, f, top: rowTop(i),
        lev: BUCKETS.map((b) => binArr(LEV[n][b], k, nb)),
        tot: binArr(NTOT[n], k, nb),
        has: binHas(n, k, nb),
        g: sel ? binArr(denseOf(sel.hourly[n]), k, nb) : null,
      };
    });
    let shared = 0;
    for (const r of rows) {
      const src = fit ? r.g : r.tot;
      let m = 0;
      for (let b = 0; b < nb; b++) m = Math.max(m, src[b] * r.f);
      r.peak = m;
      shared = Math.max(shared, m);
    }
    for (const r of rows) {
      r.ticks = niceTicks(S.y === "node" ? r.peak : shared, BAND < 70 ? 2 : 3);
      r.yMax = r.ticks[r.ticks.length - 1] || 1;
    }
    const tickText = (t) => (S.y === "pct" ? compact(t) + "%" : compact(t));
    const P = [];
    P.push(`<svg class="${S.arrows ? "" : "arrows-off"}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Entries per ${esc(binLabel(k))} by level, one band per node, with event lanes">`);
    P.push(`<rect class="hover-band" x="0" y="${RULER}" width="${r1(slot)}" height="${bottomAll - RULER}" visibility="hidden"/>`);
    // window ruler
    if (WIN) {
      const x0 = clampX(xOfMs(WIN[0])), x1 = clampX(xOfMs(WIN[1]));
      if (x1 > x0) {
        const label = `${WIN_NAME} ${tsShort(isoOf(WIN[0]))} → ${tsShort(isoOf(WIN[1]))} UTC (${spanText(WIN[1] - WIN[0])})`;
        const right = x0 > padL + plotW * 0.6;
        P.push(`<line class="win-line" x1="${r1(x0)}" x2="${r1(x1)}" y1="19" y2="19"/>`);
        P.push(`<line class="win-line" x1="${r1(x0)}" x2="${r1(x0)}" y1="14" y2="24"/><line class="win-line" x1="${r1(x1)}" x2="${r1(x1)}" y1="14" y2="24"/>`);
        P.push(`<text class="win-lbl" x="${r1(right ? x1 : x0 + 2)}" y="11" text-anchor="${right ? "end" : "start"}">${esc(label)}</text>`);
        for (const x of [x0, x1]) {
          if (x > padL + 1 && x < xEnd - 1) P.push(`<line class="win-edge" x1="${r1(x) + 0.5}" x2="${r1(x) + 0.5}" y1="24" y2="${bottomAll}"/>`);
        }
      }
    }
    for (const r of rows) {
      const n = r.n;
      const laneY = r.top + LANE / 2;
      const bTop = r.top + LANE, bBot = bTop + BAND;
      const plotT = bTop + 4, plotH = bBot - plotT;
      r.laneY = laneY;
      r.bTop = bTop;
      r.bBot = bBot;
      P.push(`<g class="${ncls(n)}">`);
      P.push(`<rect class="nsw" x="4" y="${laneY - 5}" width="10" height="10" rx="2"/>`);
      P.push(`<text class="row-lbl" x="19" y="${laneY + 4}">${esc(NLABEL(n))}</text>`);
      P.push(`<text class="row-sub" x="${19 + 8 * NLABEL(n).length + 6}" y="${laneY + 4}">${esc(trunc(n, 9))}<title>${esc(n)}</title></text>`);
      P.push(`</g>`);
      P.push(`<line class="lane-line" x1="${padL}" x2="${r1(xEnd)}" y1="${laneY + 0.5}" y2="${laneY + 0.5}"/>`);
      // no logs: outside the node's coverage, and its gaps
      const hatch = (xa, xb, label) => {
        if (xb - xa < 1) return;
        P.push(`<rect class="nolog-bg" x="${r1(xa)}" y="${bTop}" width="${r1(xb - xa)}" height="${BAND}"/><rect class="nolog" x="${r1(xa)}" y="${bTop}" width="${r1(xb - xa)}" height="${BAND}"/>`);
        if (label && xb - xa > 96) P.push(`<text class="nolog-lbl" x="${r1((xa + xb) / 2)}" y="${bTop + 16}" text-anchor="middle">${esc(label)}</text>`);
      };
      const cov = COVT[n];
      if (!cov) hatch(padL, xEnd, `no logs from ${NLABEL(n)}`);
      else {
        hatch(padL, clampX(xOfMs(cov[0])), `no logs from ${NLABEL(n)}`);
        hatch(clampX(xOfMs(cov[1])), xEnd, `no logs from ${NLABEL(n)}`);
      }
      for (const [a, b] of GAPS[n]) hatch(clampX(xOfMs(a)), clampX(xOfMs(b)), "gap");
      // y grid
      for (const t of r.ticks) {
        const y = r1(bBot - (plotH * t) / r.yMax) + 0.5;
        if (t > 0) P.push(`<line class="grid" x1="${padL}" x2="${r1(xEnd)}" y1="${y}" y2="${y}"/>`);
        P.push(`<text class="axis" x="${padL - 6}" y="${y + 4}" text-anchor="end">${esc(tickText(t))}</text>`);
      }
      // stacked level columns (left out when the axis is fitted to the selected group: they would all be clipped)
      const paths = BUCKETS.map(() => []);
      for (let b = 0; b < nb && !fit; b++) {
        if (!r.tot[b]) continue;
        const x = padL + b * slot + (slot - bw) / 2;
        let topIdx = -1;
        for (let s = 0; s < BUCKETS.length; s++) if (r.lev[s][b]) topIdx = s;
        let y = bBot, firstSeg = true;
        for (let s = 0; s < BUCKETS.length; s++) {
          const v = r.lev[s][b] * r.f;
          if (!v) continue;
          const hpx = (plotH * v) / r.yMax;
          const bottom = firstSeg ? y : y - GAP;
          if (bottom <= plotT) break;
          let top = Math.max(plotT, y - hpx);
          if (bottom - top < 1) top = bottom - 1;
          paths[s].push(s === topIdx && top > plotT ? roundTopPath(x, top, bw, bottom) : rectPath(x, top, bw, bottom));
          y -= hpx;
          firstSeg = false;
        }
      }
      P.push(`<g class="bars${sel ? " faded" : ""}">`);
      for (let s = 0; s < BUCKETS.length; s++) if (paths[s].length) P.push(`<path class="s-${BUCKETS[s]}" d="${paths[s].join("")}"/>`);
      P.push(`</g>`);
      P.push(`<line class="base" x1="${padL}" x2="${r1(xEnd)}" y1="${bBot + 0.5}" y2="${bBot + 0.5}"/>`);
      // selected group: its counts on this node as a line (same axis; the bars are clipped when fitted)
      if (sel) {
        const pts = [];
        for (let b = 0; b < nb; b++) {
          if (!r.has[b] && !r.g[b]) { pts.push(null); continue; }
          const v = Math.min(r.yMax, r.g[b] * r.f);
          pts.push([padL + b * slot + slot / 2, bBot - (plotH * v) / r.yMax]);
        }
        const { d, dots } = linePath(pts);
        if (d) P.push(`<path class="ghalo" d="${d}"/><path class="gline" d="${d}"/>`);
        for (const [x, y] of dots) P.push(`<circle class="gdot" cx="${r1(x)}" cy="${r1(y)}" r="3"/>`);
      }
      // events
      const rg = tRange();
      for (const e of EVN[n]) {
        if (e.kind === "job_run" && !S.jobs) continue; // routine; would hide the rare events
        const x = xOfMs(e.ms);
        if (x < padL - 2 || x > xEnd + 2) continue;
        P.push(glyph(e.kind, x, laneY, rg && !inRange(e.ms, rg) ? " dim" : ""));
      }
    }
    // mention arrows: from the reporting node's lane to the subject's lane
    const arrow = (from, to, x, cls) => {
      const a = rows[NIDX.get(from)], b = rows[NIDX.get(to)];
      if (!a || !b || a === b) return;
      const down = b.laneY > a.laneY;
      const y1 = a.laneY + (down ? 7 : -7), y2 = b.laneY + (down ? -8 : 8);
      const xr = r1(x);
      P.push(`<g class="${ncls(from)}"><line class="arrow${cls}" x1="${xr}" x2="${xr}" y1="${r1(y1)}" y2="${r1(y2 + (down ? -4 : 4))}"/>` +
        `<path class="arrow-h" d="M${xr} ${r1(y2)}L${r1(x - 3.5)} ${r1(y2 + (down ? -6 : 6))}H${r1(x + 3.5)}Z"/></g>`);
    };
    const seen = new Set();
    for (const e of EVENTS) {
      if (!e.subject || e.subject === e.node || !NIDX.has(e.subject) || !NIDX.has(e.node)) continue;
      const x = xOfMs(e.ms);
      if (x < padL || x > xEnd) continue;
      arrow(e.node, e.subject, x, "");
      seen.add(e.node + "\n" + e.subject + "\n" + Math.floor((e.ms - TA) / (BMS * k)));
    }
    const MARKS = [];
    for (const m of MENT) {
      const from = str(m.from), to = str(m.to);
      if (!NIDX.has(from) || !NIDX.has(to) || from === to) continue;
      const bins = new Map();
      for (const sig of arr(m.groups)) {
        const g = GBY.get(str(sig));
        if (!g) continue;
        for (const [i, c] of g.hourly[from]) {
          const b = Math.floor(i / k);
          const cur = bins.get(b) || { count: 0, sigs: new Set() };
          cur.count += num(c);
          cur.sigs.add(g.sig);
          bins.set(b, cur);
        }
      }
      for (const [b, v] of bins) {
        MARKS.push({ from, to, b, count: v.count, sigs: [...v.sigs] });
        if (!seen.has(from + "\n" + to + "\n" + b)) arrow(from, to, padL + b * slot + slot / 2, " arrow-m");
      }
    }
    // x axis
    for (const [x, label] of xTicks(xOfMs, pxPerMs, W, padR)) {
      P.push(`<line class="base" x1="${r1(x) + 0.5}" x2="${r1(x) + 0.5}" y1="${bottomAll}" y2="${bottomAll + 4}"/>`);
      P.push(`<text class="axis" x="${r1(x)}" y="${H - 5}" text-anchor="middle">${esc(label)}</text>`);
    }
    P.push(`<rect class="brush" x="0" y="${RULER}" width="0" height="${bottomAll - RULER}" visibility="hidden"/>`);
    for (const r of rows) {
      P.push(`<rect class="lane-hit" data-row="${r.i}" x="${padL}" y="${r.top}" width="${r1(xEnd - padL)}" height="${LANE}"/>`);
      P.push(`<rect class="hit" data-row="${r.i}" x="${padL}" y="${r.bTop}" width="${r1(xEnd - padL)}" height="${BAND}"/>`);
    }
    P.push(`</svg>`);
    el.innerHTML = P.join("");
    const svg = el.firstElementChild;
    TLG = {
      svg, padL, slot, k, nb, rows, xOfMs, pxPerMs, marks: MARKS, sel,
      band: svg.querySelector(".hover-band"), brush: svg.querySelector(".brush"),
      binAt: (clientX) => Math.max(0, Math.min(nb - 1, Math.floor((clientX - svg.getBoundingClientRect().left - padL) / slot))),
    };
    $("tl-bin").textContent = binLabel(k);
    bindTimeline(TLG);
    showWindow(TLG);
    renderTlLegend();
  });

  function renderTlLegend() {
    $("tl-legend").innerHTML = BUCKETS.filter((b) => NODES.some((n) => LEV[n][b].some((v) => v > 0))).map((b) =>
      `<span title="${esc(LV_TITLE[b])}"><i class="sw sw-${b}"></i>${b}</span>`).join("") +
      `<span title="Time a node has no logs for: before its oldest or after its newest entry, and gaps"><svg width="12" height="12" aria-hidden="true"><rect class="nolog-bg" width="12" height="12"/><rect class="nolog" width="12" height="12"/></svg>no logs</span>`;
    const counts = new Map();
    for (const e of EVENTS) counts.set(e.kind, (counts.get(e.kind) || 0) + 1);
    const ev = KINDS.map((kd) => `<span class="ev-key${kd === "job_run" && !S.jobs ? " off" : ""}" title="${esc(kd === "job_run" && !S.jobs ? "job runs are hidden from the lanes: Job runs button" : kd)}">${glyphKey(kd)}${esc(kindLabel(kd))} <span class="muted">${fmt(counts.get(kd))}</span></span>`);
    const arrows = EVENTS.some((e) => e.subject && e.subject !== e.node && NIDX.has(e.subject)) || MENT.length;
    if (arrows) ev.push(`<span class="ev-key" title="An entry on one node names another node: arrow from the reporting node to the node it names">↕ mention (from the node that logs it to the node it names)</span>`);
    $("tl-events").innerHTML = ev.length
      ? `<span class="muted">Event lanes:</span>` + ev.join("")
      : `<span class="muted">No cluster events detected in this capture.</span>`;
  }
  function showWindow(geo, range) {
    if (!geo) return;
    const w = range || (S.t ? [S.t[0] / geo.k, S.t[1] / geo.k] : null);
    if (!w) {
      geo.brush.setAttribute("visibility", "hidden");
      return;
    }
    const x0 = geo.padL + Math.floor(w[0]) * geo.slot;
    const x1 = geo.padL + (Math.floor(w[1]) + 1) * geo.slot;
    geo.brush.setAttribute("x", r1(x0));
    geo.brush.setAttribute("width", r1(Math.max(1, x1 - x0)));
    geo.brush.setAttribute("visibility", "visible");
  }
  function nearEvents(geo, row, clientX) {
    const x = clientX - geo.svg.getBoundingClientRect().left;
    const n = NODES[row];
    const out = [];
    for (const e of EVN[n] || []) if (Math.abs(geo.xOfMs(e.ms) - x) <= 7) out.push(e);
    return out;
  }
  function eventTip(list) {
    const shown = list.slice(0, 6).map((e) =>
      `<div class="tt-e"><div>${glyphKey(e.kind)} <b>${esc(kindLabel(e.kind))}</b> ${nodeBadge(e.node)}${e.subject ? ` → ${nodeBadge(e.subject)}` : ""} <span class="muted">${esc(tsSec(e.ts))}</span></div>` +
      (e.text ? `<div class="tt-x">${esc(trunc(e.text, 180))}</div>` : "") + `</div>`).join("");
    return `<div class="tt-h">${esc(list.length === 1 ? "Event" : list.length + " events")} · click to open the merged log here</div>${shown}` +
      (list.length > 6 ? `<div class="muted">+${fmt(list.length - 6)} more</div>` : "");
  }
  function bandTip(geo, row, b) {
    const r = geo.rows[row];
    const n = r.n;
    const out = [`<div class="tt-h">${esc(binRange(b, geo.k))}</div>`];
    out.push(`<div class="tt-r tt-n">${nodeBadge(n)}<span>${esc(n)}</span></div>`);
    if (!r.has[b]) out.push(`<div class="muted">No logs from ${esc(NLABEL(n))} here (${COVT[n] && TA + b * geo.k * BMS >= COVT[n][0] && TA + b * geo.k * BMS < COVT[n][1] ? "gap" : "outside its coverage"})</div>`);
    for (let s = BUCKETS.length - 1; s >= 0; s--) {
      const v = r.lev[s][b];
      if (v) out.push(`<div class="tt-r"><b>${fmt(v)}</b><i class="sw-${BUCKETS[s]}"></i><span>${BUCKETS[s]}</span></div>`);
    }
    if (r.tot[b]) {
      out.push(`<div class="tt-r tt-t"><b>${fmt(r.tot[b])}</b><i></i><span>total${S.y === "pct" ? ` · ${r1(r.tot[b] * r.f)}% of ${esc(NLABEL(n))}'s entries` : ""}</span></div>`);
    }
    if (geo.sel) out.push(`<div class="tt-r tt-t"><b>${fmt(r.g[b])}</b><i class="gkey"></i><span>selected group</span></div>`);
    if (NODES.length > 1) {
      out.push(`<div class="tt-sep">All nodes</div>` + geo.rows.map((o) =>
        `<div class="tt-r"><b>${o.has[b] ? fmt(geo.sel ? o.g[b] : o.tot[b]) : "–"}</b>${nodeBadge(o.n)}<span>${o.has[b] ? (geo.sel ? "group" : "entries") : "no logs"}</span></div>`).join(""));
    }
    return out.join("");
  }
  function bindTimeline(geo) {
    const svg = geo.svg;
    const rowOf = (t) => (t && t.dataset && t.dataset.row != null ? +t.dataset.row : -1);
    svg.addEventListener("pointermove", (e) => {
      const d = geo.dragging;
      if (d) {
        d.b1 = geo.binAt(e.clientX);
        if (Math.abs(e.clientX - d.x0) > 3) d.moved = true;
        if (d.moved) showWindow(geo, [Math.min(d.b0, d.b1), Math.max(d.b0, d.b1)]);
        return;
      }
      const t = e.target;
      const row = rowOf(t);
      if (row < 0) return;
      const b = geo.binAt(e.clientX);
      geo.band.setAttribute("x", r1(geo.padL + b * geo.slot));
      geo.band.setAttribute("visibility", "visible");
      if (t.classList.contains("lane-hit")) {
        const near = nearEvents(geo, row, e.clientX);
        if (near.length) return showTip(e, eventTip(near));
        const n = NODES[row];
        const ms = geo.marks.filter((m) => m.from === n && m.b === b);
        if (ms.length) {
          return showTip(e, `<div class="tt-h">${esc(binRange(b, geo.k))}</div>` + ms.map((m) =>
            `<div class="tt-e">${nodeBadge(m.from)} → ${nodeBadge(m.to)} named ${fmt(m.count)}× by ${m.sigs.map((s) => `<div class="tt-x">${tplHtml(trunc((GBY.get(s) || {}).tpl || s, 120))}</div>`).join("")}</div>`).join(""));
        }
        tip.hidden = true;
        return;
      }
      showTip(e, bandTip(geo, row, b));
    });
    svg.addEventListener("pointerleave", () => {
      if (geo.dragging) return;
      geo.band.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
    svg.addEventListener("pointerdown", (e) => {
      const row = rowOf(e.target);
      if (row < 0 || e.button !== 0) return;
      geo.dragging = { b0: geo.binAt(e.clientX), x0: e.clientX, b1: geo.binAt(e.clientX), moved: false, row, lane: e.target.classList.contains("lane-hit") };
      try { e.target.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
      tip.hidden = true;
      geo.band.setAttribute("visibility", "hidden");
    });
    const end = (e) => {
      const d = geo.dragging;
      if (!d) return;
      geo.dragging = null;
      if (e.type === "pointercancel") return showWindow(geo);
      if (!d.moved) {
        if (d.lane) {
          const near = nearEvents(geo, d.row, e.clientX);
          if (near.length) return openLogAt(near[0].ts);
        }
        if (S.t) commit({ t: null });
        return;
      }
      const a = Math.min(d.b0, d.b1), b = Math.max(d.b0, d.b1);
      commit({ t: [a * geo.k, Math.min(NB - 1, (b + 1) * geo.k - 1)] });
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
  }
  function renderTimelineControls() {
    const modes = [["shared", "Shared", "One y-scale for every node: compare volumes"], ["node", "Per node", "Each band scaled to its own maximum: compare shapes"],
      ["pct", "% of node", "Each node's counts as a percentage of its entries in the range: compare distributions across nodes of different volume"]];
    $("tl-y").innerHTML = `<span class="lbl">y</span>` + modes.map(([k, label, title]) =>
      `<button type="button" data-y="${k}" aria-pressed="${S.y === k}" title="${esc(title)}">${label}</button>`).join("");
    const ab = $("tl-arrows");
    ab.setAttribute("aria-pressed", String(S.arrows));
    ab.hidden = !(MENT.length || EVENTS.some((e) => e.subject));
    const jb = $("tl-jobs");
    jb.setAttribute("aria-pressed", String(S.jobs));
    jb.hidden = !EVENTS.some((e) => e.kind === "job_run");
    const w = $("tl-window");
    if (!S.t) {
      w.innerHTML = NB ? `<span class="muted">Drag across any band to set the time window of every view</span>` : "";
    } else {
      const rg = tRange();
      w.innerHTML = `<span>Window <b>${esc(ts16(isoOf(rg[0])))} → ${esc(tsShort(isoOf(rg[1])))}</b> UTC (${esc(spanText(rg[1] - rg[0]))})</span>` +
        `<button type="button" class="btn small" data-clear-t>Clear</button>`;
    }
    const sel = S.sig ? GBY.get(S.sig) : null;
    $("tl-sel").innerHTML = sel
      ? `<span class="gkey" aria-hidden="true"></span><span>Selected group</span>${badge(sel.lvl, sel.level)}<span class="tpl" title="${esc(sel.tpl)}">${tplHtml(sel.tpl)}</span>` +
        `<button type="button" class="btn small toggle" data-fit aria-pressed="${S.fit}" title="Scale each band's y-axis to the selected group and hide the level columns; off: the group's line over the level columns, on their scale">Fit y to group</button>` +
        `<button type="button" class="btn small" data-unsel title="Close the group">Clear</button>`
      : "";
  }

  // ---------------------------------------------------------------- header and node cards
  const VERDICT = {
    new_errors: "new errors", new_warnings: "new warnings", new_exceptions: "new exceptions", clean: "clean", no_baseline: "no baseline",
  };
  function verdictChip(v) {
    if (v == null || v === "") return "";
    const k = str(v);
    return `<span class="vchip v-${esc(k.replace(/[^\w-]/g, ""))}"><i></i>${esc(VERDICT[k] || k.replace(/_/g, " "))}</span>`;
  }
  function renderHeader() {
    const name = CAP.cluster || CAP.capture || "cluster";
    $("c-title").textContent = name;
    $("c-capture").textContent = CAP.cluster && CAP.capture ? "capture " + CAP.capture : "";
    $("c-nodes").innerHTML = NODES.map((n) => {
      const set = setOf(n);
      const href = URLS && URLS.set_report && set.id ? urlFill(URLS.set_report, { set: set.id }) : "";
      return `<span class="nchip">${nodeBadge(n)}<span>${esc(n)}</span>` +
        (href ? `<a href="${esc(href)}" title="Report of ${esc(set.name || set.id)}">report ↗</a>` : "") + `</span>`;
    }).join("");
    const meta = [];
    if (WIN) meta.push(`${WIN_NAME}: ${ts16(isoOf(WIN[0]))} → ${tsShort(isoOf(WIN[1]))} UTC (${spanText(WIN[1] - WIN[0])})`);
    else if (WMODE) meta.push(`window: ${WMODE}`);
    meta.push(plural(NODES.length, "node"));
    const trunc_ = num(OPTS.truncated_groups);
    meta.push(trunc_ > 0 ? `showing ${fmt(ROWS.length)} of ${fmt(ROWS.length + trunc_)} groups` : plural(ROWS.length, "group"));
    for (const s of arr(CAP.skew)) {
      if (!s || !isNum(s.offset_ms)) continue;
      meta.push(`clock skew ${NLABEL(str(s.a))}↔${NLABEL(str(s.b))} ≈ ${lagText(s.offset_ms)}${isNum(s.pairs) ? ` (${plural(s.pairs, "pair")})` : ""}, estimate, not applied`);
    }
    $("c-meta").textContent = meta.join(" · ");
  }
  function evCounts(c) {
    const ev = c.events;
    if (isNum(ev)) return { total: ev, kinds: null };
    if (ev && typeof ev === "object") {
      const kinds = Object.entries(ev).filter(([, v]) => isNum(v) && v > 0).sort((a, b) => b[1] - a[1]);
      return { total: kinds.reduce((a, [, v]) => a + v, 0), kinds };
    }
    const own = EVN[c.node] || [];
    const m = new Map();
    for (const e of own) m.set(e.kind, (m.get(e.kind) || 0) + 1);
    return { total: own.length, kinds: [...m].sort((a, b) => b[1] - a[1]) };
  }
  const renderCards = guard(function renderCards() {
    const rg = tRange();
    $("c-cards").innerHTML = NODES.map((n) => {
      const c = obj(CN.get(n));
      const set = setOf(n);
      const href = URLS && URLS.set_report && set.id ? urlFill(URLS.set_report, { set: set.id }) : "";
      let entries = c.entries, errors = c.errors, warnings = c.warnings, eph = c.errors_per_hour, note = "";
      if (rg && NB) {
        entries = 0; errors = 0; warnings = 0;
        for (let i = S.t[0]; i <= S.t[1]; i++) {
          entries += NTOT[n][i];
          errors += LEV[n].ERROR[i];
          warnings += LEV[n].WARN[i];
        }
        const cov = COVT[n];
        const hrs = cov ? Math.max(0, Math.min(rg[1], cov[1]) - Math.max(rg[0], cov[0])) / 3.6e6 : 0;
        eph = hrs > 0 ? errors / hrs : null;
        note = `<span class="nc-win">in window</span>`;
      }
      const ev = evCounts({ ...c, node: n });
      const evInWin = rg ? (EVN[n] || []).filter((e) => inRange(e.ms, rg)).length : null;
      const stats = [
        ["Entries", isNum(entries) ? compact(entries) : "—", fmt(entries)],
        ["Errors", isNum(errors) ? compact(errors) : "—", fmt(errors)],
        ["Warnings", isNum(warnings) ? compact(warnings) : "—", fmt(warnings)],
        ["Errors / h", isNum(eph) ? (eph >= 100 ? fmt(Math.round(eph)) : String(r1(eph))) : "—", ""],
        ["Events", fmt(evInWin ?? ev.total), ""],
      ];
      const cov = obj(c.coverage);
      const since = cov.since ?? c.first_ts, until = cov.until ?? c.last_ts;
      const gaps = GAPS[n];
      const gapMs = gaps.reduce((a, [x, y]) => a + (y - x), 0);
      const top = c.top_error && typeof c.top_error === "object" ? c.top_error : null;
      const topRow = top ? GBY.get(str(top.signature)) : null;
      const baseline = c.baseline && typeof c.baseline === "object" ? c.baseline.name || c.baseline.id : c.baseline;
      const rows = [];
      rows.push(["Coverage", since ? `${esc(ts16(since))} → ${esc(tsShort(until))} UTC (${esc(spanText(parseTs(until) - parseTs(since)))})` +
        (gaps.length ? ` · <span title="${esc(gaps.map(([x, y]) => `${ts16(isoOf(x))} → ${tsShort(isoOf(y))} UTC`).join("\n"))}">${plural(gaps.length, "gap")} (${esc(spanText(gapMs))})</span>` : " · no gaps")
        : `<span class="muted">no entries</span>`]);
      if (top) {
        rows.push(["Top error", (topRow ? `<button type="button" class="tpl-btn" data-open="${esc(topRow.id)}" title="${esc(top.template)}">${tplHtml(trunc(top.template, 110))}</button>` : tplHtml(trunc(top.template, 110))) +
          (isNum(top.count) ? ` <span class="muted">${fmt(top.count)}</span>` : "")]);
      }
      if (c.verdict != null || baseline) {
        rows.push(["Triage", verdictChip(c.verdict) + (baseline ? `<span class="muted">vs ${esc(baseline)}</span>` : "")]);
      }
      if (ev.kinds && ev.kinds.length) {
        rows.push(["Events", ev.kinds.slice(0, 5).map(([k, v]) => `${fmt(v)} ${esc(kindLabel(k))}`).join(" · ") + (ev.kinds.length > 5 ? " …" : "")]);
      }
      return `<article class="ncard ${ncls(n)}${S.nd === n ? " is-filter" : ""}" data-node="${esc(n)}">` +
        `<div class="nc-head">${nodeBadge(n)}<b>${esc(n)}</b>` +
        (set.name || set.id ? `<span class="muted mono" title="${esc(set.id)}">${esc(set.name || set.id)}</span>` : "") +
        `<span class="grow"></span>` + (href ? `<a href="${esc(href)}">set report ↗</a>` : "") +
        `<button type="button" class="btn small toggle" data-node-filter="${esc(n)}" aria-pressed="${S.nd === n}" title="Show only groups seen on ${esc(n)}">Groups on ${esc(NLABEL(n))}</button></div>` +
        `<dl class="nc-stats">${stats.map(([k, v, t], i) => `<div title="${esc(t)}"><dt>${k}${i === 0 ? note : ""}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` +
        rows.map(([k, v]) => `<div class="nc-row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("") +
        `</article>`;
    }).join("") || `<div class="empty">This capture has no nodes.</div>`;
  });

  // ---------------------------------------------------------------- groups table
  const PAGE = 100;
  const HEAT = NODES.length > 8;
  function spreadChip(r) {
    if (r.spread === "only") {
      const n = r.present[0] || r.top || "";
      return `<span class="schip ${ncls(n)}" title="Seen only on ${esc(n)}"><i></i>only ${esc(NLABEL(n))}</span>`;
    }
    if (r.spread === "skewed") {
      return `<span class="schip ${ncls(r.top)}" title="Rate (normalised by each node's volume) is ${r.skew != null ? "×" + ratioText(r.skew) : "much"} higher on ${esc(r.top)} than on the least affected node">${r.skew != null ? "×" + esc(ratioText(r.skew)) : "skewed"} ${esc(NLABEL(r.top))}</span>`;
    }
    return `<span class="schip s-all" title="Seen on ${r.present.length === NODES.length ? "all nodes" : plural(r.present.length, "node")} at similar rates${r.skew != null ? ` (max/min ×${ratioText(r.skew)})` : ""}">${r.present.length === NODES.length ? "all" : esc(r.present.map(NLABEL).join(" "))}</span>`;
  }
  function leadChip(r) {
    if (r.first == null || r.present.length < 2) return "";
    if (r.lead == null) return "";
    if (r.lead <= 0) return `<span class="schip s-all" title="First seen at the same time on several nodes">same time</span>`;
    return `<span class="schip ${ncls(r.first)}" title="First seen on ${esc(r.first)}; the next node followed ${esc(spanText(r.lead))} later"><i></i>${esc(NLABEL(r.first))} first, ${esc(lagText(r.lead))}</span>`;
  }
  function cellBar(c) {
    if (!(c > 0)) return 0;
    const lmin = Math.log10(CMIN), span = Math.log10(CMAX) - lmin;
    return Math.max(2, span > 0 ? ((Math.log10(c) - lmin) / span) * 100 : 100);
  }
  function renderHead() {
    const th = (cls, label, key, title) => {
      const sorted = key && S.sort === key ? ` aria-sort="${key === "template" ? "ascending" : "descending"}"` : "";
      return `<th class="${cls}${key ? " sortable" : ""}"${key ? ` data-sort="${esc(key)}"` : ""}${sorted} title="${esc(title || "")}">${label}</th>`;
    };
    let cells = th("c-lv", "Level", "level", "Level") + th("c-tpl", "Template", "template", "Template") + th("c-total", "Total", "count", "Entries on all nodes");
    if (HEAT) {
      cells += `<th class="c-heat" style="--hn:${NODES.length}" title="Count per node (darker = more, log scale)"><div class="heat-head">${NODES.map((n, i) => `<span title="${esc(n)}">${i + 1}</span>`).join("")}</div></th>`;
    } else {
      for (const n of NODES) cells += th(`c-n ${ncls(n)}`, `${nodeBadge(n)}<span class="nid">${esc(trunc(n, 12))}</span>`, "node:" + n, `Count on ${n}; click to sort`);
    }
    cells += th("c-spread", "Spread", "skew", "all = on every node at similar rates; only = on one node; ×N = rate N times higher on that node (normalised by node volume)");
    cells += th("c-lead", "Lead", "lead", "Which node saw the group first, and how much later the next node did (within the window)");
    $("t-head").innerHTML = `<tr>${cells}</tr>`;
  }
  function rowHtml(r) {
    const cls = (r.id === S.sig ? "is-sel " : "") + (r.id === CUR ? "is-cur" : "");
    let nodes = "";
    if (HEAT) {
      const mx = Math.max(1, ...r.counts);
      nodes = `<td class="c-heat"><div class="heat">${NODES.map((n, i) => {
        const c = r.counts[i];
        const a = c > 0 ? r1(0.18 + 0.82 * (Math.log10(c + 1) / Math.log10(mx + 1))) : 0;
        return `<i class="${c > 0 ? "" : "z"}" style="opacity:${c > 0 ? a : 1}" title="${esc(NLABEL(n))} ${esc(n)}: ${fmt(c)}"></i>`;
      }).join("")}</div></td>`;
    } else {
      nodes = NODES.map((n, i) => {
        const c = r.counts[i];
        return `<td class="c-n ${ncls(n)}" title="${esc(NLABEL(n))} ${esc(n)}: ${fmt(c)}${isNum(obj(r.pn[n]).rate_per_hour) ? ` (${r1(r.pn[n].rate_per_hour)}/h)` : ""}">` +
          `<div class="cnt"><span class="num${c > 0 ? "" : " zero"}">${c > 0 ? fmt(c) : "·"}</span>` +
          (CMAX >= 100 ? `<span class="track"><i style="width:${r1(cellBar(c))}%"></i></span>` : "") + `</div></td>`;
      }).join("");
    }
    return `<tr data-id="${esc(r.id)}" class="${cls}">` +
      `<td class="c-lv">${badge(r.lvl, r.level)}</td>` +
      `<td class="c-tpl" title="${esc(r.tpl)}"><span class="tpl">${tplHtml(r.tpl)}</span><span class="sub">${esc(shortLogger(r.logger))}${r.exc ? " · " + esc(shortExc(r.exc)) : ""}</span></td>` +
      `<td class="c-total">${fmt(r.total)}</td>` + nodes +
      `<td class="c-spread">${spreadChip(r)}</td><td class="c-lead">${leadChip(r)}</td></tr>`;
  }
  function pagerHtml(pages) {
    if (pages <= 1) return "";
    return `<button type="button" class="btn small" data-page="${S.p - 1}" ${S.p <= 0 ? "disabled" : ""}>‹ Prev</button>` +
      `<span>Page ${S.p + 1} of ${pages}</span>` +
      `<button type="button" class="btn small" data-page="${S.p + 1}" ${S.p >= pages - 1 ? "disabled" : ""}>Next ›</button>`;
  }
  function activeFilters() {
    const out = [];
    if (S.q) out.push(["q", `Search: ${S.q}`]);
    if (!sameSet(S.lv, DEFAULT_LV)) out.push(["lv", `Levels: ${[...S.lv].join(", ") || "none"}`]);
    if (S.sp !== "any") out.push(["sp", `Spread: ${S.sp}`]);
    if (S.nd) out.push(["nd", `Node: ${NLABEL(S.nd)} ${S.nd}`]);
    if (S.t) out.push(["t", `Window: ${tsShort(isoOf(tRange()[0]))} (${spanText(tRange()[1] - tRange()[0])})`]);
    return out;
  }
  const renderTable = guard(function renderTable() {
    const pages = Math.max(1, Math.ceil(LIST.length / PAGE));
    if (S.p >= pages) S.p = pages - 1;
    const start = S.p * PAGE;
    const page = LIST.slice(start, start + PAGE);
    renderHead();
    $("t-body").innerHTML = page.map(rowHtml).join("");
    const empty = $("t-empty");
    empty.hidden = page.length > 0;
    $("t-head").hidden = !page.length;
    if (!page.length) {
      if (!ROWS.length) empty.textContent = "This capture has no groups.";
      else {
        const chips = activeFilters().map(([k, label]) =>
          `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
        empty.innerHTML = `<div>No groups match the filters.</div>` + (chips ? `<div class="fchips">${chips}</div>` : "") +
          `<button type="button" class="btn" data-clear="all">Reset filters</button>`;
      }
    }
    let entries = 0;
    for (const r of LIST) entries += r.total;
    const shown = LIST.length ? `${fmt(start + 1)}–${fmt(start + page.length)} of ` : "";
    $("t-summary").textContent = `${shown}${fmt(LIST.length)} groups · ${fmt(entries)} entries` +
      (LIST.length < ROWS.length ? ` (${fmt(ROWS.length)} in this report)` : "") +
      (num(OPTS.truncated_groups) > 0 ? ` · ${fmt(OPTS.truncated_groups)} smaller groups not in the report` : "");
    const pg = pagerHtml(pages);
    $("pager-top").innerHTML = pg;
    $("pager-bottom").innerHTML = pg;
    $("tab-g-n").textContent = fmt(LIST.length);
  });
  function renderGroupControls() {
    const lvCounts = {};
    for (const r of ROWS) if (match(r, "lv")) lvCounts[r.bucket] = (lvCounts[r.bucket] || 0) + 1;
    $("f-levels").innerHTML = BUCKETS.filter((b) => PRESENT.has(b)).map((b) =>
      `<button type="button" class="chip" data-lv="${b}" aria-pressed="${S.lv.has(b)}" title="${esc(LV_TITLE[b])}">` +
      `<i class="sw sw-${b}"></i>${b}<span class="n">${fmt(lvCounts[b] || 0)}</span></button>`).join("");
    const spCounts = { any: 0, all: 0, only: 0, skewed: 0 };
    for (const r of ROWS) {
      if (!match(r, "sp")) continue;
      spCounts.any++;
      spCounts[r.spread]++;
    }
    const spLabel = { any: "Any", all: "All", only: "Only one", skewed: "Skewed" };
    const spTitle = { any: "Any spread", all: "On every node at similar rates", only: "Seen on one node only", skewed: "Much more often on one node (normalised by node volume)" };
    $("f-spread").innerHTML = `<span class="lbl">Spread</span>` + SPREADS.map((k) =>
      `<button type="button" data-sp="${k}" aria-pressed="${S.sp === k}" title="${esc(spTitle[k])}">${spLabel[k]}<span class="n">${fmt(spCounts[k])}</span></button>`).join("");
    const ndCounts = NODES.map((n, i) => ROWS.filter((r) => r.counts[i] > 0 && match(r, "nd")).length);
    $("f-node").innerHTML = `<option value="">All nodes</option>` + NODES.map((n, i) =>
      `<option value="${esc(n)}">${esc(NLABEL(n))} · ${esc(n)} (${fmt(ndCounts[i])})</option>`).join("");
    $("f-node").value = S.nd;
    $("f-sort").innerHTML = Object.entries(SORTS).map(([k, [label]]) => `<option value="${esc(k)}">${esc(label)}</option>`).join("");
    $("f-sort").value = S.sort;
    const sq = $("f-search");
    if (document.activeElement !== sq && sq.value !== S.q) sq.value = S.q;
  }

  // ---------------------------------------------------------------- drawer
  let drawnKey = null;
  const secId = (id, title, body) => `<section class="d-sec" id="${id}"><h3>${title}</h3>${body}</section>`;
  const copyBtn = (text, label = "Copy") => `<button type="button" class="btn small" data-copy="${esc(text)}">${label}</button>`;
  const iconCopy = (text, what) =>
    `<button type="button" class="icon-btn" data-copy="${esc(text)}" title="Copy ${esc(what)}" aria-label="Copy ${esc(what)}">⧉</button>`;
  function msgHtml(message, vars) {
    message = str(message);
    let out = "", pos = 0;
    for (const v of arr(vars)) {
      if (!v) continue;
      const i = message.indexOf(v, pos);
      if (i < 0) continue;
      out += esc(message.slice(pos, i)) + `<mark>${esc(v)}</mark>`;
      pos = i + String(v).length;
    }
    return out + esc(message.slice(pos));
  }
  // Java stack traces: tokenize the plain text, escape each piece (as report.js).
  const T_FRAME = /^(\s*)at\s+(.+?)\((.*)\)\s*$/;
  const T_MORE = /^\s*\.\.\.\s*\d+\s+(more|common frames omitted)\b/;
  const T_HEAD = /^(\s*)((?:Caused by|Suppressed):\s*)?([A-Za-z_$][\w$]*(?:\.[\w$]+)*\.[A-Z][\w$]*|[A-Z][\w$]*(?:Exception|Error|Throwable))(?::\s?(.*))?$/;
  const LIB = /^(?:[\w.]*\/\/?)?(?:java\.|javax\.|jakarta\.|jdk\.|sun\.|com\.sun\.|org\.springframework\.|org\.apache\.)/;
  const FOLD_KEEP = Infinity; // every line of an entry is shown; library frames are only dimmed
  function frameHtml(ln) {
    const m = T_FRAME.exec(ln);
    const q = m[2];
    const parts = q.split(".");
    const tail = parts.length > 2 ? parts.slice(-2).join(".") : q;
    const pkg = parts.length > 2 ? parts.slice(0, -2).join(".") + "." : "";
    const app = !LIB.test(q);
    const html = `<div class="fr${app ? " fr-app" : ""}">${esc(m[1])}<span class="t-at">at </span>` +
      `<span class="t-pkg">${esc(pkg)}</span><span class="t-cm">${esc(tail)}</span>` +
      `<span class="t-loc">(<span class="t-fl">${esc(m[3])}</span>)</span></div>`;
    return { html, app };
  }
  function traceHtml(lines) {
    let html = "", frames = [];
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
      if (T_FRAME.test(ln)) { frames.push(frameHtml(ln)); continue; }
      if (T_MORE.test(ln)) {
        frames.push({ html: `<div class="fr t-more">${esc(ln)}</div>`, app: true });
        continue;
      }
      flush();
      const h = T_HEAD.exec(ln);
      if (h) {
        html += `<div class="t-head">${esc(h[1])}${h[2] ? `<span class="t-kw">${esc(h[2])}</span>` : ""}` +
          `<span class="t-exc">${esc(h[3])}</span>${h[4] != null ? `<span class="t-msg">: ${esc(h[4])}</span>` : ""}</div>`;
      } else {
        html += `<div class="ln">${esc(ln) || "&nbsp;"}</div>`;
      }
    }
    flush();
    return html;
  }
  /** One entry (report.v1 entry shape plus node/set, or an entries.v1 row) with its actions. */
  function entryHtml(id, e) {
    e = obj(e);
    const raw = str(e.raw);
    const nl = raw.indexOf("\n");
    const body = nl < 0 ? [] : raw.slice(nl + 1).replace(/\s+$/, "").split("\n");
    const meta = [["", tsFull(e.ts) + " UTC"], ["thread", e.thread], ["user", e.user], ["request", e.request_id], ["ip", e.ip], ["url", e.url]]
      .filter((x) => x[1]);
    const src = e.source_path || (typeof e.source === "string" ? e.source : "");
    const sig = str(e.signature) || ESIG.get(str(id)) || "";
    const node = str(e.node);
    const canCtx = node && e.set != null && e.ts;
    return `<article class="entry lvb-${bucketOf(levelNum(e.level_name))}"><div class="e-meta">${node ? nodeBadge(node) : ""}${badge(e.level_name, levelNum(e.level_name))}` +
      meta.map(([k, v]) => `<span>${k ? `<span class="k">${k}</span> ` : ""}<span class="v">${esc(v)}</span></span>`).join("") +
      `</div><div class="e-msg">${msgHtml(e.message, e.variables)}</div>` +
      (body.length && body.some((l) => l.trim()) ? `<pre class="e-body trace">${traceHtml(body)}</pre>` : "") +
      `<div class="e-foot">` + (src ? `<span class="mono" title="${esc(src)}">${esc(basename(src))}${e.source_line != null ? ":" + esc(e.source_line) : ""}</span>` : "") +
      (e.raw_truncated ? `<span class="muted">${URLS && URLS.examples ? "loading the full entry…" : `cut to ${fmt(raw.length)} characters in this file; all of it: atlas-log context SET ${esc(id)} --before 0 --after 0 --max-raw 0`}</span>` : "") +
      `<span class="grow"></span>` +
      (canCtx ? `<button type="button" class="btn small" data-ctx="${esc(str(e.set) + "~" + id)}" data-cts="${esc(e.ts)}" title="What every node logged within 30 s of this entry">±30 s on all nodes</button>` : "") +
      (sig && GBY.has(sig) && sig !== S.sig ? `<button type="button" class="btn small" data-open="${esc(sig)}">Open group</button>` : "") +
      `<button type="button" class="btn small" data-copy-raw="${esc(id)}">Copy raw</button></div></article>`;
  }
  function nodeTable(r) {
    const rows = NODES.map((n, i) => {
      const p = obj(r.pn[n]);
      const c = r.counts[i];
      return `<tr><td>${nodeBadge(n)} ${esc(n)}</td><td class="r">${c > 0 ? fmt(c) : "·"}</td>` +
        `<td class="r">${isNum(p.rate_per_hour) && c > 0 ? esc(r1(p.rate_per_hour)) : ""}</td>` +
        `<td>${esc(ts16(p.first_seen))}</td><td>${esc(ts16(p.last_seen))}</td></tr>`;
    }).join("");
    return `<table class="d-nodes"><thead><tr><th>Node</th><th class="r">Count</th><th class="r">Per hour</th><th>First seen (UTC)</th><th>Last seen</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  function relatedHtml(r) {
    const out = [];
    for (const c of COB) {
      const a = obj(c.a), b = obj(c.b);
      if (str(a.signature) !== r.sig && str(b.signature) !== r.sig) continue;
      out.push(`<p class="sent">${coBurstSentence(c)}</p>`);
    }
    for (const m of MENT) {
      if (!arr(m.groups).map(String).includes(r.sig)) continue;
      out.push(`<p class="sent">This group is behind the mention ${nodeBadge(str(m.from))} → ${nodeBadge(str(m.to))}: ${esc(str(m.from))} names ${esc(str(m.to))} (${plural(num(m.count), "mention")} in all).</p>`);
    }
    const evs = EVENTS.filter((e) => e.sig === r.sig);
    if (evs.length) {
      const by = new Map();
      for (const e of evs) {
        const key = e.node + "\n" + e.kind;
        by.set(key, (by.get(key) || 0) + 1);
      }
      out.push(`<p class="sent">Detected as event${evs.length === 1 ? "" : "s"}: ` + [...by].map(([key, c]) => {
        const [n, k] = key.split("\n");
        return `${fmt(c)} ${esc(kindLabel(k))} on ${nodeBadge(n)}`;
      }).join(", ") + `<button type="button" class="linkbtn act" data-at="${esc(evs[0].ts)}">show the first</button></p>`);
    }
    return out.length ? out.join("") : `<div class="muted">No co-bursts, mentions or events involve this group.</div>`;
  }
  function drawerHtml(r, inList) {
    const out = [];
    if (!inList) out.push(`<div class="d-banner">This group is not in the current filter.</div>`);
    out.push(`<div class="d-head">${badge(r.lvl, r.level)}${spreadChip(r)}${leadChip(r)}</div>`);
    out.push(`<h2 class="d-tpl" tabindex="-1">${tplHtml(r.tpl)}</h2>`);
    if (r.logger) out.push(`<div class="d-sub mono">${esc(r.logger)}</div>`);
    if (r.exc) out.push(`<div class="d-sub mono">${esc(r.exc)}</div>`);
    out.push(`<div class="d-ids mono"><span><span class="k">sig</span> ${esc(r.sig)}${iconCopy(r.sig, "signature")}</span><span><span class="k">total</span> ${fmt(r.total)}</span></div>`);
    out.push(secId("d-s-nodes", "Per node", nodeTable(r)));
    out.push(secId("d-s-hourly", `Per ${esc(binLabel(1))} and node` + (S.t ? " (window shaded)" : ""),
      `<div class="d-legend">${NODES.filter((n, i) => r.counts[i] > 0 || NODES.length <= 8).map((n) => `<span class="${ncls(n)}"><i class="lkey"></i>${esc(NLABEL(n))} ${esc(n)}</span>`).join("")}</div><div class="d-chart" id="d-hourly"></div>`));
    const exNodes = NODES.filter((n) => arr(r.examples[n]).length);
    for (const n of Object.keys(r.examples)) if (!NIDX.has(n) && arr(r.examples[n]).length) exNodes.push(n);
    let exBody;
    if (!exNodes.length) exBody = `<div class="muted">No example entries in this report.</div>`;
    else {
      const cur = exNodes.includes(S.ex) ? S.ex : exNodes[0];
      const ids = arr(r.examples[cur]).map(String);
      const have = ids.filter((id) => ENTRIES[id]);
      loadFullExamples(r, cur, ids);
      exBody = `<div class="ex-tabs" role="tablist">${exNodes.map((n) =>
        `<button type="button" role="tab" data-ex="${esc(n)}" aria-selected="${n === cur}">${nodeBadge(n)}<span class="n">${fmt(arr(r.examples[n]).length)}</span></button>`).join("")}</div>` +
        (have.length ? have.map((id) => entryHtml(id, ENTRIES[id])).join("") : `<div class="muted">The examples of ${esc(cur)} are not in this report.</div>`);
    }
    out.push(secId("d-s-examples", "Example entries per node", exBody));
    out.push(secId("d-s-links", "Co-bursts, mentions and events", relatedHtml(r)));
    const cmds = [
      `atlas-log matrix ${CAPREF} --signature ${r.sig}`,
      `atlas-log timeline ${CAPREF} --by-node --signature ${r.sig}`,
      `atlas-log entries ${CAPREF} --signature ${r.sig} --limit 50`,
    ];
    for (const n of r.present) cmds.push(`atlas-log show ${setRef(n)} ${r.sig}`);
    out.push(secId("d-s-cli", "CLI", `<div class="cli">` + cmds.map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div>`));
    return out.join("");
  }
  /** Per-node lines of one group over the timeline buckets (shared y-axis, window shaded). */
  function drawLines(el, r) {
    if (!el || !NB) return;
    const W = Math.max(240, Math.floor(el.clientWidth || 400));
    const H = 170, padL = 42, padR = 8, padT = 8, padB = 22;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const k = chooseBin(NB, plotW);
    const nb = Math.ceil(NB / k);
    const slot = plotW / nb;
    const series = NODES.map((n) => ({ n, v: binArr(denseOf(r.hourly[n]), k, nb), has: binHas(n, k, nb) }))
      .filter((s, i) => r.counts[i] > 0 || NODES.length <= 8);
    let max = 0;
    for (const s of series) for (let b = 0; b < nb; b++) max = Math.max(max, s.v[b]);
    const ticks = niceTicks(max, 3);
    const yMax = ticks[ticks.length - 1] || 1;
    const pxPerMs = slot / (BMS * k);
    const xOfMs = (ms) => padL + (ms - TA) * pxPerMs;
    const P = [`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Entries of this group per node">`];
    if (S.t) {
      const x0 = padL + Math.floor(S.t[0] / k) * slot, x1 = padL + (Math.floor(S.t[1] / k) + 1) * slot;
      P.push(`<rect class="brush" x="${r1(x0)}" y="${padT}" width="${r1(x1 - x0)}" height="${plotH}"/>`);
    }
    for (const t of ticks) {
      const y = r1(padT + plotH - (plotH * t) / yMax) + 0.5;
      if (t > 0) P.push(`<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"/>`);
      P.push(`<text class="axis" x="${padL - 6}" y="${y + 4}" text-anchor="end">${compact(t)}</text>`);
    }
    for (const [x, label] of xTicks(xOfMs, pxPerMs, W, padR)) {
      P.push(`<line class="base" x1="${r1(x) + 0.5}" x2="${r1(x) + 0.5}" y1="${padT + plotH}" y2="${padT + plotH + 4}"/>`);
      P.push(`<text class="axis" x="${r1(x)}" y="${H - 5}" text-anchor="middle">${esc(label)}</text>`);
    }
    P.push(`<line class="base" x1="${padL}" x2="${W - padR}" y1="${padT + plotH + 0.5}" y2="${padT + plotH + 0.5}"/>`);
    P.push(`<line class="crosshair" x1="0" x2="0" y1="${padT}" y2="${padT + plotH}" visibility="hidden"/>`);
    for (const s of series) {
      const pts = [];
      for (let b = 0; b < nb; b++) pts.push(s.has[b] || s.v[b] ? [padL + b * slot + slot / 2, padT + plotH - (plotH * s.v[b]) / yMax] : null);
      const { d, dots } = linePath(pts);
      P.push(`<g class="${ncls(s.n)}">` + (d ? `<path class="nline" d="${d}"/>` : "") +
        dots.map(([x, y]) => `<circle class="ndot" cx="${r1(x)}" cy="${r1(y)}" r="3.5"/>`).join("") + `</g>`);
    }
    P.push(`<rect class="hit" x="${padL}" y="0" width="${plotW}" height="${H}"/></svg>`);
    el.innerHTML = P.join("");
    const svg = el.firstElementChild;
    const hit = svg.querySelector(".hit");
    const cross = svg.querySelector(".crosshair");
    hit.addEventListener("pointermove", (e) => {
      const b = Math.max(0, Math.min(nb - 1, Math.floor((e.clientX - svg.getBoundingClientRect().left - padL) / slot)));
      const x = r1(padL + b * slot + slot / 2) + 0.5;
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      showTip(e, `<div class="tt-h">${esc(binRange(b, k))}</div>` + series.map((s) =>
        `<div class="tt-r"><b>${s.has[b] || s.v[b] ? fmt(s.v[b]) : "–"}</b>${nodeBadge(s.n)}<span>${s.has[b] || s.v[b] ? "" : "no logs"}</span></div>`).join(""));
    });
    hit.addEventListener("pointerleave", () => {
      cross.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
  }
  /** Web app: fetch a node's examples of a group in full (max_raw=0) when the payload cut them. */
  const FULL = new Set();
  function loadFullExamples(r, node, ids) {
    if (!URLS || !URLS.examples) return;
    const set = setOf(node).id || (ids.map((id) => ENTRIES[id]).find((e) => e && e.set) || {}).set;
    const key = set + "|" + r.sig;
    if (!set || FULL.has(key) || !ids.some((id) => !ENTRIES[id] || ENTRIES[id].raw_truncated)) return;
    FULL.add(key);
    fetchJson(new URL(urlFill(URLS.examples, { set, sig: r.sig }), location.href).toString())
      .then((doc) => {
        for (const [id, e] of Object.entries(obj(doc && doc.items))) ENTRIES[id] = Object.assign({}, ENTRIES[id] || {}, e, { node, set });
        drawnKey = null;
        renderDrawer();
      })
      .catch(() => null);
  }
  const renderDrawer = guard(function renderDrawer() {
    const dr = $("drawer");
    if (!S.sig || S.tab !== "g") {
      dr.hidden = true;
      document.body.classList.remove("drawer-open");
      drawnKey = null;
      return;
    }
    dr.hidden = false;
    document.body.classList.add("drawer-open");
    const idx = LIST.findIndex((x) => x.id === S.sig);
    $("d-pos").textContent = idx >= 0 ? `${fmt(idx + 1)} of ${fmt(LIST.length)}` : `– of ${fmt(LIST.length)}`;
    const key = [S.sig, S.t, S.ex, idx >= 0].join("|");
    if (key === drawnKey) return;
    const sameGroup = drawnKey && drawnKey.split("|")[0] === S.sig;
    drawnKey = key;
    const r = GBY.get(S.sig);
    const body = $("drawer-body");
    const top = body.scrollTop;
    body.innerHTML = r ? drawerHtml(r, idx >= 0) : `<div class="empty">Group ${esc(S.sig)} is not in this report.</div>`;
    body.scrollTop = sameGroup ? top : 0;
    if (r) drawLines($("d-hourly"), r);
  });

  // ---------------------------------------------------------------- interactions
  const tplRef = (sig, tpl) => {
    const g = GBY.get(str(sig));
    const text = tplHtml(trunc(tpl || (g && g.tpl) || sig, 140));
    return g ? `<button type="button" class="tplref" data-open="${esc(g.id)}" title="${esc(g.tpl)}">${text}</button>` : `<b>${text}</b>`;
  };
  function coBurstSentence(c) {
    const a = obj(c.a), b = obj(c.b);
    const extra = [isNum(c.median_lag_ms) ? `median ${lagText(c.median_lag_ms)}` : "", isNum(c.lift) ? `lift ×${ratioText(c.lift)}` : ""].filter(Boolean);
    const ms = [a.signature, b.signature].filter((s) => s != null).map(String);
    const mn = [a.node, b.node].filter((n) => NIDX.has(str(n))).map(String);
    return `When ${tplRef(a.signature, a.template)} burst on ${nodeBadge(str(a.node))}, ${tplRef(b.signature, b.template)} burst on ${nodeBadge(str(b.node))} ` +
      `within ${esc(spanText(DELTA_S * 1000))}, <b>${fmt(num(c.hits))} of ${fmt(num(c.of))}</b> times` +
      (extra.length ? ` (${esc(extra.join(", "))})` : "") + `.` +
      `<button type="button" class="linkbtn act" data-show='${esc(JSON.stringify({ ms, mn }))}'>show me</button>`;
  }
  function pairSentence(p) {
    const a = obj(p.a), b = obj(p.b);
    const ex = arr(p.examples).filter((x) => Array.isArray(x) && x[0]);
    const rg = tRange();
    const inWin = rg ? ex.filter((x) => inRange(parseTs(x[0]), rg)) : ex;
    return `<b>${esc(kindLabel(a.kind))}</b> on ${nodeBadge(str(a.node))} was followed by <b>${esc(kindLabel(b.kind))}</b> on ${nodeBadge(str(b.node))}` +
      ` <b>${fmt(num(p.count))} of ${fmt(num(p.of))}</b> times` + (isNum(p.median_lag_ms) ? ` (median ${esc(lagText(p.median_lag_ms))})` : "") + `.` +
      (inWin.length ? inWin.slice(0, 3).map((x) => `<button type="button" class="linkbtn act" data-at="${esc(x[0])}" title="${esc(tsMs(x[0]))} → ${esc(tsMs(x[1]))}">${esc(tsSec(x[0]))}</button>`).join("")
        : ex.length && rg ? ` <span class="muted">(no example in the window)</span>` : "");
  }
  const renderInteractions = guard(function renderInteractions() {
    const el = $("p-inter");
    const rg = tRange();
    const cards = [];
    // mention matrix
    const M = new Map();
    for (const m of MENT) M.set(str(m.from) + "\n" + str(m.to), m);
    let mmax = 0;
    for (const m of MENT) mmax = Math.max(mmax, num(m.count));
    let mx = `<div class="muted">No node logged about another node.</div>`;
    if (MENT.length && NODES.length > 1) {
      mx = `<table class="mx"><thead><tr><th class="mx-corner rh">from ↓ · to →</th>${NODES.map((n) => `<th>${nodeBadge(n)}</th>`).join("")}</tr></thead><tbody>` +
        NODES.map((a) => `<tr><th class="rh">${nodeBadge(a)}</th>` + NODES.map((b) => {
          if (a === b) return `<td><span class="cell diag" title="${esc(a)}">—</span></td>`;
          const m = M.get(a + "\n" + b);
          const c = m ? num(m.count) : 0;
          if (!c) return `<td><span class="cell zero" title="${esc(a)} never names ${esc(b)}">·</span></td>`;
          const alpha = r1(0.08 + 0.3 * (Math.log10(c + 1) / Math.log10(mmax + 1)));
          return `<td><button type="button" class="cell" style="--a:${alpha}" data-mx="${esc(a + "\n" + b)}" title="${esc(a)} names ${esc(b)} ${fmt(c)} times in ${plural(arr(m.groups).length, "group")}; click to list the entries"><span>${fmt(c)}</span></button></td>`;
        }).join("") + `</tr>`).join("") + `</tbody></table>`;
      mx += `<ul class="edges">` + [...MENT].sort((x, y) => num(y.count) - num(x.count)).map((m) =>
        `<li><div class="e-head">${nodeBadge(str(m.from))} → ${nodeBadge(str(m.to))}<span>${plural(num(m.count), "mention")} in ${plural(arr(m.groups).length, "group")}</span>` +
        `<button type="button" class="linkbtn" data-mx="${esc(str(m.from) + "\n" + str(m.to))}">show entries</button></div>` +
        `<div class="e-groups">${arr(m.groups).slice(0, 6).map((s) => `<span>${tplRef(s)}</span>`).join("")}${arr(m.groups).length > 6 ? `<span class="muted">+${fmt(arr(m.groups).length - 6)} more</span>` : ""}</div></li>`).join("") + `</ul>`;
    } else if (NODES.length < 2) {
      mx = `<div class="muted">A single node: no mentions between nodes.</div>`;
    }
    cards.push(`<div class="card"><h3>Mentions <span class="n">${fmt(MENT.length)}</span></h3>${mx}<p class="note">An entry on one node naming another member node. Counts cover the whole capture window.</p></div>`);
    // event pairs + co-bursts
    cards.push(`<div class="card"><h3>Event pairs <span class="n">${fmt(PAIRS.length)}</span></h3>` +
      (PAIRS.length ? PAIRS.map((p) => `<p class="sent">${pairSentence(p)}</p>`).join("") : `<div class="muted">No events on one node were followed by events on another.</div>`) +
      `<h3 style="margin-top:14px">Co-bursts <span class="n">${fmt(COB.length)}</span></h3>` +
      (COB.length ? COB.map((c) => `<p class="sent">${coBurstSentence(c)}</p>`).join("") : `<div class="muted">No group bursts on one node were followed by bursts on another more often than chance.</div>`) +
      `<p class="note">A burst starts at the first minute at or above the threshold after at least 10 quiet minutes; lift compares the hits with chance.</p></div>`);
    // jobs + sessions
    const jobs = JOBS.length ? JOBS.map((j) => {
      const ex = arr(j.examples).filter(Boolean);
      const shown = rg ? ex.filter((t) => inRange(parseTs(t), rg)) : ex;
      return `<p class="sent"><b class="mono">${esc(j.job)}</b> ran on ${arr(j.nodes).map((n) => nodeBadge(str(n))).join(" ")} in the same slot` +
        (isNum(j.slots) ? `, <b>${plural(j.slots, "time")}</b>` : "") + `.` +
        shown.slice(0, 4).map((t) => `<button type="button" class="linkbtn act" data-at="${esc(t)}">${esc(tsSec(t))}</button>`).join("") + `</p>`;
    }).join("") : `<div class="muted">No scheduled job ran on several nodes at the same time.</div>`;
    const sess = SESS.length ? SESS.map((s) => {
      const ns = arr(s.nodes).map(String);
      return `<p class="sent"><b>${plural(num(s.count), "session")}</b> seen on ${ns.map(nodeBadge).join(" and ")} within an hour: a sticky session was lost or a node failed over.` +
        arr(s.examples).slice(0, 3).map((x) => {
          const t = Array.isArray(x) ? x[0] : obj(x).ts || x;
          return typeof t === "string" ? `<button type="button" class="linkbtn act" data-at="${esc(t)}">${esc(tsSec(t))}</button>` : "";
        }).join("") + `</p>`;
    }).join("") : `<div class="muted">No session was seen on more than one node.</div>`;
    cards.push(`<div class="card"><h3>Concurrent jobs <span class="n">${fmt(JOBS.length)}</span></h3>${jobs}` +
      `<h3 style="margin-top:14px">Sessions across nodes <span class="n">${fmt(SESS.length)}</span></h3>${sess}</div>`);
    // events table (the table view of the event lanes)
    const evs = rg ? EVENTS.filter((e) => inRange(e.ms, rg)) : EVENTS;
    const LIMIT = 300;
    cards.push(`<div class="card wide"><h3>Events <span class="n">${fmt(evs.length)}${rg ? " in the window" : ""}</span></h3>` +
      (evs.length ? `<table class="ev-table"><thead><tr><th class="c-ts">Time (UTC)</th><th class="c-nd">Node</th><th class="c-kd">Kind</th><th class="c-sb">Names</th><th>Text</th></tr></thead><tbody>` +
        evs.slice(0, LIMIT).map((e) => `<tr data-at="${esc(e.ts)}" title="Open the merged log at this moment"><td class="c-ts">${esc(tsMs(e.ts))}</td><td class="c-nd">${nodeBadge(e.node)}</td>` +
          `<td class="c-kd">${glyphKey(e.kind)} ${esc(kindLabel(e.kind))}</td><td class="c-sb">${e.subject ? nodeBadge(e.subject) : ""}</td><td title="${esc(e.text)}">${esc(e.text)}</td></tr>`).join("") +
        `</tbody></table>` + (evs.length > LIMIT ? `<p class="note">First ${fmt(LIMIT)} of ${fmt(evs.length)}; narrow the window to see the rest.</p>` : "")
        : `<div class="muted">No cluster events${rg ? " in the window" : ""}.</div>`) + `</div>`);
    el.innerHTML = cards.join("");
    $("tab-i-n").textContent = fmt(MENT.length + PAIRS.length + COB.length + JOBS.length + SESS.length);
  });

  // ---------------------------------------------------------------- merged log
  const CHUNK = 200;
  const LOG = { key: "", rows: [], next: null, total: null, loading: false, error: "", shown: CHUNK, cur: -1, open: new Set(), atDone: "", ctxRows: null, ctxKey: "" };
  function logRange() {
    if (S.at) {
      const at = parseTs(S.at);
      if (isNum(at)) return [at - 60000, tRange() ? tRange()[1] : WIN ? Math.max(WIN[1], at + 60000) : at + 3.6e6];
    }
    return tRange() || (WIN ? [WIN[0], WIN[1] + 1] : null);
  }
  function webKey() {
    const rg = logRange();
    return [rg ? rg[0] : "", rg ? rg[1] : "", S.ml].join("|");
  }
  function fetchJson(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`GET ${url}: HTTP ${res.status}`))));
  }
  function fetchLogPage() {
    if (!WEB_LOG || LOG.loading) return;
    const key = LOG.key;
    const u = new URL(URLS.entries, location.href);
    const rg = logRange();
    if (rg) {
      u.searchParams.set("since", isoOf(rg[0]));
      u.searchParams.set("until", isoOf(rg[1]));
    }
    if (S.ml) u.searchParams.set("min_level", S.ml);
    u.searchParams.set("limit", String(CHUNK));
    u.searchParams.set("offset", String(LOG.next || 0));
    LOG.loading = true;
    LOG.error = "";
    renderLog();
    fetchJson(u.toString()).then((doc) => {
      if (key !== LOG.key) return;
      doc = obj(doc);
      const items = arr(doc.items).map((e) => normEntry(obj(e).id, e)).filter((r) => isNum(r.ms));
      const off = LOG.next || 0;
      LOG.rows = LOG.rows.concat(items);
      LOG.total = isNum(doc.total) ? doc.total : LOG.total;
      LOG.next = doc.next_offset != null ? doc.next_offset : doc.truncated ? off + items.length : null;
    }).catch((err) => {
      if (key === LOG.key) LOG.error = String(err && err.message ? err.message : err);
    }).finally(() => {
      if (key !== LOG.key) return;
      LOG.loading = false;
      renderLog();
    });
  }
  function fetchContext() {
    const [set, id] = S.ctx.split("~");
    const key = S.ctx;
    LOG.ctxKey = key;
    LOG.ctxRows = null;
    LOG.loading = true;
    LOG.error = "";
    renderLog();
    fetchJson(new URL(urlFill(URLS.context, { set, id }), location.href).toString()).then((doc) => {
      if (LOG.ctxKey !== key) return;
      LOG.ctxRows = arr(obj(doc).items).map((e) => normEntry(obj(e).id, e)).filter((r) => isNum(r.ms)).sort(byTs);
    }).catch((err) => {
      if (LOG.ctxKey === key) LOG.error = String(err && err.message ? err.message : err);
    }).finally(() => {
      if (LOG.ctxKey !== key) return;
      LOG.loading = false;
      renderLog();
    });
  }
  /** Rows of the merged log after the filters (or the ±30 s context). */
  function logRows() {
    if (S.ctx) {
      const at = parseTs(S.cts);
      if (URLS && URLS.context && WEB_LOG) return LOG.ctxRows || [];
      const pool = WEB_LOG ? LOG.rows.concat(STATIC_LOG) : STATIC_LOG;
      const seen = new Set();
      return pool.filter((r) => Math.abs(r.ms - at) <= 30000 && !seen.has(r.id) && seen.add(r.id)).sort(byTs);
    }
    const base = WEB_LOG ? LOG.rows : STATIC_LOG;
    const rg = WEB_LOG ? null : tRange();
    const minLv = levelNum(S.ml);
    const q = S.mq.trim().toLowerCase();
    const sigs = S.ms.length ? new Set(S.ms) : null;
    return base.filter((r) => (!rg || inRange(r.ms, rg)) && (!minLv || r.lvl >= minLv) && (!S.mn || S.mn.has(r.node)) &&
      (!q || r.key.includes(q)) && (!sigs || sigs.has(r.sig)));
  }
  function mlRowHtml(r, i) {
    const open = LOG.open.has(r.id);
    const cls = (open ? " is-open" : "") + (i === LOG.cur ? " is-cur" : "") +
      (S.ctx && S.ctx.split("~")[1] === r.id ? " is-anchor" : "") + (LOG.atId === r.id ? " is-at" : "");
    return `<div class="ml-row lvb-${r.bucket}${cls}" data-i="${i}" role="listitem">` +
      `<div class="ml-line" title="${open ? "Collapse" : "Show the raw entry"}"><span class="ml-ts">${esc(tsMs(r.ts))}</span>${nodeBadge(r.node)}${badge(r.level_name, r.lvl)}` +
      `<span class="ml-lg" title="${esc(r.logger)}">${esc(shortLogger(r.logger))}</span><span class="ml-msg">${esc(r.message)}</span></div>` +
      (open ? `<div class="ml-detail">${entryHtml(r.id, { ...r.e, node: r.node, set: r.set, ts: r.ts, signature: r.sig })}</div>` : "") + `</div>`;
  }
  let VIEW = [];
  const renderLog = guard(function renderLog() {
    $("tab-l-n").textContent = fmt(!WEB_LOG ? STATIC_LOG.length : S.ctx ? (LOG.ctxRows || []).length : LOG.rows.length);
    if (S.tab !== "l") return;
    // web mode: (re)load when the range or level changes
    if (WEB_LOG && !S.ctx) {
      const key = webKey();
      if (key !== LOG.key) {
        LOG.key = key;
        LOG.rows = [];
        LOG.next = null;
        LOG.total = null;
        LOG.loading = false;
        LOG.shown = CHUNK;
        LOG.cur = -1;
        fetchLogPage();
        return;
      }
    }
    if (S.ctx && URLS && URLS.context && WEB_LOG && LOG.ctxKey !== S.ctx) {
      fetchContext();
      return;
    }
    VIEW = logRows();
    // jump to a moment (event click)
    LOG.atId = "";
    if (S.at && !S.ctx) {
      const at = parseTs(S.at);
      const i = VIEW.findIndex((r) => r.ms >= at - 1000);
      if (i >= 0) {
        LOG.atId = VIEW[i].id;
        if (LOG.shown < i + 20) LOG.shown = i + CHUNK;
      }
    }
    const shown = VIEW.slice(0, LOG.shown);
    const ml = $("ml");
    ml.innerHTML = shown.map(mlRowHtml).join("") ||
      `<div class="empty">${LOG.loading ? "Loading…" : S.ctx ? "No entries within ±30 s in this report." : "No entries match."}</div>`;
    // notes, chips, footer
    const notes = [];
    if (!WEB_LOG) {
      notes.push(`Static report: these are the ${fmt(STATIC_LOG.length)} example entries of all nodes, interleaved by time. The full log: <code>${esc(`atlas-log entries ${CAPREF}` + (logRange() ? ` --since ${isoOf(logRange()[0])} --until ${isoOf(logRange()[1])}` : ""))}</code>`);
    }
    if (S.ctx) {
      notes.push(`±30 s around ${esc(tsFull(S.cts))} UTC on all nodes${WEB_LOG && URLS.context ? "" : " (from the entries in this report)"}. <button type="button" class="linkbtn" data-ctx-close>Back to the log</button>`);
    }
    if (LOG.error) notes.push(`Could not load entries: ${esc(LOG.error)}`);
    $("l-summary").innerHTML = (notes.length ? notes.map((n) => `<div class="ml-note">${n}</div>`).join("") : "") +
      `<span>${plural(VIEW.length, "entry", "entries")}` +
      (WEB_LOG && !S.ctx ? ` · ${fmt(LOG.rows.length)} loaded${LOG.total != null ? ` of ${fmt(LOG.total)} in the range` : ""}` +
        (S.mn || S.mq || S.ms.length ? " (node, search and group filters apply to the loaded entries)" : "") : "") + `</span>`;
    const chips = [];
    if (S.ms.length) chips.push(["ms", `${plural(S.ms.length, "group")}: ${S.ms.map((s) => trunc((GBY.get(s) || {}).tpl || s, 40)).join(" · ")}`]);
    if (S.mn) chips.push(["mn", `Nodes: ${[...S.mn].map(NLABEL).join(", ")}`]);
    if (S.at) chips.push(["at", `From ${tsSec(S.at)}`]);
    if (S.t) chips.push(["t", `Window ${tsShort(isoOf(tRange()[0]))} (${spanText(tRange()[1] - tRange()[0])})`]);
    $("l-active").innerHTML = chips.map(([k, label]) => `<button type="button" class="fchip" data-lclear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
    const foot = [];
    if (VIEW.length > LOG.shown) foot.push(`<button type="button" class="btn small" data-more>Show ${fmt(Math.min(CHUNK, VIEW.length - LOG.shown))} more</button><span>${fmt(LOG.shown)} of ${fmt(VIEW.length)} shown</span>`);
    if (WEB_LOG && !S.ctx) {
      if (LOG.loading) foot.push(`<span>Loading…</span>`);
      else if (LOG.next != null) foot.push(`<button type="button" class="btn small" data-load>Load ${fmt(CHUNK)} more from the server</button>`);
    }
    $("ml-foot").innerHTML = foot.join("");
    if (LOG.atId && LOG.atDone !== S.at) {
      LOG.atDone = S.at;
      const row = ml.querySelector(".is-at");
      if (row) requestAnimationFrame(() => row.scrollIntoView({ block: "center" }));
    }
  });
  function renderLogControls() {
    $("l-level").innerHTML = `<span class="lbl">Min level</span>` + LOG_LEVELS.map((l) =>
      `<button type="button" data-ml="${l}" aria-pressed="${S.ml === l}">${l || "All"}</button>`).join("");
    $("l-nodes").innerHTML = NODES.map((n) =>
      `<button type="button" class="chip ${ncls(n)}" data-mn="${esc(n)}" aria-pressed="${!S.mn || S.mn.has(n)}" title="${esc(n)}"><i class="sw sw-node"></i>${esc(NLABEL(n))}</button>`).join("");
    const lq = $("l-search");
    if (document.activeElement !== lq && lq.value !== S.mq) lq.value = S.mq;
  }
  function openLogAt(ts) {
    if (!ts) return;
    commit({ tab: "l", at: str(ts), ctx: "", cts: "", ms: [], mn: null, mq: "" }, { keepPage: true });
    scrollToTabs();
  }
  function scrollToTabs() {
    const bar = $("filters");
    if (bar.getBoundingClientRect().top > window.innerHeight * 0.6 || bar.getBoundingClientRect().top < 0) {
      bar.scrollIntoView({ block: "start" });
    }
  }

  // ---------------------------------------------------------------- update
  function renderTabs() {
    for (const t of TABS) {
      const b = $("tab-" + t);
      b.setAttribute("aria-selected", String(S.tab === t));
      b.tabIndex = S.tab === t ? 0 : -1;
    }
    $("p-groups").hidden = S.tab !== "g";
    $("p-inter").hidden = S.tab !== "i";
    $("p-log").hidden = S.tab !== "l";
    $("fg-row").hidden = S.tab !== "g";
    $("fl-row").hidden = S.tab !== "l";
    const af = activeFilters();
    $("f-print").textContent = af.length ? "Filters: " + af.map((x) => x[1]).join(" · ") : "";
  }
  let lastTl = "";
  function update(forceTl) {
    compute();
    renderTabs();
    renderTimelineControls();
    const tlKey = [S.y, S.fit, S.sig, S.arrows, S.jobs].join("|");
    if (forceTl || tlKey !== lastTl) {
      lastTl = tlKey;
      renderTimeline();
    } else {
      showWindow(TLG);
      if (TLG) TLG.svg.classList.toggle("arrows-off", !S.arrows);
    }
    guard(renderGroupControls)();
    renderTable();
    renderDrawer();
    renderCards();
    renderInteractions();
    guard(renderLogControls)();
    renderLog();
    updateSticky();
  }
  function updateSticky() {
    const f = $("filters");
    const h = getComputedStyle(f).position === "sticky" ? f.offsetHeight : 0;
    document.documentElement.style.setProperty("--sticky-top", h + "px");
  }

  // ---------------------------------------------------------------- actions
  let toastTimer = 0;
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 1400);
  }
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
      return;
    } catch {
      /* fall back below */
    }
    const prev = document.activeElement;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.readOnly = true;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    toast(ok ? "Copied" : "Copy failed");
    ta.remove();
    if (prev && prev.focus) prev.focus();
  }
  const isDark = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    return cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  };
  function renderThemeBtn() {
    const b = $("btn-theme");
    b.innerHTML = isDark() ? `<span aria-hidden="true">☀</span> Light` : `<span aria-hidden="true">☾</span> Dark`;
    b.title = isDark() ? "Switch to the light theme" : "Switch to the dark theme";
  }
  function toggleTheme() {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("atlas-log-report-theme", next); } catch { /* storage unavailable */ }
    renderThemeBtn();
    if (S.theme) commit({ theme: next }, { keepPage: true, replace: true });
  }
  function open(id) {
    CUR = id;
    const was = !!S.sig && S.tab === "g";
    commit({ sig: id, tab: "g" }, { keepPage: true });
    if (!was) $("d-close").focus({ preventScroll: true });
  }
  function openFromElsewhere(id) {
    const i = LIST.findIndex((r) => r.id === id);
    CUR = id;
    commit({ sig: id, tab: "g", p: i >= 0 ? Math.floor(i / PAGE) : S.p }, { keepPage: true });
    scrollToTabs();
  }
  function move(d) {
    if (!LIST.length) return;
    const curId = S.sig || CUR;
    let i = LIST.findIndex((r) => r.id === curId);
    i = i < 0 ? (d > 0 ? 0 : LIST.length - 1) : Math.max(0, Math.min(LIST.length - 1, i + d));
    const r = LIST[i];
    CUR = r.id;
    const p = Math.floor(i / PAGE);
    if (S.sig) commit({ sig: r.id, p }, { keepPage: true, replace: true });
    else if (p !== S.p) commit({ p }, { keepPage: true, replace: true });
    else for (const tr of $("t-body").rows) tr.classList.toggle("is-cur", tr.dataset.id === CUR);
    const tr = [...$("t-body").rows].find((x) => x.dataset.id === r.id);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  }
  function moveLog(d) {
    if (!VIEW.length) return;
    LOG.cur = LOG.cur < 0 ? (d > 0 ? 0 : Math.min(VIEW.length, LOG.shown) - 1) : Math.max(0, Math.min(VIEW.length - 1, LOG.cur + d));
    if (LOG.cur >= LOG.shown) LOG.shown = LOG.cur + CHUNK;
    renderLog();
    const row = $("ml").querySelector(".is-cur");
    if (row) row.scrollIntoView({ block: "nearest" });
  }
  function toggleRow(i) {
    const r = VIEW[i];
    if (!r) return;
    if (LOG.open.has(r.id)) LOG.open.delete(r.id); else LOG.open.add(r.id);
    LOG.cur = i;
    renderLog();
  }
  function resetAll() {
    clearTimeout(qTimer);
    $("f-search").value = "";
    $("l-search").value = "";
    commit({ q: "", lv: new Set(DEFAULT_LV), sp: "any", nd: "", sort: "count", t: null, sig: "", ex: "", ml: "", mn: null, mq: "", ms: [], ctx: "", cts: "", at: "" });
  }
  const CLEAR = { q: { q: "" }, sp: { sp: "any" }, nd: { nd: "" }, t: { t: null } };
  function clearFilter(k) {
    if (k === "all") return resetAll();
    if (k === "lv") return commit({ lv: new Set(DEFAULT_LV) });
    if (k === "q") $("f-search").value = "";
    if (CLEAR[k]) commit(CLEAR[k]);
  }
  /** Clicks shared by every panel: open a group, jump to the log, show a context, copy, unfold. */
  function onCommonClick(e) {
    const t = e.target.closest("[data-open],[data-at],[data-ctx],[data-show],[data-mx],[data-copy],[data-copy-raw],[data-unfold],[data-ex],[data-node-filter]");
    if (!t) return false;
    const d = t.dataset;
    if (d.unfold != null) {
      const f = t.closest(".fold");
      const opened = f.classList.toggle("open");
      const n = +d.unfold;
      t.textContent = opened ? "hide frames" : `show ${fmt(n)} more frame${n === 1 ? "" : "s"}`;
    } else if (d.copy != null) copy(d.copy);
    else if (d.copyRaw != null) {
      const id = d.copyRaw;
      const row = ENTRIES[id] || (VIEW.find((r) => r.id === id) || {}).e || {};
      copy(str(row.raw));
    } else if (d.ctx != null) {
      commit({ tab: "l", ctx: d.ctx, cts: d.cts || "", at: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.at != null) openLogAt(d.at);
    else if (d.show != null) {
      let spec = {};
      try { spec = JSON.parse(d.show); } catch { spec = {}; }
      const mn = arr(spec.mn).filter((n) => NIDX.has(n));
      commit({ tab: "l", ms: arr(spec.ms).map(String), mn: mn.length ? new Set(mn) : null, at: "", ctx: "", cts: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.mx != null) {
      const [from, to] = d.mx.split("\n");
      const m = MENT.find((x) => str(x.from) === from && str(x.to) === to);
      commit({ tab: "l", ms: m ? arr(m.groups).map(String) : [], mn: NIDX.has(from) ? new Set([from]) : null, at: "", ctx: "", cts: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.ex != null) commit({ ex: d.ex }, { keepPage: true, replace: true });
    else if (d.nodeFilter != null) commit({ nd: S.nd === d.nodeFilter ? "" : d.nodeFilter, tab: "g" });
    else if (d.open != null) {
      if (t.closest("#drawer")) open(d.open);
      else openFromElsewhere(d.open);
    }
    return true;
  }

  function bind() {
    $("c-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tab]");
      if (b) commit({ tab: b.dataset.tab }, { keepPage: true });
    });
    $("c-tabs").addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const i = TABS.indexOf(S.tab);
      const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
      commit({ tab: next }, { keepPage: true });
      $("tab-" + next).focus();
    });
    $("f-search").addEventListener("input", (e) => {
      clearTimeout(qTimer);
      const value = e.target.value;
      qTimer = setTimeout(() => commit({ q: value }, { replace: true }), 90);
    });
    $("l-search").addEventListener("input", (e) => {
      clearTimeout(qTimer);
      const value = e.target.value;
      qTimer = setTimeout(() => commit({ mq: value }, { replace: true, keepPage: true }), 120);
    });
    $("f-levels").addEventListener("click", (e) => {
      const b = e.target.closest("[data-lv]");
      if (!b) return;
      const lv = new Set(S.lv);
      const k = b.dataset.lv;
      if (e.altKey || e.shiftKey) {
        lv.clear();
        lv.add(k);
      } else if (lv.has(k)) lv.delete(k);
      else lv.add(k);
      commit({ lv });
    });
    $("f-spread").addEventListener("click", (e) => {
      const b = e.target.closest("[data-sp]");
      if (b) commit({ sp: b.dataset.sp });
    });
    $("f-node").onchange = (e) => commit({ nd: e.target.value });
    $("f-sort").onchange = (e) => commit({ sort: e.target.value });
    $("f-reset").onclick = resetAll;
    $("t-empty").addEventListener("click", (e) => {
      const b = e.target.closest("[data-clear]");
      if (b) clearFilter(b.dataset.clear);
    });
    $("t-head").addEventListener("click", (e) => {
      const th = e.target.closest("[data-sort]");
      if (th) commit({ sort: th.dataset.sort });
    });
    $("t-body").addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (tr) open(tr.dataset.id);
    });
    const pagerClick = (e) => {
      const b = e.target.closest("[data-page]");
      if (b && !b.disabled) {
        commit({ p: +b.dataset.page }, { keepPage: true });
        $("table-wrap").scrollIntoView({ block: "start" });
      }
    };
    $("pager-top").addEventListener("click", pagerClick);
    $("pager-bottom").addEventListener("click", pagerClick);
    $("d-close").onclick = () => commit({ sig: "" }, { keepPage: true });
    $("d-prev").onclick = () => move(-1);
    $("d-next").onclick = () => move(1);
    $("d-nav").addEventListener("click", (e) => {
      const b = e.target.closest("[data-jump]");
      const t = b && document.getElementById(b.dataset.jump);
      if (t) t.scrollIntoView({ block: "start", behavior: "smooth" });
    });
    for (const id of ["drawer-body", "c-cards", "p-inter", "p-log", "tl-sel", "tl-window", "c-timeline"]) {
      $(id).addEventListener("click", (e) => {
        if (id === "tl-sel") {
          if (e.target.closest("[data-fit]")) return commit({ fit: !S.fit }, { keepPage: true, replace: true });
          if (e.target.closest("[data-unsel]")) return commit({ sig: "" }, { keepPage: true });
        }
        if (id === "tl-window" && e.target.closest("[data-clear-t]")) return commit({ t: null });
        if (id === "p-log") {
          if (e.target.closest("[data-more]")) { LOG.shown += CHUNK; return renderLog(); }
          if (e.target.closest("[data-load]")) return fetchLogPage();
          if (e.target.closest("[data-ctx-close]")) return commit({ ctx: "", cts: "" }, { keepPage: true });
          const lc = e.target.closest("[data-lclear]");
          if (lc) {
            const k = lc.dataset.lclear;
            return commit(k === "t" ? { t: null } : k === "mn" ? { mn: null } : k === "at" ? { at: "" } : { ms: [] }, { keepPage: true });
          }
        }
        if (onCommonClick(e)) return;
        if (id === "p-log") {
          const line = e.target.closest(".ml-line");
          if (line && !window.getSelection().toString()) toggleRow(+line.parentElement.dataset.i);
        }
      });
    }
    $("tl-y").addEventListener("click", (e) => {
      const b = e.target.closest("[data-y]");
      if (b) commit({ y: b.dataset.y }, { keepPage: true, replace: true });
    });
    $("tl-arrows").onclick = () => commit({ arrows: !S.arrows }, { keepPage: true, replace: true });
    $("tl-jobs").onclick = () => commit({ jobs: !S.jobs }, { keepPage: true, replace: true });
    $("l-level").addEventListener("click", (e) => {
      const b = e.target.closest("[data-ml]");
      if (b) commit({ ml: b.dataset.ml }, { keepPage: true });
    });
    $("l-nodes").addEventListener("click", (e) => {
      const b = e.target.closest("[data-mn]");
      if (!b) return;
      const n = b.dataset.mn;
      let mn = new Set(S.mn || NODES);
      if (e.altKey || e.shiftKey) mn = new Set([n]);
      else if (mn.has(n)) mn.delete(n);
      else mn.add(n);
      commit({ mn: mn.size === NODES.length || !mn.size ? null : mn }, { keepPage: true });
    });
    $("btn-theme").onclick = toggleTheme;
    window.atlasTheme = toggleTheme;
    renderThemeBtn();
    $("btn-data").onclick = () => {
      const blob = new Blob([JSON.stringify(D)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (CAP.cluster || "cluster") + "_" + (CAP.capture || "capture") + ".cluster-report.json";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    };
    const help = $("help");
    $("btn-help").onclick = () => help.showModal();
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      const typing = t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement;
      if (e.key === "Escape") {
        if (help.open) return;
        if (typing) { t.blur(); return; }
        if (S.tab === "g" && S.sig) commit({ sig: "" }, { keepPage: true });
        else if (S.tab === "l" && S.ctx) commit({ ctx: "", cts: "" }, { keepPage: true });
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || help.open) return;
      switch (e.key) {
        case "/": {
          e.preventDefault();
          const inp = S.tab === "l" ? $("l-search") : $("f-search");
          if (S.tab === "i") commit({ tab: "g" }, { keepPage: true });
          inp.focus();
          inp.select();
          break;
        }
        case "j": e.preventDefault(); if (S.tab === "l") moveLog(1); else if (S.tab === "g") move(1); break;
        case "k": e.preventDefault(); if (S.tab === "l") moveLog(-1); else if (S.tab === "g") move(-1); break;
        case "Enter":
          if (S.tab === "l" && LOG.cur >= 0) toggleRow(LOG.cur);
          else if (S.tab === "g" && CUR && !S.sig) open(CUR);
          break;
        case "c": {
          const r = GBY.get(S.sig || CUR);
          if (r) copy(r.sig);
          break;
        }
        case "n": if (S.tab === "g" && (S.p + 1) * PAGE < LIST.length) commit({ p: S.p + 1 }, { keepPage: true }); break;
        case "p": if (S.tab === "g" && S.p > 0) commit({ p: S.p - 1 }, { keepPage: true }); break;
        case "1": commit({ tab: "g" }, { keepPage: true }); break;
        case "2": commit({ tab: "i" }, { keepPage: true }); break;
        case "3": commit({ tab: "l" }, { keepPage: true }); break;
        case "?": help.showModal(); break;
        default:
      }
    });
    let rz = 0;
    window.addEventListener("resize", () => {
      clearTimeout(rz);
      rz = setTimeout(() => {
        renderTimeline();
        const r = S.sig && GBY.get(S.sig);
        if (r && $("d-hourly")) drawLines($("d-hourly"), r);
        updateSticky();
      }, 150);
    });
  }

  // ---------------------------------------------------------------- start
  if (S.theme === "dark" || S.theme === "light") document.documentElement.setAttribute("data-theme", S.theme);
  guard(renderHeader)();
  bind();
  compute();
  if (S.sig && !S.hasPage) {
    const i = LIST.findIndex((r) => r.id === S.sig);
    if (i >= 0) S.p = Math.floor(i / PAGE);
  }
  update(true);
  const T1 = performance.now();
  requestAnimationFrame(() => setTimeout(() => {
    const t2 = performance.now();
    const foot = $("foot");
    foot.textContent = `Generated ${ts16(D.generated_at)} UTC by atlas-log ${D.tool_version || ""} · ${D.schema} · ` +
      `${plural(NODES.length, "node")}, ${plural(ROWS.length, "group")}, ${plural(STATIC_LOG.length, "example entry", "example entries")}, ${plural(EVENTS.length, "event")} · ` +
      `rendered in ${Math.round(T1 - T0)} ms (script), first paint ${Math.round(t2)} ms after navigation start`;
    foot.dataset.script = String(Math.round(T1 - T0));
    foot.dataset.paint = String(Math.round(t2));
  }, 0));
  }

  // ---------------------------------------------------------------- loader
  const start = (data) => {
    try {
      boot(data);
    } catch (err) {
      fatal(err);
    }
  };
  const embedded = $("cluster-data");
  const text = embedded ? embedded.textContent : "";
  if (text && text.trim().startsWith("{")) {
    let data = null;
    try {
      data = JSON.parse(text);
    } catch (err) {
      fatal(err);
    }
    if (data) start(data);
  } else {
    const url = document.documentElement.dataset.reportUrl;
    if (!url) {
      fatal("no cluster data: #cluster-data is empty and <html data-report-url> is not set");
    } else {
      fetch(url, { headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`GET ${url}: HTTP ${r.status}`))))
        .then(start)
        .catch(fatal);
    }
  }
})();
