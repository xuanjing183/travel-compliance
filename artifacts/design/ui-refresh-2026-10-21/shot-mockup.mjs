// Screenshot the local mockup file
const out = process.argv[2] || "mockup-v7.png";
const url = `file:///C:/Users/22316/Desktop/1/lc-course/travel-compliance-desk/artifacts/design/ui-refresh-2026-10-21/mockup-v7-approval.html`;
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
await send("Page.navigate", { url });
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
