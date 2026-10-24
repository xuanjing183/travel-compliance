# TASK-009a 验收证据：个人偏好（#24）与按新版本重新校验（#14）

依据 `docs/contract-v1.1.md` §8（AC-023、AC-024）、D-12、D-20，以及所有者决定 IMP-04/IMP-05。全部证据在**确定性验收模式**下生成：`llm_mode=fake`、临时数据目录、不访问网络、不调用模型（运行时环境中移除了 `DASHSCOPE_API_KEY`）。

## 复现

```bash
uv run --offline pytest tests/test_preferences_revalidate.py -v                          # 10 passed → pytest-v.txt / junit.xml
uv run --offline python scripts/export_task009_evidence.py artifacts/acceptance/TASK-009   # 13/13 PASS → summary.json
```

## 已知限制：v1.3 不是通过发布接口发布的

制度上传、抽取、确认、发布接口（#20–#23）属于 TASK-008，尚未实现。本目录中的 v1.3 由测试专用的 `publish_v13_direct` 直接写入临时数据库：

- 规则与 v1.2 相同，只有 RULE-HOTEL-001 的员工 A 类上限改为 45000 分/晚。
- 原文引用和行号定位取自 `tests/fixtures/policies/v1.3.md`。
- 旧的发布版本在同一事务中归档。

这条捷径**只存在于测试和本证据脚本中**，产品代码里没有对应入口。S-12 要求“创建并发布 v1.3”，这一段要等 TASK-008 完成后通过管理员发布流程重跑，因此 AC-024 暂记为 not_run。

> 更新（TASK-008a）：发布接口已实现，S-12 已通过管理员上传、抽取、审核、发布 v1.3 的流程重跑，见 [TASK-008 / AC-024](../TASK-008/README.md#ac-024--s-12-通过发布接口创建-v13再重新校验)，AC-024 据此记为 passed。本目录保持原样，作为 TASK-009a 当时的证据。

## AC-023 偏好（HTTP 三段）

证据文件：

- `AC-023/phases.json`：每一段的偏好接口响应、选中的去程、总额、`preference_source`、`ranking`，以及 `preference_snapshot` 和 `candidates_selected` 审计。
- `AC-023/{1-none,2-avoid-early,3-cleared}-plan.json`：三段的完整方案。
- `AC-023/preferences-audit.json`：偏好修改的审计记录。

| 段 | 操作 | 去程 | preference_source | ranking |
|---|---|---|---|---|
| 1 | 未设偏好 | `T-BJS-SHA-1105-EARLY`（稳定次级排序键：出发时刻升序，见 IMP-07） | none | applied=false |
| 2 | `PUT /api/users/u-zhangsan/preferences {"avoid_early_departure": true}` | `T-BJS-SHA-1105-LATE` | personal_snapshot | applied=true，affected=true |
| 3 | `DELETE …/preferences` | `T-BJS-SHA-1105-EARLY` | none | applied=false |

三段的总额均为 190000 分，结果都是 `AUTO_APPROVED` → `DELIVERED`。第 1 段的 Trip 在偏好修改后仍保留空快照（D-20）。`preferences_changed` 审计依次记录了 put 和 delete 两次操作。

## AC-024 / S-12 制度变更后重新校验

| 阶段 | 证据文件 |
|---|---|
| 校验前 | `AC-024/trip-before.json`、`approval-cards-before.json` |
| v1.3 发布后的提示 | `trip-with-notice.json` |
| 校验请求 | `revalidate-response.json` |
| 校验后 | `trip-after.json`、`approval-cards-after.json` |
| 全量审计 | `audit.json` |

检查结果：

- **提示：** `policy_change_notice = {bound_version: v1.2, published_version: v1.3}`。
- **重新校验：** 由张三发起，revision 从 1 变为 2，`policy_version` 变为 v1.3，状态仍为 `PENDING_APPROVAL`，响应中 `previous_revision=1`。
- **旧审批失效：** revision 1 的卡变为 `superseded`。revision 2 有一张新的 pending 卡，李四的待办列表只显示这张新卡。用旧 revision 提交决定返回 409 `STALE_REVISION`。
- **新增违规：** `HOTEL_OVER_CAP`，实际 48000，上限 45000，依据 RULE-HOTEL-001（v1.3），引用的是 v1.3 原文。
- **复用候选：** 交通与酒店候选和校验前完全一致。所有 `provider_call` 审计都属于 revision 1；`revalidated.providers_called=false`。
- **两次结果都在审计中：** `revalidated.before` 是 v1.2 的判断，`after` 是 v1.3 的判断；`rules_evaluated` 依次记录了 v1.2 和 v1.3 两次。
- **幂等：** 用同一个 Idempotency-Key 重放，返回相同响应。

自动测试还覆盖了以下情形：

- **IMP-04：** 不再需要审批时自动通过并交付，revision 2 只发出一封 `trip_delivery`。交付写入失败时停在 `DELIVERY_PENDING`，管理员重试后变为 `DELIVERED`。
- **IMP-05：** 命中硬阻断时进入新 revision 的 `POLICY_BLOCKED`，旧方案保留在审计中。
- **D-21：** 申请人在审批期间失去直属经理时，回到 `COLLECTING`，不建新卡。
- **错误顺序与权限：** 依次为 401、400、422、404（含非直属经理）、403（admin）、409。新卡的决定走直接路径，之后交付。
