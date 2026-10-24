# TASK-010 证据：AC-016 一键 Demo 重置（REQ-025 / HC-11 例外）

**结论：管理员一键重置先经过授权与确认两道门（非 admin 403、确认串错误 422），正确执行后清空全部动态数据（Trip、审批、Outbox、幂等键、行程单、上传、图片抽取、偏好、故障开关、checkpoint 线程），恢复种子（4 名员工、政策 v1.0 archived + v1.2 published、各版本规则、Provider 复位），并按 REQ-025/HC-11 的唯一例外把既有审计事件清除、只追加 1 条 `demo_reset` 事件。30/30 项检查通过。**

本目录不含密钥；`AC-016-reset.json` 已核对不包含任何凭证值（HC-07）。

## 契约要求

- **REQ-025 (MUST)**：管理员可一键重置 Demo 数据；重置恢复种子用户、v1.0/v1.2、酒店和 Provider fixture，删除 Trip、审批、审计、Outbox、故障设置和偏好。
- **AC-016**：先制造 Trip、审批、偏好、故障和 Outbox，再重置；动态数据清空，种子用户、政策、酒店和 fixture 恢复；非 admin 为 403。证据：DB 前后快照、API 测试。
- **HC-11**：审计事件追加写；除管理员明确执行 Demo reset 外不得修改或删除既有事件（重置是唯一被授权的删除路径）。
- 接口：`POST /api/demo/reset`，`confirmation="RESET_DEMO_DATA"`，admin only（docs/api.md #26）。

## 方法

`scripts/ac016_reset.py` 用文档化的启动命令 `python -m travel_desk`（等价 `uv run travel-desk`）在临时数据目录、`TRAVEL_DESK_LLM_MODE=fake` 下启动真实服务器子进程，并从子进程环境移除 `DASHSCOPE_API_KEY`（无模型调用、无外部网络）。随后：

1. **制造每一类动态数据**：
   - Trip A（S-01）提交 → `PENDING_APPROVAL` → 李四批准 → `DELIVERED`（产生审批卡、行程单、`trip_delivery` Outbox 邮件、幂等键）。
   - Trip B（S-01）提交 → 停在 `PENDING_APPROVAL`（保留一个活动 checkpoint 线程与待办审批卡）。
   - 张三写入偏好 `avoid_early_departure=True, notes="靠窗"`。
   - 王五注入故障 `transport_timeout=True`（Provider 健康显示 `fault_injected`）。
2. **直接只读快照两个数据库**（`app.sqlite` 与 `checkpoints.sqlite`，不经服务层）：各表行数、员工与偏好、政策版本与规则数、故障开关、审计事件类型序列、checkpoint 线程列表。
3. **验证授权与确认门**：员工/经理重置 → 403；缺确认串 / 错误确认串 → 422 `CONFIRMATION_REQUIRED`；并确认这些被拒尝试**没有副作用**（Trip 仍为 2）。
4. **管理员正确重置** → 200 `{"status":"reset"}`，再次只读快照，逐项断言。
5. **API 层复核**：Trip 列表空、详情 404、审批待办空、Outbox 空；管理员只读接口不新增审计。

复现：

```bash
env -u DASHSCOPE_API_KEY uv run --offline python scripts/ac016_reset.py \
  artifacts/acceptance/TASK-010/ac016-demo-reset
# → AC-016 PASSED（30/30）
```

自动化回归：`tests/test_api.py::test_ac016_demo_reset_clears_all_dynamic_data_and_restores_seed`（同一矩阵，经 TestClient 与只读 DB 快照）。

## DB 前后快照（关键数字）

| 项目 | 重置前 | 重置后 | 判定 |
|---|---|---|---|
| trips | 2 | 0 | 清空 |
| approvals | 2 | 0 | 清空 |
| outbox_emails | 1 | 0 | 清空 |
| idempotency_keys | 3 | 0 | 清空 |
| itineraries | 1 | 0 | 清空 |
| uploads / image_extractions | 0 / 0 | 0 / 0 | 清空 |
| audit_events | 39 | 1（仅 `demo_reset`） | 按 REQ-025 清除既有事件，追加 1 条重置事件 |
| checkpoint 线程 | 2 | 0 | 全部删除 |
| faults `transport_timeout` | True | False | 故障复位 |
| 有偏好的员工 | `["u-zhangsan"]` | `[]` | 偏好清空 |
| employees | 4 | 4 | 种子恢复 |
| policy_versions | v1.0 archived + v1.2 published | 同前 | 种子恢复 |
| policy_rules（按版本） | v1.0:8, v1.2:8 | v1.0:8, v1.2:8 | 种子恢复 |
| Provider 健康 | transport=`fault_injected` | 全部 `ok` | fixture 复位 |

完整逐员工/逐版本明细见 `AC-016-reset.json` 的 `db_before` / `db_after`。

## 授权与确认门（API 测试）

| 调用者 / 输入 | 结果 |
|---|---|
| 张三（employee）+ 正确确认串 | 403 `FORBIDDEN` |
| 李四（manager）+ 正确确认串 | 403 `FORBIDDEN`（仅 admin） |
| 王五（admin）+ 空 body | 422 `CONFIRMATION_REQUIRED` |
| 王五（admin）+ 错误确认串 `"RESET"` | 422 `CONFIRMATION_REQUIRED` |
| 上述被拒尝试后 | Trip 仍为 2（无副作用） |
| 王五（admin）+ `"RESET_DEMO_DATA"` | 200 `{"status":"reset"}` |

## 关于“酒店和 fixture 恢复”

酒店与 Provider fixture 是**只读素材**，每次调用即时重读并经 schema 校验（§6.6），不落库、重置也不会改动素材目录（HC-08）。重置能改变的动态部分是 `faults` 表：清除故障后 Provider 行为回到 fixture 默认，`GET /api/health/providers` 显示全部 `ok`，以此验证“fixture 恢复”。

## HC-11 一致性

审计表带 `BEFORE UPDATE`（恒 ABORT）与 `BEFORE DELETE`（仅当 `meta.reset_in_progress='1'` 才放行）两个触发器。重置在单事务内置该标志、删除动态表、最后删 `meta` 清标志，因此“删除既有审计”只能由 Demo reset 触发；重置后审计只剩引擎追加的 1 条 `demo_reset`（其 payload 记录被删除的 checkpoint 线程数）。任何非重置路径修改/删除审计仍会被触发器拒绝（见 `tests/test_bootstrap.py::test_audit_and_outbox_are_append_only_outside_reset`）。

## 文件

| 文件 | 内容 |
|---|---|
| `AC-016-reset.json` | 30 项检查全通过；`db_before`/`db_after` 完整快照、Provider 健康前后、各步骤记录 |
| `boot-1.log` | 服务器启动日志 |
