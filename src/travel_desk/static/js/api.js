// HTTP and SSE client for the local API (docs/api.md).
// The demo identity is sent as X-Demo-User (§6.4); it is not authentication.

const USER_KEY = "tcd.user";

export function currentUserId() {
  return localStorage.getItem(USER_KEY);
}

export function setCurrentUserId(id) {
  if (id) localStorage.setItem(USER_KEY, id);
  else localStorage.removeItem(USER_KEY);
}

export class ApiError extends Error {
  constructor(status, body) {
    const err = (body && body.error) || {};
    super(err.message || `请求失败（HTTP ${status}）`);
    this.status = status;
    this.code = err.code || (status === 0 ? "NETWORK_ERROR" : "HTTP_" + status);
    this.details = err.details || {};
    this.traceId = err.trace_id || null;
    this.retryable = Boolean(err.retryable || (err.details && err.details.retryable));
  }
}

// One key per user action. A retry after a dropped connection must reuse it (§1.5).
export function newIdempotencyKey(prefix) {
  const rand = (crypto.randomUUID && crypto.randomUUID()) ||
    Math.random().toString(16).slice(2) + Date.now().toString(16);
  return `${prefix}-${rand}`;
}

function headers(key, json) {
  const h = {};
  const user = currentUserId();
  if (user) h["X-Demo-User"] = user;
  if (key) h["Idempotency-Key"] = key;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function parse(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { error: { code: "INVALID_RESPONSE", message: text.slice(0, 200) } };
  }
}

// request("GET", "/api/trips") / request("POST", path, {body, key}) / request("POST", path, {form})
export async function request(method, path, { body, key, form } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: headers(key, body !== undefined && !form),
      body: form ? form : body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, { error: { code: "NETWORK_ERROR", message: "无法连接本地服务，请确认服务仍在运行" } });
  }
  const data = await parse(response);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body, key) => request("POST", path, { body, key }),
  patch: (path, body) => request("PATCH", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, { form }),
};

function parseFrame(block) {
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null; // heartbeat comment or empty frame
  return JSON.parse(data.join("\n"));
}

// POST an SSE endpoint (#6, #8). Pre-stream errors come back as JSON and throw ApiError.
// Resolves with the terminal event (`done` or `error`); throws ApiError(NETWORK_ERROR)
// when the connection drops before a terminal event.
export async function stream(path, body, { key, onEvent } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { ...headers(key, true), Accept: "text/event-stream" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, { error: { code: "NETWORK_ERROR", message: "无法连接本地服务" } });
  }
  const type = response.headers.get("content-type") || "";
  if (!response.ok || !type.includes("text/event-stream")) {
    throw new ApiError(response.ok ? 500 : response.status, await parse(response));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let terminal = null;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const event = parseFrame(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (!event) continue;
        if (onEvent) onEvent(event);
        if (event.type === "done" || event.type === "error") terminal = event;
      }
    }
  } catch {
    // fall through: connection lost
  }
  if (!terminal) {
    throw new ApiError(0, {
      error: { code: "NETWORK_ERROR", message: "连接在完成前中断；服务端仍会继续处理，可刷新查看最新状态" },
    });
  }
  return terminal;
}
