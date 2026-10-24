# AC-006 规则引擎 — passed

- 代码版本：`6939e63`（规则核心于 `6c70b68` 实现，本次未改动）；Python 3.11.0，pytest 9.1.1；Fake 模式，无网络、无模型调用。
- 命令：`uv run pytest -v -p no:cacheprovider tests/test_rules.py tests/test_domain_basics.py --junitxml=artifacts/acceptance/AC-006/junit.xml`
- 结果：84 passed，0 failed（`pytest-v.txt` 完整输出，`junit.xml`，用例清单 `cases.txt`）。

| 预期（§8.2 AC-006） | 证据 |
|---|---|
| 每种规则 | 住宿上限（按级别 `test_hotel_cap_boundary_by_level`、按城市等级 `..._by_tier`）、补贴 `test_meal_allowance_by_tier`、舱位 `test_cabin_class_violation`、提前天数 `test_advance_booking_boundary`（v1.2 与 v1.0）、自动阈值 `test_auto_limit_boundary`/`test_v10_auto_limit_is_lower`、时长与日期阻断 `test_hard_block_boundaries`、城市等级 `test_city_normalisation` |
| 等于上限 / 超 1 分 | 50000 vs 50001、65000 vs 65001、300000 vs 300001、预算相等 vs 超 1 分（`test_budget_boundary`） |
| 日期边界 | 提前 3/2/1/0 天、v1.0 5/4 天、按出发地当地日期（`test_advance_uses_local_departure_date`）、开始日=今天/昨天、15/16 天、结束早于开始 |
| 代码结果与第 4 节一致 | 场景金额取自契约 §7.2：S-01 476000、S-02 190000、S-03 274000、S-04 180000、S-09 known 60000、AC-013 known 150000、S-12 v1.3 新增违规、S-08 预算 500000 仍需审批 |
| 模型输出不能改变结论 | 规则核心为纯函数，输入只有槽位、已选候选、已确认规则、角色、日期；`test_provider_values_cannot_be_smuggled_in`（候选模型拒绝浮点金额与未知字段）、`test_only_confirmed_rules_drive_decisions`、`test_evaluation_is_deterministic_and_json_safe` |

补充：对六个边界比较做过变异检查（`>`→`>=` 等），每个变异都被上述测试捕获（TASK-003 阶段记录）。
