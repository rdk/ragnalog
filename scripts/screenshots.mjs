// README screenshots (docs/img) from a web app serving the synthetic demo logs, never real data.
//
//   uv run python scripts/demo_logs.py DEMO
//   uv run atlas-log --store DEMO-DATA ingest DEMO/prev --cluster demo-jira --name prev
//   uv run atlas-log --store DEMO-DATA ingest DEMO/week --cluster demo-jira --name week
//   uv run atlas-log --store DEMO-DATA serve --port 8341        (in another terminal)
//   node scripts/screenshots.mjs http://127.0.0.1:8341 docs/img
//
// Needs Node 22+ (global WebSocket) and Edge or Chrome; ATLAS_LOG_BROWSER names the browser
// when it is not found. Writes hero-light, hero-dark, drawer, cluster, triage and entries .png.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base = "http://127.0.0.1:8341", out = "docs/img"] = process.argv.slice(2);
const W = 1440, H = 900, PORT = 9455;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function browserPath() {
  const candidates = [
    process.env.ATLAS_LOG_BROWSER,
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
  ];
  const found = candidates.find((p) => p && existsSync(p));
  if (!found) throw new Error("no Edge or Chrome found: set ATLAS_LOG_BROWSER");
  return found;
}

const profile = mkdtempSync(join(tmpdir(), "atlas-shots-"));
const browser = spawn(browserPath(), ["--headless=new", "--disable-gpu", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
let pages = null;
for (let i = 0; i < 50 && !pages; i++) {
  await sleep(200);
  pages = await fetch(`http://127.0.0.1:${PORT}/json/list`).then((r) => r.json()).catch(() => null);
}
const target = pages && pages.find((t) => t.type === "page");
if (!target) throw new Error("the browser did not start");
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let id = 0;
const pending = new Map();
const errors = [];
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description?.split("\n")[0] || "exception");
});
const send = (method, params = {}) => new Promise((r) => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (e) => (await send("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.result?.value;
const theme = (t) => send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: t }] });
const go = async (u, wait = 4500) => { await send("Page.navigate", { url: u }); await sleep(wait); };
const shot = async (name, h = H) => {
  const res = await send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: W, height: h, scale: 1 } });
  writeFileSync(join(out, `${name}.png`), Buffer.from(res.result.data, "base64"));
  console.log(`wrote ${join(out, name)}.png`);
};
const clickRow = (text) => evaluate(`(() => { const r = [...document.querySelectorAll("table.groups tbody tr[data-id]")]
  .find((x) => x.textContent.includes(${JSON.stringify(text)})); if (r) r.click(); return !!r; })()`);

try {
  mkdirSync(out, { recursive: true });
  await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable");
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const sets = (await (await fetch(`${base}/api/sets`)).json()).items;
  const node1 = sets.find((s) => s.name === "week_node1"), node2 = sets.find((s) => s.name === "week_node2");
  if (!node1 || !node2) throw new Error("not the demo data: no week_node1 / week_node2 sets (see the header)");
  await go(`${base}/`);
  await evaluate("localStorage.clear()");
  for (const t of ["light", "dark"]) {
    await theme(t);
    await go(`${base}/sets/${node2.id}`);
    await shot(`hero-${t}`);
  }
  await theme("light");
  await go(`${base}/sets/${node2.id}`);
  if (!(await clickRow("Sync of directory"))) throw new Error("the outage group was not found");
  await sleep(2500);
  await shot("drawer");
  const href = await evaluate(`(() => { const a = [...document.querySelectorAll("#drawer a")]
    .find((x) => /\\/entries\\?/.test(x.getAttribute("href") || "")); return a ? a.href : null; })()`);
  await go(`${base}/captures/capture:${node2.capture}`, 6000);
  await shot("cluster");
  await go(`${base}/sets/${node1.id}/triage`);
  await shot("triage", 760);
  if (href) {
    await go(href, 5000);
    await evaluate(`(() => { const r = document.querySelector("tbody tr"); if (r) r.click(); })()`);
    await sleep(2000);
    await shot("entries");
  }
  if (errors.length) console.error("page errors:", errors);
} finally {
  ws.close();
  browser.kill();
  await sleep(500);
  rmSync(profile, { recursive: true, force: true });
}
