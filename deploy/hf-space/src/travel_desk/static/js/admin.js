// Admin views: policy versions (#19–#23) and operations — faults (#27), provider health,
// outbox (#25) and demo reset (#26).

import { api, newIdempotencyKey } from "./api.js";
import { forgetRules, quoteBlock, userName } from "./parts.js";
import {
  busy, button, clear, el, errorBox, generatorLabel, jsonBlock, kv, localDateTime, notice, section,
} from "./ui.js";

const POLICY_STATUS = { draft: "草稿", published: "已发布", archived: "已归档" };
const RULE_STATUS = { candidate: "待审核", confirmed: "已确认", rejected: "已驳回", unsupported: "不支持" };

function policyBadge(status) {
  return el("span", { class: `status-badge ps-${status}` }, POLICY_STATUS[status] || status);
}

// ---- policy list + upload -------------------------------------------------------

export async function renderPolicies(root, ctx) {
  const { policies } = await api.get("/api/policies");
  const file = el("input", { type: "file", accept: ".md,.markdown,.pdf,text/markdown,application/pdf" });
  const version = el("input", { placeholder: "如 v1.3", pattern: "v\\d+\\.\\d+(-[a-z0-9]+)?" });
  const effective = el("input", { type: "date" });
  const out = el("div");
  const upload = button("上传为草稿", async () => {
    clear(out);
    if (!file.files.length || !version.value.trim() || !effective.value) {
      out.append(notice("error", "请选择文件并填写版本号与生效日期。"));
      return;
    }
    const form = new FormData();
    form.append("file", file.files[0]);
    form.append("version", version.value.trim());
    form.append("effective_date", effective.value);
    await busy(upload, async () => {
      try {
        const p = await api.upload("/api/policies", form);
        ctx.navigate(`#/admin/policies/${encodeURIComponent(p.version)}`);
      } catch (e) {
        out.append(errorBox(e));
      }
    });
  }, { kind: "primary" });

  clear(root,
    el("div", { class: "page-head" }, el("h2", null, "制度管理")),
    section("版本",
      el("table", { class: "list" },
        el("thead", null, el("tr", null, ["版本", "状态", "生效日期", "原文", "规则（确认/待审/驳回/不支持/无效引用）", "发布时间"].map((h) => el("th", null, h)))),
        el("tbody", null, policies.map((p) => {
          const c = p.rule_counts || {};
          return el("tr", { class: "row-link", onclick: () => ctx.navigate(`#/admin/policies/${encodeURIComponent(p.version)}`) },
            el("td", null, el("a", { href: `#/admin/policies/${encodeURIComponent(p.version)}` }, p.version)),
            el("td", null, policyBadge(p.status)),
            el("td", null, p.effective_date),
            el("td", null, p.document.kind, el("div", { class: "muted small mono" }, p.document.sha256.slice(0, 12))),
            el("td", null, `${c.confirmed ?? 0} / ${c.candidate ?? 0} / ${c.rejected ?? 0} / ${c.unsupported ?? 0} / ${c.invalid_quote ?? 0}`),
            el("td", { class: "muted small" }, localDateTime(p.published_at)));
        })))),
    section("上传新版本",
      el("p", { class: "muted small" }, "支持 Markdown（UTF-8）或文本型 PDF，≤10 MiB；扫描件 PDF 会被拒绝。上传后为草稿，需要抽取、逐条审核后才能发布。"),
      el("div", { class: "form" },
        el("label", { class: "field" }, el("span", { class: "field-label" }, "制度文件"), file),
        el("label", { class: "field" }, el("span", { class: "field-label" }, "版本号"), version),
        el("label", { class: "field" }, el("span", { class: "field-label" }, "生效日期"), effective)),
      el("div", { class: "actions" }, upload), out));
}

// ---- one version: extract, review, publish ------------------------------------

export async function renderPolicy(root, ctx, version) {
  const p = await api.get(`/api/policies/${encodeURIComponent(version)}`);
  const draft = p.status === "draft";
  const rerender = (flash) => renderPolicy(root, { ...ctx, flash }, version);
  const out = el("div");

  const overwrite = el("input", { type: "checkbox" });
  const extract = button(p.rules.length ? "重新抽取" : "抽取候选规则", async () => {
    clear(out);
    if (p.rules.length && !overwrite.checked) {
      out.append(notice("info", "已有候选规则。勾选“覆盖已有候选”才会重新调用抽取并替换。"));
      return;
    }
    await busy(extract, async () => {
      try {
        const res = await api.post(`/api/policies/${encodeURIComponent(version)}/extract`, { overwrite: overwrite.checked });
        rerender(`抽取完成（${generatorLabel(res.generator)}）：${res.candidates.length} 条候选${res.replaced && res.replaced.length ? `，替换 ${res.replaced.length} 条` : ""}。`);
      } catch (e) {
        out.append(errorBox(e));
      }
    });
  }, { kind: p.rules.length ? "secondary" : "primary" });

  let publishKey = null;
  const publish = button("发布此版本", async () => {
    clear(out);
    if (!window.confirm(`发布 ${version} 后，当前发布版本会被归档；已绑定旧版本的申请不变，会显示“制度已更新”提示。确定发布？`)) return;
    publishKey = publishKey || newIdempotencyKey("publish");
    await busy(publish, async () => {
      try {
        const res = await api.post(`/api/policies/${encodeURIComponent(version)}/publish`, undefined, publishKey);
        publishKey = null;
        forgetRules();
        rerender(`已发布 ${res.policy.version}${res.archived_version ? `，${res.archived_version} 已归档` : ""}；生效规则 ${res.confirmed_rule_ids.length} 条。`);
      } catch (e) {
        if (e.code !== "NETWORK_ERROR") publishKey = null;
        out.append(errorBox(e));
        if (e.code === "UNPROCESSED_CANDIDATES") out.append(notice("warn", "还有未处理的候选：", (e.details.rule_ids || []).join("、")));
        if (e.code === "MISSING_RULE_TYPES") out.append(notice("warn", "缺少必需的规则类型：", (e.details.missing_types || []).join("、")));
      }
    });
  }, { kind: "primary" });

  const counts = p.rule_counts || {};
  clear(root,
    el("div", { class: "page-head" }, el("a", { href: "#/admin/policies", class: "back" }, "← 返回版本列表")),
    el("div", { class: "trip-head" },
      el("div", null,
        el("h2", null, `制度 ${p.version} `, policyBadge(p.status)),
        el("p", { class: "muted" }, `生效 ${p.effective_date} · ${p.document.kind} · sha256 ${p.document.sha256.slice(0, 16)}… · 上传 ${localDateTime(p.created_at)}`,
          p.published_at ? ` · 发布 ${localDateTime(p.published_at)}` : ""))),
    ctx.flash ? notice("ok", ctx.flash) : null,
    draft ? section("操作",
      el("p", { class: "muted small" }, `确认 ${counts.confirmed ?? 0} · 待审核 ${counts.candidate ?? 0} · 驳回 ${counts.rejected ?? 0} · 不支持 ${counts.unsupported ?? 0} · 无效引用 ${counts.invalid_quote ?? 0}。所有候选处理完、必需规则类型齐全后才能发布。`),
      el("div", { class: "actions" },
        el("label", { class: "check" }, overwrite, " 覆盖已有候选"), extract, publish),
      out) : notice("info", "非草稿版本只读。"),
    section(`规则（${p.rules.length}）`,
      p.rules.length ? el("ul", { class: "rules" }, p.rules.map((r) => ruleCard(r, draft, version, rerender)))
        : el("p", { class: "muted" }, "还没有候选规则。")));
}

function ruleCard(r, draft, version, rerender) {
  const value = el("textarea", { rows: 4, class: "mono" }, JSON.stringify(r.value, null, 2));
  const condition = el("textarea", { rows: 2, class: "mono" }, JSON.stringify(r.condition, null, 2));
  const reason = el("input", { placeholder: "修改理由（修改值或条件时必填）", maxlength: 500 });
  const out = el("div");

  const send = (status) => async (ev) => {
    clear(out);
    const body = { status };
    let v;
    let c;
    try {
      v = JSON.parse(value.value);
      c = JSON.parse(condition.value);
    } catch {
      out.append(notice("error", "值或条件不是合法 JSON。"));
      return;
    }
    if (JSON.stringify(v) !== JSON.stringify(r.value)) body.value = v;
    if (JSON.stringify(c) !== JSON.stringify(r.condition)) body.condition = c;
    if (reason.value.trim()) body.modification_reason = reason.value.trim();
    await busy(ev.currentTarget, async () => {
      try {
        const updated = await api.patch(`/api/policies/${encodeURIComponent(version)}/rules/${encodeURIComponent(r.id)}`, body);
        rerender(`${updated.id} → ${RULE_STATUS[updated.status] || updated.status}`);
      } catch (e) {
        out.append(errorBox(e));
      }
    });
  };

  return el("li", { class: `rule-item rule-${r.status}` },
    el("div", { class: "rule-head" },
      el("span", { class: "rule-code" }, r.id), el("code", null, r.type),
      el("span", { class: `tag tag-${r.status}` }, RULE_STATUS[r.status] || r.status),
      r.supported ? null : el("span", { class: "tag tag-warn" }, "类型不在支持范围"),
      r.quote_status === "invalid_quote" ? el("span", { class: "tag tag-error" }, "引用无效") : null),
    quoteBlock({ version: r.version, clause: r.clause, quote: r.quote, locator: r.locator, source: r.source }),
    r.problems && r.problems.length
      ? el("ul", { class: "problems" }, r.problems.map((x) => el("li", null, el("code", null, x.code), ` ${x.message}`))) : null,
    draft
      ? el("div", { class: "rule-edit" },
        el("label", { class: "field" }, el("span", { class: "field-label" }, `值（unit：${r.unit}）`), value),
        el("label", { class: "field" }, el("span", { class: "field-label" }, "条件"), condition),
        reason,
        el("div", { class: "actions" },
          button("确认", send("confirmed"), { kind: "primary" }),
          button("驳回", send("rejected"), { kind: "danger" }),
          button("标为不支持", send("unsupported"))),
        out)
      : kv([["值", jsonBlock(r.value)], ["条件", jsonBlock(r.condition)], ["单位", r.unit]]),
    el("p", { class: "muted small" },
      r.confirmed_by ? `确认人 ${userName(r.confirmed_by)}` : "", r.modification_reason ? ` · 修改理由：${r.modification_reason}` : "",
      ` · 更新 ${localDateTime(r.updated_at)}`));
}

// ---- operations ----------------------------------------------------------------

const FAULTS = {
  transport_timeout: "交通 Provider 超时（航班/火车查询失败 → 待人工补录）",
  no_protocol_hotel: "无协议酒店（只能用备用目录）",
  weather_unavailable: "天气不可用（方案省略天气）",
};

export async function renderOps(root, ctx, params) {
  const kind = params.get("kind") || "";
  const [faults, health, outbox] = await Promise.all([
    api.get("/api/demo/faults"),
    api.get("/api/health/providers"),
    api.get(`/api/outbox${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),
  ]);
  const rerender = (flash) => renderOps(root, { ...ctx, flash }, params);
  const faultOut = el("div");

  const faultRows = Object.entries(FAULTS).map(([name, label]) => {
    const box = el("input", { type: "checkbox", checked: Boolean(faults.faults[name]) });
    box.addEventListener("change", async () => {
      box.disabled = true;
      try {
        await api.post("/api/demo/faults", { fault: name, enabled: box.checked });
        rerender(`故障 ${name} 已${box.checked ? "开启" : "关闭"}。只影响之后的提交。`);
      } catch (e) {
        box.checked = !box.checked;
        box.disabled = false;
        clear(faultOut, errorBox(e));
      }
    });
    return el("label", { class: "check" }, box, " ", label, " ", el("code", { class: "small" }, name));
  });

  const confirmInput = el("input", { placeholder: "输入 RESET_DEMO_DATA" });
  const resetOut = el("div");
  const reset = button("重置 Demo 数据", async () => {
    clear(resetOut);
    await busy(reset, async () => {
      try {
        await api.post("/api/demo/reset", { confirmation: confirmInput.value.trim() });
        forgetRules();
        for (const k of Object.keys(sessionStorage)) if (k.startsWith("tcd.")) sessionStorage.removeItem(k);
        rerender("Demo 数据已重置：申请、审批、审计、Outbox、故障设置、偏好和上传的制度已清空，种子数据已恢复。");
      } catch (e) {
        resetOut.append(errorBox(e));
      }
    });
  }, { kind: "danger" });

  clear(root,
    el("div", { class: "page-head" }, el("h2", null, "运维")),
    ctx.flash ? notice("ok", ctx.flash) : null,
    el("div", { class: "grid-2" },
      section("故障注入", el("p", { class: "muted small" }, "用于演示降级。交付写失败只能由自动化测试注入，不在此面板。"), faultRows, faultOut),
      section("运行状态",
        kv([
          ["模型模式", `${health.llm.mode}（文本 ${health.llm.text_model}，视觉 ${health.llm.vision_model}）`],
          ["API Key", health.llm.api_key_configured === null ? "未检查（非真实模式）" : health.llm.api_key_configured ? "已配置" : "未配置"],
        ]),
        el("table", { class: "list" },
          el("thead", null, el("tr", null, ["Provider", "实现", "模式", "状态", "故障开关", "最近错误"].map((h) => el("th", null, h)))),
          el("tbody", null, health.providers.map((p) => el("tr", null,
            el("td", null, p.name), el("td", null, p.implementation), el("td", null, p.mode),
            el("td", null, el("span", { class: `tag tag-${p.status === "ok" ? "ok" : "warn"}` }, p.status)),
            el("td", null, p.fault ? `${p.fault}${faults.faults[p.fault] ? "（开）" : ""}` : "—"),
            el("td", { class: "small" }, p.last_error || "—"))))))),
    section("Outbox（本地邮件，不外发）",
      el("div", { class: "actions" }, ["", "trip_delivery", "workflow_notification"].map((k) =>
        el("a", { class: `tab ${k === kind ? "active" : ""}`, href: `#/admin/ops${k ? `?kind=${k}` : ""}` },
          { "": "全部", trip_delivery: "交付", workflow_notification: "流程通知" }[k]))),
      outbox.emails.length
        ? el("ul", { class: "outbox" }, outbox.emails.map((m) => el("li", null,
          el("details", null,
            el("summary", null, el("strong", null, m.subject), el("span", { class: "muted small" },
              ` · ${m.kind} · 收件 ${m.to.map(userName).join("、")}${m.cc.length ? ` · 抄送 ${m.cc.map(userName).join("、")}` : ""} · ${localDateTime(m.created_at)}`)),
            el("p", null, el("a", { href: `#/trips/${m.trip_id}` }, "查看申请"), el("span", { class: "muted small mono" }, ` ${m.unique_key}`)),
            el("pre", { class: "mail-body" }, m.body)))))
        : el("p", { class: "muted" }, "没有邮件。")),
    section("重置 Demo 数据",
      el("p", { class: "muted small" }, "恢复种子用户、制度 v1.0/v1.2、酒店与 Provider fixture；删除全部申请、审批、审计、Outbox、故障设置、偏好和上传文件。不可撤销。"),
      el("div", { class: "actions" }, confirmInput, reset), resetOut));
}
