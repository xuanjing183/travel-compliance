# TASK-011 证据索引与评测报告（评测与验收自动化）

本目录汇总 TASK-011 的全部验收证据。门控项（AC-004、AC-017、AC-018、AC-025、AC-029）
依 D-08 **必须**使用真实模型/真实 embedding，不得以 Fake/Recorded 计算质量指标；AUTH-01
已批准，故这些项均为 `passed`。AC-019（确定性场景）与 AC-020（SHOULD 耗时）方法学不同，
分别以 fake 子进程与真实子进程运行。

## 结论一览

| AC | 类型 | 结果 | 关键指标（阈值） | 模型 | 证据 |
|---|---|---|---|---|---|
| AC-025 | 门控 | passed | Recall@3 = 20/20 = **1.0000**（vector 与 hybrid 打平）→ 按 D-17 保留**纯向量** | `qwen3.7-text-embedding`（1024 维） | [ac025-retrieval/](ac025-retrieval/README.md) |
| AC-004 | 门控 | passed | 引用命中率 **20/20=100%**（≥90%）；关键数值正确率 **11/11=100%**（≥85%）；拒答 **5/5**（≥4/5） | `qwen3.8-max` + 真实 embedding，vector 检索 | [ac004-qa/](ac004-qa/README.md) |
| AC-017 | 门控 | passed | 与 AC-004 同一次真实运行、同一冻结 25 题集；引用命中=出现即命中 **20/20**；数值统一换算为「分」后 **11/11** | 同上 | [ac004-qa/](ac004-qa/README.md) |
| AC-018 | 门控 | passed | recall **8/8=100%**（≥80%）；无效引用拦截 **6/6=100%**；不支持类型标记 **3/3=100%**；precision 8/10=80%（报告不设门槛） | `qwen3.8-max` 抽取冻结 v1.2 全文 | [ac018-rules/](ac018-rules/README.md) |
| AC-029 | 门控 | passed | 冻结判定口径（METRICS.md §4）四项全中：城市/日期/事由/地址 | `qwen3.5-plus`（本地 base64 data URL，不经 OSS，RISK-002 消解） | [ac029-vision/](ac029-vision/README.md) |
| AC-019 | 确定性 | passed | `scenarios 36/36 passed; checks 270/270`（S-01～S-12 各 3/3，无硬约束违反，§14.3） | fake 子进程（HTTP/SSE） | [ac019-scenarios/](ac019-scenarios/README.md) |
| AC-020 | SHOULD | passed | 计时边界「显式提交 → `approval_requested`」**中位数 4.204 秒 ≤ 90 秒**（max 10.915s / min 2.785s），3 次均达 `PENDING_APPROVAL`、审批卡总额 476000 | `qwen3.8-max` 真实子进程 | [ac020-timing/](ac020-timing/README.md) |

## 评测顺序（METRICS.md / §8.2）

1. **AC-025 先跑**，在冻结 25 题集上对比 vector 与 hybrid 的 Recall@3。两者打平（均 1.0000），
   按 D-17「只有主指标严格更好才保留混合检索，打平保留纯向量」→ 交付配置为**纯向量**
   （`TRAVEL_DESK_RETRIEVAL` 默认即 vector，无需改代码）。
2. **AC-004 / AC-017** 以该最终检索配置（vector）单次真实运行 25 题。
3. **AC-018、AC-029** 独立运行（规则抽取、图片识别）。
4. **AC-020**（SHOULD）真实模型运行 3 次取中位数。

## 冻结数据集（D-17 / §8.2）

题集、gold 规则与注入样本已在**首次授权门控真实运行前冻结**，冻结后不得修改。
指标口径见 [`evals/METRICS.md`](../../../evals/METRICS.md)，冻结清单见 [`evals/FROZEN.md`](../../../evals/FROZEN.md)。

| 文件 | 用途 | SHA-256 |
|---|---|---|
| `evals/qa_set.json` | 25 题黄金集（20 可答 + 5 拒答；11 数值题）AC-004/017/025 | `469c17a4f1adcfab13a9a8bcf677737cdaf0ed8a74900b9547fef3352da880f6` |
| `evals/gold_rules_v1.2.json` | v1.2 gold 规则集（§4.2 的 8 条）AC-018 | `686d34e07dcc4537fa476a4ca97eaca3053bb3e93de8f9b85a9360742b2a6f83` |
| `evals/injections.json` | 无效引用注入 N=6 + 不支持类型注入 N=3，AC-018 | `c8e72b1546e1b39eded0026256fbacdba0734a0457def62b8c4cc2df6d278de6` |
| `evals/METRICS.md` | 冻结的指标口径（AC-004/017/018/025/029/020） | `14ad2a2b47f994da2db81011d150e6e8971b814856e3bae0c4f1cbb334290530` |

校验：`sha256sum evals/qa_set.json evals/gold_rules_v1.2.json evals/injections.json evals/METRICS.md`。

## 评测脚本

| 脚本 | 覆盖 | 运行方式 |
|---|---|---|
| `scripts/eval_qa.py` | AC-025（`--stage retrieval`）+ AC-004/017（`--stage full`） | 门控真实运行省略 `env -u DASHSCOPE_API_KEY` 前缀 |
| `scripts/eval_rules.py` | AC-018（`RealRuleExtractor` + `compile_candidates` 对 8 条 gold） | `out --mode real` |
| `scripts/eval_vision.py` | AC-029（`RealInvitationExtractor` 对 S-11 fixture） | `out`（真实多模态） |
| `scripts/eval_timing.py` | AC-020（真实子进程 + S-01 黄金路径 + 中位数） | `out --runs 3` |
| `scripts/e2e_scenarios.py` | AC-019（fake 子进程 12 场景 × N 轮） | `--runs 3 --mode fake` |

真实调用一律经 `UsageMeter` 守卫并记账到持久 [`artifacts/llm-usage.jsonl`](../../llm-usage.jsonl)，
随后 `uv run --offline python scripts/ledger_summary.py --write` 汇总到
[`artifacts/cost-ledger.md`](../../cost-ledger.md)。脚本只核对环境变量名 `DASHSCOPE_API_KEY`
是否存在，从不读取/打印/提交其值（F-03 / HC-07）。

## 费用（AUTH-01）

TASK-011 门控真实运行的增量费用（按用途，详见台账运行期表）：
制度问答 25 次 `policy_answer`（0.7819 元）+ 相应 embedding；规则抽取 1 次
`policy_extraction`（0.0789 元）；图片识别 1 次 `image_extraction`（0.0568 元）；
AC-020 行程摘要 3 次 `itinerary_summary`（0.0461 元）。

**含手工行累计估算约 2.05 元 / 上限 50 元（暂停线 40 元）**——远低于暂停线，无需请示。

## 真实性核查（非空跑）

- **AC-004/017**：逐题保存真实回答文本与引用块 ID；引用命中率按「gold 条款是否出现在引用列表」判定，数值题统一换算为「分」后比对（「500 元」=50000 分）。
- **AC-018**：8 条 gold 与模型候选一一对应、quote 全 `valid`；另有 2 条非封闭类型候选被代码正确标 `unsupported`（计入 precision 分母，非误报）。注入样本由确定性 `compile_candidates` 编译，不依赖模型自发产出（D-17）。
- **AC-029**：真实输出与 TASK-001 录制 fixture 措辞不同而语义一致，证明为实时调用而非回放。
- **AC-020**：3 次逐运行保存 SSE 事件序列、终态、审批卡总额与 boot 日志尾部。

## 已知观察（不影响上述结论）

AC-020 子进程的出站守卫记录中，对 DashScope 的一次解析因 httpcore 以 **bytes** 主机名调用
`getaddrinfo`，被 `network_guard._local()` 误分类为 `local`（对非 `str` 主机一律返回 `True`），
未走允许清单核对。对 AC-020 无影响（real 模式 DashScope 本就在允许清单内，调用成功、计时有效），
对 AC-021 为潜伏（fake 模式业务代码从不外联，负向证据 `external_attempts=0` 仍成立；正向对照
用 `str` 主机名，未覆盖 bytes 路径）。因修复会改变安全边界控件的运行时行为，已作为 **IMP-97
提案**上报所有者等待决定，未擅自修改 `network_guard.py`。详见
[ac020-timing/README.md](ac020-timing/README.md#关于-network-guardjsonl-的一条观察不影响本项结论)。
