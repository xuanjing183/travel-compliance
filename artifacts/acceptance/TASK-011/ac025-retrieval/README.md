# AC-025 — 检索对比（纯向量 vs BM25+RRF 混合，主指标 Recall@3）

## 结论

**passed** — 主指标 Recall@3 两种检索**打平**（均为 20/20 = 1.0000）。按 D-17「只有主指标严格更好才保留混合检索，打平保留纯向量」，**保留纯向量（vector）**作为交付配置。`TRAVEL_DESK_RETRIEVAL` 默认即 `vector`，无需改代码。

## 命令

```
uv run --offline python scripts/eval_qa.py artifacts/acceptance/TASK-011/ac025-retrieval/vector.json \
    --stage retrieval --retrieval vector --mode real
uv run --offline python scripts/eval_qa.py artifacts/acceptance/TASK-011/ac025-retrieval/hybrid.json \
    --stage retrieval --retrieval hybrid --mode real
```

- `--mode real`：真实 embedding（`qwen3.7-text-embedding`，1024 维，DashScope OpenAI 兼容 `/embeddings`），门控要求（D-08）。
- `--stage retrieval`：只跑交付的 `Retriever`（排序命中），不调用文本作答模型——Recall@3 是纯检索指标。
- 相同切分语料（v1.2，`chunk_document`）、同一冻结 25 题集；两次运行 `qa_set_sha256` 一致（`469c17a4f1adcfab…`）。

## 指标（冻结口径见 `evals/METRICS.md` §1）

| 检索 | 主指标 Recall@3 | 次要 Recall@5 | also_expected 进前 5 | 调用 | token | 估算费用 |
|---|---|---|---|---:|---:|---:|
| vector（纯向量，余弦） | **20/20 = 1.0000** | 20/20 = 1.0000 | 0/6 | 27 | 1230 | 0.04428 元 |
| hybrid（BM25+RRF，k=60） | **20/20 = 1.0000** | 20/20 = 1.0000 | 0/6 | 27 | 1230 | 0.04428 元 |

- 分母 20 = 有 `primary_clause` 的可答题（5 道拒答题无 gold 条款，不计入 Recall@3）。
- **主指标打平** → 保留纯向量（D-17）。混合检索未带来主指标提升，不引入其额外复杂度。

## 排序差异（不影响主指标）

两种检索的**前 3 名次序**在 15 道题上不同（hybrid 的 BM25 融合会重排次要名次），但每题的 `primary_clause` 在两种模式下都落在前 3（多数为 rank 1），故 Recall@3 同为满分。示例：

| 题 | primary | vector 前 3 | hybrid 前 3 |
|---|---|---|---|
| Q12（自动通过阈值） | POL-6 | POL-6, 制度信息, POL-8 | POL-6, POL-7, POL-8 |
| Q14（单次时长） | POL-7 | POL-7, POL-6, POL-3 | POL-7, POL-6, POL-5 |

## also_expected（POL-1）说明

6 道「城市等级 + 标准」组合题（Q03/Q04/Q07/Q17/Q18/Q19）的 `also_expected=POL-1` 在两种模式下都**不在排序前 5**，而是经**规则依赖补充机制**（`with_supplements`，`DEPENDS_ON: hotel_cap/meal_allowance → city_tier`）作为 supplement 附在排序命中之后送入作答器。这是设计行为：城市分类条款由代码按规则依赖补全，而非靠词面/语义独立检索，因此不影响主指标 Recall@3，也保证作答器能看到定级依据。该现象为 vector 与 hybrid 共有，不构成两者取舍依据。

## 架构决定（REQ-029）

- **保留检索配置：纯向量（vector）**，余弦相似度，`qwen3.7-text-embedding`，TOP_K=5。
- 依据：主指标 Recall@3 打平（1.0000 vs 1.0000），D-17 规定打平保留纯向量。
- 混合检索（BM25+RRF）代码保留可用（`TRAVEL_DESK_RETRIEVAL=hybrid`），但**不作为交付默认**；本版语料规模小、向量检索已达满分，混合无主指标增益。
- 该决定为 AC-004/AC-017 的最终检索配置：后续问答评测以 `--retrieval vector` 运行。

## 证据文件

| 文件 | 内容 |
|---|---|
| `vector.json` | 纯向量逐题排序（ranked_clauses/top3/top5/supplements）、Recall@3/@5、usage、qa_set_sha256、embedding_model |
| `hybrid.json` | 混合检索同上 |

## 授权与台账

真实 embedding 调用已记入 `artifacts/llm-usage.jsonl`（purpose=`policy_embedding`/`policy_query_embedding`，model=`qwen3.7-text-embedding`），运行后由 `scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`（AUTH-01，上限 50 元 / 40 元暂停）。仅核对 `DASHSCOPE_API_KEY` 变量名存在，未读取/打印其值（F-03/HC-07）。
