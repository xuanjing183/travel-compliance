// Reports: analytics and insights page — all data from /api/reports.

import { api } from "./api.js";
import { clear, el, yuan } from "./ui.js";

const PERIODS = [
  { key: "week", label: "本周" },
  { key: "month", label: "本月" },
  { key: "quarter", label: "本季度" },
  { key: "year", label: "本年度" },
];

const COLORS = ["#4f46e5", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

export async function renderReports(root, ctx) {
  let activePeriod = "month";
  let data = null;

  async function load(period) {
    activePeriod = period;
    clear(listContainer);
    listContainer.appendChild(el("p", { class: "muted" }, "加载中…"));
    try {
      data = await api.get(`/api/reports?period=${period}`);
    } catch (e) {
      clear(listContainer);
      listContainer.appendChild(el("p", { class: "error" }, `加载失败：${e.message}`));
      return;
    }
    renderAll();
  }

  function renderAll() {
    clear(listContainer);
    const s = data.summary;
    const trend = data.monthly_trend;
    const breakdown = data.expense_breakdown;
    const depts = data.department_comparison;
    const dests = data.top_destinations;
    const eff = data.approval_efficiency;

    // Summary cards
    const summaryGrid = el("div", { class: "summary-grid" },
      summaryCard("blue", "差旅总数", String(s.trip_count), changeText(s.trip_count, "较上月")),
      summaryCard("green", "总费用", yuan(s.total_fen), changeText(s.total_fen, "较上月")),
      summaryCard("amber", "平均单次费用", yuan(s.avg_fen), ""),
      summaryCard("purple", "审批通过率", `${s.approval_pass_rate}%`,
        s.total_decided > 0 ? `共 ${s.total_decided} 条审批` : "暂无审批数据"));

    // Bar chart (monthly trend)
    const maxTrips = Math.max(...trend.map(t => t.trip_count), 1);
    const maxCost = Math.max(...trend.map(t => t.total_fen), 1);
    const barChart = el("div", { class: "chart-card" },
      el("div", { class: "chart-header" },
        el("div", { class: "chart-title" }, "月度差旅趋势"),
        el("div", { class: "chart-legend" },
          el("div", { class: "legend-item" }, el("span", { class: "legend-dot", style: "background:#4f46e5" }), "差旅次数"),
          el("div", { class: "legend-item" }, el("span", { class: "legend-dot", style: "background:#c7d2fe" }), "费用"))),
      el("div", { class: "bar-chart" },
        trend.map(t => {
          const h1 = Math.round(t.trip_count / maxTrips * 100);
          const h2 = Math.round(t.total_fen / maxCost * 100);
          return el("div", { class: "bar-group" },
            el("div", { class: "bar-wrapper" },
              el("div", { class: "bar primary", style: `height:${h1}%`, title: `${t.trip_count} 次` }),
              el("div", { class: "bar secondary", style: `height:${h2}%`, title: yuan(t.total_fen) })),
            el("div", { class: "bar-label" }, t.label));
        })));

    // Donut chart (expense breakdown)
    let cumulative = 0;
    const gradientStops = breakdown.map((b, i) => {
      const start = cumulative;
      cumulative += b.pct;
      return `${COLORS[i % COLORS.length]} ${start}% ${cumulative}%`;
    }).join(", ");

    const donutChart = el("div", { class: "chart-card" },
      el("div", { class: "chart-header" },
        el("div", { class: "chart-title" }, "费用构成")),
      el("div", { class: "donut-container" },
        el("div", { class: "donut-chart", style: `background: conic-gradient(${gradientStops})` }),
        el("div", { class: "donut-legend" },
          breakdown.map((b, i) =>
            el("div", { class: "donut-legend-item" },
              el("div", { class: "donut-legend-left" },
                el("div", { class: "donut-legend-dot", style: `background:${COLORS[i % COLORS.length]}` }),
                el("div", { class: "donut-legend-label" }, b.label)),
              el("div", { class: "donut-legend-value" }, `${b.pct}% (${yuan(b.fen)})`))))));

    const chartsGrid = el("div", { class: "charts-grid" }, barChart, donutChart);

    // Department comparison table
    const maxDeptFen = Math.max(...depts.map(d => d.total_fen), 1);
    const deptTable = el("div", { class: "chart-card" },
      el("div", { class: "chart-header" },
        el("div", { class: "chart-title" }, "各部门差旅费用对比")),
      depts.length === 0
        ? el("p", { class: "muted", style: "padding:16px 0;" }, "暂无部门数据")
        : el("table", { class: "data-table" },
            el("thead", null,
              el("tr", null,
                el("th", null, "部门"),
                el("th", null, "差旅次数"),
                el("th", null, "总费用"),
                el("th", null, "平均费用"),
                el("th", null, "费用占比"))),
            el("tbody", null,
              depts.map((d, i) =>
                el("tr", null,
                  el("td", { class: "dept" }, d.department),
                  el("td", null, String(d.trip_count)),
                  el("td", { class: "amount" }, yuan(d.total_fen)),
                  el("td", null, yuan(d.avg_fen)),
                  el("td", { class: "trend" },
                    el("span", { class: "trend-bar", style: `width:${Math.round(d.total_fen / maxDeptFen * 140)}px; background:${COLORS[i % COLORS.length]}` }),
                    ` ${d.pct}%`))))));

    const deptGrid = el("div", { class: "charts-grid", style: "grid-template-columns: 1fr; margin-bottom: 24px;" }, deptTable);

    // Top destinations
    const maxDestCount = Math.max(...dests.map(d => d.trip_count), 1);
    const topDest = el("div", { class: "chart-card" },
      el("div", { class: "chart-header" },
        el("div", { class: "chart-title" }, "热门目的地 TOP 5")),
      dests.length === 0
        ? el("p", { class: "muted", style: "padding:16px 0;" }, "暂无差旅数据")
        : el("div", { style: "display:flex;flex-direction:column;gap:16px;padding:8px 0;" },
            dests.map((d, i) =>
              el("div", { style: "display:flex;align-items:center;justify-content:space-between;" },
                el("div", { style: "display:flex;align-items:center;gap:12px;" },
                  el("span", { style: `font-size:16px;font-weight:700;color:${COLORS[i % COLORS.length]};width:24px;` }, String(i + 1)),
                  el("span", { style: "font-weight:600;color:#111827;" }, d.destination)),
                el("div", { style: "display:flex;align-items:center;gap:12px;" },
                  el("span", { style: "font-size:13px;color:#6b7280;" }, `${d.trip_count} 次`),
                  el("span", { class: "trend-bar", style: `width:${Math.round(d.trip_count / maxDestCount * 120)}px; background:${COLORS[i % COLORS.length]}` }))))));

    // Approval efficiency
    const approvalEff = el("div", { class: "chart-card" },
      el("div", { class: "chart-header" },
        el("div", { class: "chart-title" }, "审批效率")),
      el("div", { style: "display:flex;flex-direction:column;gap:20px;padding:12px 0;" },
        effRow("平均审批时长",
          eff.avg_decision_days != null ? `${eff.avg_decision_days} 天` : "暂无数据",
          eff.avg_decision_hours != null ? Math.min(eff.avg_decision_hours / 72 * 100, 100) : 0,
          "#10b981"),
        effRow("一次通过率", `${eff.first_pass_rate}%`, eff.first_pass_rate, "#4f46e5"),
        effRow("驳回率", `${eff.rejection_rate}%`, eff.rejection_rate, "#ef4444")));

    const bottomGrid = el("div", { class: "charts-grid" }, topDest, approvalEff);

    listContainer.append(summaryGrid, chartsGrid, deptGrid, bottomGrid);
  }

  function summaryCard(colorClass, label, value, change) {
    const icons = {
      blue: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2",
      green: "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
      amber: "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z M15 11a3 3 0 11-6 0 3 3 0 016 0z",
      purple: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z",
    };
    return el("div", { class: "summary-card" },
      el("div", { class: `summary-icon ${colorClass}` },
        el("svg", { width: "20", height: "20", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
          el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: icons[colorClass] }))),
      el("div", { class: "summary-label" }, label),
      el("div", { class: "summary-value" }, value),
      el("div", { class: "summary-change", style: "color:#6b7280;" }, change));
  }

  function effRow(label, value, pct, color) {
    return el("div", null,
      el("div", { style: "display:flex;justify-content:space-between;margin-bottom:8px;" },
        el("span", { style: "font-size:13px;color:#6b7280;" }, label),
        el("span", { style: "font-size:14px;font-weight:600;color:#111827;" }, value)),
      el("div", { style: "height:8px;background:#f3f4f6;border-radius:4px;overflow:hidden;" },
        el("div", { style: `width:${Math.min(pct, 100)}%;height:100%;background:${color};border-radius:4px;` })));
  }

  function changeText(_val, _label) {
    // Placeholder — could compute period-over-period change if we stored historical snapshots
    return "";
  }

  // Header
  const header = el("div", { class: "header" },
    el("h2", null, "报表分析"),
    el("div", { class: "header-actions" },
      el("button", { class: "btn btn-secondary", onclick: () => window.print() },
        el("svg", { width: "16", height: "16", fill: "none", stroke: "currentColor", viewBox: "0 0 24 24" },
          el("path", { "stroke-linecap": "round", "stroke-linejoin": "round", d: "M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" })),
        " 导出报表")));

  // Period tabs
  const periodTabBtns = PERIODS.map(p =>
    el("button", {
      class: `period-tab${p.key === activePeriod ? " active" : ""}`,
      onclick: () => {
        periodTabBtns.forEach(b => b.classList.remove("active"));
        periodTabBtns[PERIODS.indexOf(p)].classList.add("active");
        load(p.key);
      },
    }, p.label));
  const periodTabs = el("div", { class: "period-tabs" }, ...periodTabBtns);

  const listContainer = el("div");

  clear(root, header, periodTabs, listContainer);

  // Initial load
  load(activePeriod);
}
