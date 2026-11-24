/* ragnalog report: renders a report.v1 payload, embedded in #report-data or fetched from
   <html data-report-url> (webapp). Vanilla ES2022; the standalone file makes no network requests. */
"use strict";
(() => {
  const T0 = performance.now();
  const $ = (id) => document.getElementById(id);

  function fatal(err) {
    const el = $("fatal");
    if (!el) return;
    el.hidden = false;
    el.textContent += "Report error: " + (err && err.stack ? err.stack : String(err)) + "\n";
  }
  window.addEventListener("error", (e) => fatal(e.error || e.message));
  window.addEventListener("unhandledrejection", (e) => fatal(e.reason));

  function boot(D) {

  // ---------------------------------------------------------------- formatting
  const NF = new Intl.NumberFormat("en-US");
  const fmt = (n) => NF.format(n);
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c]));
  const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  // rich tooltips (tips.js, loaded first); without it the details are simply not shown
  const TIPS = window.RagnalogTips || null;
  const tipCard = (o) => (TIPS ? TIPS.card(o) : "");

  const UNITS = [[1e3, "K"], [1e6, "M"], [1e9, "B"]];
  function compact(n) {
    const a = Math.abs(n);
    if (a < 1000) return String(Math.round(n * 100) / 100);
    let i = UNITS.length - 1;
    while (i > 0 && a < UNITS[i][0]) i--;
    for (;; i++) {
      // rounded first: 999,500 is "1M", not "1000K"
      const x = n / UNITS[i][0];
      const t = Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, "");
      if (Math.abs(+t) < 1000 || i === UNITS.length - 1) return t + UNITS[i][1];
    }
  }
  // @zone: parseTs … tsFull (tests/report/test_report_zone.py runs this part with node)
  const parseTs = (iso) => Date.parse(String(iso).replace(/(\.\d{3})\d+/, "$1"));
  // times show in the browser's time zone or, toggled, in UTC: a viewer preference kept like the
  // theme (tz=local|utc in a link wins); windows in the hash stay UTC either way
  const TZ_KEY = "ragnalog-tz";
  let TZL = (() => { try { return localStorage.getItem(TZ_KEY) !== "utc"; } catch { return true; } })();
  let TZ = "UTC";
  /** The ISO time shifted into the shown zone (still printed with toISOString's layout). */
  function zoned(iso) {
    if (!iso) return iso;
    const ms = parseTs(iso);
    if (ms !== ms) return iso;
    // UTC too goes through toISOString: hour stamps ("…T14:00:00Z") get their milliseconds
    return new Date(TZL ? ms - new Date(ms).getTimezoneOffset() * 6e4 : ms).toISOString();
  }
  function zoneName(ms) {
    try {
      const p = new Intl.DateTimeFormat("en-GB", { timeZoneName: "short" }).formatToParts(new Date(ms));
      return (p.find((x) => x.type === "timeZoneName") || {}).value || "local";
    } catch { return "local"; }
  }
  const ts16 = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(0, 10) + " " + iso.slice(11, 16)) : "");
  const tsShort = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(5, 10) + " " + iso.slice(11, 16)) : "");
  const tsFull = (iso) => (iso ? ((iso = zoned(iso)), iso.slice(0, 10) + " " + iso.slice(11, 23)) : "");
  // @zone-end
  function duration(a, b) {
    const ms = parseTs(b) - parseTs(a);
    if (!(ms >= 0)) return "";
    const h = ms / 3.6e6;
    if (h < 1) return Math.max(1, Math.round(ms / 60000)) + " min";
    if (h < 48) return (h < 10 ? h.toFixed(1) : h.toFixed(0)) + " h";
    return (h / 24).toFixed(1).replace(/\.0$/, "") + " d";
  }
  function ratioText(r) {
    if (r >= 10) return r.toFixed(0);
    if (r >= 1) return r.toFixed(1);
    return r.toPrecision(2);
  }
  const { loggerHtml, excHtml, tsHtml, kvHtml, nilHtml } = RagnalogEntryCard;
  const PH = /&lt;([A-Z][A-Z0-9_]*|\*)&gt;/g;
  const REPEATED = /^\s*([^\w\s]){2,}\s*$|^\s*[^\w\s]{6,}\s*$/;
  const tplHtml = (t) =>
    t && REPEATED.test(t)
      ? `<span class="rep" title="${esc(t)}">(repeated characters)</span>`
      : logInline(t).replace(PH, (m, n) => `<span class="ph">${n.toLowerCase()}</span>`);
  function shortLogger(l) {
    if (!l) return "";
    const parts = l.split(".");
    if (parts.length <= 2) return l;
    return parts.slice(0, -1).map((p) => p.slice(0, 1)).join(".") + "." + parts[parts.length - 1];
  }
  const shortExc = (t) => (t ? t.slice(t.lastIndexOf(".") + 1) : "");
  const basename = (p) => (p ? String(p).split(/[\\/]/).pop() : "");
  const shellArg = (s) => (/^[\w.:@%+=,/-]+$/.test(s) ? s : "'" + String(s).replace(/'/g, "'\\''") + "'");

  // ---------------------------------------------------------------- levels
  const BUCKETS = ["ERROR", "WARN", "INFO", "DEBUG", "OTHER"];
  const bucketOf = (level) =>
    level >= 50 ? "ERROR" : level >= 40 ? "WARN" : level >= 30 ? "INFO" : level >= 10 ? "DEBUG" : "OTHER";
  const badge = (name, level) => `<span class="lv lv-${bucketOf(level)}">${esc(name || "?")}</span>`;

  // ---------------------------------------------------------------- data
  if (!D || D.schema !== "report.v1") throw new Error("unsupported payload schema: " + (D && D.schema));
  const HOURS = (D.timeline && D.timeline.hours) || [];
  const NH = HOURS.length;
  const T0MS = NH ? parseTs(HOURS[0]) : 0;
  const setZone = (local) => {
    TZL = local;
    if (!local) { TZ = "UTC"; return; }
    const a = zoneName(NH ? parseTs(HOURS[0]) : Date.now()), b = zoneName(NH ? parseTs(HOURS[NH - 1]) : Date.now());
    TZ = a === b ? a : a + "/" + b;  // the set spans a DST change
  };
  const ENTRIES = D.entries || (D.entries = {});
  const ENTRIES_URL = D.entries_url || "";
  const MERGES = D.merges || [];
  const LS = D.logset || {};
  const SETREF = shellArg(LS.name || LS.id || "SET");
  const CMPD = D.compare || null;
  const OTHER = CMPD ? CMPD.other || {} : null;
  const OTHERREF = OTHER ? shellArg(OTHER.name || OTHER.id || "SET_A") : "";
  const OTHERNAME = OTHER ? OTHER.name || OTHER.id : "";
  const PAGE = 100;
  const SRC_PATHS = (LS.sources || []).map((s) => (s && typeof s === "object" ? s.path : s));
  const srcPath = (i) => (typeof i === "number" ? SRC_PATHS[i] ?? "#" + i : i == null ? "" : String(i));
  const pairs = (list) => (list || []).map((x) => (Array.isArray(x) ? { value: x[0], count: x[1] } : x));

  // ---------------------------------------------------------------- chart bins
  // @bins: BIN_SIZES … binChoices (tests/report/test_report_bins.py runs this part with node)
  const HOUR = 3.6e6;
  const BIN_SIZES = [["1s", 1e3], ["5s", 5e3], ["10s", 1e4], ["30s", 3e4], ["1m", 6e4], ["2m", 12e4],
    ["5m", 3e5], ["10m", 6e5], ["15m", 9e5], ["30m", 18e5], ["1h", HOUR], ["2h", 2 * HOUR],
    ["3h", 3 * HOUR], ["4h", 4 * HOUR], ["6h", 6 * HOUR], ["12h", 12 * HOUR], ["1d", 24 * HOUR],
    ["2d", 48 * HOUR], ["7d", 168 * HOUR]];
  const BIN_MS = Object.fromEntries(BIN_SIZES);
  const binKey = (ms) => (BIN_SIZES.find(([, v]) => v === ms) || [""])[0];
  const UNIT_NAMES = { s: "second", m: "minute", h: "hour", d: "day" };
  /** What one bin is, for the chart's title: "minute", "5 minutes", "hour", "3 hours", "day". */
  function binName(ms) {
    const m = /^(\d+)([smhd])$/.exec(binKey(ms));
    if (!m) return Math.round(ms / 6e4) + " minutes";
    return m[1] === "1" ? UNIT_NAMES[m[2]] : m[1] + " " + UNIT_NAMES[m[2]] + "s";
  }
  /** A bin size in the bins menu: "5 s", "1 min", "3 h", "1 d". */
  const binShort = (ms) => binKey(ms).replace(/^(\d+)([smhd])$/, (_, n, u) => n + " " + (u === "m" ? "min" : u));
  /** The automatic bin for span ms over plotW px: the finest whose bars get at least 5 px (the
   *  rule the hourly chart always had), and not finer than floor ms. */
  function autoBin(span, plotW, floor) {
    for (const [, ms] of BIN_SIZES) if (ms >= floor && Math.ceil(span / ms) * 5 <= plotW) return ms;
    return BIN_SIZES[BIN_SIZES.length - 1][1];
  }
  /** The bins a viewer may pick for span ms over plotW px: 2 bars at least, one per 2 px at most
   *  (finer than the automatic one), not finer than floor ms. */
  const binChoices = (span, plotW, floor) => BIN_SIZES.map(([, ms]) => ms).filter((ms) => {
    const n = Math.ceil(span / ms);
    return ms >= floor && n >= 2 && n <= Math.floor(plotW / 2);
  });
  // @bins-end
  /** End of the timeline's last hour. */
  const T1MS = T0MS + NH * HOUR;
  /** A zoom range [a, b) in ms inside the timeline, or null for the whole timeline (or a range of
   *  under 10 seconds). */
  function clampZoom(z) {
    if (!z || !NH) return null;
    let [a, b] = z;
    if (!(a === a && b === b)) return null;
    if (a > b) [a, b] = [b, a];
    a = Math.max(T0MS, a);
    b = Math.min(T1MS, b);
    if (b - a < 1e4 || (a <= T0MS && b >= T1MS)) return null;
    return [a, b];
  }
  /** The timeline hours [first, last] that cover the times [a, b): the window of a zoom. */
  const hoursCovering = (a, b) => [
    Math.max(0, Math.floor((a - T0MS) / HOUR)), Math.min(NH - 1, Math.ceil((b - T0MS) / HOUR) - 1),
  ];
  // the zoom toggle: on (the default), a drag on the chart zooms into the selection; off, it
  // selects a time window. A viewer preference like the theme, shared with the cluster page.
  const ZS_KEY = "ragnalog-dragzoom";
  let ZOOMSEL = (() => { try { return localStorage.getItem(ZS_KEY) !== "0"; } catch { return true; } })();
  /** Earlier chart views (window, zoom, bin) for Undo, the newest last; Clear is undone too. */
  const UNDO = [];
  const viewKey = (s) => JSON.stringify([s.t || null, s.z || null, s.bin || ""]);

  // ---------------------------------------------------------------- grave errors
  // report/grave.py: out of memory, a stack overflow, a JVM error, an exhausted resource or FATAL,
  // entries of a kind within 60 s folded into one marker; drawn loudly over the chart
  const GRAVE_KINDS = ["oom", "stack_overflow", "jvm_error", "resource", "fatal"];  // most grave first
  const GRAVE_LABEL = { oom: "Out of memory", stack_overflow: "Stack overflow", jvm_error: "JVM error", resource: "Resource exhausted", fatal: "FATAL" };
  const GRAVE_TAG = { oom: "OOM", stack_overflow: "STACK", jvm_error: "JVM", resource: "LIMIT", fatal: "FATAL" };
  const GRAVE = (D.grave || []).map((g) => ({
    kind: Object.hasOwn(GRAVE_TAG, g.kind) ? g.kind : "fatal", ts: g.ts, last_ts: g.last_ts || g.ts,
    ms: parseTs(g.ts), last: parseTs(g.last_ts || g.ts), count: g.count || 1, sig: g.signature || "", text: g.text || "",
  })).filter((g) => g.ms === g.ms).sort((a, b) => a.ms - b.ms);
  const graveTop = (items) => items.reduce((k, g) => (GRAVE_KINDS.indexOf(g.kind) < GRAVE_KINDS.indexOf(k) ? g.kind : k), items[0].kind);
  /** A grave badge's text ("OOM", "OOM ×3") and a safe first guess of its width (the font decides:
   *  fitBadges sets the real one once drawn). */
  const badgeText = (items, kind) => GRAVE_TAG[kind] + (items.length > 1 ? " ×" + items.length : "");
  const badgeW = (items, kind) => 12 + badgeText(items, kind).length * 7.4;
  /** Size each drawn grave badge to its text, kept inside [left, right]; a flag hangs from its line
   *  (data-x) to the right, or to the left (data-side="l"). */
  function fitBadges(svg, left, right) {
    for (const g of svg.querySelectorAll(".grave-badge")) {
      const t = g.querySelector("text"), r = g.querySelector("rect");
      let len = 0;
      try { len = t.getComputedTextLength(); } catch { len = 0; }
      if (!(len > 0)) continue;  // not laid out (a hidden tab): the guess stays
      const w = Math.ceil(len) + 12, at = +g.dataset.x;
      let x = g.dataset.side === "l" ? at - w + 0.5 : at - 0.5;
      x = Math.max(left, Math.min(right - w, x));
      r.setAttribute("x", r1(x));
      r.setAttribute("width", w);
      t.setAttribute("x", r1(x + 6));
    }
  }
  const GRAVE_SIGS = new Map();
  for (const g of GRAVE) if (!GRAVE_SIGS.has(g.sig)) GRAVE_SIGS.set(g.sig, g.kind);

  const CMP = CMPD ? new Map() : null;
  if (CMPD) {
    for (const d of CMPD.new || []) {
      if (Array.isArray(d)) CMP.set(d[0], { cls: "new", a: 0, b: d[1] });
      else CMP.set(d.signature, { cls: "new", a: 0, b: d.count_b, d });
    }
    for (const d of CMPD.changed || []) {
      if (Array.isArray(d)) CMP.set(d[0], { cls: "changed", a: d[1], b: d[2] });
      else CMP.set(d.signature, { cls: "changed", a: d.count_a, b: d.count_b, d });
    }
  }
  const CMPN = (() => {
    const c = (CMPD && CMPD.counts) || {};
    const len = (k) => (CMPD && CMPD[k] ? CMPD[k].length : 0);
    return {
      new: c.new ?? len("new"), changed: c.changed ?? len("changed"), vanished: c.vanished ?? len("vanished"),
      unchanged: c.unchanged ?? (CMPD ? CMPD.unchanged_count || 0 : 0),
    };
  })();

  function finish(r) {
    r.key = (r.tpl + "\n" + r.logger + "\n" + r.exc).toLowerCase();
    r.sigs = r.members ? r.members.map((m) => m.sig) : [r.sig];
    return r;
  }
  function mkRow(g) {
    const cmp = CMP ? CMP.get(g.signature) || { cls: "unchanged", a: g.count, b: g.count } : null;
    return finish({
      id: g.signature, sig: g.signature, coarse: g.coarse_signature || g.signature,
      tpl: g.template || "", level: g.level, lvl: g.level_name, bucket: bucketOf(g.level),
      logger: g.logger || "", exc: g.exception_type || "",
      count: g.count, first: g.first_seen, last: g.last_seen,
      users: g.user_count, threads: g.thread_count, nsrc: g.source_count,
      hourly: g.hourly || [], g, members: null, kind: "sig", cmp, gone: false,
    });
  }
  function goneRow(d) {
    return finish({
      id: d.signature, sig: d.signature, coarse: d.signature,
      tpl: d.template || "", level: d.level, lvl: d.level_name, bucket: bucketOf(d.level),
      logger: d.logger || "", exc: d.exception_type || "",
      count: 0, first: null, last: null, users: 0, threads: 0, nsrc: 0,
      hourly: [], g: null, members: null, kind: "sig",
      cmp: { cls: "vanished", a: d.count_a, b: 0, d }, gone: true,
    });
  }
  const BASE = (D.groups || []).map(mkRow);
  const GONE = CMPD ? (CMPD.vanished || []).map(goneRow) : [];
  const BASE_BY = new Map();
  for (const r of BASE) BASE_BY.set(r.id, r);
  for (const r of GONE) if (!BASE_BY.has(r.id)) BASE_BY.set(r.id, r);
  const CLUSTER = new Map();
  for (const m of MERGES) for (const s of m.signatures) CLUSTER.set(s, m);
  const PRESENT = new Set([...BASE, ...GONE].map((r) => r.bucket));
  const DEFAULT_LV = (() => {
    const s = new Set(["ERROR", "WARN"].filter((b) => PRESENT.has(b)));
    return s.size ? s : new Set(PRESENT);
  })();

  /** Whole-set order (members of merged/coarse rows); the table sorts with SORTS. */
  const byCount = (a, b) =>
    b.count - a.count || (b.cmp ? b.cmp.a : 0) - (a.cmp ? a.cmp.a : 0) || cmpStr(a.id, b.id);
  /** Count in the window (when one is set), then as byCount. */
  const byShown = (a, b) => (S.t ? cnt(b) - cnt(a) : 0) || byCount(a, b);
  const deltaAbs = (r) => (r.cmp ? Math.abs(r.cmp.b - r.cmp.a) : 0);
  const seenMs = (r, k) => {
    const x = winOf(r)[k];
    return x ? x.ms : NaN;
  };
  /** How much a group stands out: with a window its rate there over its rate outside (+1
   *  smoothing), else its busiest hour over its average hour. Cached per window. */
  function spikeOf(r) {
    if (r.spKey === WKEY) return r.sp;
    let v = 0;
    if (S.t) {
      const n = winOf(r).n;
      const hin = S.t[1] - S.t[0] + 1, hout = Math.max(1, NH - hin);
      v = n ? n / hin / ((r.count - n + 1) / hout) : 0;
    } else if (r.hourly.length) {
      // over every hour of the set, so a group seen in one hour only is a spike, not x1
      let peak = 0;
      for (const [, c] of r.hourly) if (c > peak) peak = c;
      v = peak / (r.count / NH);
    }
    r.spKey = WKEY;
    return (r.sp = v);
  }
  /** 1 when the group's first entry of the whole set falls in the window: it started there. */
  const newIn = (r) => (S.t && r.first && parseTs(r.first) >= T0MS + S.t[0] * 3.6e6 ? 1 : 0);
  const SORTS = {
    count: ["Count", byShown],
    spike: ["Spike (window vs rest)", (a, b) => spikeOf(b) - spikeOf(a) || byShown(a, b)],
    last_seen: ["Last seen", (a, b) => (S.t ? (seenMs(b, "last") || -1) - (seenMs(a, "last") || -1) : cmpStr(b.last || "", a.last || "")) || byShown(a, b)],
    first_seen: ["First seen", (a, b) => (S.t ? newIn(b) - newIn(a) || (seenMs(a, "first") || Infinity) - (seenMs(b, "first") || Infinity) : cmpStr(a.first || "￿", b.first || "￿")) || byShown(a, b)],
    level: ["Level", (a, b) => b.level - a.level || byShown(a, b)],
    template: ["Template", (a, b) => cmpStr(a.tpl, b.tpl) || byShown(a, b)],
    logger: ["Logger", (a, b) => cmpStr(a.logger, b.logger) || byShown(a, b)],
    users: ["Users", (a, b) => b.users - a.users || byShown(a, b)],
  };
  if (CMP) SORTS.delta = ["Change (|B − A|)", (a, b) => deltaAbs(b) - deltaAbs(a) || byShown(a, b)];

  function aggregate(id, members, tpl, kind) {
    members.sort(byCount);
    const top = members[0];
    let count = 0, first = null, last = null, users = 0, threads = 0, nsrc = 0, a = 0, b = 0;
    const hm = new Map();
    for (const m of members) {
      count += m.count;
      if (m.first && (!first || m.first < first)) first = m.first;
      if (m.last && (!last || m.last > last)) last = m.last;
      users = Math.max(users, m.users);
      threads = Math.max(threads, m.threads);
      nsrc = Math.max(nsrc, m.nsrc);
      if (m.cmp) { a += m.cmp.a; b += m.cmp.b; }
      for (const [i, n] of m.hourly) hm.set(i, (hm.get(i) || 0) + n);
    }
    const hourly = [...hm.entries()].sort((x, y) => x[0] - y[0]);
    const cmp = CMP
      ? { cls: a === 0 ? "new" : b === 0 ? "vanished" : a === b ? "unchanged" : "changed", a, b }
      : null;
    return finish({
      ...top, id, tpl: tpl == null ? top.tpl : tpl, count, first, last, users, threads, nsrc,
      hourly, members, kind, cmp, gone: count === 0, g: null, w: null, wKey: "", spKey: null, sp: 0,
    });
  }

  const VIEWS = {};
  function view(name) {
    if (VIEWS[name]) return VIEWS[name];
    let rows;
    if (name === "coarse") {
      const by = new Map();
      for (const r of BASE) {
        const list = by.get(r.coarse);
        if (list) list.push(r); else by.set(r.coarse, [r]);
      }
      rows = [];
      for (const [k, ms] of by) rows.push(ms.length === 1 ? ms[0] : aggregate("c:" + k, ms, null, "coarse"));
    } else if (name === "merged") {
      const seen = new Set();
      rows = [];
      for (const m of MERGES) {
        const ms = m.signatures.map((s) => BASE_BY.get(s)).filter((r) => r && !r.gone);
        if (ms.length < 2) continue;
        for (const r of ms) seen.add(r.sig);
        const agg = aggregate("m:" + m.cluster_id, ms, m.drain_template, "merged");
        agg.cluster = m;
        rows.push(agg);
      }
      for (const r of BASE) if (!seen.has(r.sig)) rows.push(r);
    } else {
      rows = BASE.slice();
    }
    rows = rows.concat(GONE);
    let max = 1, min = Infinity;
    for (const r of rows) {
      if (r.count > max) max = r.count;
      if (r.count > 0 && r.count < min) min = r.count;
    }
    if (!(min < Infinity)) min = 1;
    const byId = new Map(rows.map((r) => [r.id, r]));
    return (VIEWS[name] = { rows, byId, max, min });
  }
  const lookup = (id) => view(S.view).byId.get(id) || BASE_BY.get(id) || null;

  // ---------------------------------------------------------------- state <-> hash
  const VIEW_NAMES = ["sig", "coarse", "merged"];
  const CMP_NAMES = ["all", "new", "vanished", "changed"];
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
  const parseWindow = (s) => windowOf(s, T0MS, 3.6e6, NH);
  // the chart's zoom (z=20261029T1400-20261029T1530, UTC, seconds when not whole minutes) and
  // its bin size (bin=5m); both are views of the timeline only, the window stays in whole hours
  const zstamp = (ms) => new Date(ms).toISOString().slice(0, 19).replace(/[-:]/g, "").replace(/(T\d{4})00$/, "$1");
  function unzstamp(s) {
    const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)?$/.exec(s);
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : NaN;
  }
  function parseZoom(s) {
    const m = /^(\d{8}T\d{4}(?:\d\d)?)-(\d{8}T\d{4}(?:\d\d)?)$/.exec(s || "");
    return m ? clampZoom([unzstamp(m[1]), unzstamp(m[2])]) : null;
  }
  function parseHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    const v = p.get("view");
    const c = p.get("cmp");
    const sort = p.get("sort");
    return {
      q: p.get("q") || "",
      lv: p.has("lv") ? new Set(p.get("lv").split(",").filter((x) => BUCKETS.includes(x))) : new Set(DEFAULT_LV),
      lg: p.get("lg") || "",
      ex: p.get("ex") || "",
      hx: p.get("hx") === "1",
      sort: sort && Object.hasOwn(SORTS, sort) ? sort : "count",
      view: VIEW_NAMES.includes(v) && (v !== "merged" || MERGES.length) ? v : "sig",
      cmp: CMP && CMP_NAMES.includes(c) ? c : "all",
      p: Math.max(0, (parseInt(p.get("p"), 10) || 1) - 1),
      hasPage: p.has("p"),
      sig: p.get("sig") || "",
      t: parseWindow(p.get("t")),
      z: parseZoom(p.get("z")),
      tab: p.get("tab") === "users" ? "users" : "",
      user: p.get("user") || "",
      bin: Object.hasOwn(BIN_MS, p.get("bin") || "") ? p.get("bin") : "",
      theme: p.get("theme") || "",
      tz: ["local", "utc"].includes(p.get("tz")) ? p.get("tz") : "",
      mute: new Set((p.get("mute") || "").split(",").filter((x) => BASE_BY.has(x))),
      hl: new Set((p.get("hl") || "").split(",").filter((x) => BUCKETS.includes(x))),
    };
  }
  const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
  function hashOf(s) {
    const p = new URLSearchParams();
    if (s.q) p.set("q", s.q);
    if (!sameSet(s.lv, DEFAULT_LV)) p.set("lv", [...s.lv].join(",") || "none");
    if (s.lg) p.set("lg", s.lg);
    if (s.ex) p.set("ex", s.ex);
    if (s.hx) p.set("hx", "1");
    if (s.sort !== "count") p.set("sort", s.sort);
    if (s.view !== "sig") p.set("view", s.view);
    if (s.cmp !== "all") p.set("cmp", s.cmp);
    if (s.t) p.set("t", windowText(s.t, T0MS, 3.6e6));
    if (s.z) p.set("z", zstamp(s.z[0]) + "-" + zstamp(s.z[1]));
    if (s.tab) p.set("tab", s.tab);
    if (s.user) p.set("user", s.user);
    if (s.bin) p.set("bin", s.bin);
    if (s.p > 0) p.set("p", String(s.p + 1));
    if (s.sig) p.set("sig", s.sig);
    if (s.theme) p.set("theme", s.theme);
    if (s.tz) p.set("tz", s.tz);
    if (s.mute.size) p.set("mute", [...s.mute].sort().join(","));
    if (s.hl.size) p.set("hl", BUCKETS.filter((b) => s.hl.has(b)).join(","));
    return p.toString();
  }
  let S = parseHash();
  let CUR = "";

  function commit(patch, opts = {}) {
    if ("sig" in patch && patch.sig !== S.sig) CLIP.d = null; // each group's chart starts automatic
    if (!opts.noUndo && ("t" in patch || "z" in patch || "bin" in patch) && viewKey(S) !== viewKey({ ...S, ...patch })) {
      UNDO.push({ t: S.t ? [...S.t] : null, z: S.z ? [...S.z] : null, bin: S.bin || "" });
      if (UNDO.length > 100) UNDO.shift();
    }
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
    S = parseHash();
    if (S.theme === "dark" || S.theme === "light") {
      // back/forward to a link that names a theme shows it, as opening that link does
      document.documentElement.setAttribute("data-theme", S.theme);
      renderThemeBtn();
    }
    if (S.tz && (S.tz === "local") !== TZL) applyZone(S.tz === "local");
    $("f-search").value = S.q;
    update();
  }
  window.addEventListener("popstate", onNav);
  window.addEventListener("hashchange", () => {
    if (hashOf(parseHash()) !== hashOf(S)) onNav();
  });

  // ---------------------------------------------------------------- filtering
  let Q = { text: "", sig: "" };
  function parseQ(q) {
    q = q.trim();
    const m = /^sig:\s*(\S*)/i.exec(q);
    return m ? { text: "", sig: m[1].toLowerCase() } : { text: q.toLowerCase(), sig: "" };
  }
  // ---------------------------------------------------------------- time window
  // A window S.t = [a, b] (hour indices) narrows the table's numbers to it: counts, first and
  // last seen, sorting, count bars, summary and drawer. The per-group hourly counts cover every
  // entry with a timestamp, so window counts are exact. First/last seen in the window are exact
  // when the group's own first/last entry falls in it, otherwise known to the hour ("14:xx").
  const hourOf = (iso) => (iso ? Math.floor((parseTs(iso) - T0MS) / 3.6e6) : -1);
  let WKEY = "";
  /** Window stats of a row: n entries, first/last as {iso, ms, approx}; cached per window. */
  function winOf(r) {
    if (r.wKey === WKEY && r.w) return r.w;
    const [a, b] = S.t;
    let n = 0, f = -1, l = -1;
    for (const [i, c] of r.hourly) {
      if (i < a) continue;
      if (i > b) break;
      n += c;
      if (f < 0) f = i;
      l = i;
    }
    const at = (iso, h, end) => {
      if (h < 0) return null;
      if (iso && hourOf(iso) === h) return { iso, ms: parseTs(iso), approx: false };
      const ms = T0MS + h * 3.6e6;
      return { iso: HOURS[h], ms: end ? ms + 3.6e6 - 1 : ms, approx: true };
    };
    r.wKey = WKEY;
    return (r.w = { n, first: at(r.first, f, false), last: at(r.last, l, true) });
  }
  /** Entries of a row in the window, else in the whole set. */
  const cnt = (r) => (S.t ? winOf(r).n : r.count);
  const inWindow = (r) => winOf(r).n > 0;
  /** "11-03 14:05", or "11-03 14:xx" when only the hour is known. */
  const seenText = (x, long) => (!x ? "" : x.approx ? (long ? ts16(x.iso) : tsShort(x.iso)).slice(0, -2) + "xx" : long ? ts16(x.iso) : tsShort(x.iso));
  const seenTitle = (x) => (x && x.approx ? "Within this hour: the exact time of the group's entries inside the window is not in the report" : "");
  /** Signatures of a row (its members for a coarse or merged row) that have entries here. */
  const sigsOf = (r) => (r.members || [r]).filter((m) => !m.gone).map((m) => m.sig);
  /** The grave kind of a row (any of its signatures), or "". */
  const graveKind = (r) => { for (const s of r.sigs || [r.sig]) if (GRAVE_SIGS.has(s)) return GRAVE_SIGS.get(s); return ""; };
  const isMuted = (r) => S.mute.size > 0 && !r.gone && sigsOf(r).every((s) => S.mute.has(s));
  let LVKEY = null, LV_EFF = null;
  /** The timeline's level series without the entries of muted groups. */
  function levelData() {
    const key = [...S.mute].sort().join(",");
    if (key === LVKEY) return LV_EFF;
    LVKEY = key;
    const src = (D.timeline || {}).levels || {};
    if (!S.mute.size) return (LV_EFF = src);
    const out = {};
    for (const b of BUCKETS) out[b] = Float64Array.from(src[b] || []);
    for (const s of S.mute) {
      const r = BASE_BY.get(s);
      const arr = r && out[r.bucket];
      if (arr) for (const [i, n] of r.hourly) arr[i] = Math.max(0, arr[i] - n);
    }
    return (LV_EFF = out);
  }
  /** Hour -> [group, count] of every signature group active in it (built on first hover). */
  let BY_HOUR = null;
  /** The largest groups of hours [a, b] the chart shows (not muted, level not hidden): what a
   *  bar is made of, for the timeline tooltip. */
  function topIn(a, b, n) {
    if (!BY_HOUR) {
      BY_HOUR = Array.from({ length: NH }, () => []);
      for (const r of BASE) if (!r.gone) for (const [i, c] of r.hourly) if (i >= 0 && i < NH) BY_HOUR[i].push(r, c);
    }
    const sum = new Map();
    for (let h = a; h <= b; h++) {
      const l = BY_HOUR[h];
      for (let j = 0; j < l.length; j += 2) sum.set(l[j], (sum.get(l[j]) || 0) + l[j + 1]);
    }
    const out = [];
    for (const [r, c] of sum) if (!S.hl.has(r.bucket) && !(S.mute.size && S.mute.has(r.sig))) out.push([r, c]);
    out.sort((x, y) => y[1] - x[1]);
    return out.slice(0, n);
  }
  /** Level totals of the timeline over hours [a, b] (muted groups left out). */
  function levelsIn(a, b) {
    const out = {};
    const levels = levelData();
    for (const k of BUCKETS) {
      const arr = levels[k] || [];
      let t = 0;
      for (let i = a; i <= b && i < arr.length; i++) t += arr[i] || 0;
      out[k] = t;
    }
    return out;
  }
  function match(r, skip) {
    if (skip !== "lv" && !S.lv.has(r.bucket)) return false;
    if (Q.sig) {
      if (!r.id.startsWith(Q.sig) && !r.coarse.startsWith(Q.sig) && !r.sigs.some((s) => s.startsWith(Q.sig))) return false;
    } else if (Q.text && !r.key.includes(Q.text)) return false;
    if (S.lg && skip !== "lg" && r.logger !== S.lg && !r.logger.startsWith(S.lg + ".")) return false;
    if (S.ex && skip !== "ex" && r.exc !== S.ex) return false;
    if (S.hx && !r.exc) return false;
    if (CMP && S.cmp !== "all" && skip !== "cmp" && r.cmp.cls !== S.cmp) return false;
    if (S.t && skip !== "t" && !inWindow(r)) return false;
    return true;
  }

  let LIST = [];
  let SCALE = { max: 1, min: 1 }; // count bars: the view's range, or the window's
  function compute() {
    const v = view(S.view);
    Q = parseQ(S.q);
    WKEY = S.t ? S.t.join("-") : "";
    LIST = v.rows.filter((r) => match(r, ""));
    LIST.sort(SORTS[S.sort][1]);
    if (S.t) {
      let max = 1, min = Infinity;
      for (const r of v.rows) {
        const n = winOf(r).n;
        if (n > max) max = n;
        if (n > 0 && n < min) min = n;
      }
      SCALE = { max, min: min < Infinity ? min : 1 };
    } else SCALE = { max: v.max, min: v.min };
    return v;
  }

  // ---------------------------------------------------------------- charts
  const GAP = 2;
  const r1 = (x) => Math.round(x * 10) / 10;
  function chooseBin(nh, plotW) {
    for (const k of [1, 2, 3, 4, 6, 12, 24, 48, 168]) if (Math.ceil(nh / k) * 5 <= plotW) return k;
    return 168;
  }
  function niceTicks(max, parts = 3, int = false) {
    if (max <= 0) return [0, 1];
    const raw = max / parts;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
    if (int) step = step < 1 ? 1 : step === 2.5 ? 2 : step;  // a short chart: no 0.5 entries, no 2.5 steps
    const out = [];
    for (let v = 0; v < max + step * 0.999; v += step) out.push(Math.round(v * 1e6) / 1e6);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  }
  // @hours: hodOf … binRange (tests/report/test_report_zone.py)
  /** Hour of day and day number of hour i in the shown zone (a half-hour zone's hours start at :30). */
  const hodOf = (i) => Number(zoned(HOURS[i]).slice(11, 13));
  const dayOf = (i) => Math.floor(parseTs(zoned(HOURS[i]).slice(0, 10)) / 864e5);
  const newDay = (i) => i === 0 || dayOf(i) !== dayOf(i - 1);
  function hourLabel(i) {
    const z = zoned(HOURS[i]);
    return newDay(i) && hodOf(i) === 0 ? z.slice(5, 10) : z.slice(11, 16);
  }
  /** Bins of k hours start on the shown zone's clock (3-hour bins at 00, 03, ...), not at the
   *  first hour shown (ha): the bins of hours [ha, ha + off) are partial. */
  const binOff = (k, ha = 0) => (k > 1 && 24 % k === 0 && NH ? hodOf(ha) % k : 0);
  /** Hours [first, end) of bin b of k hours over the hours [ha, hb) shown. */
  const binHours = (b, k, off, ha = 0, hb = NH) => [Math.max(ha, ha + b * k - off), Math.min(hb, ha + (b + 1) * k - off)];
  function binRange(b, k, off = 0, ha = 0, hb = NH) {
    const [a, e] = binHours(b, k, off, ha, hb);
    const start = zoned(HOURS[a]);
    const end = zoned(new Date(parseTs(HOURS[e - 1]) + 3.6e6).toISOString());
    return start.slice(0, 10) + " " + start.slice(11, 16) + " – " +
      (end.slice(0, 10) === start.slice(0, 10) ? "" : end.slice(5, 10) + " ") + end.slice(11, 16) + " " + TZ;
  }
  // @hours-end
  // ---- frames: what a chart's x-axis covers, a range [a, b) in ms cut into nb bins. Bins of an
  // hour and more add up the payload's hourly counts (on the shown zone's clock); finer ones are
  // counted by the server (GET /api/sets/{id}/timeline?by_level=true), so only the web app has
  // them: the standalone file makes no requests.
  // @frames: FINE … fineTicks (tests/report/test_report_bins.py)
  const FINE = !!(ENTRIES_URL && LS.id);
  const isoOf = (ms) => new Date(ms).toISOString().replace(".000Z", "Z");
  /** Bins of k hours over the timeline hours [ha, hb). */
  function hourFrame(ha, hb, k) {
    const off = binOff(k, ha);
    return {
      kind: "hours", ha, hb, k, off, size: k * HOUR, nb: Math.max(1, Math.ceil((hb - ha + off) / k)),
      a: T0MS + ha * HOUR, b: T0MS + hb * HOUR,
      hours: (b) => binHours(b, k, off, ha, hb),
      binOfHour: (i) => Math.floor((i - ha + off) / k),
      start: (b) => T0MS + binHours(b, k, off, ha, hb)[0] * HOUR,
      end: (b) => T0MS + binHours(b, k, off, ha, hb)[1] * HOUR,
      pos: (ms) => ((ms - T0MS) / HOUR - ha + off) / k,  // the fractional bin of a time
      range: (b) => binRange(b, k, off, ha, hb),
    };
  }
  /** Bins of size ms (under an hour) over [a, b), aligned to the epoch as the server counts them. */
  function fineFrame(a, b, size) {
    const a0 = Math.floor(a / size) * size, b0 = Math.ceil(b / size) * size;
    const secs = size < 6e4 ? 19 : 16;
    return {
      kind: "fine", size, nb: Math.max(1, Math.round((b0 - a0) / size)), a: a0, b: b0,
      hours: null,
      start: (i) => a0 + i * size,
      end: (i) => a0 + (i + 1) * size,
      pos: (ms) => (ms - a0) / size,
      range: (i) => {
        const s = zoned(isoOf(a0 + i * size)), e = zoned(isoOf(a0 + (i + 1) * size));
        return s.slice(0, 10) + " " + s.slice(11, secs) + " – " +
          (e.slice(0, 10) === s.slice(0, 10) ? "" : e.slice(5, 10) + " ") + e.slice(11, secs) + " " + TZ;
      },
    };
  }
  /** A per-hour series added up into the bins of an hour frame. */
  function binHourly(data, f) {
    const out = new Float64Array(f.nb);
    for (let i = f.ha; i < f.hb; i++) if (data[i]) out[f.binOfHour(i)] += data[i];
    return out;
  }
  /** x-axis ticks of a fine frame: [x position in bins, label] on the shown zone's wall clock,
   *  the smallest step whose labels are >= 56 px apart; 24-hour times, the date at midnight. */
  function fineTicks(f, slot) {
    const steps = [1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, 24 * HOUR];
    const pxPerMs = slot / f.size;
    const step = steps.find((s) => s * pxPerMs >= 56) || 24 * HOUR;
    const shift = (ms) => (TZL ? -new Date(ms).getTimezoneOffset() * 6e4 : 0);
    const out = [];
    for (let w = Math.ceil((f.a + shift(f.a)) / step) * step; out.length < 400; w += step) {
      const ms = w - shift(w - shift(f.a));
      if (ms >= f.b) break;
      const z = zoned(isoOf(ms));
      const label = w % (24 * HOUR) === 0 ? z.slice(5, 10) : step < 6e4 ? z.slice(11, 19) : z.slice(11, 16);
      out.push([f.pos(ms), label]);
    }
    return out;
  }
  // @frames-end
  const rectPath = (x, top, w, bottom) => `M${r1(x)} ${r1(bottom)}V${r1(top)}H${r1(x + w)}V${r1(bottom)}Z`;
  function roundTopPath(x, top, w, bottom) {
    const r = Math.min(4, w / 2, bottom - top);
    if (r < 1) return rectPath(x, top, w, bottom);
    return `M${r1(x)} ${r1(bottom)}V${r1(top + r)}Q${r1(x)} ${r1(top)} ${r1(x + r)} ${r1(top)}` +
      `H${r1(x + w - r)}Q${r1(x + w)} ${r1(top)} ${r1(x + w)} ${r1(top + r)}V${r1(bottom)}Z`;
  }

  /** Columns over a frame (stacked when several series). Returns geometry. Without opts.frame the
   *  series are per-hour arrays over the whole timeline, binned here as wide as the chart allows;
   *  with it they come binned (series[].binned, overlay.binned, births as {n, lv} per bin). */
  function drawColumns(el, series, opts) {
    const W = Math.max(200, Math.floor(el.clientWidth || 600));
    const H = opts.height;
    // with an overlay, a lane under the axis shows it on its own scale (a small group's line would
    // hug the baseline); the line over the bars is drawn only when it rises above it
    const born = opts.births ? 10 : 0;
    // the lane's room is kept when no group is open, so opening one does not rescale the bars
    const lane = born + (opts.overlay || opts.ovLane ? 9 : 0);
    const small = H <= 100;  // the drawer's chart: two y steps, a slimmer axis
    const padL = 42, padR = 6, padB = (small ? 20 : 22) + lane;
    const plotW = W - padL - padR;
    const frame = opts.frame || hourFrame(0, NH, chooseBin(NH, plotW));
    const { k = 0, off = 0, nb } = frame;
    const binned = opts.frame ? series.map((s) => s.binned) : series.map((s) => binHourly(s.data, frame));
    const totals = new Float64Array(nb);
    for (const arr of binned) for (let b = 0; b < nb; b++) totals[b] += arr[b];
    let maxT = 0;
    for (let b = 0; b < nb; b++) if (totals[b] > maxT) maxT = totals[b];
    const nz = [...totals].filter((x) => x > 0).sort((x, y) => x - y);
    const p95 = nz.length ? nz[Math.floor(0.95 * (nz.length - 1))] : 0;
    const spiky = p95 > 0 && maxT > 4 * p95;
    const clip = spiky && CLIP[opts.clipKey || "tl"] !== false;
    // grave errors in the frame get a badge row above the plot
    const gv = opts.grave ? opts.grave.filter((g) => g.last >= frame.a && g.ms < frame.b) : [];
    const padT = (clip ? 18 : 8) + (gv.length ? 16 : 0);
    const plotH = H - padT - padB;
    const ticks = small ? niceTicks(clip ? p95 : maxT, 2, true) : niceTicks(clip ? p95 : maxT);
    const yMax = ticks[ticks.length - 1];
    const slot = plotW / nb;
    // thin bars (a finer bin than the automatic one) keep a hairline gap, then none
    const bw = slot >= 5 ? Math.min(24, Math.max(3, slot - GAP)) : Math.max(0.8, slot - (slot >= 3 ? 1 : 0.3));
    const bottom0 = padT + plotH;
    const paths = series.map(() => []);
    for (let b = 0; b < nb; b++) {
      if (!totals[b]) continue;
      const x = padL + b * slot + (slot - bw) / 2;
      let topIdx = -1;
      for (let s = 0; s < series.length; s++) if (binned[s][b]) topIdx = s;
      let y = bottom0, firstSeg = true;
      for (let s = 0; s < series.length; s++) {
        const v = binned[s][b];
        if (!v) continue;
        const hpx = (plotH * v) / yMax;
        const bottom = firstSeg ? y : y - GAP;
        if (bottom <= padT) break;
        let top = Math.max(padT, y - hpx);
        if (bottom - top < 1) top = bottom - 1;
        paths[s].push(s === topIdx ? roundTopPath(x, top, bw, bottom) : rectPath(x, top, bw, bottom));
        y -= hpx;
        firstSeg = false;
      }
    }
    const parts = [];
    const svgAt = parts.length;
    parts.push(`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label)}">`);
    if (series.some((s) => s.key === "OTHER")) {
      parts.push(`<defs><pattern id="hatch-other" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line class="hatch-line" x1="0" y1="0" x2="0" y2="4"/></pattern></defs>`);
    }
    for (const t of ticks) {
      const y = r1(bottom0 - (plotH * t) / yMax) + 0.5;
      if (t > 0) parts.push(`<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"/>`);
      // with the "new" lane under the axis its label takes the place of the 0
      if (t > 0 || !born) parts.push(`<text class="axis" x="${padL - 6}" y="${y + 4}" text-anchor="end">${compact(t)}</text>`);
    }
    const tick = (pos, label) => {
      const x = r1(padL + pos * slot) + 0.5;
      if (x < padL || x > W - padR - 16) return;
      parts.push(`<line class="base" x1="${x}" x2="${x}" y1="${bottom0 + lane}" y2="${bottom0 + lane + 4}"/>`);
      parts.push(`<text class="axis" x="${x}" y="${H - 5}" text-anchor="middle">${label}</text>`);
    };
    if (frame.kind === "hours") {
      // x ticks: smallest hour step whose labels are >= 56px apart
      const pxPerHour = slot / k;
      const step = [1, 2, 3, 6, 12, 24, 48, 72, 168, 336, 720].find((h) => h * pxPerHour >= 56) || 720;
      for (let i = frame.ha; i < frame.hb; i++) {
        const hod = hodOf(i);
        // a repeated hour (DST fall-back) gets no second tick
        const ok = step < 24 ? hod % step === 0 && (i === 0 || hodOf(i - 1) !== hod) : hod === 0 && newDay(i) && dayOf(i) % (step / 24) === 0;
        if (ok) tick((i - frame.ha + off) / k, hourLabel(i));
      }
    } else for (const [pos, label] of fineTicks(frame, slot)) tick(pos, label);
    parts.push(`<rect class="hover-band" x="0" y="${padT}" width="${r1(slot)}" height="${plotH}" visibility="hidden"/>`);
    for (let s = 0; s < series.length; s++) {
      if (paths[s].length) parts.push(`<path class="${series[s].cls}" d="${paths[s].join("")}"/>`);
    }
    if (clip) {
      // one label per run of adjacent clipped bins (the run's maximum), so labels never overlap
      let lastX = -Infinity;
      for (let b = 0; b < nb; b++) {
        if (totals[b] <= yMax) continue;
        let e = b, top = totals[b];
        while (e + 1 < nb && totals[e + 1] > yMax) top = Math.max(top, totals[++e]);
        const cx = r1(padL + ((b + e + 1) / 2) * slot);
        if (cx - lastX >= 40) {
          parts.push(`<text class="clip-lbl" x="${Math.min(cx, W - padR - 18)}" y="${padT - 5}" text-anchor="middle">↑ ${compact(top)}</text>`);
          lastX = cx;
        }
        b = e;
      }
    }
    parts.push(`<line class="base" x1="${padL}" x2="${W - padR}" y1="${bottom0 + 0.5}" y2="${bottom0 + 0.5}"/>`);
    let bornB = null, bornLv = null;
    if (opts.births) {
      // a dot per bin with new groups: area by count, color by the highest level among them
      if (opts.frame) ({ n: bornB, lv: bornLv } = opts.births);
      else {
        bornB = new Float64Array(nb);
        bornLv = new Int32Array(nb);
        for (let i = 0; i < NH; i++) {
          if (!opts.births.n[i]) continue;
          const b = frame.binOfHour(i);
          bornB[b] += opts.births.n[i];
          if (opts.births.lv[i] > bornLv[b]) bornLv[b] = opts.births.lv[i];
        }
      }
      let mx = 0;
      for (let b = 0; b < nb; b++) if (bornB[b] > mx) mx = bornB[b];
      const cy = bottom0 + 6, dots = [];
      for (let b = 0; b < nb; b++) {
        if (!bornB[b]) continue;
        const rad = Math.min(slot / 2 + 0.5, 1.5 + 2.5 * Math.sqrt(bornB[b] / mx));
        dots.push(`<circle class="s-${bucketOf(Math.max(10, bornLv[b]))}" cx="${r1(padL + (b + 0.5) * slot)}" cy="${cy}" r="${r1(Math.max(1.2, rad))}"/>`);
      }
      parts.push(`<text class="axis born-lbl" x="${padL - 6}" y="${cy + 3}" text-anchor="end">first</text><g class="born">${dots.join("")}</g>`);
    }
    let ovb = null;
    if (opts.overlay) {
      // a stepped line per bin (a run of empty bins sits on the baseline), clamped to a clipped axis
      ovb = opts.frame ? opts.overlay.binned : binHourly(opts.overlay.data, frame);
      let ovMax = 0;
      for (let b = 0; b < nb; b++) if (ovb[b] > ovMax) ovMax = ovb[b];
      const cells = [];
      for (let b = 0; b < nb; b++) {
        if (!ovb[b]) continue;
        const o = (0.2 + 0.8 * Math.sqrt(ovb[b] / ovMax)).toFixed(2);
        cells.push(`<rect x="${r1(padL + b * slot)}" y="${bottom0 + born + 2}" width="${r1(Math.max(1, slot - (slot > 3 ? 1 : 0)))}" height="6" fill-opacity="${o}"/>`);
      }
      parts.push(`<g class="ov-lane">${cells.join("")}</g>`);
      if (ovMax >= 0.06 * yMax) {
        const yOf = (v) => r1(bottom0 - (plotH * Math.min(v, yMax)) / yMax);
        // one stepped outline per run of bins with entries, down to the baseline at its ends:
        // no line along the baseline where the group is silent (it read as a second axis)
        let d = "";
        const base = r1(bottom0);
        for (let b = 0; b < nb; b++) {
          if (!ovb[b]) continue;
          d += `M${r1(padL + b * slot)} ${base}`;
          for (; b < nb && ovb[b]; b++) {
            const y = yOf(ovb[b]);
            d += `L${r1(padL + b * slot)} ${y}L${r1(padL + (b + 1) * slot)} ${y}`;
          }
          d += `L${r1(padL + b * slot)} ${base}`;
        }
        parts.push(`<path class="ov-halo" d="${d}"/><path class="ov-line" d="${d}"/>`);
        parts[svgAt] = parts[svgAt].replace("<svg ", `<svg class="has-ov" `);
      }
    }
    const badges = [];
    if (gv.length) {
      // a red line (and a band for a run of them) through the plot at each grave error
      const xs = (ms) => Math.max(padL, Math.min(padL + plotW, padL + frame.pos(ms) * slot));
      const marks = [];
      for (const g of gv) {
        const x0 = xs(g.ms), x1 = xs(g.last + 1);
        if (x1 - x0 > 2) marks.push(`<rect class="grave-band g-${g.kind}" x="${r1(x0)}" y="${padT}" width="${r1(x1 - x0)}" height="${r1(plotH)}"/>`);
        marks.push(`<line class="grave-line g-${g.kind}" x1="${r1(x0) + 0.5}" x2="${r1(x0) + 0.5}" y1="16" y2="${r1(bottom0)}"/>`);
        // markers whose badges would touch share one badge ("OOM ×3")
        const last = badges[badges.length - 1];
        if (last && x0 <= last.x0 + badgeW([...last.items, g], graveTop([...last.items, g])) + 4) last.items.push(g);
        else badges.push({ x0, items: [g] });
      }
      parts.push(`<g class="grave" aria-hidden="true">${marks.join("")}</g>`);
    }
    parts.push(`<rect class="brush" x="0" y="${padT}" width="0" height="${plotH}" visibility="hidden"/>`);
    parts.push(`<rect class="hit" x="${padL}" y="0" width="${plotW}" height="${H}"/>`);
    badges.forEach((c, i) => {
      const kind = graveTop(c.items), bwid = badgeW(c.items, kind);
      const bx = Math.max(padL - 6, Math.min(W - padR - bwid, c.x0));
      parts.push(`<g class="grave-badge g-${kind}" data-grave="${i}" data-x="${r1(c.x0)}" tabindex="0" role="button" aria-label="${esc(graveLabel(c.items))}">` +
        `<rect x="${r1(bx)}" y="1" width="${r1(bwid)}" height="15" rx="3"/><text x="${r1(bx + 6)}" y="12">${esc(badgeText(c.items, kind))}</text></g>`);
    });
    parts.push(`</svg>`);
    el.innerHTML = parts.join("");
    const svg = el.firstElementChild;
    if (badges.length) fitBadges(svg, padL - 6, W - padR);
    const geo = { svg, W, H, padL, padT, plotW, plotH, k, nb, slot, binned, totals, series, spiky, clip, ovb, bornB, bornLv, bottom0, born, top: !!opts.top, off, frame, graves: badges, ovName: opts.overlay ? opts.overlay.name : "" };
    geo.band = svg.querySelector(".hover-band");
    geo.brush = svg.querySelector(".brush");
    geo.binAt = (clientX) => {
      const x = clientX - svg.getBoundingClientRect().left - padL;
      return Math.max(0, Math.min(nb - 1, Math.floor(x / slot)));
    };
    attachHover(geo);
    showWindow(geo);
    return geo;
  }

  const tip = $("tooltip");
  function placeTip(e) {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 4) x = Math.max(4, e.clientX - pad - w);
    if (y + h > window.innerHeight - 4) y = Math.max(4, e.clientY - pad - h);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  window.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true, capture: true });
  document.addEventListener("pointerdown", () => { tip.hidden = true; });
  function attachHover(geo) {
    const hit = geo.svg.querySelector(".hit");
    hit.addEventListener("pointermove", (e) => {
      if (geo.dragging) return;
      const b = geo.binAt(e.clientX);
      geo.band.setAttribute("x", r1(geo.padL + b * geo.slot));
      geo.band.setAttribute("visibility", "visible");
      const rows = [];
      for (let s = geo.series.length - 1; s >= 0; s--) {
        const v = geo.binned[s][b];
        if (!v && geo.series.length > 1) continue;
        rows.push(`<div class="tt-r"><b>${fmt(v)}</b><i class="sw-${geo.series[s].key}"></i><span>${esc(geo.series[s].name)}</span></div>`);
      }
      if (geo.series.length > 1) rows.push(`<div class="tt-r tt-t"><b>${fmt(geo.totals[b])}</b><i></i><span>total</span></div>`);
      if (geo.ovb) rows.push(`<div class="tt-r tt-o"><b>${fmt(geo.ovb[b])}</b><i class="sw-sel"></i><span>${esc(geo.ovName)}</span></div>`);
      if (geo.top && geo.frame.hours) {
        // the groups of a bin are known per hour only: not for bins under an hour
        const [ha, he] = geo.frame.hours(b);
        const top = topIn(ha, he - 1, 3);
        if (top.length) {
          rows.push(`<div class="tt-top"><div class="tt-k">Largest groups</div>` + top.map(([r, c]) =>
            `<div class="tt-g"><b>${fmt(c)}</b><i class="sw-${r.bucket}"></i><span>${esc(r.tpl || r.sig)}</span></div>`).join("") + `</div>`);
        }
      }
      if (geo.bornB && geo.bornB[b]) {
        rows.push(`<div class="tt-r tt-o"><b>${fmt(geo.bornB[b])}</b><i class="sw-${bucketOf(geo.bornLv[b])}"></i><span>new group${geo.bornB[b] === 1 ? "" : "s"} first seen</span></div>` +
          `<div class="tt-note">Click the dot under the axis to list them</div>`);
      } else if (geo.top) {
        rows.push(`<div class="tt-note">${ZOOMSEL ? (S.z ? "Drag to zoom in further; − zooms out, Undo goes back" : "Drag to zoom into a time range") : S.t ? "Drag for another window; click to clear it" : "Drag to select a time window"}</div>`);
      }
      tip.innerHTML = `<div class="tt-h">${esc(geo.frame.range(b))}</div>${rows.join("")}`;
      if (tip.hidden && window.RagnalogTips) window.RagnalogTips.hide();  // one tooltip at a time
      tip.hidden = false;
      placeTip(e);
    });
    hit.addEventListener("pointerleave", () => {
      geo.band.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
  }
  function showWindow(geo, range) {
    const f = geo.frame;
    const w = range || (S.t ? [f.pos(T0MS + S.t[0] * HOUR), f.pos(T0MS + (S.t[1] + 1) * HOUR - 1)] : null);
    if (!w) {
      geo.brush.setAttribute("visibility", "hidden");
      return;
    }
    const x0 = geo.padL + Math.max(0, Math.floor(w[0])) * geo.slot;
    const x1 = geo.padL + Math.min(geo.nb, Math.floor(w[1]) + 1) * geo.slot;
    // a zoomed chart inside its window: shading all of it would say nothing
    if (x1 <= x0 || (!range && S.z && geo.top && x0 <= geo.padL && x1 >= geo.padL + geo.plotW - 0.5)) {
      geo.brush.setAttribute("visibility", "hidden");
      return;
    }
    geo.brush.setAttribute("x", r1(x0));
    geo.brush.setAttribute("width", r1(Math.max(1, x1 - x0)));
    geo.brush.setAttribute("visibility", "visible");
  }

  let TL = null;
  // per chart (timeline, drawer): null is automatic (clip when max > 4 × p95)
  const CLIP = { tl: null, d: null };
  const clipBtn = (geo) =>
    geo && geo.spiky
      ? `<button type="button" class="btn small toggle" data-clip aria-pressed="${geo.clip}" title="Clip the y-axis to the 95th percentile so spikes do not flatten the rest; clipped bars are labelled with their value">Clip spikes</button>`
      : "";
  const TL_TOTALS = {};
  let TLK = "";
  const tlKey = () => [...S.mute].sort().join(",") + "|" + [...S.hl].join(",") + "|" + TZ + "|" + overlayKey() + "|" + (S.z ? S.z.join("-") : "") + "|" + S.bin;
  /** Groups first seen per hour (signatures; the set's first hour left out: everything starts
   *  there) and the highest level among them: a deploy or an incident shows as a cluster of
   *  new groups. */
  const BIRTHS = (() => {
    const n = new Float64Array(NH), lv = new Int32Array(NH);
    for (const r of BASE) {
      if (r.gone || !r.first) continue;
      const h = Math.floor((parseTs(r.first) - T0MS) / 3.6e6);
      if (h >= 1 && h < NH) {
        n[h]++;
        if (r.level > lv[h]) lv[h] = r.level;
      }
    }
    return n.some((x) => x > 0) ? { n, lv } : null;
  })();
  /** What the timeline draws over its bars: the open group, else (with a search, logger,
   *  exception or has-exception filter) the groups the table lists, so a search shows when
   *  its matches happen. ``{key, name, rows}`` or null. */
  function overlaySpec() {
    const r = S.sig ? lookup(S.sig) : null;
    if (r && !r.gone && r.hourly && r.hourly.length && !isMuted(r)) return { key: "g:" + r.id, name: "open group", rows: [r] };
    if (!(S.q || S.lg || S.ex || S.hx)) return null;
    const rows = LIST.filter((x) => !x.gone && x.hourly.length && !isMuted(x));
    if (!rows.length || rows.length >= view(S.view).rows.length) return null;
    return { key: ["f", S.view, S.cmp, S.q, S.lg, S.ex, S.hx, [...S.lv].sort().join(","), S.t ? S.t.join("-") : "", rows.length].join("|"), name: `${fmt(rows.length)} listed group${rows.length === 1 ? "" : "s"}`, rows };
  }
  const overlayKey = () => { const o = overlaySpec(); return o ? o.key : ""; };
  const LV_TITLE = { ERROR: "ERROR and FATAL", DEBUG: "DEBUG and TRACE", OTHER: "Unknown level" };
  function renderLegend() {
    const shown = BUCKETS.filter((b) => (((D.timeline || {}).levels || {})[b] || []).some((v) => v > 0));
    let html = shown.map((b) => {
      const on = !S.hl.has(b);
      return `<button type="button" class="lg-item" data-hl="${b}" aria-pressed="${on}" data-tip-k="rpt-lg" aria-label="${LV_TITLE[b] || b}: ${on ? "hide it in" : "show it in"} the chart"><i class="sw sw-${b}"></i><span class="lg-n">${b}</span><span class="lg-c">${fmt(TL_TOTALS[b] || 0)}</span></button>`;
    }).join("");
    if (S.mute.size) {
      let n = 0;
      for (const s of S.mute) n += (BASE_BY.get(s) || { count: 0 }).count;
      html += `<span class="tl-muted" title="Muted groups are left out of the chart and the window totals; the table still lists them">${fmt(S.mute.size)} muted (${compact(n)} entries) <button type="button" class="btn small link" data-unmute>Unmute</button></span>`;
    }
    const clipped = (D.timeline || {}).clipped;
    if (clipped) {
      html += `<span class="tl-muted" title="Entries stamped far outside the set's main time span (a wrong clock, a corrupt line) are left out of the chart; the table counts them">${fmt(clipped)} outside the chart</span>`;
    }
    const ov = overlaySpec();
    if (ov) {
      const what = ov.rows.length === 1 && S.sig ? "The open group" : "The groups the filters list";
      html += `<span class="lg-sel" title="${what}: entries per bin as a line on the same scale (when it rises above the baseline) and as the shaded lane under the axis, darkest at its own busiest bin"><i class="sw-sel"></i>${esc(ov.name.charAt(0).toUpperCase() + ov.name.slice(1))}</span>`;
    }
    if (TL_NOTE) html += `<span class="tl-muted">${esc(TL_NOTE)}</span>`;
    if (GRAVE.length) {
      const by = new Map();
      for (const g of GRAVE) by.set(g.kind, (by.get(g.kind) || 0) + 1);
      const what = GRAVE_KINDS.filter((k) => by.has(k)).map((k) => `${GRAVE_TAG[k]} ${fmt(by.get(k))}×`).join(" · ");
      html = `<button type="button" class="grave-key" data-grave-first title="Grave errors in the set (out of memory, stack overflow, JVM errors, exhausted resources, FATAL): click to zoom to the first">⚠ ${esc(what)}</button>` + html;
    }
    $("tl-legend").innerHTML = html;
  }
  // ---- the top chart's frame and its counts
  /** The range the top chart shows: the zoom, else the whole timeline. */
  const viewRange = () => S.z || [T0MS, T1MS];
  /** Bins under an hour need the server; without a zoom a timeline of a day or more keeps
   *  hourly bins at least (a page opens without waiting for a request). */
  const autoFloor = (span) => (!FINE ? HOUR : !S.z && span >= 24 * HOUR ? HOUR : 1e3);
  let BINS = { auto: HOUR, choices: [], size: HOUR, span: 0 };
  /** The bin size of the top chart over plotW px: the viewer's pick when it fits, else automatic. */
  function chooseFrameBin(plotW) {
    const [a, b] = viewRange(), span = b - a;
    const auto = autoBin(span, plotW, autoFloor(span));
    const choices = binChoices(span, plotW, FINE ? 1e3 : HOUR);
    const want = S.bin ? BIN_MS[S.bin] : 0;
    BINS = { auto, choices, span, size: want && choices.includes(want) ? want : auto };
    return BINS.size;
  }
  /** Groups first seen in each bin of a frame (the set's first hour left out, as BIRTHS). */
  function birthsIn(f) {
    if (f.kind === "hours") {
      if (!BIRTHS) return null;
      return { n: binHourly(BIRTHS.n, f), lv: (() => {
        const lv = new Int32Array(f.nb);
        for (let i = f.ha; i < f.hb; i++) if (BIRTHS.n[i] && BIRTHS.lv[i] > lv[f.binOfHour(i)]) lv[f.binOfHour(i)] = BIRTHS.lv[i];
        return lv;
      })() };
    }
    const n = new Float64Array(f.nb), lv = new Int32Array(f.nb);
    let any = false;
    for (const r of BASE) {
      if (r.gone || !r.first) continue;
      const ms = parseTs(r.first);
      if (!(ms >= f.a && ms < f.b) || ms < T0MS + HOUR) continue;
      const i = Math.floor((ms - f.a) / f.size);
      n[i]++;
      if (r.level > lv[i]) lv[i] = r.level;
      any = true;
    }
    return any ? { n, lv } : null;
  }
  /** The API filter of the overlay's group (the open group: a signature or a coarse signature);
   *  null when the overlay is several groups (filters, a merged row): bins under an hour cannot
   *  draw it. */
  function fineOverlayQuery(ov) {
    if (!ov || !S.sig || !ov.key.startsWith("g:")) return null;
    const r = ov.rows[0];
    if (r.kind === "sig") return "signature=" + encodeURIComponent(r.sig);
    if (r.kind === "coarse") return "coarse_signature=" + encodeURIComponent(r.coarse);
    return null;
  }
  const MAX_FINE_MUTED = 40;
  const FINE_CACHE = new Map();
  /** Counts of a fine frame from the server: {levels, ov, mutedLeft} once there, {err} when the
   *  request failed, undefined while it runs (the chart redraws when it is done). */
  function fineData(f, ovQuery) {
    const muted = [...S.mute].filter((s) => BASE_BY.has(s) && !BASE_BY.get(s).gone).sort();
    const key = [f.a, f.b, f.size, muted.join(","), ovQuery || ""].join("|");
    const hit = FINE_CACHE.get(key);
    if (hit) return hit.value;
    const entry = { value: undefined };
    FINE_CACHE.set(key, entry);
    if (FINE_CACHE.size > 40) FINE_CACHE.delete(FINE_CACHE.keys().next().value);
    const base = `/api/sets/${encodeURIComponent(LS.id)}/timeline?bucket=${binKey(f.size)}` +
      `&since=${encodeURIComponent(isoOf(f.a))}&until=${encodeURIComponent(isoOf(f.b))}`;
    const get = (q) => fetch(base + q, { headers: { Accept: "application/json" } }).then((res) =>
      res.ok ? res.json() : res.json().catch(() => ({})).then((d) => { throw new Error((d.error && d.error.message) || "HTTP " + res.status); }));
    const sub = muted.slice(0, MAX_FINE_MUTED);
    Promise.all([get("&by_level=true"), ovQuery ? get("&" + ovQuery) : null, ...sub.map((s) => get("&signature=" + encodeURIComponent(s)))])
      .then(([all, ov, ...mutes]) => {
        const at = (it) => Math.round((parseTs(it.bucket) - f.a) / f.size);
        const levels = {};
        for (const b of BUCKETS) levels[b] = new Float64Array(f.nb);
        for (const it of all.items || []) {
          const i = at(it);
          if (i >= 0 && i < f.nb) for (const b of BUCKETS) levels[b][i] += (it.levels || {})[b] || 0;
        }
        mutes.forEach((d, j) => {
          const arr = levels[BASE_BY.get(sub[j]).bucket];
          for (const it of d.items || []) { const i = at(it); if (i >= 0 && i < f.nb) arr[i] = Math.max(0, arr[i] - it.count); }
        });
        let ovb = null;
        if (ov) {
          ovb = new Float64Array(f.nb);
          for (const it of ov.items || []) { const i = at(it); if (i >= 0 && i < f.nb) ovb[i] += it.count; }
        }
        entry.value = { levels, ov: ovb, mutedLeft: muted.length - sub.length };
      })
      .catch((err) => { entry.value = { err: String(err && err.message ? err.message : err) }; })
      .finally(() => { if (TL_WANT === key) renderTimeline(); });
    return undefined;
  }
  let TL_WANT = "";
  let TL_NOTE = "";
  function renderTimeline() {
    const el = $("timeline");
    TLK = tlKey();
    if (!NH) {
      el.innerHTML = `<div class="empty">No entries.</div>`;
      $("tl-legend").innerHTML = "";
      return;
    }
    const levels = levelData();
    for (const b of BUCKETS) {
      let t = 0;
      for (const v of levels[b] || []) t += v;
      TL_TOTALS[b] = t;
    }
    const shown = BUCKETS.filter((b) => TL_TOTALS[b] > 0 && !S.hl.has(b));
    const ov = overlaySpec();
    const W = Math.max(200, Math.floor(el.clientWidth || 600));
    const size = chooseFrameBin(W - 48);  // the plot width of drawColumns (padL 42, padR 6)
    const [za, zb] = viewRange();
    let frame = null, series, overlay = null, note = "";
    if (size < HOUR) {
      frame = fineFrame(za, zb, size);
      const q = fineOverlayQuery(ov);
      TL_WANT = [frame.a, frame.b, frame.size, [...S.mute].filter((s) => BASE_BY.has(s) && !BASE_BY.get(s).gone).sort().join(","), q || ""].join("|");
      const data = fineData(frame, q);
      if (data === undefined) {
        // keep the chart drawn so far, faded, until the counts are in
        el.classList.add("loading");
        if (!TL || !el.firstElementChild) el.innerHTML = `<div class="empty">Loading entries per ${esc(binName(size))}…</div>`;
        $("tl-bin").textContent = binName(size);
        renderBins();
        return;
      }
      if (data.err) {
        note = `Entries per ${binName(size)} could not be loaded (${data.err}); showing hours`;
        frame = null;
      } else {
        series = shown.map((b) => ({ key: b, name: b, cls: "s-" + b, binned: data.levels[b] }));
        if (ov && data.ov) overlay = { binned: data.ov, name: ov.name };
        else if (ov) note = `${ov.name.charAt(0).toUpperCase() + ov.name.slice(1)}: drawn at bins of an hour or more`;
        if (data.mutedLeft) note = `${fmt(data.mutedLeft)} muted groups are still counted at bins under an hour`;
      }
    }
    TL_WANT = frame ? TL_WANT : "";
    if (!frame) {
      const ha = Math.max(0, Math.floor((za - T0MS) / HOUR)), hb = Math.min(NH, Math.max(Math.ceil((zb - T0MS) / HOUR), Math.floor((za - T0MS) / HOUR) + 1));
      frame = hourFrame(ha, hb, Math.max(1, Math.round(Math.max(size, HOUR) / HOUR)));
      series = shown.map((b) => ({ key: b, name: b, cls: "s-" + b, binned: binHourly(levels[b], frame) }));
      if (ov) {
        const data = new Float64Array(NH);
        for (const row of ov.rows) for (const [i, n] of row.hourly) if (i >= 0 && i < NH) data[i] += n;
        overlay = { binned: binHourly(data, frame), name: ov.name };
      }
    }
    TL_NOTE = note;
    renderLegend();
    el.classList.remove("loading");
    TL = drawColumns(el, series, { frame, height: 120, label: `Entries per ${binName(frame.size)} by level`, overlay, births: birthsIn(frame), top: true, ovLane: true, grave: GRAVE });
    for (const b of TL.svg.querySelectorAll("[data-grave]")) {
      const items = TL.graves[+b.dataset.grave].items;
      b.addEventListener("pointermove", (e) => { tip.innerHTML = graveTip(items); tip.hidden = false; placeTip(e); });
      b.addEventListener("pointerleave", () => { tip.hidden = true; });
      const go = () => openGrave(items[0]);
      b.addEventListener("click", go);
      b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    }
    $("tl-bin").textContent = binName(frame.size);
    el.title = ZOOMSEL ? (S.z ? "Drag across the chart to zoom in further" : "Drag across the chart to zoom into a time range") : "Drag across the chart to select a time window; click to clear it";
    $("tl-clip").innerHTML = clipBtn(TL);
    renderBins();
    const hit = TL.svg.querySelector(".hit");
    const geo = TL;
    hit.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      geo.dragging = { b0: geo.binAt(e.clientX), x0: e.clientX, b1: geo.binAt(e.clientX), moved: false };
      hit.setPointerCapture(e.pointerId);
      tip.hidden = true;
      geo.band.setAttribute("visibility", "hidden");
    });
    hit.addEventListener("pointermove", (e) => {
      const d = geo.dragging;
      if (!d) return;
      d.b1 = geo.binAt(e.clientX);
      if (Math.abs(e.clientX - d.x0) > 3) d.moved = true;
      if (d.moved) showWindow(geo, [Math.min(d.b0, d.b1), Math.max(d.b0, d.b1)]);
    });
    const end = (e) => {
      const d = geo.dragging;
      if (!d) return;
      geo.dragging = null;
      if (e.type === "pointercancel") return showWindow(geo);
      const f = geo.frame;
      if (!d.moved) {
        const y = e.clientY - geo.svg.getBoundingClientRect().top;
        if (geo.bornB && geo.bornB[d.b0] && y > geo.bottom0 && y <= geo.bottom0 + geo.born + 2) {
          commit({ t: hoursCovering(f.start(d.b0), f.end(d.b0)), sort: "first_seen" });
          return;
        }
        if (S.t && !S.z) commit({ t: null });
        return;
      }
      const a = f.start(Math.min(d.b0, d.b1)), b = f.end(Math.max(d.b0, d.b1));
      // zoom on: the selection is the zoom (the window follows it, in whole hours); off: the window
      if (ZOOMSEL) zoomTo([a, b]);
      else commit({ t: hoursCovering(a, b) });
    };
    hit.addEventListener("pointerup", end);
    hit.addEventListener("pointercancel", end);
  }
  /** Zoom the top chart into [a, b) (null: the whole timeline); the window becomes its hours. */
  function zoomTo(range) {
    const z = clampZoom(range);
    if (!z) return commit({ z: null }, { keepPage: true });
    commit({ z, t: hoursCovering(z[0], z[1]) });
  }
  /** + : into the window, or (zoomed) into the middle third. */
  function zoomIn() {
    if (S.z) {
      const [a, b] = S.z, w = (b - a) / 3;
      if (w >= 1e4) zoomTo([a + w, b - w]);
    } else if (S.t) zoomTo([T0MS + S.t[0] * HOUR, T0MS + (S.t[1] + 1) * HOUR]);
    else toast("Select a time window on the chart first");
  }
  /** − : three times as wide around the middle, kept inside the timeline. */
  function zoomOut() {
    if (!S.z) return;
    const [a, b] = S.z, w = (b - a) * 3;
    let na = (a + b) / 2 - w / 2;
    na = Math.max(T0MS, Math.min(T1MS - w, na));
    zoomTo([na, na + w]);
  }
  /** The zoom toggle: off shows the whole set (the window stays) and drags select windows; on
   *  zooms into the window, if one is set, and drags zoom. */
  function toggleZoomSel() {
    ZOOMSEL = !ZOOMSEL;
    try { localStorage.setItem(ZS_KEY, ZOOMSEL ? "1" : "0"); } catch { /* storage unavailable */ }
    if (!ZOOMSEL && S.z) commit({ z: null }, { keepPage: true });
    else if (ZOOMSEL && S.t && !S.z) zoomTo([T0MS + S.t[0] * HOUR, T0MS + (S.t[1] + 1) * HOUR]);
    else { renderZoomCtl(); renderTimeline(); }
    toast(ZOOMSEL ? "Zoom on: a drag zooms into the selection" : "Zoom off: a drag selects a time window");
  }
  /** Undo: the chart's previous window, zoom and bin (Clear included). */
  function undo() {
    const v = UNDO.pop();
    if (!v) return toast("Nothing to undo");
    commit({ t: v.t, z: v.z, bin: v.bin }, { noUndo: true, keepPage: true });
  }
  const MAG = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4 14 14M5 7h4M7 5v4"/></svg>`;
  const UNDO_SVG = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5.5 3 2.5 6l3 3"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H7"/></svg>`;
  /** Undo and the zoom toggle, at the head of the chart. */
  function renderZoomCtl() {
    const el = $("tl-zoom");
    if (!el) return;
    if (!NH) { el.innerHTML = ""; return; }
    el.innerHTML = `<button type="button" class="btn small zbtn z-undo" data-undo ${UNDO.length ? "" : "disabled"} title="Undo: back to the previous zoom, window and bins, also after Clear (u)">${UNDO_SVG}<span>Undo</span></button>` +
      `<button type="button" class="btn small zbtn z-toggle" data-zoomsel aria-pressed="${ZOOMSEL}" title="${ZOOMSEL ? "Zoom is on: a drag on the chart zooms into the selection, with finer bins. Click: the whole set again, and drags select a time window" : "Zoom is off: a drag selects a time window. Click: zoom into the window, and drags zoom"}">${MAG}<span>Zoom</span></button>`;
  }
  const graveLabel = (items) => items.length === 1
    ? `${GRAVE_LABEL[items[0].kind]} at ${tsShort(items[0].ts)} ${TZ}${items[0].count > 1 ? `, ${fmt(items[0].count)} entries` : ""}`
    : `${items.length} grave errors from ${tsShort(items[0].ts)} ${TZ}`;
  function graveTip(items) {
    const rows = items.slice(0, 6).map((g) => `<div class="tt-g grave-row"><b class="g-${g.kind}">${esc(GRAVE_TAG[g.kind])}</b><span>` +
      `${esc(tsShort(g.ts))}${g.count > 1 ? ` – ${esc(tsShort(g.last_ts).slice(6))} · ${fmt(g.count)} entries` : ""}<br>${esc(g.text)}</span></div>`).join("");
    return `<div class="tt-h grave-h">⚠ ${esc(items.length === 1 ? GRAVE_LABEL[items[0].kind] : `${items.length} grave errors`)} <span class="muted">${TZ}</span></div>${rows}` +
      (items.length > 6 ? `<div class="tt-note">+${fmt(items.length - 6)} more</div>` : "") +
      `<div class="tt-note">Click: open its group</div>`;
  }
  /** A grave error: its group in the drawer (its line on the chart). */
  function openGrave(g) {
    if (lookup(g.sig)) open(g.sig);
    else toast("Its group is not in this report (max groups)");
  }
  /** The bins menu: automatic, or a finer or coarser bin the chart's width allows. */
  function renderBins() {
    const el = $("tl-bins");
    if (!el) return;
    if (!NH || BINS.choices.length < 2) {
      el.innerHTML = "";
      return;
    }
    const cur = S.bin && BINS.choices.includes(BIN_MS[S.bin]) ? S.bin : "";
    const opts = [`<option value=""${cur ? "" : " selected"}>Auto (${esc(binShort(BINS.auto))})</option>`]
      .concat(BINS.choices.map((ms) => `<option value="${binKey(ms)}"${binKey(ms) === cur ? " selected" : ""}>${esc(binShort(ms))}</option>`));
    const finer = FINE ? "" : " Bins under an hour need the web app (ragnalog serve).";
    el.innerHTML = `<label class="sel" title="Bin size of the chart: Auto fits the shown range; pick a finer one to see more detail.${finer}"><span>Bins</span><select id="tl-binsel" aria-label="Bin size">${opts.join("")}</select></label>`;
  }
  /** Quick windows ending at the last hour of the set: those shorter than the set. */
  const PRESETS = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"]].filter(([h]) => h < NH);
  const hoursText = (h) => (h < 48 ? h + " h" : (h / 24).toFixed(1).replace(/\.0$/, "") + " d");
  /** Move the window by its own width (dir -1 / +1), clamped to the set; zoomed, move the zoom
   *  by its width (the window follows). */
  function shiftWindow(dir) {
    if (S.z) {
      const [a, b] = S.z, w = b - a;
      const na = Math.max(T0MS, Math.min(T1MS - w, a + dir * w));
      if (na !== a) zoomTo([na, na + w]);
      return;
    }
    if (!S.t) return;
    const [a, b] = S.t, w = b - a + 1;
    const na = Math.max(0, Math.min(NH - w, a + dir * w));
    if (na !== a) commit({ t: [na, na + w - 1] }, { keepPage: false });
  }
  /** "15 min", "40 s", "2.5 h", "3 d": the length of a zoom. */
  const spanText = (ms) => (ms < 6e4 ? Math.round(ms / 1e3) + " s" : ms < HOUR ? Math.round(ms / 6e4) + " min"
    : ms < 48 * HOUR ? (ms / HOUR).toFixed(1).replace(/\.0$/, "") + " h" : (ms / 864e5).toFixed(1).replace(/\.0$/, "") + " d");
  /** "2026-10-29 14:05 → 14:20" in the shown zone; seconds when the ends are not whole minutes. */
  function zoomText(a, b) {
    const n = a % 6e4 || b % 6e4 ? 19 : 16;
    const s = zoned(isoOf(a)), e = zoned(isoOf(b));
    return s.slice(0, 10) + " " + s.slice(11, n) + " → " + (e.slice(0, 10) === s.slice(0, 10) ? "" : e.slice(5, 10) + " ") + e.slice(11, n);
  }
  function renderWindowLabel() {
    const el = $("tl-window");
    if (!NH) {
      el.innerHTML = "";
      return;
    }
    const presets = PRESETS.map(([h, label]) => {
      const on = S.t && S.t[0] === NH - h && S.t[1] === NH - 1 && (!S.z || (S.z[0] === T0MS + (NH - h) * HOUR && S.z[1] === T1MS));
      return `<button type="button" data-win="${h}" aria-pressed="${!!on}" title="The last ${label} of the set">${label}</button>`;
    }).join("");
    const seg = presets ? `<span class="seg win-seg" role="group" aria-label="Quick windows"><span class="lbl">Last</span>${presets}</span>` : "";
    if (!S.t) {
      el.innerHTML = seg;
      return;
    }
    const [a, b] = S.t;
    const end = new Date(parseTs(HOURS[b]) + 3.6e6).toISOString();
    // the window's entries, errors and warnings are on the tiles and in the range's tooltip
    const what = [];
    if (BIRTHS) {
      let born = 0;
      for (let h = a; h <= b; h++) born += BIRTHS.n[h];
      if (born) {
        what.push(S.sort === "first_seen"
          ? `<span class="muted">${fmt(born)} first seen here (listed first)</span>`
          : `<button type="button" class="btn small link" data-sort-first title="Sort by first seen: the groups that started in the window come first">${fmt(born)} first seen here</button>`);
      }
    }
    const win = `<span class="win-what" data-tip-k="rpt-win"><span class="win-k">Window</span> <b>${esc(ts16(HOURS[a]))} → ${esc(tsShort(end))}</b> <span class="muted">${TZ} · ${hoursText(b - a + 1)}</span></span>`;
    if (S.z) {
      // zoomed: the zoom leads, the window (the table's hours around it) follows it
      const [za, zb] = S.z;
      const tableNote = `The table counts whole hours: the window around the zoom, ${ts16(HOURS[a])} → ${tsShort(end)} ${TZ}`;
      el.innerHTML = `<span class="win-what zoom-what" title="${esc(tableNote)}"><span class="win-k">Zoom</span> <b>${esc(zoomText(za, zb))}</b> <span class="muted">${TZ} · ${spanText(zb - za)}</span></span>` +
        `<span class="win-nav"><button type="button" class="btn small" data-shift="-1" ${za <= T0MS ? "disabled" : ""} title="Earlier, by the zoom's width ([)" aria-label="Earlier">‹</button>` +
        `<button type="button" class="btn small" data-shift="1" ${zb >= T1MS ? "disabled" : ""} title="Later, by the zoom's width (])" aria-label="Later">›</button></span>` +
        `<span class="win-nav"><button type="button" class="btn small" data-zoom="in" ${zb - za < 3e4 ? "disabled" : ""} title="Zoom into the middle third (+)" aria-label="Zoom in">+</button>` +
        `<button type="button" class="btn small" data-zoom="out" title="Zoom out to three times the width (−)" aria-label="Zoom out">−</button></span>` +
        `<button type="button" class="btn small" data-zoom="reset" title="Show the whole set again; the window stays">Reset zoom</button>` +
        what.join("") + `<button type="button" class="btn small" data-win="0" title="Show the whole set">Clear</button>`;
      return;
    }
    el.innerHTML = win + what.join("") +
      `<span class="win-nav"><button type="button" class="btn small" data-shift="-1" ${a <= 0 ? "disabled" : ""} title="Previous window of the same length ([)" aria-label="Previous window">‹</button>` +
      `<button type="button" class="btn small" data-shift="1" ${b >= NH - 1 ? "disabled" : ""} title="Next window of the same length (])" aria-label="Next window">›</button></span>` +
      seg + `<button type="button" class="btn small" data-win="0" title="Show the whole set">Clear</button>`;
  }

  function spark(r) {
    if (!NH || !r.hourly.length) return `<span class="muted">—</span>`;
    // whole hours per bin (k), so a steady rate draws flat: 174 h over 50 bins of 3 or 4 h
    // each drew a sawtooth
    const W = 100, H = 22, k = Math.ceil(NH / 50), nb = Math.ceil(NH / k), bw = W / nb;
    const bins = new Float64Array(nb);
    let max = 0;
    for (const [i, n] of r.hourly) {
      const b = Math.floor(i / k);
      bins[b] += n;
      if (bins[b] > max) max = bins[b];
    }
    // with a window: its bins drawn in full, the rest muted
    const wb = S.t ? [Math.floor(S.t[0] / k), Math.floor(S.t[1] / k)] : null;
    let d = "", o = "";
    const w = bw >= 3 ? bw - 1 : bw;
    for (let b = 0; b < nb; b++) {
      if (!bins[b]) continue;
      const h = Math.max(1.5, ((H - 2) * bins[b]) / max);
      const p = `M${r1(b * bw)} ${H}v-${r1(h)}h${r1(w)}v${r1(h)}Z`;
      if (wb && (b < wb[0] || b > wb[1])) o += p;
      else d += p;
    }
    let win = "";
    if (wb) win = `<rect class="spark-win" x="${r1(wb[0] * bw)}" y="0" width="${r1((wb[1] + 1 - wb[0]) * bw)}" height="${H}"/>`;
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${win}` +
      (o ? `<path class="out" d="${o}"/>` : "") + `<path d="${d}"/></svg>`;
  }

  // ---------------------------------------------------------------- table
  function deltaChip(c) {
    if (!c) return "";
    let k, t;
    if (c.cls === "new") [k, t] = ["new", "new"];
    else if (c.cls === "vanished") [k, t] = ["gone", "gone"];
    else if (c.cls === "unchanged" || c.a === c.b) [k, t] = ["same", "="];
    else {
      const r = c.b / c.a;
      [k, t] = r > 1 ? ["up", "↑ ×" + ratioText(r)] : ["down", "↓ ×" + ratioText(r)];
    }
    return `<span class="dchip d-${k}"><i></i>${t}</span>`;
  }
  const abText = (c) =>
    c.cls === "new" ? fmt(c.b) : c.cls === "vanished" ? fmt(c.a) : `${fmt(c.a)} → ${fmt(c.b)}`;
  /** The table's A → B: counts from 10,000 up compact (369K → 624K), so the chip still fits. */
  const abShort = (c) => {
    const f = (n) => (n >= 1e4 ? compact(n) : fmt(n));
    return c.cls === "new" ? f(c.b) : c.cls === "vanished" ? f(c.a) : `${f(c.a)} → ${f(c.b)}`;
  };
  const OTHER_SPAN = OTHER && OTHER.first_ts ? duration(OTHER.first_ts, OTHER.last_ts) : "";
  const MOSTLY_NEW = !!CMPD && CMPN.new > 0.8 * Math.max(1, (D.groups || []).length + CMPN.vanished) && !!OTHER_SPAN;
  const baselineNote = () =>
    MOSTLY_NEW
      ? `<span class="b-note" title="Most groups are new: the baseline ${esc(OTHERNAME)} may simply cover a shorter or different period">baseline covers ${esc(OTHER_SPAN)}</span>`
      : "";
  const COLS = [
    ["c-count", "Count", "count"],
    ["c-lv", "Level", "level"],
    ["c-logger", "Logger", "logger"],
    ["c-tpl", "Template", "template"],
    ["c-exc", "Exception", null],
    ["c-spark", "Per hour", null],
    ["c-last", "Last seen", "last_seen"],
    ["c-ut", "Users / threads", "users", "Distinct users / distinct threads"],
  ];
  if (CMP) COLS.push(["c-delta", "vs " + OTHERNAME, "delta"]);

  const WIN_COLS = {
    "c-count": ["In window", "Entries in the time window; below: the group's total"],
    "c-last": ["Last seen", "Last entry in the time window (\"xx\": within that hour)"],
    "c-delta": [null, "Whole sets, not the time window"],
  };
  function renderHead() {
    $("t-head").innerHTML = "<tr>" + COLS.map(([c, label0, key, tip0]) => {
      const wc = S.t ? WIN_COLS[c] : null;
      const label = (wc && wc[0]) || label0;
      // a tooltip only where it adds something: how a column counts, or a label cut short
      const tip = wc ? (c === "c-delta" ? `vs ${OTHERNAME}: ${wc[1]}` : wc[1]) : tip0 || (c === "c-delta" ? label : "");
      const sorted = !key ? "" : ` aria-sort="${S.sort === key ? (key === "template" || key === "logger" || key === "first_seen" ? "ascending" : "descending") : "none"}"`;
      return `<th class="${c}${key ? " sortable" : ""}"${key ? ` data-sort="${key}"` : ""}${sorted}${tip ? ` title="${esc(tip)}"` : ""}>${key ? `<button type="button" class="th-sort" data-sort="${key}">${esc(label)}</button>` : esc(label)}</th>`;
    }).join("") + "</tr>";
  }
  /** ``html`` (escaped text and tags) with every match of the search text marked. */
  const UNESC = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };
  function markHtml(html, text) {
    if (!text) return html;
    const needle = text.toLowerCase();
    return html.split(/(<[^>]*>)/).map((part) => {
      if (part.startsWith("<")) return part;
      // match on the text itself, not on its escaped form ("lt" must not split "&lt;")
      const raw = part.replace(/&(amp|lt|gt|quot|#39);/g, (_, n) => UNESC[n]);
      const low = raw.toLowerCase();
      let out = "", i = 0, j;
      while ((j = low.indexOf(needle, i)) >= 0) {
        out += esc(raw.slice(i, j)) + "<mark>" + esc(raw.slice(j, j + needle.length)) + "</mark>";
        i = j + needle.length;
      }
      return out + esc(raw.slice(i));
    }).join("");
  }
  function rowHtml(r, v) {
    const n = cnt(r);
    const lmin = Math.log10(SCALE.min), span = Math.log10(SCALE.max) - lmin;
    const w = n > 0 ? Math.max(2, span > 0 ? ((Math.log10(n) - lmin) / span) * 100 : 100) : 0;
    const track = v.max >= 1000 ? `<span class="track"><i style="width:${r1(w)}%"></i></span>` : "";
    const cls = (r.id === S.sig ? "is-sel " : "") + (r.id === CUR ? "is-cur " : "") + (r.gone ? "is-gone " : "") + (isMuted(r) ? "is-muted" : "");
    const multi = r.members ? `<span class="badge-m" title="${r.members.length} signatures">${r.members.length}×</span>` : "";
    const of = S.sort === "spike" && spikeOf(r) > 0
      ? `<small class="of spk">×${esc(compact(spikeOf(r)))}</small>`
      : S.t ? `<small class="of"> / ${esc(compact(r.count))}</small>` : "";
    const last = S.t ? winOf(r).last : r.last ? { iso: r.last } : null;
    // every cell's detail (full names, exact times, breakdowns) is a tooltip built on hover
    // from the row: see registerTips
    return `<tr data-id="${esc(r.id)}" class="${cls}" tabindex="-1">` +
      `<td class="c-count" data-tip-k="rpt-count"><div class="cnt"><span class="num">${fmt(n)}${of}</span>${track}</div></td>` +
      `<td class="c-lv">${badge(r.lvl, r.level)}</td>` +
      `<td class="c-logger" data-tip-k="rpt-logger">${loggerHtml(r.logger, true)}</td>` +
      `<td class="c-tpl" data-tip-k="rpt-group"><span class="tpl">${multi}${graveKind(r) ? `<span class="grave-pill g-${graveKind(r)}" title="${esc(GRAVE_LABEL[graveKind(r)])}: this group holds grave errors (a red marker on the chart)">⚠ ${GRAVE_TAG[graveKind(r)]}</span>` : ""}${isMuted(r) ? `<span class="badge-mute" title="Left out of the chart (m)">muted</span>` : ""}${markHtml(tplHtml(r.tpl), Q.text)}</span><span class="sub">${markHtml(loggerHtml(r.logger, true), Q.text)}${r.exc ? " · " + markHtml(excHtml(r.exc, true), Q.text) : ""}</span></td>` +
      `<td class="c-exc"${r.exc ? ` data-tip-k="rpt-exc"` : ""}>${r.exc ? excHtml(r.exc) : nilHtml()}</td>` +
      `<td class="c-spark"${r.hourly.length ? ` data-tip-k="rpt-spark"` : ""}>${spark(r)}</td>` +
      `<td class="c-last"${last ? ` data-tip-k="rpt-seen"` : ""}>${last ? tsHtml(seenText(last)) : nilHtml()}</td>` +
      `<td class="c-ut"${r.gone ? "" : ` data-tip-k="rpt-ut"`}>${r.gone ? "" : `${fmt(r.users)}<span class="sl">/</span>${fmt(r.threads)}`}</td>` +
      (CMP ? `<td class="c-delta" data-tip-k="rpt-delta"><span class="ab">${abShort(r.cmp)}</span>${deltaChip(r.cmp)}</td>` : "") +
      `</tr>`;
  }
  function pagerHtml(pages) {
    if (pages <= 1) return "";
    return `<button type="button" class="btn small" data-page="${S.p - 1}" data-dir="prev" ${S.p <= 0 ? "disabled" : ""}>‹ Prev</button>` +
      `<span>Page ${S.p + 1} of ${pages}</span>` +
      `<button type="button" class="btn small" data-page="${S.p + 1}" data-dir="next" ${S.p >= pages - 1 ? "disabled" : ""}>Next ›</button>`;
  }
  function renderTable(v) {
    const pages = Math.max(1, Math.ceil(LIST.length / PAGE));
    if (S.p >= pages) S.p = pages - 1;
    const start = S.p * PAGE;
    const page = LIST.slice(start, start + PAGE);
    renderHead();
    $("t-body").innerHTML = page.map((r) => rowHtml(r, v)).join("");
    const empty = $("t-empty");
    empty.hidden = page.length > 0;
    $("t-head").hidden = !page.length;
    $("groups").hidden = !page.length;  // no empty frame above the empty-state panel
    $("groups").classList.toggle("no-track", v.max < 1000);  // counts only: a narrower column
    $("groups").classList.toggle("in-win", !!S.t); // "N / total" on one line: a wider column
    if (!page.length) {
      if (!v.rows.length) empty.textContent = "This report has no groups.";
      else if (S.cmp === "vanished" && S.t) {
        empty.innerHTML = `<div>Vanished groups have no entries in this set, so none falls in a time window.</div>` +
          `<button type="button" class="btn" data-win="0">Show the whole set</button>`;
      } else {
        const chips = activeFilters().map(([k, label]) =>
          `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
        empty.innerHTML = `<div>No group matches all of these filters. Remove one to see more.</div>` +
          (chips ? `<div class="fchips">${chips}</div>` : "") +
          `<button type="button" class="btn" data-clear="all">Remove all filters</button>`;
      }
    }
    let entries = 0;
    for (const r of LIST) entries += cnt(r);
    const shown = LIST.length ? `${fmt(start + 1)}–${fmt(start + page.length)} of ` : "";
    const noun = S.view === "sig" ? "groups" : "rows";
    $("t-summary").textContent = `${shown}${fmt(LIST.length)} ${noun}` + (LIST.length < v.rows.length ? ` (of ${fmt(v.rows.length)})` : "") +
      ` · ${fmt(entries)} entries` + (S.t ? " in the window" : "");
    const pg = pagerHtml(pages);
    $("pager-top").innerHTML = pg;
    $("pager-bottom").innerHTML = pg;
    renderScope();
  }
  /** The sticky bar's own line: the set, how many groups are listed, the window and the active filters. */
  function renderScope() {
    const el = $("f-scope");
    if (!el) return;
    const name = LS.name || LS.id || "log set";
    const n = `${fmt(LIST.length)} ${S.view === "sig" ? "groups" : "rows"}`;
    const rest = activeFilters().map((x) => x[1]);
    el.title = [name, n, ...rest].join(" · ") + " (click: back to the top)";
    const r = $("f-reset");
    if (r) r.disabled = !(rest.length || S.sort !== "count" || S.view !== "sig" || S.sig);
    el.innerHTML = `<b>${esc(name)}</b><span class="sc-n">${esc(n)}</span>` +
      (rest.length ? `<span class="sc-f">${esc(rest.join(" · "))}</span>` : "");
  }
  /** Marks the filter bar once it sticks, which shows its scope line. */
  function updateStuck() {
    const f = $("filters");
    const top = parseFloat(getComputedStyle(f).top) || 0;
    const on = getComputedStyle(f).position === "sticky" && f.getBoundingClientRect().top <= top + 0.5 && window.scrollY > 0;
    if (f.classList.contains("is-stuck") === on) return;
    f.classList.toggle("is-stuck", on);
    updateSticky();
  }

  // ---------------------------------------------------------------- filter bar
  function activeFilters() {
    const out = [];
    if (S.q) out.push(["q", `Search: ${S.q}`]);
    if (!sameSet(S.lv, DEFAULT_LV)) out.push(["lv", `Levels: ${[...S.lv].join(", ") || "none"}`]);
    if (S.lg) out.push(["lg", `Logger: ${S.lg}.*`]);
    if (S.ex) out.push(["ex", `Exception: ${shortExc(S.ex)}`]);
    if (S.hx) out.push(["hx", "Has exception"]);
    if (S.cmp !== "all") out.push(["cmp", `Compare: ${S.cmp}`]);
    if (S.t) out.push(["t", `Window: ${tsShort(HOURS[S.t[0]])} (${S.t[1] - S.t[0] + 1} h)`]);
    return out;
  }
  const CLEAR = { q: { q: "" }, lv: null, lg: { lg: "" }, ex: { ex: "" }, hx: { hx: false }, cmp: { cmp: "all" }, t: { t: null, z: null } };
  function resetAll() {
    clearTimeout(qTimer);
    $("f-search").value = "";
    $("f-more").open = false;
    closePops();
    $("f-search").focus({ preventScroll: true });
    commit({ q: "", lv: new Set(DEFAULT_LV), lg: "", ex: "", hx: false, sort: "count", view: "sig", cmp: "all", t: null, z: null, sig: "" });
  }
  function clearFilter(k) {
    if (k === "all") return resetAll();
    if (k === "lv") return commit({ lv: new Set(DEFAULT_LV) });
    if (k === "q") $("f-search").value = "";
    if (CLEAR[k]) commit(CLEAR[k]);
  }
  let advancedInitialized = false;
  function renderControls(v) {
    const lvCounts = {}, cmpCounts = { all: 0, new: 0, vanished: 0, changed: 0 };
    for (const r of v.rows) {
      if (match(r, "lv")) lvCounts[r.bucket] = (lvCounts[r.bucket] || 0) + 1;
      if (CMP && match(r, "cmp")) {
        cmpCounts.all++;
        if (r.cmp.cls in cmpCounts) cmpCounts[r.cmp.cls]++;
      }
    }
    $("f-levels").innerHTML = BUCKETS.filter((b) => PRESENT.has(b)).map((b) =>
      `<button type="button" class="chip" data-lv="${b}" aria-pressed="${S.lv.has(b)}" data-tip-k="rpt-chip">` +
      `<i class="sw sw-${b}"></i>${b}<span class="n">${fmt(lvCounts[b] || 0)}</span></button>`).join("");
    if (CMP) {
      const seg = $("f-compare");
      seg.hidden = false;
      seg.innerHTML = `<span class="lbl">vs ${esc(OTHERNAME)}${baselineNote()}</span>` + CMP_NAMES.map((c) =>
        `<button type="button" data-cmp="${c}" aria-pressed="${S.cmp === c}">${c}<span class="n">${fmt(cmpCounts[c])}</span></button>`).join("");
    }
    const sq = $("f-search");
    if (document.activeElement !== sq && sq.value !== S.q) sq.value = S.q;
    const lg = $("f-logger");
    lg.classList.toggle("on", !!S.lg);
    lg.innerHTML = S.lg ? `<span>Logger: ${esc(S.lg)}.*</span>` : "Logger ▾";
    lg.title = S.lg ? "Logger filter: " + S.lg : "Filter by logger or package";
    const ex = $("f-exc");
    ex.classList.toggle("on", !!S.ex);
    ex.innerHTML = S.ex ? `<span>Exception: ${esc(shortExc(S.ex))}</span>` : "Exception ▾";
    ex.title = S.ex ? "Exception filter: " + S.ex : "Filter by exception type";
    $("f-hasexc").setAttribute("aria-pressed", String(S.hx));
    const af = activeFilters();
    $("f-print").textContent = af.length ? "Filters: " + af.map((x) => x[1]).join(" · ") : "";
    $("f-coarse").setAttribute("aria-pressed", String(S.view === "coarse"));
    $("f-merged").setAttribute("aria-pressed", String(S.view === "merged"));
    $("f-sort").value = S.sort;
    const advanced = [S.lg ? `Logger: ${S.lg}` : "", S.ex ? `Exception: ${shortExc(S.ex)}` : "", S.hx ? "Has exception" : "", S.view !== "sig" ? (S.view === "coarse" ? "Coarse groups" : "Merged groups") : ""].filter(Boolean);
    const moreLabel = $("f-more-label");
    moreLabel.textContent = `More filters${advanced.length ? ` (${advanced.length})` : ""}`;
    moreLabel.classList.toggle("has-active", !!advanced.length);
    moreLabel.title = advanced.length ? advanced.join("; ") : "Logger, exception and grouping options";
    if (!advancedInitialized) {
      $("f-more").open = !!advanced.length;
      advancedInitialized = true;
    }
    if (!$("f-logger-pop").hidden) renderLoggerPop();
    if (!$("f-exc-pop").hidden) renderExcPop();
  }

  // logger prefix tree
  const TREE_OPEN = new Set();
  const MORE_OPEN = new Set();
  let popQuery = { lg: "", ex: "" };
  function loggerTree() {
    const root = { seg: "", full: "", n: 0, own: 0, kids: new Map() };
    for (const r of view(S.view).rows) {
      if (!match(r, "lg") || !r.logger) continue;
      root.n++;
      let node = root;
      const parts = r.logger.split(".");
      for (let i = 0; i < parts.length; i++) {
        let next = node.kids.get(parts[i]);
        if (!next) {
          next = { seg: parts[i], full: parts.slice(0, i + 1).join("."), n: 0, own: 0, kids: new Map() };
          node.kids.set(parts[i], next);
        }
        next.n++;
        node = next;
      }
      node.own++;
    }
    return root;
  }
  function treeItem(full, label, n, depth, twisty) {
    return `<div class="tn" style="--d:${depth}">${twisty}<button type="button" class="tl${S.lg === full ? " on" : ""}" data-lg="${esc(full)}" data-tip-k="rpt-lgnode"><span class="t">${esc(label)}</span><span class="n">${fmt(n)}</span></button></div>`;
  }
  function treeHtml(node, depth, autoOpen) {
    let kids = [...node.kids.values()].sort((a, b) => b.n - a.n || cmpStr(a.seg, b.seg));
    const key = node.full;
    const limit = MORE_OPEN.has(key) ? kids.length : 30;
    let out = "";
    for (const kid of kids.slice(0, limit)) {
      let n = kid, label = kid.seg;
      while (n.kids.size === 1 && n.own === 0) {
        n = n.kids.values().next().value;
        label += "." + n.seg;
      }
      const hasKids = n.kids.size > 0;
      const open = hasKids && (TREE_OPEN.has(n.full) || (autoOpen && kids.length === 1));
      const tw = `<button type="button" class="tw" ${hasKids ? `data-tw="${esc(n.full)}" aria-expanded="${open}"` : "tabindex=\"-1\""}>${hasKids ? (open ? "▾" : "▸") : ""}</button>`;
      out += treeItem(n.full, label + (hasKids ? ".*" : ""), n.n, depth, tw);
      if (open) out += treeHtml(n, depth + 1, false);
    }
    if (kids.length > limit) {
      out += `<div class="tn" style="--d:${depth + 1}"><button type="button" class="more" data-more="${esc(key)}">${fmt(kids.length - limit)} more…</button></div>`;
    }
    return out;
  }
  function renderLoggerPop() {
    const pop = $("f-logger-pop");
    const root = loggerTree();
    let list;
    const q = popQuery.lg.trim().toLowerCase();
    if (q) {
      const found = [];
      const walk = (n) => {
        for (const k of n.kids.values()) {
          if (k.full.toLowerCase().includes(q)) found.push(k);
          walk(k);
        }
      };
      walk(root);
      found.sort((a, b) => b.n - a.n);
      const limit = MORE_OPEN.has("?" + q) ? found.length : 30;
      list = found.slice(0, limit).map((n) => treeItem(n.full, n.full + (n.kids.size ? ".*" : ""), n.n, 0, "")).join("") +
        (found.length > limit ? `<button type="button" class="more" data-more="${esc("?" + q)}">${fmt(found.length - limit)} more…</button>` : "") +
        (found.length ? "" : `<div class="muted">No logger matches.</div>`);
    } else {
      list = treeHtml(root, 0, true) || `<div class="muted">No loggers.</div>`;
    }
    const head = pop.querySelector(".pop-head");
    if (!head) {
      pop.innerHTML = `<div class="pop-head"><input type="search" data-pq="lg" placeholder="Filter loggers" aria-label="Filter loggers"></div><div class="pop-list"></div>`;
    }
    pop.querySelector(".pop-list").innerHTML =
      (S.lg ? `<button type="button" class="more" data-lg="">Clear logger filter</button>` : "") + list;
  }
  function renderExcPop() {
    const pop = $("f-exc-pop");
    const counts = new Map();
    for (const r of view(S.view).rows) if (r.exc && match(r, "ex")) counts.set(r.exc, (counts.get(r.exc) || 0) + 1);
    const q = popQuery.ex.trim().toLowerCase();
    const all = [...counts].filter(([t]) => !q || t.toLowerCase().includes(q)).sort((a, b) => b[1] - a[1] || cmpStr(a[0], b[0]));
    const limit = MORE_OPEN.has("ex:" + q) ? all.length : 30;
    if (!pop.querySelector(".pop-head")) {
      pop.innerHTML = `<div class="pop-head"><input type="search" data-pq="ex" placeholder="Filter exception types" aria-label="Filter exception types"></div><div class="pop-list"></div>`;
    }
    pop.querySelector(".pop-list").innerHTML =
      (S.ex ? `<button type="button" class="more" data-ex="">Clear exception filter</button>` : "") +
      all.slice(0, limit).map(([t, n]) =>
        `<div class="tn"><button type="button" class="tl${S.ex === t ? " on" : ""}" data-ex="${esc(t)}" data-tip-k="rpt-excitem"><span class="t">${esc(shortExc(t))} <span class="muted">${esc(t.slice(0, Math.max(0, t.lastIndexOf("."))))}</span></span><span class="n">${fmt(n)}</span></button></div>`).join("") +
      (all.length > limit ? `<button type="button" class="more" data-more="${esc("ex:" + q)}">${fmt(all.length - limit)} more…</button>` : "") +
      (all.length ? "" : `<div class="muted">No exception types.</div>`);
  }
  const POPS = { "f-logger-pop": "f-logger", "f-exc-pop": "f-exc" };
  /** Close the open facet popover; focus returns to its button when it was inside. */
  function closePops() {
    let closed = false;
    for (const [id, btn] of Object.entries(POPS)) {
      const p = $(id);
      if (p.hidden) continue;
      const inside = p.contains(document.activeElement);
      p.hidden = true;
      closed = true;
      $(btn).setAttribute("aria-expanded", "false");
      if (inside) $(btn).focus({ preventScroll: true });
    }
    return closed;
  }
  function togglePop(id, render) {
    const p = $(id);
    const open = p.hidden;
    closePops();
    if (open) {
      p.hidden = false;
      $(POPS[id]).setAttribute("aria-expanded", "true");
      render();
      const inp = p.querySelector("input");
      if (inp) inp.focus();
    }
  }

  // ---------------------------------------------------------------- drawer
  let drawnKey = null;
  let DCHART = null;
  /** A section head: the label, its count (a trailing "(…)") muted, then the action at the right. */
  const secHead = (title) => {
    const m = /^(.*?) \((.+)\)$/.exec(title);
    return m ? `${esc(m[1])} <span class="sec-n">${esc(m[2])}</span>` : esc(title);
  };
  const sec = (title, body) => `<section class="d-sec"><h3>${secHead(title)}</h3>${body}</section>`;
  /** Web app: the link in the head of the examples section (set by drawerHtml for its group). */
  let EX_ALL = "";
  const secId = (id, title, body) => `<section class="d-sec" id="${id}"><h3>${secHead(title)}${id === "d-s-examples" ? EX_ALL : ""}</h3>${body}</section>`;
  /** Web app: "All N entries ↗", the group's entries in a table in a new tab; inside a window, its entries only. */
  function allEntriesLink(r) {
    if (!ENTRIES_URL || !LS.id || r.gone || r.kind === "merged" || !(r.kind === "coarse" ? r.coarse : r.sig)) return "";
    const p = new URLSearchParams();
    p.set(r.kind === "coarse" ? "coarse_signature" : "signature", r.kind === "coarse" ? r.coarse : r.sig);
    if (S.t) {
      p.set("since", HOURS[S.t[0]]);
      p.set("until", new Date(parseTs(HOURS[S.t[1]]) + 3.6e6).toISOString());
    }
    const n = cnt(r);
    const label = `All ${fmt(n)} ${n === 1 ? "entry" : "entries"}${S.t ? " in window" : ""} ↗`;
    return `<a class="d-all" href="/sets/${encodeURIComponent(LS.id)}/entries?${esc(p.toString())}" target="_blank" rel="noopener" title="Every entry of this group${S.t ? " inside the selected window" : ""}, as a table in a new tab">${label}</a>`;
  }
  // ---------------------------------------------------------------- users
  // GET /api/sets/{id}/users (users.v1) counts the matching entries per user; the standalone
  // file knows each group's 10 most frequent users only (top_users)
  const USERS_URL = FINE ? `/api/sets/${encodeURIComponent(LS.id)}/users` : "";
  const USERS_CACHE = new Map();
  /** The users.v1 document of a query: once in, {err} if it failed, undefined while it loads
   *  (then redraw runs). */
  function usersDoc(qs, redraw) {
    const hit = USERS_CACHE.get(qs);
    if (hit) return hit.value;
    const entry = { value: undefined };
    USERS_CACHE.set(qs, entry);
    if (USERS_CACHE.size > 60) USERS_CACHE.delete(USERS_CACHE.keys().next().value);
    fetch(USERS_URL + "?" + qs, { headers: { Accept: "application/json" } })
      .then((res) => (res.ok ? res.json() : res.json().catch(() => ({})).then((d) => { throw new Error((d.error && d.error.message) || "HTTP " + res.status); })))
      .then((d) => { entry.value = d; }, (err) => { entry.value = { err: String((err && err.message) || err) }; })
      .finally(redraw);
    return undefined;
  }
  /** The window as since/until (UTC, whole hours), added to p. */
  function winParams(p) {
    if (S.t) {
      p.set("since", isoOf(T0MS + S.t[0] * HOUR));
      p.set("until", isoOf(T0MS + (S.t[1] + 1) * HOUR));
    }
    return p;
  }
  /** p with the group's filter (its signature, or a coarse row's coarse signature). */
  function groupParam(r, p) {
    p.set(r.kind === "coarse" ? "coarse_signature" : "signature", r.kind === "coarse" ? r.coarse : r.sig);
    return p;
  }
  const share = (n, of) => {
    if (!(of > 0)) return "";
    const v = (100 * n) / of;
    return n > 0 && v < 0.1 ? "<0.1%" : (v < 10 ? v.toFixed(1) : Math.round(v)) + "%";
  };
  const nounN = (n, one, many = one + "s") => `${fmt(n)} ${n === 1 ? one : many}`;
  /** A count with a small bar beside it, its length against max. */
  const barN = (n, max) => `<span class="u-cell"><span class="u-track"><i style="width:${Math.max(2, (100 * n) / max).toFixed(1)}%"></i></span><span class="u-v">${fmt(n)}</span></span>`;
  /** A user's name, Jira's "anonymous" (no one signed in) marked as such. */
  const userHtml = (u) => esc(u) + (u === "anonymous" ? ` <span class="muted u-anon" title="Jira logs requests of no signed-in user as anonymous">not signed in</span>` : "");
  /** The entries page with one user's entries (of a group, when r is given), in the window. */
  function userEntriesUrl(user, r) {
    if (!ENTRIES_URL || !LS.id) return "";
    const p = new URLSearchParams();
    if (r) groupParam(r, p);
    p.set("user", user);
    return `/sets/${encodeURIComponent(LS.id)}/entries?${winParams(p)}`;
  }
  const USERS_SHOWN = 12;
  const USERS_ALL = new Set();  // groups whose drawer lists every user
  /** The drawer's Users section: every user of the group (web app) with entries, share, first
   *  and last seen and threads, the top user first; the standalone file: the 10 most frequent. */
  function usersSection(r) {
    const of = cnt(r);
    const whereText = S.t ? " in the window" : "";
    let rows, note = "", full = false;
    if (FINE && r.kind !== "merged") {
      const d = usersDoc(winParams(groupParam(r, new URLSearchParams({ limit: "0" }))).toString(), redrawDrawer);
      if (d === undefined) return secId("d-s-users", "Users", `<div class="muted">Counting the entries per user…</div>`);
      if (d.err) return secId("d-s-users", "Users", `<div class="ex-err">Could not load the users (${esc(d.err)}).</div>`);
      rows = d.items.map((u) => ({ user: u.user, n: u.entries, first: u.first_ts, last: u.last_ts, threads: u.threads }));
      full = true;
      if (d.without_user) note = `${fmt(d.without_user)} of the entries${whereText} name no user.`;
    } else {
      rows = mergeTops(r.members || [r], "top_users").filter((x) => x.value != null && x.value !== "").map((x) => ({ user: String(x.value), n: x.count }));
      note = `The ${fmt(rows.length)} most frequent of ${fmt(r.users || rows.length)} users, in the whole set` +
        (FINE ? " (a merged row has no complete list)." : "; the web app (ragnalog serve) lists every user, with first and last seen.");
    }
    if (!rows.length) return secId("d-s-users", "Users", `<div class="muted">No entry of this group${whereText} names a user.</div>`);
    const top = rows[0], of2 = full ? of : r.count;
    const lead = `<p class="u-top"><span class="u-k">Top user</span> <b>${userHtml(top.user)}</b> <span class="muted">${fmt(top.n)} of ${fmt(of2)} entries${full ? whereText : ""} (${share(top.n, of2)})` +
      (rows.length > 1 ? ` · next ${esc(rows[1].user)} (${share(rows[1].n, of2)})` : "") + `</span></p>`;
    const all = USERS_ALL.has(r.id), list = all ? rows : rows.slice(0, USERS_SHOWN);
    const line = (u) => {
      const href = userEntriesUrl(u.user, r);
      const tip = full ? `${u.user}: first ${tsShort(u.first)}, last ${tsShort(u.last)} ${TZ}, ${nounN(u.threads, "thread")}` : u.user;
      const name = FINE
        ? `<button type="button" class="u-link" data-user-tab="${esc(u.user)}" title="${esc(u.user)} across the set: all their groups, in the Users tab">${userHtml(u.user)}</button>`
        : userHtml(u.user);
      return `<tr title="${esc(tip)}"><td class="u-name">${name}</td><td class="num u-n">${barN(u.n, top.n)}</td><td class="num u-sh">${share(u.n, of2)}</td>` +
        (full ? `<td class="num u-t">${esc(tsShort(u.last))}</td>` : "") +
        (href ? `<td class="u-ext"><a href="${esc(href)}" target="_blank" rel="noopener" title="${esc(u.user)}'s entries of this group${whereText}, in a new tab" aria-label="${esc(u.user)}'s entries">↗</a></td>` : "") + `</tr>`;
    };
    const head = `<tr><th>User</th><th class="num">Entries</th><th class="num">Share</th>` +
      (full ? `<th class="num">Last seen</th>` : "") + (ENTRIES_URL ? `<th></th>` : "") + `</tr>`;
    const more = rows.length > USERS_SHOWN
      ? `<button type="button" class="btn small" data-users-all>${all ? "Fewer users" : `All ${fmt(rows.length)} users`}</button>` : "";
    return secId("d-s-users", `Users (${fmt(full ? rows.length : r.users || rows.length)})`,
      lead + `<table class="u-tbl u-grp"><thead>${head}</thead><tbody>${list.map(line).join("")}</tbody></table>` + more +
      (note ? `<p class="muted small u-note">${esc(note)}</p>` : ""));
  }
  // the Users tab: every user of what the filters list (levels, logger, exception, window),
  // the search matching user names; a row opens the user's groups
  const LV_PARAM = { ERROR: "ERROR,FATAL", WARN: "WARN", INFO: "INFO", DEBUG: "DEBUG,TRACE", OTHER: "UNKNOWN" };
  function usersQs(extra) {
    const p = new URLSearchParams({ limit: "0", ...extra });
    if (![...PRESENT].every((b) => S.lv.has(b))) p.set("level", [...S.lv].map((b) => LV_PARAM[b]).join(","));
    if (S.lg) p.set("logger", S.lg);
    if (S.ex) p.set("exception", S.ex);
    if (S.hx) p.set("with_exception", "true");
    return winParams(p).toString();
  }
  const UCOLS = [
    ["user", "User", (a, b) => cmpStr(a.user, b.user)],
    ["entries", "Entries", (a, b) => b.entries - a.entries],
    ["errors", "Errors", (a, b) => b.errors - a.errors],
    ["warnings", "Warnings", (a, b) => b.warnings - a.warnings],
    ["groups", "Groups", (a, b) => b.groups - a.groups],
    ["threads", "Threads", (a, b) => b.threads - a.threads],
    ["last", "Last seen", (a, b) => cmpStr(b.last_ts || "", a.last_ts || "")],
  ];
  let USORT = "entries", USHOW = 200;
  const redrawUsers = () => { if (S.tab === "users") renderUsers(); };
  function renderUsers() {
    const el = $("u-view");
    document.body.classList.toggle("tab-users", S.tab === "users");
    for (const b of $("t-tabs").querySelectorAll("[data-tab]")) {
      const on = (b.dataset.tab || "") === S.tab;
      b.setAttribute("aria-selected", String(on));
      b.setAttribute("aria-pressed", String(on));
    }
    $("f-search").placeholder = S.tab === "users" ? "Search users" : "Search groups";
    if (S.tab !== "users") { el.hidden = true; return; }
    $("u-sum").innerHTML = "";
    el.hidden = false;
    if (!FINE) {
      el.innerHTML = `<div class="empty">The Users tab counts every entry per user, so it needs the web app (<code>ragnalog serve</code>). Each group's drawer lists its 10 most frequent users.</div>`;
      return;
    }
    if (!S.lv.size) { el.innerHTML = `<div class="empty">No level is selected: pick one above.</div>`; return; }
    const d = usersDoc(usersQs(), redrawUsers);
    if (d === undefined) { if (!el.querySelector("table")) el.innerHTML = `<div class="empty">Counting the entries per user…</div>`; el.classList.add("loading"); return; }
    el.classList.remove("loading");
    if (d.err) { el.innerHTML = `<div class="ex-err">Could not load the users (${esc(d.err)}).</div>`; return; }
    const q = S.q.trim().toLowerCase();
    const sorter = (UCOLS.find((c) => c[0] === USORT) || UCOLS[1])[2];
    const items = d.items.filter((u) => !q || u.user.toLowerCase().includes(q))
      .sort((a, b) => sorter(a, b) || b.entries - a.entries || cmpStr(a.user, b.user));
    const max = Math.max(1, ...d.items.map((u) => u.entries));
    // what is counted: the levels of the level chips (always said), the other filters, the window
    const lvText = [...PRESENT].every((b) => S.lv.has(b)) ? "all levels" : BUCKETS.filter((b) => S.lv.has(b)).join(", ") + " entries";
    const scope = [lvText, S.t ? "in the window" : "", activeFilters().filter(([k]) => !["q", "t", "cmp", "lv"].includes(k)).map(([, l]) => l).join(", ")].filter(Boolean).join(" · ");
    $("u-sum").innerHTML = `<b>${fmt(items.length)}</b> ${items.length === 1 ? "user" : "users"}${q ? ` of ${fmt(d.total)}` : ""}` +
      ` · ${fmt(d.entries)} entries with a user` + (d.without_user ? `, ${fmt(d.without_user)} without` : "") +
      (scope ? ` <span class="muted">· ${esc(scope)}</span>` : "");
    if (!items.length) { el.innerHTML = `<div class="empty">No user ${q ? "matches the search" : "in these entries"}.</div>`; return; }
    const th = UCOLS.map(([k, label]) => `<th class="uc-${k}${k === "user" ? "" : " num"}"><button type="button" class="u-sort" data-usort="${k}" aria-pressed="${USORT === k}">${label}${USORT === k ? " ↓" : ""}</button></th>`).join("") + `<th class="uc-g">Largest group</th>`;
    const rows = items.slice(0, USHOW).map((u) => {
      const open = S.user === u.user;
      const g = u.top[0] && BASE_BY.get(u.top[0].signature);
      const main = `<tr class="u-row${open ? " open" : ""}" data-user="${esc(u.user)}" tabindex="0" aria-expanded="${open}">` +
        `<td class="u-name uc-user"><span class="u-caret" aria-hidden="true">${open ? "▾" : "▸"}</span>${userHtml(u.user)}</td>` +
        `<td class="num u-n uc-entries">${barN(u.entries, max)}</td>` +
        `<td class="num uc-errors${u.errors ? " w-err" : " muted"}">${fmt(u.errors)}</td><td class="num uc-warnings${u.warnings ? "" : " muted"}">${fmt(u.warnings)}</td>` +
        `<td class="num uc-groups">${fmt(u.groups)}</td><td class="num uc-threads">${fmt(u.threads)}</td><td class="num u-t uc-last">${esc(tsShort(u.last_ts))}</td>` +
        `<td class="u-g uc-g"><div class="u-clip">${g ? `${badge(g.lvl, g.level)} <span class="tpl">${tplHtml(g.tpl)}</span>` : u.top[0] ? `<span class="mono">${esc(u.top[0].signature)}</span>` : ""}</div></td></tr>`;
      return main + (open ? `<tr class="u-detail"><td colspan="${UCOLS.length + 1}">${userDetail(u)}</td></tr>` : "");
    }).join("");
    const more = items.length > USHOW ? `<button type="button" class="btn small" data-umore>Show ${fmt(Math.min(200, items.length - USHOW))} more of ${fmt(items.length - USHOW)}</button>` : "";
    el.innerHTML = `<div class="u-wrap"><table class="u-tbl u-all"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table></div>` + more;
  }
  /** An opened user: their groups (up to 50) with their share of the user's entries, and links. */
  function userDetail(u) {
    const d = usersDoc(usersQs({ user: u.user, top: "50" }), redrawUsers);
    const one = d && !d.err ? d.items[0] : null;
    const tops = one ? one.top : u.top;
    const list = tops.map((x) => {
      const g = BASE_BY.get(x.signature);
      return `<li><span class="num u-gn">${fmt(x.count)}</span><span class="muted u-gp">${share(x.count, u.entries)}</span>` +
        (g ? `${badge(g.lvl, g.level)} <button type="button" class="u-link u-open" data-open-group="${esc(x.signature)}" title="Open this group in the drawer">${tplHtml(g.tpl)}</button>`
          : `<span class="mono">${esc(x.signature)}</span> <span class="muted">(not in this report)</span>`) + `</li>`;
    }).join("");
    const href = userEntriesUrl(u.user, null);
    const facts = `${nounN(u.entries, "entry", "entries")} · <span class="${u.errors ? "w-err" : ""}">${nounN(u.errors, "error")}</span> · ${nounN(u.warnings, "warning")} · ${nounN(u.groups, "group")} · ${nounN(u.threads, "thread")} · ${esc(tsShort(u.first_ts))} → ${esc(tsShort(u.last_ts))} ${TZ}`;
    return `<div class="u-dh">${facts}${href ? ` · <a href="${esc(href)}" target="_blank" rel="noopener">All their entries${S.t ? " in the window" : ""} ↗</a>` : ""}</div>` +
      `<div class="u-dk">${one ? (u.groups > tops.length ? `Their ${fmt(tops.length)} largest of ${fmt(u.groups)} groups` : `Their ${fmt(u.groups)} ${u.groups === 1 ? "group" : "groups"}`) : d && d.err ? "Their largest groups (the full list did not load)" : "Their largest groups (loading the rest…)"}</div>` +
      `<ol class="u-glist">${list}</ol>`;
  }
  const copyBtn = (text, label = "Copy") => `<button type="button" class="btn small" data-copy="${esc(text)}">${label}</button>`;
  function mergeTops(rows, key) {
    const m = new Map();
    for (const r of rows) {
      if (!r.g) continue;
      for (const vc of pairs(r.g[key])) {
        const v = key === "sources" ? srcPath(vc.value) : vc.value;
        m.set(v, (m.get(v) || 0) + vc.count);
      }
    }
    return [...m].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count).slice(0, 10);
  }
  /** A name cut in the middle: the head gives way to an ellipsis, the tail stays (a thread's number, a file's ".log.1"). */
  function nameHtml(full, file) {
    const text = file ? basename(full) : full;
    // a file keeps its last word whole ("jira.log.1" of NODE_atlassian-jira.log.1), not a cut word
    const m = file ? /[^-_\s.]*\.[A-Za-z0-9]+(?:\.\d+)*$/.exec(text) : null;
    const n = m ? Math.min(m[0].length, 16, text.length) : text.length > 14 ? 7 : 0;
    return `<span class="l"><span class="lh">${esc(text.slice(0, text.length - n))}</span><span class="lt">${esc(text.slice(text.length - n))}</span></span>`;
  }
  const whoRows = (items, max, file) => items.map((x) => {
    const full = x.value == null ? "(none)" : String(x.value);
    return `<div class="wr" style="--p:${r1(Math.max(2, (100 * x.count) / max))}%" title="${esc(full)}">${nameHtml(full, file)}<span class="v">${fmt(x.count)}</span></div>`;
  }).join("");
  /** One column of the "Who" section: its top 5, a "+N more" toggle for the rest, "none" when empty. */
  function whoCol(title, items, total, noun, file) {
    const head = `<h3>${esc(title)}${total ? ` <span class="sec-n">${fmt(total)}</span>` : ""}</h3>`;
    if (!items || !items.length) return `<div class="who-c">${head}<div class="muted">none</div></div>`;
    const max = Math.max(...items.map((x) => x.count));
    const min = Math.min(...items.map((x) => x.count));
    const rest = items.slice(5);
    let list = whoRows(items.slice(0, 5), max, file) +
      (rest.length ? `<details class="who-more"><summary><span class="o">+${rest.length} more</span><span class="c">fewer</span></summary>${whoRows(rest, max, file)}</details>` : "");
    if (items.length >= 3 && (max - min) / max < 0.1) {
      const first = items[0].value == null ? "(none)" : String(items[0].value);
      list = `<details class="flat"><summary title="${esc(file ? basename(first) : first)}, for example">${fmt(Math.max(total || 0, items.length))} ${esc(noun)}, evenly spread</summary>${list}</details>`;
    }
    return `<div class="who-c">${head}${list}</div>`;
  }
  /** Signature groups whose entries per hour rise and fall with r's: Pearson's r over every
   *  hour of the set (sparse sums, so ~1,500 groups take a few ms). Two halves of one story
   *  (a job's start and end, an error and the warning it causes) move together. */
  function movesWith(r, n) {
    if (NH < 6 || !r.hourly.length) return [];
    const x = new Float64Array(NH);
    let sx = 0, sxx = 0;
    for (const [i, c] of r.hourly) if (i >= 0 && i < NH) x[i] += c;
    for (let i = 0; i < NH; i++) { sx += x[i]; sxx += x[i] * x[i]; }
    const vx = sxx - (sx * sx) / NH;
    if (vx <= 0) return [];
    const own = new Set(r.sigs);
    const out = [];
    for (const g of BASE) {
      if (g.gone || own.has(g.sig) || g.count < 5 || g.hourly.length < 3) continue;
      let sy = 0, syy = 0, sxy = 0;
      for (const [i, c] of g.hourly) {
        if (i < 0 || i >= NH) continue;
        sy += c; syy += c * c; sxy += x[i] * c;
      }
      const vy = syy - (sy * sy) / NH;
      if (vy <= 0) continue;
      const rho = (sxy - (sx * sy) / NH) / Math.sqrt(vx * vy);
      if (rho >= 0.6) out.push([g, rho]);
    }
    out.sort((a, b) => b[1] - a[1] || b[0].count - a[0].count);
    return out.slice(0, n);
  }
  function movesList(rows) {
    return `<ul class="members moves">` + rows.map(([m, rho]) =>
      `<li><button type="button" data-open="${esc(m.id)}" title="Correlation of the entries per hour: ${rho.toFixed(2)}">${badge(m.lvl, m.level)}<span class="n">${fmt(m.count)}</span><span class="t" title="${esc(m.tpl)}">${tplHtml(m.tpl)}</span><span class="rho">r ${rho.toFixed(2)}</span>${spark(m)}</button></li>`).join("") + `</ul>`;
  }
  function memberList(rows) {
    return `<ul class="members">` + rows.map((m) =>
      `<li><button type="button" data-open="${esc(m.id)}">${badge(m.lvl, m.level)}<span class="n">${fmt(m.count)}</span><span class="t" title="${esc(m.tpl)}">${tplHtml(m.tpl)}</span><span class="s">${esc(m.sig)}</span></button></li>`).join("") + `</ul>`;
  }
  // entry cards: the code is shared with the cluster page and the entries page (entry-card.js)
  const { logInline, msgHtml, traceHtml, entryHtml, toggleRaw, showAllLines, fullText } = RagnalogEntryCard.create({
    badge: (name, level) => badge(name, level),
    bucketOf: (level) => bucketOf(level),
    levelNum: (name) => levelNum(name),
    tsFull: (iso) => tsFull(iso),
    tz: () => TZ,
    entry: (id) => ENTRIES[id],
    srcPath: (e) => srcPath(e.source ?? e.source_path),
    /** Web app: the full raw entry as plain text (opens in a new tab). */
    rawUrl: (id) => (ENTRIES_URL && LS.id ? `/sets/${encodeURIComponent(LS.id)}/entries/${encodeURIComponent(id)}/raw` : ""),
    fullLoads: () => !!ENTRIES_URL,
  });
  const LEVEL_NUM = { TRACE: 10, DEBUG: 20, INFO: 30, WARN: 40, ERROR: 50, FATAL: 60 };
  const levelNum = (name) => LEVEL_NUM[name] || 0;

  const LAZY = new Set();
  // Web app with a time window: the drawer's examples are the group's first entries inside it,
  // fetched per signature and window (examples URL with since/until); null while loading.
  const WIN_EX = new Map(); // "sig|a-b" -> entry ids, in time order
  const WIN_PENDING = new Set();
  const winEx = () => !!(S.t && ENTRIES_URL);
  const winExKey = (sig) => sig + "|" + WKEY;
  // a failed fetch is not cached: its key waits in EX_FAILED until "Retry" (no retry loop)
  const EX_FAILED = new Set();
  let EX_ERR = "";
  // an aggregate shows one example per member: only its 5 biggest members in the window are asked
  const winExMembers = (r) => (r.members
    ? r.members.filter((m) => !m.gone && winOf(m).n > 0).sort((a, b) => winOf(b).n - winOf(a).n).slice(0, 5)
    : [r]);
  function windowExampleIds(r) {
    const got = winExMembers(r).map((m) => WIN_EX.get(winExKey(m.sig)));
    if (!got.every(Boolean)) return null;
    return r.members ? got.flatMap((ids) => ids.slice(0, 1)) : got[0];
  }
  function fetchFail(res) {
    return Promise.reject(new Error(res ? `HTTP ${res.status}` : "no answer"));
  }
  function exampleIds(r) {
    if (winEx()) return windowExampleIds(r) || [];
    return r.g ? r.g.examples || [] : (r.members || []).flatMap((m) => (m.g && m.g.examples ? m.g.examples.slice(0, 1) : [])).slice(0, 5);
  }
  /** Fetch the window examples of a row's signatures not loaded yet, then redraw the drawer once. */
  function loadWindowExamples(r) {
    const [a, b] = S.t;
    const since = HOURS[a], until = new Date(parseTs(HOURS[b]) + 3.6e6).toISOString();
    const todo = [];
    for (const m of winExMembers(r)) {
      const key = winExKey(m.sig);
      if (WIN_EX.has(key) || WIN_PENDING.has(key) || EX_FAILED.has(key)) continue;
      if (m.gone || !winOf(m).n) WIN_EX.set(key, []);
      else todo.push([m.sig, key]);
    }
    if (!todo.length) return;
    for (const [, key] of todo) WIN_PENDING.add(key);
    const q = `max_raw=0&limit=3&since=${encodeURIComponent(since)}&until=${encodeURIComponent(until)}`;
    Promise.all(todo.map(([sig, key]) =>
      fetch(ENTRIES_URL.replace("{sig}", encodeURIComponent(sig)) + (ENTRIES_URL.includes("?") ? "&" : "?") + q, { headers: { Accept: "application/json" } })
        .then((res) => (res.ok ? res.json() : fetchFail(res)))
        .then((doc) => {
          const items = (doc && doc.items) || {};
          Object.assign(ENTRIES, items);
          WIN_EX.set(key, Object.keys(items).sort((x, y) => cmpStr(items[x].ts || "", items[y].ts || "") || cmpStr(x, y)));
        })
        .catch((err) => { EX_FAILED.add(key); EX_ERR = String(err.message || err); })
        .finally(() => WIN_PENDING.delete(key))))
      .then(redrawDrawer);
  }
  /** Webapp: fetch examples missing from the payload (entries.v1), then redraw the drawer once. */
  function loadExamples(r) {
    if (!ENTRIES_URL) return;
    if (winEx()) return loadWindowExamples(r);
    const sigs = (r.members || [r]).filter((m) => m.g && (m.g.examples || []).some((id) => !ENTRIES[id] || ENTRIES[id].raw_truncated)).map((m) => m.sig);
    const todo = sigs.filter((s) => !LAZY.has(s) && !EX_FAILED.has(s));
    if (!todo.length) return;
    for (const s of todo) LAZY.add(s);
    Promise.all(todo.map((s) =>
      fetch(ENTRIES_URL.replace("{sig}", encodeURIComponent(s)) + (ENTRIES_URL.includes("?") ? "&" : "?") + "max_raw=0", { headers: { Accept: "application/json" } })
        .then((res) => (res.ok ? res.json() : fetchFail(res)))
        .then((doc) => { if (doc && doc.items) Object.assign(ENTRIES, doc.items); })
        .catch((err) => { LAZY.delete(s); EX_FAILED.add(s); EX_ERR = String(err.message || err); })))
      .then(redrawDrawer);
  }
  const iconCopy = (text, what) =>
    `<button type="button" class="icon-btn" data-copy="${esc(text)}" title="Copy ${esc(what)}" aria-label="Copy ${esc(what)}">⧉</button>`;
  const lbl = (t) => `<span class="fl">${esc(t)}</span>`;
  /** The drawer's "Identifiers & CLI" part is open (it also shows the member signatures). */
  let IDS_OPEN = false;
  function drawerHtml(r, inList) {
    const out = [];
    if (!inList) out.push(`<div class="d-banner">This group is not in the current filter.</div>`);
    const what = r.kind === "merged" ? `Drain3 cluster ${r.cluster ? r.cluster.cluster_id : ""}` : r.kind === "coarse" ? "Coarse group" : "";
    // the figures sit on the badge row, as on the cluster page; a window adds its own count
    const figs = [];
    if (r.members) figs.push(`<span>${esc(what)} · ${r.members.length} signatures</span>`);
    if (r.gone) figs.push(`<span>only in ${esc(OTHERNAME)}</span>`);
    else {
      figs.push(`<span><b>${fmt(r.count)}</b> ${r.count === 1 ? "entry" : "entries"}</span>`);
      if (S.t) figs.push(`<span><b>${fmt(winOf(r).n)}</b> in window</span>`);
    }
    out.push(`<div class="d-head">${badge(r.lvl, r.level)}${r.cmp ? deltaChip(r.cmp) : ""}<span class="d-figs">${figs.join(" · ")}</span></div>`);
    out.push(`<h2 class="d-tpl" tabindex="-1">${tplHtml(r.tpl)}</h2>`);
    if (r.exc || r.logger) {
      // one line: the logger with its package abbreviated, the exception by its class name; full names in the tooltips
      out.push(`<div class="d-sub">${r.logger ? `<span title="Logger ${esc(r.logger)}">${loggerHtml(r.logger, true)}</span>` : ""}${r.exc ? `<span title="Exception ${esc(r.exc)}">${excHtml(r.exc, true)}</span>` : ""}</div>`);
    }
    if (!r.gone) {
      // one line of facts: muted labels, ink values; exact times and how a number is counted on hover
      const at = (x, what) => (x ? tipCard({ h: what, rows: [["Time", tsFull(x.iso) + " " + TZ]],
        note: x.approx ? seenTitle(x) : null }) : "");
      const whole = (h, extra) => {
        const n = [S.t ? "Over the whole set, not the window." : "", extra || ""].filter(Boolean).join(" ");
        return tipCard({ h, note: n || null });
      };
      const many = r.members ? "The largest member's count: the members may share them." : "";
      const w = S.t ? winOf(r) : null;
      const first = w ? w.first : { iso: r.first }, last = w ? w.last : { iso: r.last };
      const T = (x) => (x && x.iso ? tsHtml(w ? seenText(x, true) : ts16(x.iso)) : nilHtml());
      const item = (html, tip) => `<span class="fi"${tip ? ` data-tip="${esc(tip)}"` : ""}>${html}</span>`;
      const count = (n, one, more, tip, ge) => item(n
        ? `<span class="fv">${ge ? "≥ " : ""}${fmt(n)}</span> ${lbl(n === 1 && !ge ? one : more)}` : lbl(`no ${more}`), tip);
      const span = S.t ? S.t[1] - S.t[0] + 1 : NH, n = S.t ? w.n : r.count;
      const rate = span > 0 ? n / span : 0;
      const f = [item(`${lbl(S.t ? "First in window" : "First")} ${T(first)}`, at(first, S.t ? "First entry in the window" : "First entry")) + " " +
        item(`→ ${lbl("last")} ${T(last)} ${TZ}`, at(last, S.t ? "Last entry in the window" : "Last entry"))];
      if (rate > 0) {
        f.push(item(`<span class="fv">≈${rate >= 10 ? fmt(Math.round(rate)) : rate < 0.05 ? "<0.1" : String(Math.round(rate * 10) / 10)}</span> ${lbl("/ h")}`,
          tipCard({ h: "Entries per hour", note: S.t ? `${fmt(n)} entries over the ${fmt(span)} hours of the window` : `The average over the ${fmt(span)} hours of the set` })));
      }
      f.push(count(r.threads, "thread", "threads", whole("Distinct threads", many), !!r.members));
      f.push(count(r.users, "user", "users", whole("Distinct users", many), !!r.members));
      f.push(count(r.nsrc, "source", "sources", whole("Source files with entries of the group", many)));
      if (S.t) f.push(`<span class="fi fm">whole set: ${esc(ts16(r.first))} → ${esc(ts16(r.last))} ${TZ}</span>`);
      out.push(`<div class="d-facts">${f.join(" · ")}</div>`);
    }
    if (r.cmp) {
      const d = r.cmp.d;
      out.push(`<div class="d-facts d-cmp" data-tip-k="rpt-delta"><span class="fi wrap">${lbl("vs " + OTHERNAME)} <span class="fv">${abText(r.cmp)}</span></span>` +
        (d && d.first_seen_a ? ` · <span class="fi" title="First and last entry in ${esc(OTHERNAME)}">${lbl("seen")} ${tsHtml(ts16(d.first_seen_a))} → ${tsHtml(ts16(d.last_seen_a))} ${TZ}</span>` : "") + `</div>`);
    }
    if (!r.gone) out.push(secId("d-s-hourly", "Per hour", `<div class="d-chart" id="d-hourly"></div>`));
    EX_ALL = allEntriesLink(r);
    const exIds = exampleIds(r);
    let ex = exIds.filter((id) => ENTRIES[id]);
    let pending = ENTRIES_URL && ex.length < exIds.length;
    let exTitle = `Example entries (${ex.length})`;
    if (winEx()) {
      pending = windowExampleIds(r) === null;
      exTitle = pending ? "Examples in the window" : `Examples in the window (${ex.length})`;
      if (!ex.length && !pending && !r.gone) out.push(secId("d-s-examples", exTitle, `<div class="muted">No entries of this group in the window.</div>`));
    } else if (S.t && ex.length) {
      // examples inside the window first; the rest stay for context
      const inWin = (id) => {
        const h = hourOf(ENTRIES[id].ts);
        return h >= S.t[0] && h <= S.t[1];
      };
      const n = ex.filter(inWin).length;
      ex = [...ex.filter(inWin), ...ex.filter((id) => !inWin(id))];
      exTitle = `Example entries (${ex.length}, ${n ? n + " in the window" : "none in the window"})`;
    }
    const failed = winEx() ? winExMembers(r).some((m) => EX_FAILED.has(winExKey(m.sig))) : (r.members || [r]).some((m) => EX_FAILED.has(m.sig));
    const exErr = failed
      ? `<div class="ex-err">Could not load the examples (${esc(EX_ERR)}). <button type="button" class="btn small" data-retry-ex>Retry</button></div>` : "";
    if (exErr) { out.push(secId("d-s-examples", exTitle.replace(/ \(\d+\)$/, ""), exErr + ex.map((id) => entryHtml(id, ENTRIES[id])).join(""))); }
    else if (ex.length) out.push(secId("d-s-examples", exTitle, ex.map((id) => entryHtml(id, ENTRIES[id])).join("")));
    else if (!r.gone && (pending || !winEx())) out.push(secId("d-s-examples", exTitle.replace(/ \(\d+\)$/, ""), `<div class="muted">${pending ? "Loading examples…" : "No examples in this report."}</div>`));
    if (!r.gone) {
      const src = r.members || [r];
      out.push(usersSection(r));
      const tops = [
        ["Top threads", mergeTops(src, "top_threads"), r.threads, "threads", false],
        ["Sources", mergeTops(src, "sources"), r.nsrc, "sources", true],
      ];
      if (tops.some((t) => t[1].length)) out.push(`<section class="d-sec tops" id="d-s-who">` + tops.map((t) => whoCol(...t)).join("") + `</section>`);
      const moves = movesWith(r, 6);
      if (moves.length) {
        out.push(secId("d-s-rel", "Moves with", `<p class="muted small" title="Pearson's r of the entries per hour over the whole set, 0.6 and up">Groups that rise and fall with this one, hour by hour: often the other half of the story.</p>` + movesList(moves)));
      }
    }
    if (r.members) {
      out.push(sec(r.kind === "merged" ? "Merged signatures" : "Member signatures", memberList(r.members)));
    } else if (CLUSTER.has(r.sig)) {
      const m = CLUSTER.get(r.sig);
      const sibs = m.signatures.filter((s) => s !== r.sig).map((s) => BASE_BY.get(s)).filter(Boolean).sort(byCount);
      out.push(sec(`Merge proposal (Drain3 cluster ${m.cluster_id})`,
        `<div class="d-sub mono" style="margin-bottom:6px">${tplHtml(m.drain_template || "")}</div>${memberList(sibs)}`));
    }
    const set = r.gone ? OTHERREF : SETREF;
    const sig = r.members ? r.members[0].sig : r.sig;
    const cmds = [`ragnalog show ${set} ${sig}`, `ragnalog entries ${set} --signature ${sig} --limit 50`];
    if (r.kind === "coarse") cmds.push(`ragnalog query ${set} "select signature, count, template from groups where coarse_signature = '${r.coarse}' order by count desc"`);
    if (r.kind === "merged") cmds.push(`ragnalog query ${set} "select signature, count, template from groups where signature in (${r.sigs.map((s) => `'${s}'`).join(", ")}) order by count desc"`);
    const ids = [];
    if (r.kind === "sig") ids.push(["sig", r.sig, "signature"]);
    if (r.kind !== "merged" && !r.gone && r.coarse !== r.sig) ids.push(["coarse", r.coarse, "coarse signature"]);
    out.push(`<details class="d-sec d-idcli" id="d-s-cli"${IDS_OPEN ? " open" : ""}><summary>Identifiers &amp; CLI</summary>` +
      (ids.length ? `<div class="d-ids">` + ids.map(([k, v, w]) => `<span>${kvHtml(k, v).replace('class="v"', 'class="v ident"')}${iconCopy(v, w)}</span>`).join("") + `</div>` : "") +
      `<div class="cli">` + cmds.map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div></details>`);
    return out.join("");
  }
  function drawHourly(r) {
    const el = $("d-hourly");
    if (!el || !NH) return;
    const dense = new Float64Array(NH);
    for (const [i, n] of r.hourly) dense[i] = n;
    const lv = bucketOf(r.level);  // the group's level colour, as on the entries page
    DCHART = drawColumns(el, [{ key: lv, name: "entries", cls: "s-" + lv, data: dense }], { height: 84, label: "Entries per hour", clipKey: "d" });
    const h3 = el.parentElement && el.parentElement.querySelector("h3");
    const k = DCHART.k;
    if (h3) h3.innerHTML = esc("Per " + (k === 1 ? "hour" : k === 24 ? "day" : k + " hours")) + (S.t ? ` <span class="sec-n">(window shaded)</span>` : "") + clipBtn(DCHART);
  }
  /** Redraw the drawer with what arrived (examples), keeping its scroll position. */
  function redrawDrawer() {
    if (drawnKey) drawnKey += "|stale";  // still the same group: no jump to the top, no fade
    renderDrawer();
  }
  function renderDrawer() {
    const dr = $("drawer");
    if (!S.sig) {
      dr.hidden = true;
      document.body.classList.remove("drawer-open");
      drawnKey = null;
      DCHART = null;
      return;
    }
    dr.hidden = false;
    document.body.classList.add("drawer-open");
    const idx = LIST.findIndex((x) => x.id === S.sig);
    $("d-pos").innerHTML = `<b>${idx >= 0 ? fmt(idx + 1) : "–"}</b> of ${fmt(LIST.length)}`;
    const cur = lookup(S.sig);
    $("d-mute").hidden = !cur || !!cur.gone;
    $("d-mute").setAttribute("aria-pressed", String(!!cur && !cur.gone && isMuted(cur)));
    $("d-ticket").hidden = !cur;
    $("d-which").innerHTML = cur ? `${badge(cur.lvl, cur.level)}<span class="t" title="${esc(cur.tpl)}">${tplHtml(cur.tpl)}</span>` : "";
    const key = S.view + "|" + S.sig + "|" + (S.t || "") + "|" + (idx >= 0) + "|" + TZ + "|" + [...S.mute].sort().join(",");
    if (key === drawnKey) return;
    const sameGroup = drawnKey && drawnKey.split("|")[1] === S.sig;
    drawnKey = key;
    const r = lookup(S.sig);
    const body = $("drawer-body");
    const top = body.scrollTop;
    body.classList.toggle("show-ids", IDS_OPEN);
    body.innerHTML = r ? drawerHtml(r, idx >= 0) : `<div class="empty">Group ${esc(S.sig)} is not in this report.</div>`;
    // the section buttons of sections this group has (Related: only with "Moves with")
    for (const b of $("d-nav").querySelectorAll("[data-jump]")) b.hidden = !document.getElementById(b.dataset.jump);
    body.scrollTop = sameGroup ? top : 0;
    if (!sameGroup) {
      body.classList.remove("fresh");
      void body.offsetWidth;
      body.classList.add("fresh");
    }
    if (r && !r.gone) drawHourly(r);
    if (r) loadExamples(r);
  }

  // ---------------------------------------------------------------- overview
  function renderOverview() {
    const st = D.stats || {};
    const lv = st.levels || {};
    const tr = st.time_range || {};
    $("set-name").textContent = LS.name || LS.id || "log set";
    const nsrc = (LS.sources || []).length;
    const opts = D.options || {};
    // the set's facts, quiet; the exact span and the source files are in their tooltips
    $("set-meta").innerHTML = [
      LS.format ? `<span>${esc(LS.format)}</span>` : "",
      tr.first ? `<span class="m-range" data-tip-k="rpt-meta" data-k="range">${esc(ts16(tr.first))} → ${esc(tsShort(tr.last))} ${esc(TZ)}</span>` +
        ` <span class="m-dur">${esc(duration(tr.first, tr.last))}</span>` : "<span>no entries</span>",
      `<span data-tip-k="rpt-meta" data-k="src">${fmt(nsrc)} source file${nsrc === 1 ? "" : "s"}</span>`,
      opts.truncated_groups > 0 ? `<span class="m-warn">showing ${fmt(BASE.length)} of ${fmt(BASE.length + opts.truncated_groups)} groups</span>` : "",
    ].filter(Boolean).join(`<span class="m-sep" aria-hidden="true">·</span>`);
    renderTiles();
    const sort = $("f-sort");
    sort.innerHTML = Object.entries(SORTS).map(([k, [label]]) => `<option value="${k}">${esc(label)}</option>`).join("");
    $("f-merged").hidden = !MERGES.length;
    if (MERGES.length) $("f-merged").title = `Apply ${MERGES.length} Drain3 merge proposals`;
  }
  let tilesKey = null;
  /** Overview tiles; with a time window each also tells how much of it falls in the window. */
  function renderTiles() {
    WKEY = S.t ? S.t.join("-") : "";
    const tk = WKEY + "|" + [...S.mute].sort().join(",") + "|" + [...S.lv].sort().join(",");
    if (tilesKey === tk) return;
    tilesKey = tk;
    const st = D.stats || {};
    const lv = st.levels || {};
    const opts = D.options || {};
    const total = st.group_count ?? BASE.length;
    const groupsNote = BASE.length !== total
      ? `${fmt(BASE.length)} in this report` + (opts.min_level ? ` (${opts.min_level}+)` : "")
      : "";
    // [key, label, value, note, window note, level swatch]; the breakdowns are in the tooltips
    const tiles = [
      ["entries", "Entries", fmt(st.entry_count ?? LS.entry_count ?? 0), ""],
      ["groups", "Groups", fmt(total), groupsNote],
      ["errors", "Errors", fmt((lv.ERROR || 0) + (lv.FATAL || 0)), lv.FATAL ? `incl. ${fmt(lv.FATAL)} FATAL` : "", "", "ERROR"],
      ["warnings", "Warnings", fmt(lv.WARN || 0), "", "", "WARN"],
    ];
    if (S.t) {
      const wl = levelsIn(S.t[0], S.t[1]);
      let we = 0;
      for (const k of BUCKETS) we += wl[k];
      let wg = 0;
      for (const r of BASE) if (winOf(r).n > 0) wg++;
      const win = [we, wg, wl.ERROR, wl.WARN];
      for (let i = 0; i < 4; i++) tiles[i][4] = `${fmt(win[i])} in window`;
    }
    if (st.parse_errors) tiles.push(["unparsed", "Unparsed", fmt(st.parse_errors), ""]);
    // Summary cards retain the exact breakdowns and selected-window totals in their tooltips.
    let html = tiles.map(([k, l, v, n, w, sw]) => `<span class="tile" data-tip-k="rpt-tile" data-k="${k}">${sw ? `<i class="sw sw-${sw}"></i>` : ""}<span class="label">${l}</span><b class="value">${esc(v)}</b>` +
      (w ? `<span class="note win-note">${esc(w)}</span>` : "") + (n ? `<span class="note">${esc(n)}</span>` : "") + `</span>`).join("");
    if (CMPD) {
      const mini = [["new", CMPN.new, (CMPD.new || []).length], ["changed", CMPN.changed, (CMPD.changed || []).length],
        ["vanished", CMPN.vanished, (CMPD.vanished || []).length]];
      const partial = mini.filter(([, n, shown]) => shown < n).map(([k, n, shown]) => `${k} ${fmt(shown)} of ${fmt(n)}`);
      html += `<span class="tile cmp-tile"><span class="label" data-tip-k="rpt-tile" data-k="cmp">vs <span class="nm">${esc(OTHERNAME)}</span>${CMPD.min_level ? ` (${esc(CMPD.min_level)}+)` : ""}${baselineNote()}</span>` +
        mini.map(([k, n]) => `<span class="mini" data-tip-k="rpt-tile" data-k="cmp-${k}"><b class="value">${fmt(n)}</b> <span class="label">${k}</span></span>`).join("") +
        `<span class="note" data-tip-k="rpt-tile" data-k="cmp">${fmt(CMPN.unchanged)} unchanged${partial.length ? " · the table lists " + esc(partial.join(", ")) : ""}</span></span>`;
    }
    $("tiles").innerHTML = html;
    const notices = [];
    if (CMPD) notices.push(`Compared with ${esc(OTHERNAME)}${baselineNote()} · ${fmt(CMPN.new)} new, ${fmt(CMPN.changed)} changed, ${fmt(CMPN.vanished)} vanished`);
    if (st.parse_errors) notices.push(`<span class="parse-note">${fmt(st.parse_errors)} unparsed entries</span>`);
    $("set-notices").innerHTML = notices.join(" · ");
    $("set-notices").hidden = !notices.length;
  }

  // ---------------------------------------------------------------- tooltips
  // Builders for data-tip-k: run on hover only, from the row or control under the pointer, so
  // the table stays light; they hold what the cells leave out (full names, exact times,
  // breakdowns, how a number is counted).
  const ALL_ENTRIES = (D.stats || {}).entry_count ?? LS.entry_count ?? 0;
  const pct = (n, d) => {
    if (!(d > 0) || !n) return "0%";
    const p = (100 * n) / d;
    return p < 0.1 ? "<0.1%" : (p < 10 ? p.toFixed(1) : p.toFixed(0)) + "%";
  };
  const exact = (iso) => (iso ? tsFull(iso) + " " + TZ : "—");
  const hourAt = (i) => ts16(HOURS[i]) + " " + TZ;
  /** "8,883 at 10-29 11:00 UTC": the busiest hour of a [hour, count] peak. */
  const peakText = (p) => `${fmt(p[1])} at ${tsShort(HOURS[p[0]])} ${TZ}`;
  /** [hour index, count] of the busiest hour of an hourly [[i, n], ...] list, or null. */
  function peakOf(hourly) {
    let pi = -1, pc = 0;
    for (const [i, c] of hourly) if (c > pc) { pc = c; pi = i; }
    return pi < 0 ? null : [pi, pc];
  }
  /** The same for a dense per-hour array. */
  function peakArr(arr) {
    let pi = -1, pc = 0, hours = 0;
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] > 0) hours++;
      if (arr[i] > pc) { pc = arr[i]; pi = i; }
    }
    return { peak: pi < 0 ? null : [pi, pc], hours };
  }
  /** Entries of the whole timeline (muted groups included) in hours [a, b]. */
  function entriesIn(a, b) {
    const levels = (D.timeline || {}).levels || {};
    let t = 0;
    for (const k of BUCKETS) for (let i = a; i <= b && i < (levels[k] || []).length; i++) t += levels[k][i] || 0;
    return t;
  }
  const rowOf = (el) => {
    const tr = el.closest("[data-id]");
    return lookup(tr ? tr.dataset.id : S.sig);
  };
  const tplTip = (t) => (t && REPEATED.test(t) ? esc(t) : tplHtml(t)) || '<span class="m">(no message)</span>';
  const monoTip = (s) => ({ html: `<span class="atip-mono">${esc(s)}</span>` });
  /** A larger per-hour chart of a row for its tooltip, the window shaded. */
  function bigSpark(r) {
    const W = 300, H = 54, k = Math.max(1, Math.ceil(NH / 100)), nb = Math.ceil(NH / k), bw = W / nb;
    const bins = new Float64Array(nb);
    let max = 0;
    for (const [i, n] of r.hourly) {
      const b = Math.floor(i / k);
      bins[b] += n;
      if (bins[b] > max) max = bins[b];
    }
    let d = "";
    const w = bw >= 3 ? bw - 1 : bw;
    for (let b = 0; b < nb; b++) {
      if (!bins[b]) continue;
      const h = Math.max(1.5, ((H - 2) * bins[b]) / max);
      d += `M${r1(b * bw)} ${H}v-${r1(h)}h${r1(w)}v${r1(h)}Z`;
    }
    const win = S.t ? `<rect class="spark-win" x="${r1(Math.floor(S.t[0] / k) * bw)}" y="0" width="${r1((Math.floor(S.t[1] / k) + 1 - Math.floor(S.t[0] / k)) * bw)}" height="${H}"/>` : "";
    return `<svg class="rtip-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${win}<path d="${d}"/></svg>` +
      `<div class="rtip-axis"><span>${esc(tsShort(HOURS[0]))}</span><span>${esc(TZ)} · ${k === 1 ? "1 hour" : k + " hours"} a bar</span><span>${esc(tsShort(HOURS[NH - 1]))}</span></div>`;
  }
  /** Top values (users, threads) of a row as tooltip rows. */
  function topRows(r, key, n) {
    return mergeTops(r.members || [r], key).slice(0, n).map((x) => [x.value == null ? "(none)" : String(x.value), fmt(x.count)]);
  }
  /** Groups and entries of the view's rows that pass the filters (but `skip`) and `pick`. */
  function tally(skip, pick) {
    let groups = 0, entries = 0;
    for (const x of view(S.view).rows) {
      if (!pick(x) || !match(x, skip)) continue;
      groups++;
      entries += cnt(x);
    }
    return { groups, entries };
  }
  const tallyRows = (t) => [["Groups", fmt(t.groups)], [S.t ? "Entries in the window" : "Entries", fmt(t.entries)]];
  const FILTERED = () => (S.q || S.lg || S.ex || S.hx || S.t || (CMP && S.cmp !== "all") || !sameSet(S.lv, DEFAULT_LV));

  function registerTips() {
    if (!TIPS) return;
    const T = TIPS.register;
    // ---- table cells
    T("rpt-group", (el) => {
      const r = rowOf(el);
      if (!r) return "";
      const rows = [];
      if (r.gone) rows.push(["Entries", `none here; ${fmt(r.cmp.a)} in ${OTHERNAME}`]);
      else {
        rows.push(["Entries", fmt(r.count) + (S.t ? ` · ${fmt(winOf(r).n)} in the window` : "")]);
        rows.push(["First seen", exact(r.first)], ["Last seen", exact(r.last)]);
      }
      if (r.exc) rows.push(["Exception", monoTip(r.exc)]);
      if (r.members) rows.push([r.kind === "merged" ? "Merged" : "Coarse group of", `${r.members.length} signatures`]);
      else rows.push(["Signature", monoTip(r.sig)]);
      if (r.kind === "sig" && r.coarse !== r.sig && !r.gone) rows.push(["Coarse", monoTip(r.coarse)]);
      return TIPS.card({
        h: { html: tplTip(r.tpl) },
        sub: r.logger ? monoTip(r.logger) : null,
        rows,
        note: `Click to open · c copies the signature${r.gone ? "" : " · m mutes it in the chart"}`,
      });
    });
    T("rpt-count", (el) => {
      const r = rowOf(el);
      if (!r) return "";
      const rows = [];
      const p = peakOf(r.hourly);
      if (S.t) {
        const n = winOf(r).n;
        rows.push(["Whole set", fmt(r.count)], ["Share of the window", pct(n, entriesIn(S.t[0], S.t[1]))]);
      }
      if (!r.gone) rows.push(["Share of the set", pct(r.count, ALL_ENTRIES)]);
      if (p) rows.push(["Busiest hour", peakText(p)]);
      if (r.hourly.length) rows.push(["Hours with entries", `${fmt(r.hourly.length)} of ${fmt(NH)}`]);
      const sp = spikeOf(r);
      if (sp > 0) rows.push(["Spike", "×" + (sp >= 1000 ? compact(sp) : ratioText(sp)), sp >= 10 ? "e" : ""]);
      if (r.cmp && !r.gone) rows.push([`In ${OTHERNAME}`, fmt(r.cmp.a)]);
      return TIPS.card({
        h: r.gone ? `No entries here` : `${fmt(cnt(r))} entries${S.t ? " in the window" : ""}`,
        rows,
        note: (sp > 0 ? (S.t ? "Spike: the rate in the window over the rate outside it. " : "Spike: the busiest hour over the average hour. ") : "") +
          "The bar is on a log scale, smallest to largest group" + (S.t ? " in the window." : "."),
      });
    });
    T("rpt-logger", (el) => {
      const r = rowOf(el);
      if (!r || !r.logger) return "";
      const t = tally("lg", (x) => x.logger === r.logger);
      return TIPS.card({ h: r.logger.slice(r.logger.lastIndexOf(".") + 1), sub: r.logger.includes(".") ? monoTip(r.logger) : null, rows: tallyRows(t),
        note: "Groups of this logger the filters list. Filter by a logger or a package: the Logger menu" });
    });
    T("rpt-exc", (el) => {
      const r = rowOf(el);
      if (!r || !r.exc) return "";
      const t = tally("ex", (x) => x.exc === r.exc);
      return TIPS.card({ h: shortExc(r.exc), sub: r.exc.includes(".") ? monoTip(r.exc) : null, rows: tallyRows(t),
        note: "Groups with this exception the filters list. Filter: the Exception menu" });
    });
    T("rpt-spark", (el) => {
      const r = rowOf(el);
      if (!r || !r.hourly.length) return "";
      const p = peakOf(r.hourly);
      const rows = [];
      if (p) rows.push(["Busiest hour", peakText(p)]);
      rows.push(["Hours with entries", `${fmt(r.hourly.length)} of ${fmt(NH)}`]);
      if (S.t) rows.push(["In the window", fmt(winOf(r).n)]);
      const kv = '<dl class="atip-kv">' + rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") + "</dl>";
      return TIPS.card({ h: "Entries per hour", body: bigSpark(r) + kv, note: S.t ? "Shaded: the time window" : null });
    });
    T("rpt-seen", (el) => {
      const r = rowOf(el);
      if (!r || r.gone) return "";
      const rows = [["First seen", exact(r.first)], ["Last seen", exact(r.last)]];
      if (r.first && r.last) rows.push(["Seen over", duration(r.first, r.last) || "—"]);
      let note = null;
      if (S.t) {
        const w = winOf(r);
        rows.push(["First in window", seenText(w.first, true) || "—"], ["Last in window", seenText(w.last, true) || "—"]);
        if ((w.first && w.first.approx) || (w.last && w.last.approx)) note = "\"xx\": " + seenTitle({ approx: true });
      }
      return TIPS.card({ h: S.t ? "Last entry in the window" : "Last entry", rows, note });
    });
    T("rpt-ut", (el) => {
      const r = rowOf(el);
      if (!r || r.gone) return "";
      const rows = [["Distinct users", fmt(r.users)], ["Distinct threads", fmt(r.threads)], ["Source files", fmt(r.nsrc)]];
      const users = topRows(r, "top_users", 3), threads = topRows(r, "top_threads", 3);
      const list = (title, items) => (items.length
        ? `<div class="rtip-k">${esc(title)}</div><dl class="atip-kv">` + items.map(([k, v]) => `<dt class="rtip-v">${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") + "</dl>"
        : "");
      return TIPS.card({
        h: "Users and threads", rows,
        body: list("Top users", users) + list("Top threads", threads) || null,
        note: (S.t ? "Over the whole set, not the window. " : "") + (r.members ? "Coarse and merged rows: the largest member's counts" : "") || null,
      });
    });
    T("rpt-delta", (el) => {
      const r = rowOf(el);
      if (!r || !r.cmp) return "";
      const c = r.cmp;
      const h = c.cls === "new" ? "New: not in the baseline" : c.cls === "vanished" ? "Vanished: only in the baseline"
        : c.a === c.b ? "As often as in the baseline" : `${c.b > c.a ? "More" : "Less"} often than in the baseline`;
      const rows = [["Baseline", fmt(c.a)], ["This set", fmt(c.b)]];
      if (c.a && c.b && c.a !== c.b) rows.push(["Change", `${c.b > c.a ? "+" : "−"}${fmt(Math.abs(c.b - c.a))} (×${ratioText(c.b / c.a)})`, c.b > c.a ? "e" : ""]);
      const d = c.d;
      if (d && d.first_seen_a) rows.push(["Seen in the baseline", `${ts16(d.first_seen_a)} → ${tsShort(d.last_seen_a)} ${TZ}`]);
      const tr = (D.stats || {}).time_range || {};
      if (OTHER_SPAN && tr.first) rows.push(["Spans", `baseline ${OTHER_SPAN}, this set ${duration(tr.first, tr.last)}`]);
      return TIPS.card({ h, sub: `Baseline: ${OTHERNAME}`, rows,
        note: "Whole sets, not the time window" + (MOSTLY_NEW ? ". Most groups are new: the baseline may cover a shorter or different period" : "") });
    });
    // ---- filters
    T("rpt-chip", (el) => {
      const b = el.dataset.lv;
      const t = tally("lv", (x) => x.bucket === b);
      return TIPS.card({ h: LV_TITLE[b] || b, rows: [...tallyRows(t), ["In the table", S.lv.has(b) ? "shown" : "hidden", S.lv.has(b) ? "" : "m"]],
        note: (FILTERED() ? "Counted with the other filters. " : "") + "Click to show or hide · Shift-click: only this level" });
    });
    T("rpt-lgnode", (el) => {
      const full = el.dataset.lg;
      const t = tally("lg", (x) => x.logger === full || x.logger.startsWith(full + "."));
      return TIPS.card({ h: full.slice(full.lastIndexOf(".") + 1) || full, sub: full.includes(".") ? monoTip(full) : null, rows: tallyRows(t),
        note: "Click to list this logger and the loggers under it" });
    });
    T("rpt-excitem", (el) => {
      const ex = el.dataset.ex;
      const t = tally("ex", (x) => x.exc === ex);
      return TIPS.card({ h: shortExc(ex), sub: ex.includes(".") ? monoTip(ex) : null, rows: tallyRows(t), note: "Click to list the groups with this exception" });
    });
    // ---- timeline
    T("rpt-lg", (el) => {
      const b = el.dataset.hl;
      const arr = levelData()[b] || [];
      const { peak, hours } = peakArr(arr);
      let all = 0;
      for (const k of BUCKETS) all += TL_TOTALS[k] || 0;
      const rows = [["Entries", fmt(TL_TOTALS[b] || 0)], ["Share of the chart", pct(TL_TOTALS[b] || 0, all)]];
      if (peak) rows.push(["Busiest hour", peakText(peak)]);
      rows.push(["Hours with entries", `${fmt(hours)} of ${fmt(NH)}`]);
      if (S.t) rows.push(["In the window", fmt(levelsIn(S.t[0], S.t[1])[b] || 0)]);
      const on = !S.hl.has(b);
      return TIPS.card({ h: LV_TITLE[b] || b, rows,
        note: (S.mute.size ? "Muted groups left out. " : "") + (on ? "Click to hide it in the chart · Shift-click: only this level" : "Hidden in the chart: click to show it") });
    });
    T("rpt-win", () => {
      if (!S.t) return "";
      const [a, b] = S.t;
      const lv = levelsIn(a, b);
      let total = 0;
      for (const k of BUCKETS) total += lv[k];
      const rows = [["From", hourAt(a)], ["To", ts16(new Date(parseTs(HOURS[b]) + 3.6e6).toISOString()) + " " + TZ], ["Entries", fmt(total)]];
      if (lv.ERROR) rows.push(["Errors", fmt(lv.ERROR), "e"]);
      if (lv.WARN) rows.push(["Warnings", fmt(lv.WARN)]);
      if (BIRTHS) {
        let born = 0;
        for (let h = a; h <= b; h++) born += BIRTHS.n[h];
        rows.push(["Groups first seen", fmt(born)]);
      }
      return TIPS.card({ h: `Time window · ${hoursText(b - a + 1)}`, rows,
        note: (S.mute.size ? "Muted groups left out. " : "") + "[ and ] move it by its own length; a click on the chart clears it" });
    });
    // ---- overview
    T("rpt-meta", (el) => {
      const tr = (D.stats || {}).time_range || {};
      if (el.dataset.k === "range") {
        return TIPS.card({ h: "Time span of the set", rows: [["First entry", exact(tr.first)], ["Last entry", exact(tr.last)],
          ["Span", duration(tr.first, tr.last)], ["Hours in the chart", fmt(NH)]],
          note: TZL ? "Times in your time zone: z or the time zone button shows UTC" : "Times in UTC: z or the UTC button shows your time zone" });
      }
      const paths = SRC_PATHS.filter(Boolean);
      const shown = paths.slice(0, 12).map((p) => `<div class="rtip-file">${esc(basename(p))}</div>`).join("");
      return TIPS.card({ h: `${fmt(paths.length)} source file${paths.length === 1 ? "" : "s"}`,
        rows: [["Node", LS.node], ["Cluster", LS.cluster], ["Format", LS.format]],
        body: shown ? `<div class="rtip-files">${shown}${paths.length > 12 ? `<div class="m">and ${fmt(paths.length - 12)} more</div>` : ""}</div>` : null });
    });
    T("rpt-tile", (el) => tileTip(el.dataset.k));
  }
  function tileTip(k) {
    const st = D.stats || {};
    const lv = st.levels || {};
    const tr = st.time_range || {};
    const win = S.t ? levelsIn(S.t[0], S.t[1]) : null;
    const byBucket = (b) => {
      let n = 0;
      for (const r of BASE) if (r.bucket === b) n++;
      return n;
    };
    const peakRow = (b) => {
      const { peak } = peakArr(((D.timeline || {}).levels || {})[b] || []);
      return peak ? ["Busiest hour", peakText(peak)] : null;
    };
    if (k === "entries") {
      const order = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"];
      const names = [...order.filter((x) => lv[x]), ...Object.keys(lv).filter((x) => !order.includes(x) && lv[x])];
      return tipCard({ h: "Entries by level", rows: [...names.map((x) => [x, `${fmt(lv[x])} · ${pct(lv[x], ALL_ENTRIES)}`, x === "ERROR" || x === "FATAL" ? "e" : ""]),
        st.parse_errors ? ["Unparsed lines", fmt(st.parse_errors)] : null, ["From", exact(tr.first)], ["To", exact(tr.last)]],
        note: win ? "Window numbers leave muted groups out" : null });
    }
    if (k === "groups") {
      let exc = 0;
      for (const r of BASE) if (r.exc) exc++;
      const rows = BUCKETS.filter((b) => byBucket(b)).map((b) => [LV_TITLE[b] || b, fmt(byBucket(b)), b === "ERROR" ? "e" : ""]);
      rows.push(["With an exception", fmt(exc)]);
      if (MERGES.length) rows.push(["Drain3 merge proposals", fmt(MERGES.length)]);
      const total = st.group_count ?? BASE.length;
      if (BASE.length !== total) rows.push(["In this report", `${fmt(BASE.length)} of ${fmt(total)}`]);
      return tipCard({ h: "Signature groups by level", rows,
        note: "A group is one message template with its logger and exception chain. Coarse ignores the chain" });
    }
    if (k === "errors" || k === "warnings") {
      const b = k === "errors" ? "ERROR" : "WARN";
      const n = k === "errors" ? (lv.ERROR || 0) + (lv.FATAL || 0) : lv.WARN || 0;
      const rows = [["Share of the entries", pct(n, ALL_ENTRIES)], ["Groups", fmt(byBucket(b))]];
      if (k === "errors" && lv.FATAL) rows.unshift(["ERROR", fmt(lv.ERROR || 0), "e"], ["FATAL", fmt(lv.FATAL), "e"]);
      rows.push(peakRow(b));
      if (win) rows.push(["In the window", fmt(win[b] || 0)]);
      return tipCard({ h: k === "errors" ? "ERROR and FATAL entries" : "WARN entries", rows });
    }
    if (k === "unparsed") return tipCard({ h: "Unparsed lines", note: "Lines the format did not recognise as the start of an entry or as part of one" });
    if (k && k.startsWith("cmp")) {
      const what = { "cmp-new": `Groups in this set that ${OTHERNAME} does not have`, "cmp-changed": "Groups in both sets with a different count",
        "cmp-vanished": `Groups of ${OTHERNAME} with no entries here` }[k];
      const o = OTHER || {};
      const rows = [["Baseline", o.name || o.id], ["Its entries", o.entry_count != null ? fmt(o.entry_count) : null],
        ["Its span", o.first_ts ? `${ts16(o.first_ts)} → ${tsShort(o.last_ts)} ${TZ}` : null],
        ["This set", tr.first ? `${ts16(tr.first)} → ${tsShort(tr.last)} ${TZ}` : null], ["Unchanged", fmt(CMPN.unchanged)]];
      return tipCard({ h: what || `Compared with ${OTHERNAME}`, rows,
        note: "Groups of every level" + (CMPD && CMPD.min_level ? ` from ${CMPD.min_level}` : "") +
          (S.lv.size < BUCKETS.length ? "; the compare filter counts the levels the table shows" : "") + ". Pick new, changed or vanished in the filter bar" });
    }
    return "";
  }

  // ---------------------------------------------------------------- update
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
  function update() {
    const fk = focusKey(document.activeElement);
    const v = compute();
    renderTiles();
    renderControls(v);
    renderTable(v);
    renderUsers();
    if (TL && tlKey() !== TLK) renderTimeline();
    renderWindowLabel();
    renderZoomCtl();
    if (TL) showWindow(TL);
    renderDrawer();
    updateSticky();
    updateStuck();
    if ((S.sig || "") !== lastSig) {
      lastSig = S.sig || "";
      const id = lastSig || prevSig;
      if (id) requestAnimationFrame(() => {
        const tr = [...$("t-body").rows].find((x) => x.dataset.id === id);
        if (tr) tr.scrollIntoView({ block: "nearest" });
      });
    }
    if (S.sig) prevSig = S.sig;
    restoreFocus(fk);
  }
  let lastSig = "", prevSig = "", stuckRaf = 0;
  /** The "IDs & CLI" dialog: the set's identifiers, its source files and the commands to reproduce the view. */
  function renderIds() {
    const row = (k, v, copyText) => `<div class="ids-row"><span class="k">${k}</span><span class="v mono">${esc(v)}</span>${copyText ? iconCopy(copyText, k) : ""}</div>`;
    const facts = [["Set id", LS.id, LS.id], ["Name", LS.name], ["Format", LS.format], ["Node", LS.node], ["Cluster", LS.cluster],
      ["Unparsed lines", String((D.stats || {}).parse_errors || 0)], ["Generated", D.generated_at ? ts16(D.generated_at) + " " + TZ : ""], ["Tool", D.tool_version ? "ragnalog " + D.tool_version : ""]]
      .filter((x) => x[1]).map((x) => row(x[0], x[1], x[2])).join("");
    const paths = SRC_PATHS.filter(Boolean);
    const files = paths.length ? `<h3>${fmt(paths.length)} source file${paths.length === 1 ? "" : "s"}</h3><ul class="ids-files mono">` +
      paths.map((x) => `<li title="${esc(x)}">${esc(x)}</li>`).join("") + `</ul>` : "";
    const win = S.t ? ` --since ${HOURS[S.t[0]]} --until ${new Date(parseTs(HOURS[S.t[1]]) + 3.6e6).toISOString()}` : "";
    const cmds = [`ragnalog set ${SETREF}`, `ragnalog groups ${SETREF}${win} --limit 50`, `ragnalog stats ${SETREF}`, `ragnalog timeline ${SETREF}${win}`];
    if (CMPD) cmds.push(`ragnalog triage ${SETREF} --baseline ${OTHERREF}`);
    const sel = S.sig && lookup(S.sig);
    let grp = "";
    if (sel) {
      const sigs = [];
      if (sel.kind === "sig") sigs.push(["signature", sel.sig]);
      if (sel.kind !== "merged" && !sel.gone && sel.coarse !== sel.sig) sigs.push(["coarse signature", sel.coarse]);
      const sg = sel.members ? sel.members[0].sig : sel.sig;
      const set = sel.gone ? OTHERREF : SETREF;
      grp = `<h3>Open group</h3>` + sigs.map(([k, v]) => row(k, v, v)).join("") +
        `<div class="cli"><div><code>${esc(`ragnalog show ${set} ${sg}`)}</code>${copyBtn(`ragnalog show ${set} ${sg}`)}</div></div>`;
    }
    $("ids-body").innerHTML = `<div class="ids-facts">${facts}</div>${grp}${files}<h3>CLI</h3><div class="cli">` +
      cmds.map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div>`;
  }
  let stickyRO = null;
  function updateSticky() {
    const f = $("filters");
    const h = getComputedStyle(f).position === "sticky" ? f.offsetHeight : 0;
    document.documentElement.style.setProperty("--sticky-top", h + "px");
    // the bar's height changes when it sticks (scope line, hidden items): the table header follows
    if (!stickyRO && window.ResizeObserver) {
      stickyRO = new ResizeObserver(() => {
        const g = $("filters");
        document.documentElement.style.setProperty("--sticky-top", (getComputedStyle(g).position === "sticky" ? g.offsetHeight : 0) + "px");
      });
      stickyRO.observe(f);
    }
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
  async function copy(text, done = "Copied") {
    try {
      await navigator.clipboard.writeText(text);
      toast(done);
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
    toast(ok ? done : "Copy failed");
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
    b.setAttribute("aria-pressed", String(TZL));
    b.title = TZL ? `Times in your time zone (${TZ}); click to show UTC` : "Times in UTC; click to show your time zone";
  }
  /** Re-renders everything that prints a time in the newly shown zone. */
  function applyZone(local) {
    setZone(local);
    renderTzBtn();
    renderOverview();
    renderTimeline();
    update();
    renderFoot();
  }
  function toggleTz() {
    const next = !TZL;
    try { localStorage.setItem(TZ_KEY, next ? "local" : "utc"); } catch { /* storage unavailable */ }
    if (S.tz) commit({ tz: next ? "local" : "utc" }, { keepPage: true, replace: true });
    applyZone(next);
  }
  function toggleClip(geo, key) {
    CLIP[key] = !(geo && geo.clip);
    renderTimeline();
    const r = S.sig && lookup(S.sig);
    if (r && !r.gone) drawHourly(r);
  }
  function open(id) {
    CUR = id;
    const was = !!S.sig;
    commit({ sig: id }, { keepPage: true });
    if (!was) $("drawer").focus({ preventScroll: true });
  }
  /** Mute or unmute a row's signatures in the chart (all of them when any is not muted). */
  function toggleMute(r) {
    if (!r || r.gone) return;
    const mute = new Set(S.mute);
    const sigs = sigsOf(r);
    if (isMuted(r)) for (const s of sigs) mute.delete(s);
    else for (const s of sigs) mute.add(s);
    commit({ mute }, { keepPage: true, replace: true });
    toast(isMuted(r) ? "Muted in the chart" : "Back in the chart");
  }
  /** The group as Jira wiki markup for a ticket: what, how often, when, where, one example. */
  function ticketText(r) {
    // placeholders as <num>, and Jira's markup characters escaped so a template stays text
    const plain = (t) => String(t || "").replace(/<([A-Z][A-Z0-9_]*|\*)>/g, (_, n) => "<" + n.toLowerCase() + ">")
      .replace(/[\\{}\[\]*_|^~+!#-]/g, (c) => "\\" + c);
    const lines = [`h3. ${plain(r.tpl) || "(no message)"}`];
    const facts = [`*Level:* ${r.lvl || ""}`, `*Logger:* {{${r.logger || "-"}}}`];
    if (r.exc) facts.push(`*Exception:* {{${r.exc}}}`);
    lines.push(facts.join(" · "));
    const when = r.gone ? `only in ${OTHERNAME}` : `${fmt(r.count)} entries, ${ts16(r.first)} → ${ts16(r.last)} ${TZ}`;
    const win = S.t ? ` (${fmt(winOf(r).n)} in the window ${ts16(HOURS[S.t[0]])} → ${ts16(new Date(parseTs(HOURS[S.t[1]]) + 3.6e6).toISOString())} ${TZ})` : "";
    lines.push(`* *Seen:* ${when}${win}`);
    if (!r.gone) lines.push(`* *Users / threads:* ${fmt(r.users)} / ${fmt(r.threads)}`);
    if (r.cmp && r.cmp.cls !== "unchanged") lines.push(`* *vs ${OTHERNAME}:* ${abText(r.cmp)} (${r.cmp.cls})`);
    const sig = r.members ? r.members[0].sig : r.sig;
    lines.push(`* *Log set:* ${LS.name || LS.id}${LS.name ? ` ({{${LS.id}}})` : ""} · signature {{${sig}}}`);
    const id = exampleIds(r).find((x) => ENTRIES[x]);
    if (id) {
      const raw = fullText(ENTRIES[id]).replace(/\{noformat\}/gi, "{ noformat}").split("\n");
      lines.push("{noformat}", ...raw.slice(0, 60), ...(raw.length > 60 ? [`… ${raw.length - 60} more lines`] : []), "{noformat}");
    }
    lines.push(`_ragnalog show ${r.gone ? OTHERREF : SETREF} ${sig}_`);
    return lines.join("\n");
  }
  /** Close the drawer; focus goes back to the group's row (else the table), not the page. */
  function closeDrawer() {
    const id = S.sig;
    commit({ sig: "" }, { keepPage: true });
    const tr = [...$("t-body").rows].find((x) => x.dataset.id === id);
    (tr || $("table-wrap")).focus({ preventScroll: true });
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
    for (const id of ["report-details", "f-more"]) {
      const details = $(id);
      details.addEventListener("keydown", (e) => {
        if (e.key !== "Escape" || !details.open) return;
        e.stopPropagation();
        if (id === "f-more" && closePops()) return;
        details.open = false;
        details.querySelector("summary").focus();
      });
      document.addEventListener("click", (e) => {
        if (details.open && !details.contains(e.target)) details.open = false;
      });
    }
    $("f-more").addEventListener("toggle", () => { if (!$("f-more").open) closePops(); });

    $("tl-window").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      if (b.dataset.shift) shiftWindow(Number(b.dataset.shift));
      else if (b.dataset.zoom === "in") zoomIn();
      else if (b.dataset.zoom === "out") zoomOut();
      else if (b.dataset.zoom === "reset") commit({ z: null }, { keepPage: true });
      else if (b.dataset.sortFirst != null) commit({ sort: "first_seen" });
      else if (b.dataset.win != null) {
        const h = Number(b.dataset.win);
        if (h > 0 && ZOOMSEL) zoomTo([T0MS + (NH - h) * HOUR, T1MS]);
        else commit({ t: h > 0 ? [NH - h, NH - 1] : null, z: null });
      }
    });
    $("f-search").addEventListener("input", (e) => {
      clearTimeout(qTimer);
      const value = e.target.value;
      qTimer = setTimeout(() => commit({ q: value }, { replace: true }), 90);
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
    $("f-compare").addEventListener("click", (e) => {
      const b = e.target.closest("[data-cmp]");
      if (b) commit({ cmp: b.dataset.cmp });
    });
    $("f-hasexc").onclick = () => commit({ hx: !S.hx });
    $("f-coarse").onclick = () => commit({ view: S.view === "coarse" ? "sig" : "coarse", sig: "" });
    $("f-merged").onclick = () => commit({ view: S.view === "merged" ? "sig" : "merged", sig: "" });
    $("f-sort").onchange = (e) => commit({ sort: e.target.value });
    $("f-reset").onclick = resetAll;
    $("f-scope").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    $("t-empty").addEventListener("click", (e) => {
      const b = e.target.closest("[data-clear]");
      if (b) clearFilter(b.dataset.clear);
      else if (e.target.closest("[data-win]")) commit({ t: null, z: null });
    });
    $("f-logger").onclick = () => togglePop("f-logger-pop", renderLoggerPop);
    $("f-exc").onclick = () => togglePop("f-exc-pop", renderExcPop);
    for (const id of ["f-logger-pop", "f-exc-pop"]) {
      const pop = $(id);
      pop.addEventListener("input", (e) => {
        const k = e.target.dataset && e.target.dataset.pq;
        if (!k) return;
        popQuery[k] = e.target.value;
        if (k === "lg") renderLoggerPop(); else renderExcPop();
      });
      pop.addEventListener("click", (e) => {
        const t = e.target.closest("button");
        if (!t) return;
        if (t.dataset.tw != null) {
          const k = t.dataset.tw;
          if (TREE_OPEN.has(k)) TREE_OPEN.delete(k); else TREE_OPEN.add(k);
          renderLoggerPop();
        } else if (t.dataset.more != null) {
          MORE_OPEN.add(t.dataset.more);
          if (id === "f-logger-pop") renderLoggerPop(); else renderExcPop();
        } else if (t.dataset.lg != null) {
          closePops();
          commit({ lg: t.dataset.lg });
        } else if (t.dataset.ex != null) {
          closePops();
          commit({ ex: t.dataset.ex });
        }
      });
    }
    document.addEventListener("pointerdown", (e) => {
      if (!e.target.closest(".facet")) closePops();
    });
    $("t-head").addEventListener("click", (e) => {
      const th = e.target.closest("[data-sort]");
      if (th) commit({ sort: th.dataset.sort });
    });
    $("t-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tab]");
      if (b) commit({ tab: b.dataset.tab || "" }, { keepPage: true });
    });
    const uv = $("u-view");
    const toggleUser = (row) => commit({ user: S.user === row.dataset.user ? "" : row.dataset.user }, { keepPage: true, replace: true });
    uv.addEventListener("click", (e) => {
      const s = e.target.closest("[data-usort]");
      if (s) { USORT = s.dataset.usort; renderUsers(); return; }
      if (e.target.closest("[data-umore]")) { USHOW += 200; renderUsers(); return; }
      const g = e.target.closest("[data-open-group]");
      if (g) { open(g.dataset.openGroup); return; }
      if (e.target.closest("a, button")) return;
      const row = e.target.closest("tr[data-user]");
      if (row) toggleUser(row);
    });
    uv.addEventListener("keydown", (e) => {
      const row = e.target.closest && e.target.closest("tr[data-user]");
      if (!row || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      e.stopPropagation();
      toggleUser(row);
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
    $("d-mute").onclick = () => toggleMute(lookup(S.sig));
    $("d-ticket").onclick = () => {
      const r = lookup(S.sig);
      if (r) copy(ticketText(r), "Copied for a Jira ticket");
    };
    $("drawer-body").addEventListener("click", (e) => {
      const ub = e.target.closest("[data-users-all],[data-user-tab]");
      if (ub) {
        if (ub.dataset.userTab != null) {
          commit({ tab: "users", user: ub.dataset.userTab }, { keepPage: true });
          $("table-wrap").scrollIntoView({ block: "start" });
        } else {
          const id = S.sig;
          if (USERS_ALL.has(id)) USERS_ALL.delete(id); else USERS_ALL.add(id);
          redrawDrawer();
        }
        return;
      }
      const t = e.target.closest("[data-copy],[data-copy-raw],[data-open],[data-clip],[data-unfold],[data-all-lines],[data-raw-view],[data-retry-ex]");
      if (!t) return;
      if (t.dataset.retryEx != null) {
        EX_FAILED.clear();
        EX_ERR = "";
        drawnKey = null;
        renderDrawer();
        return;
      }
      if (t.dataset.rawView != null) toggleRaw(t);
      else if (t.dataset.allLines != null) showAllLines(t);
      else if (t.dataset.unfold != null) {
        const f = t.closest(".fold");
        const opened = f.classList.toggle("open");
        const n = +t.dataset.unfold;
        t.textContent = opened ? "hide frames" : `show ${fmt(n)} more frame${n === 1 ? "" : "s"}`;
      } else if (t.dataset.clip != null) toggleClip(DCHART, "d");
      else if (t.dataset.copy != null) copy(t.dataset.copy);
      else if (t.dataset.copyRaw != null) copy(fullText(ENTRIES[t.dataset.copyRaw] || {}));
      else if (t.dataset.open != null) open(t.dataset.open);
    });
    $("btn-theme").onclick = toggleTheme;
    if ($("btn-tz")) $("btn-tz").onclick = toggleTz;
    $("tl-clip").addEventListener("click", (e) => { if (e.target.closest("[data-clip]")) toggleClip(TL, "tl"); });
    $("tl-zoom").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      if (b.dataset.undo != null) undo();
      else if (b.dataset.zoomsel != null) toggleZoomSel();
    });
    $("tl-legend").addEventListener("click", (e) => {
      if (!e.target.closest("[data-grave-first]") || !GRAVE.length) return;
      const g = GRAVE[0];
      zoomTo([g.ms - 15 * 6e4, g.last + 15 * 6e4]);
    });
    $("tl-bins").addEventListener("change", (e) => {
      if (e.target.id === "tl-binsel") commit({ bin: e.target.value }, { keepPage: true, replace: true });
    });
    $("tl-legend").addEventListener("click", (e) => {
      if (e.target.closest("[data-unmute]")) return commit({ mute: new Set() }, { keepPage: true, replace: true });
      const b = e.target.closest("[data-hl]");
      if (!b) return;
      const k = b.dataset.hl;
      const shown = BUCKETS.filter((x) => TL_TOTALS[x] > 0);
      let hl = new Set(S.hl);
      if (e.shiftKey || e.altKey) hl = new Set(shown.filter((x) => x !== k));  // only this level
      else if (hl.has(k)) hl.delete(k);
      else hl.add(k);
      if (shown.every((x) => hl.has(x))) hl = new Set();  // never an empty chart
      commit({ hl }, { keepPage: true, replace: true });
    });
    window.ragnalogTheme = toggleTheme;
    renderThemeBtn();
    {
      // the drawer: the bar names the group once the headline scrolled away; the nav marks the section in view
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
    $("d-nav").addEventListener("click", (e) => {
      const b = e.target.closest("[data-jump]");
      const t = b && document.getElementById(b.dataset.jump);
      if (t && t.tagName === "DETAILS") t.open = true;
      if (!t) return;
      // scroll the drawer only: the table and the selected row stay where they are
      const body = $("drawer-body");
      const y = t.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 6;
      body.scrollTo({ top: y, behavior: "smooth" });
    });
    $("btn-data").onclick = () => {
      const blob = new Blob([JSON.stringify(D)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (LS.name || LS.id || "report") + ".report.json";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    };
    const help = $("help");
    $("btn-help").onclick = () => help.showModal();
    const idsDlg = $("ids");
    $("btn-ids").onclick = () => { renderIds(); idsDlg.showModal(); };
    idsDlg.addEventListener("click", (e) => {
      const t = e.target.closest("[data-copy]");
      if (t) copy(t.dataset.copy);
      else if (e.target === idsDlg) idsDlg.close();
    });
    // the drawer's "Identifiers & CLI" part also reveals the member signatures
    $("drawer-body").addEventListener("toggle", (e) => {
      if (!e.target.classList || !e.target.classList.contains("d-idcli")) return;
      IDS_OPEN = e.target.open;
      $("drawer-body").classList.toggle("show-ids", IDS_OPEN);
    }, true);
    window.addEventListener("scroll", () => {
      if (!stuckRaf) stuckRaf = requestAnimationFrame(() => { stuckRaf = 0; updateStuck(); });
    }, { passive: true });
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      const typing = t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement;
      // Enter on a focused button or link is that control's, not the page shortcut's
      if (e.key === "Enter" && t instanceof Element && t.closest("button, a, summary, [role=tab], [contenteditable]")) return;
      if (e.key === "Escape") {
        if (help.open || idsDlg.open) return;
        if (closePops()) return;
        if (typing) { t.blur(); return; }
        if (S.sig) closeDrawer();
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || help.open || idsDlg.open) return;
      switch (e.key) {
        case "/": {
          e.preventDefault();
          const box = $("f-search");
          if (box.offsetParent === null) window.scrollTo({ top: 0 }); // the stuck narrow bar hides it
          requestAnimationFrame(() => { box.focus(); box.select(); });
          break;
        }
        case "w": toggleWide(); break;
        case "j": e.preventDefault(); move(1); break;
        case "k": e.preventDefault(); move(-1); break;
        case "Enter": if (CUR && !S.sig) open(CUR); break;
        case "m": toggleMute(lookup(S.sig || CUR)); break;
        case "z": toggleTz(); toast(TZL ? `Times in ${TZ}` : "Times in UTC"); break;
        case "c": {
          const r = lookup(S.sig || CUR);
          // a merged row has no signature of its own: copy its first member's (as its CLI does)
          const sig = r && (r.kind === "sig" ? r.sig : r.kind === "merged" ? (r.sigs || [])[0] : r.id.replace(/^c:/, ""));
          if (sig) copy(sig); else if (r) toast("This row has no signature");
          break;
        }
        case "n": if ((S.p + 1) * PAGE < LIST.length) commit({ p: S.p + 1 }, { keepPage: true }); break;
        case "p": if (S.p > 0) commit({ p: S.p - 1 }, { keepPage: true }); break;
        case "[": shiftWindow(-1); break;
        case "]": shiftWindow(1); break;
        case "+": case "=": zoomIn(); break;
        case "-": zoomOut(); break;
        case "u": undo(); break;
        case "?": help.showModal(); break;
        default:
      }
    });
    let rz = 0;
    window.addEventListener("resize", () => {
      clearTimeout(rz);
      rz = setTimeout(() => {
        renderTimeline();
        if (S.sig && $("d-hourly")) {
          const r = lookup(S.sig);
          if (r && !r.gone) drawHourly(r);
        }
        updateSticky();
        updateStuck();
      }, 150);
    });
  }

  // ---------------------------------------------------------------- start
  if (S.theme === "dark" || S.theme === "light") document.documentElement.setAttribute("data-theme", S.theme);
  setZone(S.tz ? S.tz === "local" : TZL);
  renderTzBtn();
  registerTips();
  renderOverview();
  bind();
  renderTimeline();
  compute();
  if (S.sig && !S.hasPage) {
    const i = LIST.findIndex((r) => r.id === S.sig);
    if (i >= 0) S.p = Math.floor(i / PAGE);
  }
  update();
  const T1 = performance.now();
  let T2 = 0;
  function renderFoot() {
    if (!T2) return;
    const foot = $("foot");
    foot.textContent = `Generated ${ts16(D.generated_at)} ${TZ} by ragnalog ${D.tool_version || ""}`;
    $("report-provenance").textContent = `${D.schema} · ` +
      `${fmt(BASE.length)} groups, ${ENTRIES_URL ? "examples fetched per group" : fmt(Object.keys(ENTRIES).length) + " example entries"} · ` +
      `rendered in ${Math.round(T1 - T0)} ms (script), first paint ${Math.round(T2)} ms after navigation start`;
    foot.dataset.script = String(Math.round(T1 - T0));
    foot.dataset.paint = String(Math.round(T2));
  }
  requestAnimationFrame(() => setTimeout(() => {
    T2 = performance.now();
    renderFoot();
  }, 0));
  }

  // ---------------------------------------------------------------- loader
  const embedded = $("report-data");
  const text = embedded ? embedded.textContent : "";
  if (text && text.trim().startsWith("{")) {
    boot(JSON.parse(text));
  } else {
    const url = document.documentElement.dataset.reportUrl;
    if (!url) {
      fatal("no report data: #report-data is empty and <html data-report-url> is not set");
    } else {
      fetch(url, { headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`GET ${url}: HTTP ${r.status}`))))
        .then(boot)
        .catch(fatal);
    }
  }
})();
