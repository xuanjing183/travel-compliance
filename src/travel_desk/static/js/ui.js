// DOM helpers, formatting and labels shared by all views.
// All text is inserted with text nodes (never innerHTML), so API data cannot inject markup.

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.className = value;
      else if (name === "dataset") Object.assign(node.dataset, value);
      else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
      else if (value === true) node.setAttribute(name, "");
      else node.setAttribute(name, String(value));
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

// ---- money and time -------------------------------------------------------

export function yuan(fen) {
  if (fen === null || fen === undefined) return "—";
  const sign = fen < 0 ? "-" : "";
  const abs = Math.abs(fen);
  const whole = Math.floor(abs / 100).toLocaleString("en-US");
  return `${sign}${whole}.${String(abs % 100).padStart(2, "0")} 元`;
}

// Parse a yuan amount typed by the user into integer fen; null when not a positive amount.
export function parseYuan(text) {
  const t = String(text).trim().replace(/[,，]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const [int, dec = ""] = t.split(".");
  const fen = Number(int) * 100 + Number(dec.padEnd(2, "0"));
  return fen > 0 ? fen : null;
}

const TZ = "Asia/Shanghai";
const timeFmt = new Intl.DateTimeFormat("zh-CN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

export function localTime(iso) {
  return iso ? timeFmt.format(new Date(iso)) : "—";
}

export function localDateTime(iso) {
  return iso ? dateTimeFmt.format(new Date(iso)) : "—";
}

// ---- labels ---------------------------------------------------------------

export const STATUS = {
  COLLECTING: "收集信息",
  VALIDATING: "校验中",
  POLICY_BLOCKED: "制度阻断",
  PLANNING: "规划中",
  AUTO_APPROVED: "自动通过",
  PENDING_APPROVAL: "待审批",
  APPROVED: "已批准",
  DELIVERY_PENDING: "待交付",
  DELIVERED: "已交付",
  REJECTED: "已驳回",
};

export function statusBadge(status) {
  return el("span", { class: `status-badge st-${String(status).toLowerCase()}` }, STATUS[status] || status);
}

export const FIELDS = {
  origin: "出发城市",
  destination: "目的城市",
  start_date: "出发日期",
  end_date: "返回日期",
  purpose: "出差事由",
  declared_budget_fen: "申报预算",
  meeting_address: "会议地址",
  preferences: "本次偏好",
};

export const CODES = {
  HOTEL_OVER_CAP: "住宿超标",
  ADVANCE_BOOKING_VIOLATION: "提前预订不足",
  CABIN_CLASS_VIOLATION: "舱位超标",
  BUDGET_EXCEEDED: "超出申报预算",
  OVER_AUTO_APPROVAL_LIMIT: "超过自动通过阈值",
  PLAN_INCOMPLETE: "方案不完整",
  START_DATE_IN_PAST: "出发日期已过",
  END_BEFORE_START: "返回早于出发",
  DURATION_EXCEEDED: "行程天数超限",
};

const SOURCES = {
  demo_fixture: "Demo 数据",
  backup_fixture: "备用目录",
  live: "实时 Provider",
  policy: "制度原文",
  model: "模型生成",
};

// REQ-024: every displayed datum carries its source code.
export function sourceBadge(source) {
  if (!source) return el("span", { class: "src src-user", title: "申请人填写" }, "申请人填写");
  return el("span", { class: `src src-${source}`, title: SOURCES[source] || source }, source);
}

export function generatorLabel(generator) {
  if (!generator) return "—";
  if (generator === "fake") return "Fake（确定性测试替身，非模型）";
  if (generator === "fake:extractive") return "Fake 抽取式（测试替身，非模型）";
  if (generator === "template") return "代码模板";
  if (generator === "recorded:model") return "录制的模型输出";
  if (generator === "recorded:hand_written") return "手写录制（非模型）";
  return `模型 ${generator}`;
}

export const ROLE = { employee: "员工", manager: "经理", admin: "管理员" };

export const MODE = { air: "飞机", rail: "火车" };

export const CABIN = { economy: "经济舱", business: "商务舱", first: "头等舱", second: "二等座" };

// ---- small widgets --------------------------------------------------------

export function errorBox(error) {
  if (!error) return null;
  const details = error.details && Object.keys(error.details).length ? JSON.stringify(error.details) : null;
  return el("div", { class: "alert alert-error", role: "alert" },
    el("strong", null, error.code || "错误"), " ", error.message || String(error),
    details ? el("div", { class: "muted small" }, details) : null,
    error.traceId ? el("div", { class: "muted small" }, `trace_id ${error.traceId}`) : null);
}

export function notice(kind, ...children) {
  return el("div", { class: `alert alert-${kind}` }, ...children);
}

export function section(title, ...children) {
  return el("section", { class: "card" }, el("h3", { class: "card-title" }, title), ...children);
}

export function kv(rows) {
  return el("dl", { class: "kv" }, rows.filter(Boolean).map(([k, v]) => [el("dt", null, k), el("dd", null, v)]));
}

export function jsonBlock(value) {
  return el("pre", { class: "json" }, JSON.stringify(value, null, 2));
}

export function button(label, onclick, { kind = "secondary", disabled = false, title } = {}) {
  return el("button", { type: "button", class: `btn btn-${kind}`, onclick, disabled, title }, label);
}

// Disable a button while an async action runs; re-enable afterwards.
export async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.classList.add("is-busy");
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.textContent = label;
  }
}
