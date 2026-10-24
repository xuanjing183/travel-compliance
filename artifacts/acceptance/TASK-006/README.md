# TASK-006 验收证据：HTTP API、SSE、权限层、上传与真实模型路径

依据 `docs/contract-v1.1.md` §8（AC-002、AC-003、AC-009、AC-010、AC-011）。除 `real-smoke.json` 外，所有证据均在**确定性验收模式**下生成（D-08、§8 开头）：

- 槽位抽取使用 `fake`，即确定性解析器，它是测试替身而不是模型，输出中标为 `generator="fake"`。
- 图片抽取读录制输出 `tests/fixtures/recorded/invitations.json`。该条目由 TASK-001 的真实视觉模型调用冻结而来，标注 `source=model`，输出中为 `generator="recorded:model"`。
- 全程不访问网络、不调用模型，子进程环境中移除了 `DASHSCOPE_API_KEY`。

接口里的 `source: "model"` 是 api.md 规定的内容类别标记，区分 fake、录制和真实模型要看 `generator`。

## 复现

```bash
uv run --offline pytest tests/test_api.py tests/test_llm.py -v          # 48 passed → pytest-v.txt / junit.xml
uv run --offline python scripts/export_api_evidence.py artifacts/acceptance/TASK-006   # 28/28 PASS
uv run --offline python scripts/ac010_restart.py artifacts/acceptance/TASK-006/AC-010  # 14/14 PASS
```

`export_api_evidence.py` 和 `ac010_restart.py` 每次都使用临时数据目录，可重复运行。任何一项检查失败，它们都以非零码退出。每项检查的观测值见 `api-evidence-summary.json` 与 `AC-010/AC-010-restart.json`。

## AC-002 槽位接入：S-05、补齐并修改预算

证据：`AC-002/turn-{1,2,3}.sse.txt`（原始 SSE 响应体与 X-Trace-Id）、`turns.json`、`audit.json`、`trip-after.json`；自动测试为 `test_ac002_s05_asks_only_for_missing_fields_then_updates_without_repeating`。

| 轮次 | 输入 | 结果 |
|---|---|---|
| 1 | “下周去杭州” | destination=杭州；“下周”不被猜成具体日期；同一次回复只追问 origin、start_date、end_date、purpose、declared_budget_fen，不再问目的城市 |
| 2 | “从北京出发，10月28日到10月30日，参加供应商大会，预算四千元” | 槽位齐全，回复不再追问，并提示核对后提交（D-13） |
| 3 | “预算改成五千” | `changes=[declared_budget_fen 400000→500000]`；审计 `slots_changed` 记录 before/after，origin=message；不重复追问 |

三轮 `message_processed` 审计中记录的追问依次为“5 项”“无”“无”。

## AC-003 图片入口：仅 API 部分

证据：`AC-003/trip-before.json`、`extraction.json`、`trip-after-extraction.json`、`trip-after-confirm.json`、`audit.json`、`upload-errors.json`；另有自动测试 `test_ac003_*`、`test_upload_type_and_size_are_checked_by_content`。

- 识别结果是 `pending` 提议。识别后 Trip 的槽位与 revision 都不变，审计中只有 `trip_created` 和 `image_extracted`。
- 不修改直接确认后，字段符合 S-11 判定口径：上海、2026-10-28～10-30、事由含“供应商大会”、地址去空白后同时含“滨江大道2727号”和“星衡国际会议中心”。审计依次为 `slots_changed`（origin=image）和 `image_confirmed`。
- 错误类型（PDF 字节，文件名写作 .png）返回 415 `UNSUPPORTED_MEDIA_TYPE`；超过 10 MiB 返回 413 `PAYLOAD_TOO_LARGE`。
- **尚缺：** AC-003 要求的截图，需要图片入口界面（TASK-007/009），所以本项仍为 not_run。

## AC-009 权限、revision 与幂等（HTTP）

证据：`AC-009/decisions-http.json`，其中包含每次请求的状态码、错误码，以及合法决定前后的数据库计数；自动测试为 `test_s01_over_http_*`、`test_ac009_decision_errors_follow_the_documented_order`。

| 尝试 | 结果 |
|---|---|
| 张三自批 | 403 `FORBIDDEN` |
| 非直属经理赵六 | 403 `NOT_DIRECT_MANAGER` |
| 旧 revision | 409 `STALE_REVISION` |
| 首次批准 | 200，DELIVERED |
| 同 Key 重放 | 响应完全相同 |
| 不同 Key | 409 `ALREADY_DECIDED` |

数据库计数：被拒的尝试没有产生副作用（`decided_approvals=0`、`outbox_trip_delivery=0`）。最终 `decided_approvals=1`、`decision_recorded_events=1`、`outbox_trip_delivery=1`、`itineraries=1`。

## AC-010 重启恢复：真实终止进程

证据：`AC-010/AC-010-restart.json`（14 项检查及其观测值）和 `boot-1/2/3.log`。

`scripts/ac010_restart.py` 的过程：

1. 以子进程 `python -m travel_desk` 启动真实服务（临时数据目录、空闲端口）。
2. 通过 HTTP/SSE 让 S-01 进入 `PENDING_APPROVAL`。
3. 用 `taskkill /F /T` 强制终止，并确认端口已关闭。
4. 以只读方式查询数据库：Trip、待审批、checkpoint 行、Outbox 计数。
5. 用同一数据库重启。日志中有 `startup recovery`；Trip、revision 和李四的待办都没有丢失。
6. 李四批准后经 checkpoint 中断恢复（审计 `via=graph`），状态为 DELIVERED；同 Key 重放返回已存的响应。
7. 再次终止并第三次启动，仍然只有 1 封 `trip_delivery` 和 1 份行程单。

## AC-011 交付与通知（HTTP）

证据：`AC-011/scenarios-http.json`，含每个场景的 Outbox 和状态时间线（来自审计 `status_changed`）。

- 邮件 kind 与数量：S-01 为 1 封 `trip_delivery`；S-07 驳回、S-08 退回修改各为 1 封 `workflow_notification`，且没有交付邮件。
- 交付写入失败：用测试钩子 `delivery_fault_hook`（§4.7）模拟 Outbox 写入失败。按 REQ-022，此类故障只能通过测试钩子注入，不进入管理员故障面板。批准后状态停在 `DELIVERY_PENDING`。
- 管理员通过 `POST /api/trips/{id}/delivery/retry` 重试后变为 DELIVERED；再次重试返回 409，`trip_delivery` 仍为 1 封。
- 服务层证据见 `../TASK-005/AC-011-delivery-failure-retry.json`。

## 真实模型冒烟（AUTH-01，计费）

证据：`real-smoke.json`；费用见 `artifacts/cost-ledger.md`。

`scripts/real_smoke.py` 在 `llm_mode=real` 下把各模型路径各跑一次：

- 创建时带文本、对话轮次：槽位抽取，模型 qwen3.8-max。
- 邀请函识别：视觉模型 qwen3.5-plus。
- 提交 S-01：行程摘要，模型 qwen3.8-max，status=ok。

共 4 次调用，估算 0.1294 元；累计 0.5413 / 50 元。本项只证明接入可用，**不作为任何授权门控验收或质量指标的证据**；那些在 TASK-011 中完成。观察记录：真实模型会把文本中的“出差”直接当作 purpose，这一点留给 TASK-011 评测。
