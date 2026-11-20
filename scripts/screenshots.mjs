// Aurora README screenshots (docs/img) from synthetic demo logs, never real data.
//
//   uv run python scripts/demo_logs.py DEMO
//   uv run ragnalog --store DEMO-DATA ingest DEMO/prev --cluster demo-jira --name prev
//   uv run ragnalog --store DEMO-DATA ingest DEMO/week --cluster demo-jira --name week
//   uv run ragnalog --store DEMO-DATA serve --port 8341        (in another terminal)
//   node scripts/screenshots.mjs http://127.0.0.1:8341 docs/img
//
// Needs Node 22+ (global WebSocket) and Edge or Chrome; RAGNALOG_BROWSER names the browser
// when it is not found. Captures 1440×900 CSS pixels at native DPR 2 (2880×1800 before the
// shadow). Writes hero-light, hero-dark, drawer, cluster, triage and entries .png, each with
// rounded corners and a soft shadow baked in on a transparent background, since GitHub drops
// CSS from READMEs. The hero shots are cut below the table of groups (bottom margin = side
// margin).
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base = "http://127.0.0.1:8341", out = "docs/img"] = process.argv.slice(2);
const W = 1440, H = 900, DPR = 2, PORT = 9455;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function browserPath() {
  const candidates = [
    process.env.RAGNALOG_BROWSER,
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
  ];
  const found = candidates.find((p) => p && existsSync(p));
  if (!found) throw new Error("no Edge or Chrome found: set RAGNALOG_BROWSER");
  return found;
}

const profile = mkdtempSync(join(tmpdir(), "ragnalog-shots-"));
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
// Room around a shadowed shot, in CSS pixels: the shadow falls mostly below.
const PAD = { x: 32, top: 24, bottom: 48 };
const withShadow = async (png, h, dark) => {
  const w = W + 2 * PAD.x, ht = h + PAD.top + PAD.bottom;
  const ink = dark ? "0,0,0" : "15,23,42", [a1, a2] = dark ? [0.7, 0.45] : [0.45, 0.18];
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: ht, deviceScaleFactor: DPR, mobile: false });
  await send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
  await go("about:blank", 300);
  await evaluate(`(async () => {
    document.documentElement.style.background = "transparent";
    document.body.style.cssText = "margin:0;padding:${PAD.top}px ${PAD.x}px ${PAD.bottom}px;background:transparent";
    const img = new Image();
    img.style.cssText = "display:block;width:${W}px;height:${h}px;border-radius:8px;"
      + "box-shadow:0 14px 36px -6px rgba(${ink},${a1}),0 2px 8px rgba(${ink},${a2})";
    img.src = "data:image/png;base64,${png.toString("base64")}";
    document.body.append(img);
    await img.decode();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  const res = await send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: w, height: ht, scale: 1 } });
  await send("Emulation.setDefaultBackgroundColorOverride");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: false });
  const out = Buffer.from(res.result.data, "base64");
  if (out.readUInt32BE(16) !== w * DPR || out.readUInt32BE(20) !== ht * DPR) throw new Error("Unexpected shadow PNG size");
  return out;
};
const shots = [];
// cropBelow: cut the shot below that element, leaving the margin it has on its left side.
const shot = async (name, { cropBelow = null } = {}) => {
  const loaded = {
    "hero-light": '#t-body tr', "hero-dark": '#t-body tr', drawer: '#drawer .entry',
    cluster: '#c-timeline svg', triage: '.triage-sec', entries: '#e-body .entry',
  }[name];
  const deadline = Date.now() + 15000;
  while (!(await evaluate(`!!document.querySelector(${JSON.stringify(loaded)})`))) {
    if (Date.now() >= deadline) throw new Error(`Content did not load for ${name}`);
    await sleep(100);
  }
  await evaluate(`(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  })()`);
  if (await evaluate('document.documentElement.getAttribute("data-skin")') !== "aurora") {
    throw new Error(`Aurora skin is not active for ${name}`);
  }
  if (await evaluate("devicePixelRatio") !== DPR) throw new Error(`Unexpected DPR for ${name}`);
  const h = cropBelow ? await evaluate(`(() => {
    const r = document.querySelector(${JSON.stringify(cropBelow)}).getBoundingClientRect();
    return Math.min(${H}, Math.ceil(r.bottom + r.left)); })()`) : H;
  const res = await send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: W, height: h, scale: 1 } });
  const png = Buffer.from(res.result.data, "base64");
  if (png.readUInt32BE(16) !== W * DPR || png.readUInt32BE(20) !== h * DPR) {
    throw new Error(`Unexpected PNG size: ${png.readUInt32BE(16)}×${png.readUInt32BE(20)}`);
  }
  shots.push({ name, png, h });
};
// The shadows are added after all shots: withShadow navigates away from the page.
const writeShots = async () => {
  for (const { name, png, h } of shots) {
    const framed = await withShadow(png, h, name.endsWith("dark"));
    writeFileSync(join(out, `${name}.png`), framed);
    console.log(`wrote ${join(out, name)}.png (${framed.readUInt32BE(16)}×${framed.readUInt32BE(20)}, DPR ${DPR})`);
  }
};
const clickRow = (text) => evaluate(`(() => { const r = [...document.querySelectorAll("table.groups tbody tr[data-id]")]
  .find((x) => x.textContent.includes(${JSON.stringify(text)})); if (r) r.click(); return !!r; })()`);

try {
  mkdirSync(out, { recursive: true });
  await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable");
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: false });
  const sets = (await (await fetch(`${base}/api/sets`)).json()).items;
  const node1 = sets.find((s) => s.name === "week_node1"), node2 = sets.find((s) => s.name === "week_node2");
  if (!node1 || !node2) throw new Error("not the demo data: no week_node1 / week_node2 sets (see the header)");
  await go(`${base}/`);
  await evaluate('localStorage.clear(); localStorage.setItem("ragnalog-skin", "aurora")');
  for (const t of ["light", "dark"]) {
    await theme(t);
    await go(`${base}/sets/${node2.id}`);
    await shot(`hero-${t}`, { cropBelow: "#groups" });
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
  await shot("triage");
  if (!href) throw new Error("the drawer has no link to the group's entries");
  await go(href, 5000);
  await evaluate(`(() => { const r = document.querySelector("tbody tr"); if (r) r.click(); })()`);
  await sleep(2000);
  await shot("entries");
  await writeShots();
  if (errors.length) console.error("page errors:", errors);
} finally {
  ws.close();
  browser.kill();
  await sleep(500);
  rmSync(profile, { recursive: true, force: true });
}
