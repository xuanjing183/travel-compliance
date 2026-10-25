// Policy Q&A (#18) and personal preferences (#24).

import { api } from "./api.js";
import { quoteBlock } from "./parts.js";
import { busy, button, clear, el, errorBox, generatorLabel, kv, notice, section, sourceBadge } from "./ui.js";

export async function renderAsk(root, ctx) {
  const { policies } = await api.get("/api/policies");
  const published = policies.find((p) => p.status === "published");
  const realMode = ctx?.health?.llm_mode === "real";
  const enhanced = el("input", { type: "checkbox", disabled: !realMode });
  const select = el("select", null,
    el("option", { value: "" }, published ? `当前发布版本（${published.version}）` : "当前发布版本"),
    policies.filter((p) => p.status !== "draft" && p !== published)
      .map((p) => el("option", { value: p.version }, `${p.version}（${p.status === "archived" ? "已归档" : p.status}）`)));
  const question = el("textarea", { rows: 2, maxlength: 500, placeholder: "例如：去上海出差住宿每晚最多能报多少？" });
  const out = el("div");
  const go = button("提问", async () => {
    const q = question.value.trim();
    if (!q) return;
    await busy(go, async () => {
      clear(out, el("p", { class: "muted" }, "检索中…"));
      try {
        const body = select.value ? { question: q, version: select.value } : { question: q };
        const endpoint = realMode && enhanced.checked ? "/api/policy/ask-agent" : "/api/policy/ask";
        clear(out, answerView(await api.post(endpoint, body)));
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  }, { kind: "primary" });
  question.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) go.click();
  });
  clear(root,
    el("div", { class: "page-head" }, el("h2", null, "制度问答")),
    section("提问",
      el("p", { class: "muted small" }, "回答只依据所选版本的制度原文；找不到依据时会明确回答“不知道”，不做猜测。引用原文与定位由代码给出。"),
      el("div", { class: "form-inline" }, el("label", null, "制度版本 ", select)),
      el("label", { class: "check" }, enhanced,
        realMode ? " 增强 Agent（查询改写 + 混合检索 + Rerank）" : " 增强 Agent（仅 real 模式可用；当前走原链路）"),
      question, el("div", { class: "actions" }, el("span", { class: "muted small" }, "Ctrl+Enter 提问"), go)),
    out);
}

function answerView(a) {
  const chat = a.grounded === false;
  const agent = a.agent;
  return section(`回答 · ${a.version}`,
    el("div", { class: `answer ${a.answerable ? "" : "answer-refused"}` },
      el("p", null, a.answer),
      el("p", { class: "muted small" }, sourceBadge(a.source), ` 生成方式：${generatorLabel(a.generator)}`,
        chat ? " · 闲聊（未检索制度）" : "",
        a.needs_clarification ? " · 需要补充信息" : "",
        a.confidence > 0 ? ` · 置信度 ${Math.round(a.confidence * 100)}%` : "")),
    a.citations.length ? [el("h4", null, "引用"), a.citations.map(quoteBlock)]
      : el("p", { class: "muted small" }, a.answerable || chat ? "" : "拒答时不展示引用。"),
    el("details", { class: "small" },
      el("summary", null, `检索明细（${a.retrieval.mode}）`),
      agent && agent.mode === "react"
        ? el("p", { class: "muted small" },
          `Agent 工具调用：${agent.tool_calls} 次`,
          agent.split && agent.paths?.length ? `；已拆分 ${agent.paths.length} 条检索路径：${agent.paths.join("；")}`
            : agent.queries?.length ? `；查询：${agent.queries.join("；")}` : "")
        : null,
      agent && agent.mode === "fallback"
        ? el("p", { class: "muted small" }, "增强 Agent 仅在 real 模式启用；本次走原链路。") : null,
      el("table", { class: "list" },
        el("thead", null, el("tr", null, ["排名", "条款", "定位", "得分"].map((h) => el("th", null, h)))),
        el("tbody", null, a.retrieval.chunks.map((c) => el("tr", null,
          el("td", null, String(c.rank)), el("td", null, c.clause), el("td", null, c.locator || "—"),
          el("td", null, String(c.score)))))),
      a.retrieval.supplements.length
        ? el("p", null, "补充条款（按规则依赖，不参与排序）：",
          a.retrieval.supplements.map((s) => `${s.clause} ${s.locator}（${s.reason}）`).join("；"))
        : null));
}

// ---- preferences ------------------------------------------------------------

export async function renderPrefs(root, ctx) {
  const me = ctx.user;
  const data = await api.get(`/api/users/${me.id}/preferences`);
  const p = data.preferences || {};
  const early = el("input", { type: "checkbox", checked: p.avoid_early_departure === true });
  const notes = el("input", { value: p.notes || "", maxlength: 200, placeholder: "例如：靠窗（只供参考，不参与排序）" });
  const out = el("div");
  const rerender = (flash) => renderPrefs(root, { ...ctx, flash });
  const save = button("保存", async () => {
    const body = { avoid_early_departure: early.checked };
    if (notes.value.trim()) body.notes = notes.value.trim();
    await busy(save, async () => {
      try {
        await api.put(`/api/users/${me.id}/preferences`, body);
        rerender("偏好已保存。只影响之后提交的申请，已提交的申请不变。");
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  }, { kind: "primary" });
  const reset = button("清空偏好", async () => {
    await busy(reset, async () => {
      try {
        await api.del(`/api/users/${me.id}/preferences`);
        rerender("偏好已清空。");
      } catch (e) {
        clear(out, errorBox(e));
      }
    });
  });
  clear(root,
    el("div", { class: "page-head" }, el("h2", null, "个人出行偏好")),
    ctx.flash ? notice("ok", ctx.flash) : null,
    section("偏好",
      el("p", { class: "muted small" }, "提交申请时会把当前偏好快照进申请；申请里单独填写的“本次偏好”优先。偏好只用于在合规候选之间排序，不会放宽制度。"),
      el("label", { class: "check" }, early, " 回避早班（当地时间 08:00 之前出发）"),
      el("label", { class: "field" }, el("span", { class: "field-label" }, "备注"), notes),
      el("div", { class: "actions" }, save, reset), out),
    section("当前保存的值", kv([
      ["回避早班", "avoid_early_departure" in p ? (p.avoid_early_departure ? "是" : "否") : "未设置"],
      ["备注", p.notes || "—"],
    ])));
}
