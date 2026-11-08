/* atlas-log report: renders a report.v1 payload, embedded in #report-data or fetched from
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
  // times show in UTC or, toggled, in the browser's time zone: a viewer preference kept like the
  // theme (tz=local|utc in a link wins); windows in the hash stay UTC either way
  const TZ_KEY = "atlas-log-tz";
  let TZL = (() => { try { return localStorage.getItem(TZ_KEY) === "local"; } catch { return false; } })();
  let TZ = "UTC";
  /** The ISO time shifted into the shown zone (still printed with toISOString's layout). */
  function zoned(iso) {
    if (!TZL || !iso) return iso;
    const ms = parseTs(iso);
    return ms === ms ? new Date(ms - new Date(ms).getTimezoneOffset() * 6e4).toISOString() : iso;
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
  const PH = /&lt;([A-Z][A-Z0-9_]*|\*)&gt;/g;
  const REPEATED = /^\s*([^\w\s]){2,}\s*$|^\s*[^\w\s]{6,}\s*$/;
  const tplHtml = (t) =>
    t && REPEATED.test(t)
      ? `<span class="rep" title="${esc(t)}">(repeated characters)</span>`
      : esc(t).replace(PH, (m, n) => `<span class="ph">${n.toLowerCase()}</span>`);
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
      sort: sort && SORTS[sort] ? sort : "count",
      view: VIEW_NAMES.includes(v) && (v !== "merged" || MERGES.length) ? v : "sig",
      cmp: CMP && CMP_NAMES.includes(c) ? c : "all",
      p: Math.max(0, (parseInt(p.get("p"), 10) || 1) - 1),
      hasPage: p.has("p"),
      sig: p.get("sig") || "",
      t: parseWindow(p.get("t")),
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
  function niceTicks(max) {
    if (max <= 0) return [0, 1];
    const raw = max / 3;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
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
   *  set's first hour: the bins of hours [0, off) are partial. */
  const binOff = (k) => (k > 1 && 24 % k === 0 && NH ? hodOf(0) % k : 0);
  const binHours = (b, k, off) => [Math.max(0, b * k - off), Math.min(NH, (b + 1) * k - off)];
  function binRange(b, k, off = 0) {
    const [a, e] = binHours(b, k, off);
    const start = zoned(HOURS[a]);
    const end = zoned(new Date(parseTs(HOURS[e - 1]) + 3.6e6).toISOString());
    return start.slice(0, 10) + " " + start.slice(11, 16) + " – " +
      (end.slice(0, 10) === start.slice(0, 10) ? "" : end.slice(5, 10) + " ") + end.slice(11, 16) + " " + TZ;
  }
  // @hours-end
  const rectPath = (x, top, w, bottom) => `M${r1(x)} ${r1(bottom)}V${r1(top)}H${r1(x + w)}V${r1(bottom)}Z`;
  function roundTopPath(x, top, w, bottom) {
    const r = Math.min(4, w / 2, bottom - top);
    if (r < 1) return rectPath(x, top, w, bottom);
    return `M${r1(x)} ${r1(bottom)}V${r1(top + r)}Q${r1(x)} ${r1(top)} ${r1(x + r)} ${r1(top)}` +
      `H${r1(x + w - r)}Q${r1(x + w)} ${r1(top)} ${r1(x + w)} ${r1(top + r)}V${r1(bottom)}Z`;
  }

  /** Columns over the timeline hours (stacked when several series). Returns geometry. */
  function drawColumns(el, series, opts) {
    const W = Math.max(200, Math.floor(el.clientWidth || 600));
    const H = opts.height;
    // with an overlay, a lane under the axis shows it on its own scale (a small group's line would
    // hug the baseline); the line over the bars is drawn only when it rises above it
    const born = opts.births ? 10 : 0;
    // the lane's room is kept when no group is open, so opening one does not rescale the bars
    const lane = born + (opts.overlay || opts.ovLane ? 9 : 0);
    const padL = 42, padR = 6, padB = 22 + lane;
    const plotW = W - padL - padR;
    const k = chooseBin(NH, plotW);
    const off = binOff(k);
    const nb = Math.ceil((NH + off) / k);
    const binOf = (i) => Math.floor((i + off) / k);
    const binned = series.map((s) => {
      const out = new Float64Array(nb);
      for (let i = 0; i < NH; i++) if (s.data[i]) out[binOf(i)] += s.data[i];
      return out;
    });
    const totals = new Float64Array(nb);
    for (const arr of binned) for (let b = 0; b < nb; b++) totals[b] += arr[b];
    let maxT = 0;
    for (let b = 0; b < nb; b++) if (totals[b] > maxT) maxT = totals[b];
    const nz = [...totals].filter((x) => x > 0).sort((x, y) => x - y);
    const p95 = nz.length ? nz[Math.floor(0.95 * (nz.length - 1))] : 0;
    const spiky = p95 > 0 && maxT > 4 * p95;
    const clip = spiky && CLIP[opts.clipKey || "tl"] !== false;
    const padT = clip ? 18 : 8;
    const plotH = H - padT - padB;
    const ticks = niceTicks(clip ? p95 : maxT);
    const yMax = ticks[ticks.length - 1];
    const slot = plotW / nb;
    const bw = Math.min(24, Math.max(3, slot - GAP));
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
    // x ticks: smallest hour step whose labels are >= 56px apart
    const pxPerHour = slot / k;
    const step = [1, 2, 3, 6, 12, 24, 48, 72, 168, 336, 720].find((h) => h * pxPerHour >= 56) || 720;
    for (let i = 0; i < NH; i++) {
      const hod = hodOf(i);
      // a repeated hour (DST fall-back) gets no second tick
      const ok = step < 24 ? hod % step === 0 && (i === 0 || hodOf(i - 1) !== hod) : hod === 0 && newDay(i) && dayOf(i) % (step / 24) === 0;
      if (!ok) continue;
      const x = r1(padL + ((i + off) / k) * slot) + 0.5;
      if (x > W - padR - 16) continue;
      parts.push(`<line class="base" x1="${x}" x2="${x}" y1="${bottom0 + lane}" y2="${bottom0 + lane + 4}"/>`);
      parts.push(`<text class="axis" x="${x}" y="${H - 5}" text-anchor="middle">${hourLabel(i)}</text>`);
    }
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
      bornB = new Float64Array(nb);
      bornLv = new Int32Array(nb);
      for (let i = 0; i < NH; i++) {
        if (!opts.births.n[i]) continue;
        const b = binOf(i);
        bornB[b] += opts.births.n[i];
        if (opts.births.lv[i] > bornLv[b]) bornLv[b] = opts.births.lv[i];
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
      ovb = new Float64Array(nb);
      for (let i = 0; i < NH; i++) if (opts.overlay.data[i]) ovb[binOf(i)] += opts.overlay.data[i];
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
    parts.push(`<rect class="brush" x="0" y="${padT}" width="0" height="${plotH}" visibility="hidden"/>`);
    parts.push(`<rect class="hit" x="${padL}" y="0" width="${plotW}" height="${H}"/>`);
    parts.push(`</svg>`);
    el.innerHTML = parts.join("");
    const svg = el.firstElementChild;
    const geo = { svg, W, H, padL, padT, plotW, plotH, k, nb, slot, binned, totals, series, spiky, clip, ovb, bornB, bornLv, bottom0, born, top: !!opts.top, off, ovName: opts.overlay ? opts.overlay.name : "" };
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
      if (geo.top) {
        const [ha, he] = binHours(b, geo.k, geo.off);
        const top = topIn(ha, he - 1, 3);
        if (top.length) {
          rows.push(`<div class="tt-top">` + top.map(([r, c]) =>
            `<div class="tt-g"><b>${fmt(c)}</b><i class="sw-${r.bucket}"></i><span>${esc(r.tpl || r.sig)}</span></div>`).join("") + `</div>`);
        }
      }
      if (geo.bornB && geo.bornB[b]) {
        rows.push(`<div class="tt-r tt-o"><b>${fmt(geo.bornB[b])}</b><i class="sw-${bucketOf(geo.bornLv[b])}"></i><span>new group${geo.bornB[b] === 1 ? "" : "s"} (first seen; click the dot to list them)</span></div>`);
      }
      tip.innerHTML = `<div class="tt-h">${esc(binRange(b, geo.k, geo.off))}</div>${rows.join("")}`;
      tip.hidden = false;
      placeTip(e);
    });
    hit.addEventListener("pointerleave", () => {
      geo.band.setAttribute("visibility", "hidden");
      tip.hidden = true;
    });
  }
  function showWindow(geo, range) {
    const w = range || (S.t ? [(S.t[0] + geo.off) / geo.k, (S.t[1] + geo.off) / geo.k] : null);
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

  let TL = null;
  // per chart (timeline, drawer): null is automatic (clip when max > 4 × p95)
  const CLIP = { tl: null, d: null };
  const clipBtn = (geo) =>
    geo && geo.spiky
      ? `<button type="button" class="btn small toggle" data-clip aria-pressed="${geo.clip}" title="Clip the y-axis to the 95th percentile so spikes do not flatten the rest; clipped bars are labelled with their value">Clip spikes</button>`
      : "";
  const TL_TOTALS = {};
  let TLK = "";
  const tlKey = () => [...S.mute].sort().join(",") + "|" + [...S.hl].join(",") + "|" + TZ + "|" + overlayKey();
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
    return { key: ["f", S.view, S.q, S.lg, S.ex, S.hx, S.lv.size, rows.length].join("|"), name: `${fmt(rows.length)} listed group${rows.length === 1 ? "" : "s"}`, rows };
  }
  const overlayKey = () => { const o = overlaySpec(); return o ? o.key : ""; };
  const LV_TITLE = { ERROR: "ERROR and FATAL", DEBUG: "DEBUG and TRACE", OTHER: "Unknown level" };
  function renderLegend() {
    const shown = BUCKETS.filter((b) => (((D.timeline || {}).levels || {})[b] || []).some((v) => v > 0));
    let html = shown.map((b) => {
      const on = !S.hl.has(b);
      return `<button type="button" class="lg-item" data-hl="${b}" aria-pressed="${on}" title="${LV_TITLE[b] || b}: click to ${on ? "hide it in" : "show it in"} the chart; Shift-click to show only it"><i class="sw sw-${b}"></i>${b} ${fmt(TL_TOTALS[b] || 0)}</button>`;
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
      html += `<span class="lg-sel" title="${what}: entries per hour as a line on the same scale (when it rises above the baseline) and as the shaded lane under the axis, darkest at its own busiest hour"><i class="sw-sel"></i>${esc(ov.name.charAt(0).toUpperCase() + ov.name.slice(1))}</span>`;
    }
    $("tl-legend").innerHTML = html;
  }
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
    renderLegend();
    const series = BUCKETS.filter((b) => TL_TOTALS[b] > 0 && !S.hl.has(b)).map((b) => ({
      key: b, name: b, cls: "s-" + b, data: levels[b],
    }));
    const ov = overlaySpec();
    let overlay = null;
    if (ov) {
      const data = new Float64Array(NH);
      for (const row of ov.rows) for (const [i, n] of row.hourly) if (i >= 0 && i < NH) data[i] += n;
      overlay = { data, name: ov.name };
    }
    TL = drawColumns(el, series, { height: 200, label: "Entries per hour by level", overlay, births: BIRTHS, top: true, ovLane: true });
    const k = TL.k;
    $("tl-bin").textContent = k === 1 ? "hour" : k === 24 ? "day" : k + " hours";
    $("tl-clip").innerHTML = clipBtn(TL);
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
      if (!d.moved) {
        const y = e.clientY - geo.svg.getBoundingClientRect().top;
        if (geo.bornB && geo.bornB[d.b0] && y > geo.bottom0 && y <= geo.bottom0 + geo.born + 2) {
          const [ha, he] = binHours(d.b0, geo.k, geo.off);
          commit({ t: [ha, he - 1], sort: "first_seen" });
          return;
        }
        if (S.t) commit({ t: null });
        return;
      }
      const a = Math.min(d.b0, d.b1), b = Math.max(d.b0, d.b1);
      commit({ t: [binHours(a, geo.k, geo.off)[0], binHours(b, geo.k, geo.off)[1] - 1] });
    };
    hit.addEventListener("pointerup", end);
    hit.addEventListener("pointercancel", end);
  }
  /** Quick windows ending at the last hour of the set: those shorter than the set. */
  const PRESETS = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"]].filter(([h]) => h < NH);
  const hoursText = (h) => (h < 48 ? h + " h" : (h / 24).toFixed(1).replace(/\.0$/, "") + " d");
  /** Move the window by its own width (dir -1 / +1), clamped to the set. */
  function shiftWindow(dir) {
    if (!S.t) return;
    const [a, b] = S.t, w = b - a + 1;
    const na = Math.max(0, Math.min(NH - w, a + dir * w));
    if (na !== a) commit({ t: [na, na + w - 1] }, { keepPage: false });
  }
  function renderWindowLabel() {
    const el = $("tl-window");
    if (!NH) {
      el.innerHTML = "";
      return;
    }
    const presets = PRESETS.map(([h, label]) => {
      const on = S.t && S.t[0] === NH - h && S.t[1] === NH - 1;
      return `<button type="button" data-win="${h}" aria-pressed="${!!on}" title="The last ${label} of the set">${label}</button>`;
    }).join("");
    const seg = presets ? `<span class="seg win-seg" role="group" aria-label="Quick windows"><span class="lbl">Last</span>${presets}</span>` : "";
    if (!S.t) {
      el.innerHTML = `<span class="muted">Drag across the chart to narrow the table to a time window</span>${seg}`;
      return;
    }
    const [a, b] = S.t;
    const end = new Date(parseTs(HOURS[b]) + 3.6e6).toISOString();
    const lv = levelsIn(a, b);
    let total = 0;
    for (const k of BUCKETS) total += lv[k];
    const what = [`${fmt(total)} entries`];
    if (lv.ERROR) what.push(`<span class="w-err">${fmt(lv.ERROR)} errors</span>`);
    if (lv.WARN) what.push(`${fmt(lv.WARN)} warnings`);
    if (BIRTHS) {
      let born = 0;
      for (let h = a; h <= b; h++) born += BIRTHS.n[h];
      if (born) {
        what.push(S.sort === "first_seen"
          ? `${fmt(born)} first seen here (listed first)`
          : `<button type="button" class="btn small link" data-sort-first title="Sort by first seen: the groups that started in the window come first">${fmt(born)} first seen here</button>`);
      }
    }
    el.innerHTML = `<span class="win-what">Window <b>${esc(ts16(HOURS[a]))} → ${esc(tsShort(end))}</b> ${TZ} (${hoursText(b - a + 1)}) · ${what.join(" · ")}</span>` +
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
      const tip = wc ? (c === "c-delta" ? `vs ${OTHERNAME}: ${wc[1]}` : wc[1]) : tip0;
      const sorted = key && S.sort === key ? ` aria-sort="${key === "template" || key === "logger" || key === "first_seen" ? "ascending" : "descending"}"` : "";
      return `<th class="${c}${key ? " sortable" : ""}"${key ? ` data-sort="${key}"` : ""}${sorted} title="${esc(tip || label)}">${esc(label)}</th>`;
    }).join("") + "</tr>";
  }
  /** ``html`` (escaped text and tags) with every match of the search text marked. */
  function markHtml(html, text) {
    if (!text) return html;
    const needle = esc(text).toLowerCase();
    return html.split(/(<[^>]*>)/).map((part) => {
      if (part.startsWith("<")) return part;
      const low = part.toLowerCase();
      let out = "", i = 0, j;
      while ((j = low.indexOf(needle, i)) >= 0) {
        out += part.slice(i, j) + "<mark>" + part.slice(j, j + needle.length) + "</mark>";
        i = j + needle.length;
      }
      return out + part.slice(i);
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
      ? `<small class="of spk" title="${S.t ? "Rate in the window over the rate outside it" : "Busiest hour over the average hour of the set"}">×${esc(compact(spikeOf(r)))}</small>`
      : S.t ? `<small class="of">of ${esc(compact(r.count))}</small>` : "";
    const last = S.t ? winOf(r).last : r.last ? { iso: r.last } : null;
    const cTitle = S.t ? ` title="${fmt(n)} in the window of ${fmt(r.count)} in total"` : "";
    return `<tr data-id="${esc(r.id)}" class="${cls}" tabindex="-1">` +
      `<td class="c-count"${cTitle}><div class="cnt"><span class="num">${fmt(n)}${of}</span>${track}</div></td>` +
      `<td class="c-lv">${badge(r.lvl, r.level)}</td>` +
      `<td class="c-logger" title="${esc(r.logger)}">${esc(shortLogger(r.logger))}</td>` +
      `<td class="c-tpl" title="${esc(r.tpl)}"><span class="tpl">${multi}${isMuted(r) ? `<span class="badge-mute" title="Left out of the chart (m)">muted</span>` : ""}${markHtml(tplHtml(r.tpl), Q.text)}</span><span class="sub">${markHtml(esc(shortLogger(r.logger)), Q.text)}${r.exc ? " · " + markHtml(esc(shortExc(r.exc)), Q.text) : ""}</span></td>` +
      `<td class="c-exc" title="${esc(r.exc)}">${r.exc ? `<span class="xc"><span class="xp">${esc(r.exc.slice(0, r.exc.lastIndexOf(".") + 1))}</span><span class="xs">${esc(shortExc(r.exc))}</span></span>` : ""}</td>` +
      `<td class="c-spark">${spark(r)}</td>` +
      `<td class="c-last" title="${esc(seenTitle(last))}">${esc(seenText(last))}</td>` +
      `<td class="c-ut">${r.gone ? "" : fmt(r.users) + " / " + fmt(r.threads)}</td>` +
      (CMP ? `<td class="c-delta"><span class="ab" title="${esc(abText(r.cmp))}">${abShort(r.cmp)}</span>${deltaChip(r.cmp)}</td>` : "") +
      `</tr>`;
  }
  function pagerHtml(pages) {
    if (pages <= 1) return "";
    return `<button type="button" class="btn small" data-page="${S.p - 1}" ${S.p <= 0 ? "disabled" : ""}>‹ Prev</button>` +
      `<span>Page ${S.p + 1} of ${pages}</span>` +
      `<button type="button" class="btn small" data-page="${S.p + 1}" ${S.p >= pages - 1 ? "disabled" : ""}>Next ›</button>`;
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
    if (!page.length) {
      if (!v.rows.length) empty.textContent = "This report has no groups.";
      else if (S.cmp === "vanished" && S.t) {
        empty.innerHTML = `<div>Vanished groups have no entries in this set, so none falls in a time window.</div>` +
          `<button type="button" class="btn" data-win="0">Show the whole set</button>`;
      } else {
        const chips = activeFilters().map(([k, label]) =>
          `<button type="button" class="fchip" data-clear="${k}" title="Remove this filter">${esc(label)} <span aria-hidden="true">×</span></button>`).join("");
        empty.innerHTML = `<div>No groups match the filters.</div>` +
          (chips ? `<div class="fchips">${chips}</div>` : "") +
          `<button type="button" class="btn" data-clear="all">Reset filters</button>`;
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
  const CLEAR = { q: { q: "" }, lv: null, lg: { lg: "" }, ex: { ex: "" }, hx: { hx: false }, cmp: { cmp: "all" }, t: { t: null } };
  function resetAll() {
    clearTimeout(qTimer);
    $("f-search").value = "";
    commit({ q: "", lv: new Set(DEFAULT_LV), lg: "", ex: "", hx: false, sort: "count", view: "sig", cmp: "all", t: null, sig: "" });
  }
  function clearFilter(k) {
    if (k === "all") return resetAll();
    if (k === "lv") return commit({ lv: new Set(DEFAULT_LV) });
    if (k === "q") $("f-search").value = "";
    if (CLEAR[k]) commit(CLEAR[k]);
  }
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
      `<button type="button" class="chip" data-lv="${b}" aria-pressed="${S.lv.has(b)}" title="${b === "ERROR" ? "ERROR and FATAL" : b === "DEBUG" ? "DEBUG and TRACE" : b === "OTHER" ? "Unknown level / unparsed" : b}">` +
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
    return `<div class="tn" style="--d:${depth}">${twisty}<button type="button" class="tl${S.lg === full ? " on" : ""}" data-lg="${esc(full)}" title="${esc(full)}"><span class="t">${esc(label)}</span><span class="n">${fmt(n)}</span></button></div>`;
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
        `<div class="tn"><button type="button" class="tl${S.ex === t ? " on" : ""}" data-ex="${esc(t)}" title="${esc(t)}"><span class="t">${esc(shortExc(t))} <span class="muted">${esc(t.slice(0, Math.max(0, t.lastIndexOf("."))))}</span></span><span class="n">${fmt(n)}</span></button></div>`).join("") +
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
  const sec = (title, body) => `<section class="d-sec"><h3>${esc(title)}</h3>${body}</section>`;
  const secId = (id, title, body) => `<section class="d-sec" id="${id}"><h3>${esc(title)}</h3>${body}</section>`;
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
  function barList(items, labelFn, total, noun) {
    if (!items || !items.length) return `<div class="muted">—</div>`;
    const max = Math.max(...items.map((x) => x.count));
    const min = Math.min(...items.map((x) => x.count));
    const list = barRows(items, labelFn, max);
    if (items.length >= 3 && (max - min) / max < 0.1) {
      const first = items[0].value == null ? "(none)" : String(items[0].value);
      return `<details class="flat"><summary>${fmt(Math.max(total || 0, items.length))} ${esc(noun || "values")}, evenly spread, e.g. ` +
        `<span title="${esc(first)}">${esc(labelFn ? labelFn(first) : first)}</span></summary>${list}</details>`;
    }
    return list;
  }
  function barRows(items, labelFn, max) {
    return `<div class="blist">` + items.map((x) => {
      const full = x.value == null ? "(none)" : String(x.value);
      return `<span class="l" title="${esc(full)}">${esc(labelFn ? labelFn(full) : full)}</span>` +
        `<span class="b"><i style="width:${r1(Math.max(2, (100 * x.count) / max))}%"></i></span>` +
        `<span class="v">${fmt(x.count)}</span>`;
    }).join("") + `</div>`;
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
  // @entry-card: msgHtml … showAllLines (tests/report/test_entry_card.py runs this part with node)
  function msgHtml(message, vars) {
    message = message || "";
    let out = "", pos = 0;
    for (const v of vars || []) {
      if (!v) continue;
      const i = message.indexOf(v, pos);
      if (i < 0) continue;
      out += esc(message.slice(pos, i)) + `<mark>${esc(v)}</mark>`;
      pos = i + v.length;
    }
    return out + esc(message.slice(pos));
  }
  // Java stack traces: tokenize the plain text, escape each piece.
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
  const BODY_LINES = 500; // a longer body shows its first BODY_LINES lines and a "show all" button
  const FULL_BODIES = new Set(); // ids of the entries whose every line is shown
  const RAW_VIEW = new Set(); // ids of the entries shown as their exact text
  /** The exact text of an entry as in the log file: banner lines before it, then its raw. */
  const fullText = (e) => {
    const pre = e.preamble || (e.context && typeof e.context === "object" ? e.context.preamble : "") || "";
    return (pre ? pre + "\n" : "") + (e.raw || "");
  };
  /** Web app: the full raw entry as plain text (opens in a new tab). */
  const rawUrl = (id) => (ENTRIES_URL && LS.id ? `/api/sets/${encodeURIComponent(LS.id)}/entries/${encodeURIComponent(id)}/raw` : "");
  /** Lines after the header line of a raw entry, trailing white space dropped. */
  function bodyLines(raw) {
    const nl = raw.indexOf("\n");
    return nl < 0 ? [] : raw.slice(nl + 1).trimEnd().split("\n");
  }
  /** Banner lines attached before the entry (report.v1 `preamble`, entries.v1 `context.preamble`). */
  function preambleHtml(e) {
    const text = e.preamble || (e.context && typeof e.context === "object" ? e.context.preamble : "") || "";
    if (!text) return "";
    const lines = String(text).split("\n");
    return `<div class="e-pre"><span class="k">banner line${lines.length === 1 ? "" : "s"} before this entry</span>` +
      lines.map((ln) => `<div class="ln">${esc(ln)}</div>`).join("") + `</div>`;
  }
  function entryHtml(id, e) {
    const raw = e.raw || "";
    const all = bodyLines(raw);
    const cut = all.length > BODY_LINES && !FULL_BODIES.has(id);
    const body = cut ? all.slice(0, BODY_LINES) : all;
    const meta = [["", tsFull(e.ts) + " " + TZ], ["thread", e.thread], ["user", e.user], ["request", e.request_id], ["ip", e.ip], ["url", e.url]]
      .filter((x) => x[1]);
    return `<article class="entry lvb-${bucketOf(levelNum(e.level_name))}"><div class="e-meta">${badge(e.level_name, levelNum(e.level_name))}` +
      meta.map(([k, v]) => `<span>${k ? `<span class="k">${k}</span> ` : ""}<span class="v">${esc(v)}</span></span>`).join("") +
      `</div>` + (RAW_VIEW.has(id) ? `<pre class="e-raw">${esc(fullText(e))}</pre>` : `${preambleHtml(e)}<div class="e-msg">${msgHtml(e.message, e.variables)}</div>` +
      (body.length && body.some((l) => l.trim()) ? `<pre class="e-body trace">${traceHtml(body)}</pre>` : "") +
      (cut ? `<div class="e-more"><button type="button" class="btn small" data-all-lines="${esc(id)}">show all ${fmt(all.length)} lines</button><span class="muted">first ${fmt(BODY_LINES)} shown</span></div>` : "")) +
      `<div class="e-foot"><span class="mono" title="${esc(srcPath(e.source ?? e.source_path))}">${esc(basename(srcPath(e.source ?? e.source_path)))}:${esc(e.source_line)}</span>` +
      (e.raw_truncated ? `<span class="muted">${ENTRIES_URL ? "loading the full entry…" : `cut to ${fmt(raw.length)} characters in this file; all of it: atlas-log context SET ${esc(id)} --before 0 --after 0 --max-raw 0`}</span>` : "") +
      `<span class="grow"></span>` +
      `<button type="button" class="btn small" data-raw-view="${esc(id)}" aria-pressed="${RAW_VIEW.has(id)}" title="Show the exact text of the entry as in the log file, every line">${RAW_VIEW.has(id) ? "Formatted" : "Raw"}</button>` +
      (rawUrl(id) ? `<a class="btn small" href="${esc(rawUrl(id))}" target="_blank" rel="noopener" title="The full entry as plain text in a new tab">Open raw ↗</a>` : "") +
      `<button type="button" class="btn small" data-copy-raw="${esc(id)}">Copy raw</button></div></article>`;
  }
  /** "show all N lines": render the rest of the entry's body in place (the raw is already loaded). */
  /** "Raw" / "Formatted": redraw one card in the other view. */
  function toggleRaw(btn) {
    const id = btn.dataset.rawView;
    if (RAW_VIEW.has(id)) RAW_VIEW.delete(id); else RAW_VIEW.add(id);
    const card = btn.closest(".entry");
    if (card && ENTRIES[id]) card.outerHTML = entryHtml(id, ENTRIES[id]);
  }
  function showAllLines(btn) {
    const id = btn.dataset.allLines;
    const e = ENTRIES[id];
    FULL_BODIES.add(id);
    const card = btn.closest(".entry");
    const pre = card && card.querySelector(".e-body");
    if (pre && e) pre.innerHTML = traceHtml(bodyLines(e.raw || ""));
    const more = btn.closest(".e-more");
    if (more) more.remove();
  }
  // @entry-card-end
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
  function drawerHtml(r, inList) {
    const out = [];
    if (!inList) out.push(`<div class="d-banner">This group is not in the current filter.</div>`);
    const what = r.kind === "merged" ? `Drain3 cluster ${r.cluster ? r.cluster.cluster_id : ""}` : r.kind === "coarse" ? "Coarse group" : "";
    out.push(`<div class="d-head">${badge(r.lvl, r.level)}${r.cmp ? deltaChip(r.cmp) : ""}` +
      (r.members ? `<span class="muted">${esc(what)} · ${r.members.length} signatures</span>` : "") +
      (r.gone ? `<span class="muted">only in ${esc(OTHERNAME)}</span>` : "") +
      `<span class="d-acts">` +
      (r.gone ? "" : `<button type="button" class="btn small toggle" data-mute aria-pressed="${isMuted(r)}" title="Leave this group out of the chart and the window totals, so what it hides shows (m)">Mute in chart</button>`) +
      `<button type="button" class="btn small" data-ticket title="Copy the group as Jira wiki markup: template, counts, first and last seen, the set and an example">Copy for ticket</button>` +
      `</span></div>`);
    out.push(`<h2 class="d-tpl" tabindex="-1">${tplHtml(r.tpl)}</h2>`);
    if (r.logger) out.push(`<div class="d-sub mono">${esc(r.logger)}</div>`);
    if (r.exc) out.push(`<div class="d-sub mono">${esc(r.exc)}</div>`);
    const ids = [];
    if (r.kind === "sig") ids.push(["sig", r.sig, "signature"]);
    if (r.kind !== "merged" && !r.gone && r.coarse !== r.sig) ids.push(["coarse", r.coarse, "coarse signature"]);
    if (ids.length) {
      out.push(`<div class="d-ids mono">` + ids.map(([k, v, w]) => `<span><span class="k">${k}</span> ${esc(v)}${iconCopy(v, w)}</span>`).join("") + `</div>`);
    }
    let stats = "";
    if (!r.gone) {
      let st;
      if (S.t) {
        const w = winOf(r);
        st = [["In window", fmt(w.n), `${fmt(w.n)} of ${fmt(r.count)} entries`],
          ["First in window", seenText(w.first, true) || "—", seenTitle(w.first)], ["Last in window", seenText(w.last, true) || "—", seenTitle(w.last)]];
      } else st = [["Entries", fmt(r.count)], ["First seen", ts16(r.first)], ["Last seen", ts16(r.last)]];
      st.push([r.members ? "Users (≥)" : "Users", fmt(r.users)], [r.members ? "Threads (≥)" : "Threads", fmt(r.threads)], ["Sources", fmt(r.nsrc)]);
      stats += `<dl class="stats">` + st.map(([k, v, t]) => `<div${t ? ` title="${esc(t)}"` : ""}><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("") + `</dl>`;
      if (S.t) stats += `<div class="muted small">Whole set: ${esc(fmt(r.count))} entries, ${esc(ts16(r.first))} → ${esc(ts16(r.last))} ${TZ}. Users, threads and sources cover the whole set.</div>`;
    }
    if (r.cmp) {
      const d = r.cmp.d;
      let note = "";
      if (d && d.first_seen_a) note = `<div class="muted">In ${esc(OTHERNAME)}: ${esc(ts16(d.first_seen_a))} → ${esc(ts16(d.last_seen_a))} ${TZ}</div>`;
      stats += `<div class="cmp-row"><span class="muted">vs ${esc(OTHERNAME)}</span><span class="ab">${abText(r.cmp)}</span>${deltaChip(r.cmp)}</div>${note}`;
    }
    out.push(`<section class="d-sec" id="d-s-stats">${stats}</section>`);
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
      out.push(secId("d-s-hourly", "Entries per hour" + (S.t ? " (window shaded)" : ""), `<div class="d-chart" id="d-hourly"></div>`));
      const src = r.members || [r];
      const tops = [
        ["Top users", mergeTops(src, "top_users"), null, r.users, "users"],
        ["Top threads", mergeTops(src, "top_threads"), null, r.threads, "threads"],
        ["Sources", mergeTops(src, "sources"), basename, r.nsrc, "sources"],
      ];
      out.push(`<section class="d-sec tops" id="d-s-who">` + tops.map(([t, items, fn, total, noun]) => `<div><h3>${t}</h3>${barList(items, fn, total, noun)}</div>`).join("") + `</section>`);
      const moves = movesWith(r, 6);
      if (moves.length) {
        out.push(secId("d-s-rel", "Moves with", `<p class="muted small">Groups whose entries per hour rise and fall with this one (correlation r over the whole set): often the other half of the story.</p>` + movesList(moves)));
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
    const cmds = [`atlas-log show ${set} ${sig}`, `atlas-log entries ${set} --signature ${sig} --limit 50`];
    if (r.kind === "coarse") cmds.push(`atlas-log query ${set} "select signature, count, template from groups where coarse_signature = '${r.coarse}' order by count desc"`);
    if (r.kind === "merged") cmds.push(`atlas-log query ${set} "select signature, count, template from groups where signature in (${r.sigs.map((s) => `'${s}'`).join(", ")}) order by count desc"`);
    out.push(secId("d-s-cli", "CLI", `<div class="cli">` + cmds.map((c) => `<div><code>${esc(c)}</code>${copyBtn(c)}</div>`).join("") + `</div>`));
    return out.join("");
  }
  function drawHourly(r) {
    const el = $("d-hourly");
    if (!el || !NH) return;
    const dense = new Float64Array(NH);
    for (const [i, n] of r.hourly) dense[i] = n;
    DCHART = drawColumns(el, [{ key: "blue", name: "entries", cls: "s-blue", data: dense }], { height: 150, label: "Entries per hour", clipKey: "d" });
    const h3 = el.parentElement && el.parentElement.querySelector("h3");
    const k = DCHART.k;
    if (h3) h3.innerHTML = esc("Entries per " + (k === 1 ? "hour" : k === 24 ? "day" : k + " hours") + (S.t ? " (window shaded)" : "")) + clipBtn(DCHART);
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
    $("d-pos").textContent = idx >= 0 ? `${fmt(idx + 1)} of ${fmt(LIST.length)}` : `– of ${fmt(LIST.length)}`;
    const key = S.view + "|" + S.sig + "|" + (S.t || "") + "|" + (idx >= 0) + "|" + TZ + "|" + [...S.mute].sort().join(",");
    if (key === drawnKey) return;
    const sameGroup = drawnKey && drawnKey.split("|")[1] === S.sig;
    drawnKey = key;
    const r = lookup(S.sig);
    const body = $("drawer-body");
    const top = body.scrollTop;
    body.innerHTML = r ? drawerHtml(r, idx >= 0) : `<div class="empty">Group ${esc(S.sig)} is not in this report.</div>`;
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
    $("set-id").textContent = LS.name ? LS.id : "";
    const nsrc = (LS.sources || []).length;
    const opts = D.options || {};
    const total = st.group_count ?? BASE.length;
    $("set-meta").textContent = [
      LS.format,
      tr.first ? `${ts16(tr.first)} → ${tsShort(tr.last)} ${TZ} (${duration(tr.first, tr.last)})` : "no entries",
      `${fmt(nsrc)} source file${nsrc === 1 ? "" : "s"}`,
      st.parse_errors ? "" : "0 unparsed",
      opts.truncated_groups > 0 ? `showing ${fmt(BASE.length)} of ${fmt(BASE.length + opts.truncated_groups)} groups` : "",
    ].filter(Boolean).join(" · ");
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
    const tk = WKEY + "|" + [...S.mute].sort().join(",") + "|" + S.lv.size;
    if (tilesKey === tk) return;
    tilesKey = tk;
    const st = D.stats || {};
    const lv = st.levels || {};
    const opts = D.options || {};
    const total = st.group_count ?? BASE.length;
    const groupsNote = BASE.length !== total
      ? `${fmt(BASE.length)} in this report` + (opts.min_level ? ` (${opts.min_level}+)` : "")
      : "";
    const tiles = [
      ["Entries", fmt(st.entry_count ?? LS.entry_count ?? 0), ""],
      ["Groups", fmt(total), groupsNote],
      ["Errors", fmt((lv.ERROR || 0) + (lv.FATAL || 0)), lv.FATAL ? `incl. ${fmt(lv.FATAL)} FATAL` : ""],
      ["Warnings", fmt(lv.WARN || 0), ""],
    ];
    if (S.t) {
      const wl = levelsIn(S.t[0], S.t[1]);
      let we = 0;
      for (const k of BUCKETS) we += wl[k];
      let wg = 0;
      for (const r of BASE) if (winOf(r).n > 0) wg++;
      const win = [we, wg, wl.ERROR, wl.WARN];
      for (let i = 0; i < 4; i++) tiles[i][3] = `${fmt(win[i])} in the window`;
    }
    if (st.parse_errors) tiles.push(["Unparsed", fmt(st.parse_errors), ""]);
    let html = tiles.map(([l, v, n, w]) => `<div class="tile"><div class="label">${l}</div><div class="value">${esc(v)}</div>` +
      (w ? `<div class="note win-note">${esc(w)}</div>` : "") + (n ? `<div class="note">${esc(n)}</div>` : "") + `</div>`).join("");
    if (CMPD) {
      const mini = [["new", CMPN.new, (CMPD.new || []).length], ["changed", CMPN.changed, (CMPD.changed || []).length],
        ["vanished", CMPN.vanished, (CMPD.vanished || []).length]];
      const partial = mini.filter(([, n, shown]) => shown < n).map(([k, n, shown]) => `${k}: shown ${fmt(shown)} of ${fmt(n)}`);
      html += `<div class="tile wide cmp-tile"><div class="label">Compared with ${esc(OTHERNAME)}${CMPD.min_level ? ` (${esc(CMPD.min_level)}+)` : ""}${baselineNote()}</div>` +
        `<div class="minis">` + mini.map(([k, n]) => `<div><div class="value">${fmt(n)}</div><div class="note">${k}</div></div>`).join("") + `</div>` +
        `<div class="note">${fmt(CMPN.unchanged)} unchanged · groups of every level${S.lv.size < BUCKETS.length ? `; the filter below counts the levels it shows` : ""}${partial.length ? " · table " + esc(partial.join(", ")) : ""}</div></div>`;
    }
    $("tiles").innerHTML = html;
  }

  // ---------------------------------------------------------------- update
  // re-renders replace the chips, pagers and window buttons: put the focus back on the
  // control that had it (same id, or same tag and data-* attributes)
  function focusKey(el) {
    if (!(el instanceof HTMLElement) || el === document.body) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    const attrs = [...el.attributes].filter((a) => a.name.startsWith("data-"))
      .map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("");
    return attrs ? el.tagName.toLowerCase() + attrs : "";
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
    if (TL && tlKey() !== TLK) renderTimeline();
    renderWindowLabel();
    if (TL) showWindow(TL);
    renderDrawer();
    updateSticky();
    restoreFocus(fk);
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
  function renderTzBtn() {
    const b = $("btn-tz");
    if (!b) return;
    b.textContent = TZ;
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
    if (!was) $("d-close").focus({ preventScroll: true });
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
    lines.push(`_atlas-log show ${r.gone ? OTHERREF : SETREF} ${sig}_`);
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

  function bind() {
    $("tl-window").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      if (b.dataset.shift) shiftWindow(Number(b.dataset.shift));
      else if (b.dataset.sortFirst != null) commit({ sort: "first_seen" });
      else if (b.dataset.win != null) {
        const h = Number(b.dataset.win);
        commit({ t: h > 0 ? [NH - h, NH - 1] : null });
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
    $("t-empty").addEventListener("click", (e) => {
      const b = e.target.closest("[data-clear]");
      if (b) clearFilter(b.dataset.clear);
      else if (e.target.closest("[data-win]")) commit({ t: null });
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
    $("d-prev").onclick = () => move(-1);
    $("d-next").onclick = () => move(1);
    $("drawer-body").addEventListener("click", (e) => {
      const t = e.target.closest("[data-copy],[data-copy-raw],[data-open],[data-clip],[data-unfold],[data-all-lines],[data-raw-view],[data-retry-ex],[data-mute],[data-ticket]");
      if (!t) return;
      if (t.dataset.mute != null) return toggleMute(lookup(S.sig));
      if (t.dataset.ticket != null) {
        const r = lookup(S.sig);
        if (r) copy(ticketText(r), "Copied for a Jira ticket");
        return;
      }
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
    window.atlasTheme = toggleTheme;
    renderThemeBtn();
    $("d-nav").addEventListener("click", (e) => {
      const b = e.target.closest("[data-jump]");
      const t = b && document.getElementById(b.dataset.jump);
      if (t) t.scrollIntoView({ block: "start", behavior: "smooth" });
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
    document.addEventListener("keydown", (e) => {
      const t = e.target;
      const typing = t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement;
      // Enter on a focused button or link is that control's, not the page shortcut's
      if (e.key === "Enter" && t instanceof Element && t.closest("button, a, summary, [role=tab], [contenteditable]")) return;
      if (e.key === "Escape") {
        if (help.open) return;
        if (closePops()) return;
        if (typing) { t.blur(); return; }
        if (S.sig) closeDrawer();
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || help.open) return;
      switch (e.key) {
        case "/": e.preventDefault(); $("f-search").focus(); $("f-search").select(); break;
        case "j": e.preventDefault(); move(1); break;
        case "k": e.preventDefault(); move(-1); break;
        case "Enter": if (CUR && !S.sig) open(CUR); break;
        case "m": toggleMute(lookup(S.sig || CUR)); break;
        case "z": toggleTz(); toast(TZL ? `Times in ${TZ}` : "Times in UTC"); break;
        case "c": {
          const r = lookup(S.sig || CUR);
          if (r) copy(r.kind === "sig" ? r.sig : r.id.replace(/^[cm]:/, ""));
          break;
        }
        case "n": if ((S.p + 1) * PAGE < LIST.length) commit({ p: S.p + 1 }, { keepPage: true }); break;
        case "p": if (S.p > 0) commit({ p: S.p - 1 }, { keepPage: true }); break;
        case "[": shiftWindow(-1); break;
        case "]": shiftWindow(1); break;
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
      }, 150);
    });
  }

  // ---------------------------------------------------------------- start
  if (S.theme === "dark" || S.theme === "light") document.documentElement.setAttribute("data-theme", S.theme);
  setZone(S.tz ? S.tz === "local" : TZL);
  renderTzBtn();
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
    foot.textContent = `Generated ${ts16(D.generated_at)} ${TZ} by atlas-log ${D.tool_version || ""} · ${D.schema} · ` +
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
