# TASK-007 证据：三角色工作台与浏览器黄金路径

截图由实施负责人在本机浏览器中操作工作台后截取（AC-026 两段式演练的第一段之外的 TASK-007 验收；项目所有者的最终演练在 TASK-012）。除特别注明“real”外，服务都以 `TRAVEL_DESK_LLM_MODE=fake` 运行，使用临时数据目录，不访问外部网络（HC-06）；fake 模式的摘要与问答标有“Fake（确定性测试替身，非模型）”或 `fake:extractive`，**不能作为模型质量证据**（D-08）。

## 截图

| 文件 | 内容 | 对应 |
|---|---|---|
| `01-employee-pending-approval.png` | 张三提交 S-01 后的详情页：方案、成本、审批理由、等待李四审批 | AC-014 黄金路径 |
| `02-AC-014-employee-admin-ops-403.png` | 张三打开 `#/admin/ops`：服务端返回 403 `FORBIDDEN`（`required_role=admin`，带 trace_id）并在界面显示。截于页面角色守卫（IMP-87）之前，现在该页面先由界面守卫拦截（见 17） | AC-014 服务端 403 |
| `03-admin-v1.3-candidates.png` | 王五上传 v1.3 后的候选规则审核页 | AC-014 制度发布流程 |
| `04-admin-invalid-quote-refused.png` | 确认原文不匹配的候选被拒（422 `QUOTE_INVALID`） | AC-014 / AC-005 |
| `05-admin-v1.3-published.png` | v1.3 发布成功，v1.2 归档 | AC-014 制度发布流程 |
| `06-manager-policy-change-notice.png` | 李四的审批页提示“制度已更新”（审批卡按 v1.2 生成，当前 v1.3，IMP-88） | AC-024 界面 |
| `07-revalidated-v1.3-hotel-over-cap.png` | 按 v1.3 重新校验后：revision 2、新增 `HOTEL_OVER_CAP`、旧卡失效 | AC-024 界面 |
| `08-AC-008-manager-card-S01-rev1.png` | **AC-008 主证据**：S-01 revision 1 审批卡，合计 4,760.00 元（476000 分）、超出预算 760.00 元（76000 分）、`BUDGET_EXCEEDED` + `OVER_AUTO_APPROVAL_LIMIT`、依据 RULE-AUTO-001 v1.2 POL-6 原文与定位、来源 demo_fixture/model/policy、revision 1 | AC-008 |
| `08-AC-008-manager-card-rev3.png` | 较早一次演练：要求修改后 revision 3 的审批卡（旧版导航布局，保留作为过程记录） | AC-009 界面 |
| `09-admin-ops-outbox.png` | 王五的运维页：Provider 健康与 Outbox（本地邮件，不外发）：revision 1、3 的交付邮件（`trip_delivery`）和 revision 2 “要求修改”的流程通知（`workflow_notification`，正文注明不是出行确认） | AC-011 界面 |
| `10-employee-policy-ask.png` | 张三的制度问答（fake:extractive）：回答、policy 引用与定位 | AC-015（fake） |
| `11-AC-015-itinerary-rev3-sources.png` | 交付后行程单：每个条目的来源徽标，员工自填条目显示“申请人填写”（IMP-26）。截于 IMP-90 之前，页面上的酒店说明仍是旧文字“45000 分/晚”，现已改为元 | AC-015 / AC-011 |
| `12-AC-015-S03-backup-hotel-sources.png` | S-03 详情：成都无合规协议酒店，选用 `backup_fixture` 备用酒店，违规与引用为 policy | AC-015 |
| `13-AC-014-manager-admin-page-guard.png` | 李四打开管理员页面：界面守卫显示“无权访问”与允许的角色（IMP-87） | AC-014 |
| `14-AC-015-audit-source-badges-S01.png` | S-01 审计时间线：`rules_evaluated` 标 policy，`approval_requested` 标 demo_fixture/model/policy（IMP-91） | AC-015 审计 |
| `15-AC-015-real-mode-S01-plan-sources.png` | **real**：S-01 经真实模型抽取槽位（qwen3.8-max）后提交，方案中住宿/天气/日程的来源徽标，行程摘要标 model、生成方式“模型 qwen3.8-max” | AC-015 真实模式界面 |
| `16-AC-015-real-mode-policy-answer.png` | **real**：“上海住宿标准是多少”，回答标 model（qwen3.8-max），3 条引用均标 policy 并给出 v1.2 定位（POL-2 两条、关联条款 POL-1） | AC-015 真实模式界面 |
| `17-AC-014-employee-admin-page-guard.png` | 张三打开 `#/admin/ops`：界面守卫拦截（对照 02 的服务端 403） | AC-014 |
| `18-AC-003-image-entry.png` | 张三在 COLLECTING Trip 详情页“从邀请函识别”：上传邀请函 fixture 后点“识别邀请函”，显示“识别结果（model 录制的模型输出）”、可修改字段（目的城市/出发日期/返回日期/出差事由/会议地址）与“确认写入申请/放弃”；确认前不修改申请（fake 录制输出，非真实模型，D-08）。补拍于 TASK-010 的浏览器会话 | AC-003 |

## 服务端检查

- `server-checks.txt`：对运行中的工作台逐项调用：员工/经理/管理员越权接口均为 403 `FORBIDDEN`（带 `required_role`）、旧 revision 决定 409 `STALE_REVISION`、缺身份头 401、静态资源类型正确。
- `tests/test_workbench.py::test_page_role_guards_match_the_server`：解析 `app.js` 的路由表，对 4 个演示用户 × 6 个受限页面实际调用的接口逐一探测，断言“界面允许”与“服务端不返回 403”完全一致（IMP-87）。

## AC-015 JSON 扫描

- 自动测试 `tests/test_sources.py`（fake 模式）：S-01（批准 → DELIVERED）、S-03（备用酒店，待审批）与一题问答；扫描待审批列表、Trip、行程单、审计、回答中的每一个 `source`/`sources`：值必须在 §6.1 枚举内，`null` 只允许出现在员工自填的行程条目，带价格的候选、天气、原文引用、违规、摘要缺少来源即失败。扫描器自检用 8 个构造错误证明它会失败。
- 真实模式 `AC-015-real/`（`scripts/source_scan_real.py`，IMP-92）：同一扫描器，`llm_mode=real`；摘要（qwen3.8-max）与问答（qwen3.7-text-embedding 向量检索 + qwen3.8-max）来自 DashScope。`summary.json` 中 8 份文档 `problems` 全为空，`result=pass`；S-01 DELIVERED、S-03 酒店来源 backup_fixture、回答 source=model 且引用全为 policy。完整 API/审计 JSON 同目录保存（不含密钥，已检查）。

## 真实模型费用（AUTH-01）

| 运行 | 调用 | 估算费用 |
|---|---|---|
| `scripts/source_scan_real.py` | 摘要 2、制度嵌入 2、问题嵌入 1、回答 1 | 0.0853 元 |
| 浏览器 real 演练（截图 15、16） | 槽位抽取 1、摘要 1、制度嵌入 2、问题嵌入 1、回答 1 | 0.1000 元 |

逐次记录见 [`artifacts/llm-usage.jsonl`](../../llm-usage.jsonl)，汇总见 [`artifacts/cost-ledger.md`](../../cost-ledger.md)：累计估算 0.9581 元 / 上限 50 元。

## 自动检查结果

```text
env -u DASHSCOPE_API_KEY uv run --offline pytest -q -p no:cacheprovider   → 266 passed
uvx ruff@0.14 check src tests scripts                                      → All checks passed
node --check（Qoder 内置 Node，--input-type=module）8 个 js 模块           → 全部通过
```

## 未覆盖 / 限制

- AC-003 图片入口：界面截图已补（截图 18，补拍于 TASK-010 的浏览器会话）；确认行为由 TASK-006 API 测试覆盖。
- AC-013 降级：界面“待人工补录/已省略”截图已在 TASK-010 完成（`../TASK-010/ac013-fault-injection/ui-01..03-*.png`）。
- 截图 08-rev3 与 11 截于后续界面修改之前（导航布局、IMP-90 酒店说明文字），内容的数值与来源仍然正确。
- 浏览器演练中两次遇到浏览器缓存旧模块，已由 IMP-94（`Cache-Control: no-cache`）修复并加自动测试。
