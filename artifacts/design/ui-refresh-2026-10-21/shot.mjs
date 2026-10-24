// CDP screenshot driver for the local demo (verification only, not shipped).
// Usage: node shot.mjs <hash> <out.png> [user] [extraEval]
import { writeFileSync } from "node:fs";

const [, , hash = "#/trips", out = "shot.png", user = "u-zhangsan", extra = ""] = process.argv;

const mk = await (await fetch(`http://127.0.0.1:9222/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(mk.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const events = [];

ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  else if (msg.method) events.push(msg.method);
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));

const send = (method, params = {}) => new Promise((res) => {
  const n = ++id;
  pending.set(n, res);
  ws.send(JSON.stringify({ id: n, method, params }));
});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

await send("Page.navigate", { url: "http://127.0.0.1:8000/" });
await wait(2500);
await evaluate(`localStorage.setItem("tcd.user", ${JSON.stringify(user)})`);

await send("Page.navigate", { url: `http://127.0.0.1:8000/${hash}` });
await wait(1200);
// A hash-only change never reboots the SPA, so force a real reload now that the user is seeded.
await send("Page.reload", { ignoreCache: true });
await wait(3500);
if (extra) { console.log("extra:", await evaluate(extra)); await wait(1800); }

// Full-page capture: the sticky topbar must not be pinned or it tiles on the long page.
await evaluate(`(() => { const t = document.querySelector(".topbar"); if (t) t.style.position = "static"; return true; })()`);
const { result } = await send("Page.getLayoutMetrics");
const h = Math.min(Math.ceil(result.cssContentSize.height), 20000);
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: h, deviceScaleFactor: 1, mobile: false });
await wait(700);

const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(out, Buffer.from(shot.result.data, "base64"));
const probe = await evaluate(`JSON.stringify({ title: document.title, h: document.documentElement.scrollHeight, secs: document.querySelectorAll(".sec").length, icons: document.querySelectorAll(".leg-ico svg").length, rows: document.querySelectorAll(".trip-row").length, tags: document.querySelectorAll(".tag").length, err: document.body.innerText.slice(0, 160) })`);
console.log(out, probe);
await send("Page.close");
ws.close();
