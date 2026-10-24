// Trips: list, create, detail (chat, slot form, invitation image, submit, results, audit), itinerary.

import { api, currentUserId, newIdempotencyKey, stream } from "./api.js";
import { decisionForm } from "./approvals.js";
import {
  costTable, planView, ruleResults, rulesOf, slotsTable, userName,
} from "./parts.js";
import {
  FIELDS, STATUS, busy, button, clear, el, errorBox, generatorLabel, localDateTime, notice, parseYuan,
  section, statusBadge, yuan,
} from "./ui.js";

const EDITABLE = new Set(["COLLECTING", "POLICY_BLOCKED"]);
const REQUIRED = ["origin", "destination", "start_date", "end_date", "purpose", "declared_budget_fen"];

// ---- chat log kept per trip in sessionStorage (the server keeps the audit, not the transcript) ----

function chatLog(tripId) {
  try {
    return JSON.parse(sessionStorage.getItem(`tcd.chat.${tripId}`) || "[]");
  } catch {
    return [];
  }
}

function pushChat(tripId, entry) {
  const log = chatLog(tripId);
  log.push(entry);
  sessionStorage.setItem(`tcd.chat.${tripId}`, JSON.stringify(log.slice(-50)));
}

const lastProgress = new Map(); // tripId → [{text, kind}] of the latest submit run

// ---- list ---------------------------------------------------------------

export async function renderTripList(root, ctx, params) {
  const role = ctx.user.role;
  const scopes = role === "admin" ? ["all"] : role === "manager" ? ["mine", "reports"] : ["mine"];
  const scope = scopes.includes(params.get("scope")) ? params.get("scope") : scopes[0];
  const data = await api.get(`/api/trips?scope=${scope}`);
  const title = { mine: "差旅列表", reports: "直属下属的申请", all: "差旅列表" }[scope];

  // Calculate stats from actual data
  const totalTrips = data.trips.length;
  const pendingApprovals = data.trips.filter(t => t.status === "PENDING_APPROVAL").length;
  const totalCostFen = data.trips.reduce((sum, t) => {
    const cost = t.total_fen !== null ? t.total_fen : (t.known_subtotal_fen !== null ? t.known_subtotal_fen : 0);
    return sum + cost;
  }, 0);
  const approvedCount = data.trips.filter(t => t.status === "APPROVED" || t.status === "AUTO_APPROVED").length;
  const approvalRate = totalTrips > 0 ? Math.round((approvedCount / totalTrips) * 100) : 0;

  // Stats cards
  const statsGrid = el("div", { class: "stats-grid" },
    el("div", { class: "stat-card" },
      el("div", { class: "stat-label" }, "本月差旅总数"),
      el("div", { class: "stat-value" }, String(totalTrips))),
    el("div", { class: "stat-card" },
      el("div", { class: "stat-label" }, "待审批"),
      el("div", { class: "stat-value" }, String(pendingApprovals))),
    el("div", { class: "stat-card" },
      el("div", { class: "stat-label" }, "本月费用"),
      el("div", { class: "stat-value" }, totalCostFen > 0 ? `¥${yuan(totalCostFen)}` : "¥0")),
    el("div", { class: "stat-card" },
      el("div", { class: "stat-label" }, "审批通过率"),
      el("div", { class: "stat-value" }, `${approvalRate}%`)));

  // Filter tabs
  const statusFilters = [
    { label: "全部", status: null },
    { label: "待审批", status: "PENDING_APPROVAL" },
    { label: "已批准", status: "APPROVED" },
    { label: "自动通过", status: "AUTO_APPROVED" },
    { label: "已驳回", status: "REJECTED" },
    { label: "收集信息", status: "COLLECTING" },
  ];
  let activeFilter = null;

  function renderFilteredList() {
    const filtered = activeFilter
      ? data.trips.filter(t => t.status === activeFilter)
      : data.trips;
    const tbody = tableBody;
    clear(tbody);
    if (filtered.length) {
      filtered.forEach((t) => {
        const row = el("tr", { class: "row-link", onclick: () => ctx.navigate(`#/trips/${t.id}`) },
          el("td", { class: "trip-id" }, t.id.slice(0, 8)),
          el("td", { class: "destination" }, `${t.origin || "?"} → ${t.destination || "?"}`),
          el("td", { class: "date-range" }, t.start_date ? `${t.start_date} 至 ${t.end_date || "?"}` : "—"),
          el("td", null, t.employee_name),
          el("td", null, statusBadge(t.status)),
          el("td", { class: "cost" }, t.total_fen !== null ? yuan(t.total_fen) : t.known_subtotal_fen !== null ? `≥ ${yuan(t.known_subtotal_fen)}` : "—"),
          el("td", { style: "text-align:right" },
            el("a", { href: `#/trips/${t.id}`, class: "action-btn" }, "查看")));
        tbody.appendChild(row);
      });
    } else {
      tbody.appendChild(el("tr", null, el("td", { colspan: 7, class: "empty" }, "没有匹配的申请。")));
    }
  }

  const filterBtns = statusFilters.map(f =>
    el("button", {
      class: `filter-btn${f.status === activeFilter ? " active" : ""}`,
      onclick: () => {
        activeFilter = f.status;
        filterBtns.forEach(b => b.classList.remove("active"));
        filterBtns[statusFilters.indexOf(f)].classList.add("active");
        renderFilteredList();
      },
    }, f.label));
  const filters = el("div", { class: "filters" }, ...filterBtns);

  // Table
  const tableBody = el("tbody");
  const table = el("div", { class: "table-container" },
    el("table", { class: "modern-table" },
      el("thead", null,
        el("tr", null,
          el("th", null, "差旅编号"),
          el("th", null, "目的地"),
          el("th", null, "日期"),
          el("th", null, "申请人"),
          el("th", null, "状态"),
          el("th", null, "预算"),
          el("th", null, ""))),
      tableBody));

  // Initial render
  data.trips.forEach((t) => {
    const row = el("tr", { class: "row-link", onclick: () => ctx.navigate(`#/trips/${t.id}`) },
      el("td", { class: "trip-id" }, t.id.slice(0, 8)),
      el("td", { class: "destination" }, `${t.origin || "?"} → ${t.destination || "?"}`),
      el("td", { class: "date-range" }, t.start_date ? `${t.start_date} 至 ${t.end_date || "?"}` : "—"),
      el("td", null, t.employee_name),
      el("td", null, statusBadge(t.status)),
      el("td", { class: "cost" }, t.total_fen !== null ? yuan(t.total_fen) : t.known_subtotal_fen !== null ? `≥ ${yuan(t.known_subtotal_fen)}` : "—"),
      el("td", { style: "text-align:right" },
        el("a", { href: `#/trips/${t.id}`, class: "action-btn" }, "查看")));
    tableBody.appendChild(row);
  });
  if (!data.trips.length) {
    tableBody.appendChild(el("tr", null, el("td", { colspan: 7, class: "empty" }, role === "admin" ? "还没有任何申请。" : "还没有申请。点击「新建差旅」按钮开始。")));
  }

  // Header
  const header = el("div", { class: "header" },
    el("h2", null, title),
    el("div", { class: "header-actions" },
      role !== "admin" ? el("button", { class: "btn btn-primary", onclick: () => ctx.navigate("#/trips/new") }, "+ 新建差旅") : null));

  clear(root, header, statsGrid, filters, table);
}

// ---- create ---------------------------------------------------------------

export function renderTripCreate(root, ctx) {
  const text = el("textarea", { rows: 4, maxlength: 2000, placeholder: "例如：10月28日到30日从北京去上海参加供应商大会，预算四千元" });
  const out = el("div");
  const go = button("开始", async () => {
    await busy(go, async () => {
      clear(out);
      const body = text.value.trim() ? { text: text.value.trim() } : {};
      try {
        const res = await api.post("/api/trips", body);
        const id = res.trip.id;
        if (body.text) pushChat(id, { who: "me", text: body.text });
        if (res.assistant && res.assistant.error) {
          pushChat(id, { who: "error", text: `${res.assistant.error.code}：${res.assistant.error.message}` });
        } else if (res.assistant) {
          pushChat(id, { who: "bot", text: res.assistant.text, meta: `抽取：${generatorLabel(res.assistant.generator)}` });
        }
        ctx.navigate(`#/trips/${id}`);
      } catch (e) {
        out.append(errorBox(e));
      }
    });
  }, { kind: "primary" });
  clear(root,
    el("div", { class: "page-head" }, el("h2", null, "新建出差申请")),
    section("用一句话描述这次出差",
      el("p", { class: "muted" }, "系统只记录你明确说出的信息；没说清楚的日期不会被猜测，会继续追问。也可以留空，直接在表单里填写。"),
      text, el("div", { class: "actions" }, go), out));
}

// ---- detail ---------------------------------------------------------------

export async function renderTripDetail(root, ctx, id) {
  const trip = await api.get(`/api/trips/${id}`);
  const me = ctx.user;
  const isOwner = trip.employee_id === me.id;
  const owner = ctx.users.find((u) => u.id === trip.employee_id);
  const manager = ctx.users.find((u) => u.id === owner?.manager_id);
  const isDirectManager = me.role === "manager" && owner && owner.manager_id === me.id;
  const editable = isOwner && EDITABLE.has(trip.status);
  const rerender = () => renderTripDetail(root, ctx, id);
  const [rules, audit] = await Promise.all([
    rulesOf(trip.policy_version),
    api.get(`/api/trips/${id}/audit`).catch(() => null),
  ]);

  const banners = [];
  if (trip.policy_change_notice) {
    const n = trip.policy_change_notice;
    const canRevalidate = (isOwner || isDirectManager) && trip.status === "PENDING_APPROVAL";
    banners.push(notice("warn",
      el("strong", null, "制度已更新："), `本申请按 ${n.bound_version} 校验，当前发布版本为 ${n.published_version}。`,
      canRevalidate ? revalidateButton(trip, rerender, banners) : el("span", { class: "muted" }, " 申请人或其直属经理可以按新版本重新校验。")));
  }
  if (trip.status === "POLICY_BLOCKED") {
    banners.push(notice("error", el("strong", null, "提交被制度阻断。"), isOwner ? " 修改下方信息后可再次提交。" : ""));
  }
  if (trip.delivery && trip.delivery.error) {
    banners.push(notice("error", el("strong", null, "交付写入失败："), trip.delivery.error, `（已尝试 ${trip.delivery.attempts} 次）`,
      me.role === "admin" && trip.status === "DELIVERY_PENDING" ? retryDeliveryButton(trip, rerender) : null));
  }
  if (trip.delivery && trip.delivery.itinerary_url) {
    banners.push(notice("ok", "行程单与交付邮件已生成。 ",
      el("a", { href: `#/trips/${trip.id}/itinerary?revision=${trip.revision}` }, "查看行程单")));
  }

  // The submit transcript is only meaningful to the person who ran it, and only while the
  // trip is still in the state that run ended in.
  const run = lastProgress.get(id);
  const progress = run && run.userId === me.id && run.revision === trip.revision && run.status === trip.status ? run.items : null;
  const results = trip.plan || trip.violations.length || trip.blocks.length || trip.approval_reasons.length;
  const decisions = (audit?.events || []).filter((e) => e.type === "decision_recorded");
  const lastDecision = decisions[decisions.length - 1];

  // request_changes bumps the revision and returns the trip to COLLECTING; the decision itself
  // was recorded on the previous revision, so surface its comment while the trip is being edited.
  const changeRequest = el("div");
  if (lastDecision && trip.status === "COLLECTING" && lastDecision.payload.decision === "request_changes"
      && lastDecision.revision === trip.revision - 1) {
    changeRequest.append(notice("warn", el("strong", null, `${userName(lastDecision.actor)} 要求修改：`),
      lastDecision.payload.comment || "", el("span", { class: "muted small" }, `（${localDateTime(lastDecision.ts)}）`)));
  }

  // Manager decision controls: header buttons give a one-click approve; 拒绝/退回修改 jump to the
  // full decision form because those decisions require a comment.
  const approval = trip.current_approval;
  const canDecide = approval && approval.status === "pending" && isDirectManager && me.id !== trip.employee_id;
  const decisionMsg = el("div");
  const decisionPanel = canDecide
    ? section("审批操作", decisionMsg, decisionForm(approval, rerender))
    : null;
  const openDecision = () => {
    decisionPanel?.scrollIntoView({ behavior: "smooth", block: "center" });
    decisionPanel?.querySelector("textarea")?.focus();
  };
  let approveKey = newIdempotencyKey("decision-approve");
  const quickApprove = async (ev) => {
    await busy(ev.currentTarget, async () => {
      try {
        await api.post(`/api/trips/${approval.trip_id}/decision`,
          { revision: approval.revision, decision: "approve", comment: null }, approveKey);
        ctx.navigate(`#/trips/${id}`);
      } catch (e) {
        if (e.code !== "NETWORK_ERROR") approveKey = newIdempotencyKey("decision-approve");
        clear(decisionMsg, errorBox(e));
        openDecision();
      }
    });
  };

  // Breadcrumb
  const breadcrumb = el("div", { class: "breadcrumb" },
    el("a", { href: "#/trips" }, "差旅列表"),
    el("span", { class: "sep" }, "/"),
    el("span", { class: "current" }, `TRP-${trip.id.slice(0, 8)}`));

  // Detail header
  const detailHeader = el("div", { class: "detail-header" },
    el("div", null,
      el("div", { class: "detail-title" }, `${trip.slots.origin || "?"} → ${trip.slots.destination || "?"} ${trip.slots.purpose || "商务差旅"}`),
      el("div", { class: "detail-meta" },
        el("span", null,
          el("svg", { width: "14", height: "14", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
            el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" })),
          ` ${userName(trip.employee_id)} · ${owner?.department || ""}`),
        el("span", null,
          el("svg", { width: "14", height: "14", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
            el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" })),
          ` ${trip.slots.start_date || "?"} 至 ${trip.slots.end_date || "?"}`),
        el("span", null,
          el("svg", { width: "14", height: "14", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
            el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" })),
          ` ${trip.slots.purpose || "出差"}`))),
    el("div", { class: "header-actions" },
      canDecide
        ? [el("button", { class: "btn btn-danger", onclick: openDecision }, "拒绝"),
           el("button", { class: "btn btn-secondary", onclick: openDecision }, "退回修改"),
           el("button", { class: "btn btn-success", onclick: quickApprove }, "通过审批")]
        : null));

  // Basic info card
  const basicInfoCard = el("div", { class: "card" },
    el("div", { class: "card-title" }, "基本信息"),
    el("div", { class: "info-grid" },
      el("div", { class: "info-item" },
        el("label", null, "差旅编号"),
        el("div", { class: "value" }, `TRP-${trip.id.slice(0, 8)}`)),
      el("div", { class: "info-item" },
        el("label", null, "差旅类型"),
        el("div", { class: "value" }, "国内差旅")),
      el("div", { class: "info-item" },
        el("label", null, "出发城市"),
        el("div", { class: "value" }, trip.slots.origin || "?")),
      el("div", { class: "info-item" },
        el("label", null, "目的城市"),
        el("div", { class: "value" }, trip.slots.destination || "?")),
      el("div", { class: "info-item" },
        el("label", null, "出发日期"),
        el("div", { class: "value" }, trip.slots.start_date || "?")),
      el("div", { class: "info-item" },
        el("label", null, "返回日期"),
        el("div", { class: "value" }, trip.slots.end_date || "?")),
      el("div", { class: "info-item" },
        el("label", null, "差旅事由"),
        el("div", { class: "value" }, trip.slots.purpose || "?")),
      el("div", { class: "info-item" },
        el("label", null, "同行人"),
        el("div", { class: "value" }, "无"))));

  // Expense breakdown card
  const expenseCard = el("div", { class: "card" },
    el("div", { class: "card-title" }, "费用明细"),
    el("table", { class: "expense-table" },
      el("thead", null,
        el("tr", null,
          el("th", null, "费用项目"),
          el("th", null, "说明"),
          el("th", { style: "text-align:right" }, "金额"))),
      el("tbody", null,
        trip.cost_breakdown ? [
          el("tr", null,
            el("td", null, "去程交通"),
            el("td", null, `${trip.slots.origin} → ${trip.slots.destination}`),
            el("td", { class: "expense-amount" }, trip.cost_breakdown.outbound_fen ? yuan(trip.cost_breakdown.outbound_fen) : "—")),
          el("tr", null,
            el("td", null, "返程交通"),
            el("td", null, `${trip.slots.destination} → ${trip.slots.origin}`),
            el("td", { class: "expense-amount" }, trip.cost_breakdown.return_fen ? yuan(trip.cost_breakdown.return_fen) : "—")),
          el("tr", null,
            el("td", null, "酒店住宿"),
            el("td", null, `${trip.cost_breakdown.nights || 0} 晚`),
            el("td", { class: "expense-amount" }, trip.cost_breakdown.hotel_fen ? yuan(trip.cost_breakdown.hotel_fen) : "—")),
          el("tr", null,
            el("td", null, "餐饮补贴"),
            el("td", null, `${trip.cost_breakdown.days || 0} 天`),
            el("td", { class: "expense-amount" }, trip.cost_breakdown.meal_fen ? yuan(trip.cost_breakdown.meal_fen) : "—")),
          el("tr", { class: "total-row" },
            el("td", { colspan: 2 }, "合计"),
            el("td", { class: "expense-amount" }, trip.cost_breakdown.total_fen ? yuan(trip.cost_breakdown.total_fen) : "—")),
        ] : el("tr", null, el("td", { colspan: 3, class: "muted" }, "尚未生成成本明细")))));

  // Approval status card — steps follow the live trip status and real audit decisions,
  // not a hardcoded template.
  const approvalStatusCard = el("div", { class: "card" },
    el("div", { class: "card-title" }, "审批状态"),
    el("div", { style: "text-align:center; margin-bottom: 16px;" },
      statusBadge(trip.status)),
    el("div", { class: "timeline-v2" },
      approvalSteps(trip, decisions)));

  // Approvers card — the person who actually decides is the applicant's direct manager.
  const approversCard = el("div", { class: "card" },
    el("div", { class: "card-title" }, "审批人"),
    manager
      ? el("div", { class: "approver-item" },
          el("div", { class: "approver-avatar" }, manager.name.charAt(0)),
          el("div", { class: "approver-info" },
            el("div", { class: "approver-name" }, manager.name),
            el("div", { class: "approver-role" }, `${manager.department || ""} · 直属上级`)))
      : el("p", { class: "muted small" }, "未设置直属上级，申请将由管理员处理。"));

  // Content grid
  const leftCol = el("div", null, basicInfoCard, expenseCard);
  const rightCol = el("div", null, approvalStatusCard, approversCard);
  const contentGrid = el("div", { class: "content-grid" }, leftCol, rightCol);

  clear(root,
    breadcrumb,
    detailHeader,
    changeRequest,
    banners,
    progress ? section("最近一次提交过程", progressList(progress)) : null,
    contentGrid,
    results ? el("div", { style: "margin-top:24px" },
      el("div", { class: "grid-2" },
        section("出行方案", planView(trip.plan)),
        section("规则判断", ruleResults(trip, rules)))) : null,
    decisionPanel ? el("div", { style: "margin-top:24px" }, decisionPanel) : null,
    editable
      ? el("div", { style: "margin-top:24px" },
          el("div", { class: "grid-2" },
            el("div", null, chatPanel(trip, rerender), imagePanel(trip, rerender)),
            el("div", null, slotForm(trip, owner, rerender), submitPanel(trip, rerender))))
      : null);
}

// Map the live trip status onto a compact approval timeline. Decision events from the audit
// log supply who decided, when and with what comment, so every step reflects the real state.
function approvalSteps(trip, decisions) {
  const items = [];
  const step = (title, desc, time, dot) => items.push(el("div", { class: "timeline-item" },
    el("div", { class: `timeline-dot ${dot}` }),
    dot === "gray" ? null : el("div", { class: "timeline-line" }),
    el("div", { class: "timeline-content" },
      el("div", { class: "timeline-title", style: dot === "gray" ? "color:var(--muted-2)" : null }, title),
      el("div", { class: "timeline-desc" }, desc),
      el("div", { class: "timeline-time" }, time))));

  step("提交申请", `${userName(trip.employee_id)} 提交了差旅申请`, localDateTime(trip.created_at), "green");

  const last = decisions[decisions.length - 1];
  const decided = last && last.revision === trip.revision ? last : null;
  if (trip.status === "PENDING_APPROVAL") {
    step("审批中", `等待 ${userName(trip.current_approval?.approver_id)} 审批`, "—", "amber");
  } else if (trip.status === "REJECTED") {
    step("已驳回",
      `${userName(decided?.actor || trip.current_approval?.approver_id)} 驳回${decided?.payload.comment ? `：${decided.payload.comment}` : ""}`,
      decided ? localDateTime(decided.ts) : "—", "red");
  } else if (["AUTO_APPROVED", "APPROVED", "DELIVERY_PENDING", "DELIVERED"].includes(trip.status)) {
    step("审批通过",
      decided ? `${userName(decided.actor)} 批准${decided.payload.comment ? `：${decided.payload.comment}` : ""}` : "满足自动通过条件",
      decided ? localDateTime(decided.ts) : "—", "green");
    if (trip.status === "DELIVERY_PENDING") step("行程交付", "行程单与交付邮件生成中", "—", "gray");
    if (trip.status === "DELIVERED") step("行程已交付", "行程单与交付邮件已发送", "—", "green");
  } else {
    step("提交校验", "信息确认后提交审批", "—", "gray");
  }
  return items;
}

function progressList(items) {
  return el("ol", { class: "progress" }, items.map((p) => el("li", { class: `pg-${p.kind}` }, p.text)));
}

// ---- chat -----------------------------------------------------------------

function chatPanel(trip, rerender) {
  const log = el("div", { class: "chat-log", "aria-live": "polite" });
  const draw = () => clear(log, chatLog(trip.id).map((m) => el("div", { class: `msg msg-${m.who}` },
    el("div", { class: "msg-text" }, m.text), m.meta ? el("div", { class: "msg-meta" }, m.meta) : null)));
  draw();
  if (!chatLog(trip.id).length) {
    log.append(el("div", { class: "msg msg-bot" }, el("div", { class: "msg-text" },
      "请告诉我这次出差的出发城市、目的城市、日期（具体到日）、事由和预算。")));
  }
  const input = el("textarea", { rows: 2, maxlength: 2000, placeholder: "补充或修改信息，例如：预算改成五千" });
  const typing = el("div", { class: "muted small" });
  const send = button("发送", async () => {
    const text = input.value.trim();
    if (!text) return;
    await busy(send, async () => {
      pushChat(trip.id, { who: "me", text });
      draw();
      input.value = "";
      try {
        const terminal = await stream(`/api/trips/${trip.id}/messages`, { text, expected_revision: trip.revision }, {
          key: newIdempotencyKey("msg"),
          onEvent: (ev) => {
            if (ev.type === "status") typing.textContent = ev.payload.message;
            if (ev.type === "slots") {
              const changed = (ev.payload.changes || []).map((c) => FIELDS[c.field] || c.field);
              const rejected = (ev.payload.invalid_fields || []).filter((f) => "value" in f).map((f) => FIELDS[f.field] || f.field);
              trip._lastSlotsMeta = `抽取：${generatorLabel(ev.payload.generator)}` +
                (changed.length ? `；写入 ${changed.join("、")}` : "；未写入新值") + (rejected.length ? `；未采纳 ${rejected.join("、")}` : "");
            }
            if (ev.type === "token") pushChat(trip.id, { who: "bot", text: ev.payload.text, meta: trip._lastSlotsMeta });
            if (ev.type === "error") pushChat(trip.id, { who: "error", text: `${ev.payload.code}：${ev.payload.message}` });
          },
        });
        typing.textContent = "";
        if (terminal.type === "done") rerender();
        else draw();
      } catch (e) {
        pushChat(trip.id, { who: "error", text: `${e.code}：${e.message}` });
        typing.textContent = "";
        draw();
        if (e.status === 409) rerender();
      }
    });
  }, { kind: "primary" });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) send.click();
  });
  return section("对话", log, typing, input, el("div", { class: "actions" }, el("span", { class: "muted small" }, "Ctrl+Enter 发送"), send));
}

// ---- slot form ---------------------------------------------------------------

function slotForm(trip, owner, rerender) {
  const s = trip.slots;
  const inputs = {};
  const invalid = new Map((trip.invalid_fields || []).map((f) => [f.field, f]));
  const missing = new Set(trip.missing_fields || []);
  const prefs = s.preferences || {};
  const row = (field, input, extra) => el("label", { class: `field ${missing.has(field) ? "is-missing" : ""} ${invalid.has(field) ? "is-invalid" : ""}` },
    el("span", { class: "field-label" }, FIELDS[field], REQUIRED.includes(field) ? el("span", { class: "req" }, "*") : null),
    input, extra || null,
    invalid.has(field) ? el("span", { class: "field-error" }, invalid.get(field).message) : null);

  inputs.origin = el("input", { value: s.origin || "", placeholder: "如 北京" });
  inputs.destination = el("input", { value: s.destination || "", placeholder: "如 上海" });
  inputs.start_date = el("input", { type: "date", value: s.start_date || "" });
  inputs.end_date = el("input", { type: "date", value: s.end_date || "" });
  inputs.purpose = el("input", { value: s.purpose || "", placeholder: "如 供应商大会" });
  inputs.declared_budget_fen = el("input", { value: s.declared_budget_fen ? (s.declared_budget_fen / 100).toFixed(2) : "", inputmode: "decimal", placeholder: "元" });
  inputs.meeting_address = el("input", { value: s.meeting_address || "", placeholder: "可选" });
  const early = el("select", null,
    el("option", { value: "", selected: !("avoid_early_departure" in prefs) }, "沿用个人偏好"),
    el("option", { value: "true", selected: prefs.avoid_early_departure === true }, "本次回避早班（08:00 前）"),
    el("option", { value: "false", selected: prefs.avoid_early_departure === false }, "本次不回避早班"));
  const notes = el("input", { value: prefs.notes || "", maxlength: 200, placeholder: "备注（只供参考，不参与排序）" });

  const suggest = owner && owner.home_city && !s.origin
    ? el("button", { type: "button", class: "link", onclick: () => { inputs.origin.value = owner.home_city; } }, `使用常驻城市 ${owner.home_city}`)
    : null;

  const out = el("div");
  const save = button("保存修改", async () => {
    clear(out);
    const changes = {};
    const text = (f) => inputs[f].value.trim() || null;
    for (const f of ["origin", "destination", "start_date", "end_date", "purpose", "meeting_address"]) {
      if (text(f) !== (s[f] ?? null)) changes[f] = text(f);
    }
    const budgetText = inputs.declared_budget_fen.value.trim();
    const budget = budgetText ? parseYuan(budgetText) : null;
    if (budgetText && budget === null) {
      out.append(notice("error", "申报预算请填写大于 0 的金额（元，最多两位小数）。"));
      return;
    }
    if (budget !== (s.declared_budget_fen ?? null)) changes.declared_budget_fen = budget;
    const p = {};
    if (early.value) p.avoid_early_departure = early.value === "true";
    if (notes.value.trim()) p.notes = notes.value.trim();
    const newPrefs = Object.keys(p).length ? p : null;
    if (JSON.stringify(newPrefs) !== JSON.stringify(s.preferences ?? null)) changes.preferences = newPrefs;
    if (!Object.keys(changes).length) {
      out.append(notice("info", "没有需要保存的修改。"));
      return;
    }
    await busy(save, async () => {
      try {
        await api.patch(`/api/trips/${trip.id}/slots`, { expected_revision: trip.revision, slots: changes });
        rerender();
      } catch (e) {
        out.append(errorBox(e));
      }
    });
  });

  return section("申请信息",
    missing.size ? el("p", { class: "muted small" }, `还缺：${[...missing].map((f) => FIELDS[f]).join("、")}`) : el("p", { class: "muted small" }, "必填项已齐全。"),
    el("div", { class: "form" },
      row("origin", inputs.origin, suggest), row("destination", inputs.destination),
      row("start_date", inputs.start_date), row("end_date", inputs.end_date),
      row("purpose", inputs.purpose), row("declared_budget_fen", inputs.declared_budget_fen),
      row("meeting_address", inputs.meeting_address),
      el("label", { class: "field" }, el("span", { class: "field-label" }, FIELDS.preferences), early, notes)),
    el("div", { class: "actions" }, save), out);
}

// ---- submit ---------------------------------------------------------------

function describe(ev) {
  const p = ev.payload || {};
  switch (ev.type) {
    case "trip_status": return { kind: "status", text: `状态 → ${STATUS[p.status] || p.status}${p.replayed ? "（重放）" : ""}` };
    case "status": return { kind: "info", text: p.message };
    case "plan": return { kind: "info", text: `方案已生成${p.incomplete ? "（不完整，待人工补录）" : ""}` };
    case "violations": return { kind: "info", text: `规则判断：阻断 ${p.blocks.length} 项，违规 ${p.violations.length} 项，审批理由 ${p.approval_reasons.length} 项` };
    case "approval_required": return { kind: "info", text: `审批卡已发给 ${userName(p.approver_id)}` };
    case "error": return { kind: "error", text: `${p.code}：${p.message}` };
    case "done": return { kind: "done", text: `完成：${STATUS[p.status] || p.status}` };
    default: return null;
  }
}

function submitPanel(trip, rerender) {
  const out = el("div");
  const live = el("ol", { class: "progress" });
  const storeKey = `tcd.submitKey.${trip.id}.${trip.revision}`;
  const ready = trip.status === "COLLECTING" && !trip.missing_fields.length && !trip.invalid_fields.length;

  const run = async (key) => {
    clear(out);
    clear(live);
    const items = [];
    sessionStorage.setItem(storeKey, key);
    try {
      const terminal = await stream(`/api/trips/${trip.id}/submit`, { expected_revision: trip.revision }, {
        key,
        onEvent: (ev) => {
          const d = describe(ev);
          if (!d) return;
          items.push(d);
          live.append(el("li", { class: `pg-${d.kind}` }, d.text));
        },
      });
      sessionStorage.removeItem(storeKey);
      // Terminal errors (NO_APPROVER, WORKFLOW_FAILED) leave the trip back in COLLECTING.
      lastProgress.set(trip.id, {
        userId: currentUserId(), items,
        revision: terminal.type === "done" ? terminal.payload.revision : trip.revision,
        status: terminal.type === "done" ? terminal.payload.status : "COLLECTING",
      });
      if (terminal.type === "error" && terminal.payload.code === "NO_APPROVER") {
        items.push({ kind: "error", text: "需要审批，但申请人没有直属上级，未创建审批卡；申请已退回“收集信息”。" });
      }
      rerender();
    } catch (e) {
      if (e.code === "NETWORK_ERROR") {
        out.append(notice("error", e.message, " ",
          button("用同一请求重试", () => run(key)), " ", button("刷新状态", rerender)));
      } else {
        sessionStorage.removeItem(storeKey);
        out.append(errorBox(e));
      }
    }
  };

  const pendingKey = sessionStorage.getItem(storeKey);
  const submit = button("提交校验", async () => {
    await busy(submit, () => run(pendingKey || newIdempotencyKey("submit")));
  }, { kind: "primary", disabled: !ready, title: ready ? "" : "必填项齐全且无格式错误后才能提交" });

  return section("提交",
    el("p", { class: "muted small" }, "提交后系统按当前发布的制度查询交通、酒店与天气，计算成本并判断是否需要审批。只有你点击提交才会开始（D-13）。"),
    trip.status === "POLICY_BLOCKED" ? el("p", { class: "muted small" }, "被阻断的申请需要先修改信息（保存后回到“收集信息”），再提交。") : null,
    el("div", { class: "actions" }, submit), live, out);
}

// ---- invitation image -----------------------------------------------------

function imagePanel(trip, rerender) {
  const out = el("div");
  const file = el("input", { type: "file", accept: "image/png,image/jpeg" });
  const go = button("识别邀请函", async () => {
    if (!file.files.length) {
      clear(out, notice("info", "请先选择 PNG 或 JPG 图片。"));
      return;
    }
    await busy(go, async () => {
      clear(out, el("p", { class: "muted" }, "正在上传与识别…"));
      try {
        const form = new FormData();
        form.append("file", file.files[0]);
        const up = await api.upload("/api/uploads", form);
        const ex = await api.post(`/api/trips/${trip.id}/image-extractions`, { upload_id: up.id });
        clear(out, proposalForm(trip, ex, rerender));
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  });
  return section("从邀请函识别",
    el("p", { class: "muted small" }, "图片只保存在本机。识别结果只是建议，确认前不会修改申请。"),
    el("div", { class: "actions" }, file, go), out);
}

function proposalForm(trip, ex, rerender) {
  const keys = ["destination", "start_date", "end_date", "purpose", "meeting_address"];
  const inputs = Object.fromEntries(keys.map((k) => [k, el("input", {
    type: k.endsWith("_date") ? "date" : "text", value: ex.fields[k] ?? "",
  })]));
  const out = el("div");
  const confirm = button("确认写入申请", async () => {
    const fields = {};
    for (const k of keys) {
      const v = inputs[k].value.trim();
      fields[k] = v || null; // null = skip (keeps the existing slot)
    }
    await busy(confirm, async () => {
      try {
        await api.post(`/api/trips/${trip.id}/image-extractions/${ex.id}/confirm`, { expected_revision: trip.revision, fields });
        rerender();
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  }, { kind: "primary" });
  return el("div", { class: "proposal" },
    el("p", { class: "small" }, "识别结果（", el("span", { class: "src src-model" }, "model"), ` ${generatorLabel(ex.generator)}），可修改后确认：`),
    el("div", { class: "form" }, keys.map((k) => el("label", { class: "field" }, el("span", { class: "field-label" }, FIELDS[k]), inputs[k]))),
    el("div", { class: "actions" }, confirm, button("放弃", () => rerender())), out);
}

// ---- revalidate / delivery retry --------------------------------------------

function revalidateButton(trip, rerender, banners) {
  const out = el("span");
  let key = null;
  const b = button("按新版本重新校验", async () => {
    key = key || newIdempotencyKey("revalidate");
    await busy(b, async () => {
      try {
        const res = await api.post(`/api/trips/${trip.id}/revalidate`, { revision: trip.revision }, key);
        lastProgress.set(trip.id, {
          userId: currentUserId(), revision: res.trip.revision, status: res.trip.status,
          items: [{ kind: "done", text: `重新校验：revision ${res.previous_revision} → ${res.trip.revision}，结论 ${STATUS[res.outcome] || res.outcome}，当前状态 ${STATUS[res.trip.status] || res.trip.status}` }],
        });
        rerender();
      } catch (e) {
        if (e.code !== "NETWORK_ERROR") key = null;
        clear(out, errorBox(e));
      }
    });
  }, { kind: "primary" });
  void banners;
  return el("span", null, " ", b, out);
}

function retryDeliveryButton(trip, rerender) {
  const out = el("span");
  const b = button("重试交付", async () => {
    await busy(b, async () => {
      try {
        await api.post(`/api/trips/${trip.id}/delivery/retry`);
        rerender();
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  }, { kind: "primary" });
  return el("span", null, " ", b, out);
}

// ---- itinerary --------------------------------------------------------------

export async function renderItinerary(root, ctx, id, params) {
  const rev = params.get("revision");
  const data = await api.get(`/api/trips/${id}/itinerary${rev ? `?revision=${encodeURIComponent(rev)}` : ""}`);
  const it = data.itinerary;
  clear(root,
    el("div", { class: "page-head no-print" }, el("a", { href: `#/trips/${id}`, class: "back" }, "← 返回申请"),
      button("打印", () => window.print())),
    el("article", { class: "itinerary-doc" },
      el("h2", null, "差旅行程单"),
      el("p", { class: "muted" }, `${it.employee.name}（${it.employee.department}） · revision ${it.revision} · 制度 ${it.policy_version} · 生成于 ${localDateTime(data.created_at)}`),
      section("申请信息", slotsTable(it.slots)),
      section("出行方案", planView(it.plan)),
      section("成本明细", costTable(it.cost_breakdown)),
      section("审批", approvalLine(it.approval))));
}

// itinerary.approval: {mode: "auto"} | {mode: "manager", approval_id, approver_id, comment, decided_at}
function approvalLine(a) {
  if (!a) return el("p", { class: "muted" }, "—");
  if (a.mode === "auto") return el("p", null, "未触发审批条件，系统按已发布规则自动通过。");
  return el("p", null, `${userName(a.approver_id)} 批准`, a.comment ? `，意见：${a.comment}` : "",
    a.decided_at ? el("span", { class: "muted small" }, ` · ${localDateTime(a.decided_at)}`) : null);
}
