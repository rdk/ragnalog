/* ragnalog cluster page: renders a cluster-report.v1 payload, embedded in #cluster-data or fetched
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
    if (!isNum(n)) return String(n);
    const a = Math.abs(n);
    if (Math.round(a * 100) / 100 < 1000) return String(Math.round(n * 100) / 100);
    // round first, then pick the unit: 999,500 is "1M", not "1000K"
    const units = [[1e3, "K"], [1e6, "M"], [1e9, "B"]];
    for (let i = 0; i < units.length; i++) {
      const [v, u] = units[i];
      const x = a / v;
      const s = x >= 100 ? String(Math.round(x)) : (Math.round(x * 10) / 10).toFixed(1).replace(/\.0$/, "");
      if (+s < 1000 || i === units.length - 1) return (n < 0 ? "-" : "") + s + u;
    }
    return String(n);
  }
  // @zone: parseTs … tsFull (tests/report/test_cluster_zone.py runs this part and xTicks with node)
  const parseTs = (iso) => (iso == null || iso === "" ? NaN : Date.parse(String(iso).replace(/(\.\d{3})\d+/, "$1")));
  const isoOf = (ms) => (isNum(ms) ? new Date(ms).toISOString() : "");
  // times show in the browser's time zone or, toggled, in UTC: a viewer preference shared with the
  // set report (tz=local|utc in a link wins); windows in the hash and API parameters stay UTC
  const TZ_KEY = "ragnalog-tz";
  let TZL = (() => { try { return localStorage.getItem(TZ_KEY) !== "utc"; } catch { return true; } })();
  let TZ = "UTC";
  let TZ2 = false; // the shown zone changes its name inside the capture (a DST change)
  /** The browser zone's offset from UTC at instant ms, in ms (0 when times show in UTC). */
  const zoff = (ms) => (TZL && isNum(ms) ? -new Date(ms).getTimezoneOffset() * 6e4 : 0);
  /** The ISO time shifted into the shown zone (still printed with toISOString's layout). */
  function zoned(iso) {
    if (!TZL || !iso) return iso == null ? "" : str(iso);
    const ms = parseTs(iso);
    return ms === ms ? new Date(ms + zoff(ms)).toISOString() : str(iso);
  }
  function zoneName(ms) {
    try {
      const p = new Intl.DateTimeFormat("en-GB", { timeZoneName: "short" }).formatToParts(new Date(ms));
      return (p.find((x) => x.type === "timeZoneName") || {}).value || "local";
    } catch { return "local"; }
  }
  /** The name of the shown zone at instant ms (CEST or CET across a DST change; UTC). */
  const zAt = (ms) => (TZL && TZ2 && isNum(ms) ? zoneName(ms) : TZ);
  /** The earliest instant at which the shown zone's clock reads L (a wall-clock time written as
      if UTC); NaN when the clock skips it (DST spring-forward). The repeated hour of a fall-back
      maps to its first occurrence only, so it gets one tick. */
  function wallToMs(L) {
    if (!TZL) return L;
    let best = NaN;
    for (const o of [zoff(L - 864e5), zoff(L), zoff(L + 864e5)]) {
      const t = L - o;
      if (t + zoff(t) === L && !(best <= t)) best = t;
    }
    return best;
  }
  const ts16 = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(0, 10) + " " + iso.slice(11, 16)) : "");
  const tsShort = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(5, 10) + " " + iso.slice(11, 16)) : "");
  const tsSec = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(5, 10) + " " + iso.slice(11, 19)) : "");
  const tsMs = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(5, 10) + " " + iso.slice(11, 23)) : "");
  const tsFull = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(0, 10) + " " + iso.slice(11, 23)) : "");
  // @zone-end
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
  /** "12%", "0.4%", "<0.1%": a share in [0, 1]. */
  const pctText = (x) => (!(x > 0) ? "0%" : x >= 0.1 ? Math.round(x * 100) + "%" : x >= 0.001 ? (x * 100).toFixed(1) + "%" : "<0.1%");
  const { loggerHtml, excHtml, tsHtml, kvHtml, nilHtml } = RagnalogEntryCard;
  const PH = /&lt;([A-Z][A-Z0-9_]*|\*)&gt;/g;
  const REPEATED = /^\s*([^\w\s]){2,}\s*$|^\s*[^\w\s]{6,}\s*$/;
  const tplHtml = (t) =>
    t && REPEATED.test(t)
      ? `<span class="rep" title="${esc(t)}">(repeated characters)</span>`
      : logInline(t).replace(PH, (m, n) => `<span class="ph">${n.toLowerCase()}</span>`);
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
  // rich tooltips (tips.js runs before this script): secondary detail lives in them, built on
  // hover for table rows (data-tip-k, builders registered below) or up front for fixed parts
  const TIPS = window.RagnalogTips || null;
  const tipCard = (o) => (TIPS ? TIPS.card(o) : "");
  /** A value for tipCard that is markup already (badges, glyphs, escaped templates). */
  const trusted = (html) => ({ html });
  /** ` data-tip="…"`: a tooltip card built now. */
  const tipAttr = (o) => ` data-tip="${esc(tipCard(o))}"`;
  const mono = (s) => trusted(`<span class="atip-mono">${esc(s)}</span>`);

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
  /** A merged-log group filter for its chip: the template, "N2: template" for NODE:SIGNATURE. */
  const msLabel = (m) => {
    const i = m.indexOf(":"), sig = i < 0 ? m : m.slice(i + 1);
    const g = GBY.get(sig);
    return (i < 0 ? "" : NLABEL(m.slice(0, i)) + ": ") + trunc(g ? g.tpl : sig, 40);
  };
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
  const TE = NB ? parseTs(BKS[NB - 1]) + BMS : NaN;
  /** Show times in the browser's zone or in UTC; a capture across a DST change names both zones
      (CEST/CET), and ranges name each end's zone when they differ. */
  const setZone = (local) => {
    TZL = local;
    if (!local) { TZ = "UTC"; TZ2 = false; return; }
    const a = zoneName(isNum(TA) ? TA : Date.now()), b = zoneName(isNum(TE) ? TE : Date.now());
    TZ2 = a !== b;
    TZ = TZ2 ? a + "/" + b : a;
  };
  /** "10-25 02:00 → 10-26 01:44 CET" for instants a, b (ms); each end's zone when they differ. */
  function rangeText(a, b, fa = ts16, fb = tsShort) {
    const za = zAt(a), zb = zAt(b);
    return za === zb ? `${fa(isoOf(a))} → ${fb(isoOf(b))} ${za}` : `${fa(isoOf(a))} ${za} → ${fb(isoOf(b))} ${zb}`;
  }
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
  // ---- zoom and bins: the timeline shows a range [a, b) in ms (S.z, else the capture) in bins of a
  // size (S.bin, else automatic). Bins of the base bucket (an hour, or 15 min) and its multiples
  // add up the payload's counts; finer ones come from the capture's timeline API (web app only).
  const TL_URL = URLS ? str(URLS.timeline || str(URLS.entries).replace(/\/entries(?=$|\?)/, "/timeline")) : "";
  const FINE = !!(TL_URL && NB);
  const BASE_KS = [1, 2, 3, 4, 6, 8, 12, 24, 48, 96, 168, 336];
  const SIZES = [...(FINE ? [1e3, 5e3, 1e4, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5].filter((ms) => ms < BMS) : []), ...BASE_KS.map((k) => k * BMS)];
  const sizeKey = (ms) => (ms % 864e5 === 0 ? ms / 864e5 + "d" : ms % 3.6e6 === 0 ? ms / 3.6e6 + "h" : ms % 6e4 === 0 ? ms / 6e4 + "m" : Math.round(ms / 1e3) + "s");
  const keyMs = (k) => { const m = /^(\d+)([smhd])$/.exec(k || ""); return m ? +m[1] * { s: 1e3, m: 6e4, h: 3.6e6, d: 864e5 }[m[2]] : 0; };
  /** A bin size in the bins menu: "5 s", "15 min", "3 h", "1 d". */
  const sizeShort = (ms) => sizeKey(ms).replace(/^(\d+)([smhd])$/, (_, n, u) => n + " " + (u === "m" ? "min" : u));
  /** What one bin is, for the chart's title: "hour", "15 min", "5 s", "2 hours", "day". */
  const sizeLabel = (ms) => (ms === 3.6e6 ? "hour" : ms === 864e5 ? "day" : ms < 6e4 ? Math.round(ms / 1e3) + " s"
    : ms < 3.6e6 ? Math.round(ms / 6e4) + " min" : ms % 864e5 === 0 ? ms / 864e5 + " days" : Math.round((ms / 3.6e6) * 10) / 10 + " hours");
  /** A zoom range inside the capture's timeline, or null (the whole timeline, or under 10 s). */
  function clampZoom(z) {
    if (!z || !NB || !isNum(TA)) return null;
    let [a, b] = z;
    if (!(isNum(a) && isNum(b))) return null;
    if (a > b) [a, b] = [b, a];
    a = Math.max(TA, a);
    b = Math.min(TE, b);
    return b - a < 1e4 || (a <= TA && b >= TE) ? null : [a, b];
  }
  /** The base buckets [first, last] covering the times [a, b): the window of a zoom. */
  const bucketsCovering = (a, b) => [Math.max(0, Math.floor((a - TA) / BMS)), Math.min(NB - 1, Math.ceil((b - TA) / BMS) - 1)];
  // the zoom toggle: on (the default), a drag on the timeline zooms into the selection; off, it
  // selects the time window. A viewer preference shared with the set report.
  const ZS_KEY = "ragnalog-dragzoom";
  let ZOOMSEL = (() => { try { return localStorage.getItem(ZS_KEY) !== "0"; } catch { return true; } })();
  /** Earlier timeline views (window, zoom, bin) for Undo, the newest last; Clear is undone too. */
  const UNDO = [];
  const viewKey = (s) => JSON.stringify([s.t || null, s.z || null, s.bin || ""]);
  /** Bins of k base buckets over the buckets [ia, ib). */
  function baseFrame(ia, ib, k) {
    return {
      kind: "base", ia, ib, k, size: k * BMS, nb: Math.max(1, Math.ceil((ib - ia) / k)), a: TA + ia * BMS, b: TA + ib * BMS,
      binOf: (i) => Math.floor((i - ia) / k),
      start: (b) => TA + (ia + b * k) * BMS,
      end: (b) => Math.min(TA + ib * BMS, TA + (ia + (b + 1) * k) * BMS),
    };
  }
  /** Bins of size ms (under the base bucket) over [a, b), aligned to the epoch as the server counts them. */
  function fineFrame(a, b, size) {
    const a0 = Math.floor(a / size) * size, b0 = Math.ceil(b / size) * size;
    return { kind: "fine", k: 0, size, nb: Math.max(1, Math.round((b0 - a0) / size)), a: a0, b: b0, binOf: null,
      start: (i) => a0 + i * size, end: (i) => a0 + (i + 1) * size };
  }
  /** "2026-10-29 14:05 – 14:10 CET" of bin b of a frame; seconds under a minute; each end's zone
      when they differ (the repeated hour of a DST change). */
  function frameRange(fr, b) {
    const a = fr.start(b), e = Math.min(fr.end(b), Math.max(TE, a));
    const n = fr.size < 6e4 ? 19 : 16;
    const s = zoned(isoOf(a)), t = zoned(isoOf(e));
    const za = zAt(a), zb = zAt(e);
    return s.slice(0, 10) + " " + s.slice(11, n) + (za === zb ? "" : " " + za) + " – " +
      (t.slice(0, 10) === s.slice(0, 10) ? "" : t.slice(5, 10) + " ") + t.slice(11, n) + " " + zb;
  }
  // grave errors (report/grave.py): out of memory, a stack overflow, a JVM error, an exhausted
  // resource or FATAL, per node, entries of a kind within 60 s folded into one marker
  const GRAVE_KINDS = ["oom", "stack_overflow", "jvm_error", "resource", "fatal"];  // most grave first
  const GRAVE_LABEL = { oom: "Out of memory", stack_overflow: "Stack overflow", jvm_error: "JVM error", resource: "Resource exhausted", fatal: "FATAL" };
  const GRAVE_TAG = { oom: "OOM", stack_overflow: "STACK", jvm_error: "JVM", resource: "LIMIT", fatal: "FATAL" };
  const GRAVE = arr(D.grave).map((g) => ({
    node: str(g.node), kind: Object.hasOwn(GRAVE_TAG, g.kind) ? g.kind : "fatal", ts: str(g.ts), last_ts: str(g.last_ts || g.ts),
    ms: parseTs(g.ts), last: parseTs(g.last_ts || g.ts), count: num(g.count) || 1, sig: str(g.signature), entry: str(g.entry_id), text: str(g.text),
  })).filter((g) => isNum(g.ms) && NIDX.has(g.node)).sort((a, b) => a.ms - b.ms);
  const GRAVE_N = Object.fromEntries(NODES.map((n) => [n, GRAVE.filter((g) => g.node === n)]));
  const graveTop = (items) => items.reduce((k, g) => (GRAVE_KINDS.indexOf(g.kind) < GRAVE_KINDS.indexOf(k) ? g.kind : k), items[0].kind);

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
    index_restore: "index restore", index_replay: "index replay", reindex: "reindex", cluster_lock: "cluster lock", job_run: "job run",
  };
  const KIND_PLURAL = {
    health_failed: "health checks failed", cache_replication: "cache replications", cache_peer: "cache peers re-created",
    index_replication: "index replications", index_snapshot: "index snapshots", index_restore: "index restores",
    index_replay: "index replays", reindex: "reindexes", cluster_lock: "cluster locks", job_run: "job runs",
  };
  const kindLabel = (k) => KIND_LABEL[k] || str(k).replace(/_/g, " ");
  /** "14 health checks failed", "1 job run". */
  const kindCount = (k, n) => `${fmt(n)} ${n === 1 ? kindLabel(k) : KIND_PLURAL[k] || kindLabel(k) + "s"}`;

  // ---------------------------------------------------------------- state <-> hash
  const Y_MODES = ["shared", "node", "pct", "err"];
  const MULTI = NODES.length > 1; // a single node has no spread, lead or node filter
  const SPREADS = ["any", "all", "only", "skewed"];
  const TABS = ["g", "i", "l"];
  const LOG_LEVELS = ["", "DEBUG", "INFO", "WARN", "ERROR"];
  // sorts read the numbers through V(): the window's when one is set
  const byTotal = (a, b) => V(b).total - V(a).total || cmpStr(a.id, b.id);
  const SORTS = {
    count: ["Count", byTotal],
    skew: ["Skew", (a, b) => skewKey(V(b)) - skewKey(V(a)) || byTotal(a, b)],
    lead: ["Lead", (a, b) => (b.lead ?? -1) - (a.lead ?? -1) || byTotal(a, b)],
    level: ["Level", (a, b) => b.level - a.level || byTotal(a, b)],
    template: ["Template", (a, b) => cmpStr(a.tpl, b.tpl) || byTotal(a, b)],
  };
  const skewKey = (r) => (r.spread === "only" ? Infinity : r.skew ?? -1);
  for (const n of NODES) {
    const i = NIDX.get(n);
    SORTS["node:" + n] = [`Count on ${NLABEL(n)}`, (a, b) => V(b).counts[i] - V(a).counts[i] || byTotal(a, b)];
  }
  if (!MULTI) {
    delete SORTS.skew;
    delete SORTS.lead;
    for (const n of NODES) delete SORTS["node:" + n];
  }
  // the window in the hash is a UTC time range (t=20261028T0200-20261029T0200, end exclusive) so a
  // link or a switch to another set keeps the same hours; plain bucket indices (t=3-7) still parse
  const stamp = (ms) => new Date(ms).toISOString().slice(0, 16).replace(/[-:]/g, "");
  function unstamp(s) {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)$/.exec(s);
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : NaN;
  }
  function windowOf(s, t0, unit, n) {
    if (!s || !n) return null;
    let a, b, m;
    if ((m = /^(\d+)-(\d+)$/.exec(s))) { a = +m[1]; b = +m[2]; }
    else if ((m = /^(\d{8}T\d{4})-(\d{8}T\d{4})$/.exec(s))) {
      a = Math.floor((unstamp(m[1]) - t0) / unit);
      b = Math.ceil((unstamp(m[2]) - t0) / unit) - 1;
      if (!(a === a && b === b)) return null;
    } else return null;
    if (a > b) [a, b] = [b, a];
    if (b < 0 || a > n - 1) return null;
    return [Math.max(0, a), Math.min(b, n - 1)];
  }
  const windowText = (t, t0, unit) =>
    unit < 6e4 ? t[0] + "-" + t[1] : stamp(t0 + t[0] * unit) + "-" + stamp(t0 + (t[1] + 1) * unit);
  const parseWindow = (s) => windowOf(s, TA, BMS, NB);
  // the timeline's zoom (z=20261029T1400-20261029T1530, UTC, seconds when needed) and bin (bin=5m)
  const zstamp = (ms) => new Date(ms).toISOString().slice(0, 19).replace(/[-:]/g, "").replace(/(T\d{4})00$/, "$1");
  function unzstamp(s) {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)?$/.exec(s);
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : NaN;
  }
  function parseZoom(s) {
    const m = /^(\d{8}T\d{4}(?:\d\d)?)-(\d{8}T\d{4}(?:\d\d)?)$/.exec(s || "");
    return m ? clampZoom([unzstamp(m[1]), unzstamp(m[2])]) : null;
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
      z: parseZoom(get("z")),
      bin: keyMs(get("bin")) ? get("bin") : "",
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
      ae: get("ae"),
      ek: new Set(get("ek").split(",").filter((k) => KINDS.includes(k) && k !== "job_run")),
      theme: get("theme"),
      tz: ["local", "utc"].includes(get("tz")) ? get("tz") : "",
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
    if (s.t) p.set("t", windowText(s.t, TA, BMS));
    if (s.z) p.set("z", zstamp(s.z[0]) + "-" + zstamp(s.z[1]));
    if (s.bin) p.set("bin", s.bin);
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
    if (s.at && s.ae) p.set("ae", s.ae);
    if (s.ek.size) p.set("ek", [...s.ek].join(","));
    if (s.theme) p.set("theme", s.theme);
    if (s.tz) p.set("tz", s.tz);
    return p.toString();
  }
  let S = parseHash();
  let CUR = "";

  function commit(patch, opts = {}) {
    if (!opts.noUndo && ("t" in patch || "z" in patch || "bin" in patch) && viewKey(S) !== viewKey({ ...S, ...patch })) {
      UNDO.push({ t: S.t ? [...S.t] : null, z: S.z ? [...S.z] : null, bin: S.bin || "" });
      if (UNDO.length > 100) UNDO.shift();
    }
    Object.assign(S, patch);
    if (opts.keepPage !== true && !("p" in patch)) S.p = 0;
    // a moment outside a newly set window is dropped (the log shows the window)
    if ("t" in patch && S.at && !inRange(parseTs(S.at), tRange())) { S.at = ""; S.ae = ""; }
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
  let qTimer = 0, mqTimer = 0; // one debounce per search box
  function onNav() {
    clearTimeout(qTimer);
    clearTimeout(mqTimer);
    const prevY = S.y + S.fit + S.arrows + S.jobs + S.sig + S.t + S.z + S.bin;
    S = parseHash();
    if (S.theme === "dark" || S.theme === "light") {
      // back/forward to a link that names a theme shows it, as opening that link does
      document.documentElement.setAttribute("data-theme", S.theme);
      renderThemeBtn();
    }
    // likewise a link that names a zone (applyZone re-renders; update below then draws the rest)
    if (S.tz && (S.tz === "local") !== TZL) applyZone(S.tz === "local", true);
    $("f-search").value = S.q;
    $("l-search").value = S.mq;
    update(prevY !== S.y + S.fit + S.arrows + S.jobs + S.sig + S.t + S.z + S.bin);
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
  // ---------------------------------------------------------------- time window
  // A window S.t = [a, b] (bucket indices) narrows the groups table to it: per-node counts,
  // totals, sorting, count bars, the summary, and spread/skew, recomputed with each node's
  // volume inside the window (the rule of service/cluster.py, skew_min as in the payload).
  // Lead stays the capture's: the buckets are too coarse to time a lead inside a window.
  const SKEW_MIN = isNum(OPTS.skew_min) ? OPTS.skew_min : 4;
  let WKEY = "";
  let NWT = {}; // node -> entries in the window
  /** The row as seen in the window: counts, total, present, spread, skew, top; cached per window. */
  function winOf(r) {
    if (r.wKey === WKEY && r.w) return r.w;
    const [a, b] = S.t;
    const counts = NODES.map((n) => {
      let c = 0;
      for (const [i, v] of r.hourly[n]) {
        if (i < a) continue;
        if (i > b) break;
        c += num(v);
      }
      return c;
    });
    const present = NODES.filter((n, i) => counts[i] > 0);
    let top = null, topV = -1;
    const rates = [];
    for (const n of present) {
      const c = counts[NIDX.get(n)], vol = NWT[n];
      const v = vol ? c / vol : c;
      if (vol) rates.push(v);
      if (v > topV) { topV = v; top = n; }
    }
    let spread = "only", skew = null;
    if (present.length > 1) {
      const lo = Math.min(...rates);
      skew = rates.length && lo > 0 ? Math.max(...rates) / lo : null;
      const missing = NODES.some((n, i) => NWT[n] > 0 && !(counts[i] > 0));
      spread = missing || (skew != null && skew >= SKEW_MIN) ? "skewed" : "all";
    }
    r.wKey = WKEY;
    r.w = Object.assign(Object.create(r), { counts, total: counts.reduce((x, y) => x + y, 0), present, spread, skew, top, whole: r });
    return r.w;
  }
  /** The row with its numbers in the window when one is set (same id, template, …). */
  const V = (r) => (S.t ? winOf(r) : r);
  let SC = { max: 1, min: 1 }; // count bars: the capture's range, or the window's
  function match(r, skip) {
    if (skip !== "lv" && !S.lv.has(r.bucket)) return false;
    if (Q.sig) {
      if (!r.id.toLowerCase().startsWith(Q.sig)) return false;
    } else if (Q.text && !r.key.includes(Q.text)) return false;
    const v = V(r);
    if (skip !== "sp" && S.sp !== "any" && v.spread !== S.sp) return false;
    if (skip !== "nd" && S.nd && !(v.counts[NIDX.get(S.nd)] > 0)) return false;
    if (S.t && skip !== "t" && !(v.total > 0)) return false;
    return true;
  }
  let LIST = [];
  function compute() {
    Q = parseQ(S.q);
    WKEY = S.t ? S.t.join("-") : "";
    if (S.t) {
      NWT = {};
      for (const n of NODES) {
        let t = 0;
        for (let i = S.t[0]; i <= S.t[1]; i++) t += NTOT[n][i] || 0;
        NWT[n] = t;
      }
    }
    LIST = ROWS.filter((r) => match(r, ""));
    LIST.sort(SORTS[S.sort][1]);
    if (S.t) {
      let max = 1, min = Infinity;
      for (const r of ROWS) for (const c of winOf(r).counts) {
        if (c > max) max = c;
        if (c > 0 && c < min) min = c;
      }
      SC = { max, min: min < Infinity ? min : 1 };
    } else SC = { max: CMAX, min: CMIN };
  }

  // ---------------------------------------------------------------- charts
  const GAP = 2;
  const r1 = (x) => Math.round(x * 10) / 10;
  function chooseBin(n, plotW) {
    for (const k of [1, 2, 3, 4, 6, 8, 12, 24, 48, 96, 168, 336]) if (Math.ceil(n / k) * 5 <= plotW) return k;
    return Math.max(1, Math.ceil((n * 5) / Math.max(1, plotW)));
  }
  /** Round axis ticks from 0 to at least max; whole steps only on a count axis (`int`). */
  function niceTicks(max, parts = 3, int = false) {
    if (!(max > 0)) return [0, 1];
    const raw = max / parts;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
    if (int) step = step < 1 ? 1 : step === 2.5 ? 2 : step; // no "0.5 entries", no 2.5 steps
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
    const e = Math.min(TE, a + k * BMS);
    const s = zoned(isoOf(a)), t = zoned(isoOf(e));
    const za = zAt(a), zb = zAt(e);
    // across a DST change each end names its zone: "02:00 CEST – 02:00 CET" (the repeated hour)
    return s.slice(0, 10) + " " + s.slice(11, 16) + (za === zb ? "" : " " + za) + " – " +
      (t.slice(0, 10) === s.slice(0, 10) ? "" : t.slice(5, 10) + " ") + t.slice(11, 16) + " " + zb;
  }
  /** x-axis ticks: the smallest step whose labels are >= 64 px apart, aligned to the shown zone's
      clock (midnight, whole hours; a half-hour zone's ticks fall on its own :00). */
  function xTicks(xOfMs, pxPerMs, W, padR, a = TA, b = TE) {
    const steps = [1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5, 432e5, 864e5, 1728e5, 6048e5, 12096e5];
    const step = steps.find((s) => s * pxPerMs >= 64) || 12096e5;
    const out = [];
    let last = -Infinity;
    if (!isNum(a) || !isNum(b)) return out;
    // L is a wall-clock time in the zone (as if UTC); t the first instant the clock shows it: a
    // skipped hour (spring-forward) has no tick, a repeated one (fall-back) one; dates at midnight
    const endL = b + zoff(b) + step;
    for (let L = Math.ceil((a + zoff(a)) / step) * step; L <= endL; L += step) {
      const t = wallToMs(L);
      if (!(t === t) || t < a || t <= last) continue;
      if (t > b) break;
      last = t;
      const x = xOfMs(t);
      if (x > W - padR - 14) continue;
      const iso = zoned(isoOf(t));
      out.push([x, iso.slice(11, 19) === "00:00:00" ? iso.slice(5, 10) : step < 6e4 ? iso.slice(11, 19) : iso.slice(11, 16)]);
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
      // a circular arrow: drawn as a stroke, so it never reads as index_replay's filled dot
      case "reindex": return `<path class="ev-x${extra}" d="M${x} ${r1(y - 4.5)}A4.5 4.5 0 1 1 ${r1(x - 4.5)} ${y}"/>` +
        `<path class="ev${extra}" d="M${r1(x - 7.5)} ${r1(y + 0.5)}L${r1(x - 4.5)} ${r1(y - 3)}L${r1(x - 1.5)} ${r1(y + 0.5)}Z"/>`;
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
  let BINS = { auto: BMS, choices: [], size: BMS };
  let TL_WANT = "", TL_NOTE = "";
  const FINE_CACHE = new Map();
  /** Counts of a fine frame from the capture's timeline API, per node {lev, tot, g}, once they are
      in; {err} when the request failed; undefined while it runs (the chart redraws when done). */
  function fineData(fr, sig) {
    const key = [fr.a, fr.b, fr.size, sig || ""].join("|");
    const hit = FINE_CACHE.get(key);
    if (hit) return hit.value;
    const entry = { value: undefined };
    FINE_CACHE.set(key, entry);
    if (FINE_CACHE.size > 30) FINE_CACHE.delete(FINE_CACHE.keys().next().value);
    const u = new URL(TL_URL, location.href);
    u.searchParams.set("bucket", sizeKey(fr.size));
    u.searchParams.set("since", isoOf(fr.a));
    u.searchParams.set("until", isoOf(fr.b));
    if (WMODE === "overlap" || WMODE === "union") u.searchParams.set("window", WMODE);
    const g = new URL(u.toString());
    if (sig) g.searchParams.set("signature", sig);
    const at = (doc) => arr(obj(doc).buckets).map((b) => Math.round((parseTs(b) - fr.a) / fr.size));
    Promise.all([fetchJson(u.toString()), sig ? fetchJson(g.toString()) : null]).then(([all, one]) => {
      const idx = at(all), gi = one ? at(one) : [];
      const out = {};
      for (const n of NODES) {
        const tl = obj(obj(obj(all).nodes)[n]);
        const lev = {};
        for (const b of BUCKETS) lev[b] = new Float64Array(fr.nb);
        const tot = new Float64Array(fr.nb);
        for (const [name, values] of Object.entries(obj(tl.levels))) {
          const b = BUCKETS.includes(name) ? name : bucketOf(levelNum(name));
          arr(values).forEach((v, j) => { const i = idx[j]; if (i >= 0 && i < fr.nb) { lev[b][i] += num(v); tot[i] += num(v); } });
        }
        let gl = null;
        if (one) {
          gl = new Float64Array(fr.nb);
          for (const values of Object.values(obj(obj(obj(obj(one).nodes)[n]).levels))) {
            arr(values).forEach((v, j) => { const i = gi[j]; if (i >= 0 && i < fr.nb) gl[i] += num(v); });
          }
        }
        out[n] = { lev, tot, g: gl };
      }
      entry.value = out;
    }).catch((err) => { entry.value = { err: str(err && err.message ? err.message : err) }; })
      .finally(() => { if (TL_WANT === key) renderTimeline(); });
    return undefined;
  }
  const renderTimeline = guard(function renderTimeline() {
    const el = $("c-timeline");
    if (!NB || !NODES.length || !isNum(TA)) {
      el.innerHTML = `<div class="empty">No entries in this capture's time range.</div>`;
      $("tl-legend").innerHTML = "";
      $("tl-events").innerHTML = "";
      TLG = null;
      return;
    }
    const W = Math.max(240, Math.floor(el.clientWidth || 900));
    const N = NODES.length;
    const padL = 116, padR = 12;
    const plotW = W - padL - padR;
    // the frame: the zoom (else the capture) in bins of the picked size, else the automatic one
    // (the finest whose bars keep 5 px, as before); bins under the base bucket from the server
    const [za, zb] = S.z || [TA, TE];
    const span = zb - za;
    const floor = !FINE ? BMS : !S.z && span >= 864e5 ? BMS : 1e3;
    const auto = SIZES.find((ms) => ms >= floor && Math.ceil(span / ms) * 5 <= plotW) || SIZES[SIZES.length - 1];
    const choices = SIZES.filter((ms) => { const c = Math.ceil(span / ms); return c >= 2 && c <= Math.floor(plotW / 2); });
    const want = keyMs(S.bin);
    const size = want && choices.includes(want) ? want : auto;
    BINS = { auto, choices, size };
    const selSig = S.sig && GBY.get(S.sig) ? S.sig : "";
    let fr = null, fine = null;
    TL_NOTE = "";
    if (size < BMS) {
      fr = fineFrame(za, zb, size);
      TL_WANT = [fr.a, fr.b, fr.size, selSig].join("|");
      fine = fineData(fr, selSig);
      if (fine === undefined) {
        // keep the chart drawn so far, faded, until the counts are in
        el.classList.add("loading");
        if (!TLG || !el.firstElementChild) el.innerHTML = `<div class="empty">Loading entries per ${esc(sizeLabel(size))}…</div>`;
        renderBins();
        return;
      }
      if (fine.err) {
        TL_NOTE = `Entries per ${sizeLabel(size)} could not be loaded (${fine.err}); showing ${binLabel(1)}s`;
        fr = null;
        fine = null;
      }
    }
    if (!fr) {
      TL_WANT = "";
      const ia = Math.max(0, Math.floor((za - TA) / BMS)), ib = Math.min(NB, Math.max(ia + 1, Math.ceil((zb - TA) / BMS)));
      fr = baseFrame(ia, ib, Math.max(1, Math.round(Math.max(size, BMS) / BMS)));
    }
    el.classList.remove("loading");
    const k = fr.k;  // base buckets per bin (0 in a fine frame)
    const nb = fr.nb;
    const slot = plotW / nb;
    // thin bars (a finer bin than the automatic one) keep a hairline gap, then none
    const bw = slot >= 4 ? Math.min(24, Math.max(2, slot - GAP)) : Math.max(0.8, slot - (slot >= 2.5 ? 1 : 0.3));
    const RULER = WIN ? 14 : 4, LANE = 18, ROWGAP = 6, AXIS = 20;
    const XM = MULTI ? 14 : 0; // the cross-node moments lane above the first node
    const BAND = N <= 2 ? 52 : N <= 4 ? 64 : N <= 8 ? 56 : 44;
    const rowTop = (i) => RULER + XM + i * (LANE + BAND + ROWGAP);
    const bottomAll = rowTop(N - 1) + LANE + BAND;
    const H = bottomAll + AXIS;
    const pxPerMs = slot / fr.size;
    const xOfMs = (ms) => padL + (ms - fr.a) * pxPerMs;
    const xEnd = Math.min(padL + plotW, xOfMs(Math.min(fr.b, TE)));
    const clampX = (x) => Math.max(padL, Math.min(xEnd, x));
    const sel = S.sig ? GBY.get(S.sig) || null : null;
    const fit = !!sel && S.fit;
    const ERR = S.y === "err"; // only the ERROR columns, on one scale: WARN no longer flattens them
    // a base frame adds up base buckets [ia, ib); a fine frame has the server's counts
    const binB = (src) => {
      const out = new Float64Array(nb);
      for (let i = fr.ia; i < fr.ib && i < src.length; i++) if (src[i]) out[fr.binOf(i)] += src[i];
      return out;
    };
    const inCover = (n, a, b) => !!COVT[n] && b > COVT[n][0] && a < COVT[n][1] && !GAPS[n].some(([x, y]) => a >= x && b <= y);
    const rows = NODES.map((n, i) => {
      const f = S.y === "pct" ? 100 / Math.max(1, NSUM[n]) : 1;
      const fd = fine && fine[n];
      const levAll = BUCKETS.map((b) => (fd ? fd.lev[b] : binB(LEV[n][b])));
      const all = fd ? fd.tot : binB(NTOT[n]);
      const has = new Uint8Array(nb);
      if (fd) for (let b = 0; b < nb; b++) has[b] = all[b] > 0 || inCover(n, fr.start(b), fr.end(b)) ? 1 : 0;
      else for (let j = fr.ia; j < fr.ib; j++) if (hasLogs(n, j)) has[fr.binOf(j)] = 1;
      return {
        n, i, f, top: rowTop(i), levAll, all,
        lev: ERR ? levAll.map((a, s) => (BUCKETS[s] === "ERROR" ? a : new Float64Array(nb))) : levAll,
        tot: ERR ? levAll[0] : all,
        has,
        g: sel ? (fd ? fd.g || new Float64Array(nb) : binB(denseOf(sel.hourly[n]))) : null,
      };
    });
    // errors mode: a spike far above the rest (max > 4 × the 95th percentile) is clipped and labelled,
    // so one burst does not flatten every other hour's errors
    let clipAt = 0;
    if (ERR && !fit) {
      const nz = [];
      let mx = 0;
      for (const r of rows) for (let b = 0; b < nb; b++) if (r.tot[b] > 0) { nz.push(r.tot[b]); mx = Math.max(mx, r.tot[b]); }
      nz.sort((x, y) => x - y);
      const p95 = nz.length ? nz[Math.floor(0.95 * (nz.length - 1))] : 0;
      if (p95 > 0 && mx > 4 * p95) clipAt = p95 * 1.25;
    }
    let shared = 0;
    for (const r of rows) {
      const src = fit ? r.g : r.tot;
      let m = 0;
      for (let b = 0; b < nb; b++) m = Math.max(m, clipAt ? Math.min(clipAt, src[b]) : src[b] * r.f);
      r.peak = m;
      shared = Math.max(shared, m);
    }
    for (const r of rows) {
      r.ticks = niceTicks(S.y === "node" ? r.peak : shared, BAND < 70 ? 2 : 3, S.y !== "pct" && !fit);
      r.yMax = r.ticks[r.ticks.length - 1] || 1;
    }
    const tickText = (t) => (S.y === "pct" ? compact(t) + "%" : compact(t));
    const P = [];
    P.push(`<svg class="${S.arrows ? "" : "arrows-off"}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="group" aria-label="${ERR ? "Errors" : "Entries"} per ${esc(binLabel(k))}${ERR ? "" : " by level"}, one band per node, with event lanes">`);
    P.push(`<rect class="hover-band" x="0" y="${RULER}" width="${r1(slot)}" height="${bottomAll - RULER}" visibility="hidden"/>`);
    // window ruler
    if (WIN) {
      const x0 = clampX(xOfMs(WIN[0])), x1 = clampX(xOfMs(WIN[1]));
      if (x1 > x0) {
        // the bracket of the capture's window; its range is in the header
        P.push(`<line class="win-line" x1="${r1(x0)}" x2="${r1(x1)}" y1="9" y2="9"/>`);
        P.push(`<line class="win-line" x1="${r1(x0)}" x2="${r1(x0)}" y1="4" y2="14"/><line class="win-line" x1="${r1(x1)}" x2="${r1(x1)}" y1="4" y2="14"/>`);
        for (const x of [x0, x1]) {
          if (x > padL + 1 && x < xEnd - 1) P.push(`<line class="win-edge" x1="${r1(x) + 0.5}" x2="${r1(x) + 0.5}" y1="14" y2="${bottomAll}"/>`);
        }
      }
    }
    // cross-node moments: the bins where 2 or more nodes log errors far above their own usual
    const XMS = MULTI && fr.kind === "base" ? crossMoments(rows, fr) : [];
    if (XM) {
      const y = RULER + 3;
      P.push(`<text class="xm-lbl" x="${padL - 6}" y="${y + 9}" text-anchor="end"${tipAttr({ h: "Cross-node moments",
        body: esc(`The ${sizeLabel(fr.size)} bins holding a ${binLabel(1)} in which two or more nodes log more than 3× their own usual errors (the median of their ${binLabel(1)}s with errors)${fr.kind === "fine" ? "; marked at bins of a " + binLabel(1) + " or more" : ""}.`),
        note: "Click a mark, or focus it and press Enter: the window moves there and the merged log opens ±30 s on all nodes around its busiest moment." })}>together</text>`);
      P.push(`<line class="lane-line" x1="${padL}" x2="${r1(xEnd)}" y1="${y + 5.5}" y2="${y + 5.5}"/>`);
      for (const m of XMS) {
        const x = padL + m.b * slot + Math.max(0, (slot - Math.max(3, bw)) / 2);
        P.push(`<rect class="xm" data-xm="${m.b}" tabindex="0" role="button" aria-label="${esc(xmLabel(m, fr))}" x="${r1(x)}" y="${y + 1}" width="${r1(Math.max(3, Math.min(bw, slot)))}" height="9" rx="2" style="--xa:${r1(0.45 + 0.55 * Math.min(1, (m.nodes.length - 1) / Math.max(1, N - 1)))}"/>`);
      }
    }
    const GB = [];  // grave badges: {n, y, x0, x1, items}
    for (const r of rows) {
      const n = r.n;
      const laneY = r.top + LANE / 2;
      const bTop = r.top + LANE, bBot = bTop + BAND;
      const plotT = bTop + 4, plotH = bBot - plotT;
      r.laneY = laneY;
      r.bTop = bTop;
      r.bBot = bBot;
      P.push(`<g class="${ncls(n)} row-id"${tipAttr(nodeTip(n))}>`);
      P.push(`<rect class="row-id-hit" x="0" y="${r.top}" width="${padL - 40}" height="${LANE}"/>`);
      P.push(`<rect class="nsw" x="4" y="${laneY - 4.5}" width="9" height="9" rx="2"/>`);
      P.push(`<text class="row-lbl" x="19" y="${laneY + 4}">${esc(NLABEL(n))}</text>`);
      P.push(`<text class="row-sub" x="${19 + 8 * NLABEL(n).length + 6}" y="${laneY + 4}">${esc(trunc(n, 9))}</text>`);
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
      if (clipAt) {
        // one label per run of clipped bins (its maximum), so labels never overlap
        let lastX = -Infinity;
        for (let b = 0; b < nb; b++) {
          if (!(r.tot[b] > r.yMax)) continue;
          let e = b, top = r.tot[b];
          while (e + 1 < nb && r.tot[e + 1] > r.yMax) top = Math.max(top, r.tot[++e]);
          const cx = r1(padL + ((b + e + 1) / 2) * slot);
          if (cx - lastX >= 40) {
            P.push(`<text class="clip-lbl" x="${Math.min(cx + 4, xEnd - 2)}" y="${plotT + 9}" text-anchor="start">↑ ${esc(compact(top))}</text>`);
            lastX = cx;
          }
          b = e;
        }
      }
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
      // grave errors: a red line through the node's lane and band (a band for a run of them), a
      // badge in its lane (drawn last, over the hit areas)
      for (const g of GRAVE_N[n]) {
        if (g.last < fr.a || g.ms >= fr.b) continue;
        const x0 = clampX(xOfMs(g.ms)), x1 = clampX(xOfMs(g.last + 1));
        if (x1 - x0 > 2) P.push(`<rect class="grave-band g-${g.kind}" x="${r1(x0)}" y="${bTop}" width="${r1(x1 - x0)}" height="${BAND}"/>`);
        P.push(`<line class="grave-line g-${g.kind}" x1="${r1(x0) + 0.5}" x2="${r1(x0) + 0.5}" y1="${r.top + 1}" y2="${bBot}"/>`);
        const lastB = GB.length && GB[GB.length - 1];
        if (lastB && lastB.n === n && x0 - lastB.x1 < 44) { lastB.items.push(g); lastB.x1 = x0; } else GB.push({ n, y: laneY, x0, x1: x0, items: [g] });
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
      seen.add(e.node + "\n" + e.subject + "\n" + Math.floor((e.ms - fr.a) / fr.size));
    }
    const MARKS = [];
    for (const m of fr.kind === "base" ? MENT : []) {  // per base bucket: not at finer bins
      const from = str(m.from), to = str(m.to);
      if (!NIDX.has(from) || !NIDX.has(to) || from === to) continue;
      const bins = new Map();
      for (const sig of arr(m.groups)) {
        const g = GBY.get(str(sig));
        if (!g) continue;
        for (const [i, c] of g.hourly[from]) {
          if (i < fr.ia || i >= fr.ib) continue;
          const b = fr.binOf(i);
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
    for (const [x, label] of xTicks(xOfMs, pxPerMs, W, padR, fr.a, Math.min(fr.b, TE))) {
      P.push(`<line class="base" x1="${r1(x) + 0.5}" x2="${r1(x) + 0.5}" y1="${bottomAll}" y2="${bottomAll + 4}"/>`);
      P.push(`<text class="axis" x="${r1(x)}" y="${H - 5}" text-anchor="middle">${esc(label)}</text>`);
    }
    P.push(`<rect class="brush" x="0" y="${RULER}" width="0" height="${bottomAll - RULER}" visibility="hidden"/>`);
    for (const r of rows) {
      P.push(`<rect class="lane-hit" data-row="${r.i}" x="${padL}" y="${r.top}" width="${r1(xEnd - padL)}" height="${LANE}"/>`);
      P.push(`<rect class="hit" data-row="${r.i}" x="${padL}" y="${r.bTop}" width="${r1(xEnd - padL)}" height="${BAND}"/>`);
    }
    GB.forEach((c, i) => {
      const kind = graveTop(c.items);
      const label = GRAVE_TAG[kind] + (c.items.length > 1 ? " ×" + c.items.length : "");
      const w = 10 + label.length * 6.6;
      const x = Math.min(xEnd - w, Math.max(padL - 4, (c.x0 + c.x1) / 2 - w / 2));
      P.push(`<g class="grave-badge g-${kind}" data-grave="${i}" tabindex="0" role="button" aria-label="${esc(graveLabel(c.n, c.items))}">` +
        `<rect x="${r1(x)}" y="${r1(c.y - 7.5)}" width="${r1(w)}" height="15" rx="3"/><text x="${r1(x + w / 2)}" y="${r1(c.y + 4)}" text-anchor="middle">⚠ ${esc(label)}</text></g>`);
    });
    P.push(`</svg>`);
    el.innerHTML = P.join("");
    const svg = el.firstElementChild;
    TLG = {
      svg, padL, slot, k, nb, rows, xOfMs, pxPerMs, marks: MARKS, sel, xms: XMS, frame: fr, graves: GB, plotW, xEnd,
      band: svg.querySelector(".hover-band"), brush: svg.querySelector(".brush"),
      binAt: (clientX) => Math.max(0, Math.min(nb - 1, Math.floor((clientX - svg.getBoundingClientRect().left - padL) / slot))),
    };
    $("tl-title").innerHTML = `${ERR ? "Errors" : "Entries"} per ${esc(sizeLabel(fr.size))} <span class="h-sub">${ERR ? "ERROR and FATAL" : "by level"}</span>`;
    $("tl-title").title = "One band per node; the columns are stacked by level. " +
      (ZOOMSEL ? "Drag across a band to zoom into a time range (Zoom is on)." : "Drag across a band to select a time window.");
    bindTimeline(TLG);
    showWindow(TLG);
    renderTlLegend();
    renderBins();
    renderZoomCtl();
  });

  /** A node at a glance: name, set, entries, errors, coverage (the timeline's row labels). */
  function nodeTip(n) {
    const c = obj(CN.get(n)), set = setOf(n), cov = COVT[n];
    return { h: trusted(`${nodeBadge(n)} ${esc(n)}`), sub: set.name || set.id || null,
      rows: [["Entries", fmt(num(c.entries) || NSUM[n])], ["Errors", isNum(c.errors) ? fmt(c.errors) : "", c.errors > 0 ? "e" : ""],
        ["Warnings", isNum(c.warnings) ? fmt(c.warnings) : ""], ["Coverage", cov ? rangeText(cov[0], cov[1]) : "no entries"],
        ["Gaps", GAPS[n].length ? plural(GAPS[n].length, "gap") : ""]] };
  }
  function renderTlLegend() {
    $("tl-legend").innerHTML = BUCKETS.filter((b) => (S.y !== "err" || b === "ERROR") && NODES.some((n) => LEV[n][b].some((v) => v > 0))).map((b) =>
      `<span title="${esc(LV_TITLE[b])}"><i class="sw sw-${b}"></i>${b}</span>`).join("") +
      `<span title="Time a node has no logs for: before its oldest or after its newest entry, and gaps"><svg width="12" height="12" aria-hidden="true"><rect class="nolog-bg" width="12" height="12"/><rect class="nolog" width="12" height="12"/></svg>no logs</span>` +
      (TL_NOTE ? `<span class="tl-muted">${esc(TL_NOTE)}</span>` : "");
    const counts = new Map();
    for (const e of EVENTS) counts.set(e.kind, (counts.get(e.kind) || 0) + 1);
    const ev = KINDS.map((kd) => `<span class="ev-key${kd === "job_run" && !S.jobs ? " off" : ""}" title="${esc(kd === "job_run" && !S.jobs ? "Job runs are hidden from the lanes: the Job runs button shows them" : `${kindCount(kd, counts.get(kd) || 0)} on all nodes (${kd})`)}">${glyphKey(kd)}${esc(kindLabel(kd))} <span class="n">${fmt(counts.get(kd))}</span></span>`);
    const arrows = EVENTS.some((e) => e.subject && e.subject !== e.node && NIDX.has(e.subject)) || MENT.length;
    if (arrows) ev.push(`<span class="ev-key" title="An entry on one node names another node: the arrow goes from the node that logs it to the node it names">↕ mention</span>`);
    if (GRAVE.length) {
      const by = new Map();
      for (const g of GRAVE) by.set(g.kind, (by.get(g.kind) || 0) + 1);
      const what = GRAVE_KINDS.filter((kd) => by.has(kd)).map((kd) => `${GRAVE_TAG[kd]} ${fmt(by.get(kd))}×`).join(" · ");
      const nodes = [...new Set(GRAVE.map((g) => NLABEL(g.node)))].join(", ");
      ev.unshift(`<button type="button" class="grave-key" data-grave-first title="Grave errors (out of memory, stack overflow, JVM errors, exhausted resources, FATAL) on ${esc(nodes)}: click to zoom to the first">⚠ ${esc(what)}</button>`);
    }
    $("tl-events").innerHTML = ev.length
      ? ev.join("")
      : `<span class="lg-k">No cluster events detected in this capture.</span>`;
  }
  function showWindow(geo, range) {
    if (!geo) return;
    const fr = geo.frame;
    const pos = (ms) => (ms - fr.a) / fr.size;
    const w = range || (S.t ? [pos(TA + S.t[0] * BMS), pos(TA + (S.t[1] + 1) * BMS - 1)] : null);
    if (!w) {
      geo.brush.setAttribute("visibility", "hidden");
      return;
    }
    const x0 = geo.padL + Math.max(0, Math.floor(w[0])) * geo.slot;
    const x1 = geo.padL + Math.min(geo.nb, Math.floor(w[1]) + 1) * geo.slot;
    // a zoomed timeline inside its window: shading all of it would say nothing
    if (x1 <= x0 || (!range && S.z && x0 <= geo.padL && x1 >= geo.padL + geo.plotW - 0.5)) {
      geo.brush.setAttribute("visibility", "hidden");
      return;
    }
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
      `<div class="tt-e"><div class="tt-eh">${glyphKey(e.kind)}<b>${esc(kindLabel(e.kind))}</b>${nodeBadge(e.node)}${e.subject ? `<span class="tt-arr">→</span>${nodeBadge(e.subject)}` : ""}<span class="tt-ts">${esc(tsSec(e.ts))}</span></div>` +
      (e.text ? `<div class="tt-x">${esc(trunc(e.text, 180))}</div>` : "") + `</div>`).join("");
    return `<div class="tt-h">${esc(list.length === 1 ? "Event" : list.length + " events")}</div>${shown}` +
      (list.length > 6 ? `<div class="tt-more">+${fmt(list.length - 6)} more</div>` : "") +
      `<div class="tt-note">Click to open the merged log here</div>`;
  }
  function bandTip(geo, row, b) {
    const r = geo.rows[row];
    const n = r.n;
    const fr = geo.frame, b0 = fr.start(b), b1 = fr.end(b);
    const out = [`<div class="tt-h">${esc(frameRange(fr, b))}</div>`];
    out.push(`<div class="tt-r tt-n">${nodeBadge(n)}<span>${esc(n)}</span></div>`);
    for (const g of GRAVE_N[n].filter((x) => x.last >= b0 && x.ms < b1).slice(0, 3)) {
      out.push(`<div class="tt-r grave-row"><b class="g-${g.kind}">⚠ ${esc(GRAVE_TAG[g.kind])}</b><span>${esc(tsSec(g.ts))}${g.count > 1 ? ` · ${fmt(g.count)} entries` : ""}</span></div>`);
    }
    if (!r.has[b]) out.push(`<div class="muted">No logs from ${esc(NLABEL(n))} here (${COVT[n] && b0 >= COVT[n][0] && b0 < COVT[n][1] ? "gap" : "outside its coverage"})</div>`);
    for (let s = BUCKETS.length - 1; s >= 0; s--) {
      const v = r.levAll[s][b];
      if (v) out.push(`<div class="tt-r"><b>${fmt(v)}</b><i class="sw-${BUCKETS[s]}"></i><span>${BUCKETS[s]}</span></div>`);
    }
    if (r.all[b]) {
      out.push(`<div class="tt-r tt-t"><b>${fmt(r.all[b])}</b><i></i><span>total${S.y === "pct" ? ` · ${r1(r.all[b] * r.f)}% of ${esc(NLABEL(n))}'s entries` : ""}</span></div>`);
    }
    if (geo.sel) out.push(`<div class="tt-r tt-t"><b>${fmt(r.g[b])}</b><i class="gkey"></i><span>selected group</span></div>`);
    if (NODES.length > 1) {
      const what = geo.sel ? "group" : S.y === "err" ? "errors" : "entries";
      out.push(`<div class="tt-sep">All nodes</div>` + geo.rows.map((o) =>
        `<div class="tt-r"><b>${o.has[b] ? fmt(geo.sel ? o.g[b] : o.tot[b]) : "–"}</b>${nodeBadge(o.n)}<span>${o.has[b] ? what : "no logs"}</span></div>`).join(""));
    }
    return out.join("");
  }
  /** Drawn bins holding a base bucket (the timeline's hour or 15 min) in which two or more nodes
      log more than 3× their usual errors (the median of their base buckets with errors):
      [{b, nodes: [{n, e, x}]}], each with its strongest bucket. Found at the base resolution,
      so a narrow chart (2-hour bins, where a spike averages out) marks the same moments. */
  function crossMoments(rows, fr) {
    const errs = rows.map((r) => (LEV[r.n] && LEV[r.n].ERROR) || []);
    const base = errs.map((a) => {
      const nz = [];
      for (let j = 0; j < NB && j < a.length; j++) if (a[j] > 0) nz.push(a[j]);
      nz.sort((x, y) => x - y);
      return nz.length ? nz[Math.floor((nz.length - 1) / 2)] : 0;
    });
    const best = new Map();
    for (let j = fr.ia; j < fr.ib; j++) {
      const nodes = [];
      rows.forEach((r, i) => {
        const e = errs[i][j] || 0;
        if (base[i] > 0 && e > 3 * base[i]) nodes.push({ n: r.n, e, x: e / base[i] });
      });
      if (nodes.length < 2) continue;
      const b = fr.binOf(j);
      if (b >= fr.nb) continue;
      const score = nodes.length * 1e9 + nodes.reduce((t, o) => t + o.e, 0);
      const old = best.get(b);
      if (!old || score > old.score) best.set(b, { b, nodes, score });
    }
    return [...best.values()].sort((x, y) => x.b - y.b);
  }
  const xmLabel = (m, fr) => `Cross-node moment ${frameRange(fr, m.b)}: ` +
    m.nodes.map((o) => `${NLABEL(o.n)} ${fmt(o.e)} errors in its peak ${binLabel(1)} (×${ratioText(o.x)} its usual)`).join(", ") +
    ". Opens the merged log ±30 s around its busiest moment.";
  function xmTip(m, fr) {
    return `<div class="tt-h">Cross-node moment · ${esc(frameRange(fr, m.b))}</div>` +
      m.nodes.map((o) => `<div class="tt-r"><b>${fmt(o.e)}</b>${nodeBadge(o.n)}<span>errors in the peak ${esc(binLabel(1))}, ×${esc(ratioText(o.x))} its usual</span></div>`).join("") +
      `<div class="muted tt-note">Two or more nodes log more than 3× their usual errors at once (usual: the median of a node's ${esc(binLabel(1))}s with errors). Click: the window moves here and the merged log opens ±30 s on all nodes around the busiest moment.</div>`;
  }
  /** A cross-node moment: window on its bin, then ±30 s on all nodes around its busiest moment
      (web app: the 10 s with the most errors on two or more nodes; static: the window's errors). */
  let xmReq = 0;
  function openMoment(geo, b) {
    const fr = geo.frame;
    const t = [fr.ia + b * fr.k, Math.min(NB - 1, fr.ia + (b + 1) * fr.k - 1)];
    const m = arr(geo.xms).find((x) => x.b === b);
    const fallback = () => {
      commit({ t, tab: "l", ml: "ERROR", at: "", ae: "", ctx: "", cts: "", ms: [], mn: m ? new Set(m.nodes.map((o) => o.n)) : null, mq: "" }, { keepPage: true });
      scrollToTabs();
    };
    if (!WEB_LOG || !URLS.context) return fallback();
    const req = ++xmReq;
    const u = new URL(URLS.entries, location.href);
    u.searchParams.set("since", isoOf(TA + t[0] * BMS));
    u.searchParams.set("until", isoOf(TA + (t[1] + 1) * BMS));
    u.searchParams.set("min_level", "ERROR");
    if (m) u.searchParams.set("node", m.nodes.map((o) => o.n).join(","));
    u.searchParams.set("columns", "id,ts,node,set");
    u.searchParams.set("limit", "10000");
    toast("Finding the busiest moment…");
    fetchJson(u.toString()).then((doc) => {
      if (req !== xmReq) return;
      const slots = new Map(); // 10 s slot -> {n, nodes, first}
      for (const e of arr(obj(doc).items)) {
        const ms = parseTs(e.ts);
        if (!isNum(ms) || e.set == null) continue;
        const k = Math.floor(ms / 1e4);
        const s = slots.get(k) || { n: 0, nodes: new Set(), first: e };
        s.n++;
        s.nodes.add(str(e.node));
        slots.set(k, s);
      }
      let best = null;
      for (const s of slots.values()) {
        const score = (s.nodes.size >= 2 ? 1e9 : 0) + s.n;
        if (!best || score > best.score) best = { score, e: s.first };
      }
      if (!best) return fallback();
      commit({ t, tab: "l", ctx: str(best.e.set) + "~" + str(best.e.id), cts: str(best.e.ts), at: "", ae: "" }, { keepPage: true });
      scrollToTabs();
    }).catch(() => { if (req === xmReq) fallback(); });
    return undefined;
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
      const gb = t.closest && t.closest("[data-grave]");
      if (gb) {
        const c = geo.graves[+gb.dataset.grave];
        if (c) showTip(e, graveTip(c.n, c.items));
        return;
      }
      if (t.dataset && t.dataset.xm != null) {
        const m = geo.xms.find((x) => x.b === +t.dataset.xm);
        if (m) showTip(e, xmTip(m, geo.frame));
        return;
      }
      const row = rowOf(t);
      if (row < 0) {
        tip.hidden = true;
        geo.band.setAttribute("visibility", "hidden");
        return;
      }
      const b = geo.binAt(e.clientX);
      geo.band.setAttribute("x", r1(geo.padL + b * geo.slot));
      geo.band.setAttribute("visibility", "visible");
      if (t.classList.contains("lane-hit")) {
        const near = nearEvents(geo, row, e.clientX);
        if (near.length) return showTip(e, eventTip(near));
        const n = NODES[row];
        const ms = geo.marks.filter((m) => m.from === n && m.b === b);
        if (ms.length) {
          return showTip(e, `<div class="tt-h">${esc(frameRange(geo.frame, b))}</div>` + ms.map((m) =>
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
      if (e.target.closest && e.target.closest("[data-grave]")) return;  // a badge is a button
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
          if (near.length) return openLogAt(near[0].ts, near[0].entry);
        }
        if (S.t && !S.z) commit({ t: null });
        return;
      }
      const fr = geo.frame;
      const a = fr.start(Math.min(d.b0, d.b1)), b = fr.end(Math.max(d.b0, d.b1));
      // zoom on: the selection is the zoom (the window follows it, in whole buckets); off: the window
      if (ZOOMSEL) zoomTo([a, b]);
      else commit({ t: bucketsCovering(a, b) });
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
    const openGrave = (el) => {
      const c = geo.graves[+el.dataset.grave];
      if (c) openLogAt(c.items[0].ts, c.items[0].entry);
    };
    svg.addEventListener("click", (e) => {
      const g = e.target.closest && e.target.closest("[data-grave]");
      if (g) return openGrave(g);
      const m = e.target.closest && e.target.closest("[data-xm]");
      if (m) openMoment(geo, +m.dataset.xm);
    });
    svg.addEventListener("keydown", (e) => {
      const g = e.target.closest && e.target.closest("[data-grave]");
      if (g && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        e.stopPropagation();
        return openGrave(g);
      }
      const m = e.target.closest && e.target.closest("[data-xm]");
      if (!m || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      e.stopPropagation();
      openMoment(geo, +m.dataset.xm);
    });
    svg.addEventListener("focusin", (e) => {
      const m = e.target.closest && e.target.closest("[data-xm]");
      const x = m && geo.xms.find((v) => v.b === +m.dataset.xm);
      if (!x) return;
      const r = m.getBoundingClientRect();
      showTip({ clientX: r.left, clientY: r.bottom }, xmTip(x, geo.frame));
    });
    svg.addEventListener("focusout", () => { tip.hidden = true; });
  }
  /** Quick windows ending at the last bucket: [buckets, label], those shorter than the capture. */
  const PRESETS = [[3.6e6, "1 h"], [6 * 3.6e6, "6 h"], [864e5, "24 h"], [7 * 864e5, "7 d"]]
    .map(([ms, label]) => [Math.round(ms / BMS), label]).filter(([nb]) => nb >= 1 && nb < NB);
  /** Move the window by its own width (dir -1 / +1), clamped to the capture. */
  function shiftWindow(dir) {
    if (S.z) {
      const [a, b] = S.z, w = b - a;
      const na = Math.max(TA, Math.min(TE - w, a + dir * w));
      if (na !== a) zoomTo([na, na + w]);
      return;
    }
    if (!S.t) return;
    const [a, b] = S.t, w = b - a + 1;
    const na = Math.max(0, Math.min(NB - w, a + dir * w));
    if (na !== a) commit({ t: [na, na + w - 1] });
  }
  /** "10-29 14:05" (the start with its date) or "14:20", with seconds when the zoom needs them. */
  function zoomStamp(iso, a, b, first) {
    const z = zoned(iso), secs = a % 6e4 || b % 6e4;
    return (first ? z.slice(0, 10) + " " : z.slice(0, 10) === zoned(isoOf(a)).slice(0, 10) ? "" : z.slice(5, 10) + " ") + z.slice(11, secs ? 19 : 16);
  }
  /** Zoom the timeline into [a, b) (null: the whole timeline); the window becomes its buckets. */
  function zoomTo(range) {
    const z = clampZoom(range);
    if (!z) return commit({ z: null }, { keepPage: true });
    commit({ z, t: bucketsCovering(z[0], z[1]) });
  }
  /** + : into the window, or (zoomed) into the middle third. */
  function zoomIn() {
    if (S.z) {
      const [a, b] = S.z, w = (b - a) / 3;
      if (w >= 1e4) zoomTo([a + w, b - w]);
    } else if (S.t) zoomTo([TA + S.t[0] * BMS, TA + (S.t[1] + 1) * BMS]);
    else toast("Select a time window on the timeline first");
  }
  /** − : three times as wide around the middle, kept inside the timeline. */
  function zoomOut() {
    if (!S.z) return;
    const [a, b] = S.z, w = (b - a) * 3;
    const na = Math.max(TA, Math.min(TE - w, (a + b) / 2 - w / 2));
    zoomTo([na, na + w]);
  }
  /** The zoom toggle: off shows the whole capture (the window stays) and drags select windows; on
      zooms into the window, if one is set, and drags zoom. */
  function toggleZoomSel() {
    ZOOMSEL = !ZOOMSEL;
    try { localStorage.setItem(ZS_KEY, ZOOMSEL ? "1" : "0"); } catch { /* storage unavailable */ }
    if (!ZOOMSEL && S.z) commit({ z: null }, { keepPage: true });
    else if (ZOOMSEL && S.t && !S.z) zoomTo([TA + S.t[0] * BMS, TA + (S.t[1] + 1) * BMS]);
    else renderTimeline();
    toast(ZOOMSEL ? "Zoom on: a drag zooms into the selection" : "Zoom off: a drag selects the time window");
  }
  /** Undo: the timeline's previous window, zoom and bin (Clear included). */
  function undo() {
    const v = UNDO.pop();
    if (!v) return toast("Nothing to undo");
    commit({ t: v.t, z: v.z, bin: v.bin }, { noUndo: true, keepPage: true });
  }
  const MAG = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4 14 14M5 7h4M7 5v4"/></svg>`;
  const UNDO_SVG = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5.5 3 2.5 6l3 3"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H7"/></svg>`;
  /** Undo and the zoom toggle, at the head of the timeline. */
  function renderZoomCtl() {
    const el = $("tl-zoom");
    if (!el) return;
    if (!NB || !isNum(TA)) { el.innerHTML = ""; return; }
    el.innerHTML = `<button type="button" class="btn small zbtn z-undo" data-undo ${UNDO.length ? "" : "disabled"} title="Undo: back to the previous zoom, window and bins, also after Clear (u)">${UNDO_SVG}<span>Undo</span></button>` +
      `<button type="button" class="btn small zbtn z-toggle" data-zoomsel aria-pressed="${ZOOMSEL}" title="${ZOOMSEL ? "Zoom is on: a drag across a band zooms into the selection, with finer bins. Click: the whole capture again, and drags select the time window" : "Zoom is off: a drag selects the time window. Click: zoom into the window, and drags zoom"}">${MAG}<span>Zoom</span></button>`;
  }
  /** The bins menu: automatic, or a finer or coarser bin the timeline's width allows. */
  function renderBins() {
    const el = $("tl-bins");
    if (!el) return;
    if (!NB || BINS.choices.length < 2) { el.innerHTML = ""; return; }
    const cur = S.bin && BINS.choices.includes(keyMs(S.bin)) ? sizeKey(keyMs(S.bin)) : "";
    const opts = [`<option value=""${cur ? "" : " selected"}>Auto (${esc(sizeShort(BINS.auto))})</option>`]
      .concat(BINS.choices.map((ms) => `<option value="${sizeKey(ms)}"${sizeKey(ms) === cur ? " selected" : ""}>${esc(sizeShort(ms))}</option>`));
    const finer = FINE ? "" : ` Bins under ${binLabel(1) === "hour" ? "an hour" : binLabel(1)} need the web app (ragnalog serve).`;
    el.innerHTML = `<label class="sel" title="Bin size of the timeline: Auto fits the shown range; pick a finer one to see more detail.${esc(finer)}"><span>Bins</span><select id="tl-binsel" aria-label="Bin size">${opts.join("")}</select></label>`;
  }
  const graveLabel = (n, items) => `${NLABEL(n)}: ` + (items.length === 1
    ? `${GRAVE_LABEL[items[0].kind]} at ${tsShort(items[0].ts)} ${zAt(items[0].ms)}${items[0].count > 1 ? `, ${fmt(items[0].count)} entries` : ""}`
    : `${items.length} grave errors from ${tsShort(items[0].ts)} ${zAt(items[0].ms)}`) + ". Opens the merged log there.";
  function graveTip(n, items) {
    const rows = items.slice(0, 6).map((g) => `<div class="tt-e"><div class="tt-eh"><b class="grave-tag g-${g.kind}">⚠ ${esc(GRAVE_TAG[g.kind])}</b>${nodeBadge(n)}<span class="tt-ts">${esc(tsSec(g.ts))}${g.count > 1 ? ` – ${esc(tsSec(g.last_ts).slice(6))} · ${fmt(g.count)} entries` : ""}</span></div>` +
      (g.text ? `<div class="tt-x">${esc(trunc(g.text, 180))}</div>` : "") + `</div>`).join("");
    return `<div class="tt-h grave-h">⚠ ${esc(items.length === 1 ? GRAVE_LABEL[items[0].kind] : `${items.length} grave errors`)}</div>${rows}` +
      (items.length > 6 ? `<div class="tt-more">+${fmt(items.length - 6)} more</div>` : "") +
      `<div class="tt-note">Click to open the merged log here</div>`;
  }
  function renderTimelineControls() {
    const modes = [["shared", "Shared", "One y-scale for every node: compare volumes"], ["node", "Per node", "Each band scaled to its own maximum: compare shapes"],
      ["pct", "% of node", "Each node's counts as a percentage of its entries in the range: compare distributions across nodes of different volume"],
      ["err", "Errors", "Only the ERROR and FATAL entries, one y-scale for every node: errors stay visible next to a flood of warnings"]];
    const tl = !!NB && NODES.length > 0 && isNum(TA);
    $("tl-y").innerHTML = tl ? `<span class="lbl">Scale</span>` + modes.map(([k, label, title]) =>
      `<button type="button" data-y="${k}" aria-pressed="${S.y === k}" title="${esc(title)}">${label}</button>`).join("") : "";
    $("tl-y").hidden = !tl;
    const ab = $("tl-arrows");
    ab.setAttribute("aria-pressed", String(S.arrows));
    ab.hidden = !tl || !(MENT.length || EVENTS.some((e) => e.subject));
    const jb = $("tl-jobs");
    jb.setAttribute("aria-pressed", String(S.jobs));
    jb.hidden = !tl || !EVENTS.some((e) => e.kind === "job_run");
    const w = $("tl-window");
    const presets = PRESETS.map(([nb, label]) => {
      const on = S.t && S.t[0] === NB - nb && S.t[1] === NB - 1 && (!S.z || (S.z[0] === TA + (NB - nb) * BMS && S.z[1] === TE));
      return `<button type="button" data-win="${nb}" aria-pressed="${!!on}" title="The last ${label} of the capture">${label}</button>`;
    }).join("");
    const seg = presets ? `<span class="seg win-seg" role="group" aria-label="Quick windows"><span class="lbl">Last</span>${presets}</span>` : "";
    if (!NB) {
      w.innerHTML = "";
    } else if (!S.t) {
      w.innerHTML = seg;
    } else if (S.z) {
      // zoomed: the zoom leads, the window (the table's buckets around it) follows it
      const rg = tRange(), [za, zb] = S.z;
      w.innerHTML = `<span class="win-what zoom-what"><span class="win-k">Zoom</span> <b>${esc(rangeText(za, zb, (s) => zoomStamp(s, za, zb, true), (s) => zoomStamp(s, za, zb, false)))}</b> (${esc(spanText(zb - za))})</span>` +
        `<span class="win-nav"><button type="button" class="btn small" data-shift="-1" ${za <= TA ? "disabled" : ""} title="Earlier, by the zoom's width ([)" aria-label="Earlier">‹</button>` +
        `<button type="button" class="btn small" data-shift="1" ${zb >= TE ? "disabled" : ""} title="Later, by the zoom's width (])" aria-label="Later">›</button></span>` +
        `<span class="win-nav"><button type="button" class="btn small" data-zoom="in" ${zb - za < 3e4 ? "disabled" : ""} title="Zoom into the middle third (+)" aria-label="Zoom in">+</button>` +
        `<button type="button" class="btn small" data-zoom="out" title="Zoom out to three times the width (−)" aria-label="Zoom out">−</button></span>` +
        `<button type="button" class="btn small" data-zoom="reset" title="Show the whole capture again; the window stays">Reset zoom</button>` +
        (rg ? `<span class="muted win-hours" title="The groups table counts whole buckets: the window around the zoom">table: ${esc(rangeText(rg[0], rg[1]))}</span>` : "") +
        seg + `<button type="button" class="btn small" data-clear-t title="Show the whole capture">Clear</button>`;
    } else {
      const rg = tRange();
      let entries = 0, errors = 0;
      for (const n of NODES) {
        entries += NWT[n] || 0;
        for (let i = S.t[0]; i <= S.t[1]; i++) errors += LEV[n].ERROR[i] || 0;
      }
      w.innerHTML = `<span class="win-what">Window <b>${esc(rangeText(rg[0], rg[1]))}</b> (${esc(spanText(rg[1] - rg[0]))})` +
        ` · ${fmt(entries)} entries` + (errors ? ` · <span class="w-err">${fmt(errors)} errors</span>` : "") + `</span>` +
        `<span class="win-nav"><button type="button" class="btn small" data-shift="-1" ${S.t[0] <= 0 ? "disabled" : ""} title="Previous window of the same length ([)" aria-label="Previous window">‹</button>` +
        `<button type="button" class="btn small" data-shift="1" ${S.t[1] >= NB - 1 ? "disabled" : ""} title="Next window of the same length (])" aria-label="Next window">›</button></span>` +
        seg + `<button type="button" class="btn small" data-clear-t title="Show the whole capture">Clear</button>`;
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
    // label, value, detail: the window range is what to read, the rest is quiet
    const meta = [];
    if (WIN) {
      const name = WIN_NAME.replace(/^./, (c) => c.toUpperCase());
      meta.push(`<span class="m-item"${tipAttr({ h: name, rows: [["From", `${tsFull(isoOf(WIN[0]))} ${zAt(WIN[0])}`],
        ["To", `${tsFull(isoOf(WIN[1]))} ${zAt(WIN[1])}`], ["Length", spanText(WIN[1] - WIN[0])]] })}>` +
        `<span class="m-k">${esc(name)}</span> <span class="m-v">${esc(rangeText(WIN[0], WIN[1]))}</span> ` +
        `<span class="m-k">${esc(spanText(WIN[1] - WIN[0]))}</span></span>`);
    } else if (WMODE) meta.push(`<span class="m-item"><span class="m-k">window</span> <span class="m-v">${esc(WMODE)}</span></span>`);
    const trunc_ = num(OPTS.truncated_groups);
    meta.push(trunc_ > 0
      ? `<span class="m-item m-k" title="${esc(`The ${fmt(ROWS.length)} largest groups are on this page; ${fmt(trunc_)} smaller ones are left out.`)}">${fmt(ROWS.length)} of ${fmt(ROWS.length + trunc_)} groups</span>`
      : `<span class="m-item m-k">${esc(plural(ROWS.length, "group"))}</span>`);
    for (const s of arr(CAP.skew)) {
      if (!s || !isNum(s.offset_ms)) continue;
      const a = str(s.a), b = str(s.b);
      meta.push(`<span class="m-item m-k"${tipAttr({ h: `Clock skew ${NLABEL(a)} ↔ ${NLABEL(b)}`,
        rows: [[trusted(nodeBadge(a)), a], [trusted(nodeBadge(b)), b], [`${NLABEL(b)} − ${NLABEL(a)}`, lagText(s.offset_ms)], ["From", isNum(s.pairs) ? plural(s.pairs, "event pair") : ""]],
        note: "The median time between matching cluster events on the two nodes: an estimate, the times on this page are not corrected." })}>` +
        `clock skew ${esc(NLABEL(a))}↔${esc(NLABEL(b))} ≈ ${esc(lagText(s.offset_ms))}</span>`);
    }
    $("c-meta").innerHTML = meta.join(`<span class="m-sep" aria-hidden="true">·</span> `);
    $("c-ctx").textContent = name;
    $("c-ctx").title = (CAP.capture ? `Capture ${CAP.capture}` : name) + " (click: back to the top)";
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
  let cardsKey = null;
  /** One slim row per node: the numbers to compare across nodes; the rest is in the name's tooltip. */
  const renderCards = guard(function renderCards() {
    // the node filter too: it presses a row's name button
    const key = WKEY + "|" + S.nd;
    if (cardsKey === key) return;
    cardsKey = key;
    const rg = tRange();
    $("c-cards").innerHTML = NODES.map((n) => {
      const c = obj(CN.get(n));
      const set = setOf(n);
      const href = URLS && URLS.set_report && set.id ? urlFill(URLS.set_report, { set: set.id }) : "";
      let entries = c.entries, errors = c.errors, warnings = c.warnings, eph = c.errors_per_hour;
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
      }
      const ev = evCounts({ ...c, node: n });
      const own = rg ? (EVN[n] || []).filter((e) => inRange(e.ms, rg)) : null;
      const evTotal = own ? own.length : ev.total;
      const kinds = own ? [...own.reduce((m, e) => m.set(e.kind, (m.get(e.kind) || 0) + 1), new Map())].sort((x, y) => y[1] - x[1]) : ev.kinds;
      // exact values, the whole capture's numbers in a window, and the events by kind are in each number's tooltip
      const inWin = rg ? " in the window" : "";
      const whole = (v) => (rg && isNum(v) ? ["Whole capture", fmt(v), "m"] : null);
      const share = (v) => (isNum(v) && entries > 0 ? ["Share of entries", pctText(v / entries)] : null);
      const stats = [
        ["entries", isNum(entries) ? compact(entries) : "—", { h: `${NLABEL(n)} entries${inWin}`, rows: [["Entries", fmt(entries)], whole(c.entries)] }],
        ["errors", isNum(errors) ? compact(errors) : "—", { h: `${NLABEL(n)} errors${inWin}`, rows: [["ERROR and FATAL", fmt(errors), errors > 0 ? "e" : ""], whole(c.errors), share(errors)] }, "e"],
        ["warnings", isNum(warnings) ? compact(warnings) : "—", { h: `${NLABEL(n)} warnings${inWin}`, rows: [["WARN", fmt(warnings)], whole(c.warnings), share(warnings)] }, "w"],
        ["errors/h", isNum(eph) ? (eph >= 100 ? fmt(Math.round(eph)) : String(r1(eph))) : "—", { h: `${NLABEL(n)} errors per hour${inWin}`,
          rows: [["Errors / h", isNum(eph) ? String(r1(eph)) : ""], rg && isNum(c.errors_per_hour) ? ["Whole capture", String(r1(c.errors_per_hour)), "m"] : null],
          note: `Errors divided by the hours ${rg ? "of the window " : ""}the node has logs for.` }, "", "q"],
        ["events", fmt(evTotal), { h: `${NLABEL(n)} events${inWin}`,
          rows: [...arr(kinds).map(([k, v]) => [trusted(`${glyphKey(k)}${esc(kindLabel(k))}`), fmt(v)])],
          note: "Cluster events detected in the log: the node's event lane in the timeline, listed in Interactions." }, "", "q"],
      ];
      const cov = obj(c.coverage);
      const since = cov.since ?? c.first_ts, until = cov.until ?? c.last_ts;
      const gaps = GAPS[n];
      const gapMs = gaps.reduce((x, [g0, g1]) => x + (g1 - g0), 0);
      const top = c.top_error && typeof c.top_error === "object" ? c.top_error : null;
      const baseline = c.baseline && typeof c.baseline === "object" ? c.baseline.name || c.baseline.id : c.baseline;
      const nameTip = tipCard({ h: trusted(`${nodeBadge(n)} ${esc(n)}`), sub: set.name || null,
        rows: [["Set id", set.id && set.id !== set.name ? mono(set.id) : ""],
          ["Logs from", since ? `${tsFull(since)} ${zAt(parseTs(since))}` : "no entries"],
          ["Logs to", since ? `${tsFull(until)} ${zAt(parseTs(until))}` : ""],
          ["Length", since ? spanText(parseTs(until) - parseTs(since)) : ""],
          ["Gaps", since ? (gaps.length ? `${plural(gaps.length, "gap")}, ${spanText(gapMs)} in all` : "none") : ""],
          ["Top error", top ? trusted(tplHtml(trunc(top.template, 110))) : ""],
          ["Top error count", top && isNum(top.count) ? fmt(top.count) : ""],
          ["Triage", c.verdict != null ? `${VERDICT[str(c.verdict)] || str(c.verdict).replace(/_/g, " ")}${baseline ? ` vs ${baseline}` : ""}` : ""]],
        note: MULTI ? "Click to show only the groups seen on this node." : null });
      const label = `${nodeBadge(n)}<span class="nr-nm">${esc(n)}</span>`;
      const head = MULTI
        ? `<button type="button" class="nr-name" data-node-filter="${esc(n)}" aria-pressed="${S.nd === n}" data-tip="${esc(nameTip)}">${label}</button>`
        : `<span class="nr-name" data-tip="${esc(nameTip)}">${label}</span>`;
      return `<div class="nrow ${ncls(n)}${S.nd === n ? " is-filter" : ""}" data-node="${esc(n)}">${head}` +
        (rg ? `<span class="nc-win" title="The numbers count the time window only">in window</span>` : "") +
        `<span class="nr-figs">${stats.map(([k, v, t, cls, opt]) => `<span class="nr-f${opt ? " opt" : ""}"${tipAttr(t)}>${cls === "e" || cls === "w" ? `<i class="lvdot lvdot-${cls}" aria-hidden="true"></i>` : ""}<b>${esc(v)}</b><small>${k}</small></span>`).join("")}</span>` +
        (c.verdict != null ? `<span class="nr-v"${tipAttr({ h: "Triage", rows: [["Verdict", VERDICT[str(c.verdict)] || str(c.verdict).replace(/_/g, " ")], ["Baseline", baseline || ""]],
          note: "The node's errors compared with the baseline set." })}>${verdictChip(c.verdict)}</span>` : "") +
        (href ? `<a class="nr-link" href="${esc(href)}" title="${esc(`Report of the log set ${set.name || set.id}`)}" aria-label="Report of ${esc(n)}">↗</a>` : "") +
        `</div>`;
    }).join("") || `<div class="empty">This capture has no nodes.</div>`;
  });

  // ---------------------------------------------------------------- groups table
  const PAGE = 100;
  const HEAT = NODES.length > 8;
  // spread and lead: the uneven cases are chips in the node's colour, "all" is quiet text; the
  // per-node rates and first-seen times behind them are in their tooltips (builders below)
  function spreadChip(r) {
    const sig = ` data-sig="${esc(r.id)}" data-tip-k="cs"`;
    if (!r.present.length) return `<span class="spr-all"${sig}>none in window</span>`;
    if (r.spread === "only") {
      const n = r.present[0] || r.top || "";
      return `<span class="schip ${ncls(n)}"${sig}><i></i>only ${esc(NLABEL(n))}</span>`;
    }
    if (r.spread === "skewed") {
      return `<span class="schip ${ncls(r.top)}"${sig}><i></i>${r.skew != null ? "×" + esc(ratioText(r.skew)) : "skewed"} ${esc(NLABEL(r.top))}</span>`;
    }
    return `<span class="spr-all"${sig}>${r.present.length === NODES.length ? "all" : esc(r.present.map(NLABEL).join(" "))}</span>`;
  }
  function leadChip(r) {
    if (r.first == null || r.present.length < 2) return "";
    if (r.lead == null) return "";
    const sig = ` data-sig="${esc(r.id)}" data-tip-k="cl"`;
    if (r.lead <= 0) return `<span class="lead s-same"${sig}>same time</span>`;
    return `<span class="lead ${ncls(r.first)}"${sig}><i></i>${esc(NLABEL(r.first))} first <span class="lag">${esc(lagText(r.lead))}</span></span>`;
  }
  /** The group of a tipped element: its data-sig / data-open, else its table row's. */
  function tipGroup(el) {
    const tr = el.closest("tr[data-id]");
    return GBY.get(el.dataset.sig || el.dataset.open || (tr ? tr.dataset.id : "")) || null;
  }
  /** Per 10,000 entries of node n (the whole capture's, or the window's): the volume-normalised rate. */
  const nodeVol = (n) => (S.t ? NWT[n] : NODE_VOL[n]);
  const per10k = (c, n) => (nodeVol(n) > 0 && c > 0 ? ratioText((c / nodeVol(n)) * 1e4) : "");
  const atZ = (iso) => (iso ? `${tsFull(iso)} ${zAt(parseTs(iso))}` : "");
  /** A group: its full template, numbers per node, spread, lead, logger, exception, signature. */
  function groupTip(row) {
    const r = V(row);
    const rows = [[S.t ? "In window" : "Entries", S.t ? `${fmt(r.total)} of ${fmt(row.total)}` : fmt(row.total)]];
    if (MULTI) NODES.forEach((n, i) => rows.push([trusted(nodeBadge(n)), r.counts[i] > 0 ? fmt(r.counts[i]) : "none", r.counts[i] > 0 ? "" : "m"]));
    if (MULTI) rows.push(["Spread", spreadText(r)]);
    if (MULTI && row.first != null && row.lead != null && row.present.length > 1) {
      rows.push(["Lead", row.lead <= 0 ? "same time" : `${NLABEL(row.first)} first, next ${lagText(row.lead)}`]);
    }
    if (row.logger) rows.push(["Logger", mono(row.logger)]);
    if (row.exc) rows.push(["Exception", mono(row.exc)]);
    rows.push(["Signature", mono(row.sig)]);
    return tipCard({ h: trusted(tplHtml(trunc(row.tpl, 420))), sub: row.lvl, rows });
  }
  function spreadText(r) {
    if (!r.present.length) return "on no node in the time window";
    if (r.spread === "only") return `only on ${NLABEL(r.present[0] || r.top || "")}`;
    if (r.spread === "skewed") return `${r.skew != null ? "×" + ratioText(r.skew) : "much"} more often on ${NLABEL(r.top)}`;
    return r.present.length === NODES.length ? "on every node at similar rates" : `on ${r.present.map(NLABEL).join(", ")} at similar rates`;
  }
  function spreadTip(row) {
    const r = V(row);
    const h = spreadText(r).replace(/^./, (c) => c.toUpperCase());
    const rows = NODES.map((n, i) => [trusted(nodeBadge(n)), r.counts[i] > 0
      ? `${fmt(r.counts[i])}` + (per10k(r.counts[i], n) ? ` · ${per10k(r.counts[i], n)} per 10k entries` : "") : "none", r.counts[i] > 0 ? "" : "m"]);
    return tipCard({ h, sub: S.t ? "in the time window" : null, rows,
      note: `The rate is per 10,000 entries of each node, so a busier node does not look skewed. ×N: the highest rate over the lowest; skewed from ×${ratioText(SKEW_MIN)}.` });
  }
  function leadTip(row) {
    const seen = NODES.map((n) => [n, obj(row.pn[n]).first_seen]).filter(([, t]) => t).sort((x, y) => parseTs(x[1]) - parseTs(y[1]));
    const t0 = seen.length ? parseTs(seen[0][1]) : NaN;
    return tipCard({ h: row.lead <= 0 ? "First seen at the same time" : `First seen on ${NLABEL(row.first)}, the next node ${lagText(row.lead)} later`,
      rows: seen.map(([n, t], i) => [trusted(nodeBadge(n)), i ? `${tsSec(t)} · ${lagText(parseTs(t) - t0)}` : tsSec(t)]),
      note: S.t ? "Over the whole capture, not the time window." : `Times in ${TZ}.` });
  }
  /** One group on one node: its count, share, rates and first/last seen. */
  function nodeCountTip(row, i) {
    const n = NODES[i], p = obj(row.pn[n]), c = V(row).counts[i];
    const rows = [];
    if (S.t) rows.push(["In window", fmt(c)]);
    rows.push([S.t ? "Whole capture" : "Entries", fmt(row.counts[i]), S.t ? "m" : ""]);
    if (MULTI && row.total > 0) rows.push(["Share of group", pctText(row.counts[i] / row.total)]);
    if (isNum(p.rate_per_hour) && row.counts[i] > 0) rows.push(["Per hour", String(r1(p.rate_per_hour))]);
    if (per10k(c, n)) rows.push(["Per 10k entries", per10k(c, n)]);
    rows.push(["First seen", atZ(p.first_seen)], ["Last seen", atZ(p.last_seen)]);
    return tipCard({ h: trusted(`${nodeBadge(n)} ${esc(n)}`), sub: trunc(row.tpl, 90), rows });
  }
  if (TIPS) {
    TIPS.register("cg", (el) => { const r = tipGroup(el); return r ? groupTip(r) : ""; });
    TIPS.register("cs", (el) => { const r = tipGroup(el); return r ? spreadTip(r) : ""; });
    TIPS.register("cl", (el) => { const r = tipGroup(el); return r ? leadTip(r) : ""; });
    TIPS.register("cn", (el) => { const r = tipGroup(el); return r ? nodeCountTip(r, +el.dataset.n) : ""; });
  }
  function cellBar(c) {
    if (!(c > 0)) return 0;
    const lmin = Math.log10(SC.min), span = Math.log10(SC.max) - lmin;
    return Math.max(2, span > 0 ? ((Math.log10(c) - lmin) / span) * 100 : 100);
  }
  function renderHead() {
    const th = (cls, label, key, title) => {
      const sorted = !key ? "" : ` aria-sort="${S.sort === key ? (key === "template" ? "ascending" : "descending") : "none"}"`;
      return `<th class="${cls}${key ? " sortable" : ""}"${key ? ` data-sort="${esc(key)}"` : ""}${sorted} title="${esc(title || "")}">${key ? `<button type="button" class="th-sort" data-sort="${esc(key)}">${label}</button>` : label}</th>`;
    };
    let cells = th("c-lv", "Level", "level", "Level") + th("c-tpl", "Template", "template", "Template") +
      (S.t ? th("c-total", "In window", "count", "Entries on all nodes in the time window; below: the capture's total")
        : th("c-total", "Total", "count", "Entries on all nodes"));
    if (HEAT) {
      cells += `<th class="c-heat" style="--hn:${NODES.length}" title="Count per node (darker = more, log scale)"><div class="heat-head">${NODES.map((n, i) => `<span title="${esc(n)}">${i + 1}</span>`).join("")}</div></th>`;
    } else {
      for (const n of NODES) cells += th(`c-n ${ncls(n)}`, `${nodeBadge(n)}<span class="nid">${esc(trunc(n, 12))}</span>`, MULTI ? "node:" + n : "", MULTI ? `Count on ${n}; click to sort` : `Count on ${n}`);
    }
    if (MULTI) {
      cells += th("c-spread", "Spread", "skew", "all = on every node at similar rates; only = on one node; ×N = rate N times higher on that node (normalised by node volume)" +
        (S.t ? "; computed inside the time window" : ""));
      cells += th("c-lead", "Lead", "lead", "Which node saw the group first, and how much later the next node did (within the window)");
    }
    $("t-head").innerHTML = `<tr>${cells}</tr>`;
  }
  function rowHtml(row) {
    const r = V(row);
    const cls = (r.id === S.sig ? "is-sel " : "") + (r.id === CUR ? "is-cur" : "");
    let nodes = "";
    if (HEAT) {
      const mx = Math.max(1, ...r.counts);
      nodes = `<td class="c-heat"><div class="heat">${NODES.map((n, i) => {
        const c = r.counts[i];
        const a = c > 0 ? r1(0.18 + 0.82 * (Math.log10(c + 1) / Math.log10(mx + 1))) : 0;
        return `<i class="${c > 0 ? "" : "z"}" style="opacity:${c > 0 ? a : 1}" data-tip-k="cn" data-n="${i}"></i>`;
      }).join("")}</div></td>`;
    } else {
      nodes = NODES.map((n, i) => {
        const c = r.counts[i];
        return `<td class="c-n ${ncls(n)}" data-tip-k="cn" data-n="${i}">` +
          `<div class="cnt"><span class="num${c > 0 ? "" : " zero"}">${c > 0 ? fmt(c) : "·"}</span>` +
          (CMAX >= 100 ? `<span class="track"><i style="width:${r1(cellBar(c))}%"></i></span>` : "") + `</div></td>`;
      }).join("");
    }
    return `<tr data-id="${esc(r.id)}" class="${cls}" tabindex="-1">` +
      `<td class="c-lv">${badge(r.lvl, r.level)}</td>` +
      `<td class="c-tpl" data-tip-k="cg"><span class="tpl">${tplHtml(r.tpl)}</span><span class="sub">${loggerHtml(r.logger, true)}${r.exc ? " · " + excHtml(r.exc, true) : ""}</span></td>` +
      (S.t ? `<td class="c-total" title="${fmt(r.total)} in the window of ${fmt(row.total)} in total">${fmt(r.total)}<small class="of"> / ${esc(compact(row.total))}</small></td>`
        : `<td class="c-total">${fmt(r.total)}</td>`) + nodes +
      (!MULTI ? "" : `<td class="c-spread">${spreadChip(r)}</td>` +
        (S.t ? `<td class="c-lead is-whole">${leadChip(row)}</td>` : `<td class="c-lead">${leadChip(r)}</td>`)) + `</tr>`;
  }
  function pagerHtml(pages) {
    if (pages <= 1) return "";
    return `<button type="button" class="btn small" data-page="${S.p - 1}" data-dir="prev" ${S.p <= 0 ? "disabled" : ""}>‹ Prev</button>` +
      `<span>Page ${S.p + 1} of ${pages}</span>` +
      `<button type="button" class="btn small" data-page="${S.p + 1}" data-dir="next" ${S.p >= pages - 1 ? "disabled" : ""}>Next ›</button>`;
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
    $("groups").classList.toggle("in-win", !!S.t); // "N / total" on one line: a wider column
    const empty = $("t-empty");
    empty.hidden = page.length > 0;
    $("t-head").hidden = !page.length;
    if (!page.length) {
      if (!ROWS.length) empty.textContent = "This capture has no groups.";
      else {
        const chips = activeFilters().map(([k, label]) =>
          `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
        empty.innerHTML = `<div>No group matches all of these filters. Remove one to see more.</div>` + (chips ? `<div class="fchips">${chips}</div>` : "") +
          `<button type="button" class="btn" data-clear="all">Remove all filters</button>`;
      }
    }
    let entries = 0;
    for (const r of LIST) entries += V(r).total;
    const shown = LIST.length ? `${fmt(start + 1)}–${fmt(start + page.length)} of ` : "";
    const sep = `<span class="m-sep" aria-hidden="true">·</span>`;
    $("t-summary").innerHTML = `<span class="s-main">${shown}${fmt(LIST.length)} groups</span>` +
      (LIST.length < ROWS.length ? ` (of ${fmt(ROWS.length)} in this report)` : "") +
      `${sep}${fmt(entries)} entries` + (S.t ? " in the window" : "") +
      (num(OPTS.truncated_groups) > 0 ? `${sep}${fmt(OPTS.truncated_groups)} smaller groups not in the report` : "");
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
      spCounts[V(r).spread]++;
    }
    const spLabel = { any: "Any", all: "All", only: "Only one", skewed: "Skewed" };
    const spTitle = { any: "Any spread", all: "On every node at similar rates", only: "Seen on one node only", skewed: "Much more often on one node (normalised by node volume)" };
    $("f-spread").hidden = !MULTI;
    $("f-node").closest("label").hidden = !MULTI;
    $("f-spread").innerHTML = `<span class="lbl">Spread</span>` + SPREADS.map((k) =>
      `<button type="button" data-sp="${k}" aria-pressed="${S.sp === k}" title="${esc(spTitle[k])}">${spLabel[k]}<span class="n">${fmt(spCounts[k])}</span></button>`).join("");
    const ndCounts = NODES.map((n, i) => ROWS.filter((r) => V(r).counts[i] > 0 && match(r, "nd")).length);
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
  let drawerStale = false; // the drawn group's data changed (examples arrived): redraw in place
  /** Web app: the link in the head of the examples section (set by drawerHtml for its group). */
  let EX_ALL = "";
  /** A section head: the label, its count (a trailing "(…)") muted, then the action at the right. */
  const secHead = (title) => {
    const m = /^(.*?) \((.+)\)$/.exec(title);
    return m ? `${m[1]} <span class="sec-n">${m[2]}</span>` : title;
  };
  const secId = (id, title, body) => `<section class="d-sec" id="${id}"><h3>${secHead(title)}${id === "d-s-examples" ? EX_ALL : ""}</h3>${body}</section>`;
  /** Web app: "All N entries ↗", the group's entries on all nodes in a table in a new tab; inside a window, its entries only. */
  function allEntriesLink(r) {
    if (!WEB_LOG || !r.sig) return "";
    const p = new URLSearchParams();
    p.set("signature", r.sig);
    const rg = tRange();
    if (rg) {
      p.set("since", isoOf(rg[0]));
      p.set("until", isoOf(rg[1]));
    }
    const n = rg ? winOf(r).total : r.total;
    const href = URLS.entries.replace(/^\/api\//, "/");
    return `<a class="d-all" href="${esc(href + "?" + p.toString())}" target="_blank" rel="noopener" title="Every entry of this group on all nodes${rg ? " inside the selected window" : ""}, as a table in a new tab">All ${fmt(n)} ${n === 1 ? "entry" : "entries"}${rg ? " in window" : ""} ↗</a>`;
  }
  const copyBtn = (text, label = "Copy") => `<button type="button" class="btn small" data-copy="${esc(text)}">${label}</button>`;
  const iconCopy = (text, what) =>
    `<button type="button" class="icon-btn" data-copy="${esc(text)}" title="Copy ${esc(what)}" aria-label="Copy ${esc(what)}">⧉</button>`;
  // entry cards: the code is shared with the report page and the entries page (entry-card.js)
  const { logInline, msgHtml, traceHtml, entryHtml, toggleRaw, showAllLines, fullText } = RagnalogEntryCard.create({
    badge: (name, level) => badge(name, level),
    nodeBadge: (n) => nodeBadge(n),
    bucketOf: (level) => bucketOf(level),
    levelNum: (name) => levelNum(name),
    tsFull: (iso) => tsFull(iso),
    tz: () => TZ,
    entry: (id) => entryOf(id),
    /** Web app: the full raw entry as plain text (opens in a new tab). */
    rawUrl: (id, e) => (URLS && URLS.raw && e.set != null ? urlFill(URLS.raw, { set: e.set, id }) : ""),
    fullLoads: () => !!(URLS && URLS.examples),
    /** Context on all nodes and a link to the entry's group. */
    actions: (id, e) => {
      const sig = str(e.signature) || ESIG.get(str(id)) || "";
      const node = str(e.node);
      return (node && e.set != null && e.ts ? `<button type="button" class="btn small" data-ctx="${esc(str(e.set) + "~" + id)}" data-cts="${esc(e.ts)}" title="What every node logged within 30 s of this entry">±30 s on all nodes</button>` : "") +
        (sig && GBY.has(sig) && sig !== S.sig ? `<button type="button" class="btn small" data-open="${esc(sig)}">Open group</button>` : "");
    },
  });
  /** The entry of a card: an example, or a row of the merged log. */
  const entryOf = (id) => ENTRIES[id] || (VIEW.find((r) => r.id === id) || {}).e || {};
  function rawOf(id) {
    return fullText(obj(entryOf(id)));
  }
  function nodeTable(r) {
    const w = S.t ? winOf(r) : null;
    const rows = NODES.map((n, i) => {
      const p = obj(r.pn[n]);
      const c = r.counts[i];
      const wc = w ? `<td class="r"><b>${w.counts[i] > 0 ? fmt(w.counts[i]) : "·"}</b></td>` : "";
      return `<tr><td title="${esc(n)}">${nodeBadge(n)}<span class="nm">${esc(n)}</span></td>${wc}<td class="r">${c > 0 ? fmt(c) : "·"}</td>` +
        `<td class="r rate">${isNum(p.rate_per_hour) && c > 0 ? esc(r1(p.rate_per_hour)) : ""}</td>` +
        `<td class="t">${tsHtml(ts16(p.first_seen))}</td><td class="t">${tsHtml(ts16(p.last_seen))}</td></tr>`;
    }).join("");
    return `<div class="d-nodes-wrap"><table class="d-nodes"><thead><tr><th>Node</th>${w ? `<th class="r" title="Entries in the time window">In window</th><th class="r">Total</th>` : `<th class="r">Count</th>`}` +
      `<th class="r rate">Per hour</th><th>First seen (${esc(TZ)})</th><th>Last seen</th></tr></thead><tbody>${rows}</tbody></table></div>`;
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
      }).join(", ") + `<button type="button" class="linkbtn act" data-at="${esc(evs[0].ts)}" data-ae="${esc(evs[0].entry)}">show the first</button></p>`);
    }
    return out.length ? out.join("") : `<div class="muted">No co-bursts, mentions or events involve this group.</div>`;
  }
  /** The ragnalog commands for one group of this capture. */
  function groupCmds(r) {
    const cmds = [
      `ragnalog matrix ${CAPREF} --signature ${r.sig}`,
      `ragnalog timeline ${CAPREF} --by-node --signature ${r.sig}`,
      `ragnalog entries ${CAPREF} --signature ${r.sig} --limit 50`,
    ];
    for (const n of r.present) cmds.push(`ragnalog show ${setRef(n)} ${r.sig}`);
    return cmds;
  }
  /** The dialog behind "Identifiers & CLI": the capture id, the log sets, the commands, the open group. */
  function renderIds() {
    const line = (k, text) => `<div class="id-row"><span class="k">${esc(k)}</span><code>${esc(text)}</code>${copyBtn(text)}</div>`;
    const out = [`<section><h3>Capture</h3>`];
    if (CAP.capture) out.push(line("Capture id", CAP.capture));
    if (CAP.cluster) out.push(line("Cluster", CAP.cluster));
    out.push(`</section>`);
    const sets = NODES.map((n) => ({ n, set: setOf(n) })).filter((x) => x.set.name || x.set.id);
    if (sets.length) {
      out.push(`<section><h3>Log sets</h3>` + sets.map(({ n, set }) =>
        `<div class="id-row"><span class="k">${nodeBadge(n)}</span><code>${esc(set.name || set.id)}</code>${copyBtn(set.name || set.id)}</div>` +
        (set.id && set.name && set.id !== set.name ? `<div class="id-row id-sub"><span class="k">id</span><code>${esc(set.id)}</code>${copyBtn(set.id)}</div>` : "")).join("") + `</section>`);
    }
    const cmds = ["capture", "matrix", "events", "interactions", "timeline"].map((c) => `ragnalog ${c} ${CAPREF}` + (c === "timeline" ? " --by-node" : ""));
    cmds.push(`ragnalog entries ${CAPREF} --limit 50`, `ragnalog report ${CAPREF} --out report.html`);
    out.push(`<section><h3>Commands for this capture</h3><div class="cli">` + cmds.map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div></section>`);
    const sel = S.sig ? GBY.get(S.sig) : null;
    if (sel) {
      out.push(`<section><h3>Open group</h3>${line("Signature", sel.sig)}<div class="cli">` +
        groupCmds(sel).map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div></section>`);
    }
    $("ids-body").innerHTML = out.join("");
  }
  function drawerHtml(r, inList) {
    const out = [];
    if (!inList) out.push(`<div class="d-banner">This group is not in the current filter.</div>`);
    // the figures sit on the badge row (they used to be a panel of their own; the per-node table
    // right below has the detail)
    const nNodes = V(r).counts.filter((c) => c > 0).length; // in the window when one is set
    const figs = [`<span><b>${fmt(r.total)}</b> ${r.total === 1 ? "entry" : "entries"}</span>`];
    if (S.t) figs.push(`<span><b>${fmt(winOf(r).total)}</b> in window</span>`);
    figs.push(`<span>${NODES.length === 1 ? (nNodes ? "on the node" : "not on the node") : `${fmt(nNodes)} of ${fmt(NODES.length)} nodes`}</span>`);
    out.push(`<div class="d-head">${badge(r.lvl, r.level)}${MULTI ? spreadChip(V(r)) + leadChip(r) : ""}` +
      `<span class="d-figs">${figs.join(" · ")}</span></div>`);
    out.push(`<h2 class="d-tpl" tabindex="-1">${tplHtml(r.tpl)}</h2>`);
    if (r.exc || r.logger) {
      // one line: the logger with its package abbreviated, the exception by its class name; the full
      // names are in the tooltips
      out.push(`<div class="d-sub">${r.logger ? `<span title="Logger ${esc(r.logger)}">${loggerHtml(r.logger, true)}</span>` : ""}${r.exc ? `<span title="Exception ${esc(r.exc)}">${excHtml(r.exc, true)}</span>` : ""}</div>`);
    }
    out.push(secId("d-s-nodes", "Per node", nodeTable(r)));
    out.push(secId("d-s-hourly", `Per ${esc(binLabel(1))} and node` + (S.t ? " (window shaded)" : ""),
      `<div class="d-chart" id="d-hourly"></div>`));  // the lines take the node colours of the table above
    EX_ALL = allEntriesLink(r);
    const exNodes = NODES.filter((n) => arr(r.examples[n]).length);
    for (const n of Object.keys(r.examples)) if (!NIDX.has(n) && arr(r.examples[n]).length) exNodes.push(n);
    let exBody, exTitle = "Example entries per node";
    if (S.t && URLS && URLS.examples) {
      // web app with a time window: each node's first entries of the group inside the window
      exTitle = "Examples in the window, per node";
      const w = winOf(r);
      const nodes = NODES.filter((n, i) => w.counts[i] > 0 && setOf(n).id);
      if (!nodes.length) exBody = `<div class="muted">No entries of this group in the window.</div>`;
      else {
        const cur = nodes.includes(S.ex) ? S.ex : nodes[0];
        const ids = WIN_EXN.get(winExKey(r, cur));
        if (!ids) loadWindowExamples(r, cur);
        exBody = `<div class="ex-tabs" role="tablist">${nodes.map((n) =>
          `<button type="button" role="tab" data-ex="${esc(n)}" aria-selected="${n === cur}" title="${esc(fmt(w.counts[NIDX.get(n)]))} entries in the window">${nodeBadge(n)}<span class="n">${fmt(w.counts[NIDX.get(n)])}</span></button>`).join("")}</div>` +
          (!ids && EXN_ERR.has(winExKey(r, cur))
            ? `<div class="ex-err">Could not load the examples of ${esc(cur)} (${esc(EXN_ERR.get(winExKey(r, cur)))}). <button type="button" class="btn small" data-retry-ex>Retry</button></div>`
            : !ids ? `<div class="muted">Loading the examples of ${esc(cur)} in the window…</div>`
            : ids.length ? ids.map((id) => entryHtml(id, ENTRIES[id])).join("") : `<div class="muted">No entries of ${esc(cur)} in the window.</div>`);
      }
    } else if (!exNodes.length) exBody = `<div class="muted">No example entries in this report.</div>`;
    else {
      const cur = exNodes.includes(S.ex) ? S.ex : exNodes[0];
      const ids = arr(r.examples[cur]).map(String);
      const have = ids.filter((id) => ENTRIES[id]);
      loadFullExamples(r, cur, ids);
      exBody = `<div class="ex-tabs" role="tablist">${exNodes.map((n) =>
        `<button type="button" role="tab" data-ex="${esc(n)}" aria-selected="${n === cur}">${nodeBadge(n)}<span class="n">${fmt(arr(r.examples[n]).length)}</span></button>`).join("")}</div>` +
        (have.length ? have.map((id) => entryHtml(id, ENTRIES[id])).join("") : `<div class="muted">The examples of ${esc(cur)} are not in this report.</div>`);
    }
    out.push(secId("d-s-examples", exTitle, exBody));
    out.push(secId("d-s-links", "Co-bursts, mentions and events", relatedHtml(r)));
    out.push(`<details class="d-sec d-idcli" id="d-s-cli"><summary>Identifiers &amp; CLI</summary>` +
      `<div class="cli"><div><span class="k">Signature</span><code class="ident">${esc(r.sig)}</code>${copyBtn(r.sig)}</div></div>` +
      `<div class="cli">` + groupCmds(r).map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div></details>`);
    return out.join("");
  }
  // drawer chart: null clips automatically (max > 4 × p95), true/false is the user's choice
  let DCLIP = null;
  let DGEO = null;
  /** The drawer chart and its title, which names the bin drawn (an hour, 2 hours, …). */
  function drawHourly(r) {
    const el = $("d-hourly");
    if (!el) return;
    DGEO = drawLines(el, r) || null;
    const h3 = el.parentElement && el.parentElement.querySelector("h3");
    if (!h3 || !DGEO) return;
    h3.innerHTML = `Per ${esc(binLabel(DGEO.k))} and node` + (S.t ? ` <span class="sec-n">(window shaded)</span>` : "") +
      (DGEO.spiky ? `<button type="button" class="btn small toggle" data-clip aria-pressed="${DGEO.clip}" title="Clip the y-axis to the 95th percentile so spikes do not flatten the rest; clipped points are labelled with their value">Clip spikes</button>` : "");
  }
  /** Per-node lines of one group over the timeline buckets (shared y-axis, window shaded). */
  function drawLines(el, r) {
    if (!el) return undefined;
    if (!NB) { el.innerHTML = `<p class="muted">No timeline in this report.</p>`; return undefined; }
    const W = Math.max(240, Math.floor(el.clientWidth || 400));
    const H = 84, padL = 42, padR = 8, padB = 20;
    const k = chooseBin(NB, W - padL - padR);
    const nb = Math.ceil(NB / k);
    const series = NODES.map((n) => ({ n, v: binArr(denseOf(r.hourly[n]), k, nb), has: binHas(n, k, nb) }))
      .filter((s, i) => r.counts[i] > 0 || NODES.length <= 8);
    let max = 0;
    const nz = [];
    for (const s of series) for (let b = 0; b < nb; b++) {
      max = Math.max(max, s.v[b]);
      if (s.v[b] > 0) nz.push(s.v[b]);
    }
    nz.sort((x, y) => x - y);
    const p95 = nz.length ? nz[Math.floor(0.95 * (nz.length - 1))] : 0;
    const spiky = p95 > 0 && max > 4 * p95;
    const clip = spiky && DCLIP !== false;
    const padT = clip ? 18 : 8;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const slot = plotW / nb;
    const ticks = niceTicks(clip ? p95 : max, 2, true);  // two steps: the chart is short
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
    const over = new Float64Array(nb); // clipped: the highest value per bin, labelled above the plot
    for (const s of series) {
      const pts = [];
      for (let b = 0; b < nb; b++) {
        if (s.v[b] > yMax) over[b] = Math.max(over[b], s.v[b]);
        pts.push(s.has[b] || s.v[b] ? [padL + b * slot + slot / 2, Math.max(padT, padT + plotH - (plotH * s.v[b]) / yMax)] : null);
      }
      const { d, dots } = linePath(pts);
      P.push(`<g class="${ncls(s.n)}">` + (d ? `<path class="nline" d="${d}"/>` : "") +
        dots.map(([x, y]) => `<circle class="ndot" cx="${r1(x)}" cy="${r1(y)}" r="3.5"/>`).join("") + `</g>`);
    }
    // one label per run of clipped bins (its maximum), at least 40px apart, as on the timeline
    for (let b = 0, lastX = -Infinity; b < nb; b++) {
      if (!over[b]) continue;
      let e = b, top = over[b];
      while (e + 1 < nb && over[e + 1]) top = Math.max(top, over[++e]);
      const cx = r1(padL + ((b + e + 1) / 2) * slot);
      if (cx - lastX >= 40) {
        const x = Math.max(padL + 14, Math.min(cx, W - padR - 14));
        P.push(`<text class="clip-lbl" x="${x}" y="${padT - 5}" text-anchor="middle">${esc(compact(top))}</text>`);
        lastX = cx;
      }
      b = e;
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
    return { k, spiky, clip };
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
        redrawDrawer();
      })
      .catch(() => FULL.delete(key)); // asked again the next time the drawer shows it
  }
  /** Web app, time window: a node's first entries of a group inside the window (examples URL with since/until). */
  const WIN_EXN = new Map(); // "set|sig|a-b" -> entry ids in time order
  const WIN_EXN_PENDING = new Set();
  const EXN_ERR = new Map(); // failed keys -> message; not asked again until "Retry"
  const winExKey = (r, node) => setOf(node).id + "|" + r.sig + "|" + WKEY;
  function loadWindowExamples(r, node) {
    const set = setOf(node).id;
    const key = winExKey(r, node);
    if (!set || WIN_EXN.has(key) || WIN_EXN_PENDING.has(key) || EXN_ERR.has(key)) return;
    WIN_EXN_PENDING.add(key);
    const rg = tRange();
    const u = new URL(urlFill(URLS.examples, { set, sig: r.sig }), location.href);
    u.searchParams.set("max_raw", "0");
    u.searchParams.set("limit", "3");
    u.searchParams.set("since", isoOf(rg[0]));
    u.searchParams.set("until", isoOf(rg[1]));
    fetchJson(u.toString())
      .then((doc) => {
        const items = obj(doc && doc.items);
        for (const [id, e] of Object.entries(items)) ENTRIES[id] = Object.assign({}, ENTRIES[id] || {}, e, { node, set });
        WIN_EXN.set(key, Object.keys(items).sort((x, y) => cmpStr(str(items[x].ts), str(items[y].ts)) || cmpStr(x, y)));
      })
      .catch((err) => EXN_ERR.set(key, String(err && err.message ? err.message : err)))
      .finally(() => {
        WIN_EXN_PENDING.delete(key);
        redrawDrawer();
      });
  }
  const renderDrawer = guard(function renderDrawer() {
    const dr = $("drawer");
    if (!S.sig || S.tab !== "g") {
      dr.hidden = true;
      document.body.classList.remove("drawer-open");
      drawnKey = null;
      drawerStale = false;
      DGEO = null;
      resetDrawerSpy(dr);
      return;
    }
    dr.hidden = false;
    document.body.classList.add("drawer-open");
    const idx = LIST.findIndex((x) => x.id === S.sig);
    $("d-pos").innerHTML = `<b>${idx >= 0 ? fmt(idx + 1) : "–"}</b> of ${fmt(LIST.length)}`;
    const r0 = GBY.get(S.sig);
    $("d-bar-id").innerHTML = r0 ? `${badge(r0.lvl, r0.level)}<span class="d-bar-tpl" title="${esc(r0.tpl)}">${esc(r0.tpl)}</span>` : "";
    const key = [S.sig, S.t, S.ex, idx >= 0].join("|");
    if (key === drawnKey && !drawerStale) return;
    const sameGroup = drawnKey && drawnKey.split("|")[0] === S.sig;
    drawnKey = key;
    drawerStale = false;
    const r = GBY.get(S.sig);
    const body = $("drawer-body");
    const top = body.scrollTop;
    body.innerHTML = r ? drawerHtml(r, idx >= 0) : `<div class="empty">Group ${esc(S.sig)} is not in this report.</div>`;
    body.scrollTop = sameGroup ? top : 0;
    if (!sameGroup) {
      DCLIP = null; // each group starts clipped when spiky
      resetDrawerSpy(dr);
      body.classList.remove("fresh");
      void body.offsetWidth;
      body.classList.add("fresh");
    }
    if (r) drawHourly(r);
  });
  /** Forget the scroll spy's state: a hidden or refilled body scrolls to the top without an event. */
  function resetDrawerSpy(dr) {
    dr.classList.remove("scrolled");
    for (const b of document.querySelectorAll("#d-nav [data-jump].on")) b.classList.remove("on");
  }
  /** Redraw the drawer after its data changed, keeping its scroll position (no fade). */
  function redrawDrawer() {
    drawerStale = true;
    renderDrawer();
  }

  // ---------------------------------------------------------------- interactions
  const tplRef = (sig, tpl) => {
    const g = GBY.get(str(sig));
    const text = tplHtml(trunc(tpl || (g && g.tpl) || sig, 140));
    return g ? `<button type="button" class="tplref" data-open="${esc(g.id)}" data-tip-k="cg">${text}</button>` : `<b>${text}</b>`;
  };
  /** The data-show of a co-burst: each group on its own node only (NODE:SIGNATURE; a side whose
   * node the page lacks: on any node). */
  const coBurstShow = (a, b) => esc(JSON.stringify({
    ms: [...new Set([a, b].filter((x) => x.signature != null)
      .map((x) => (NIDX.has(str(x.node)) ? `${str(x.node)}:` : "") + str(x.signature)))],
    mn: [],
  }));
  function coBurstSentence(c) {
    const a = obj(c.a), b = obj(c.b);
    const extra = [isNum(c.median_lag_ms) ? `median ${lagText(c.median_lag_ms)}` : "", isNum(c.lift) ? `lift ${c.lift > LIFT_CAP ? ">×" + LIFT_CAP : "×" + ratioText(c.lift)}` : ""].filter(Boolean);
    const show = coBurstShow(a, b);
    return `When ${tplRef(a.signature, a.template)} burst on ${nodeBadge(str(a.node))}, ${tplRef(b.signature, b.template)} burst on ${nodeBadge(str(b.node))} ` +
      `within ${esc(spanText(DELTA_S * 1000))}, <b>${fmt(num(c.hits))} of ${fmt(num(c.of))}</b> times` +
      (extra.length ? ` (${esc(extra.join(", "))})` : "") + `.` +
      `<button type="button" class="linkbtn act" data-show='${show}' title="The merged log with each group on its own node">show me</button>`;
  }
  /** Strongest first: the share of hits, then lift, then hits. */
  const share = (h, o) => (o > 0 ? Math.min(1, h / o) : 0);
  const byStrength = (hits) => (x, y) =>
    share(hits(y), num(y.of)) - share(hits(x), num(x.of)) || num(y.lift) - num(x.lift) || hits(y) - hits(x);
  /** "18 / 18" with a small bar: how often the first side was followed by the second. */
  const hitsMeter = (h, o, title) =>
    `<span class="ix-hits" title="${esc(title)}"><span class="meter" aria-hidden="true"><i style="width:${r1(share(h, o) * 100)}%"></i></span>` +
    `<b>${fmt(h)}</b><span class="of">/ ${fmt(o)}</span></span>`;
  const LIFT_CAP = 100;
  const liftChip = (l) => (!isNum(l) ? "" : l > LIFT_CAP
    ? `<span class="lift" title="Lift: ×${esc(ratioText(l))} the hits chance would give; that large from few events, so shown capped">&gt;×${LIFT_CAP}</span>`
    : `<span class="lift" title="Lift: ×${esc(ratioText(l))} the hits chance would give">×${esc(ratioText(l))}</span>`);
  /** One side of a co-burst: node badge, then the group's template on one line (full text in the title). */
  function burstSide(s) {
    const sig = str(s.signature), node = str(s.node);
    const g = GBY.get(sig);
    const tpl = str(s.template) || (g ? g.tpl : "");
    const text = tpl ? tplHtml(tpl) : `<span class="muted">group not on this page</span>`;
    const label = g
      ? `<button type="button" class="tplref ix-tpl" data-open="${esc(g.id)}" data-tip-k="cg">${text}</button>`
      : `<span class="ix-tpl" title="${esc(tpl || sig)}">${text}</span>`;
    const others = num(s.groups) - 1;
    const more = others > 0
      ? `<span class="ix-more" title="${esc(`${plural(others, "other group")} of ${node} burst together with it; it stands for them`)}">+${fmt(others)}</span>` : "";
    return `<div class="ix-side">${nodeBadge(node)}${label}${more}</div>`;
  }
  const eventSide = (s) => `<div class="ix-side">${nodeBadge(str(s.node))}<span class="ix-kind">${glyphKey(str(s.kind))}${esc(kindLabel(s.kind))}</span></div>`;
  const flowHead = (from, to, act) =>
    `<thead><tr><th class="c-from">${from}</th><th class="c-arr"><span class="sr-only">followed by</span></th><th class="c-to">${to}</th>` +
    `<th class="c-hits">Followed</th><th class="c-lag" title="Median time from the first to the second">Lag</th><th class="c-lift">Lift</th><th class="c-act">${act}</th></tr></thead>`;
  function coBurstRow(c) {
    const a = obj(c.a), b = obj(c.b);
    const hits = num(c.hits), of = num(c.of);

    return `<tr><td class="c-from">${burstSide(a)}</td><td class="c-arr" aria-hidden="true">→</td><td class="c-to">${burstSide(b)}</td>` +
      `<td class="c-hits">${hitsMeter(hits, of, `${fmt(hits)} of ${fmt(of)} bursts on ${str(a.node)} were followed by a burst on ${str(b.node)} within ${spanText(DELTA_S * 1000)}`)}</td>` +
      `<td class="c-lag">${esc(lagText(c.median_lag_ms))}</td><td class="c-lift">${liftChip(c.lift)}</td>` +
      `<td class="c-act"><button type="button" class="linkbtn" data-show='${coBurstShow(a, b)}' title="The merged log with each group on its own node">show in log</button></td></tr>`;
  }
  function pairRow(p, rg) {
    const a = obj(p.a), b = obj(p.b);
    const n = num(p.count), of = num(p.of);
    const ex = arr(p.examples).filter((x) => Array.isArray(x) && x[0]);
    const inWin = rg ? ex.filter((x) => inRange(parseTs(x[0]), rg)) : ex;
    const acts = inWin.length
      ? inWin.slice(0, 3).map((x) => `<button type="button" class="linkbtn ix-at" data-at="${esc(x[0])}" title="${esc(rangeText(parseTs(x[0]), parseTs(x[1]), tsMs, tsMs))}: open the merged log here">${esc(tsSec(x[0]))}</button>`).join("")
      : ex.length && rg ? `<span class="muted" title="Its examples fall outside the time window">none in window</span>` : "";
    return `<tr><td class="c-from">${eventSide(a)}</td><td class="c-arr" aria-hidden="true">→</td><td class="c-to">${eventSide(b)}</td>` +
      `<td class="c-hits">${hitsMeter(n, of, `${fmt(n)} of ${fmt(of)} ${kindLabel(a.kind)} events on ${str(a.node)} were followed by ${kindLabel(b.kind)} on ${str(b.node)}`)}</td>` +
      `<td class="c-lag">${esc(lagText(p.median_lag_ms))}</td><td class="c-lift">${liftChip(p.lift)}</td><td class="c-act">${acts}</td></tr>`;
  }
  /** An event of a group (its own node's first): the text of a mention group the page has no row for. */
  const EVSIG = new Map();
  for (const e of EVENTS) {
    if (!e.sig || !e.text) continue;
    if (!EVSIG.has(e.sig)) EVSIG.set(e.sig, e);
    if (!EVSIG.has(e.sig + "\n" + e.node)) EVSIG.set(e.sig + "\n" + e.node, e);
  }
  /** One group behind a mention: its template, else a short signature with the text of one of its entries. */
  function mentionGroup(sig, from) {
    const g = GBY.get(sig);
    if (g) return `<li><button type="button" class="tplref ix-tpl" data-open="${esc(g.id)}" data-tip-k="cg">${tplHtml(g.tpl)}</button></li>`;
    const ev = EVSIG.get(sig + "\n" + from) || EVSIG.get(sig);
    const show = esc(JSON.stringify({ ms: [sig], mn: NIDX.has(from) ? [from] : [] }));
    return `<li class="ix-gone"><span class="sigchip" title="Signature ${esc(sig)}: not among the groups of this page">off page</span>` +
      (ev ? `<span class="ix-tx" title="${esc(ev.text)}"><span class="muted">${esc(kindLabel(ev.kind))}:</span> ${esc(ev.text)}</span>`
        : `<span class="ix-tx muted">group not in this page</span>`) +
      `<button type="button" class="linkbtn" data-show='${show}' title="This group's entries on ${esc(from)} in the merged log">entries</button></li>`;
  }
  /** Example slot / session times as buttons that open the merged log, those in the window when one is set. */
  function atButtons(times, rg, max) {
    const ok = times.filter((t) => typeof t === "string" && t);
    const shown = rg ? ok.filter((t) => inRange(parseTs(t), rg)) : ok;
    if (!shown.length) return ok.length && rg ? `<span class="muted" title="Its examples fall outside the time window">none in window</span>` : "";
    return shown.slice(0, max).map((t) => `<button type="button" class="linkbtn ix-at" data-at="${esc(t)}" title="${esc(tsMs(t))} ${esc(TZ)}: open the merged log here">${esc(tsSec(t))}</button>`).join("");
  }
  const ixHead = (title, n, sub, info, lead) => `<div class="ix-head"><h3>${title} <span class="n">${fmt(n)}</span></h3>` +
    (info ? `<span class="ix-info" tabindex="0" role="note" aria-label="${esc(info.text)}"${tipAttr({ h: info.h || title, body: esc(info.text), note: info.note || null })}>i</span>` : "") +
    (sub ? `<span class="ix-sub">${sub}</span>` : "") + `</div>` + (lead ? `<p class="ix-lead">${lead}</p>` : "");
  /** Events of one kind on one node in the same second: one row (a burst), in time order. */
  function foldEvents(evs) {
    const rows = [], at = new Map();
    for (const e of evs) {
      const key = e.node + "|" + e.kind + "|" + Math.floor(e.ms / 1000);
      const row = at.get(key);
      if (row) row.list.push(e);
      else {
        const r = { key, list: [e] };
        at.set(key, r);
        rows.push(r);
      }
    }
    return rows;
  }
  const EVX = new Set(); // keys of the bursts shown expanded
  /** One event row: the time is a button (keyboard), the row a click target (mouse). */
  function evRowHtml(e, cls) {
    const target = `data-at="${esc(e.ts)}" data-ae="${esc(e.entry)}"`;
    return `<tr ${target}${cls ? ` class="${cls}"` : ""}><td class="c-ts"><button type="button" class="linkbtn ev-at" ${target} title="${esc(`${tsFull(e.ts)} ${zAt(e.ms)}: open the merged log here`)}">${esc(tsMs(e.ts))}</button></td><td class="c-nd">${nodeBadge(e.node)}</td>` +
      `<td class="c-kd">${glyphKey(e.kind)} ${esc(kindLabel(e.kind))}</td><td class="c-sb">${e.subject ? nodeBadge(e.subject) : ""}</td><td class="ev-tx" title="${esc(e.text)}">${esc(e.text)}</td></tr>`;
  }
  function evRowsHtml(f) {
    if (f.list.length === 1) return evRowHtml(f.list[0], "");
    const e = f.list[0], n = f.list.length, open = EVX.has(f.key);
    const target = `data-at="${esc(e.ts)}" data-ae="${esc(e.entry)}"`;
    const subjects = [...new Set(f.list.map((x) => x.subject).filter(Boolean))];
    const texts = [...new Set(f.list.map((x) => x.text).filter(Boolean))];
    return `<tr ${target} class="ev-burst"><td class="c-ts"><button type="button" class="linkbtn ev-at" ${target} title="${esc(`${tsFull(e.ts)} ${zAt(e.ms)}: open the merged log at the first of them`)}">${esc(tsMs(e.ts))}</button></td><td class="c-nd">${nodeBadge(e.node)}</td>` +
      `<td class="c-kd">${glyphKey(e.kind)} <b>${esc(kindCount(e.kind, n))}</b></td><td class="c-sb">${subjects.map(nodeBadge).join("")}</td>` +
      `<td class="ev-tx" title="${esc(texts.slice(0, 20).join("\n"))}"><button type="button" class="linkbtn ev-x" data-evx="${esc(f.key)}" aria-expanded="${open}">${open ? "hide" : "show all"}</button>${esc(texts[0] || "")}${texts.length > 1 ? ` <span class="muted">+${fmt(texts.length - 1)} other text${texts.length === 2 ? "" : "s"}</span>` : ""}</td></tr>` +
      (open ? f.list.map((x) => evRowHtml(x, "ev-sub")).join("") : "");
  }
  // the panel depends on the window, the zone, the job runs toggle and the kind chips: rebuilt when
  // they change and the tab is shown
  let interKey = null;
  const renderInteractions = guard(function renderInteractions() {
    $("tab-i-n").textContent = fmt(MENT.length + PAIRS.length + COB.length + JOBS.length + SESS.length);
    const key = S.tab === "i" ? [WKEY, S.jobs, [...S.ek].join(","), [...EVX].join(",")].join("|") : null;
    if (key === null || key === interKey) return;
    interKey = key;
    const el = $("p-inter");
    const rg = tRange();
    const out = [];
    // co-bursts and event pairs: one row each, source → target, strongest first
    const span = esc(spanText(DELTA_S * 1000));
    const cob = [...COB].sort(byStrength((c) => num(c.hits)));
    const always = cob.filter((c) => num(c.of) > 0 && num(c.hits) >= num(c.of)).length;
    // pairs seen often first: a pair seen once (1 of 1) has a huge lift and tells little
    const pairs = [...PAIRS].sort((x, y) => num(y.count) - num(x.count) || byStrength((p) => num(p.count))(x, y));
    out.push(`<section class="card ix-flows" aria-label="Co-bursts and event pairs">` +
      ixHead("Co-bursts", COB.length, `strongest first` +
        (always ? ` · ${always === cob.length && always > 1 ? "all" : fmt(always)} followed every time` : "") + (rg ? " · whole capture, not the window" : ""),
        { text: `A group bursting on one node, then one bursting on another within ${spanText(DELTA_S * 1000)}. A burst starts at the first minute at or above the threshold after at least 10 quiet minutes.`,
          note: "Followed: bursts of the first group followed by one of the second. Lift compares that with chance. +N: other groups of the node that burst with it." },
        `Each row: when the group on the left bursts on one node, the group on the right bursts on another node within ${span}. "Followed" counts how many times.`) +
      (cob.length ? `<div class="ix-scroll"><table class="ix-t ix-flow">${flowHead("When this group bursts", `this one bursts within ${span}`, "")}<tbody>${cob.map(coBurstRow).join("")}</tbody></table></div>`
        : `<div class="muted">No group bursts on one node were followed by bursts on another more often than chance.</div>`) +
      ixHead("Event pairs", PAIRS.length, "most often first" + (rg ? " · counts: whole capture" : ""),
        { text: "A cluster event on one node, then one on another node within 60 s.", note: "Followed: events of the first kind followed by the second. Lift compares that with chance." },
        "Each row: when the event on the left happens on one node, the event on the right happens on another node within 60 s.") +
      (pairs.length ? `<div class="ix-scroll"><table class="ix-t ix-flow">${flowHead("When this event", "this follows within 60 s", "Examples")}<tbody>${pairs.map((p) => pairRow(p, rg)).join("")}</tbody></table></div>`
        : `<div class="muted">No events on one node were followed by events on another.</div>`) +
      `</section>`);
    // mention matrix and its edges
    const M = new Map();
    for (const m of MENT) M.set(str(m.from) + "\n" + str(m.to), m);
    let mmax = 0;
    for (const m of MENT) mmax = Math.max(mmax, num(m.count));
    let mx = `<div class="muted">No node logged about another node.</div>`;
    let gone = 0;
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
      mx += `<ul class="edges">` + [...MENT].sort((x, y) => num(y.count) - num(x.count)).map((m) => {
        const from = str(m.from), sigs = arr(m.groups).map(String);
        gone += sigs.slice(0, 6).filter((s) => !GBY.has(s)).length;
        return `<li><div class="e-head">${nodeBadge(from)}<span class="e-arr" aria-label="names">→</span>${nodeBadge(str(m.to))}<span class="muted">${plural(num(m.count), "mention")} · ${plural(sigs.length, "group")}</span>` +
          `<span class="grow"></span><button type="button" class="linkbtn" data-mx="${esc(from + "\n" + str(m.to))}">show entries</button></div>` +
          `<ul class="e-groups">${sigs.slice(0, 6).map((s) => mentionGroup(s, from)).join("")}${sigs.length > 6 ? `<li class="muted">+${fmt(sigs.length - 6)} more</li>` : ""}</ul></li>`;
      }).join("") + `</ul>`;
    } else if (NODES.length < 2) {
      mx = `<div class="muted">A single node: no mentions between nodes.</div>`;
    }
    const truncated = num(OPTS.truncated_groups);
    const goneNote = gone
      ? `Groups shown by a short signature are not among the ${fmt(ROWS.length)} groups of this page${truncated > 0 ? ` (the largest of ${fmt(ROWS.length + truncated)})` : ""}: the text is one of their entries, "entries" lists them in the merged log.`
      : null;
    const cards = [`<section class="card" aria-label="Mentions">${ixHead("Mentions", MENT.length, "node names node · whole capture",
      { text: "An entry on one node naming another member node. Counts cover the whole capture window.", note: goneNote })}${mx}</section>`];
    // concurrent jobs and sessions: compact tables
    let smax = 1;
    for (const j of JOBS) smax = Math.max(smax, num(j.slots));
    const jobs = JOBS.length
      ? `<div class="ix-scroll"><table class="ix-t ix-jobs"><thead><tr><th class="c-job">Job</th><th class="c-nodes">Nodes</th><th class="c-slots" title="Slots of 60 s in which it ran on 2 or more nodes">Same minute</th><th class="c-ex">Examples</th></tr></thead><tbody>` +
        JOBS.map((j) => {
          const s = num(j.slots);
          return `<tr><td class="c-job"><span class="ix-job" title="${esc(j.job)}">${esc(j.job)}</span></td><td class="c-nodes">${arr(j.nodes).map((n) => nodeBadge(str(n))).join("")}</td>` +
            `<td class="c-slots"><span class="ix-hits" title="${esc(`${fmt(s)} slots of 60 s with a run on ${plural(arr(j.nodes).length, "node")}`)}"><span class="meter" aria-hidden="true"><i style="width:${r1(share(s, smax) * 100)}%"></i></span><b>${fmt(s)}</b></span></td>` +
            `<td class="c-ex">${atButtons(arr(j.examples), rg, 2)}</td></tr>`;
        }).join("") + `</tbody></table></div>`
      : `<div class="muted">No scheduled job ran on several nodes at the same time.</div>`;
    const sess = SESS.length
      ? `<div class="ix-scroll"><table class="ix-t ix-jobs"><thead><tr><th class="c-nodes">Nodes</th><th class="c-slots">Sessions</th><th class="c-ex">Examples</th></tr></thead><tbody>` +
        SESS.map((s) => `<tr><td class="c-nodes">${arr(s.nodes).map((n) => nodeBadge(str(n))).join("")}</td><td class="c-slots"><b>${fmt(num(s.count))}</b></td>` +
          `<td class="c-ex">${atButtons(arr(s.examples).map((x) => (Array.isArray(x) ? x[0] : obj(x).ts || x)), rg, 2)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="muted">No session was seen on more than one node.</div>`;
    cards.push(`<section class="card" aria-label="Concurrent jobs and sessions">${ixHead("Concurrent jobs", JOBS.length, "the same job on several nodes in the same minute")}${jobs}` +
      `${ixHead("Sessions across nodes", SESS.length, "seen on two nodes within an hour: a lost sticky session or a fail-over")}${sess}</section>`);
    out.push(`<div class="ix-grid">${cards.join("")}</div>`);
    // events table (the table view of the event lanes): kind chips (job runs as the lanes show
    // them), same-instant bursts of one kind on one node folded into one row
    const inWin = rg ? EVENTS.filter((e) => inRange(e.ms, rg)) : EVENTS;
    const kc = new Map();
    for (const e of inWin) kc.set(e.kind, (kc.get(e.kind) || 0) + 1);
    const shownKind = (k) => (k === "job_run" ? S.jobs : !S.ek.has(k));
    const evs = inWin.filter((e) => shownKind(e.kind));
    const kinds = KINDS.filter((k) => kc.has(k)).map((k) =>
      `<button type="button" class="chip ev-chip" data-ek="${esc(k)}" aria-pressed="${shownKind(k)}" title="${esc(k === "job_run"
        ? "Job runs in this table and in the event lanes (the Job runs button)" : `Show the ${KIND_PLURAL[k] || kindLabel(k) + "s"} in this table`)}">` +
      `${glyphKey(k)}${esc(kindLabel(k))}<span class="n">${fmt(kc.get(k))}</span></button>`).join("");
    const folded = foldEvents(evs);
    const LIMIT = 300;
    const hidden = inWin.length - evs.length;
    out.push(`<section class="card wide" aria-label="Events">${ixHead("Events", evs.length, (rg ? "in the window" : "") +
        (hidden ? `${rg ? " · " : ""}${fmt(hidden)} hidden by the kind chips` : ""))}` +
      (kinds ? `<div class="chips ev-kinds" role="group" aria-label="Event kinds">${kinds}</div>` : "") +
      (folded.length ? `<table class="ev-table"><thead><tr><th class="c-ts">Time (${esc(TZ)})</th><th class="c-nd">Node</th><th class="c-kd">Kind</th><th class="c-sb">Names</th><th>Text</th></tr></thead><tbody>` +
        folded.slice(0, LIMIT).map(evRowsHtml).join("") +
        `</tbody></table>` + (folded.length > LIMIT ? `<p class="note">First ${fmt(LIMIT)} of ${fmt(folded.length)} rows; narrow the window to see the rest.</p>` : "")
        : `<div class="muted">No cluster events${rg ? " in the window" : ""}${hidden ? " of the shown kinds" : ""}.</div>`) + `</section>`);
    el.innerHTML = out.join("");
  });

  // ---------------------------------------------------------------- merged log
  // Web app: pages of the capture's entries (urls.entries) with every filter sent to the server
  // (range, min level, groups, nodes, search), each page appended as it arrives; scrolling to the
  // end of the list or j on its last row loads the next one. Static page: the payload's example
  // entries, filtered here and shown CHUNK rows at a time.
  const CHUNK = 200;
  const AT_PAGES = 25; // pages fetched at most to reach the entry of an event
  const LOG = {
    req: 0, key: "", ver: 0, rows: [], next: null, done: false, total: null, loading: false, error: "", shown: CHUNK,
    cur: -1, curId: "", open: new Set(), atId: "", atDone: "", atPages: 0, atPending: false, moveAfter: false, drawn: null,
    ctxRows: null, ctxKey: "", ctxLoading: false,
  };
  /** The log's time range [since, until) in ms: from 2 s before the moment of an event (inside the
      window), else the window, else the capture's window. */
  function logRange() {
    const tr = tRange();
    const at = S.at ? parseTs(S.at) : NaN;
    if (isNum(at) && inRange(at, tr)) {
      return [tr ? Math.max(at - 2000, tr[0]) : at - 2000, tr ? tr[1] : WIN ? Math.max(WIN[1] + 1, at + 60000) : at + 3.6e6];
    }
    return tr || (WIN ? [WIN[0], WIN[1] + 1] : null);
  }
  const logSearch = () => S.mq.trim();
  /** What the loaded pages depend on: a change starts over from the first page. */
  function webKey() {
    const rg = logRange();
    return [rg ? rg[0] : "", rg ? rg[1] : "", S.ml, S.ms.join(","), S.mn ? [...S.mn].join(",") : "", logSearch()].join("|");
  }
  function fetchJson(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`GET ${url}: HTTP ${res.status}`))));
  }
  /** The next page of the web log (the first one after a reset); its rows are appended. */
  function fetchLogPage() {
    if (!WEB_LOG || LOG.loading || LOG.done || S.ctx) return;
    // a request number, not the key: A -> B -> A must not let the first A response land
    const req = ++LOG.req;
    const key = LOG.key;
    const u = new URL(URLS.entries, location.href);
    const rg = logRange();
    if (rg) {
      u.searchParams.set("since", isoOf(rg[0]));
      u.searchParams.set("until", isoOf(rg[1]));
    }
    if (S.ml) u.searchParams.set("min_level", S.ml);
    if (S.ms.length) u.searchParams.set("signature", S.ms.join(","));
    if (S.mn) u.searchParams.set("node", [...S.mn].join(","));
    if (logSearch()) u.searchParams.set("contains", logSearch());
    u.searchParams.set("limit", String(CHUNK));
    u.searchParams.set("offset", String(LOG.next || 0));
    LOG.loading = true;
    LOG.error = "";
    renderLog();
    fetchJson(u.toString()).then((doc) => {
      if (req !== LOG.req || key !== LOG.key) return;
      doc = obj(doc);
      const items = arr(doc.items).map((e) => normEntry(obj(e).id, e)).filter((r) => isNum(r.ms));
      const off = LOG.next || 0;
      LOG.rows = LOG.rows.concat(items);
      LOG.total = isNum(doc.total) ? doc.total : LOG.total;
      LOG.next = doc.next_offset != null ? doc.next_offset : doc.truncated ? off + arr(doc.items).length : null;
      LOG.done = LOG.next == null;
    }).catch((err) => {
      if (req === LOG.req && key === LOG.key) LOG.error = String(err && err.message ? err.message : err);
    }).finally(() => {
      if (req !== LOG.req || key !== LOG.key) return;
      LOG.loading = false;
      renderLog();
      if (LOG.moveAfter) {
        LOG.moveAfter = false;
        if (!LOG.error) moveLog(1);
      }
    });
  }
  /** Scrolled near the end of the list: the next page (web) or the next rows (static). */
  function autoLoad() {
    if (S.tab !== "l" || S.ctx) return;
    if (WEB_LOG) {
      if (!LOG.loading && !LOG.done && !LOG.error && LOG.rows.length) fetchLogPage();
    } else if (VIEW.length > LOG.shown) showMoreLog();
  }
  function fetchContext() {
    const [set, id] = S.ctx.split("~");
    const key = S.ctx;
    LOG.ctxKey = key;
    LOG.ctxRows = null;
    LOG.ctxLoading = true;
    LOG.error = "";
    renderLog();
    fetchJson(new URL(urlFill(URLS.context, { set, id }), location.href).toString()).then((doc) => {
      if (LOG.ctxKey !== key) return;
      LOG.ctxRows = arr(obj(doc).items).map((e) => normEntry(obj(e).id, e)).filter((r) => isNum(r.ms)).sort(byTs);
    }).catch((err) => {
      if (LOG.ctxKey === key) LOG.error = String(err && err.message ? err.message : err);
    }).finally(() => {
      if (LOG.ctxKey !== key) return;
      LOG.ctxLoading = false;
      renderLog();
    });
  }
  /** Rows of the merged log: the server's (web, already filtered), the static entries after the
      filters, or the ±30 s context. */
  function logRows() {
    if (S.ctx) {
      const at = parseTs(S.cts);
      if (URLS && URLS.context && WEB_LOG) return LOG.ctxRows || [];
      const pool = WEB_LOG ? LOG.rows.concat(STATIC_LOG) : STATIC_LOG;
      const seen = new Set();
      return pool.filter((r) => Math.abs(r.ms - at) <= 30000 && !seen.has(r.id) && seen.add(r.id)).sort(byTs);
    }
    if (WEB_LOG) return LOG.rows;
    const rg = tRange();
    const minLv = levelNum(S.ml);
    const q = logSearch().toLowerCase();
    const sigs = S.ms.length ? new Set(S.ms) : null;
    return STATIC_LOG.filter((r) => (!rg || inRange(r.ms, rg)) && (!minLv || r.lvl >= minLv) && (!S.mn || S.mn.has(r.node)) &&
      (!q || r.key.includes(q)) && (!sigs || sigs.has(r.sig) || sigs.has(r.node + ":" + r.sig)));
  }
  /** The row reachable with Tab (a roving tabindex): the cursor's, else the first. */
  const tabRow = () => Math.max(0, LOG.cur);
  function mlRowHtml(r, i) {
    const open = LOG.open.has(r.id);
    const cls = (open ? " is-open" : "") + (i === LOG.cur ? " is-cur" : "") +
      (S.ctx && S.ctx.split("~")[1] === r.id ? " is-anchor" : "") + (LOG.atId === r.id ? " is-at" : "");
    return `<div class="ml-row lvb-${r.bucket} ${ncls(r.node)}${cls}" data-i="${i}" role="listitem">` +
      `<div class="ml-line" role="button" tabindex="${i === tabRow() ? 0 : -1}" aria-expanded="${open}"><span class="ml-ts">${mlTime(r.ts)}</span>${nodeBadge(r.node)}${badge(r.level_name, r.lvl)}` +
      `<span class="ml-lg" title="${esc(r.logger)}">${loggerHtml(r.logger, true)}</span><span class="ml-msg" title="${esc(r.message.length > 400 ? r.message.slice(0, 400) + "…" : r.message)}">${esc(r.message)}</span></div>` +
      (open ? `<div class="ml-detail">${entryHtml(r.id, { ...r.e, node: r.node, set: r.set, ts: r.ts, signature: r.sig })}</div>` : "") + `</div>`;
  }
  /** "10-29 11:47:26.303" with the date quiet: rows of one moment read by their time. */
  function mlTime(ts) {
    return tsHtml(tsMs(ts));
  }
  /** Rows on screen: every loaded row (web), else the first LOG.shown of the view. */
  const shownCount = () => (WEB_LOG && !S.ctx ? VIEW.length : Math.min(VIEW.length, LOG.shown));
  /** What the drawn rows depend on besides their number: same key, the list is kept (or extended). */
  function drawnBase() {
    const src = S.ctx ? `c${S.ctx}|${LOG.ctxRows ? LOG.ctxRows.length : -1}|${LOG.rows.length}`
      : WEB_LOG ? "w" + LOG.ver
      : ["s", S.ml, S.mn ? [...S.mn].join(",") : "", logSearch(), S.ms.join(","), S.t ? S.t.join("-") : ""].join("|");
    return [src, TZ, LOG.atId, VIEW.length ? "" : [LOG.loading, LOG.ctxLoading, LOG.error].join(",")].join("|");
  }
  let VIEW = [];
  const renderLog = guard(function renderLog() {
    // the number of entries known: the static examples, the context, or the server's total (none before it answers)
    const known = !WEB_LOG ? STATIC_LOG.length : S.ctx && URLS.context ? (LOG.ctxRows ? LOG.ctxRows.length : null) : LOG.total;
    $("tab-l-n").textContent = known != null ? fmt(known) : "";
    if (S.tab !== "l") return;
    // web mode: start over when the range or a filter changes
    if (WEB_LOG && !S.ctx) {
      const key = webKey();
      if (key !== LOG.key) {
        Object.assign(LOG, { key, ver: LOG.ver + 1, rows: [], next: null, done: false, total: null, loading: false, error: "",
          cur: -1, curId: "", atPages: 0, moveAfter: false });
        fetchLogPage();
        return;
      }
    }
    if (S.ctx && URLS && URLS.context && WEB_LOG && LOG.ctxKey !== S.ctx) {
      fetchContext();
      return;
    }
    VIEW = logRows();
    // the cursor follows its entry when the filters rebuild the view
    LOG.cur = LOG.curId ? VIEW.findIndex((r) => r.id === LOG.curId) : -1;
    // the moment of an event: its own entry when known, else the first entry at or after it; while
    // the loaded rows end before it, the next pages are fetched (at most AT_PAGES)
    LOG.atId = "";
    if (S.at && !S.ctx) {
      const at = parseTs(S.at);
      const last = VIEW.length ? VIEW[VIEW.length - 1].ms : -Infinity;
      const more = WEB_LOG && !LOG.done && !LOG.error && LOG.atPages < AT_PAGES;
      let i = S.ae ? VIEW.findIndex((r) => r.id === S.ae) : -1;
      if (i < 0) {
        if (more && (S.ae ? last <= at : last < at)) {
          if (!LOG.loading && !LOG.atPending) {
            LOG.atPending = true;
            LOG.atPages++;
            setTimeout(() => { LOG.atPending = false; fetchLogPage(); }, 0);
          }
        } else i = VIEW.findIndex((r) => r.ms >= at);
      }
      if (i >= 0) {
        LOG.atId = VIEW[i].id;
        if (!WEB_LOG && LOG.shown < i + 20) LOG.shown = i + CHUNK;
      }
    }
    // the rows: kept when nothing they show changed, extended when only more arrived (item 9)
    const n = shownCount();
    const base = drawnBase();
    const ml = $("ml");
    const d = LOG.drawn;
    if (d && d.base === base && d.n === n && ml.children.length === Math.max(1, n)) {
      /* unchanged */
    } else if (d && d.base === base && d.n > 0 && n > d.n && ml.children.length === d.n) {
      ml.insertAdjacentHTML("beforeend", VIEW.slice(d.n, n).map((r, j) => mlRowHtml(r, d.n + j)).join(""));
    } else {
      ml.innerHTML = VIEW.slice(0, n).map(mlRowHtml).join("") ||
        ((S.ctx ? LOG.ctxLoading : LOG.loading) ? `<div class="empty loading" role="status">Loading the log…</div>`
          : `<div class="empty">${S.ctx ? "No entries within ±30 s in this report." : "No entries match."}</div>`);
    }
    LOG.drawn = { base, n };
    // notes, chips, footer
    const notes = [];
    if (!WEB_LOG) {
      notes.push(`Static report: these are the ${fmt(STATIC_LOG.length)} example entries of all nodes, interleaved by time. The full log: <code>${esc(`ragnalog entries ${CAPREF}` + (logRange() ? ` --since ${isoOf(logRange()[0])} --until ${isoOf(logRange()[1])}` : ""))}</code>`);
    }
    if (S.ctx) {
      notes.push(`±30 s around ${esc(tsFull(S.cts))} ${esc(zAt(parseTs(S.cts)))} on all nodes${WEB_LOG && URLS.context ? "" : " (from the entries in this report)"}. <button type="button" class="linkbtn" data-ctx-close>Back to the log</button>`);
    }
    if (LOG.error) notes.push(`Could not load entries: ${esc(LOG.error)}`);
    const filtered = S.ml || S.ms.length || S.mn || logSearch();
    $("l-summary").innerHTML = (notes.length ? notes.map((x) => `<div class="ml-note">${x}</div>`).join("") : "") +
      `<span>` + (WEB_LOG && !S.ctx
        ? (LOG.total != null ? `${fmt(LOG.rows.length)} of ${plural(LOG.total, "entry", "entries")} loaded` +
          (filtered ? " (matching the filters)" : "") + (S.at ? " from the moment on" : " in the range") : "Loading…")
        : plural(VIEW.length, "entry", "entries")) + `</span>`;
    const chips = [];
    if (S.ms.length) chips.push(["ms", `${plural(S.ms.length, "group")}: ${S.ms.map(msLabel).join(" · ")}`]);
    if (S.mn) chips.push(["mn", `Nodes: ${[...S.mn].map(NLABEL).join(", ")}`]);
    if (S.at) chips.push(["at", `From ${tsSec(S.at)}`]);
    if (S.t) chips.push(["t", `Window ${tsShort(isoOf(tRange()[0]))} (${spanText(tRange()[1] - tRange()[0])})`]);
    $("l-active").innerHTML = chips.map(([k, label]) => `<button type="button" class="fchip" data-lclear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
    $("ml-foot").innerHTML = logFootHtml();
    if (LOG.atId && LOG.atDone !== S.at + "|" + S.ae) {
      LOG.atDone = S.at + "|" + S.ae;
      const row = ml.querySelector(".is-at");
      if (row) requestAnimationFrame(() => row.scrollIntoView({ block: "center" }));
    }
  });
  function logFootHtml() {
    if (S.ctx) return "";
    if (WEB_LOG) {
      if (LOG.loading) return LOG.rows.length ? `<span>Loading ${fmt(CHUNK)} more…</span>` : "";
      if (LOG.error) return `<button type="button" class="btn small" data-load>Retry</button>`;
      if (!LOG.rows.length) return "";
      return LOG.done ? `<span>All ${fmt(LOG.rows.length)} loaded</span>`
        : `<button type="button" class="btn small" data-load title="Also loaded when you scroll to the end of the list, or press j on its last row">Load ${fmt(CHUNK)} more</button>` +
          `<span>${fmt(LOG.rows.length)} of ${fmt(LOG.total)} loaded</span>`;
    }
    return VIEW.length > LOG.shown
      ? `<button type="button" class="btn small" data-more>Show ${fmt(Math.min(CHUNK, VIEW.length - LOG.shown))} more</button><span>${fmt(LOG.shown)} of ${fmt(VIEW.length)} shown</span>`
      : "";
  }
  /** The rendered row of VIEW[i], if shown. */
  function mlRow(i) {
    const el = $("ml").children[i];
    return el && el.dataset.i === String(i) ? el : null;
  }
  /** "Show more" (static): the next rows, appended by renderLog. */
  function showMoreLog() {
    LOG.shown += CHUNK;
    renderLog();
  }
  /** Move the cursor class and the roving tabindex to row; focus follows when it was in the list. */
  function setCurRow(row) {
    const ml = $("ml");
    const inList = ml.contains(document.activeElement);
    for (const el of ml.querySelectorAll(".ml-row.is-cur")) if (el !== row) el.classList.remove("is-cur");
    for (const el of ml.querySelectorAll('.ml-line[tabindex="0"]')) if (el.parentElement !== row) el.tabIndex = -1;
    if (!row) return;
    row.classList.add("is-cur");
    const line = row.querySelector(".ml-line");
    if (line) {
      line.tabIndex = 0;
      if (inList) line.focus({ preventScroll: true });
    }
  }
  function renderLogControls() {
    $("l-level").innerHTML = `<span class="lbl">Min level</span>` + LOG_LEVELS.map((l) =>
      `<button type="button" data-ml="${l}" aria-pressed="${S.ml === l}">${l || "All"}</button>`).join("");
    $("l-nodes").hidden = !MULTI;
    $("l-nodes").innerHTML = NODES.map((n) =>
      `<button type="button" class="chip ${ncls(n)}" data-mn="${esc(n)}" aria-pressed="${!S.mn || S.mn.has(n)}" title="${esc(n)}"><i class="sw sw-node"></i>${esc(NLABEL(n))}</button>`).join("");
    const lq = $("l-search");
    if (WEB_LOG) lq.placeholder = "Search entry text: message, logger, thread, trace";
    if (document.activeElement !== lq && lq.value !== S.mq) lq.value = S.mq;
  }
  /** Open the merged log at an event (entry: its entry id, matched before its time). */
  function openLogAt(ts, entry) {
    if (!ts) return;
    // a time outside the window would give an empty (or inverted) log range: drop the window
    const at = parseTs(str(ts)), tr = tRange();
    const out = tr && isNum(at) && (at < tr[0] || at >= tr[1]);
    LOG.atDone = ""; // the same event again scrolls to it again
    commit({ tab: "l", at: str(ts), ae: str(entry || ""), ctx: "", cts: "", ms: [], mn: null, mq: "", ...(out ? { t: null } : {}) }, { keepPage: true });
    scrollToTabs();
  }
  function scrollToTabs() {
    const bar = $("filters");
    if (bar.getBoundingClientRect().top > window.innerHeight * 0.6 || bar.getBoundingClientRect().top < 0) {
      bar.scrollIntoView({ block: "start" });
    }
  }

  // ---------------------------------------------------------------- update
  /** What the views show now: the window, the node filter, the open group; "whole capture" when nothing narrows them. */
  function renderScope() {
    const chip = (attr, k, v, title) =>
      `<button type="button" class="sc" ${attr} title="${esc(title)}"><span class="sc-k">${k}</span><span class="sc-v">${v}</span><span class="sc-x" aria-hidden="true">×</span></button>`;
    const out = [];
    const rg = tRange();
    if (rg) out.push(chip('data-clear="t"', "Window", `${esc(rangeText(rg[0], rg[1]))} <span class="sc-m">${esc(spanText(rg[1] - rg[0]))}</span>`, "Show the whole capture (clear the window)"));
    if (S.tab === "g" && S.nd) out.push(chip('data-clear="nd"', "Node", `${nodeBadge(S.nd)}`, `Groups seen on ${S.nd} only: show every node`));
    const sel = S.tab === "g" && S.sig ? GBY.get(S.sig) : null;
    if (sel) out.push(chip("data-unsel", "Group", `<span class="sc-tpl">${tplHtml(trunc(sel.tpl, 60))}</span>`, "Close the open group"));
    const rst = $("f-reset");
    if (rst) rst.disabled = !(activeFilters().length || S.sort !== "count" || S.sig || S.ml || S.mn || S.mq || S.ms.length);
    $("c-scope").innerHTML = out.length ? out.join("") : `<span class="sc-all">Whole capture${MULTI ? ", all nodes" : ""}</span>`;
  }
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
    renderScope();
    const af = activeFilters();
    $("f-print").textContent = af.length ? "Filters: " + af.map((x) => x[1]).join(" · ") : "";
  }
  let lastTl = "";
  // re-renders replace the chips, pagers and window buttons: put the focus back on the
  // control that had it (same id, or same tag and data-* attributes)
  // (data-page changes with every page and data-tip-* is set by tips.js on hover/focus: not stable)
  const UNSTABLE = /^data-(page|tip-text|tip-html)$/;
  function focusKey(el) {
    if (!(el instanceof HTMLElement) || el === document.body) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    const attrs = [...el.attributes].filter((a) => a.name.startsWith("data-") && !UNSTABLE.test(a.name))
      .map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("");
    if (!attrs) return "";
    const host = el.parentElement && el.parentElement.closest("[id]");
    return (host ? "#" + CSS.escape(host.id) + " " : "") + el.tagName.toLowerCase() + attrs;
  }
  function restoreFocus(key) {
    const a = document.activeElement;
    if (!key || (a && a !== document.body && a.isConnected)) return;
    const el = document.querySelector(key);
    if (el && el.offsetParent !== null) el.focus({ preventScroll: true });
  }
  function update(forceTl) {
    const fk = focusKey(document.activeElement);
    compute();
    renderTabs();
    renderTimelineControls();
    renderZoomCtl();
    const tlKey = [S.y, S.fit, S.sig, S.arrows, S.jobs, S.t ? S.t.join("-") : "", S.z ? S.z.join("-") : "", S.bin].join("|");
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
    restoreFocus(fk);
  }
  let T2 = 0; // first paint (ms after navigation start), once known
  function renderFoot() {
    if (!T2) return;
    const foot = $("foot");
    foot.textContent = `Generated ${ts16(D.generated_at)} ${TZ} by ragnalog ${D.tool_version || ""} · ${D.schema} · ` +
      `${plural(NODES.length, "node")}, ${plural(ROWS.length, "group")}, ${plural(STATIC_LOG.length, "example entry", "example entries")}, ${plural(EVENTS.length, "event")} · ` +
      `rendered in ${Math.round(T1 - T0)} ms (script), first paint ${Math.round(T2)} ms after navigation start`;
    foot.dataset.script = String(Math.round(T1 - T0));
    foot.dataset.paint = String(Math.round(T2));
  }
  let stickyRO = null;
  function updateSticky() {
    const f = $("filters");
    const h = getComputedStyle(f).position === "sticky" ? f.offsetHeight : 0;
    document.documentElement.style.setProperty("--sticky-top", h + "px");
    // the bar's height changes when it sticks (labels hide, rows reflow): the table header follows
    if (!stickyRO && window.ResizeObserver) {
      stickyRO = new ResizeObserver(() => {
        const g = $("filters");
        document.documentElement.style.setProperty("--sticky-top", (getComputedStyle(g).position === "sticky" ? g.offsetHeight : 0) + "px");
      });
      stickyRO.observe(f);
    }
    markStuck();
  }
  /** The bar shows the capture's name only while it is stuck to the top (its natural place scrolled away). */
  function markStuck() {
    const f = $("filters"), s = $("bar-sentinel");
    if (f && s) f.classList.toggle("is-stuck", s.getBoundingClientRect().bottom < 0);
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
    b.title = isDark() ? "Switch to the light theme" : "Switch to the dark theme";
  }
  function toggleTheme() {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("ragnalog-report-theme", next); } catch { /* storage unavailable */ }
    renderThemeBtn();
    if (S.theme) commit({ theme: next }, { keepPage: true, replace: true });
  }
  function renderTzBtn() {
    const b = $("btn-tz");
    if (!b) return;
    (b.querySelector(".tz-t") || b).textContent = TZ;
    const mh = document.querySelector(".ml-head > span");
    if (mh) mh.textContent = `Time (${TZ})`;
    b.setAttribute("aria-pressed", String(TZL));
    b.title = TZL ? `Times in your time zone (${TZ}); click to show UTC` : "Times in UTC; click to show your time zone";
  }
  /** Re-render everything that prints a time in the newly shown zone; deferred: the caller's update() draws. */
  function applyZone(local, deferred) {
    setZone(local);
    renderTzBtn();
    guard(renderHeader)();
    cardsKey = null;
    interKey = null;
    drawerStale = true;
    lastTl = "";
    renderFoot();
    if (!deferred) update(true);
  }
  function toggleTz() {
    const next = !TZL;
    try { localStorage.setItem(TZ_KEY, next ? "local" : "utc"); } catch { /* storage unavailable */ }
    // the hash keeps tz only when the link named one (like theme)
    if (S.tz) {
      S.tz = next ? "local" : "utc";
      try { history.replaceState(null, "", "#" + hashOf(S)); } catch { location.hash = hashOf(S); }
    }
    applyZone(next);
  }
  // the control that opened the drawer (a focusKey; "" for a table row): focus returns there on close
  let opener = "";
  function open(id, fromRow) {
    CUR = id;
    const was = !!S.sig && S.tab === "g";
    if (!was) opener = fromRow ? "" : focusKey(document.activeElement);
    commit({ sig: id, tab: "g" }, { keepPage: true });
    if (!was) $("drawer").focus({ preventScroll: true });
  }
  /** Close the drawer (Esc, Close) and put the focus back on its opener, else the group's row, else the table. */
  function closeDrawer() {
    const back = opener;
    opener = "";
    commit({ sig: "" }, { keepPage: true });
    let el = back ? document.querySelector(back) : null;
    if (!el || el.offsetParent === null) el = [...$("t-body").rows].find((tr) => tr.dataset.id === CUR) || null;
    if (!el || el.offsetParent === null) {
      el = $("groups");
      if (el.tabIndex < 0 && !el.hasAttribute("tabindex")) el.tabIndex = -1;
    }
    el.focus();
  }
  function openFromElsewhere(id) {
    const i = LIST.findIndex((r) => r.id === id);
    CUR = id;
    if (!(S.sig && S.tab === "g")) opener = focusKey(document.activeElement);
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
    // past the last loaded row: the next page, then the cursor moves on to its first row
    if (d > 0 && LOG.cur >= VIEW.length - 1) {
      if (WEB_LOG && !S.ctx && !LOG.done && !LOG.error) {
        LOG.moveAfter = true;
        fetchLogPage();
      }
      return;
    }
    LOG.cur = LOG.cur < 0 ? (d > 0 ? 0 : shownCount() - 1) : Math.max(0, Math.min(VIEW.length - 1, LOG.cur + d));
    LOG.curId = VIEW[LOG.cur].id;
    // move the cursor between two rows; the static list grows first when the cursor leaves it
    if (!WEB_LOG && LOG.cur >= LOG.shown) {
      LOG.shown = LOG.cur + CHUNK;
      renderLog();
    }
    const row = mlRow(LOG.cur);
    setCurRow(row);
    if (row) row.scrollIntoView({ block: "nearest" });
  }
  function toggleRow(i) {
    const r = VIEW[i];
    if (!r) return;
    if (LOG.open.has(r.id)) LOG.open.delete(r.id); else LOG.open.add(r.id);
    LOG.cur = i;
    LOG.curId = r.id;
    // replace the toggled row only (focus stays on it)
    const row = mlRow(i);
    if (!row) return renderLog();
    const focused = row.contains(document.activeElement);
    row.outerHTML = mlRowHtml(r, i);
    const now = mlRow(i);
    setCurRow(now);
    if (focused && now) now.querySelector(".ml-line").focus({ preventScroll: true });
    return undefined;
  }
  function resetAll() {
    clearTimeout(qTimer);
    clearTimeout(mqTimer);
    $("f-search").value = "";
    $("l-search").value = "";
    const sb = $(S.tab === "l" ? "l-search" : "f-search");
    if (sb) sb.focus({ preventScroll: true });
    commit({ q: "", lv: new Set(DEFAULT_LV), sp: "any", nd: "", sort: "count", t: null, sig: "", ex: "", ml: "", mn: null, mq: "", ms: [], ctx: "", cts: "", at: "", ae: "", ek: new Set() });
  }
  const CLEAR = { q: { q: "" }, sp: { sp: "any" }, nd: { nd: "" }, t: { t: null } };
  function clearFilter(k) {
    if (k === "all") return resetAll();
    if (k === "lv") return commit({ lv: new Set(DEFAULT_LV) });
    if (k === "q") $("f-search").value = "";
    if (CLEAR[k]) commit(CLEAR[k]);
  }
  /** Clicks shared by every panel: open a group, jump to the log, show a context, copy, unfold,
      show every line of an entry. */
  function onCommonClick(e) {
    const t = e.target.closest("[data-open],[data-at],[data-ctx],[data-show],[data-mx],[data-copy],[data-copy-raw],[data-unfold],[data-all-lines],[data-raw-view],[data-ex],[data-node-filter],[data-retry-ex]");
    if (!t) return false;
    const d = t.dataset;
    if (d.retryEx != null) {
      EXN_ERR.clear();
      redrawDrawer();
    } else if (d.rawView != null) toggleRaw(t);
    else if (d.allLines != null) showAllLines(t);
    else if (d.unfold != null) {
      const f = t.closest(".fold");
      const opened = f.classList.toggle("open");
      const n = +d.unfold;
      t.textContent = opened ? "hide frames" : `show ${fmt(n)} more frame${n === 1 ? "" : "s"}`;
    } else if (d.copy != null) copy(d.copy);
    else if (d.copyRaw != null) copy(rawOf(d.copyRaw));
    else if (d.ctx != null) {
      commit({ tab: "l", ctx: d.ctx, cts: d.cts || "", at: "", ae: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.at != null) openLogAt(d.at, d.ae);
    else if (d.show != null) {
      let spec = {};
      try { spec = JSON.parse(d.show); } catch { spec = {}; }
      const mn = arr(spec.mn).filter((n) => NIDX.has(n));
      commit({ tab: "l", ms: arr(spec.ms).map(String), mn: mn.length && mn.length < NODES.length ? new Set(mn) : null, at: "", ae: "", ctx: "", cts: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.mx != null) {
      const [from, to] = d.mx.split("\n");
      const m = MENT.find((x) => str(x.from) === from && str(x.to) === to);
      commit({ tab: "l", ms: m ? arr(m.groups).map(String) : [], mn: NIDX.has(from) && MULTI ? new Set([from]) : null, at: "", ae: "", ctx: "", cts: "" }, { keepPage: true });
      scrollToTabs();
    } else if (d.ex != null) commit({ ex: d.ex }, { keepPage: true, replace: true });
    else if (d.nodeFilter != null) commit({ nd: S.nd === d.nodeFilter ? "" : d.nodeFilter, tab: "g" });
    else if (d.open != null) {
      if (t.closest("#drawer")) open(d.open);
      else openFromElsewhere(d.open);
    }
    return true;
  }

  // ---- full screen width (the header's toggle, key w): the setting of every page of the app,
  // applied to <html data-wide> by the page's head script before the first paint
  const WIDE_KEY = "ragnalog-wide";
  function renderWideBtn() {
    const b = $("btn-wide");
    if (!b) return;
    const on = document.documentElement.hasAttribute("data-wide");
    const t = on ? "Back to the centred page width (w)" : "Use the full screen width (w)";
    b.setAttribute("aria-pressed", String(on));
    if (b.hasAttribute("data-tip-text")) b.setAttribute("data-tip-text", t); else b.title = t;
  }
  function toggleWide() {
    const on = !document.documentElement.hasAttribute("data-wide");
    document.documentElement.toggleAttribute("data-wide", on);
    try { localStorage.setItem(WIDE_KEY, on ? "1" : "0"); } catch { /* storage unavailable */ }
    renderWideBtn();
    window.dispatchEvent(new Event("resize"));  // the charts redraw at the new width
  }

  // ---- the drag handle between the table and the drawer: its width, remembered (double-click
  // or Home resets it; arrow keys step 32 px)
  const DRAWER_W_KEY = "ragnalog-drawer-w";
  function initDrawerGrip() {
    const dr = $("drawer");
    if (!dr || dr.querySelector(".drawer-grip")) return;
    const g = document.createElement("div");
    g.className = "drawer-grip";
    g.tabIndex = 0;
    g.setAttribute("role", "separator");
    g.setAttribute("aria-orientation", "vertical");
    g.setAttribute("aria-label", "Resize the detail panel");
    g.title = "Drag to resize; double-click to reset";
    dr.prepend(g);
    const body = document.body;
    const clamp = (w) => Math.round(Math.max(360, Math.min(w, body.clientWidth - 440)));
    const apply = (w, save) => {
      if (w == null) body.style.removeProperty("--drawer-w");
      else body.style.setProperty("--drawer-w", clamp(w) + "px");
      if (save) {
        try { if (w == null) localStorage.removeItem(DRAWER_W_KEY); else localStorage.setItem(DRAWER_W_KEY, String(clamp(w))); } catch { /* storage unavailable */ }
        window.dispatchEvent(new Event("resize"));  // the hourly chart redraws at the new width
      }
    };
    try { const v = +localStorage.getItem(DRAWER_W_KEY); if (v > 0) apply(v, false); } catch { /* storage unavailable */ }
    let x0 = 0, w0 = 0;
    const move = (e) => apply(w0 + (x0 - e.clientX), false);
    g.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      x0 = e.clientX; w0 = dr.getBoundingClientRect().width;
      g.setPointerCapture(e.pointerId);
      body.classList.add("drawer-resizing");
      g.addEventListener("pointermove", move);
    });
    const end = (e) => {
      if (!body.classList.contains("drawer-resizing")) return;
      g.removeEventListener("pointermove", move);
      body.classList.remove("drawer-resizing");
      try { g.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
      apply(dr.getBoundingClientRect().width, true);
    };
    g.addEventListener("pointerup", end);
    g.addEventListener("pointercancel", end);
    g.addEventListener("dblclick", () => apply(null, true));
    g.addEventListener("keydown", (e) => {
      const w = dr.getBoundingClientRect().width;
      if (e.key === "ArrowLeft") apply(w + 32, true);
      else if (e.key === "ArrowRight") apply(w - 32, true);
      else if (e.key === "Home") apply(null, true);
      else return;
      e.preventDefault();
      e.stopPropagation();
    });
  }

  function bind() {
    const chartOptions = $("c-chart-options");
    chartOptions.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !chartOptions.open) return;
      e.stopPropagation();
      chartOptions.open = false;
      chartOptions.querySelector("summary").focus();
    });
    document.addEventListener("click", (e) => {
      if (chartOptions.open && !chartOptions.contains(e.target)) chartOptions.open = false;
    });

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
      clearTimeout(mqTimer);
      const value = e.target.value;
      mqTimer = setTimeout(() => commit({ mq: value }, { replace: true, keepPage: true }), 120);
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
    $("c-ctx").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    $("c-scope").addEventListener("click", (e) => {
      if (e.target.closest("[data-unsel]")) return commit({ sig: "" }, { keepPage: true });
      const b = e.target.closest("[data-clear]");
      if (b) clearFilter(b.dataset.clear);
      return undefined;
    });
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
      if (tr) open(tr.dataset.id, true);
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
    $("d-close").onclick = closeDrawer;
    $("btn-wide").onclick = toggleWide;
    initDrawerGrip();
    const more = $("more-menu");
    if (more) {
      more.addEventListener("click", (e) => { if (e.target.closest(".menu-item")) more.open = false; });
      more.addEventListener("keydown", (e) => {
        if (e.key !== "Escape" || !more.open) return;
        e.stopPropagation();
        more.open = false;
        more.querySelector("summary").focus();
      });
      document.addEventListener("click", (e) => { if (more.open && !more.contains(e.target)) more.open = false; });
    }
    renderWideBtn();
    $("d-prev").onclick = () => move(-1);
    $("d-next").onclick = () => move(1);
    $("d-nav").addEventListener("click", (e) => {
      const b = e.target.closest("[data-jump]");
      const t = b && document.getElementById(b.dataset.jump);
      if (!t) return;
      if (t.tagName === "DETAILS") t.open = true;
      // scroll the drawer only: the table and the selected row stay where they are
      const body = $("drawer-body");
      body.scrollTo({ top: t.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 6, behavior: "smooth" });
    });
    {
      // the bar names the group once the headline scrolled away; the nav marks the section in view
      const body = $("drawer-body"), nav = $("d-nav"), drawer = $("drawer");
      let spy = 0;
      body.addEventListener("scroll", () => {
        if (spy) return;
        spy = requestAnimationFrame(() => {
          spy = 0;
          drawer.classList.toggle("scrolled", body.scrollTop > 48);
          const y = body.getBoundingClientRect().top + 24;
          let cur = null;
          for (const b of nav.querySelectorAll("[data-jump]")) {
            const t = document.getElementById(b.dataset.jump);
            if (t && t.getBoundingClientRect().top <= y) cur = b;
            b.classList.remove("on");
          }
          if (body.scrollTop + body.clientHeight >= body.scrollHeight - 2) {
            const vis = [...nav.querySelectorAll("[data-jump]:not([hidden])")];
            cur = vis[vis.length - 1] || cur;
          }
          if (cur && body.scrollTop > 0) cur.classList.add("on");
        });
      }, { passive: true });
    }
    $("tl-zoom").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      if (b.dataset.undo != null) undo();
      else if (b.dataset.zoomsel != null) toggleZoomSel();
    });
    $("tl-bins").addEventListener("change", (e) => {
      if (e.target.id === "tl-binsel") commit({ bin: e.target.value }, { keepPage: true, replace: true });
    });
    $("tl-events").addEventListener("click", (e) => {
      if (!e.target.closest("[data-grave-first]") || !GRAVE.length) return;
      const g = GRAVE[0];
      zoomTo([g.ms - 15 * 6e4, g.last + 15 * 6e4]);
    });
    for (const id of ["drawer-body", "c-cards", "p-inter", "p-log", "tl-sel", "tl-window", "c-timeline"]) {
      $(id).addEventListener("click", (e) => {
        if (id === "tl-sel") {
          if (e.target.closest("[data-fit]")) return commit({ fit: !S.fit }, { keepPage: true, replace: true });
          if (e.target.closest("[data-unsel]")) return commit({ sig: "" }, { keepPage: true });
        }
        if (id === "tl-window") {
          if (e.target.closest("[data-clear-t]")) return commit({ t: null, z: null });
          const sh = e.target.closest("[data-shift]");
          if (sh && !sh.disabled) return shiftWindow(Number(sh.dataset.shift));
          const zb = e.target.closest("[data-zoom]");
          if (zb && !zb.disabled) return zb.dataset.zoom === "in" ? zoomIn() : zb.dataset.zoom === "out" ? zoomOut() : commit({ z: null }, { keepPage: true });
          const pw = e.target.closest("[data-win]");
          if (pw) {
            const nb = Number(pw.dataset.win);
            return ZOOMSEL ? zoomTo([TA + (NB - nb) * BMS, TE]) : commit({ t: [NB - nb, NB - 1], z: null });
          }
        }
        if (id === "drawer-body" && e.target.closest("[data-clip]")) {
          const r = GBY.get(S.sig);
          if (!r) return;
          DCLIP = !(DGEO && DGEO.clip);
          drawHourly(r);
          const b = $("drawer-body").querySelector("[data-clip]");
          if (b) b.focus({ preventScroll: true });
          return;
        }
        if (id === "p-log") {
          if (e.target.closest("[data-more]")) return showMoreLog();
          if (e.target.closest("[data-load]")) return fetchLogPage();
          if (e.target.closest("[data-ctx-close]")) return commit({ ctx: "", cts: "" }, { keepPage: true });
          const lc = e.target.closest("[data-lclear]");
          if (lc) {
            const k = lc.dataset.lclear;
            return commit(k === "t" ? { t: null } : k === "mn" ? { mn: null } : k === "at" ? { at: "", ae: "" } : { ms: [] }, { keepPage: true });
          }
        }
        if (id === "p-inter") {
          const x = e.target.closest("[data-evx]");
          if (x) {
            // expand / fold a burst of events in place (the panel is rebuilt; focus stays on the button)
            if (EVX.has(x.dataset.evx)) EVX.delete(x.dataset.evx); else EVX.add(x.dataset.evx);
            const fk = focusKey(x);
            renderInteractions();
            return restoreFocus(fk);
          }
          const k = e.target.closest("[data-ek]");
          if (k) {
            const kind = k.dataset.ek;
            if (kind === "job_run") return commit({ jobs: !S.jobs }, { keepPage: true, replace: true });
            const ek = new Set(S.ek);
            if (ek.has(kind)) ek.delete(kind); else ek.add(kind);
            return commit({ ek }, { keepPage: true, replace: true });
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
    // merged log rows: Enter / Space expand, arrows move (the row with the cursor is the Tab stop)
    $("ml").addEventListener("keydown", (e) => {
      const line = e.target.closest && e.target.closest(".ml-line");
      if (!line || e.ctrlKey || e.metaKey || e.altKey) return;
      const i = +line.parentElement.dataset.i;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        toggleRow(i);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        LOG.cur = i;
        LOG.curId = VIEW[i] ? VIEW[i].id : LOG.curId;  // a page loading meanwhile restores it
        moveLog(e.key === "ArrowDown" ? 1 : -1);
      }
    });
    // scrolled near the end of the merged log: the next page
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) autoLoad(); }, { rootMargin: "0px 0px 600px 0px" })
        .observe($("ml-foot"));
    }
    $("btn-theme").onclick = toggleTheme;
    if ($("btn-tz")) $("btn-tz").onclick = toggleTz;
    window.ragnalogTheme = toggleTheme;
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
    const ids = $("ids");
    $("btn-ids").onclick = () => { renderIds(); ids.showModal(); };
    ids.addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (b) copy(b.dataset.copy);
      else if (e.target === ids) ids.close();
    });
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      const typing = t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement;
      // Enter on a focused button or link is that control's, not the page shortcut's
      if (e.key === "Enter" && t instanceof Element && t.closest("button, a, summary, [role=tab], [contenteditable]")) return;
      if (e.key === "Escape") {
        if (help.open || ids.open) return;
        if (typing) { t.blur(); return; }
        if (S.tab === "g" && S.sig) closeDrawer();
        else if (S.tab === "l" && S.ctx) commit({ ctx: "", cts: "" }, { keepPage: true });
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || help.open || ids.open) return;
      switch (e.key) {
        case "/": {
          e.preventDefault();
          const inp = S.tab === "l" ? $("l-search") : $("f-search");
          if (S.tab === "i") commit({ tab: "g" }, { keepPage: true });
          inp.focus();
          inp.select();
          break;
        }
        case "w": toggleWide(); break;
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
        case "[": shiftWindow(-1); break;
        case "]": shiftWindow(1); break;
        case "+": case "=": zoomIn(); break;
        case "-": zoomOut(); break;
        case "u": undo(); break;
        case "1": commit({ tab: "g" }, { keepPage: true }); break;
        case "2": commit({ tab: "i" }, { keepPage: true }); break;
        case "3": commit({ tab: "l" }, { keepPage: true }); break;
        case "?": help.showModal(); break;
        default:
      }
    });
    window.addEventListener("scroll", markStuck, { passive: true });
    let rz = 0;
    window.addEventListener("resize", () => {
      clearTimeout(rz);
      rz = setTimeout(() => {
        renderTimeline();
        const r = S.sig && GBY.get(S.sig);
        if (r && $("d-hourly")) drawHourly(r);
        updateSticky();
      }, 150);
    });
  }

  // ---------------------------------------------------------------- start
  if (S.theme === "dark" || S.theme === "light") document.documentElement.setAttribute("data-theme", S.theme);
  setZone(S.tz ? S.tz === "local" : TZL);
  renderTzBtn();
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
    T2 = performance.now();
    renderFoot();
  }, 0));
  }

  // ---------------------------------------------------------------- loader
  /** A failed payload request: the server's error.v1 (message, hint) when the body is one. */
  function loadError(url, status, body) {
    const err = new Error(`GET ${url}: HTTP ${status}`);
    try {
      const doc = JSON.parse(body);
      const e = doc && doc.schema === "error.v1" && doc.error;
      if (e && e.message) err.apiError = { code: String(e.code || ""), message: String(e.message), hint: e.hint ? String(e.hint) : "", status };
    } catch {
      /* not JSON: reported as the HTTP status */
    }
    return err;
  }
  /** Show the server's error in a box at the top of the page (no stack trace). */
  function showLoadError(e) {
    const box = document.createElement("div");
    box.className = "load-error";
    box.setAttribute("role", "alert");
    const h = document.createElement("h2");
    h.textContent = "Could not load this cluster page";
    const m = document.createElement("p");
    m.textContent = e.message;
    box.append(h, m);
    if (e.hint) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = e.hint;
      box.append(p);
    }
    const c = document.createElement("p");
    c.className = "code";
    c.textContent = `HTTP ${e.status}` + (e.code ? ` · ${e.code}` : "");
    box.append(c);
    const app = $("app");
    if (app) app.prepend(box);
    else document.body.prepend(box);
  }
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
        .then((r) => (r.ok ? r.json() : r.text().then((body) => Promise.reject(loadError(url, r.status, body)))))
        .then(start)
        .catch((err) => (err && err.apiError ? showLoadError(err.apiError) : fatal(err)));
    }
  }
})();
