# AC-018 — 规则抽取指标（真实模型，冻结 v1.2 全文 + gold 规则集）

## 结论

**passed** — 真实模型 `qwen3.8-max` 对冻结 v1.2 全文抽取，经代码侧 `compile_candidates` 判定，全部达标：

| 指标 | 阈值 | 实测 | 判定 |
|---|---|---|---|
| recall（匹配 gold 规则数 / 8） | ≥ 80%（≥7 条） | **8/8 = 100.00%** | ✓ |
| 无效引用拦截率（注入 N=6） | 100% | **6/6 = 100.00%** | ✓ |
| 不支持规则标记率（注入 N=3） | 100% | **3/3 = 100.00%** | ✓ |
| precision（匹配任一 gold 的候选 / 全部候选） | 报告不设门槛 | 8/10 = 80.00% | 报告 |
| 模型自发 `invalid_quote` 数量 | 另行报告 | **0** | 报告 |
| 真实抽取中非封闭类型候选误标数 | 应为 0 | **0**（2 条均正确标 `unsupported`） | ✓ |

- 匹配口径（METRICS.md §3 / D-17）：候选与 gold 的 `type`、`condition`、`value`（金额统一为分）、`unit`、`clause` **全部一致**才计命中；允许一对多。`city_tier` 各档城市与 `cabin_class.allowed` 按集合比较，`hotel_cap`/`meal_allowance` 的 `caps_fen` 逐档比较。
- 无效引用拦截**不依赖模型自发产生**（D-17）：用 `injections.json` 的 6 条篡改 quote 注入样本经 `compile_candidates` 判定，全部被标 `invalid_quote`（拦截率 100%）。
- 不支持类型标记：3 条注入样本（`taxi_expense`/`per_diem_bonus`/`cabin_upgrade_reimbursement`，均为非封闭类型、quote 有效）全部被标 `unsupported`（100%）。

## 运行配置

- `--mode real`：真实文本模型 `qwen3.8-max`（`temperature=0`，单次 function-calling 结构化输出）。门控要求（D-08）：**不得**用 Fake/Recorded 代替。
- 语料：`tests/fixtures/policies/v1.2.md`，归一化全文 SHA-256 `1810017a416e80c7…`（运行内嵌入报告，绑定冻结字节）。
- 抽取器只*提议*原始候选；类型是否封闭、quote 是否有效、规则 ID 全部由代码（`compile_candidates`）判定——模型不决定验收结果。

## 命令

```
uv run --offline python scripts/eval_rules.py \
    artifacts/acceptance/TASK-011/ac018-rules/real-v1.2.json --mode real
```

输出：
```
mode=real extractor=qwen3.8-max candidates=10
  recall=8/8=1.0 (>=0.80)
  precision=8/10=0.8 (reported)
  invalid_quote_interception=6/6=1.0 (==1.0)
  unsupported_marking=3/3=1.0 (==1.0)
  model_spontaneous_invalid_quote=0
  real_non_closed_type_mislabelled=[]
  usage: 1 calls, 2193 tokens, 0.078948 CNY
```

## 真实抽取的 10 条候选（逐条见 `real-v1.2.json`）

8 条命中 gold（全部 `quote_status=valid`、`status=candidate`），与 §4.2 的 8 条规则一一对应：

| 候选 ID | type | clause | condition | value（统一为分） | gold |
|---|---|---|---|---|---|
| RULE-CITY-001 | city_tier | POL-1 | scope=all | A={北京,上海,广州,深圳} B={杭州,南京,成都,武汉,西安,重庆,苏州,天津} fallback=C | RULE-CITY-001 ✓ |
| RULE-HOTEL-001 | hotel_cap | POL-2 | level=employee | A50000/B40000/C30000 | RULE-HOTEL-001 ✓ |
| RULE-HOTEL-002 | hotel_cap | POL-2 | level=manager | A65000/B50000/C40000 | RULE-HOTEL-002 ✓ |
| RULE-MEAL-001 | meal_allowance | POL-3 | scope=all | A10000/B8000/C6000 | RULE-MEAL-001 ✓ |
| RULE-CLASS-001 | cabin_class | POL-4 | scope=all | allowed={economy,second_class} | RULE-CLASS-001 ✓ |
| RULE-ADVANCE-001 | advance_booking_days | POL-5 | mode=air | days=3 | RULE-ADVANCE-001 ✓ |
| RULE-AUTO-001 | auto_approve_limit | POL-6 | scope=all | value_fen=300000 | RULE-AUTO-001 ✓ |
| RULE-DURATION-001 | trip_duration_limit | POL-7 | scope=all | days=15 | RULE-DURATION-001 ✓ |

2 条未匹配 gold，均为**非封闭类型、正确标记 `unsupported`、quote 有效**（设计行为：保留但运行期不使用，绝不静默丢弃）：

| 候选 ID | type | clause | 说明 |
|---|---|---|---|
| RULE-OTHER-001 | hotel_calc_rule | POL-2 | 模型把「酒店按入住晚计、退房日不占用」（D-18 语义）抽为独立候选；非 7 类封闭类型 → `unsupported` |
| RULE-OTHER-002 | taxi_reimbursement | POL-8 | 「出租车费用实报实销、不计入总额」；非封闭类型 → `unsupported` |

precision = 8/10 = 0.80：分母含这 2 条冗余/不支持候选（口径要求「重复与冗余候选计入分母」），不设门槛，仅报告。

## 费用与授权（AUTH-01）

本次真实运行 1 次调用（purpose=`policy_extraction`，model=`qwen3.8-max`），2193 token，估算 **0.078948 元**，已追加到 `artifacts/llm-usage.jsonl` 并由 `scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。含手工行累计估算约 1.95 元 / 上限 50 元（暂停线 40 元）。仅核对 `DASHSCOPE_API_KEY` 变量名存在，未读取/打印其值（F-03/HC-07）。

## 证据文件

| 文件 | 内容 |
|---|---|
| `real-v1.2.json` | 模型名、`document_normalized_sha256`、gold/injections 的 SHA-256、10 条候选明细（type/condition/value/unit/clause/quote_status/status/locator）、`per_gold` 匹配明细、recall/precision、注入拦截逐条结果、自发 invalid_quote 计数、usage |
| `../../../scripts/eval_rules.py` | 评测脚本（真实抽取 + gold 归一化匹配 + 冻结注入判定；口径见 `evals/METRICS.md` §3） |
| `../../../evals/gold_rules_v1.2.json` | 冻结 gold 规则集（8 条，SHA-256 `686d34e0…`） |
| `../../../evals/injections.json` | 冻结注入样本（6 无效引用 + 3 不支持类型，SHA-256 `c8e72b15…`） |
