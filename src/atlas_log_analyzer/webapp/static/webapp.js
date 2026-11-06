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

    // set and node switchers on the report page keep compare/filters (hash) when switching
    ["shell-set", "shell-node"].forEach((id) => {
      const sw = $(id);
      if (sw) sw.addEventListener("change", () => {
        location.href = "/sets/" + encodeURIComponent(sw.value) + location.search + location.hash;
      });
    });

    // triage verdict per set (one request each; the server caches nothing here, diffs are fast)
    const byCapture = {};
    document.querySelectorAll("[data-triage]").forEach((cell) => {
      const id = cell.getAttribute("data-triage");
      const verdict = fetch("/api/sets/" + encodeURIComponent(id) + "/triage?min_level=WARN&limit=1")
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((t) => { cell.innerHTML = verdictHtml(t, id); return t.verdict; })
        .catch(() => { cell.innerHTML = '<span class="muted">–</span>'; return null; });
      const row = cell.closest("tr");
      const capture = row && row.getAttribute("data-member-of");
      if (capture) (byCapture[capture] = byCapture[capture] || []).push(
        verdict.then((v) => ({ node: cell.getAttribute("data-node"), verdict: v })));
    });
    // a capture's verdict is the worst of its nodes'
    document.querySelectorAll("[data-capture-verdict]").forEach((cell) => {
      Promise.all(byCapture[cell.getAttribute("data-capture-verdict")] || [])
        .then((nodes) => { cell.innerHTML = captureVerdictHtml(nodes); });
    });

    document.querySelectorAll("[data-delete]").forEach((b) => b.addEventListener("click", () => {
      const id = b.getAttribute("data-delete");
      if (!confirm("Delete log set " + b.getAttribute("data-label") + "? This removes its Parquet files from the store.")) return;
      b.disabled = true;
      fetch("/api/sets/" + encodeURIComponent(id), { method: "DELETE" })
        .then((r) => (r.ok ? location.reload() : r.json().catch(() => ({})).then((d) => {
          alert(d.error ? d.error.message : "Delete failed (HTTP " + r.status + ")");
          b.disabled = false;
        })))
        .catch(() => { alert("Delete failed: the server did not answer"); b.disabled = false; });
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

  const RANK = { new_errors: 4, new_warnings: 3, new_exceptions: 2, clean: 1, no_baseline: 0 };
  const LABEL = {
    new_errors: ["bad", "new error types"], new_warnings: ["warn", "new warning types"],
    new_exceptions: ["warn", "new exception types"], clean: ["ok", "no new types"],
    no_baseline: ["none", "no baseline"],
  };
  function captureVerdictHtml(nodes) {
    const known = nodes.filter((n) => n.verdict in RANK);
    if (!known.length) return '<span class="muted">–</span>';
    const worst = known.reduce((a, b) => (RANK[b.verdict] > RANK[a.verdict] ? b : a)).verdict;
    const on = known.filter((n) => n.verdict === worst).map((n) => esc(n.node));
    const [cls, text] = LABEL[worst];
    return `<span class="verdict-badge ${cls}" title="Worst verdict of the nodes, each against its own previous set">${text}</span>` +
      `<span class="counts">on ${on.join(", ")} (${on.length} of ${nodes.length})</span>`;
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
        cancel.hidden = true;
        $("job-state").textContent = "done";
        const names = skipped.map((p) => esc(String(p).split(/[\/]/).pop()));
        $("job-detail").innerHTML = fmt(j.entries) + " entries · skipped " + names.length + " file(s) that are not " +
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
