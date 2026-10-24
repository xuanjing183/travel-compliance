// Renderers shared by the employee, manager and admin views: plan, cost, rule results,
// approval card and audit timeline. Amounts and rule results are shown exactly as the
// API computed them; nothing here recomputes money.

import { api } from "./api.js";
import {
  CABIN, CODES, FIELDS, MODE, el, generatorLabel, jsonBlock, kv, localDateTime, localTime,
  sourceBadge, statusBadge, yuan,
} from "./ui.js";

// ---- rule quotes for approval reasons (basis carries only rule_id + clause) ----

const ruleCache = new Map();

export async function rulesOf(version) {
  if (!version) return new Map();
  if (!ruleCache.has(version)) {
    ruleCache.set(version, api.get(`/api/policies/${encodeURIComponent(version)}`)
      .then((p) => new Map((p.rules || []).map((r) => [r.id, r])))
      .catch(() => new Map()));
  }
  return ruleCache.get(version);
}

export function forgetRules() {
  ruleCache.clear();
}

// ---- users ---------------------------------------------------------------

let usersById = new Map();

export function setUsers(users) {
  usersById = new Map(users.map((u) => [u.id, u]));
}

export function userName(id) {
  if (!id) return "—";
  if (id === "system") return "系统";
  const u = usersById.get(id);
  return u ? u.name : id;
}

// ---- slots ---------------------------------------------------------------

export function slotValue(field, value) {
  if (value === null || value === undefined || value === "") return el("span", { class: "muted" }, "未填写");
  if (field === "declared_budget_fen") return yuan(value);
  if (field === "preferences") return JSON.stringify(value);
  return String(value);
}

export function slotsTable(slots) {
  return kv(Object.keys(FIELDS).map((f) => [FIELDS[f], slotValue(f, slots[f])]));
}

// ---- citations and rule results -----------------------------------------

export function quoteBlock({ version, clause, quote, locator, source = "policy" }) {
  return el("blockquote", { class: "quote" },
    el("div", { class: "quote-head" }, sourceBadge(source), ` ${version || ""} ${clause || ""} · ${locator || "未定位"}`),
    el("div", { class: "quote-text" }, quote || "（无原文）"));
}

function valueText(value, unit) {
  if (value === null || value === undefined) return "—";
  if (unit === "fen" || unit === "fen_per_night" || unit === "fen_per_day") {
    return yuan(value) + (unit === "fen_per_night" ? "/晚" : unit === "fen_per_day" ? "/天" : "");
  }
  return `${value}${unit && !unit.startsWith("fen") ? " " + unit : ""}`;
}

export function violationItem(v) {
  return el("li", { class: "rule-item rule-violation" },
    el("div", { class: "rule-head" },
      el("span", { class: "rule-code" }, CODES[v.code] || v.code), el("code", null, v.code),
      el("span", { class: "muted" }, ` ${v.rule_id} · ${v.policy_version}`), sourceBadge(v.source || "policy")),
    el("div", null, v.message),
    el("div", { class: "muted small" }, `实际 ${valueText(v.actual_value, v.unit)}，上限 ${valueText(v.limit_value, v.unit)}`,
      v.subject ? `，对象 ${v.subject}` : ""),
    quoteBlock({ version: v.policy_version, clause: v.clause, quote: v.quote, locator: v.locator, source: v.source }));
}

export function reasonItem(r, rules) {
  const rule = r.basis && rules ? rules.get(r.basis.rule_id) : null;
  return el("li", { class: "rule-item rule-reason" },
    el("div", { class: "rule-head" },
      el("span", { class: "rule-code" }, CODES[r.code] || r.code), el("code", null, r.code),
      r.lower_bound ? el("span", { class: "tag tag-warn" }, "已知下界") : null),
    el("div", null, r.message),
    r.missing && r.missing.length ? el("div", { class: "muted small" }, `缺失：${r.missing.join("、")}`) : null,
    r.basis ? el("div", { class: "muted small" }, `依据 ${r.basis.rule_id} · ${r.basis.policy_version} ${r.basis.clause_ref}`) : null,
    rule ? quoteBlock({ version: rule.version, clause: rule.clause, quote: rule.quote, locator: rule.locator }) : null);
}

export function blockItem(b) {
  return el("li", { class: "rule-item rule-block" },
    el("div", { class: "rule-head" }, el("span", { class: "rule-code" }, CODES[b.code] || b.code), el("code", null, b.code),
      b.rule_id ? el("span", { class: "muted" }, ` ${b.rule_id} · ${b.policy_version} ${b.clause}`) : el("span", { class: "muted" }, " 输入校验")),
    el("div", null, b.message));
}

export function ruleResults({ violations = [], approval_reasons = [], blocks = [] }, rules) {
  const parts = [];
  if (blocks.length) parts.push(el("h4", null, "硬阻断"), el("ul", { class: "rules" }, blocks.map(blockItem)));
  if (violations.length) parts.push(el("h4", null, "制度违规"), el("ul", { class: "rules" }, violations.map(violationItem)));
  if (approval_reasons.length) {
    parts.push(el("h4", null, "需要审批的理由"), el("ul", { class: "rules" }, approval_reasons.map((r) => reasonItem(r, rules))));
  }
  if (!parts.length) parts.push(el("p", { class: "muted" }, "没有违规，也没有需要审批的理由。"));
  return parts;
}

// ---- cost ------------------------------------------------------------------

export function costTable(c) {
  if (!c) return el("p", { class: "muted" }, "尚未生成成本。");
  const pending = el("span", { class: "tag tag-warn" }, "待人工补录");
  const rows = [
    ["去程交通", c.outbound_fen === null ? pending : yuan(c.outbound_fen)],
    ["返程交通", c.return_fen === null ? pending : yuan(c.return_fen)],
    ["住宿", c.hotel_fen === null ? pending
      : c.hotel_per_night_fen === null ? `${yuan(c.hotel_fen)}（当天往返）`
        : `${c.nights} 晚 × ${yuan(c.hotel_per_night_fen)} = ${yuan(c.hotel_fen)}`],
    ["伙食补贴", `${c.days} 天 × ${yuan(c.meal_per_day_fen)} = ${yuan(c.meal_fen)}`],
  ];
  const delta = c.budget_delta_fen;
  return el("div", null,
    el("table", { class: "cost" },
      el("tbody", null, rows.map(([k, v]) => el("tr", null, el("th", null, k), el("td", null, v))),
        el("tr", { class: "total" }, el("th", null, "合计"),
          el("td", null, c.total_fen === null ? ["待补录（已知小计 ", yuan(c.known_subtotal_fen), "）"] : yuan(c.total_fen))),
        el("tr", null, el("th", null, "申报预算"), el("td", null, yuan(c.declared_budget_fen))),
        el("tr", { class: delta > 0 ? "over" : "" }, el("th", null, "预算差"),
          el("td", null, delta === null ? "—（方案不完整）" : `${delta > 0 ? "超出 " : "结余 "}${yuan(Math.abs(delta))}`)))),
    el("p", { class: "muted small" }, `目的地 ${c.destination_tier} 类城市；金额由代码按已发布规则计算。`,
      c.missing_components && c.missing_components.length ? ` 缺失部分：${c.missing_components.join("、")}，不按 0 计入。` : ""));
}

// ---- plan ------------------------------------------------------------------

// Transport leg as a card (replaces the old table row).
function legCard(label, leg, exception) {
  if (!leg) {
    return el("div", { class: "leg-card empty" },
      el("div", { class: "leg-label" }, label),
      el("div", { class: "leg-empty" }, "待人工补录"));
  }
  return el("div", { class: `leg-card${exception ? " exception" : ""}` },
    el("div", { class: "leg-label" }, label,
      exception ? el("span", { class: "leg-exception-tag" }, "例外") : null),
    el("div", { class: "leg-flight" }, `${leg.service_no} ${leg.carrier || ""}`.trim()),
    el("div", { class: "leg-cabin" }, `${MODE[leg.mode] || leg.mode} · ${CABIN[leg.cabin] || leg.cabin}`),
    el("div", { class: "leg-route" },
      el("span", { class: "leg-city" }, leg.origin),
      el("span", { class: "leg-arrow" }, "→"),
      el("span", { class: "leg-city" }, leg.destination)),
    el("div", { class: "leg-meta" },
      el("div", { class: "leg-time" },
        `${localTime(leg.depart_at)} – ${localTime(leg.arrive_at)}`,
        el("div", { class: "leg-date" }, leg.depart_at.slice(0, 10))),
      el("div", { class: "leg-price" }, yuan(leg.price_fen))));
}

// Hotel as a card.
function hotelCard(hotel) {
  if (hotel.status === "not_needed") {
    return el("div", { class: "hotel-card" },
      el("div", { class: "hotel-icon" }, "🏨"),
      el("div", { class: "hotel-info" },
        el("div", { class: "hotel-name" }, "当天往返"),
        el("div", { class: "hotel-detail" }, "无需住宿")));
  }
  if (!hotel.candidate) {
    return el("div", { class: "hotel-card empty" },
      el("div", { class: "hotel-icon" }, "🏨"),
      el("div", { class: "hotel-info" },
        el("div", { class: "hotel-name" }, "待人工补录"),
        el("div", { class: "hotel-detail" }, `${hotel.nights || "?"} 晚住宿尚未确认`)));
  }
  return el("div", { class: "hotel-card" },
    el("div", { class: "hotel-icon" }, "🏨"),
    el("div", { class: "hotel-info" },
      el("div", { class: "hotel-name" }, hotel.candidate.name),
      el("div", { class: "hotel-detail" },
        `${hotel.candidate.kind === "backup" ? "备用目录" : "协议酒店"} · ${hotel.nights} 晚`,
        hotel.exception ? [" · ", el("span", { class: "tag tag-warn" }, "超标最少的例外")] : null)),
    el("div", { class: "hotel-price" },
      yuan(hotel.candidate.price_fen),
      el("div", { class: "per" }, "每晚")));
}

// Schedule and weather share the same dates, rendered as a timeline.
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function schedDay(day, weather) {
  const weekday = WEEKDAYS[new Date(`${day.date}T00:00:00`).getDay()];
  const items = day.items && day.items.length
    ? day.items.map((it) => {
        const hasTime = !!it.time;
        return el("div", { class: `sched-item${hasTime ? " has-time" : ""}` },
          el("span", { class: "sched-item-time" }, it.time || "—"),
          el("span", { class: "sched-item-title" }, it.title));
      })
    : [el("div", { class: "sched-item" },
        el("span", { class: "sched-item-time" }, "—"),
        el("span", { class: "sched-item-title muted" }, "暂无安排"))];
  return el("div", { class: "sched-day" },
    el("div", { class: "sched-date-block" },
      el("div", { class: "sched-date-num" }, day.date.slice(5, 7) === "01" ? day.date.slice(6) : day.date.slice(5)),
      el("div", { class: "sched-date-weekday" }, weekday),
      weather
        ? el("div", { class: "sched-weather-pill" }, `${weather.summary} ${weather.temp_low_c}–${weather.temp_high_c}℃`)
        : el("div", { class: "sched-weather-pill" }, "天气不可用")),
    el("div", { class: "sched-content" }, items));
}

export function planView(plan) {
  if (!plan) return el("p", { class: "muted" }, "尚未生成方案。");
  const t = plan.transport;
  const h = plan.hotel;
  const w = plan.weather;
  const ex = new Set(t.exception_legs || []);
  const weatherByDate = new Map((w && w.status === "ok" ? w.days : []).map((d) => [d.date, d]));
  return el("div", { class: "plan" },
    el("h4", null, "交通"),
    el("div", { class: "transport-grid" },
      legCard("去程", t.outbound, ex.has("outbound")),
      legCard("返程", t.return, ex.has("return"))),
    el("h4", null, "住宿"),
    hotelCard(h),
    el("h4", null, "日程与天气"),
    plan.itinerary && plan.itinerary.length
      ? el("div", { class: "schedule-timeline" }, plan.itinerary.map((day) => schedDay(day, weatherByDate.get(day.date))))
      : el("p", { class: "muted" }, "暂无日程。"));
}

export function summaryView(summary) {
  if (!summary) return null;
  return el("div", { class: "summary" },
    el("h4", null, "行程摘要 ", sourceBadge(summary.source || "model")),
    summary.status === "ok"
      ? el("p", null, summary.text)
      : el("p", { class: "muted" }, summary.note || "行程摘要不可用"),
    el("p", { class: "muted small" }, `生成方式：${generatorLabel(summary.generator)}。摘要只概括文字，安排与金额以上文为准。`));
}

function rankingView(r) {
  if (!r) return null;
  return el("details", { class: "small" },
    el("summary", null, "候选与排序"),
    kv([
      ["回避早班偏好", r.preference_applied ? (r.preference_affected ? "已启用，改变了选择" : "已启用，未改变选择") : "未启用"],
      ["去程候选", (r.considered.outbound || []).join("、") || "—"],
      ["返程候选", (r.considered.return || []).join("、") || "—"],
      ["酒店候选", (r.considered.hotel || []).join("、") || "—"],
      ["排除", (r.filtered_out || []).map((f) => `${f.id}（${f.reason} ${(f.nights || []).join("/")}）`).join("；") || "—"],
    ]));
}

// ---- approval card ----------------------------------------------------------

export function approvalCard(approval, rules) {
  const c = approval.card;
  const p = c.plan_summary || {};
  const leg = (l) => (l ? `${l.service_no} ${l.origin}→${l.destination} ${l.depart_at.slice(5, 10)} ${localTime(l.depart_at)} · ${yuan(l.price_fen)}` : "待人工补录");
  return el("div", { class: "approval-card" },
    kv([
      ["申请人", `${c.employee.name}（${c.employee.department}）`],
      ["行程", `${c.slots.origin} → ${c.slots.destination}，${c.slots.start_date} ～ ${c.slots.end_date}`],
      ["事由", c.slots.purpose],
      ["revision", String(c.revision)],
      ["制度版本", c.policy_version],
      ["来源", el("span", null, (c.sources || []).map((s) => [sourceBadge(s), " "]))],
    ]),
    el("h4", null, "成本明细"),
    costTable(c.cost_breakdown),
    el("h4", null, "方案摘要"),
    kv([
      ["去程", leg(p.outbound)],
      ["返程", leg(p.return)],
      ["住宿", p.hotel ? `${p.hotel.name} ${p.nights} 晚 × ${yuan(p.hotel.price_fen)}（${p.hotel.source}）` : p.hotel_status === "not_needed" ? "无需住宿" : "待人工补录"],
      p.summary ? ["行程摘要", el("span", null, p.summary, el("span", { class: "muted small" }, `（${generatorLabel(p.summary_generator)}）`))] : null,
    ]),
    ruleResults({ violations: c.violations, approval_reasons: c.approval_reasons }, rules));
}

// ---- audit timeline -------------------------------------------------------

const AUDIT = {
  trip_created: "创建申请", slots_changed: "槽位变化", message_processed: "对话抽取", blocked_cleared: "解除阻断",
  submitted: "提交", preference_snapshot: "偏好快照", policy_bound: "绑定制度版本", validation_blocked: "校验阻断",
  provider_call: "Provider 调用", candidates_selected: "选定候选", plan_created: "生成方案", rules_evaluated: "规则判断",
  auto_approved: "自动通过", approval_requested: "发起审批", no_approver: "无直属上级", decision_recorded: "审批决定",
  notification_written: "写入通知", delivery_written: "写入交付", delivery_failed: "交付失败", status_changed: "状态变化",
  file_uploaded: "上传文件", image_extracted: "图片识别", image_confirmed: "确认图片字段", approval_superseded: "旧审批失效",
  revalidated: "按新版本重新校验", error: "错误", demo_reset: "Demo 重置",
};

function auditLine(e) {
  const p = e.payload || {};
  switch (e.type) {
    case "status_changed": return `${p.from} → ${p.to}（${p.event}）`;
    case "slots_changed": return (p.changes || []).map((c) => {
      const show = (v) => (v == null ? "空" : c.field === "declared_budget_fen" ? yuan(v) : v);
      return `${FIELDS[c.field] || c.field}：${show(c.before)} → ${show(c.after)}`;
    }).join("；") + `（来源 ${p.origin}）`;
    // Weather returns days, not candidates, so its candidate_ids is always empty (IMP-93).
    case "provider_call": return `${p.provider}/${p.implementation} ${p.status}${
      p.provider === "weather" ? `，请求 ${((p.request && p.request.dates) || []).length} 天`
        : p.candidate_ids ? `，候选 ${p.candidate_ids.length} 个` : ""}`;
    case "rules_evaluated": return `${p.policy_version} → ${p.outcome}`;
    case "decision_recorded": return `${p.decision}${p.comment ? `：${p.comment}` : ""}`;
    case "policy_bound": return p.version;
    case "error": return `${p.stage || ""} ${p.code || p.error_type || ""}`.trim();
    default: return "";
  }
}

export function auditTimeline(events) {
  if (!events.length) return el("p", { class: "muted" }, "没有审计事件。");
  // Sources the event itself declares (REQ-024); plan snapshots keep theirs inside the payload.
  const declared = (p) => [...new Set([p && p.source, ...((p && p.sources) || [])].filter(Boolean))];
  return el("ol", { class: "timeline" }, events.map((e) => el("li", { class: `ev ev-${e.type}` },
    el("div", { class: "ev-head" },
      el("span", { class: "ev-seq" }, `#${e.seq}`),
      el("span", { class: "ev-type" }, AUDIT[e.type] || e.type), el("code", { class: "small" }, e.type),
      el("span", { class: "muted small" }, ` ${localDateTime(e.ts)} · ${userName(e.actor)} · rev ${e.revision ?? "—"}`),
      declared(e.payload).map((s) => [" ", sourceBadge(s)])),
    auditLine(e) ? el("div", { class: "ev-line" }, auditLine(e)) : null,
    e.refs && e.refs.length ? el("div", { class: "muted small" }, "引用：", e.refs.map((r) => `${r.kind}:${r.id}${r.version ? "@" + r.version : ""}${r.clause ? " " + r.clause : ""}`).join("，")) : null,
    el("details", null, el("summary", { class: "small" }, "payload"), jsonBlock(e.payload)))));
}

export function tripHeader(trip, extra) {
  const s = trip.slots;
  return el("div", { class: "trip-head" },
    el("div", null,
      el("h2", null, `${s.origin || "?"} → ${s.destination || "?"}`, " ", statusBadge(trip.status)),
      el("p", { class: "muted" }, `${userName(trip.employee_id)} · revision ${trip.revision} · 制度 ${trip.policy_version || "未绑定"} · 更新 ${localDateTime(trip.updated_at)}`)),
    extra || null);
}