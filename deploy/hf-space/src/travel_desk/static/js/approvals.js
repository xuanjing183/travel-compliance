// Manager approvals: pending list (#12) and the decision form (#13).

import { api, newIdempotencyKey } from "./api.js";
import { rulesOf } from "./parts.js";
import { STATUS, busy, button, clear, el, errorBox, localDateTime, notice, yuan } from "./ui.js";

const DECISIONS = [
  ["approve", "批准", "primary"],
  ["request_changes", "要求修改", "secondary"],
  ["reject", "驳回", "danger"],
];

// One Idempotency-Key per (approval, decision). A retry after a dropped connection reuses it;
// any answered request (success or business error) releases it.
export function decisionForm(approval, onDone) {
  const comment = el("textarea", { rows: 2, maxlength: 500, placeholder: "审批意见（驳回或要求修改时必填）" });
  const out = el("div");
  const keys = new Map();
  const buttons = DECISIONS.map(([decision, label, kind]) => {
    const b = button(label, async () => {
      clear(out);
      const text = comment.value.trim();
      if (decision !== "approve" && !text) {
        out.append(notice("error", `「${label}」需要填写审批意见。`));
        comment.focus();
        return;
      }
      if (!keys.has(decision)) keys.set(decision, newIdempotencyKey(`decision-${decision}`));
      await busy(b, async () => {
        try {
          const res = await api.post(`/api/trips/${approval.trip_id}/decision`,
            { revision: approval.revision, decision, comment: text || null }, keys.get(decision));
          keys.delete(decision);
          onDone(res);
        } catch (e) {
          if (e.code !== "NETWORK_ERROR") keys.delete(decision);
          out.append(errorBox(e));
          if (e.code === "NETWORK_ERROR") out.append(el("p", { class: "muted small" }, "连接中断：再次点击同一按钮会用同一请求重试，不会重复审批。"));
        }
      });
    }, { kind });
    return b;
  });
  return el("div", { class: "decision-form" },
    el("h4", null, `审批 revision ${approval.revision}`),
    comment, el("div", { class: "actions" }, buttons), out);
}

export async function renderApprovals(root, ctx) {
  const [{ policies }] = await Promise.all([api.get("/api/policies")]);
  const published = (policies.find((p) => p.status === "published") || {}).version;
  // Full route rebuild (not just this view) so the sidebar's pending badge refreshes too.
  const rerender = () => ctx.navigate("#/approvals");

  const tabDefs = [
    { key: "pending", label: "待审批" },
    { key: "decided", label: "我已审批" },
    { key: "all", label: "全部" },
  ];
  let activeTab = "pending";
  const tabData = {};   // key → { approvals: [...], summary: {...} }
  const tabLoading = {}; // key → bool
  let summary = null;    // { pending_count, decided_today, decided_this_month, avg_decision_hours }

  // Decision badge text for the "decided" tab.
  const DECISION_LABEL = { approve: "已批准", reject: "已驳回", request_changes: "要求修改" };
  const DECISION_KIND  = { approve: "ok",    reject: "error",  request_changes: "warn" };

  // Trip status label for the "initiated" tab.
  const TRIP_STATUS_LABEL = {
    PENDING_APPROVAL: "待审批", APPROVED: "已批准", AUTO_APPROVED: "自动通过",
    REJECTED: "已驳回", COLLECTING: "收集信息", DELIVERED: "已交付",
  };

  async function fetchTab(key) {
    if (tabData[key]) return tabData[key];
    tabLoading[key] = true;
    try {
      tabData[key] = await api.get(`/api/approvals?tab=${key}`);
      // Store summary from any tab response (they all return the same summary)
      if (tabData[key].summary) summary = tabData[key].summary;
    } finally {
      tabLoading[key] = false;
    }
    return tabData[key];
  }

  function buildCard(a, tab) {
    const rules = rulesOf(a.card.policy_version);
    const stale = published && a.card.policy_version !== published;
    const card = el("div", { class: "ap-card-v2" });

    // Header
    const cardHeader = el("div", { class: "approval-card-header" },
      el("div", null,
        el("div", { class: "ap-title" }, `${a.card.slots.origin || "?"} → ${a.card.slots.destination || "?"} ${a.card.slots.purpose || "商务差旅"}`),
        el("div", { class: "ap-subtitle" }, `${a.card.employee.name} · ${a.card.employee.department} · ${a.card.slots.purpose || "出差"}`)),
      el("span", { class: "ap-id" }, `TRP-${a.trip_id.slice(0, 8)}`));
    card.appendChild(cardHeader);

    // Fields grid
    const fields = el("div", { class: "ap-fields" },
      el("div", { class: "field" },
        el("label", null, "日期"),
        el("div", { class: "value" }, `${a.card.slots.start_date || "?"} - ${a.card.slots.end_date || "?"}`)),
      el("div", { class: "field" },
        el("label", null, "预算"),
        el("div", { class: "value" }, a.card.cost_breakdown?.total_fen ? yuan(a.card.cost_breakdown.total_fen) : "—")),
      el("div", { class: "field" },
        el("label", null, "目的地"),
        el("div", { class: "value" }, a.card.slots.destination || "?")),
      el("div", { class: "field" },
        el("label", null, "审批节点"),
        el("div", { class: "value" }, "部门经理")));
    card.appendChild(fields);

    // Tab-specific footer
    const footer = el("div", { class: "ap-footer" });
    const meta = el("div", { class: "ap-meta" },
      el("span", null, el("span", { class: "priority-dot priority-high" }), "高优先级"),
      el("span", null, `提交于 ${localDateTime(a.created_at)}`));
    footer.appendChild(meta);

    const actions = el("div", { class: "ap-actions" });

    // Trip status badge (shown on all tabs)
    const tripStatus = a.trip_status;
    if (tripStatus) {
      const tripStatusLabel = TRIP_STATUS_LABEL[tripStatus] || tripStatus;
      const tripStatusKind = tripStatus === "APPROVED" || tripStatus === "AUTO_APPROVED" ? "ok"
        : tripStatus === "REJECTED" ? "error"
        : tripStatus === "PENDING_APPROVAL" ? "warn"
        : tripStatus === "DELIVERED" ? "ok"
        : "muted";
      actions.appendChild(el("span", { class: `badge badge-${tripStatusKind}` }, tripStatusLabel));
    }

    if (tab === "pending") {
      // Full decision form + quick-approve button
      const decisionBox = el("div", { class: "ap-decision", style: "display:none" },
        decisionForm(a, (res) => {
          ctx.flash = `已处理 ${a.card.employee.name} 的申请，当前状态：${STATUS[res.trip.status] || res.trip.status}`;
          rerender();
        }));
      const openForm = () => { decisionBox.style.display = ""; decisionBox.querySelector("textarea")?.focus(); };
      let approveKey = newIdempotencyKey("decision-approve");
      const approveBtn = el("button", { class: "btn-sm btn-sm-success" }, "通过");
      approveBtn.onclick = async () => {
        await busy(approveBtn, async () => {
          try {
            const res = await api.post(`/api/trips/${a.trip_id}/decision`,
              { revision: a.revision, decision: "approve", comment: null }, approveKey);
            ctx.flash = `已批准 ${a.card.employee.name} 的申请，当前状态：${STATUS[res.trip.status] || res.trip.status}`;
            rerender();
          } catch (e) {
            if (e.code !== "NETWORK_ERROR") approveKey = newIdempotencyKey("decision-approve");
            decisionBox.style.display = "";
            decisionBox.append(errorBox(e));
            if (e.code === "NETWORK_ERROR") {
              decisionBox.append(el("p", { class: "muted small" }, "连接中断：再次点击「通过」会用同一请求重试，不会重复审批。"));
            }
          }
        });
      };
      actions.appendChild(el("button", { class: "btn-sm btn-sm-ghost", onclick: () => ctx.navigate(`#/trips/${a.trip_id}`) }, "查看详情"));
      actions.appendChild(el("button", { class: "btn-sm btn-sm-danger", onclick: openForm }, "拒绝"));
      actions.appendChild(approveBtn);
      card.appendChild(decisionBox);
    } else if (tab === "decided") {
      // Show decision result badge + comment
      const label = DECISION_LABEL[a.decision] || a.decision || "已处理";
      const kind = DECISION_KIND[a.decision] || "ok";
      actions.appendChild(el("span", { class: `badge badge-${kind}` }, label));
      if (a.decided_at) {
        actions.appendChild(el("span", { class: "muted small" }, ` · ${localDateTime(a.decided_at)}`));
      }
      if (a.comment) {
        actions.appendChild(el("span", { class: "muted small" }, ` · "${a.comment}"`));
      }
      actions.appendChild(el("button", { class: "btn-sm btn-sm-ghost", onclick: () => ctx.navigate(`#/trips/${a.trip_id}`) }, "查看详情"));
    } else {
      // "all" tab — show approval status badge
      const statusLabel = a.status === "pending" ? "待审批" : a.status === "decided" ? (DECISION_LABEL[a.decision] || "已处理") : "已替换";
      const statusKind = a.status === "pending" ? "warn" : a.status === "decided" ? (a.decision === "approve" ? "ok" : "error") : "muted";
      actions.appendChild(el("span", { class: `badge badge-${statusKind}` }, statusLabel));
      if (a.decided_at) {
        actions.appendChild(el("span", { class: "muted small" }, ` · ${localDateTime(a.decided_at)}`));
      }
      actions.appendChild(el("button", { class: "btn-sm btn-sm-ghost", onclick: () => ctx.navigate(`#/trips/${a.trip_id}`) }, "查看详情"));
    }

    footer.appendChild(actions);
    card.appendChild(footer);

    // Stale warning (only meaningful for pending)
    if (stale && tab === "pending") {
      card.appendChild(notice("warn", el("strong", null, "制度已更新："),
        `此审批卡按 ${a.card.policy_version} 生成，当前发布版本为 ${published}。可在 `,
        el("a", { href: `#/trips/${a.trip_id}` }, "申请详情"), " 中按新版本重新校验后再审批。"));
    }

    return card;
  }

  function buildCards(approvals, tab) {
    return approvals.map(a => buildCard(a, tab));
  }

  async function renderTabContent() {
    clear(listContainer);
    // Show loading state
    listContainer.appendChild(el("p", { class: "muted" }, "加载中…"));
    const data = await fetchTab(activeTab);
    clear(listContainer);
    const approvals = data.approvals || [];
    if (approvals.length) {
      listContainer.appendChild(el("div", { class: "approval-list" }, buildCards(approvals, activeTab)));
    } else {
      const emptyMsg = {
        pending: "没有待审批的申请。只显示直属下属当前 revision 的审批卡。",
        decided: "还没有已审批的记录。",
        all: "没有任何审批记录。",
      };
      listContainer.appendChild(el("p", { class: "empty" }, emptyMsg[activeTab] || "暂无数据。"));
    }
    // Update tab counts
    tabBtns.forEach((b, i) => {
      const key = tabDefs[i].key;
      const countEl = b.querySelector(".count");
      if (countEl && tabData[key]) {
        countEl.textContent = String(tabData[key].approvals?.length ?? 0);
      }
    });
    // Update stats bar
    updateStatsBar();
  }

  function updateStatsBar() {
    if (!summary) return;
    const statNumbers = statsBar.querySelectorAll(".stat-number");
    if (statNumbers.length >= 4) {
      statNumbers[0].textContent = String(summary.pending_count ?? "—");
      statNumbers[1].textContent = String(summary.decided_today ?? "—");
      statNumbers[2].textContent = String(summary.decided_this_month ?? "—");
      statNumbers[3].textContent = summary.avg_decision_hours != null ? `${summary.avg_decision_hours}h` : "—";
    }
  }

  // Header with search
  const header = el("div", { class: "header" },
    el("h2", null, "审批中心"),
    el("div", { class: "header-actions" },
      el("div", { class: "search-box" },
        el("svg", { class: "search-icon", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
          el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" })),
        el("input", { type: "text", placeholder: "搜索差旅编号、申请人..." }))));

  // Stats bar
  const statsBar = el("div", { class: "stats-bar" },
    el("div", { class: "stat-item urgent" },
      el("div", { class: "stat-number" }, "—"),
      el("div", { class: "stat-label" }, "待我审批")),
    el("div", { class: "stat-item today" },
      el("div", { class: "stat-number" }, "—"),
      el("div", { class: "stat-label" }, "今日新增")),
    el("div", { class: "stat-item" },
      el("div", { class: "stat-number" }, "—"),
      el("div", { class: "stat-label" }, "本月已审")),
    el("div", { class: "stat-item" },
      el("div", { class: "stat-number" }, "—"),
      el("div", { class: "stat-label" }, "平均审批时长")));

  // Tabs
  const tabBtns = tabDefs.map(t =>
    el("button", {
      class: `bb-tab${t.key === activeTab ? " active" : ""}`,
      onclick: () => {
        activeTab = t.key;
        tabBtns.forEach(b => b.classList.remove("active"));
        tabBtns[tabDefs.indexOf(t)].classList.add("active");
        renderTabContent();
      },
    }, t.label, el("span", { class: "count" }, "…")));
  const tabs = el("div", { class: "bb-tabs" }, ...tabBtns);

  const listContainer = el("div");

  const flash = ctx.flash;
  ctx.flash = null;

  clear(root, header, statsBar, tabs,
    flash ? notice("ok", flash) : null,
    listContainer);

  // Initial load
  renderTabContent();
}
