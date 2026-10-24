# 评测指标口径（冻结，D-17）

本文件与 `qa_set.json`、`gold_rules_v1.2.json`、`injections.json` 一同在**首次授权门控真实运行前冻结**，SHA-256 记入 `docs/acceptance.md`。冻结后不得修改；确需修改须重新冻结并记录原因（D-17）。指标口径事先定死，避免 0/0 与出题人自评漂移。

语料：`tests/fixtures/policies/v1.2.md`（已发布版本 v1.2）。题集：`qa_set.json`（25 题 = 20 可答 + 5 无答案）。所有门控指标必须使用真实模型 / 真实 embedding，不得用 Fake/Recorded 结果计算或宣称通过（D-08）。

## 1. AC-025 检索对比（主指标 Recall@3）

- 对每道**有 `primary_clause` 的题**（20 道可答题），取 `/api/policy/ask` 响应中 `retrieval.chunks` 按 `rank` 升序的**前 3 条**的 `clause` 集合。
- **Recall@3 命中** = `primary_clause` ∈ 前 3 条检索 `clause` 集合。
- **Recall@3** = 命中题数 / 20。
- 相同切分语料、同一 25 题集，分别以 `TRAVEL_DESK_RETRIEVAL=vector` 与 `=hybrid`（BM25+RRF）各运行一次。
- **保留规则（D-17）**：只有 hybrid 的主指标（Recall@3）**严格大于** vector 才保留 hybrid；打平或更差保留纯 vector。次要指标（Recall@5、`also_expected` 命中率）一并报告但不参与取舍。
- 检索指标只在排序命中（ranked hits）上计算，不含 supplements（见 `policy/rag.py` 模块说明）。

## 2. AC-004 / AC-017 制度问答（以 AC-025 选定的最终检索配置运行一次）

每题取 `/api/policy/ask` 响应：`answerable`、`answer`、`citations[].clause`、`retrieval.chunks[].clause`。

### 2.1 引用命中率（AC-004 阈值 ≥ 90%）

- 分母 = **需引用题数** = 20 道可答题（每题有唯一 `primary_clause`）。
- **命中** = 该题 `primary_clause` ∈ 答案 `citations[].clause` 集合（出现即命中，不要求逐字一致，不要求引用列表完全相等；多引不计错）。
- 引用命中率 = 命中题数 / 20。

### 2.2 关键数值正确率（AC-004 阈值 ≥ 85%）

- 分母 = **数值题数** = `qa_set.json` 中 `numeric != null` 的题（11 道：9 道金额 `kind=fen` + 2 道天数 `kind=count`）。
- 数值归一化（统一为分 / 计数）：从 `answer` 文本抽取所有数字及其紧邻单位，
  - 金额：单位含「元/块」→ ×100 化为分；单位含「分」→ ×1；无单位裸数按「元」处理 ×100（制度原文金额均以元表述）。得到候选「分」值集合。
  - 天数：单位含「天/日/晚」或「个自然日」→ 取整数计数，得到候选计数集合。
- **命中**：`kind=fen` 时 `value_fen` ∈ 候选分值集合；`kind=count` 时 `value` ∈ 候选计数集合。
- 前提：该题 `answerable=true`（对数值题拒答 = 未命中）。
- 关键数值正确率 = 命中题数 / 11。
- 说明：候选集合允许包含答案中出现的其他数值（如列出多档标准），只要 gold 值在其中即命中；逐题原始 `answer` 与抽取到的候选值一并写入报告供人工复核。

### 2.3 无答案题正确拒答（AC-004 阈值：5 题中 ≥ 4 题正确拒答）

- 分母 = 5 道 `refusal=true` 题。
- **正确拒答** = `answerable=false` **且** `citations` 为空（`finalize` 保证拒答时 citations 被清空；结构化输出标记无法回答且未给出任何引用）。
- 正确拒答数 ≥ 4 即通过。

## 3. AC-018 规则抽取（真实模型，冻结 v1.2 全文 + gold 规则集）

- 用 `RealRuleExtractor` 对 v1.2 全文抽取，经 `compile_candidates` 得到带 `quote_status`/`status`/`id` 的候选。
- **匹配口径**：候选与 gold 规则的 `type`、`condition`、`value`（金额统一为分）、`unit`、`clause` **全部一致**才计命中；允许一对多（多条候选匹配同一 gold）。
  - `value` 归一化：`city_tier` 的各档城市按**集合**比较，`fallback_tier` 单独比较（gold 为 `C`；候选若以显式 C 档城市列表表达「其他」，视为等价于 `fallback_tier=C`，前提是 A、B 档集合与 gold 一致）；`hotel_cap`/`meal_allowance` 的 `caps_fen` 逐档比较；`auto_approve_limit` 的 `value_fen`、`advance_booking_days`/`trip_duration_limit` 的 `days`、`cabin_class` 的 `allowed`（按集合）比较。
  - 金额「统一为分」：候选 `unit` 为 fen 类（`fen_per_night`/`fen_per_day`/`fen`）时数值按分比较；若候选以元为单位（unit 含元/yuan）则 ×100 化分后比较。
- **recall** = 匹配到的 gold 规则数 / 8，阈值 **≥ 80%**（即 ≥ 7 条）。
- **precision** = 匹配到任一 gold 的候选数 / 全部候选数（重复与冗余候选计入分母），**报告但不设门槛**。
- **无效引用拦截率**：用 `injections.json` 的 `invalid_quote` 注入样本（N=6 ≥ 5）经 `compile_candidates` 判定，被标 `invalid_quote` 者 / 注入总数，须 **100%**（D-17，不依赖模型自发产生）。模型自发产生的 `invalid_quote` 数量另行报告。
- **不支持规则标记率**：用 `injections.json` 的 `unsupported` 注入样本（N=3）经 `compile_candidates` 判定，被标 `unsupported` 者 / 注入总数，须 **100%**；同时报告真实抽取中所有非封闭类型候选是否均被标 `unsupported`。

## 4. AC-029 真实图片识别（真实多模态，本地 base64，不经 OSS）

- 对 `tests/fixtures/invitations/shanghai_supplier_conference.png` 用 `RealInvitationExtractor` 抽取。
- 判定口径（S-11）：城市 = 上海；日期 = 2026-10-28 ～ 2026-10-30；事由**包含**「供应商大会」即一致；地址去除空白后须**同时包含**「滨江大道2727号」和「星衡国际会议中心」。
- 结果仍须员工确认后才写入 Trip（确认流程由 AC-003 确定性验证）。
- 证据：模型名称、脱敏请求摘要（不含图片原始字节与密钥）、抽取 JSON。

## 5. AC-020 黄金路径耗时（SHOULD，真实模型）

- 与 AC-019 同一台机器；真实模型模式。
- 计时边界：从员工**显式提交**（最后一个必填槽位已齐，D-13）到**审批卡事件**（审批卡可获取 / `approval_requested`）。
- 运行 3 次，取**中位数 ≤ 90 秒**；同时报告最大值、机器信息、模型名称与计时边界。
- Fake/Recorded 模式计时不得作为本项证据（D-08）。可为 passed / blocked / 有理由的 degraded，不得虚报。

## 6. 台账与授权

- 每次真实调用自动追加 `artifacts/llm-usage.jsonl`；运行后执行 `uv run --offline python scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。
- 仅核对环境变量名 `DASHSCOPE_API_KEY` 是否存在，绝不读取/打印/提交其值（F-03/HC-07）。
- 累计上限 50 元，达 40 元暂停并请示（AUTH-01）。
