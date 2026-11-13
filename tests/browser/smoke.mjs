// Browser smoke driver: Chrome DevTools Protocol over Node's global WebSocket (Node >= 22).
// Usage: node smoke.mjs CONFIG_JSON   {base, cdp, sets: {name: id}, capture: id}
// Prints one JSON document: {pages: [{name, checks: [{name, ok, detail}], problems: [...]}]}.

const cfg = JSON.parse(process.argv[2]);
const BASE = cfg.base;
const ORIGIN = new URL(BASE).origin;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect() {
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    try {
      const res = await fetch(`${cfg.cdp}/json/list`);
      target = (await res.json()).find((t) => t.type === "page");
    } catch {
      /* browser still starting */
    }
    if (!target) await sleep(100);
  }
  if (!target) throw new Error("no page target from the browser");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = () => rej(new Error("websocket error"));
  });
  return ws;
}

const ws = await connect();
let nextId = 1;
const pending = new Map();
const listeners = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { res, rej } = pending.get(m.id);
    pending.delete(m.id);
    if (m.error) rej(new Error(m.error.message));
    else res(m.result);
  } else if (m.method) {
    for (const l of [...listeners]) l(m.method, m.params);
  }
};
const send = (method, params = {}) =>
  new Promise((res, rej) => {
    const id = nextId++;
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });

let problems = [];
let docStatus = {};
listeners.push((method, p) => {
  if (method === "Runtime.exceptionThrown") {
    const d = p.exceptionDetails;
    const text = (d.exception && (d.exception.description || d.exception.value)) || d.text;
    problems.push(`JS exception: ${String(text).split("\n")[0]}`);
  } else if (method === "Runtime.consoleAPICalled" && p.type === "error") {
    const text = p.args.map((a) => a.value ?? a.description ?? "").join(" ");
    problems.push(`console.error: ${text.slice(0, 300)}`);
  } else if (method === "Network.responseReceived") {
    const r = p.response;
    if (r.url.startsWith(ORIGIN)) {
      if (p.type === "Document") docStatus[new URL(r.url).pathname] = r.status;
      if (r.status >= 400) problems.push(`HTTP ${r.status}: ${r.url.slice(ORIGIN.length)}`);
    }
  }
});

async function evaluate(expr) {
  const r = await send("Runtime.evaluate", {
    expression: expr,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error(`evaluate failed: ${r.exceptionDetails.text} in ${expr}`);
  return r.result.value;
}

async function waitFor(expr, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try {
      const v = await evaluate(expr);
      if (v) return v;
    } catch {
      /* page is navigating */
    }
    await sleep(50);
  }
  return false;
}

async function open(url) {
  const loaded = new Promise((res) => {
    const l = (method) => {
      if (method === "Page.loadEventFired") {
        listeners.splice(listeners.indexOf(l), 1);
        res();
      }
    };
    listeners.push(l);
  });
  await send("Page.navigate", { url: new URL(url, BASE).href });
  await Promise.race([loaded, sleep(15000)]);
}

const count = (sel) => `document.querySelectorAll(${JSON.stringify(sel)}).length`;
const visible = (sel) =>
  `(() => { const e = document.querySelector(${JSON.stringify(sel)}); ` +
  `return !!e && !e.hidden && e.getClientRects().length > 0; })()`;

const results = [];

/** Run one page scenario: `body(check, expect)` registers checks; collected problems are reported. */
async function page(name, body, { allow = [] } = {}) {
  problems = [];
  docStatus = {};
  const checks = [];
  const check = (cname, ok, detail = "") =>
    checks.push({ name: cname, ok: !!ok, detail: String(detail) });
  const expect = async (cname, expr, timeout) => {
    const v = await waitFor(expr, timeout);
    check(cname, v, v ? "" : `never true: ${expr}`);
    return v;
  };
  try {
    await body(check, expect);
  } catch (e) {
    check("scenario ran to the end", false, e.message);
  }
  await sleep(150);
  const seen = problems.filter((p) => !allow.some((a) => p.includes(a)));
  results.push({ name, checks, problems: seen });
}

const S = cfg.sets;

await send("Page.enable");
await send("Runtime.enable");
await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 900,
  deviceScaleFactor: 1,
  mobile: false,
});
await send("Emulation.setEmulatedMedia", {
  features: [{ name: "prefers-color-scheme", value: "light" }],
});

await page("index", async (check, expect) => {
  await open("/");
  await expect("at least 4 set rows", `${count("table.sets tr[data-set]")} >= 4`);
  await expect("capture row listed", `${count("table.sets tr.capture-row")} >= 1`);
  await expect("triage cells resolved", `!document.querySelector("td.triage .pending")`);
  check("ingest form is collapsed while browsing existing sets", await evaluate(`!document.getElementById("upload").open`));
  await evaluate(`document.querySelector(".ingest-link").click()`);
  await expect("Ingest logs reveals the upload form", `document.getElementById("upload").open`);
  check("secondary ingest options start collapsed", await evaluate(`!document.getElementById("upload-advanced").open`));
  await evaluate(`document.querySelector("#upload-advanced > summary").click()`);
  await expect("advanced ingest options remain available", `document.getElementById("upload-advanced").open && !!document.querySelector('#upload-advanced input[name="overwrite"]')`);
  await evaluate(`document.querySelector("#upload > summary").click()`);
  await expect("upload form can be collapsed again", `!document.getElementById("upload").open`);
  await open("/?smoke=ingest#upload");
  await expect("direct ingest link opens the form", `document.getElementById("upload").open`);
  await evaluate(`document.querySelector("#upload > summary").click()`);
  const setCount = await evaluate(`document.querySelectorAll("table.sets tr[data-set]").length`);
  await evaluate(`(() => {
    const input = document.getElementById("sets-search");
    input.value = "__no_matching_log_sets__";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  })()`);
  await expect("search hides nonmatching sets", `${count("table.sets tr[data-set]:not([hidden])")} === 0`);
  await expect("search gives a useful empty state", visible("#sets-no-matches"));
  await evaluate(`document.getElementById("sets-clear-search").click()`);
  await expect("clear search restores every set", `${count("table.sets tr[data-set]:not([hidden])")} === ${setCount}`);
  await expect("clear search returns keyboard focus", `document.activeElement.id === "sets-search"`);
  await evaluate(`(() => {
    const member = document.querySelector("table.sets tr[data-member-of]");
    const input = document.getElementById("sets-search");
    input.value = member.dataset.set;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  })()`);
  await expect("search preserves the matching capture and all its nodes", `(() => {
    const member = document.querySelector("table.sets tr[data-member-of]");
    const capture = member.dataset.memberOf;
    const visibleSets = [...document.querySelectorAll("table.sets tr[data-set]:not([hidden])")];
    const members = [...document.querySelectorAll("table.sets tr[data-member-of]")].filter(r => r.dataset.memberOf === capture);
    const headers = [...document.querySelectorAll("table.sets tr[data-capture]:not([hidden])")];
    return visibleSets.length === members.length && visibleSets.every(r => r.dataset.memberOf === capture)
      && headers.length === 1 && headers[0].dataset.capture === capture;
  })()`);
  await expect("search count explains capture grouping", `document.getElementById("sets-search-count").textContent.includes("matching captures include all nodes")`);
  await evaluate(`document.getElementById("sets-search").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
  await expect("Escape resets collection search", `${count("table.sets tr[data-set]:not([hidden])")} === ${setCount}`);
});

await page("report", async (check, expect) => {
  await open(`/sets/${S.week_node2}`);
  await expect("group rows", `${count("#t-body tr")} > 0`);
  await expect("set details are available on demand", `${count("#tiles > *")} > 0 && !document.getElementById("report-details").open`);
  await evaluate(`document.querySelector("#report-details > summary").click()`);
  await expect("set details open", `document.getElementById("report-details").open`);
  await evaluate(`document.querySelector("#report-details > summary").click()`);
  check("advanced filters start collapsed", await evaluate(`!document.getElementById("f-more").open`));
  await evaluate(`document.querySelector("#f-more > summary").click()`);
  await expect("More filters opens the advanced controls", `document.getElementById("f-more").open`);
  await evaluate(`document.getElementById("f-hasexc").click()`);
  await expect("advanced filter affects the URL and control", `location.hash.includes("hx=1") && document.getElementById("f-hasexc").getAttribute("aria-pressed") === "true"`);
  await evaluate(`document.querySelector("#f-more > summary").click()`);
  await expect("collapsed advanced controls disclose an active filter", `!document.getElementById("f-more").open && document.getElementById("f-more-label").textContent.includes("1")`);
  await evaluate(`document.getElementById("f-reset").click()`);
  await expect("Reset clears a filter while its panel is closed", `!location.hash.includes("hx=1") && document.getElementById("f-hasexc").getAttribute("aria-pressed") === "false"`);
  await open(`/sets/${S.week_node2}?smoke=filters#hx=1&view=coarse`);
  await expect("deep-linked advanced filters are exposed", `document.getElementById("f-more").open && document.getElementById("f-hasexc").getAttribute("aria-pressed") === "true" && document.getElementById("f-coarse").getAttribute("aria-pressed") === "true"`);
  await evaluate(`document.getElementById("f-reset").click()`);
  await expect("Reset restores the default group view", `document.getElementById("f-coarse").getAttribute("aria-pressed") === "false" && document.querySelectorAll("#t-body tr").length > 0`);
  await evaluate(`document.querySelector("#t-body tr").click()`);
  await expect("drawer shown", visible("#drawer"));
  await expect("drawer holds an example entry card", `${count("#drawer-body .entry")} > 0`, 12000);
  const href = await expect(
    "'All N entries' link exists",
    `(() => { const a = document.querySelector("#drawer a.d-all"); ` +
      `return a && /^All .*entr/.test(a.textContent) ? a.getAttribute("href") : ""; })()`,
  );
  if (href) {
    await open(href);
    await expect("entries page has rows", `${count("#e-body tr")} > 0`);
    await evaluate(`document.querySelector("#e-body tr").click()`);
    await expect("clicking an entry row opens an entry card", `${count(".entry")} > 0`);
  }
});

await page("triage", async (check, expect) => {
  await open(`/sets/${S.week_node1}/triage`);
  await expect(
    "verdict shown",
    `((document.querySelector(".verdict-badge") || {}).textContent || "").trim().length > 0`,
  );
  await expect("New types has rows", `${count("#s-new tbody tr")} > 0`);
  check("baseline metadata starts collapsed", await evaluate(`!document.querySelector(".baseline-details").open`));
  await evaluate(`document.querySelector(".baseline-details > summary").click()`);
  await expect("baseline rationale remains accessible", `document.querySelector(".baseline-details").open && !!document.querySelector('[data-tip-ref="tip-baseline"]')`);
  await evaluate(`document.querySelector('[data-open="s-gone"]').click()`);
  await expect("compact section link opens its collapsed findings", `document.getElementById("s-gone").open`);
});

await page("cluster", async (check, expect) => {
  await open(`/captures/capture:${cfg.capture}`);
  await expect("groups table rows", `${count("#t-body tr")} > 0`);
  check("secondary chart controls start collapsed", await evaluate(`!document.getElementById("c-chart-options").open`));
  await evaluate(`document.querySelector("#c-chart-options > summary").click()`);
  await expect("Chart options reveals its controls", `document.getElementById("c-chart-options").open`);
  const arrowsBefore = await evaluate(`document.getElementById("tl-arrows").getAttribute("aria-pressed")`);
  await evaluate(`document.getElementById("tl-arrows").click()`);
  await expect("revealed chart control remains functional", `document.getElementById("tl-arrows").getAttribute("aria-pressed") !== ${JSON.stringify(arrowsBefore)}`);
  await evaluate(`document.querySelector("#c-chart-options > summary").click()`);
  await evaluate(`location.hash = "#tab=i"`);
  await expect(
    "interactions tab renders",
    `${visible("#p-inter")} && document.querySelector("#p-inter").children.length > 0`,
  );
  await evaluate(`location.hash = "#tab=l"`);
  await expect("merged log shown", visible("#p-log"));
  await expect("merged log has rows", `${count("#ml .ml-row")} > 0`);
});

await page(
  "not-found",
  async (check, expect) => {
    await open("/nope");
    check("document answered 404", docStatus["/nope"] === 404, `status ${docStatus["/nope"]}`);
    await expect(
      "404 page renders",
      `((document.querySelector("h1") || {}).textContent || "").trim().length > 0 ` +
        `&& !!document.querySelector("a[href='/']")`,
    );
  },
  { allow: ["HTTP 404: /nope"] },
);

await page("report-dark", async (check, expect) => {
  await send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-color-scheme", value: "dark" }],
  });
  await open(`/sets/${S.week_node2}`);
  await expect("group rows", `${count("#t-body tr")} > 0`);
  check("dark scheme active", await evaluate(`matchMedia("(prefers-color-scheme: dark)").matches`));
  const bg = () => evaluate(`getComputedStyle(document.body).backgroundColor`);
  const before = await bg();
  const themeBefore = await evaluate(`document.documentElement.getAttribute("data-theme") || ""`);
  await evaluate(`document.getElementById("btn-theme").click()`);
  await expect(
    "theme toggle changes data-theme",
    `(document.documentElement.getAttribute("data-theme") || "") !== ${JSON.stringify(themeBefore)}`,
  );
  check("theme toggle changes the background", (await bg()) !== before, before);
  await evaluate(`document.getElementById("btn-wide").click()`);
  await expect("full-width toggle sets data-wide", `document.documentElement.hasAttribute("data-wide")`);
  await expect("rows still there", `${count("#t-body tr")} > 0`);
});

const skinValue = `(document.documentElement.getAttribute("data-skin") || "classic")`;
const skinPaint = `getComputedStyle(document.documentElement).getPropertyValue("--blue").trim()`;
async function chooseSkin(skin) {
  await evaluate(`(() => {
    const picker = document.querySelector(".skin-picker");
    if (!picker.open) picker.querySelector("summary").click();
    picker.querySelector('[data-skin-choice="${skin}"]').click();
  })()`);
}

await page("skins", async (check, expect) => {
  await open("/");
  await evaluate(`localStorage.removeItem("atlas-log-skin"); localStorage.removeItem("atlas-log-report-theme"); localStorage.removeItem("atlas-log-wide")`);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
  await open("/?smoke=skins");
  await expect("one skin picker with three choices", `${count(".skin-picker")} === 1 && ${count("[data-skin-choice]")} === 3`);
  check("Aurora is the fresh default", await evaluate(`${skinValue} === "aurora" && localStorage.getItem("atlas-log-skin") === null`));
  const auroraPaint = await evaluate(skinPaint);
  await chooseSkin("classic");
  await expect("Classic remains an explicit saved choice", `${skinValue} === "classic" && localStorage.getItem("atlas-log-skin") === "classic"`);
  for (const path of ["/?smoke=classic-reload", `/sets/${S.week_node2}`, "/?smoke=classic-return"]) {
    await open(path);
    await expect(`saved Classic survives reload and navigation at ${path}`, `${skinValue} === "classic" && document.querySelector('[data-skin-choice="classic"]').getAttribute("aria-pressed") === "true"`);
  }
  const classicPaint = await evaluate(skinPaint);
  check("Classic preserves its original palette", classicPaint !== auroraPaint);
  await chooseSkin("aurora");
  await expect("Aurora selection is applied and persisted", `${skinValue} === "aurora" && localStorage.getItem("atlas-log-skin") === "aurora"`);
  check("skin changes the actual palette", (await evaluate(skinPaint)) !== classicPaint);
  check("skin selection leaves color mode alone", await evaluate(`!document.documentElement.hasAttribute("data-theme")`));
  check("selected skin has an accessible pressed state", await evaluate(`document.querySelector('[data-skin-choice="aurora"]').getAttribute("aria-pressed") === "true"`));
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await expect("Escape closes the picker and restores focus", `!document.querySelector(".skin-picker").open && document.activeElement === document.querySelector(".skin-picker > summary")`);

  await open(`/sets/${S.week_node2}`);
  await expect("skin survives navigation to report", `${skinValue} === "aurora" && ${count(".skin-picker")} === 1 && ${count("#t-body tr")} > 0`);
  await evaluate(`(() => { const input = document.getElementById("f-search"); input.value = "Sync of directory"; input.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  await expect("report is filtered", `${count("#t-body tr")} === 3`);
  await evaluate(`document.querySelector("#t-body tr").click()`);
  await expect("report drawer is open", visible("#drawer"));
  const hashBefore = await evaluate("location.hash");
  await chooseSkin("mineral");
  check("switching skin preserves filters, selection and drawer", await evaluate(`location.hash === ${JSON.stringify(hashBefore)} && ${count("#t-body tr")} === 3 && ${visible("#drawer")}`));
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await expect("Escape dismisses skin choices before the report drawer", `!document.querySelector(".skin-picker").open && ${visible("#drawer")}`);
  await chooseSkin("mineral");
  await evaluate(`document.getElementById("btn-theme").click()`);
  await expect("dark mode is independent of Mineral", `document.documentElement.dataset.theme === "dark" && ${skinValue} === "mineral"`);
  await evaluate(`document.body.click()`);
  await expect("clicking outside dismisses skin choices", `!document.querySelector(".skin-picker").open`);
  for (const path of [`/sets/${S.week_node2}/entries`, `/sets/${S.week_node1}/triage`, `/captures/capture:${cfg.capture}`, "/?smoke=reload"]) {
    await open(path);
    await expect(`skin and mode persist at ${path}`, `${skinValue} === "mineral" && document.documentElement.dataset.theme === "dark" && ${count(".skin-picker")} === 1`);
  }
  await chooseSkin("classic");
  check("Classic restores the original palette", (await evaluate(skinValue)) === "classic");
  await evaluate(`localStorage.setItem("atlas-log-skin", "invalid-skin")`);
  await open("/?smoke=invalid-skin");
  await expect("invalid stored skin falls back to Aurora without overwriting the preference", `${skinValue} === "aurora" && localStorage.getItem("atlas-log-skin") === "invalid-skin" && document.querySelector('[data-skin-choice="aurora"]').getAttribute("aria-pressed") === "true"`);

  const blocked = await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `Object.defineProperty(window, "localStorage", { get() { throw new DOMException("Storage blocked", "SecurityError"); } });`,
  });
  try {
    await open("/?smoke=blocked-storage");
    await expect("blocked storage defaults to Aurora", `${skinValue} === "aurora" && ${count(".skin-picker")} === 1`);
    await chooseSkin("classic");
    await expect("skin remains usable without storage", `${skinValue} === "classic"`);
    await open(`/sets/${S.week_node2}`);
    await expect("report also defaults to Aurora without storage", `${skinValue} === "aurora" && ${count(".skin-picker")} === 1`);
  } finally {
    await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: blocked.identifier });
  }
});

await page("standalone-skins", async (check, expect) => {
  for (const [index, url] of cfg.standalone.entries()) {
    await open(url);
    await evaluate(`localStorage.removeItem("atlas-log-skin")`);
    await open(url);
    await expect(`export ${index + 1} defaults to Aurora with no preference`, `${skinValue} === "aurora"`);
    await expect(`export ${index + 1} loads a picker and rows without web assets`, `${count(".skin-picker")} === 1 && ${count("#t-body tr")} > 0 && ${count("script[src], link[rel=stylesheet]")} === 0`);
    for (const skin of ["aurora", "mineral", "classic"]) {
      await chooseSkin(skin);
      await expect(`export ${index + 1} switches to ${skin}`, `${skinValue} === "${skin}"`);
      const paintBefore = await evaluate(skinPaint);
      await evaluate(`document.getElementById("btn-theme").click()`);
      check(`export ${index + 1} toggles light/dark independently with ${skin}`, (await evaluate(skinPaint)) !== paintBefore && (await evaluate(skinValue)) === skin);
    }
    await chooseSkin("classic");
    await open(url);
    await expect(`export ${index + 1} preserves saved Classic on reload`, `${skinValue} === "classic" && localStorage.getItem("atlas-log-skin") === "classic"`);
    await evaluate(`localStorage.setItem("atlas-log-skin", "invalid-skin")`);
    await open(url);
    await expect(`export ${index + 1} falls back to Aurora for invalid storage`, `${skinValue} === "aurora"`);
    const blocked = await send("Page.addScriptToEvaluateOnNewDocument", {
      source: `Object.defineProperty(window, "localStorage", { get() { throw new DOMException("Storage blocked", "SecurityError"); } });`,
    });
    try {
      await open(url);
      await expect(`export ${index + 1} defaults to Aurora with blocked storage`, `${skinValue} === "aurora" && ${count(".skin-picker")} === 1`);
    } finally {
      await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: blocked.identifier });
    }
    await chooseSkin("mineral");
    await send("Emulation.setEmulatedMedia", { media: "print" });
    check(`export ${index + 1} prints without skin decoration`, await evaluate(`getComputedStyle(document.body).backgroundColor === "rgb(255, 255, 255)" && getComputedStyle(document.querySelector(".skin-picker")).display === "none"`));
    await send("Emulation.setEmulatedMedia", { media: "", features: [{ name: "prefers-color-scheme", value: "light" }] });
  }
});

console.log(JSON.stringify({ pages: results }));
ws.close();
process.exit(0);
