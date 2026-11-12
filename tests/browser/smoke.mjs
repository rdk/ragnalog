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
  await send("Page.navigate", { url: BASE + url });
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
});

await page("report", async (check, expect) => {
  await open(`/sets/${S.week_node2}`);
  await expect("group rows", `${count("#t-body tr")} > 0`);
  await expect("tiles drawn", `${count("#tiles > *")} > 0`);
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
});

await page("cluster", async (check, expect) => {
  await open(`/captures/capture:${cfg.capture}`);
  await expect("groups table rows", `${count("#t-body tr")} > 0`);
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

console.log(JSON.stringify({ pages: results }));
ws.close();
process.exit(0);
