# AC-007 Provider 候选与代码选择 — passed

- 代码版本：`6939e63`；Python 3.11.0，pytest 9.1.1；Demo Provider（`tests/fixtures/providers/`），Fake 模式，无网络、无模型调用。
- 自动测试：`uv run pytest -v -p no:cacheprovider tests/test_planning.py --junitxml=artifacts/acceptance/AC-007/junit.xml` → 35 passed（`pytest-v.txt`、`junit.xml`、`cases.txt`）。
- Provider 响应与 plan JSON：`uv run python scripts/export_plan_evidence.py artifacts/acceptance/AC-007/plans`。每个场景一个文件，含 `provider_calls`（请求、状态、返回候选 ID）、`plan`、`evaluation`、`selected_ids`、`all_selected_ids_in_responses`；汇总见 `plans/summary.json`。

| 预期 | 证据 |
|---|---|
| 所有选中 ID 存在于响应 | `plans/summary.json` 中 9 个场景 `all_selected_ids_in_responses` 均为 `true`；测试 `assert_selected_ids_in_responses`（S-01～S-03） |
| 排序符合 REQ-013 | S-01：`H-SHA-CONTRACT-02` 因 10-28/10-29 不可订被过滤（`plans/S-01.json` `ranking.filtered_out`），选 `H-SHA-CONTRACT-01`；S-02：40000 先于 48000；S-03：成都无协议酒店，备用 `H-CTU-BACKUP-01` 超标作为例外；S-04：去程唯一候选提前 2 天，标 `exception_legs=["outbound"]`。排序键单测：`test_compliance_ranks_before_price`、`test_price_ranks_before_preference`、`test_preference_breaks_a_price_tie_and_08_00_is_not_early`、`test_stable_secondary_key_is_departure_time_then_id`、`test_contract_hotel_beats_cheaper_compliant_backup`、`test_compliant_backup_beats_over_cap_contract`、`test_hotel_tie_breaks_by_rating_then_id`、`test_value_equal_to_cap_is_compliant` |
| 无合规候选 | `test_no_compliant_transport_takes_cheapest_and_marks_exception`、`test_no_compliant_hotel_takes_least_over_cap`、S-03、S-04 |
| 伪造 ID 被拒绝 | `plans/forged-candidates.json`：伪造去程 ID、篡改酒店价格均 `rejected: true`；测试 `test_forged_candidate_id_is_rejected`、`test_real_id_with_altered_price_is_rejected`、`test_candidate_from_another_run_is_rejected`、`test_empty_responses_reject_any_selection` |
| Provider 响应经 Schema 校验 | `test_invalid_fixture_degrades_instead_of_trusting_partial_data`（浮点价格 → 整份作废、交通待补录）、`test_unknown_field_and_duplicate_ids_are_rejected` |

同目录的 `S-09-*`、`AC-013-*`、`AC-023-*` 文件是 AC-013/AC-023 的规划层证据；这两项还需要 API、审计和界面证据，状态仍为 `not_run`（见 `docs/acceptance.md`）。
