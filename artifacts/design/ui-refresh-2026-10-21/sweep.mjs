// Route sweep: visit each page as each role and report any runtime error text.
const ROUTES = [
  ["#/trips", "u-zhangsan"], ["#/trips/new", "u-zhangsan"], ["#/prefs", "u-zhangsan"],
  ["#/ask", "u-zhangsan"], ["#/approvals", "u-lisi"], ["#/trips", "u-lisi"],
  ["#/admin/policies", "u-wangwu"], ["#/admin/ops", "u-wangwu"], ["#/trips", "u-wangwu"],
  ["#/login", null],
];
const DETAILS = [
  ["#/trips/6896508c-519c-43e6-a97e-4aa9027aeef6", "u-zhangsan"],
  ["#/trips/9143fb30-1526-418d-b5b4-ccee43534308", "u-zhangsan"],
  ["#/trips/6896508c-519c-43e6-a97e-4aa9027aeef6/itinerary", "u-zhangsan"],
  ["#/trips/9143fb30-1526-418d-b5b4-ccee43534308/itinerary", "u-zhangsan"],
];

const mk = await (await fetch("http://127.0.0.1:9222/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(mk.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const consoleErrors = [];
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  else if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
const send = (method, params = {}) => new Promise((res) => { const n = ++id; pending.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

await send("Page.enable");
await send("Runtime.enable");

for (const [route, user] of [...ROUTES, ...DETAILS]) {
  consoleErrors.length = 0;
  await send("Page.navigate", { url: "http://127.0.0.1:8000/" });
  await wait(900);
  await evaluate(`localStorage.${user ? `setItem("tcd.user", ${JSON.stringify(user)})` : `clear()`}`);
  await send("Page.navigate", { url: `http://127.0.0.1:8000/${route}` });
  await wait(700);
  await send("Page.reload", { ignoreCache: true });
  await wait(2200);
  // Open every disclosure so lazily built renderers run too.
  await evaluate(`document.querySelectorAll("details").forEach((d) => (d.open = true)); true`);
  await wait(1800);
  const probe = await evaluate(`JSON.stringify({ secs: document.querySelectorAll(".sec").length, icons: document.querySelectorAll(".leg-ico svg").length, text: document.querySelector("main").innerText.replace(/\\s+/g, " ").slice(0, 90) })`);
  const p = JSON.parse(probe || "{}");
  const bad = consoleErrors.join(" | ").slice(0, 300);
  console.log(`${route} [${user || "-"}] secs=${p.secs} icons=${p.icons} ${bad ? "JSERR " + bad : "ok"} :: ${p.text}`);
}
await send("Page.close");
ws.close();
