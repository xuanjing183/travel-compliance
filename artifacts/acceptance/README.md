# 验收证据索引（REQ-030）

> 本文件是契约 REQ-030 要求的「验收证据索引」。逐条验收状态、命令与说明的权威表在
> [docs/acceptance.md](../../docs/acceptance.md)；本索引按 **AC** 与 **TASK** 两个维度导航到
> `artifacts/acceptance/` 下的具体证据目录。门控评测（AC-004/017/018/025/029）依 D-08 用
> **真实模型**验收；AUTH-01 已批准，故均为 `passed`。

## 1. 按验收标准（AC）导航

| AC | 状态 | 证据目录 / 文件 | 任务 |
|---|---|---|---|
| AC-001 安装与启动 | 见 acceptance.md | `TASK-012/`（全新克隆 + `uv sync` + 启动 + 健康 + 工作台截图） | TASK-012 |
| AC-002 槽位接入（S-05） | passed | [TASK-006/AC-002](TASK-006/AC-002/) | TASK-006 |
| AC-003 图片入口（API） | passed | [TASK-006/AC-003](TASK-006/AC-003/)；界面 [TASK-007](TASK-007/README.md) 截图 18 | TASK-006/007 |
| AC-004 制度问答质量 | passed（门控·真实） | [TASK-011/ac004-qa](TASK-011/ac004-qa/README.md)（`full-vector.json`） | TASK-011 |
| AC-005 制度上传与发布 | passed | [TASK-008/AC-005](TASK-008/AC-005/) | TASK-008 |
| AC-006 规则核心 | passed | [AC-006](AC-006/README.md)（84 用例） | TASK-003 |
| AC-007 Provider 与选择器 | passed | [AC-007](AC-007/README.md)（35 用例 + `plans/`） | TASK-004 |
| AC-008 审批卡 | passed | [TASK-005](TASK-005/README.md)；界面 [TASK-007](TASK-007/README.md) 截图 08 | TASK-005/007 |
| AC-009 权限/revision/幂等 | passed | [TASK-006/AC-009](TASK-006/AC-009/) | TASK-006 |
| AC-010 重启恢复（真实终止） | passed | [TASK-006/AC-010](TASK-006/AC-010/) | TASK-006 |
| AC-011 交付与通知 | passed | [TASK-006/AC-011](TASK-006/AC-011/) | TASK-006 |
| AC-012 审计完整性 | passed | [TASK-010/ac012-audit-integrity](TASK-010/ac012-audit-integrity/README.md) | TASK-010 |
| AC-013 故障注入降级 | passed | [TASK-010/ac013-fault-injection](TASK-010/ac013-fault-injection/README.md) | TASK-010 |
| AC-014 角色权限矩阵 | passed | [TASK-007](TASK-007/README.md)（截图 01/03–07/09/13/17 + 服务端检查） | TASK-007 |
| AC-015 来源标识 | passed | [TASK-007](TASK-007/README.md#ac-015-json-扫描)；真实 [TASK-007/AC-015-real](TASK-007/AC-015-real/) | TASK-007 |
| AC-016 Demo 重置 | passed | [TASK-010/ac016-demo-reset](TASK-010/ac016-demo-reset/README.md) | TASK-010 |
| AC-017 引用/数值口径 | passed（门控·真实） | [TASK-011/ac004-qa](TASK-011/ac004-qa/README.md)（与 AC-004 同次运行） | TASK-011 |
| AC-018 规则抽取质量 | passed（门控·真实） | [TASK-011/ac018-rules](TASK-011/ac018-rules/README.md)（`real-v1.2.json`） | TASK-011 |
| AC-019 确定性 12 场景 | passed | [TASK-011/ac019-scenarios](TASK-011/ac019-scenarios/README.md)（`e2e-report.json` 36/36） | TASK-011 |
| AC-020 黄金路径耗时 | passed（SHOULD·真实） | [TASK-011/ac020-timing](TASK-011/ac020-timing/README.md)（中位数 4.204s） | TASK-011 |
| AC-021 Demo 网络边界 | passed | [TASK-010/ac021-network-boundary](TASK-010/ac021-network-boundary/README.md)；IMP-97 修复后重跑 [TASK-013/ac021-post-fix](TASK-013/ac021-post-fix/) | TASK-010/013 |
| AC-022 密钥与日志扫描 | passed | [TASK-010/ac022-secret-scan](TASK-010/ac022-secret-scan/README.md) | TASK-010 |
| AC-023 偏好影响规划 | passed | [TASK-009/AC-023](TASK-009/AC-023/)；规划层 [AC-007/plans](AC-007/plans/) | TASK-009 |
| AC-024 制度变更后重新校验 | passed | [TASK-008/AC-024](TASK-008/AC-024/)；[TASK-009/AC-024](TASK-009/AC-024/) | TASK-008/009 |
| AC-025 检索对比 | passed（门控·真实） | [TASK-011/ac025-retrieval](TASK-011/ac025-retrieval/README.md)（vector vs hybrid） | TASK-011 |
| AC-026 文档与演示 | 见 acceptance.md | [TASK-012/ac026-rehearsal](TASK-012/ac026-rehearsal/README.md)（第一次演练：计时与 8 张截图）；IMP-97 修复后复演 [TASK-013/ac026-rehearsal-post-fix](TASK-013/ac026-rehearsal-post-fix/README.md)（10 张截图 + 10 份 API 原始响应：9 份 JSON + 1 份复位核对 TXT；S-01/S-09 界面与 API 双核对） | TASK-012/013 |
| AC-027 真实模型路径（SHOULD） | passed（真实·整链） | [TASK-013/ac027-fullchain](TASK-013/README.md)（`fullchain-report.json` 16/16）；修复后复核 [ac027-post-imp97-fix](TASK-013/ac027-post-imp97-fix/) | TASK-013 |
| AC-028 Live Provider | blocked | —（AUTH-02 未授权） | — |
| AC-029 真实图片识别 | passed（门控·真实） | [TASK-011/ac029-vision](TASK-011/ac029-vision/README.md)（`real-s11.json`） | TASK-011 |

## 2. 按任务（TASK）导航

| 任务 | 证据目录 | 覆盖 |
|---|---|---|
| TASK-003 规则核心 | [AC-006](AC-006/) | 金额/规则/状态机单元测试 |
| TASK-004 Provider 与选择器 | [AC-007](AC-007/) | Provider 响应、排序、伪造/改价拦截、`plans/` |
| TASK-005 工作流与审批 | [TASK-005](TASK-005/) | 服务层审批卡、状态机、幂等 |
| TASK-006 API/SSE/权限 | [TASK-006](TASK-006/)（AC-002/003/009/010/011 子目录） | HTTP 层权限、重启恢复、交付通知 |
| TASK-007 三角色工作台 | [TASK-007](TASK-007/)（含 `AC-015-real/`） | 浏览器黄金路径、角色守卫、来源标识、截图 |
| TASK-008 制度编译与问答 | [TASK-008](TASK-008/)（AC-005/AC-024） | 上传/抽取/审核/发布、S-12 重新校验 |
| TASK-009 偏好与重新校验 | [TASK-009](TASK-009/)（AC-023/AC-024） | 偏好三段、制度变更后重新校验 |
| TASK-010 可靠性与边界 | [TASK-010](TASK-010/)（ac012/ac013/ac016/ac021/ac022） | 审计完整性、故障注入、Demo 重置、网络边界、密钥扫描 |
| TASK-011 评测与验收自动化 | [TASK-011](TASK-011/README.md)（ac004/ac018/ac019/ac020/ac025/ac029） | 门控真实评测、确定性场景、耗时；汇总报告见该目录 README |
| TASK-012 文档与演示交付 | `TASK-012/` | AC-001 安装启动、AC-026 两段式演练 |
| TASK-013 真实模型整链 | [TASK-013](TASK-013/README.md)（`ac027-fullchain/`、`ac027-fullchain-spare/`、`ac027-post-imp97-fix/`、`ac021-post-fix/`、`ac026-rehearsal-post-fix/`） | AC-027 自然语言 → 真实槽位抽取 → 真实摘要 → 审批卡；留证 IMP-97 被实际触发，以及修复（`ca3a9f7`）后的 AC-021 重跑、real 模式 `decision: allowed` 首次取证，与修复后重走两套演示脚本（浏览器路径 + `blocked=0` 未误伤核对） |

## 3. 冻结评测数据集（D-17 / §8.2）

门控评测使用的题集、gold 规则与注入样本已在**首次授权门控真实运行前冻结**，冻结后不得修改。
口径见 [evals/METRICS.md](../../evals/METRICS.md)，清单见 [evals/FROZEN.md](../../evals/FROZEN.md)。

| 文件 | SHA-256 |
|---|---|
| `evals/qa_set.json` | `469c17a4f1adcfab13a9a8bcf677737cdaf0ed8a74900b9547fef3352da880f6` |
| `evals/gold_rules_v1.2.json` | `686d34e07dcc4537fa476a4ca97eaca3053bb3e93de8f9b85a9360742b2a6f83` |
| `evals/injections.json` | `c8e72b1546e1b39eded0026256fbacdba0734a0457def62b8c4cc2df6d278de6` |
| `evals/METRICS.md` | `14ad2a2b47f994da2db81011d150e6e8971b814856e3bae0c4f1cbb334290530` |

校验：`sha256sum evals/qa_set.json evals/gold_rules_v1.2.json evals/injections.json evals/METRICS.md`。

## 4. 评测与验收脚本

| 脚本 | 覆盖 |
|---|---|
| `scripts/eval_qa.py` | AC-025（`--stage retrieval`）+ AC-004/017（`--stage full`） |
| `scripts/eval_rules.py` | AC-018 规则抽取 recall / 拦截率 / precision |
| `scripts/eval_vision.py` | AC-029 真实图片识别 |
| `scripts/eval_timing.py` | AC-020 黄金路径耗时（真实子进程 + 中位数） |
| `scripts/eval_fullchain.py` | AC-027 真实模型整链 S-01（A 段 fake 实测确定性基线 + B 段 real 整链，同一断言集对比） |
| `scripts/e2e_scenarios.py` | AC-019 确定性 12 场景（fake 子进程）；同一运行也是 AC-021 的负向取证（`--mode fake` 下 `external_attempts=0`） |
| `scripts/ac012_audit.py` / `ac013_faults.py` / `ac016_reset.py` | AC-012 / AC-013 / AC-016 |
| `scripts/source_scan_real.py` | AC-015 真实模式来源扫描 |
| `scripts/ledger_summary.py` | 运行期费用汇总到 `artifacts/cost-ledger.md` |

真实调用一律经 `UsageMeter` 守卫并记账到 [artifacts/llm-usage.jsonl](../llm-usage.jsonl)，
汇总到 [artifacts/cost-ledger.md](../cost-ledger.md)（AUTH-01；脚本只核对密钥变量名存在，
从不读取/打印/提交其值，F-03/HC-07）。

## 5. 真实性与诚实声明

- 门控项（AC-004/017/018/025/029）与 AC-020、AC-027 用**真实模型**，非回放：AC-029 真实视觉输出与录制
  fixture 措辞不同而语义一致；AC-018 的 8 条 gold 与模型候选一一对应、注入样本由确定性编译产生；
  AC-027 的真实摘要文本与 fake 模板措辞不同，且台账逐次记录 `slot_extraction` 与
  `itinerary_summary` 两类真实调用。
- 模拟 / 占位 / 真实集成明确区分：fake 输出标 `generator="fake:*"`、recorded 标 `hand_written`，
  均**不得**作为质量指标结论（D-08）。
- 未接 / 阻塞如实标记：AC-028（Live Provider）`blocked`（AUTH-02 未授权）。AC-027 已由 TASK-013
  的整链真实模型运行覆盖，不再是 `not_run`；AC-020/AC-027 均有真实运行证据，仅 AC-028 因
  AUTH-02 阻塞。已知限制的完整分类见 [docs/acceptance.md](../../docs/acceptance.md)。
- 真实运行中发现的缺陷如实留证：TASK-013 的 `real/network-guard.jsonl` 记录了 IMP-97
  （bytes 主机名被判为 `local`）被**实际触发**，而非仅潜在；所有者复核 diff 后批准按 fail-closed 修复，
  已落地（commit `ca3a9f7`，单测 14→50、全量 319 passed、AC-021 重跑结论不变、real 模式首次取证
  `decision: "allowed"`）。修复状态与四项技术决定见 [docs/integration.md](../../docs/integration.md) §4；
  修复前的三份守卫日志（AC-020 一份、AC-027 两份）**按所有者决定保持原样不改写**，改在
  `docs/acceptance.md` 对应行加注解。
