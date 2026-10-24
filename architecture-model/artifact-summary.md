# 产出物清单（artifact-summary.md）

> 生成日期：2026-10-23
> 生成技能：`architecture-visualization` 插件 —— `explore`（路由）→ `system-modeler`（主）+ `c4model` + `graphviz`（格式基础），套用 `business-application-fitness` 参考
> 目标：`travel-compliance-desk`（差旅合规与审批工作台）
> 范围：**current-state only**（不含 target / proposed 架构）
> 目录：`architecture-model/`（项目根下新建，**未提交**，不触碰 `docs/` 与 `artifacts/acceptance/` 的既有证据）

---

## 1. 产出物一览

| 文件 | 大小 | 回答什么问题 | 格式 / 查看方式 |
|---|---|---|---|
| `business-architecture.md` | 24 KB | **主交付物**。业务上做什么？谁能做什么？领域实体、规则词汇表、关键路径、agent 注意事项 | Markdown，直接读 |
| `system-model.summary.md` | 14 KB | 模型总览：系统边界、容器、组件分层、三个可替换适配层、证据强度、未知项、维护说明 | Markdown，直接读 |
| `system-model.structurizr.dsl` | 17 KB | L1 系统上下文 / L2 容器 / L3 组件 / L3 模型边界（4 个视图） | Structurizr DSL —— 用 Qoder 的 DSL 查看器打开，或贴到 structurizr.com/dsl |
| `system-model.evidence.md` | 19 KB | 每个节点与边的文件行号、置信度、假设、7 项未知与验证任务 | Markdown，取证时查 |
| `golden-path-flow.dot` | 9 KB | 一次差旅申请从员工开口到收到行程单的全部业务步骤（含降级分支与横切关注点） | Graphviz DOT —— 用 Qoder 的 DOT 查看器打开 |
| `trip-lifecycle.dot` | 5 KB | 10 个状态、14 个事件如何流转；revision 何时递增；异常与自环路径 | Graphviz DOT |
| `module-layers.dot` | 7 KB | 谁 import 谁（穷举计数）；依赖方向；1 个环 + 3 处层序倒置；4 条结构性质 | Graphviz DOT |
| `artifact-summary.md` | 本文 | 产出物索引与取证方法 | Markdown |

**渲染说明**：本机未安装 Graphviz（`which dot` 无结果），三个 `.dot` 文件**未导出 SVG/PNG**。DOT 源文件本身就是交付物（按 `architecture-contract.md`：源文件是真值，渲染结果是派生产物）。需要图片时：`winget install Graphviz.Graphviz` 后 `dot -Tsvg <file>.dot -o <file>.svg`。

所有文件已通过花括号配平检查（DOT 7/7、7/7、6/6；DSL 29/29）。

---

## 2. 核心结论（五句话版本）

1. **系统边界内是一个单进程 FastAPI 应用 + 一个无构建步骤的原生 ES module 前端 + 两个物理分离的 SQLite 库**；默认配置下对外部系统的调用数为 **0**，由进程内网络守卫的空允许清单物理强制（AC-021 已取证）。

2. **最重要的架构性质是「模型只做理解与文字生成，金额/合规结论/候选选择/状态转换全部由代码决定」**，且这不是文档声明而是三重强制：违规与理由码是 `Literal` 封闭枚举（模型无法产出新类型）、`domain/` 在依赖图上零外部依赖（规则核心物理上无法被模型层影响）、模型输出经代码终审（无效引用降级为拒答、含金额的摘要直接丢弃）。

3. **三个可替换适配层**（LLM 三模式 / Provider 双模式 / 检索三方式）共享完全相同的下游代码路径，且每层的确定性变体都是零网络的 —— 这使 36/36 场景、319 个测试、全部演示都不依赖外部服务可用性，同时切到真实模式不会绕过任何关卡（AC-027 已验证结果与确定性基线逐项一致）。

4. **依赖方向总体健康**：四层结构、`domain/` 为纯核心、`network_guard` 是横切边界而非依赖节点。结构上有 **1 个环**（`services ↔ workflow`，且 services 侧只有 `conversation.py` 一个文件参与）与 **3 处层序倒置**（`llm/` 导入 `db.seed.CITY_TIERS`、`services.idempotency.fingerprint`、`planning.plan.Plan/PlanSummary`）—— 导入的都是参考数据、哈希工具与输出契约，不是业务逻辑。

5. **证据密度异常高**：每条结构主张可落到文件行号，行为主张有 319 测试 + 30 项验收 + `docs/architecture.md` 的 IMP-01～IMP-96 决策日志（区分「实施负责人决定」与「所有者决定」）支撑。唯一的行为未知项集中在 Live Provider 真实响应，且已被契约正确标记为 `blocked` 而非伪装成功。

---

## 3. 取证方法（可复现）

| 方法 | 用途 | 命令 / 位置 |
|---|---|---|
| 穷举 import 统计 | 模块依赖图的全部边与计数 | `cd src/travel_desk && for d in api services workflow domain planning policy providers llm db; do grep -rhoP "from travel_desk\.\K[a-z_]+" $d/*.py \| sort \| uniq -c \| sort -rn; done` |
| 路由穷举 | 接口面（38 个端点） | `grep -rn "@router\.\(get\|post\|patch\|put\|delete\)(" api/` |
| 直接读源码 | 状态机、规则枚举、图节点、Schema、守卫、Provider | `domain/states.py`、`domain/rules.py:25-27`、`domain/policy.py:16-23`、`workflow/graph.py:285-305`、`db/schema.py`、`network_guard.py`、`providers/{base,demo,live}.py` |
| 交叉核对权威文档 | 验证代码与文档一致 | `docs/architecture.md`（含 IMP-01～IMP-96）、`README.md`、`docs/acceptance.md` |
| 环与倒置精确定位 | 把结构事实收窄到具体文件与符号 | `grep -rn "from travel_desk.workflow" services/*.py` 等三组 |

**结构证据 vs 行为证据已严格区分**（按 `business-application-fitness` 要求）：表/字段/枚举只确认数据形状；状态流转、事务、幂等、降级、权限这些**行为**主张另由 `apply_transition` 的守卫 UPDATE、触发器 DDL、唯一索引、以及 AC-006/009/010/011/013/019 的验收结果支撑。

---

## 4. 需要你决定的一件事

取证中发现 `docs/acceptance.md`「契约范围与扩展说明」章节（我在上一轮按 B 方案提交的那段）把 Live Provider 描述为**「骨架（返回 `ProviderUnavailable`）」**，与代码不符。

`providers/live.py` 是 **356 行的完整实现**：

- `LiveTransportProvider` —— Kiwi MCP JSON-RPC over SSE，航段拆分、舱位归一化（`_CABIN_MAP` 12 项映射）、价格转整数分、产出 `TransportCandidate(source="live")`
- `HybridHotelProvider` —— **带价候选只来自本地 fixture 目录**，Tavily 仅用于「无价参考」（title/url/snippet/score），永不产出带价候选（这是刻意的安全设计，docstring 明确写了）
- `LiveWeatherProvider` —— QWeather 城市查找 + `30d/15d/7d` 分级回退（402 降级）；无凭据时退回 `_QWeatherStub`
- `tests/test_live_providers.py` —— 用 `AsyncMock`/`patch` 覆盖解析与 `ProviderTimeout`/`ProviderUnavailable`/`ProviderInvalidResponse` 三类错误路径

**准确表述应为**：「Live Provider 已实现并有 mock 单测覆盖解析与错误处理，但**从未对真实外部服务验证过**（AUTH-02 未授权，AC-028 `blocked`）」—— 即**结构完整、行为未验证**，而非骨架。

这个区别有实质影响：说「骨架」既低估了已完成的工作量，也会让读者以为切到 live 模式什么都拿不到（实际上会真的去调 Kiwi/Tavily/QWeather，只是没人验证过返回结果对不对）。

详见 `system-model.evidence.md` §2.1 与 `system-model.summary.md` §7。**要我修订 acceptance.md 那一段吗？** 我不会未经你同意改动已提交的验收文档。

---

## 5. 未知项（validation tasks）

| # | 未知项 | 现状 | 验证步骤 |
|---|---|---|---|
| U-1 | Live Provider 真实响应 | **unknown（顶层限制）** | 授权 AUTH-02 后运行 `scripts/test_ac028_integration.py`；Kiwi 不覆盖国内路线不得伪装成功 |
| U-2 | 「待人工补录」后续流程 | **代码未实现**。`docs/DESIGN.md` 有设计，但 `domain/states.py` 的 10 个状态里没有 `PENDING_MANUAL_ENTRY` | 需先决定归属（契约 v1.2 还是契约外扩展）。当前契约规定的处理方式 `PLAN_INCOMPLETE` + 进入审批**已实现且已验收** |
| U-3 | 真实邮件投递 | 未接（有意）；终点是本地 `outbox_emails` 表 | 契约明确不连真实邮件系统 |
| U-4 | 多进程部署并发正确性 | unknown（超出当前部署形态）。锁都是进程内的，跨进程只靠数据库状态守卫与唯一索引兜底（IMP-30 已声明） | 多进程部署前实测 `UPDATE ... WHERE status='pending'` 与 `unique_key` 的并发表现 |
| U-5 | 认证 | 不适用。`X-Demo-User` 只是演示身份切换 | 生产化需替换（契约范围内的有意省略，非缺陷） |
| U-6 | `services ↔ workflow` 环的实际影响 | 结构已确认（high），**影响未评估** | 属 `dependency-impact-analyzer` / `risk-quality-reviewer` 职责；本模型只报告结构事实 |
| U-7 | `hybrid` 检索是否有价值 | **已决策关闭**。AC-025 实测 Recall@3 两者均 1.0000 打平，按 D-17 保留纯向量 | 无需验证；代码路径保留但非交付配置 |

---

## 6. 本模型**没有**覆盖的（按 MECE 边界主动排除）

`system-modeler` 的职责是「结构与边界」，以下问题需要另外的场景技能，本次**未做**：

| 未覆盖 | 应路由到 |
|---|---|
| 风险登记、技术债、质量属性评估、`services ↔ workflow` 环是否算问题 | `risk-quality-reviewer` |
| 某个改动的影响半径（例如「改 `domain/rules.py` 会波及什么」） | `dependency-impact-analyzer` |
| 运行时部署拓扑（进程、端口、HF Space 部署形态、`deploy/hf-space/` 那份副本与主源码的关系） | `deployment-topology-analyzer` |
| 演进路线（例如「怎么把 U-2 的人工补录实现进去」「怎么生产化」） | `evolution-planner` |
| 面向特定受众（面试官 / 评审）的讲解版本 | `architecture-communicator` |
| 图与代码是否仍然同步（living architecture 校验） | `architecture-health` |

其中**部署拓扑**值得单独一提：仓库里有 `deploy/hf-space/src/travel_desk/` 这份源码副本，本次未分析它与主源码的同步关系（是 vendored 副本、构建产物，还是会漂移的分支）。如果你要部署或要说明部署形态，这需要 `deployment-topology-analyzer` 单独跑一次。
