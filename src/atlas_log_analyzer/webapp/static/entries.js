/* atlas-log web app: the entries page (/sets/ID/entries, /captures/REF/entries).
   A table of the entries that match the page's query (the filters of GET /api/.../entries),
   a row opens the shared entry card (entry-card.js, loaded first). The URL's query is the
   state: chips remove a filter, the search box is `contains`, a click on a thread / user / node
   value adds it as a filter and a drag over the time histogram sets since/until; rows load a
   page at a time. */
(function () {
  "use strict";
  const root = document.getElementById("ent");
  if (!root || !window.AtlasEntryCard) return;
  const $ = (id) => document.getElementById(id);
  const PAGE = JSON.parse(root.dataset.page);
  const CAPTURE = PAGE.kind === "capture";
  const NF = new Intl.NumberFormat("en-US");
  const fmt = (n) => NF.format(n);
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ESC[c]));

  // ---- levels and time (the zone is the viewer's preference, as on the report) ----
  const bucketOf = (level) =>
    level >= 50 ? "ERROR" : level >= 40 ? "WARN" : level >= 30 ? "INFO" : level >= 10 ? "DEBUG" : "OTHER";
  const LEVELS = { TRACE: 10, DEBUG: 20, INFO: 30, WARN: 40, ERROR: 50, FATAL: 60 };
  const levelNum = (name) => LEVELS[name] || 0;
  const badge = (name, level) => `<span class="lv lv-${bucketOf(level)}">${esc(name || "?")}</span>`;
  const zone = () => window.atlasZone || { local: () => false, zoneName: () => "UTC" };
  /** Milliseconds of an absolute ISO time (naive = UTC, as the API reads it); NaN for "2h" and the like. */
  const parseTs = (iso) => {
    const s = String(iso).trim().replace(/^(\d{4}-\d{2}-\d{2}) (?=\d)/, "$1T").replace(/(\.\d{3})\d+/, "$1");
    if (!/^\d{4}-\d{2}-\d{2}/.test(s)) return NaN;
    return Date.parse(/T\d/.test(s) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s) ? s + "Z" : s);
  };
  /** A window bound: milliseconds, or null when absent or relative (the server resolves those). */
  const bound = (v) => { const ms = v ? parseTs(v) : NaN; return ms === ms ? ms : null; };
  /** "YYYY-MM-DDTHH:MM:SS.mmmZ" of the ISO time shifted into the shown zone. */
  function shifted(iso) {
    const ms = parseTs(iso);
    if (ms !== ms) return "";
    return new Date(zone().local() ? ms - new Date(ms).getTimezoneOffset() * 6e4 : ms).toISOString();
  }
  const tsFull = (iso) => { const s = shifted(iso); return s ? s.slice(0, 10) + " " + s.slice(11, 23) : ""; };
  const tsMin = (iso) => { const s = shifted(iso); return s ? s.slice(0, 10) + " " + s.slice(11, 16) : ""; };
  const tzName = () => zone().zoneName(Date.now());
  /** The cell of a time: the date muted, the time in ink, the milliseconds muted. */
  const timeCell = (iso) => window.AtlasEntryCard.tsHtml(tsFull(iso));
  const { loggerHtml, excHtml, nilHtml } = window.AtlasEntryCard;

  // ---- state: the URL's query ----
  let Q = new URLSearchParams(location.search);
  const FILTERS = ["signature", "coarse_signature", "level", "min_level", "logger", "user", "thread", "request_id",
    "since", "until", "contains", "with_exception", "exception", "node"];
  const isOn = (v) => !!v && !/^(false|0|no|off)$/i.test(v);
  const hasFilters = () => FILTERS.some((k) => (k === "with_exception" ? isOn(Q.get(k)) : Q.get(k)));
  const sigList = () => (Q.get("signature") || "").split(",").map((s) => s.trim()).filter(Boolean);
  const oneGroup = () => (sigList().length === 1 && !Q.get("node")) || !!Q.get("coarse_signature");
  let ROWS = [];
  const BY_KEY = new Map();
  let TOTAL = null;
  let NEXT = null;
  let HEAD = null; // what the first row says about the group: level, template, logger, exception
  const SEEN = new Map(); // the same by signature, kept while a later filter leaves no row to say it
  let LAST_TS = "";
  let SEQ = 0;
  let CUR = -1;
  const OPEN = new Set();
  const FULLING = new Set();
  const keyOf = (it) => (CAPTURE && it.set ? it.set + "~" + it.id : it.id);

  const card = window.AtlasEntryCard.create({
    badge, bucketOf, levelNum, tsFull,
    tz: tzName,
    entry: (key) => BY_KEY.get(key),
    rawUrl: (key, e) => `/sets/${encodeURIComponent(e.set || PAGE.ref)}/entries/${encodeURIComponent(e.id)}/raw`,
    fullLoads: (key) => FULLING.has(key),
    nodeBadge: (n) => `<span class="node-chip">${esc(n)}</span>`,
    srcPath: (e) => e.source_path || "",
  });
  const PH = /&lt;([A-Z][A-Z0-9_]*|\*)&gt;/g;
  const tplHtml = (t) => card.logInline(t).replace(PH, (m, n) => `<span class="ph">${n.toLowerCase()}</span>`);

  // ---- loading ----
  function url(offset, extra) {
    const p = new URLSearchParams(Q);
    p.set("limit", String(PAGE.limit));
    p.set("offset", String(offset));
    if (CAPTURE) p.set("window", "union");
    for (const [k, v] of Object.entries(extra || {})) p.set(k, v);
    return PAGE.api + "?" + p.toString();
  }
  const getJson = (u) => fetch(u, { headers: { Accept: "application/json" } }).then(async (res) => {
    if (res.ok) return res.json();
    let msg = "HTTP " + res.status;
    try { const j = await res.json(); if (j && j.error) msg = j.error.message + (j.error.hint ? " (" + j.error.hint + ")" : ""); } catch (e) { /* not JSON */ }
    throw new Error(msg);
  });
  function addRows(doc) {
    for (const it of doc.items || []) {
      it.raw_truncated = !!it.body_truncated;
      if (CAPTURE) it.set = it.set || (PAGE.nodes || {})[it.node];
      BY_KEY.set(keyOf(it), it);
      ROWS.push(it);
    }
    TOTAL = doc.total;
    NEXT = doc.next_offset;
    if (ROWS.length && !HEAD) {
      const f = ROWS[0];
      HEAD = { level_name: f.level_name, level: f.level, template: f.template, logger: f.logger, exception: f.exception_type, first: f.ts };
      if (oneGroup() && sigList()[0]) SEEN.set(sigList()[0], HEAD);
    }
  }
  /** Load a page: the first one replaces the rows, a later one adds to them. */
  function load(first, doc) {
    const seq = ++SEQ;
    if (first) {
      ROWS = []; BY_KEY.clear(); OPEN.clear(); CUR = -1; TOTAL = null; NEXT = null; HEAD = null; LAST_TS = "";
      $("e-body").textContent = "";
      state("loading");
      renderHead();
      histRefresh();
    }
    const done = (d) => {
      if (seq !== SEQ) return;
      const from = ROWS.length;
      addRows(d);
      if (first) { renderHead(); if (HIST) drawHist(); }
      appendRows(from);
      renderZone();
      if (first && TOTAL > 1) lastTime(seq);
    };
    const fail = (err) => {
      if (seq !== SEQ) return;
      if (!first) { toast("Loading more failed: " + (err.message || String(err))); $("e-load").disabled = false; return; }
      state("error", err.message || String(err));
    };
    if (doc) return done(doc);
    getJson(url(first ? 0 : NEXT)).then(done, fail);
    $("e-load").disabled = !first;
  }
  /** The last entry's time, for the range in the header: one more tiny request. */
  function lastTime(seq) {
    getJson(url(TOTAL - 1, { limit: "1", max_raw: "1", columns: "ts" })).then((d) => {
      if (seq !== SEQ || !d.items || !d.items.length) return;
      LAST_TS = d.items[0].ts;
      renderHead();
    }, () => {});
  }

  // ---- rendering ----
  function state(kind, msg) {
    const empty = $("e-empty");
    empty.hidden = kind === "rows";
    $("e-table").hidden = kind === "empty" || kind === "error";
    empty.className = "card ent-empty" + (kind === "loading" ? " loading" : "");
    if (kind === "loading") empty.innerHTML = `<div class="empty loading" role="status"><span class="sr">Loading the entries…</span>${'<i class="sk"></i>'.repeat(6)}</div>`;
    else if (kind === "error") {
      empty.innerHTML = `<div class="empty"><p>Could not load the entries: ${esc(msg)}</p><button type="button" class="btn" id="e-retry">Retry</button></div>`;
      $("e-retry").onclick = () => load(true);
    } else if (kind === "empty") {
      empty.innerHTML = hasFilters()
        ? `<div class="empty"><p class="e-t">No entries match</p><div class="ent-chips">${chipList(true).join("")}</div>` +
          `<p class="e-h">Remove a filter to see more, or <button type="button" class="linkish" id="e-clear">remove all filters</button>.</p></div>`
        : `<div class="empty"><p class="e-t">This log set has no entries.</p></div>`;
      if ($("e-clear")) $("e-clear").onclick = reset;
    }
  }
  const one = (k) => !!Q.get(k) && !Q.get(k).includes(",");
  /** A column whose value every row shares (one group, thread, node or level picked) is left out. */
  const columns = () => ({ node: CAPTURE && !one("node") && !(sigList().length === 1 && sigList()[0].includes(":")), level: !oneGroup() && !one("level"), thread: !one("thread") });
  function renderHeadRow() {
    const c = columns();
    $("e-head").innerHTML = `<th class="c-t">Time <span class="tzn">${esc(tzName())}</span></th>` +
      (c.node ? `<th class="c-n">Node</th>` : "") + (c.level ? `<th class="c-l">Level</th>` : "") +
      (c.thread ? `<th class="c-th">Thread</th>` : "") + `<th class="c-m">Message</th>`;
  }
  /** A value that adds itself as a filter when clicked (plain when the filter is already set to it). */
  function fv(param, value, inner, what) {
    if (!value || Q.get(param) === String(value)) return inner;
    return `<button type="button" class="fv" data-f="${param}" data-v="${esc(value)}" title="Only ${esc(what)} ${esc(value)}">${inner}</button>`;
  }
  function rowHtml(it, i) {
    const c = columns();
    const key = keyOf(it);
    const text = String(it.message || it.exception_type || "").split("\n")[0] || "(empty message)";
    const tip = text.length > 110 ? ` title="${esc(text.slice(0, 600))}"` : "";
    return `<tr class="er" tabindex="-1" data-i="${i}" data-key="${esc(key)}" aria-expanded="false" ` +
      `aria-label="${esc(it.level_name + " " + tsFull(it.ts) + " " + text)}">` +
      `<td class="c-t">${timeCell(it.ts)}</td>` +
      (c.node ? `<td class="c-n">${fv("node", it.node, `<span class="node-chip">${esc(it.node)}</span>`, "node")}</td>` : "") +
      (c.level ? `<td class="c-l">${fv("level", it.level_name, badge(it.level_name, it.level), "level")}</td>` : "") +
      (c.thread ? `<td class="c-th">${it.thread ? fv("thread", it.thread, `<span class="ident">${esc(it.thread)}</span>`, "thread") : nilHtml()}</td>` : "") +
      `<td class="c-m"${tip}><span class="logtext">${card.logInline(text)}</span></td></tr>`;
  }
  const colspan = () => { const c = columns(); return 2 + (c.node ? 1 : 0) + (c.level ? 1 : 0) + (c.thread ? 1 : 0); };
  function appendRows(from) {
    const body = $("e-body");
    if (!ROWS.length) return state("empty");
    state("rows");
    body.insertAdjacentHTML("beforeend", ROWS.slice(from).map((it, k) => rowHtml(it, from + k)).join(""));
    if (CUR < 0) setCur(0, false);
  }
  /** Redraw every loaded row (the zone or the columns changed), keeping the open ones. */
  function redrawRows() {
    const open = [...OPEN];
    $("e-body").textContent = "";
    renderHeadRow();
    if (!ROWS.length) return;
    $("e-body").insertAdjacentHTML("beforeend", ROWS.map(rowHtml).join(""));
    OPEN.clear();
    for (const key of open) { const tr = rowOf(key); if (tr) expand(tr, true); }
    if (CUR >= 0) setCur(Math.min(CUR, ROWS.length - 1), false);
  }
  const rowOf = (key) => Array.from($("e-body").querySelectorAll("tr.er")).find((r) => r.dataset.key === key);

  function renderHead() {
    const sigs = sigList();
    const hd = HEAD || SEEN.get(sigs[0]);
    const group = oneGroup() && hd;
    const t = $("e-title");
    if (group && hd.template) { t.innerHTML = tplHtml(hd.template); t.title = hd.template; }
    else { t.textContent = sigs.length > 1 ? "Entries of " + sigs.length + " groups" : "Entries"; t.title = ""; }
    document.title = (group && hd.template ? hd.template.slice(0, 60) : "Entries") + " · " + PAGE.name + " · atlas-log";
    const bits = [];
    if (group) {
      bits.push(badge(hd.level_name, hd.level));
      if (hd.logger) bits.push(`<span title="Logger">${loggerHtml(hd.logger)}</span>`);
      if (hd.exception) bits.push(`<span title="Exception">${excHtml(hd.exception)}</span>`);
    }
    if (TOTAL != null) bits.push(`<span class="ent-n"><span class="n">${fmt(TOTAL)}</span> ${Q.get("contains") ? "matching " : ""}${TOTAL === 1 ? "entry" : "entries"}</span>`);
    if (HEAD && HEAD.first && TOTAL) {
      const a = tsMin(HEAD.first), b = LAST_TS ? tsMin(LAST_TS) : "";
      const ts = window.AtlasEntryCard.tsHtml;
      bits.push(`<span class="range" title="First and last entry of this selection">${ts(a)}${b && b !== a ? " → " + ts(a.slice(0, 10) === b.slice(0, 10) ? b.slice(11) : b) : ""} <span class="tz">${esc(tzName())}</span></span>`);
    }
    $("e-meta").innerHTML = bits.join("");
    $("e-barid").textContent = (group && hd.template ? hd.template : "Entries").slice(0, 80);
    const back = $("e-back");
    const one = sigList()[0] || "";
    const sig = one.includes(":") ? one.split(":").pop() : one;
    const toGroup = oneGroup() && sig && !Q.get("coarse_signature");
    back.href = PAGE.back + (toGroup ? "#sig=" + encodeURIComponent(sig) : "");
    back.hidden = !toGroup;  // the Report / Cluster view tab is the way back; this one opens the group
    back.title = "Show this group in " + (CAPTURE ? "the cluster view" : "the report");
    renderHeadRow();
  }
  /** One removable filter: a label word and its value (markup), `keys` are the query keys it clears. */
  function chip(keys, k, v, title) {
    return `<button type="button" class="fchip" data-clear="${keys.join(",")}" title="${esc(title)}" aria-label="Remove filter ${esc(k + " " + title)}">` +
      `<span class="k">${esc(k)}</span> <span class="v">${v}</span> <span class="x" aria-hidden="true">×</span></button>`;
  }
  function chipList(withSearch) {
    const chips = [];
    const sigs = sigList();
    if (sigs.length) {
      const one = sigs.length === 1;
      const short = one ? sigs[0].replace(/^.*:/, "").slice(0, 8) : "";
      const node = one && sigs[0].includes(":") ? sigs[0].split(":")[0] : "";
      let v = one ? esc(short) : `${sigs.length} groups`;
      let title = one ? "Only the entries of this group (signature " + short + ")" : "Only the entries of these groups";
      const h = HEAD || SEEN.get(sigs[0]);
      if (one && h && h.template && !node) {
        v = badge(h.level_name, h.level) + `<span class="t">${tplHtml(h.template)}</span>`;  // CSS cuts it, placeholders whole
        title = h.template + " (signature " + short + ")";
      } else if (node) v += " on " + esc(node);
      chips.push(chip(["signature"], "Group", v, title));
    }
    if (Q.get("coarse_signature")) chips.push(chip(["coarse_signature"], "Coarse", esc(Q.get("coarse_signature").slice(0, 8)), "Only the entries of this coarse group"));
    if (Q.get("since") || Q.get("until")) {
      const show = (k) => (Q.get(k) ? tsMin(Q.get(k)) || Q.get(k) : "");
      const a = show("since"), b = show("until");
      const sameDay = /^\d{4}-\d{2}-\d{2} /.test(a) && a.slice(0, 10) === b.slice(0, 10);
      const text = a && b ? a + " → " + (sameDay ? b.slice(11) : b) : a ? "from " + a : "until " + b;
      chips.push(chip(["since", "until"], "Window", esc(text) + ` <span class="tz">${esc(tzName())}</span>`, "Only entries in this time window"));
    }
    for (const [k, w] of [["node", "Node"], ["thread", "Thread"], ["user", "User"], ["level", "Level"], ["min_level", "Level ≥"], ["logger", "Logger"], ["exception", "Exception"], ["request_id", "Request"]]) {
      const val = Q.get(k);
      if (val) chips.push(chip([k], w, `<span class="${k === "thread" || k === "user" || k === "request_id" ? "mono" : ""}">${esc(val)}</span>`, val));
    }
    if (withSearch && Q.get("contains")) chips.push(chip(["contains"], "Search", `<span class="mono">${esc(Q.get("contains"))}</span>`, Q.get("contains")));
    if (isOn(Q.get("with_exception"))) chips.push(chip(["with_exception"], "With exception", "", "Only entries with an exception"));
    return chips;
  }
  function renderChips() {
    $("e-chips").innerHTML = chipList().join("");
    $("e-reset").disabled = !hasFilters();
  }
  function renderZone() {
    renderChips();
    renderHead();
    const n = ROWS.length;
    $("e-sum").textContent = !n ? (TOTAL === 0 ? "No entries" : "Loading…")
      : `1–${fmt(n)} of ${fmt(TOTAL)} ${TOTAL === 1 ? "entry" : "entries"}${Q.get("contains") ? " matching" : ""}`;
    const more = NEXT != null && NEXT < TOTAL;
    $("e-more").hidden = !more;
    $("e-load").disabled = false;
    $("e-load").textContent = more ? `Load ${fmt(Math.min(PAGE.limit, TOTAL - NEXT))} more` : "Load more";
  }

  // ---- time histogram: entries per bucket; a drag or a click sets since/until ----
  const SIZES = [["1m", 6e4], ["5m", 3e5], ["15m", 9e5], ["1h", 36e5], ["6h", 216e5], ["1d", 864e5]];
  const SIZE_WORD = { "1m": "minute", "5m": "5 minutes", "15m": "15 minutes", "1h": "hour", "6h": "6 hours", "1d": "day" };
  const MAX_BARS = 200;
  const NICE = [6e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5, 432e5, 864e5, 1728e5, 6048e5];
  const isoZ = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
  let HIST = null; // {from, size, name, counts[], peak}
  let HIST_KEY = "";
  let HIST_SEQ = 0;
  let HG = null; // geometry of the drawn chart
  let DRAG = null;

  /** What the chart counts: the page's group, level and node filters (the timeline API's); null: not drawn. */
  function histSpec() {
    const sigs = sigList();
    if (Q.get("coarse_signature") || sigs.length > 1) return null;
    const sig = (sigs[0] || "").replace(/^.*:/, "");
    const node = sigs[0] && sigs[0].includes(":") ? sigs[0].split(":")[0] : Q.get("node") || "";
    const spec = { sig, node, level: Q.get("level") || "", min: Q.get("min_level") || "", since: Q.get("since") || "", until: Q.get("until") || "" };
    for (const k of EXTRA) spec[k] = Q.get(k) || "";
    return spec;
  }
  /** Entry filters the set timeline API takes besides group, level and window (a capture's lacks them). */
  const EXTRA = ["thread", "user", "request_id", "logger", "contains", "exception", "with_exception"];
  const timelineUrl = (spec, bucket) => {
    const p = new URLSearchParams({ bucket });
    if (spec.sig) p.set("signature", spec.sig);
    if (spec.since) p.set("since", spec.since);
    if (spec.until) p.set("until", spec.until);
    if (CAPTURE) p.set("window", "union");
    else {
      if (spec.level) p.set("level", spec.level);
      if (spec.min) p.set("min_level", spec.min);
      for (const k of EXTRA) if (spec[k]) p.set(k, spec[k]);
    }
    return PAGE.api.replace(/\/entries$/, "/timeline") + "?" + p.toString();
  };
  /** Map of bucket start (ms) to count, from a timeline.v1 or cluster-timeline.v1 document. */
  function timelineCounts(doc, spec) {
    const out = new Map();
    const add = (t, c) => out.set(t, (out.get(t) || 0) + c);
    if (doc.items) for (const it of doc.items) add(Date.parse(it.bucket), it.count);
    else if (doc.buckets) {
      const keep = spec.node ? spec.node.split(",") : null;
      for (const [node, n] of Object.entries(doc.nodes || {})) {
        if (keep && !keep.includes(node)) continue;
        const series = Object.values(n.levels || {});
        doc.buckets.forEach((b, i) => add(Date.parse(b), series.reduce((a, s) => a + (s[i] || 0), 0)));
      }
    }
    return out;
  }
  const pickSize = (span) => SIZES.find(([, ms]) => span / ms <= MAX_BARS) || SIZES[SIZES.length - 1];
  const sizeMs = (name) => SIZES.find((s) => s[0] === name)[1];
  async function fetchHist(spec) {
    let name = "1h";
    const s0 = bound(spec.since), u0 = bound(spec.until);
    const known = s0 != null && u0 != null ? u0 - s0 : 0;
    if (known > 0) name = pickSize(known)[0];
    let map = timelineCounts(await getJson(timelineUrl(spec, name)), spec);
    for (let pass = 0; pass < 2 && map.size; pass++) {
      const ts = [...map.keys()];
      const from = s0 != null ? s0 : Math.min(...ts);
      const to = u0 != null ? u0 : Math.max(...ts) + sizeMs(name);
      const want = pickSize(to - from)[0];
      if (want === name) break;
      name = want;
      map = timelineCounts(await getJson(timelineUrl(spec, name)), spec);
    }
    if (!map.size) return null;
    const size = sizeMs(name);
    const ts = [...map.keys()];
    const from = Math.floor((s0 != null ? s0 : Math.min(...ts)) / size) * size;
    const to = u0 != null ? u0 : Math.max(...ts) + size;
    const nb = Math.max(1, Math.ceil((to - from) / size));
    const counts = new Array(nb).fill(0);
    for (const [t, c] of map) { const i = Math.floor((t - from) / size); if (i >= 0 && i < nb) counts[i] += c; }
    return { from, size, name, counts, peak: Math.max(1, ...counts) };
  }
  /** Fetch (when what it counts changed) and draw the chart. */
  function histRefresh() {
    const spec = histSpec();
    const key = spec ? JSON.stringify(spec) : "";
    if (key === HIST_KEY) return;
    HIST_KEY = key;
    const seq = ++HIST_SEQ;
    $("e-hist-full").hidden = !(spec && (spec.since || spec.until));
    if (!spec) { HIST = null; drawHist(); return; }
    fetchHist(spec).then(
      (h) => { if (seq === HIST_SEQ) { HIST = h; drawHist(); } },
      () => { if (seq === HIST_SEQ) { HIST = null; drawHist(); } });
  }
  const bucketLabel = (ms, size) => {
    const a = shifted(isoZ(ms)), b = shifted(isoZ(ms + size));
    if (!a) return "";
    if (size >= 864e5) return a.slice(0, 10);
    return a.slice(0, 10) + " " + a.slice(11, 16) + " – " + b.slice(11, 16) + " " + tzName();
  };
  function drawHist() {
    const sec = $("e-hist"), host = $("e-hist-plot");
    HG = null;
    // one bucket draws too when a window is set: the chart holds the "Full range" button
    if (!HIST || (HIST.counts.length < 2 && !(Q.get("since") || Q.get("until")))) { sec.hidden = true; host.textContent = ""; return; }
    sec.hidden = false;
    const { from, size, counts, peak } = HIST;
    const nb = counts.length;
    const W = Math.max(200, host.clientWidth), H = 68, axisH = 18, padT = 4, plotH = H - axisH - padT;
    const slot = W / nb, bw = Math.max(1, slot - (slot > 5 ? 1 : 0));
    const hd = HEAD || SEEN.get(sigList()[0]);
    const lvl = oneGroup() && hd ? bucketOf(hd.level) : "";
    const cls = lvl && lvl !== "OTHER" ? "s-" + lvl : "s-flat";
    const base = padT + plotH;
    const parts = [`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Entries per ${SIZE_WORD[HIST.name]}, peak ${fmt(peak)}">`];
    parts.push(`<line class="grid" x1="0" x2="${W}" y1="${base + 0.5}" y2="${base + 0.5}"/>`);
    counts.forEach((c, i) => {
      if (!c) return;
      const h = Math.max(5, Math.round((c / peak) * plotH));
      parts.push(`<rect class="hb ${cls}" x="${(i * slot).toFixed(2)}" y="${base - h}" width="${bw.toFixed(2)}" height="${h}"/>`);
    });
    // axis: ticks on round times of the shown zone
    const off = Date.parse(shifted(isoZ(from))) - from;
    const want = (nb * size) / Math.max(2, Math.floor(W / 100));
    const unit = NICE.find((u) => u >= want && u >= size) || NICE[NICE.length - 1];
    let lastX = -1e9;
    for (let i = 0; i < nb; i++) {
      const t = from + i * size;
      if ((t + off) % unit !== 0) continue;
      const x = Math.round(i * slot + bw / 2);
      if (x - lastX < 60) continue;
      const first = lastX < 0;
      lastX = x;
      const s = shifted(isoZ(t));
      const text = unit >= 864e5 ? s.slice(5, 10) : first || s.slice(11, 16) === "00:00" ? s.slice(5, 10) + " " + s.slice(11, 16) : s.slice(11, 16);
      const anchor = x < 40 ? "start" : x > W - 40 ? "end" : "middle";
      parts.push(`<line class="grid" x1="${x + 0.5}" x2="${x + 0.5}" y1="${base}" y2="${base + 3}"/><text class="axis" x="${x}" y="${H - 3}" text-anchor="${anchor}">${esc(text)}</text>`);
    }
    parts.push(`<rect class="brush" id="e-brush" x="0" y="${padT}" width="0" height="${plotH}" visibility="hidden"/>`);
    counts.forEach((c, i) => parts.push(`<rect class="hit" x="${(i * slot).toFixed(2)}" y="0" width="${slot.toFixed(2)}" height="${H}" data-tip-k="ent-bucket" data-tip-up data-i="${i}"/>`));
    parts.push("</svg>");
    host.innerHTML = parts.join("");
    HG = { svg: host.firstElementChild, slot, nb };
    const total = counts.reduce((a, b) => a + b, 0);
    // a capture's timeline API counts by group, level and node only: say so when other filters narrow the table
    const unseen = [];
    if (CAPTURE) unseen.push(...EXTRA.filter((k) => k !== "with_exception" && Q.get(k)));
    if (CAPTURE && isOn(Q.get("with_exception"))) unseen.push("with_exception");
    if (CAPTURE) unseen.push(...["level", "min_level"].filter((k) => Q.get(k)));
    const note = unseen.length ? ` · <span class="muted" title="The chart counts the group${CAPTURE ? " and node" : ", level and node"} only; the table also applies the other filters">not filtered by ${esc(unseen.map((k) => k.replace("_", " ")).join(", "))}</span>` : "";
    $("e-hist-what").innerHTML = `per ${SIZE_WORD[HIST.name]} · peak <span class="n">${fmt(peak)}</span> · <span class="n">${fmt(total)}</span> in view${note}`;
  }
  const idxAt = (ev) => Math.max(0, Math.min(HG.nb - 1, Math.floor((ev.clientX - HG.svg.getBoundingClientRect().left) / HG.slot)));
  function setBrush(i0, i1) {
    const b = $("e-brush");
    if (!b) return;
    b.setAttribute("visibility", i0 == null ? "hidden" : "visible");
    if (i0 != null) {
      b.setAttribute("x", (Math.min(i0, i1) * HG.slot).toFixed(2));
      b.setAttribute("width", ((Math.abs(i1 - i0) + 1) * HG.slot).toFixed(2));
    }
  }
  /** Narrow the page to buckets i0..i1: since/until in the URL, the table and the chart reload. */
  function pickWindow(i0, i1) {
    const a = Math.min(i0, i1), b = Math.max(i0, i1);
    const since = HIST.from + a * HIST.size, until = HIST.from + (b + 1) * HIST.size;
    commit((q) => { q.set("since", isoZ(since)); q.set("until", isoZ(until)); }, false);
  }
  function initHist() {
    const host = $("e-hist-plot");
    if (window.AtlasTips) {
      window.AtlasTips.register("ent-bucket", (el) => {
        if (!HIST) return "";
        const i = +el.dataset.i, c = HIST.counts[i], total = HIST.counts.reduce((a, b) => a + b, 0);
        return window.AtlasTips.card({
          h: bucketLabel(HIST.from + i * HIST.size, HIST.size),
          rows: [["Entries", fmt(c)], ["Of this view", c && total ? (100 * c / total).toFixed(c / total < 0.1 ? 1 : 0) + " %" : ""]],
          note: "Click: only this " + SIZE_WORD[HIST.name] + ". Drag: a time range.",
        });
      });
    }
    host.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || !HG) return;
      if (window.AtlasTips) window.AtlasTips.hide();
      DRAG = { a: idxAt(ev), b: idxAt(ev), x: ev.clientX };
      host.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    host.addEventListener("pointermove", (ev) => {
      if (!DRAG || !HG) return;
      DRAG.b = idxAt(ev);
      if (Math.abs(ev.clientX - DRAG.x) > 4 || DRAG.a !== DRAG.b) setBrush(DRAG.a, DRAG.b);
    });
    const end = (ev) => {
      if (!DRAG) return;
      const d = DRAG;
      DRAG = null;
      setBrush(null);
      if (ev.type === "pointerup" && HIST) pickWindow(d.a, d.b);
    };
    host.addEventListener("pointerup", end);
    host.addEventListener("pointercancel", end);
    $("e-hist-full").addEventListener("click", () => commit((q) => { q.delete("since"); q.delete("until"); }, false));
    if (window.ResizeObserver) {
      let w = host.clientWidth;
      new ResizeObserver(() => { if (host.clientWidth !== w) { w = host.clientWidth; if (HIST) drawHist(); } }).observe(host);
    }
  }

  // ---- query changes ----
  function commit(mut, replace) {
    mut(Q);
    const s = Q.toString();
    const href = location.pathname + (s ? "?" + s : "") + location.hash;
    if (replace) history.replaceState(null, "", href); else history.pushState(null, "", href);
    $("e-search").value = Q.get("contains") || "";
    renderChips();
    load(true);
  }
  function reset() { commit((q) => { for (const k of FILTERS) q.delete(k); }, false); $("e-search").focus(); }
  let searchTimer = 0;
  function onSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const v = $("e-search").value.trim();
      if (v === (Q.get("contains") || "")) return;
      commit((q) => { if (v) q.set("contains", v); else q.delete("contains"); }, true);
    }, 300);
  }

  // ---- rows: cursor and the open entry ----
  const rows = () => Array.from($("e-body").querySelectorAll("tr.er"));
  function setCur(i, focus) {
    const all = rows();
    if (!all.length) return;
    CUR = Math.max(0, Math.min(i, all.length - 1));
    all.forEach((r, k) => { r.tabIndex = k === CUR ? 0 : -1; r.classList.toggle("is-cur", k === CUR); });
    if (focus) { all[CUR].focus({ preventScroll: true }); all[CUR].scrollIntoView({ block: "nearest" }); }
  }
  function expand(tr, on) {
    const key = tr.dataset.key;
    const next = tr.nextElementSibling;
    const has = next && next.classList.contains("er-detail");
    if (on && !has) {
      const e = BY_KEY.get(key);
      const d = document.createElement("tr");
      d.className = "er-detail";
      d.innerHTML = `<td colspan="${colspan()}"><div class="report ent-card">${card.entryHtml(key, e)}</div></td>`;
      tr.after(d);
      decorate(d);
      OPEN.add(key);
      if (e.raw_truncated) fetchFull(key);
    } else if (!on && has) { next.remove(); OPEN.delete(key); }
    tr.setAttribute("aria-expanded", String(!!(on || (has && on))));
    tr.classList.toggle("is-sel", !!on);
  }
  const toggle = (tr) => expand(tr, tr.getAttribute("aria-expanded") !== "true");
  /** An entry cut at the row size: fetch it whole when it is opened. */
  function fetchFull(key) {
    const e = BY_KEY.get(key);
    if (FULLING.has(key) || !e.raw_truncated) return;
    FULLING.add(key);
    const set = e.set || PAGE.ref;
    getJson(`/api/sets/${encodeURIComponent(set)}/entries/${encodeURIComponent(e.id)}/context?before=0&after=0&max_raw=0`)
      .then((doc) => {
        const full = (doc.items || []).find((x) => x.id === e.id);
        if (full) Object.assign(e, full, { raw_truncated: false, node: e.node, set: e.set });
      })
      .catch(() => { e.raw_truncated = false; e.raw_failed = true; })
      .finally(() => {
        FULLING.delete(key);
        const tr = rowOf(key), art = tr && tr.nextElementSibling && tr.nextElementSibling.querySelector(".entry");
        if (art) { const host = art.parentElement; art.outerHTML = card.entryHtml(key, e); decorate(host); }
      });
  }
  /** Add one filter (the URL changes, the table reloads). */
  function addFilter(param, value) {
    if (!param || !value) return;
    if (Q.get(param) === value) { toast("Already filtered"); return; }
    commit((q) => q.set(param, value), false);
  }
  const CARD_FILTERS = { thread: "thread", user: "user", request: "request_id" };
  /** Make the thread / user / request / node values of an open entry card filters. */
  function decorate(root) {
    if (!root) return;
    root.querySelectorAll(".kv").forEach((kv) => {
      const k = CARD_FILTERS[(kv.querySelector(".k") || {}).textContent];
      const v = (kv.querySelector(".v") || {}).textContent;
      if (!k || !v || Q.get(k) === v) return;
      kv.classList.add("f");
      kv.dataset.f = k; kv.dataset.v = v;
      kv.setAttribute("role", "button"); kv.tabIndex = 0;
      kv.title = "Only " + (k === "request_id" ? "request" : k) + " " + v;
    });
    if (CAPTURE) root.querySelectorAll(".node-chip").forEach((n) => {
      const v = n.textContent.trim();
      if (!v || Q.get("node") === v) return;
      n.classList.add("f");
      n.dataset.f = "node"; n.dataset.v = v;
      n.setAttribute("role", "button"); n.tabIndex = 0; n.title = "Only node " + v;
    });
  }
  const toastEl = $("e-toast");
  let toastTimer = 0;
  function toast(text) {
    toastEl.textContent = text; toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 1400);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); return; } catch (e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); toast("Copied"); } catch (e) { toast("Copy failed"); }
    ta.remove();
  }

  function init() {
    $("e-search").value = Q.get("contains") || "";
    $("e-search").addEventListener("input", onSearch);
    $("e-reset").addEventListener("click", reset);
    initHist();
    $("e-load").addEventListener("click", () => load(false));
    const clearChip = (ev) => {
      const b = ev.target.closest("[data-clear]");
      if (b) commit((q) => b.dataset.clear.split(",").forEach((k) => q.delete(k)), false);
    };
    $("e-chips").addEventListener("click", clearChip);
    $("e-empty").addEventListener("click", clearChip);
    $("e-body").addEventListener("click", (ev) => {
      const t = ev.target;
      const f = t.closest(".fv, .kv.f, .ent-card .node-chip.f");
      if (f) { addFilter(f.dataset.f, f.dataset.v); return; }
      const art = t.closest(".ent-card");
      if (!art) {
        const tr = t.closest("tr.er");
        if (tr && !window.getSelection().toString()) { setCur(+tr.dataset.i, false); toggle(tr); }
        return;
      }
      const b = t.closest("[data-raw-view],[data-all-lines],[data-copy-raw],[data-unfold]");
      if (!b) return;
      if (b.dataset.rawView != null) decorate(card.toggleRaw(b));
      else if (b.dataset.allLines != null) card.showAllLines(b);
      else if (b.dataset.copyRaw != null) copy(card.fullText(BY_KEY.get(b.dataset.copyRaw) || {}));
      else {
        const f = b.closest(".fold");
        const opened = f.classList.toggle("open");
        const n = +b.dataset.unfold;
        b.textContent = opened ? "hide frames" : `show ${fmt(n)} more frame${n === 1 ? "" : "s"}`;
      }
    });
    $("e-body").addEventListener("keydown", (ev) => {
      const f = ev.target.closest && ev.target.closest(".kv.f, .node-chip.f");
      if (f && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); addFilter(f.dataset.f, f.dataset.v); }
    });
    const zb = $("shell-tz");
    document.addEventListener("keydown", (ev) => {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      if (document.querySelector("dialog[open]")) return;
      const tag = (ev.target.tagName || "").toLowerCase();
      if (ev.key === "Escape") {
        if (tag === "input") { ev.target.blur(); return; }
        const all = rows();
        const tr = all[CUR];
        if (tr && tr.getAttribute("aria-expanded") === "true") { expand(tr, false); setCur(CUR, true); }
        else for (const r of all) if (r.getAttribute("aria-expanded") === "true") expand(r, false);
        return;
      }
      if (tag !== "input" && tag !== "textarea" && tag !== "select" && !ev.target.isContentEditable) {
        if (ev.key === "?") { ev.preventDefault(); $("e-help").showModal(); return; }
        if (ev.key === "z" && zb) { ev.preventDefault(); zb.click(); return; }
      }
      if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button" || tag === "a" || ev.target.isContentEditable) return;
      if (ev.target.closest && ev.target.closest(".ent-card")) return;
      if (ev.key === "/") { ev.preventDefault(); $("e-search").focus(); $("e-search").select(); }
      else if (ev.key === "j" || ev.key === "ArrowDown") { ev.preventDefault(); setCur(CUR + 1, true); }
      else if (ev.key === "k" || ev.key === "ArrowUp") { ev.preventDefault(); setCur(CUR - 1, true); }
      else if (ev.key === "Enter" || ev.key === " ") {
        const tr = ev.target.closest && ev.target.closest("tr.er");
        if (tr) { ev.preventDefault(); toggle(tr); }
      } else if (ev.key === "Home") { ev.preventDefault(); setCur(0, true); }
      else if (ev.key === "End") { ev.preventDefault(); setCur(rows().length - 1, true); }
    });
    // after webapp.js has switched the zone (its listener is added later than this one)
    if (zb) zb.addEventListener("click", () => setTimeout(() => { renderZone(); redrawRows(); drawHist(); }, 0));
    window.addEventListener("popstate", () => {
      Q = new URLSearchParams(location.search);
      $("e-search").value = Q.get("contains") || "";
      renderChips();
      load(true);
    });
    if (window.ResizeObserver) {
      const bar = $("e-bar");
      new ResizeObserver(() => document.documentElement.style.setProperty("--sticky-top", bar.offsetHeight + "px")).observe(bar);
    }
    const hb = $("e-keys-btn");
    if (hb) hb.addEventListener("click", () => $("e-help").showModal());
    renderChips();
    let first = null;
    try { first = JSON.parse($("e-first").textContent); } catch (e) { first = null; }
    load(true, first);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
