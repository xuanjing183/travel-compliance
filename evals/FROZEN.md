# 评测数据集冻结清单（D-17 / §8.2）

门控评测使用的题集、gold 文件与注入样本已在**首次授权门控真实运行前冻结**。冻结后不得修改；确需修改须重新冻结并记录原因。SHA-256 同时记入 `docs/acceptance.md`。

- 冻结时间：2026-10-20（UTC+8）
- 冻结人：实施与交付负责人
- 语料：`tests/fixtures/policies/v1.2.md`（已发布版本 v1.2）
- 指标口径：`METRICS.md`（同批冻结）

| 文件 | 用途 | 字节数 | SHA-256 |
|---|---|---:|---|
| `qa_set.json` | 25 题制度问答黄金集（AC-004/017/025，REQ-026） | 5887 | `469c17a4f1adcfab13a9a8bcf677737cdaf0ed8a74900b9547fef3352da880f6` |
| `gold_rules_v1.2.json` | v1.2 gold 规则集，§4.2 的 8 条（AC-018，REQ-027） | 2508 | `686d34e07dcc4537fa476a4ca97eaca3053bb3e93de8f9b85a9360742b2a6f83` |
| `injections.json` | 无效引用注入样本 N=6 + 不支持类型注入样本 N=3（AC-018，D-17） | 5052 | `c8e72b1546e1b39eded0026256fbacdba0734a0457def62b8c4cc2df6d278de6` |
| `METRICS.md` | 冻结的指标口径（AC-004/017/018/025/029/020） | 6825 | `14ad2a2b47f994da2db81011d150e6e8971b814856e3bae0c4f1cbb334290530` |

## 校验命令

```
sha256sum evals/qa_set.json evals/gold_rules_v1.2.json evals/injections.json evals/METRICS.md
```

## 题集构成

- 总计 25 题：20 道可答（覆盖 POL-1～POL-8）+ 5 道无答案（拒答）。
- 数值题 11 道（金额 9 + 天数 2），非数值可答 9 道。
- 注入样本：无效引用 6 条（≥5，篡改 quote 构造，均不在归一化文档中）；不支持类型 3 条（均为封闭 7 类以外、quote 有效）。

## 评测顺序（§8.2）

1. 先运行 **AC-025**（vector vs hybrid，Recall@3）确定保留的检索配置；
2. 再以该最终配置运行 **AC-004 / AC-017**，使指标反映实际交付配置；
3. **AC-018**（规则抽取）、**AC-029**（真实图片识别）独立运行；
4. **AC-020**（SHOULD，黄金路径耗时）真实模型运行 3 次。

所有门控运行使用真实模型 / 真实 embedding，调用记入 `artifacts/llm-usage.jsonl` 与 `artifacts/cost-ledger.md`（AUTH-01，上限 50 元 / 40 元暂停）。
