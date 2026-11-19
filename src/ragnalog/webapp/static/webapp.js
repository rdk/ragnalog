/* ragnalog web app: theme, set switcher, triage verdicts, upload with job progress, and the
   builders of the pages' rich tooltips (tips.js, loaded first, is window.RagnalogTips). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-US") : "–");
  const KEY = "ragnalog-report-theme";

  function currentTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function setThemeLabel() {
    const b = $("shell-theme");
    if (b) b.title = currentTheme() === "dark" ? "Switch to the light theme" : "Switch to the dark theme";  // the icon swaps in CSS
  }
  function toggleTheme() {
    if (window.ragnalogTheme) { window.ragnalogTheme(); return; }  // the report page owns the toggle
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
    setThemeLabel();
  }

  // times: UTC or the viewer's zone, the preference the report and cluster pages share
  const TZ_KEY = "ragnalog-tz";
  let tzLocal = false;
  try { tzLocal = localStorage.getItem(TZ_KEY) === "local"; } catch (e) { tzLocal = false; }
  /** The ISO-like "YYYY-MM-DDTHH:MM" of ms in the shown zone. */
  const zoned = (ms) => new Date(tzLocal ? ms - new Date(ms).getTimezoneOffset() * 6e4 : ms).toISOString().slice(0, 16);
  function zoneName(ms) {
    if (!tzLocal) return "UTC";
    try {
      const p = new Intl.DateTimeFormat("en-GB", { timeZoneName: "short" }).formatToParts(new Date(ms));
      return (p.find((x) => x.type === "timeZoneName") || {}).value || "local";
    } catch (e) { return "local"; }
  }
  const parseIso = (s) => Date.parse(String(s).replace(/(\.\d{3})\d+/, "$1").replace(" ", "T"));
  function rangeText(a, b) {
    // as pages.py _fmt_range: the end drops the year (and date) it shares
    a = a.replace("T", " "); b = b.replace("T", " ");
    if (a.slice(0, 10) === b.slice(0, 10)) return `${a} → ${b.slice(11)}`;
    return a.slice(0, 4) === b.slice(0, 4) ? `${a} → ${b.slice(5)}` : `${a} → ${b}`;
  }
  /** Show the ranges and times under root in the chosen zone (pages print UTC). */
  function tzIn(root) {
    root.querySelectorAll(".tz-range[data-a]").forEach((el) => {
      const a = parseIso(el.dataset.a), b = parseIso(el.dataset.b);
      if (!(a === a && b === b)) return;
      // the time kind: the date muted, the time of day in ink
      el.textContent = "";
      rangeText(zoned(a), zoned(b)).split(/( → )/).forEach((part) => {
        if (part === " → ") { el.append(part); return; }
        const [d, t] = part.includes(" ") ? part.split(" ") : [null, part];
        if (d) { const ds = document.createElement("span"); ds.className = "d"; ds.textContent = d + " "; el.append(ds); }
        const ts = document.createElement("span"); ts.className = "t"; ts.textContent = t; el.append(ts);
      });
    });
    root.querySelectorAll("time.tz-ts[datetime]").forEach((el) => {
      const t = parseIso(el.getAttribute("datetime"));
      if (t !== t) return;
      // the text kind of a time (report.css .ts): the date muted, the time of day in ink
      const [d, hm] = zoned(t).split("T");
      el.textContent = "";
      const ds = document.createElement("span"), ts = document.createElement("span");
      ds.className = "d"; ds.textContent = d; ts.className = "t"; ts.textContent = hm;
      el.append(ds, " ", ts);
    });
  }
  function applyTz() {
    tzIn(document);
    const table = document.querySelector("table.sets[data-axis-lo]");
    if (table) {
      const lo = +table.dataset.axisLo, hi = +table.dataset.axisHi;
      const short = hi - lo <= 864e5 ? (s) => s.slice(11, 16) : (s) => s.slice(5, 10);
      const ax = table.querySelectorAll(".strip-axis span");
      if (ax.length === 2) { ax[0].textContent = short(zoned(lo)); ax[1].textContent = short(zoned(hi)); }
    }
    document.querySelectorAll("[data-tz-name]").forEach((el) => { el.textContent = zoneName(Date.now()); });
    const b = $("shell-tz");
    if (b) {
      (b.querySelector(".tz-t") || b).textContent = zoneName(Date.now());
      b.setAttribute("aria-pressed", String(tzLocal));
      b.title = tzLocal ? "Times in your time zone; click to show UTC" : "Times in UTC; click to show your time zone";
    }
  }
  window.ragnalogZone = { zoned, zoneName, local: () => tzLocal };

  // full screen width: shared with the report and cluster pages (base.html applies it before paint)
  const WIDE_KEY = "ragnalog-wide";
  function renderWide() {
    const b = $("shell-wide");
    if (!b) return;
    const on = document.documentElement.hasAttribute("data-wide");
    const t = on ? "Back to the centred page width (w)" : "Use the full screen width (w)";
    b.setAttribute("aria-pressed", String(on));
    if (b.hasAttribute("data-tip-text")) b.setAttribute("data-tip-text", t); else b.title = t;
  }
  function toggleWide() {
    const on = !document.documentElement.hasAttribute("data-wide");
    document.documentElement.toggleAttribute("data-wide", on);
    try { localStorage.setItem(WIDE_KEY, on ? "1" : "0"); } catch (e) { /* private mode */ }
    renderWide();
    window.dispatchEvent(new Event("resize"));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const tb = $("shell-theme");
    if (tb) { setThemeLabel(); tb.addEventListener("click", toggleTheme); }
    applyTz();
    const wb = $("shell-wide");
    if (wb) {
      renderWide();
      wb.addEventListener("click", toggleWide);
      document.addEventListener("keydown", (ev) => {
        const t = ev.target;
        if (ev.key !== "w" || ev.ctrlKey || ev.metaKey || ev.altKey || ev.defaultPrevented) return;
        if (t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement) return;
        if (document.querySelector("dialog[open]")) return;
        toggleWide();
      });
    }
    const zb = $("shell-tz");
    if (zb) zb.addEventListener("click", () => {
      tzLocal = !tzLocal;
      try { localStorage.setItem(TZ_KEY, tzLocal ? "local" : "utc"); } catch (e) { /* private mode */ }
      applyTz();
    });

    // ragnalog's menu in the breadcrumb closes on a click elsewhere, on Esc and on a choice
    const menu = document.querySelector(".crumb-menu");
    if (menu) {
      document.addEventListener("click", (ev) => {
        if (!menu.contains(ev.target) || ev.target.closest(".crumb-menu a")) menu.open = false;
      });
      menu.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); ev.stopPropagation(); }
      });
    }

    // set and node switchers on the report page keep compare/filters (hash) when switching
    ["shell-set", "shell-node"].forEach((id) => {
      const sw = $(id);
      if (sw) sw.addEventListener("change", () => {
        const q = new URLSearchParams(location.search);
        if (q.get("compare") === sw.value) q.delete("compare"); // a set is not its own baseline
        const qs = q.toString();
        location.href = "/sets/" + encodeURIComponent(sw.value) + (qs ? "?" + qs : "") + location.hash;
      });
    });

    // the triage page's set switcher opens that set's triage (its baseline and level are the set's own)
    const ts = $("shell-triage-set");
    if (ts) ts.addEventListener("change", () => { location.href = "/sets/" + encodeURIComponent(ts.value) + "/triage"; });

    // triage verdict per set (one request each; the server caches nothing here, diffs are fast)
    const byCapture = {};
    document.querySelectorAll("[data-triage]").forEach((cell) => {
      const id = cell.getAttribute("data-triage");
      const verdict = fetch("/api/sets/" + encodeURIComponent(id) + "/triage?min_level=WARN&limit=1")
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((t) => { verdicts[id] = t; cell.innerHTML = verdictHtml(t, id); return t.verdict; })
        .catch(() => { cell.innerHTML = '<span class="none">–</span>'; return null; });
      const row = cell.closest("tr");
      const capture = row && row.getAttribute("data-member-of");
      if (capture) (byCapture[capture] = byCapture[capture] || []).push(
        verdict.then((v) => ({ id, node: cell.getAttribute("data-node"), verdict: v })));
    });
    // a capture's verdict is the worst of its nodes'
    document.querySelectorAll("[data-capture-verdict]").forEach((cell) => {
      Promise.all(byCapture[cell.getAttribute("data-capture-verdict")] || [])
        .then((nodes) => { cell.innerHTML = captureVerdictHtml(nodes, cell.getAttribute("data-capture-verdict")); });
    });

    document.querySelectorAll("[data-delete]").forEach((b) => b.addEventListener("click", () => {
      const id = b.getAttribute("data-delete");
      if (!confirm("Delete log set " + b.getAttribute("data-label") + "? This removes its Parquet files from the data directory.")) return;
      b.disabled = true;
      fetch("/api/sets/" + encodeURIComponent(id), { method: "DELETE" })
        .then((r) => (r.ok ? location.reload() : r.json().catch(() => ({})).then((d) => {
          alert(d.error ? d.error.message : "Delete failed (HTTP " + r.status + ")");
          b.disabled = false;
        })))
        .catch(() => { alert("Delete failed: the server did not answer"); b.disabled = false; });
    }));

    setupSetSearch();
    setupUpload();
    setupStrips();
    setupTips();

    // triage: "Show all" reveals a section's other rows; the section bar opens what it jumps to
    document.querySelectorAll("[data-show-rest]").forEach((b) => b.addEventListener("click", () => {
      const sec = b.closest(".triage-sec");
      sec.querySelectorAll("tbody.rest").forEach((t) => { t.hidden = false; });
      b.parentElement.remove();
    }));
    // the section bar names the page once it is stuck to the top
    const sentinel = document.querySelector(".bar-sentinel");
    const secBar = document.querySelector(".section-bar");
    if (secBar && "ResizeObserver" in window) {
      new ResizeObserver(() => {
        document.body.style.setProperty("--section-bar-h", secBar.offsetHeight + "px");
      }).observe(secBar);
    }
    if (sentinel && secBar && "IntersectionObserver" in window) {
      new IntersectionObserver(([e]) => secBar.classList.toggle("stuck", !e.isIntersecting && e.boundingClientRect.top < 0))
        .observe(sentinel);
    }
    // the section in view is marked in the bar (while it is stuck); the page name goes back to the top
    if (secBar) {
      const links = Array.from(secBar.querySelectorAll("a[data-open]"));
      const spy = () => {
        const line = secBar.offsetHeight + 24;
        let cur = null;
        if (secBar.classList.contains("stuck")) {
          for (const a of links) {
            const d = document.getElementById(a.getAttribute("data-open"));
            if (d && d.getBoundingClientRect().top <= line) cur = a;
          }
        }
        links.forEach((a) => {
          a.classList.toggle("cur", a === cur);
          if (a === cur) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current");
        });
      };
      window.addEventListener("scroll", spy, { passive: true });
      window.addEventListener("resize", spy);
      new MutationObserver(spy).observe(secBar, { attributes: true, attributeFilter: ["class"] });
      const id = secBar.querySelector(".bar-id");
      if (id) id.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    }
    document.querySelectorAll("a[data-open]").forEach((a) => a.addEventListener("click", () => {
      const d = document.getElementById(a.getAttribute("data-open"));
      if (d) d.open = true;
    }));
  });

  function setupSetSearch() {
    const input = $("sets-search"), source = $("sets-data");
    if (!input || !source) return;
    let sets;
    try { sets = JSON.parse(source.textContent); } catch (e) { return; }
    const byId = new Map(sets.map((s) => [s.id, s]));
    const rows = Array.from(document.querySelectorAll("table.sets tr[data-set], table.sets tr[data-capture]"));
    const text = new Map(rows.map((row) => {
      const s = byId.get(row.dataset.set);
      return [row, (s ? [s.id, s.name, s.node, s.cluster].filter(Boolean).join(" ") : row.querySelector(".c-set").textContent).toLocaleLowerCase()];
    }));
    const apply = () => {
      const query = input.value.trim().toLocaleLowerCase();
      const matchingCaptures = new Set();
      rows.forEach((row) => {
        if (text.get(row).includes(query)) {
          const capture = row.dataset.capture || row.dataset.memberOf;
          if (capture) matchingCaptures.add(capture);
        }
      });
      let shown = 0;
      rows.forEach((row) => {
        const capture = row.dataset.capture || row.dataset.memberOf;
        row.hidden = !(capture ? matchingCaptures.has(capture) : text.get(row).includes(query));
        if (!row.hidden && row.dataset.set) shown++;
      });
      $("sets-no-matches").hidden = shown > 0;
      $("sets-search-count").textContent = query ? `${shown} of ${sets.length} sets · matching captures include all nodes` : `${sets.length} log sets`;
    };
    input.addEventListener("input", apply);
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { input.value = ""; apply(); }
    });
    $("sets-clear-search").addEventListener("click", () => { input.value = ""; apply(); input.focus(); });
  }

  // activity strips of the index: one time axis for every row; hover shows the bucket on all
  // rows at once (where sets overlap), a click opens that row's report narrowed to the bucket
  function setupStrips() {
    const table = document.querySelector("table.sets[data-axis-lo]");
    if (!table) return;
    const lo = +table.dataset.axisLo, hi = +table.dataset.axisHi, size = +table.dataset.axisBucket;
    const n = Math.round((hi - lo) / (size * 3.6e6));
    const strips = Array.from(table.querySelectorAll("svg.strip"));
    const bins = new Map(strips.map((s) => [s, new Map((s.dataset.bins || "").split(";").filter(Boolean)
      .map((t) => t.split(",").map(Number)).map(([i, c, e]) => [i, [c, e]]))]));
    // its own box in the tooltips' look: it follows the pointer and changes with the bucket
    const tip = document.createElement("div");
    tip.className = "atip strip-tip"; tip.hidden = true;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    const stamp = (ms) => new Date(ms).toISOString().slice(0, 16).replace(/[-:]/g, "");
    const label = (ms) => zoned(ms).slice(5, 16).replace("T", " ");
    const at = (svg, ev) => {
      const r = svg.getBoundingClientRect();
      return Math.max(0, Math.min(n - 1, Math.floor(((ev.clientX - r.left) / r.width) * n)));
    };
    let shown = -1;
    const show = (i) => {
      if (i === shown) return;
      shown = i;
      table.style.setProperty("--strip-x", i < 0 ? "-10" : String(((i + 0.5) / n) * 100) + "%");
      table.classList.toggle("strip-hover", i >= 0);
    };
    strips.forEach((svg) => {
      const link = svg.closest("a");
      svg.addEventListener("mousemove", (ev) => {
        const i = at(svg, ev);
        show(i);
        const [c, e] = bins.get(svg).get(i) || [0, 0];
        const t0 = lo + i * size * 3.6e6;
        tip.innerHTML = card({
          h: `${label(t0)}${size > 1 ? " – " + label(t0 + size * 3.6e6).slice(6) : ""} ${zoneName(t0)}`,
          rows: c ? [["Entries", fmt(c)], ["Errors", fmt(e), e ? "e" : "m"]] : [["Entries", "none"]],
          note: c ? "Click to open this window in the report" : null,
        });
        tip.hidden = false;
        tip.classList.add("on");
        tip.style.left = Math.max(8, Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8)) + "px";
        tip.style.top = ev.clientY + 18 + "px";
      });
      svg.addEventListener("mouseleave", () => { show(-1); tip.hidden = true; tip.classList.remove("on"); });
      if (link) link.addEventListener("click", (ev) => {
        const i = at(svg, ev);
        if (!(bins.get(svg).get(i) || [0])[0]) return;  // empty bucket: open the whole set
        ev.preventDefault();
        // the bucket with an hour of context on each side
        const a = lo + (i * size - 1) * 3.6e6, b = lo + ((i + 1) * size + 1) * 3.6e6;
        location.href = link.dataset.href + "#t=" + stamp(a) + "-" + stamp(b);
      });
    });
  }

  // verdicts: the badge says what to look at; baseline, counts and reasons are in its tooltip
  const verdicts = {};
  const captureNodes = {};
  function badgeOf(t) {
    const s = t.summary || {};
    const by = s.new_by_level || {};
    switch (t.verdict) {
      case "new_errors": return ["bad", fmt((by.ERROR || 0) + (by.FATAL || 0)) + " new error types"];
      case "new_warnings": return ["warn", fmt(s.new) + " new warning types"];
      case "new_exceptions": return ["warn", fmt(s.new_exceptions) + " new exception types"];
      case "clean": return ["ok", "no new types"];
      default: return ["none", "no baseline"];
    }
  }
  function verdictHtml(t, id) {
    const href = "/sets/" + encodeURIComponent(id) + "/triage";
    const [cls, text] = badgeOf(t);
    return `<a href="${href}" data-tip-k="wa-verdict" data-id="${esc(id)}"><span class="verdict-badge ${cls}">${text}</span></a>`;
  }

  const RANK = { new_errors: 4, new_warnings: 3, new_exceptions: 2, clean: 1, no_baseline: 0 };
  const LABEL = {
    new_errors: ["bad", "new error types"], new_warnings: ["warn", "new warning types"],
    new_exceptions: ["warn", "new exception types"], clean: ["ok", "no new types"],
    no_baseline: ["none", "no baseline"],
  };
  function captureVerdictHtml(nodes, capture) {
    captureNodes[capture] = nodes;
    const known = nodes.filter((n) => n.verdict in RANK);
    if (!known.length) return '<span class="none">–</span>';
    const worst = known.reduce((a, b) => (RANK[b.verdict] > RANK[a.verdict] ? b : a)).verdict;
    const [cls, text] = LABEL[worst];
    return `<button type="button" class="badge-btn" data-tip-k="wa-capverdict" data-capture="${esc(capture)}">` +
      `<span class="verdict-badge ${cls}">${text}</span></button>`;
  }

  // ---- rich tooltips (tips.js): built on hover from the page's data ----
  const card = (o) => (window.RagnalogTips ? window.RagnalogTips.card(o) : esc(o.h || ""));
  const mono = (s) => ({ html: `<span class="atip-mono">${esc(s)}</span>` });
  const sizeText = (v) => {
    let n = +v || 0;
    for (const u of ["B", "KB", "MB", "GB"]) {
      if (n < 1024 || u === "GB") return u === "B" ? `${n.toFixed(0)} B` : `${n.toFixed(1)} ${u}`;
      n /= 1024;
    }
    return "";
  };
  /** A UTC ISO time in the shown zone, to the second. */
  const stampSec = (iso) => {
    const ms = parseIso(iso);
    if (ms !== ms) return null;
    return new Date(tzLocal ? ms - new Date(ms).getTimezoneOffset() * 6e4 : ms).toISOString().slice(0, 19).replace("T", " ");
  };
  const span = (ms) => {
    const h = ms / 3.6e6;
    if (h < 1) return Math.max(0, Math.round(ms / 6e4)) + " min";
    return h < 48 ? h.toFixed(1) + " h" : (h / 24).toFixed(1) + " days";
  };
  const pct = (n, total) => {
    if (!total) return "";
    const p = (n / total) * 100;
    return p > 0 && p < 0.1 ? "<0.1%" : p.toFixed(1) + "%";
  };
  const perHour = (x) => (x == null || x === "" ? null : (+x > 0 && +x < 0.1 ? "<0.1" : (+x).toFixed(1)));
  const baseName = (path) => String(path || "").split(/[\\/]/).pop();

  function setupTips() {
    // the web app's own pages only (the report and cluster pages load this file for the
    // navigation bar and register their own builders); keys carry a "wa-" prefix
    const T = window.RagnalogTips;
    if (!T || !document.body.classList.contains("app")) return;
    const dataEl = $("sets-data");
    let list = [];
    try { list = dataEl ? JSON.parse(dataEl.textContent) : []; } catch (e) { list = []; }
    const byId = new Map(list.map((s) => [s.id, s]));
    const membersOf = (capture) => list.filter((s) => s.capture === capture)
      .sort((a, b) => String(a.node || a.name).localeCompare(String(b.node || b.name)));
    const rowOf = (el) => el.closest("tr") || { dataset: {} };
    const rowSet = (el) => byId.get(rowOf(el).dataset.set);
    const rowCapture = (el) => rowOf(el).dataset.capture;

    T.register("wa-set", (el) => {
      const s = rowSet(el);
      if (!s) return "";
      const files = s.sources || [];
      const shown = files.slice(0, 8).map((f) =>
        `<li><span>${esc(baseName(f.path))}</span><span>${sizeText(f.size)}</span></li>`).join("");
      return card({
        h: s.name || s.id,
        sub: mono(s.id),
        rows: [
          ["Format", s.format], ["Node", s.node], ["Cluster", s.cluster],
          ["Entries", fmt(s.entry_count)], ["Groups", fmt(s.group_count)],
          ["Ingested", s.created_at ? stampSec(s.created_at) + " " + zoneName(parseIso(s.created_at)) : null],
          ["Recipe", s.stale ? "older than this tool's: ragnalog upgrade rebuilds it" : null, "e"],
        ],
        body: files.length
          ? `<div class="tip-label">${files.length} source file${files.length === 1 ? "" : "s"}</div>` +
            `<ul class="tip-files">${shown}</ul>` +
            (files.length > 8 ? `<div class="atip-sub">and ${files.length - 8} more</div>` : "")
          : "",
        note: "Opens the report",
      });
    });

    T.register("wa-capture", (el) => {
      const id = rowCapture(el);
      const members = membersOf(id);
      return card({
        h: el.textContent.trim(),
        sub: { html: `capture <span class="atip-mono">${esc(id)}</span>` },
        rows: members.map((m) => [m.node || m.name || m.id, `${fmt(m.entry_count)} entries · ${sizeText(m.bytes)}`]),
        note: "Opens the cluster view: all nodes on one time line",
      });
    });

    T.register("wa-range", (el) => {
      const r = el.querySelector(".tz-range");
      if (!r) return "";
      const a = parseIso(r.dataset.a), b = parseIso(r.dataset.b);
      const s = rowSet(el);
      const entries = s ? s.entry_count : membersOf(rowCapture(el)).reduce((n, m) => n + (m.entry_count || 0), 0);
      const hours = (b - a) / 3.6e6;
      return card({
        h: "Log time range",
        sub: zoneName(b),
        rows: [
          ["First entry", stampSec(r.dataset.a)], ["Last entry", stampSec(r.dataset.b)], ["Span", span(b - a)],
          ["Average", hours > 0 ? fmt(Math.round(entries / hours)) + " entries per hour" : null],
        ],
        note: "The strip below: entries per hour up, errors down, every row on one time axis. Click it to open that hour.",
      });
    });

    T.register("wa-levels", (el) => {
      const s = rowSet(el);
      if (s && s.summary) {
        const sum = s.summary, total = s.entry_count;
        return card({
          h: "Errors and warnings",
          rows: [
            ["Errors", { html: `${fmt(sum.errors)} <span class="m">· ${esc(pct(sum.errors, total))}</span>` }, sum.errors ? "e" : "m"],
            ["Error groups", sum.error_groups != null ? fmt(sum.error_groups) + " of " + fmt(s.group_count) : null],
            ["Warnings", { html: `${fmt(sum.warnings)} <span class="m">· ${esc(pct(sum.warnings, total))}</span>` }],
            ["Entries", fmt(total), "m"],
          ],
          note: "Open the report to see them by group",
        });
      }
      const members = membersOf(rowCapture(el)).filter((m) => m.summary);
      if (!members.length) return "";
      return card({
        h: "Errors and warnings per node",
        rows: members.map((m) => [m.node || m.name || m.id,
          `${fmt(m.summary.errors)} errors · ${fmt(m.summary.warnings)} warnings`]),
      });
    });

    T.register("wa-verdict", (el) => {
      const t = verdicts[el.dataset.id];
      if (!t) return "";
      const warn = (t.warnings || []).map((w) => `<div class="tip-warn">${esc(w)}</div>`).join("");
      if (t.verdict === "no_baseline") {
        return card({
          h: "No baseline",
          body: warn || '<div class="atip-text">No other set of this format could serve as one.</div>',
          note: "Opens the triage page, where you can pick a baseline",
        });
      }
      const s = t.summary || {}, b = t.baseline || {};
      const by = Object.entries(s.new_by_level || {}).map(([k, v]) => (k === "ERROR" || k === "FATAL"
        ? `<span class="e">${fmt(v)} ${esc(k.toLowerCase())}</span>` : `${fmt(v)} ${esc(k.toLowerCase())}`)).join(" · ");
      const reason = b.reason
        ? `<div class="tip-label">Why this baseline</div><ul class="tip-list">${String(b.reason).split("; ").map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`
        : "";
      return card({
        h: badgeOf(t)[1].replace(/^./, (c) => c.toUpperCase()),
        sub: { html: `vs <b>${esc(b.name || b.id)}</b> · ${esc(rangeText(zoned(parseIso(b.first_ts)), zoned(parseIso(b.last_ts))))}` },
        rows: [
          ["New types", { html: fmt(s.new) + (by ? ` <span class="m">·</span> ${by}` : "") }],
          ["New exceptions", fmt(s.new_exceptions)],
          ["Grew ≥ ×2 per hour", fmt(s.grew) + " of " + fmt(s.changed) + " changed"],
          ["Vanished", fmt(s.vanished), "m"],
          ["Unchanged", fmt(s.unchanged), "m"],
        ],
        body: warn + reason,
        note: "Opens the triage page",
      });
    });

    T.register("wa-capverdict", (el) => {
      const nodes = captureNodes[el.dataset.capture] || [];
      return card({
        h: "Worst verdict of the nodes",
        sub: "each node against its own previous set",
        rows: nodes.map((n) => {
          const t = verdicts[n.id];
          const [cls, text] = t ? badgeOf(t) : ["none", "not known"];
          return [n.node || n.id, { html: `<span class="tip-verdict ${cls}">${esc(text)}</span>` }];
        }),
        note: "Each node's row links to its own triage",
      });
    });

    // triage rows: the whole template, where it comes from, counts, rates and times
    T.register("wa-trow", (el) => {
      const tr = el.closest("tr");
      if (!tr) return "";
      const d = tr.dataset, kind = el.dataset.kind;
      const lv = (tr.querySelector(".lv") || {}).textContent || "";
      const when = (iso) => (iso ? stampSec(iso) : null);
      let rows;
      if (kind === "grew") {
        rows = [
          ["Count", `${fmt(+d.ca)} → ${fmt(+d.cb)}`],
          ["Per hour", `${perHour(d.ra)} → ${perHour(d.rb)}  (×${(+d.rr).toFixed(1)})`, "e"],
          ["First seen", when(d.fb)], ["Last seen", when(d.lb)],
          ["In the baseline", d.fa ? `${when(d.fa)} → ${when(d.la)}` : null, "m"],
        ];
      } else if (kind === "vanished") {
        rows = [["Count before", fmt(+d.ca)], ["First seen", when(d.fa)], ["Last seen", when(d.la)]];
      } else {
        rows = [["Count", fmt(+d.cb)], ["Per hour", perHour(d.rb)], ["First seen", when(d.fb)], ["Last seen", when(d.lb)]];
      }
      rows.push(["Logger", d.lg ? mono(d.lg) : null], ["Exception", d.ex ? mono(d.ex) : null], ["Signature", mono(d.sig)]);
      const what = { new: "new in this set", grew: "grew", vanished: "in the baseline only" }[kind] || "";
      return card({
        h: { html: `<span class="tip-tpl">${el.innerHTML}</span>` },
        sub: `${lv} · ${what} · times ${zoneName(Date.now())}`,
        rows,
        note: kind === "vanished" ? "Opens the group in the baseline's report" : "Opens the group in the report",
      });
    });

    // markup the page rendered in a <template> (the triage baseline), in the shown zone
    T.register("wa-ref", (el) => {
      const src = document.getElementById(el.dataset.tipRef);
      if (!src || !src.content) return "";
      const box = document.createElement("div");
      box.appendChild(src.content.cloneNode(true));
      tzIn(box);
      return box.innerHTML;
    });
  }

  function setupUpload() {
    const form = $("upload-form");
    if (!form) return;
    const input = $("files"), drop = $("drop"), list = $("drop-list");
    const upload = $("upload");
    const revealUpload = () => {
      upload.open = true;
      requestAnimationFrame(() => {
        upload.scrollIntoView({ block: "start" });
        input.focus({ preventScroll: true });
      });
    };
    document.querySelectorAll('a[href="#upload"], a[href="/#upload"]').forEach((link) => {
      link.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (location.hash !== "#upload") history.pushState(null, "", "#upload");
        revealUpload();
      });
    });
    window.addEventListener("hashchange", () => { if (location.hash === "#upload") revealUpload(); });
    if (location.hash === "#upload") revealUpload();
    const showFiles = () => {
      const names = Array.from(input.files || []).map((f) => esc(f.name) + " (" + (f.size / 1048576).toFixed(1) + " MB)");
      list.innerHTML = names.join(" · ");
    };
    input.addEventListener("change", showFiles);
    ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
    drop.addEventListener("drop", (ev) => { ev.preventDefault(); input.files = ev.dataTransfer.files; showFiles(); });

    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const data = new FormData(form);
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      const job = $("job"); job.hidden = false; job.classList.remove("failed");
      $("job-state").textContent = "uploading";
      $("job-detail").textContent = "";
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/ingest");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar(e.loaded / e.total * 0.3); };
      xhr.onload = () => {
        let doc = null;
        try { doc = JSON.parse(xhr.responseText); } catch (e) { doc = null; }
        if (xhr.status !== 202 || !doc) { fail(doc); submit.disabled = false; return; }
        poll(doc.id, submit);
      };
      xhr.onerror = () => { fail(null); submit.disabled = false; };
      xhr.send(data);
    });
  }

  function bar(fraction) {
    const pct = Math.max(0, Math.min(100, fraction * 100));
    $("job-bar").style.width = pct.toFixed(1) + "%";
    document.querySelector("#job .progress").setAttribute("aria-valuenow", pct.toFixed(0));
  }
  function fail(doc) {
    $("job").classList.add("failed");
    $("job-state").textContent = "failed";
    $("job-detail").textContent = doc && doc.error ? doc.error.message : "upload failed";
  }
  function poll(id, submit) {
    const cancel = $("job-cancel");
    cancel.hidden = false;
    cancel.onclick = () => fetch("/api/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
    let misses = 0;
    const stop = (doc) => { fail(doc); submit.disabled = false; cancel.hidden = true; };
    const tick = () => fetch("/api/jobs/" + encodeURIComponent(id)).then((r) => {
      // jobs live in the server's memory: a 404 means it restarted and the job is gone
      if (r.status === 404) return Promise.reject(new Error("the job is gone (the server restarted?)"));
      if (!r.ok) return Promise.reject(new Error("HTTP " + r.status));
      return r.json();
    }).then((j) => {
      misses = 0;
      const p = j.progress || {};
      $("job-state").textContent = j.state;
      $("job-detail").textContent = fmt(p.entries) + " entries" + (p.current_file ? " · " + p.current_file : "");
      bar(0.3 + 0.7 * (p.fraction || 0));
      if (j.state === "succeeded") {
        bar(1);
        // a split upload (one set per node) opens the cluster page of its capture
        const href = j.capture
          ? "/captures/capture:" + encodeURIComponent(j.capture.capture.capture)
          : "/sets/" + encodeURIComponent(j.result.id) + "?compare=auto";
        // files that are not logs of the format are left out: say so instead of moving on
        const skipped = j.capture ? [] : ((j.result.ingest || {}).skipped || []);
        if (!skipped.length) { location.href = href; return; }
        cancel.hidden = true; submit.disabled = false;
        $("job-state").textContent = "done";
        const names = skipped.map((p) => esc(String(p).split(/[\/]/).pop()));
        $("job-detail").innerHTML = fmt((j.progress || {}).entries ?? j.result.entry_count) + " entries · skipped " + names.length + " file(s) that are not " +
          esc(j.result.format || "in the format") + ": " + names.slice(0, 5).join(", ") + (names.length > 5 ? " …" : "") +
          ' · <a href="' + href + '">Open the report</a>';
      } else if (j.state === "failed" || j.state === "cancelled") {
        stop(j.error ? j : { error: { message: j.state } });
      } else {
        setTimeout(tick, 700);
      }
    }).catch((err) => {
      // a hiccup is retried with back-off; a gone job or five misses in a row end the wait
      misses += 1;
      if (/gone/.test(err.message) || misses >= 5) stop({ error: { message: err.message } });
      else setTimeout(tick, 700 * 2 ** misses);
    });
    tick();
  }
})();
