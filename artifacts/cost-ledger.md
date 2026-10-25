# 费用台账（AUTH-01）

授权口径：仅 DashScope 模型与 embedding；累计上限 50 元；累计估算达到 40 元时暂停并请示项目所有者；禁止批量压测。

估算口径：所有 token 类型（输入、输出、图片、embedding）一律按 36 元/百万 token 计价。这是 2026-10-19 查询到的 [百炼模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing) 中 qwen3.8-max 的输出单价，也是本项目所用模型中的最高单价，因此估算只会偏高。实际结算以阿里云账单为准。

| 日期 | 任务 | 用途 | 模型 | 调用次数 | token 数 | 估算费用（元） | 证据 |
|---|---|---|---|---:|---:|---:|---|
| 2026-10-19 | TASK-001 | 结构化槽位抽取 10 样例（H-01） | qwen3.8-max | 10 | 6712 | 0.2416 | `artifacts/spikes/model-spike.json` |
| 2026-10-19 | TASK-001 | 本地 base64 图片识别 3 次（RISK-002） | qwen3.5-plus | 3 | 4656 | 0.1676 | 同上 |
| 2026-10-19 | TASK-001 | embedding 可用性 | qwen3.7-text-embedding | 1 | 74（按字符数×2 估算） | 0.0027 | 同上 |

免费调用（不计费）：2026-10-19 `GET /compatible-mode/v1/models` 1 次，用于确认可用模型。

**累计估算：0.4119 元 / 上限 50 元（暂停线 40 元）**

## 运行期调用（TASK-006 起）

应用在真实模式（`TRAVEL_DESK_LLM_MODE=real`）下的每次模型调用自动追加到 [`llm-usage.jsonl`](llm-usage.jsonl)（用途、模型、token、估算费用、耗时；不含任何输入原文或密钥）。调用前守卫按“上表手工合计 + JSONL 合计”计算累计估算，达到 40 元即拒绝调用（`BudgetPaused`），台账不可读时同样拒绝。下方标记供守卫读取，只记录上表手工行的合计：

<!-- manual_total_cny: 0.4119 -->

运行期合计由 `uv run --offline python scripts/ledger_summary.py --write` 汇总到下表（按日期、用途、模型分组；只替换两个 runtime-table 标记之间的内容）。

<!-- runtime-table:begin -->
| 日期（UTC） | 用途 | 模型 | 调用次数 | token 数 | 估算费用（元） |
|---|---|---|---:|---:|---:|
| 2026-10-19 | image_extraction | qwen3.5-plus | 1 | 1577 | 0.0568 |
| 2026-10-19 | itinerary_summary | qwen3.8-max | 4 | 1738 | 0.0626 |
| 2026-10-19 | policy_answer | qwen3.8-max | 8 | 6823 | 0.2456 |
| 2026-10-19 | policy_embedding | qwen3.7-text-embedding | 8 | 2400 | 0.0864 |
| 2026-10-19 | policy_query_embedding | qwen3.7-text-embedding | 8 | 194 | 0.0070 |
| 2026-10-19 | slot_extraction | qwen3.8-max | 3 | 2441 | 0.0879 |
| 2026-10-20 | image_extraction | qwen3.5-plus | 1 | 1577 | 0.0568 |
| 2026-10-20 | itinerary_summary | qwen3.8-max | 9 | 3815 | 0.1373 |
| 2026-10-20 | policy_answer | qwen3.8-max | 25 | 21720 | 0.7819 |
| 2026-10-20 | policy_embedding | qwen3.7-text-embedding | 6 | 1800 | 0.0648 |
| 2026-10-20 | policy_extraction | qwen3.8-max | 1 | 2193 | 0.0789 |
| 2026-10-20 | policy_query_embedding | qwen3.7-text-embedding | 75 | 1890 | 0.0680 |
| 2026-10-20 | slot_extraction | qwen3.8-max | 12 | 9558 | 0.3441 |
| 2026-10-22 | itinerary_summary | qwen3.8-max | 10 | 3938 | 0.1418 |
| 2026-10-22 | slot_extraction | qwen3.8-max | 11 | 9093 | 0.3273 |
| 2026-10-24 | itinerary_summary | qwen3.8-max | 1 | 456 | 0.0164 |
| 2026-10-24 | policy_agent | qwen3.8-max | 231 | 176043 | 6.3375 |
| 2026-10-24 | policy_answer | qwen3.8-max | 25 | 21721 | 0.7820 |
| 2026-10-24 | policy_embedding | qwen3.7-text-embedding | 54 | 16200 | 0.5832 |
| 2026-10-24 | policy_query_embedding | qwen3.7-text-embedding | 699 | 17804 | 0.6409 |
| 2026-10-24 | policy_query_rewrite | qwen3.5-plus | 184 | 100047 | 3.6017 |
| 2026-10-24 | policy_rerank | gte-rerank-v2 | 315 | 165577 | 5.9608 |
| 2026-10-24 | slot_extraction | qwen3.8-max | 3 | 2402 | 0.0865 |

**运行期合计：20.5563 元；含手工行累计估算：20.9682 元 / 上限 50 元（暂停线 40 元）**
<!-- runtime-table:end -->
