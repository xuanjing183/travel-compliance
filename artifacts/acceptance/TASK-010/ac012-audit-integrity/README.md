# TASK-010 证据：AC-012 审计完整性与不可变性（REQ-020 / HC-11）

**结论：S-01、S-08、S-09、S-12 四个场景的完整审计时间线覆盖 REQ-020 列举的全部类别（槽位、引用、规则、Provider、方案、审批、通知/交付、错误）；既有审计事件不可编辑——对运行中数据库直接执行 SQL `UPDATE`/`DELETE` 被追加式触发器拒绝，HTTP 层审计接口只读（仅 `GET`），唯一被授权的删除路径是 Demo reset（见 ../ac016-demo-reset/）。21/21 项检查通过。**

本目录不含密钥；所有 JSON 已核对不包含凭证值（HC-07）。

## 契约要求

- **REQ-020 (MUST)**：审计时间线追加记录槽位变化、检索条款、规则结果、Provider 调用摘要与来源、方案版本、审批决定、交付/通知和错误；除 Demo reset 外不允许修改既有事件。
- **AC-012**：执行 S-01、S-08、S-09、S-12；审计包含槽位、引用、规则、Provider、方案、审批、通知/交付、错误；既有事件不可编辑。证据：事件类型自动断言、审计导出。
- **HC-11**：审计事件追加写；除管理员明确执行 Demo reset 外不得修改或删除既有事件。

## 方法

`scripts/ac012_audit.py` 用文档化启动命令 `python -m travel_desk`（等价 `uv run travel-desk`）在临时数据目录、`fake` 模式下启动真实服务器子进程（移除 `DASHSCOPE_API_KEY`，无外部网络），每个场景前先 Demo reset 以隔离，然后：

1. 复用 `scripts/e2e_scenarios.py` 的 S-01/S-08/S-09/S-12 场景函数驱动真实 HTTP/SSE 流程，并断言各场景自身的预期（确保审计来自一次合法运行）。
2. 通过 `GET /api/trips/{id}/audit` **导出每个 Trip 的完整审计时间线**到 `audit-S-01.json` … `audit-S-12.json`（契约要求的“审计导出”）。
3. 取四个场景事件类型的并集，逐类别断言 REQ-020 覆盖矩阵。
4. **对运行中的数据库直接尝试改写审计**，并检查 HTTP 路由表，证明不可变性。

复现：

```bash
env -u DASHSCOPE_API_KEY uv run --offline python scripts/ac012_audit.py \
  artifacts/acceptance/TASK-010/ac012-audit-integrity
# → AC-012 PASSED（21/21）
```

自动化回归：`tests/test_api.py::test_ac012_audit_timeline_covers_req020_categories`（同一覆盖矩阵 + 注入故障后 `provider_call status=timeout` 被审计）；不可变性由 `tests/test_bootstrap.py::test_audit_and_outbox_are_append_only_outside_reset` 持续保护。

## REQ-020 覆盖矩阵（四场景事件类型并集）

并集：`approval_requested, approval_superseded, candidates_selected, decision_recorded, delivery_written, notification_written, plan_created, policy_bound, preference_snapshot, provider_call, revalidated, rules_evaluated, slots_changed, status_changed, submitted, trip_created`

| REQ-020 类别 | 命中的审计事件类型 | 主要来源场景 |
|---|---|---|
| 槽位变化 | `slots_changed`、`trip_created`（另含 `message_processed`/`image_confirmed` 见 S-05/S-11） | S-01/S-08 |
| 检索条款与引用 | `policy_bound`、`rules_evaluated`（payload 带 clause/quote/locator） | S-01/S-12 |
| 规则结果 | `rules_evaluated`、`revalidated`（`validation_blocked`/`blocked_cleared` 见 S-06） | S-01/S-12 |
| Provider 调用摘要与来源 | `provider_call`（含 status 与来源）、`candidates_selected` | S-01/S-09 |
| 方案版本 | `plan_created`（含 plan 快照与 cost_breakdown） | S-01/S-08/S-12 |
| 审批决定 | `approval_requested`、`decision_recorded`、`approval_superseded`（`auto_approved` 见 S-02） | S-01/S-08/S-12 |
| 通知与交付 | `notification_written`、`delivery_written`（`delivery_failed` 见交付重试） | S-01/S-08 |
| 错误 | S-09 记录的 Provider 故障 `provider_call status=timeout`（去程+返程两条） | S-09 |

各场景事件数与状态轨迹：S-01 = 21 条（→DELIVERED）；S-08 = 40 条（要求修改→重提交→批准→DELIVERED，含 `approval_superseded`）；S-09 = 16 条（停在 PENDING_APPROVAL，交通超时）；S-12 = 24 条（v1.3 发布后重新校验，含 `revalidated` 与两次 `rules_evaluated`）。

### 关于“错误”类别

在 `fake` 模式下，S-01/S-08/S-09/S-12 不会自然触发独立的 `error` 事件（模型/摘要/问答均为确定性替身，不失败）。AC-012 的“错误”由两层证据共同覆盖，未被弱化：

- **场景内**：S-09 注入交通 Provider 超时，审计如实记录 `provider_call status=timeout`（去程与返程各一条）及由此产生的方案降级（`PLAN_INCOMPLETE`）——这是运行中对故障/错误的审计记录。
- **独立事件类型**：专用 `error` 审计事件由 `tests/test_api.py::test_message_model_failure_ends_the_stream_with_one_error_and_no_write` 证明（模型不可用时记 `error`，`stage=slot_extraction`，且不写入槽位）；`delivery_failed` 由 `tests/test_workflow.py` 的交付重试用例证明（交付写失败时记 `delivery_failed`，停在 `DELIVERY_PENDING`，管理员重试后 `DELIVERED`）。

## 不可变性证明（对运行中的真实数据库）

| 证明项 | 结果 |
|---|---|
| 追加式触发器存在 | `audit_events_no_update`、`audit_events_no_delete` |
| 直接 SQL `UPDATE audit_events SET actor='tampered'` | 被拒：`audit_events is append-only` |
| 直接 SQL `DELETE FROM audit_events`（非重置路径） | 被拒：`audit_events can only be cleared by demo reset` |
| 被探测事件在尝试后 | 仍原样存在，`actor` 仍为 `u-wangwu`（未被篡改） |
| HTTP 审计路由 | 仅 `GET /api/trips/{trip_id}/audit`；无任何 PUT/PATCH/DELETE/POST 改写审计的路由 |

触发器定义（`src/travel_desk/db/schema.py`）：`BEFORE UPDATE` 恒 `RAISE(ABORT)`；`BEFORE DELETE` 仅当 `meta.reset_in_progress='1'` 才放行。重置在单事务内置该标志、删除动态表、最后删 `meta` 清标志，因此“删除既有审计”只能由 Demo reset 触发——该唯一例外已在 AC-016 证据中验证（重置后审计只剩 1 条 `demo_reset`）。

## 文件

| 文件 | 内容 |
|---|---|
| `AC-012-audit.json` | 21 项检查全通过；覆盖矩阵、四场景事件类型/轨迹、不可变性证明明细 |
| `audit-S-01.json` / `audit-S-08.json` / `audit-S-09.json` / `audit-S-12.json` | 各场景完整审计时间线导出（逐事件 type/actor/payload/refs/trace_id） |
| `boot-1.log` | 服务器启动日志 |
