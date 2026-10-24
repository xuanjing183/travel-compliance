# AC-004 / AC-017 — 制度问答黄金集评测（真实模型 + 真实 embedding）

## 结论

**AC-004 passed，AC-017 passed** — 冻结 25 题黄金集在**同一次真实运行**中全部达标（AC-017 要求与 AC-004 同一次运行）：

| 指标 | 阈值 | 实测 | 判定 |
|---|---|---|---|
| 引用命中率（AC-004/AC-017） | ≥ 90% | **20/20 = 100.00%** | ✓ |
| 关键数值正确率（AC-004/AC-017） | ≥ 85% | **11/11 = 100.00%** | ✓ |
| 无答案题正确拒答（AC-004） | ≥ 4/5 | **5/5** | ✓ |
| Recall@3（检索，参考） | — | 20/20 = 100.00% | ✓ |

- 正确拒答口径（AC-004/D-21）：结构化输出标记 `answerable=false` **且**引用列表为空。5 道拒答题全部满足。
- 引用命中口径（AC-017/D-18）：答案引用列表中出现 gold 条款即命中，不要求逐字一致。
- 数值口径（AC-017/D-18）：先统一换算为「分」再比较，「500 元」= 50000 分；时长题按整数个自然日比较。

## 运行配置（交付配置）

- `--mode real`：真实文本模型 `qwen3.8-max` 作答，真实 embedding `qwen3.7-text-embedding`（1024 维）检索。门控要求（D-08）：**不得**用 Fake/Recorded 代替。
- `--retrieval vector`：AC-025 决定的最终交付检索配置（纯向量，余弦，TOP_K=5）。本项与 AC-004/AC-017 同一次运行，指标反映实际交付配置（§8.2 评测顺序）。
- v1.2 已发布语料（`tests/fixtures/policies/v1.2.md`），`generator=qwen3.8-max`（逐题记录）。
- 冻结题集 SHA-256：`469c17a4f1adcfab13a9a8bcf677737cdaf0ed8a74900b9547fef3352da880f6`（`evals/qa_set.json`，25 题 = 20 可答 + 5 拒答；AC-017 数据集 hash）。

## 命令

```
uv run --offline python scripts/eval_qa.py \
    artifacts/acceptance/TASK-011/ac004-qa/full-vector.json \
    --stage full --retrieval vector --mode real
```

输出：
```
stage=full mode=real retrieval=vector Recall@3=20/20=1.0 Recall@5=20/20=1.0
  citation_hit=20/20=1.0 (>=0.90)
  numeric_correct=11/11=1.0 (>=0.85)
  refusal_correct=5/5 (>=4/5)
  usage: 52 calls, 22950 tokens, 0.8262 CNY
```

## 逐题抽样（完整 25 题见 `full-vector.json` 的 `rows` 与 `detail`）

数值题（换算为分/个自然日后与 gold 一致）：

| 题 | 问题 | 模型答案 | 引用 | 解析值 | gold |
|---|---|---|---|---|---|
| Q03 | 员工去上海出差住宿每晚最多能报销多少 | 「…每晚最多能报销500元」 | POL-2, POL-1 | 50000 分 | 50000 分 ✓ |
| Q05 | 员工在B类城市住宿每晚上限 | 「…每晚上限是400元」 | POL-2 | 40000 分 | 40000 分 ✓ |
| Q11 | 机票出发日距申请日至少间隔 | 「…至少需要间隔3个自然日」 | POL-5 | 3 个自然日 | 3 ✓ |
| Q12 | 单次差旅总额不超过多少可自动通过 | 「…不超过3000元可以自动通过审批」 | POL-6 | 300000 分 | 300000 分 ✓ |
| Q14 | 单次出差最长不超过 | 「…最长不超过15个自然日」 | POL-7 | 15 个自然日 | 15 ✓ |

拒答题（Q21–Q25）：全部 `answerable=false`、`cited_clauses=[]`，统一回复「不知道：当前制度（v1.2）中没有找到能回答这个问题的条款。」——无臆造引用。

## 费用与授权（AUTH-01）

本次真实运行 52 次调用（25 次 `policy_answer` + 25 次 `policy_query_embedding` + 2 次 `policy_embedding` 建索引），22950 token，估算 **0.8262 元**，已追加到 `artifacts/llm-usage.jsonl` 并由 `scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。含手工行累计估算约 1.87 元 / 上限 50 元（暂停线 40 元）。仅核对 `DASHSCOPE_API_KEY` 变量名存在，未读取/打印其值（F-03/HC-07）。

## 证据文件

| 文件 | 内容 |
|---|---|
| `full-vector.json` | 逐题 `rows`（问题/答案/generator/引用/排序/解析数值）+ `detail`（逐题命中判定）+ `metrics`（三项指标与阈值）+ `usage`（52 次调用明细）+ `qa_set_sha256` + 模型名 |
| `../../../scripts/eval_qa.py` | 评测脚本（`--stage full`；引用/数值/拒答打分；冻结口径见 `evals/METRICS.md`） |
