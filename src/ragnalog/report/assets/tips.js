/* ragnalog rich tooltips: one look for the report, the cluster page and the web app.
 *
 * An element gets a tooltip from, in order:
 *   data-tip-k="KEY"  a builder registered with RagnalogTips.register(KEY, (el) => html),
 *                     run on hover (keeps big tables light: nothing is built up front);
 *   data-tip="HTML"   markup the page built and escaped (RagnalogTips.card makes one);
 *   title="text"      plain text: the native tooltip is replaced by this one on first hover.
 * It appears after a short delay, at once when moving from one tipped element to the
 * next, and on keyboard focus. Escape, scrolling and a click hide it. Touch is left alone.
 */
(function () {
  "use strict";
  if (window.RagnalogTips) return;

  const SHOW_MS = 260;
  const WARM_MS = 500;
  const GAP = 14;
  const builders = new Map();
  let box = null;
  let cur = null;
  let timer = 0;
  let warmUntil = 0;
  let px = 0;
  let py = 0;

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
  }
  /** A value for card(): a string is text (escaped); {html: "..."} is trusted markup. */
  function part(v) {
    return v && typeof v === "object" && "html" in v ? String(v.html) : esc(v);
  }

  /**
   * Markup of a structured tooltip. Every field is optional:
   *   h     the heading (one line, wraps when long)
   *   sub   a muted line under it (a path, an id, a time range)
   *   lead  free markup (trusted) before the rows, e.g. a small chart
   *   rows  [[label, value, cls?], ...] as a two-column list; cls colours the value
   *   body  free markup (trusted) between the rows and the note
   *   note  a muted closing sentence: what clicking does, how a number is counted
   *   wide  true for a wider tooltip (long templates, charts)
   */
  function card(o) {
    let out = "";
    if (o.h != null) out += `<div class="atip-h">${part(o.h)}</div>`;
    if (o.sub != null) out += `<div class="atip-sub">${part(o.sub)}</div>`;
    if (o.lead) out += `<div class="atip-body">${o.lead}</div>`;
    const rows = (o.rows || []).filter((r) => r && r[1] != null && r[1] !== "");
    if (rows.length) {
      out += '<dl class="atip-kv">' + rows.map((r) =>
        `<dt>${part(r[0])}</dt><dd${r[2] ? ` class="${esc(r[2])}"` : ""}>${part(r[1])}</dd>`).join("") + "</dl>";
    }
    if (o.body) out += `<div class="atip-body">${o.body}</div>`;
    if (o.note != null) out += `<div class="atip-note">${part(o.note)}</div>`;
    return o.wide && out ? `<div class="atip-wide">${out}</div>` : out;
  }

  function node() {
    if (!box) {
      box = document.createElement("div");
      box.className = "atip";
      box.id = "atip";
      box.setAttribute("role", "tooltip");
      box.hidden = true;
      document.body.appendChild(box);
    }
    return box;
  }

  function tipped(n) {
    return n.hasAttribute("data-tip-k") || n.hasAttribute("data-tip") ||
      n.hasAttribute("data-tip-text") || n.hasAttribute("title");
  }
  function find(start) {
    for (let n = start; n && n.nodeType === 1; n = n.parentElement) {
      if (n === document.body || n === document.documentElement) return null;
      if (n.closest(".atip")) return null;
      if (tipped(n)) return n;
    }
    return null;
  }
  /** Move a native title into data-tip-text so the browser's own tooltip never shows. */
  function adopt(n) {
    if (!n.hasAttribute("title")) return;
    const t = n.getAttribute("title");
    n.removeAttribute("title");
    if (!t) return;
    n.setAttribute("data-tip-text", t);
    if (!n.hasAttribute("aria-label") && !n.textContent.trim()) n.setAttribute("aria-label", t);
  }
  function content(n) {
    const k = n.getAttribute("data-tip-k");
    if (k && builders.has(k)) {
      try {
        const h = builders.get(k)(n);
        if (h) return h;
      } catch (e) {
        console.error("tooltip", k, e);
      }
    }
    const h = n.getAttribute("data-tip");
    if (h) return h;
    const t = n.getAttribute("data-tip-text");
    return t ? `<div class="atip-text">${esc(t)}</div>` : "";
  }

  function place(n, byKey) {
    const b = node();
    const w = b.offsetWidth;
    const h = b.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    let x;
    let y;
    if (byKey) {
      const r = n.getBoundingClientRect();
      x = r.left;
      y = r.bottom + 8;
      if (y + h > vh - 8) y = r.top - h - 8;
    } else {
      x = px + GAP;
      y = py + GAP + 4;
      if (x + w > vw - 8) x = px - w - GAP;
      if (n.hasAttribute("data-tip-up") && py - h - GAP >= 8) y = py - h - GAP - 4;
      else if (y + h > vh - 8) y = py - h - GAP;
    }
    b.style.left = Math.max(8, Math.min(x, vw - w - 8)) + "px";
    b.style.top = Math.max(8, y) + "px";
  }

  function show(n, byKey) {
    clearTimeout(timer);
    const html = content(n);
    if (!html) {
      hide();
      return;
    }
    const b = node();
    if (cur && cur !== n && cur.getAttribute("aria-describedby") === "atip") {
      cur.removeAttribute("aria-describedby");
    }
    b.innerHTML = html;
    b.classList.toggle("atip-plain", !n.hasAttribute("data-tip") && !n.hasAttribute("data-tip-k"));
    b.hidden = false;
    b.classList.remove("on");
    cur = n;
    if (!n.hasAttribute("aria-describedby")) n.setAttribute("aria-describedby", "atip");
    place(n, byKey);
    requestAnimationFrame(() => b.classList.add("on"));
  }

  function hide() {
    clearTimeout(timer);
    if (cur) {
      if (cur.getAttribute("aria-describedby") === "atip") cur.removeAttribute("aria-describedby");
      warmUntil = Date.now() + WARM_MS;
      cur = null;
    }
    if (box) {
      box.hidden = true;
      box.classList.remove("on");
    }
  }

  function hover(e) {
    if (e.pointerType === "touch") return;
    px = e.clientX;
    py = e.clientY;
    const n = find(e.target);
    if (n === cur && n) return;
    if (!n) {
      if (cur) hide();
      clearTimeout(timer);
      return;
    }
    adopt(n);
    clearTimeout(timer);
    if (cur || Date.now() < warmUntil) show(n, false);
    else timer = setTimeout(() => show(n, false), SHOW_MS);
  }

  document.addEventListener("pointerover", hover, { passive: true });
  document.addEventListener("pointermove", (e) => {
    px = e.clientX;
    py = e.clientY;
  }, { passive: true });
  document.addEventListener("pointerout", (e) => {
    if (!e.relatedTarget) hide();
  }, { passive: true });
  /** Text fields keep their hint in the placeholder: a tooltip would cover what is typed. */
  const TYPING = "input:not([type=checkbox],[type=radio],[type=button],[type=submit],[type=reset],[type=file]),textarea,select,[contenteditable]";
  /** True after the last key press, false after the last pointer press: focus a script moves
   * after a mouse click (the drawer opening) shows no tooltip. */
  let byKeys = false;
  document.addEventListener("keydown", () => { byKeys = true; }, { capture: true, passive: true });
  document.addEventListener("pointerdown", () => { byKeys = false; }, { capture: true, passive: true });
  document.addEventListener("focusin", (e) => {
    const t = e.target;
    if (!byKeys || !(t instanceof Element) || !t.matches(":focus-visible") || t.matches(TYPING)) return;
    const n = find(t);
    if (!n) return;
    adopt(n);
    show(n, true);
  });
  document.addEventListener("focusout", () => hide());
  document.addEventListener("keydown", (e) => {
    // Esc dismisses an open tip only: the page's own Esc (close the drawer, …) waits for the next one
    if (e.key === "Escape" && cur) { hide(); e.stopImmediatePropagation(); }
  });
  document.addEventListener("input", () => {
    if (cur) hide();
  }, { passive: true });
  document.addEventListener("pointerdown", () => hide(), { passive: true });
  window.addEventListener("scroll", () => {
    if (cur) hide();
  }, { passive: true, capture: true });

  window.RagnalogTips = {
    /** Register a builder for data-tip-k="KEY": (el) => markup (use card()). */
    register(key, fn) {
      builders.set(key, fn);
    },
    card,
    esc,
    hide,
  };
})();
