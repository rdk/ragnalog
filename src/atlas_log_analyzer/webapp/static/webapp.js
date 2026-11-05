/* atlas-log web app: theme, set switcher, triage verdicts, upload with job progress. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-US") : "–");
  const KEY = "atlas-log-report-theme";

  function currentTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function setThemeLabel() {
    const b = $("shell-theme");
    if (b) b.textContent = currentTheme() === "dark" ? "☀ Light" : "☾ Dark";
  }
  function toggleTheme() {
    if (window.atlasTheme) { window.atlasTheme(); return; }  // the report page owns the toggle
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
    setThemeLabel();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const tb = $("shell-theme");
    if (tb) { setThemeLabel(); tb.addEventListener("click", toggleTheme); }

    // set switcher on the report page keeps compare/filters (hash) when switching sets
    const sw = $("shell-set");
    if (sw) sw.addEventListener("change", () => {
      location.href = "/sets/" + encodeURIComponent(sw.value) + location.search + location.hash;
    });

    // triage verdict per set (one request each; the server caches nothing here, diffs are fast)
    document.querySelectorAll("[data-triage]").forEach((cell) => {
      const id = cell.getAttribute("data-triage");
      fetch("/api/sets/" + encodeURIComponent(id) + "/triage?min_level=WARN&limit=1")
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((t) => { cell.innerHTML = verdictHtml(t, id); })
        .catch(() => { cell.innerHTML = '<span class="muted">–</span>'; });
    });

    document.querySelectorAll("[data-delete]").forEach((b) => b.addEventListener("click", () => {
      const id = b.getAttribute("data-delete");
      if (!confirm("Delete log set " + b.getAttribute("data-label") + "? This removes its Parquet files from the store.")) return;
      fetch("/api/sets/" + encodeURIComponent(id), { method: "DELETE" })
        .then((r) => (r.ok ? location.reload() : r.json().then((d) => alert(d.error ? d.error.message : "Delete failed"))));
    }));

    setupUpload();
  });

  function verdictHtml(t, id) {
    if (t.verdict === "no_baseline") {
      const why = esc((t.warnings || [])[0] || "no other set of this format");
      return `<span class="verdict-badge none" title="${why}">no baseline</span>`;
    }
    const s = t.summary || {};
    const base = t.baseline ? esc(t.baseline.name || t.baseline.id) : "";
    const href = "/sets/" + encodeURIComponent(id) + "/triage";
    const badge = t.verdict === "new_errors"
      ? `<span class="verdict-badge bad">${fmt((s.new_by_level || {}).ERROR || 0)} new error types</span>`
      : t.verdict === "new_warnings"
        ? `<span class="verdict-badge warn">${fmt(s.new)} new warning types</span>`
        : t.verdict === "new_exceptions"
          ? `<span class="verdict-badge warn">${fmt(s.new_exceptions)} new exception types</span>`
          : '<span class="verdict-badge ok">no new types</span>';
    const why = t.baseline && t.baseline.reason ? esc(": " + t.baseline.reason) : "";
    return `<a href="${href}" title="Compared with ${base}${why}">${badge}</a><span class="counts">vs ${base}</span>`;
  }

  function setupUpload() {
    const form = $("upload-form");
    if (!form) return;
    const input = $("files"), drop = $("drop"), list = $("drop-list");
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
    const tick = () => fetch("/api/jobs/" + encodeURIComponent(id)).then((r) => r.json()).then((j) => {
      const p = j.progress || {};
      $("job-state").textContent = j.state;
      $("job-detail").textContent = fmt(p.entries) + " entries" + (p.current_file ? " · " + p.current_file : "");
      bar(0.3 + 0.7 * (p.fraction || 0));
      if (j.state === "succeeded") {
        bar(1);
        location.href = "/sets/" + encodeURIComponent(j.result.id) + "?compare=auto";
      } else if (j.state === "failed" || j.state === "cancelled") {
        fail(j.error ? j : { error: { message: j.state } });
        submit.disabled = false; cancel.hidden = true;
      } else {
        setTimeout(tick, 700);
      }
    });
    tick();
  }
})();
