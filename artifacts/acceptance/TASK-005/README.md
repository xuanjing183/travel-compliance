# TASK-005 服务层证据（AC-008 / AC-009 / AC-010 / AC-011）

这些是**服务层**证据：直接调用 `Workflow.submit/decide/retry_delivery/recover`，与 TASK-006 的 HTTP 路由走同一组入口。四项 AC 目前都还是 `not_run`，因为各自还缺 HTTP/界面或真实进程证据，缺口见文末。

- 环境：Python 3.11.0，Fake 模式（`llm_mode=fake`），Demo Provider，临时数据目录。没有网络请求，也没有模型调用。
- 自动测试：`uv run pytest -v -p no:cacheprovider tests/test_workflow.py --junitxml=artifacts/acceptance/TASK-005/junit.xml`，结果 20 passed（见 `pytest-v.txt`、`junit.xml`）。全量 151 passed。
- 场景导出：`uv run python scripts/export_workflow_evidence.py artifacts/acceptance/TASK-005`。每个场景一个 JSON，内容是 SSE 事件类型、`status_changed` 状态时间线、数据库计数、Outbox 全文和审计事件类型；汇总见 `summary.json`。

| AC | 预期 | 服务层证据 |
|---|---|---|
| AC-008 审批卡 | 成本明细 476000、预算差 76000、规则/理由、引用、来源、revision 齐全 | `AC-008-approval-card.json` 包含以下内容：<br>• `revision=1`，`policy_version=v1.2`；<br>• 完整 `cost_breakdown`：`total_fen=476000`、`budget_delta_fen=76000`；<br>• 审批理由 `BUDGET_EXCEEDED`、`OVER_AUTO_APPROVAL_LIMIT`，后者 `basis` 引用 RULE-AUTO-001 / POL-6；<br>• `sources=[demo_fixture, model, policy]`。<br>测试：`test_s01_golden_path_pending_then_approved_and_delivered_once`。 |
| AC-009 权限、revision、幂等 | 张三与非直属经理 403；旧 revision 与不同 Key 409；同 Key 返回相同结果、无重复副作用 | `AC-009-decisions.json` 中各次请求的结果：<br>• 张三：403 `FORBIDDEN`；<br>• 赵六：403 `NOT_DIRECT_MANAGER`；<br>• revision=2：409 `STALE_REVISION`；<br>• 首次：200；<br>• 同 Key 重放：200（与首次相同）；<br>• 不同 Key：409 `ALREADY_DECIDED`。<br>之后数据库中 `decision_recorded`=1、`trip_delivery`=1。<br>测试：`test_ac009_*`（含 3 个并发决定只生效一次）、`test_self_approval_is_forbidden`。 |
| AC-010 重启恢复 | 待办不丢失、正确恢复、只交付一次 | `AC-010-restart-in-process.json`：在 `PENDING_APPROVAL` 时关闭 Workflow（关闭 aiosqlite 连接），用新实例重新打开并调用 `recover()`，再审批。审批经 checkpoint 恢复（`via=graph`），结果 `DELIVERED`，`trip_delivery`=1；同 Key 重放无副作用。<br>测试：`test_s10_restart_resumes_from_the_approval_interrupt`、`test_recover_aborts_runs_cut_off_mid_planning`、`test_startup_recovery_redelivers_pending_trips`。 |
| AC-011 交付与通知 | 邮件 kind 和数量符合场景；失败停在 DELIVERY_PENDING；重试不重复 | 各场景的邮件与状态：<br>• `S-01-approve.json`：`trip_delivery`=1，行程单=1；<br>• `S-07-reject.json`：`workflow_notification`=1，无交付；<br>• `S-08-request-changes.json`：`workflow_notification`=1，revision→2；<br>• `AC-011-delivery-failure-retry.json`：注入 Outbox 写失败后状态为 `DELIVERY_PENDING`，`delivery.error` 只含异常类型。管理员重试后变为 `DELIVERED`，`trip_delivery`=1；第二次重试返回 409 `INVALID_STATE`，没有生成第二封邮件。<br>测试：`test_s07_*`、`test_s08_*`、`test_ac011_*`。 |

说明：

- 审批卡的 `sources` 中出现 `model`，来自行程摘要段落 `plan_summary.summary`。在 Fake 模式下，该段落由确定性模板生成，`summary_generator="fake"`，并不是真实模型输出。区分方式是看 `generator` 字段（IMP-36），`source` 枚举保持封闭（IMP-02）。
- 交付失败通过 `Workflow.delivery_fault_hook` 测试钩子注入，没有 HTTP 入口（§4.7）。

仍缺（因此状态保持 `not_run`）：

- **AC-008**：浏览器截图（TASK-006/007）。
- **AC-009**：HTTP 层 API 测试（TASK-006）。
- **AC-010**：真实终止并重启进程（TASK-006）。当前只是同进程内重开，不等同于进程终止。
- **AC-011**：管理员重试走 HTTP 接口 #15，以及界面上的状态时间线（TASK-006/007）。
