// Screenshot the live approvals page
const out = process.argv[2] || "live-approvals.png";
const url = "http://127.0.0.1:8000/";
import { writeFileSync } from "node:fs";

const mk = await (await fetch("http://127.0.0.1:9224/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(mk.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); } });
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
const send = (method, params = {}) => new Promise((res) => { const n = ++id; pending.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

// Navigate to the page first, then set localStorage, then reload with hash
await send("Page.navigate", { url });
await wait(1500);

// Set manager user in localStorage
await send("Runtime.evaluate", { expression: `localStorage.setItem("tcd.user", "u-lisi")` });

// Reload to pick up the user, then navigate to hash route
await send("Page.reload", { ignoreCache: true });
await wait(1500);

await send("Runtime.evaluate", { expression: `location.hash = "#/approvals"` });
await wait(2000);

const { result } = await send("Page.getLayoutMetrics");
const h = Math.min(Math.ceil(result.cssContentSize.height), 20000);
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: h, deviceScaleFactor: 1, mobile: false });
await wait(500);

const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(out, Buffer.from(shot.result.data, "base64"));
console.log(out, h + "px");
await send("Page.close");
ws.close();
