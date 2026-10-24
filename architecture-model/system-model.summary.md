# 架构模型说明（system-model.summary.md）

> 生成技能：`architecture-visualization:system-modeler`（主）+ `c4model`（Structurizr DSL 基础）+ `graphviz`（DOT 基础），经 `explore` 路由，套用 `business-application-fitness` 参考。
> 范围：**current-state only**。生成日期 2026-10-23。
> 目标仓库：`travel-compliance-desk`（差旅合规与审批工作台）。

---

## 1. 这套模型回答什么问题

| 文件 | 回答的问题 | 格式 |
|---|---|---|
| `system-model.structurizr.dsl` | 系统边界外有谁？内部有哪些可独立拥有的单元？单进程应用内部模块职责如何划分？模型能影响什么、不能影响什么？ | Structurizr DSL（4 个视图） |
| `business-architecture.md` | 这个系统在**业务上**做什么？谁能做什么？领域实体与规则词汇表是什么？关键路径怎么走？ | Markdown（主交付物） |
| `golden-path-flow.dot` | 一次差旅申请从员工开口到收到行程单，经过哪些业务步骤？降级与异常分支在哪？ | Graphviz DOT |
| `trip-lifecycle.dot` | 10 个状态、14 个事件如何流转？revision 什么时候递增？ | Graphviz DOT |
| `module-layers.dot` | 谁 import 谁？依赖方向是否健康？有哪些环与层序倒置？ | Graphviz DOT |
| `system-model.evidence.md` | 每个主张的文件行号、置信度、假设与未知项 | Markdown |

**建议阅读顺序**：`business-architecture.md` → `golden-path-flow.dot` → `trip-lifecycle.dot` → DSL 的 L2/L3 视图 → `module-layers.dot` → `system-model.evidence.md`。

---

## 2. 系统边界

**一个软件系统**（Travel Compliance Desk）、**三类角色**（员工 / 直属经理 / 管理员）、**两组外部系统**（DashScope 模型服务 / Live Provider 组）。

边界上最重要的事实：**默认配置下对外部系统的调用数为 0**。这不是「测试时不打网络」的约定，而是 `network_guard` 在进程内给四条出站途径打补丁、允许清单为空集**物理强制**的结果（AC-021 已取证 `external_attempts=0`，12 场景 / 90 断言）。两组外部系统都只在显式切换模式后才可达：

- `llm_mode=real` → 只允许 `dashscope_base_url` 的主机名（AUTH-01 已授权，累计 2.49 元 / 上限 50 元）
- `provider_mode=live` → 追加 `mcp.kiwi.com`、`api.tavily.com`、QWeather 主机（AUTH-02 **未授权**，AC-028 `blocked`）

允许清单**由配置推导，没有运维开关** —— 这是一个刻意的架构选择：不能在运行时被放宽。

---

## 3. 容器（L2）：一个前端、一个单进程应用、两个 SQLite 库、三类文件

| 容器 | 技术 | 要点 |
|---|---|---|
| 工作台 SPA | 原生 HTML/CSS/ES module，**无构建步骤、无第三方前端依赖** | 8 个模块；文本一律以文本节点插入（从不 `innerHTML`）；金额与合规结论直接显示服务端字段、前端不重算 |
| FastAPI 应用 | Python 3.11 / FastAPI / uvicorn / LangGraph，**单进程** | 承载全部业务逻辑；lifespan 顺序：装守卫 → 初始化库 → 打开 Workflow → **启动恢复** → 开始服务 |
| 业务数据库 | SQLite (WAL)，`var/app.sqlite` | 13 张表；金额列全为整数分；**权威状态源** |
| Checkpoint 库 | SQLite / `AsyncSqliteSaver`，`var/checkpoints.sqlite` | LangGraph 中断点；**可丢弃** |
| 上传存储 | 文件系统，`var/uploads/` | 服务端生成文件名；类型按**文件头魔数**判定，不信任文件名与 Content-Type |
| 确定性 Fixture | JSON/Markdown/PDF，`tests/fixtures/` | fake 与 recorded 模式的唯一外部数据来源 |
| 费用台账 | JSONL + Markdown，`artifacts/` | 40 元暂停线；台账不可读也拒绝（fail closed） |
| 守卫日志 | JSONL，`var/network-guard.jsonl` | 每次出站尝试一行，含 `decision` 与 `caller` |

### 双库分离是有架构后果的（不是随便分了两个文件）

`workflow/engine.py:301-306`：图中断点不存在时（例如 checkpoint 库丢失），`decide()` **不失败**，而是直接调用同一个 `apply_decision()` + `deliver()`，审计标 `via="direct"`（图路径标 `via="graph"`）。

⇒ **checkpoint 库整体丢失不会让待审批的申请无法处理**，因为业务库才是权威。AC-010 用真实子进程 `taskkill /F /T` 强制终止 + 同库重启验证了这条恢复路径。

---

## 4. 组件（L3）：依赖方向自上而下，`domain/` 是零外部依赖的纯核心

10 个组件，分四层：

```
L1 接口      api/                     薄路由 + SSE + 错误体 + trace_id
L2 编排/用例  workflow/  services/     事务边界在 services
L3 能力适配   planning/ policy/ llm/ providers/    全部可替换实现
L4 核心/基础  domain/  db/  network_guard  config
```

四条**可复核的结构性质**（穷举 import 统计得出，不是观感）：

1. **`domain/` 只导入自身**（8 处自引用、0 处向外）。这是「金额、合规结论、候选选择、状态转换全部由代码决定」（HC-01/HC-02）在依赖结构上的落点 —— 规则核心物理上无法被模型层、Provider 层或存储层影响。
2. **`providers/` 只导入 `domain/`**（值类型）。所以 demo（fixture）与 live（真实 HTTP）两套实现可整体替换，替换面被 Protocol + 共享 Pydantic 值类型收窄。
3. **`network_guard` 不被任何业务模块 import**，由 `main.py` 的 lifespan 安装猴子补丁。它是**横切边界而非依赖节点** —— 业务代码既无法绕过它，也无法感知它。
4. **唯一的模块环是 `services ↔ workflow`，且 services 侧只有 `conversation.py` 一个文件参与**（它需要 `SubmitRun` 以复用同一套 SSE 流语义）。其余 12 个 services 模块都不导入 workflow。

另有三处**层序倒置**（`llm/` 向上/旁导入），已在图中标注实际导入的符号与用途：

| 边 | 导入的符号 | 观察到的用途 |
|---|---|---|
| `llm/slots.py → db/` | `db.seed.CITY_TIERS` | fake 正则解析器的城市名单（参考数据） |
| `llm/slots.py`、`llm/summary.py → services/` | `services.idempotency.fingerprint` | recorded 模式的缓存键（哈希工具） |
| `llm/summary.py → planning/` | `planning.plan.Plan, PlanSummary` | 摘要的输出契约（类型） |

这三处导入的都是**参考数据、工具函数、输出契约**，不是业务逻辑 —— 结构上确实是倒置，但没有把用例语义泄漏进模型层。

> 按 `system-modeler` 的职责边界，本模型**只报告结构事实**。这些环与倒置是否构成风险、要不要整改、怎么整改，属 `risk-quality-reviewer` / `dependency-impact-analyzer` / `evolution-planner` 的职责，未在此评估。

---

## 5. 三个可替换适配层（这是本系统最重要的扩展点设计）

| 适配层 | 变体 | 切换开关 | 收窄替换面的机制 |
|---|---|---|---|
| **LLM** | `fake`（正则/模板，零网络）/ `recorded`（冻结输出，按 SHA-256 匹配）/ `real`（DashScope） | `TRAVEL_DESK_LLM_MODE` | 四个 Protocol（`SlotExtractor`/`Summarizer`/`InvitationExtractor`/`RuleExtractor`）+ Pydantic 输出 Schema |
| **Provider** | `demo`（fixture，每次重读并逐条 Schema 校验）/ `live`（Kiwi/Tavily/QWeather） | `TRAVEL_DESK_PROVIDER_MODE` | 三个 Protocol + 共享值类型 + 三类错误（Timeout/Unavailable/InvalidResponse） |
| **检索** | `bm25`（手写 Okapi + jieba）/ `vector`（DashScope embedding，1024 维）/ `hybrid`（RRF k=60） | `TRAVEL_DESK_RETRIEVAL`（仅 real 模式生效；fake/recorded 固定 BM25） | `Retriever` 类 + `Retrieval` 值对象 |

**关键设计**：三者的 `fake`/`demo`/`bm25` 变体都是**零网络、确定性**的。这让 36/36 场景、319 个测试、全部演示都不依赖外部服务可用性 —— 可复现性是架构属性，不是测试技巧。

同时，三者的 `real`/`live`/`vector` 变体共享**完全相同的下游代码路径**：模型提议 → Schema 校验 → 代码终审。所以切到真实模式不会绕过任何一道关卡（AC-027 用真实模型跑完整链路，结果与确定性基线逐项一致，16/16 断言通过）。

---

## 6. 证据强度

**整体判断：这是一个证据密度异常高的代码库。**

| 层级 | 强度 | 说明 |
|---|---|---|
| 结构主张（容器、组件、依赖、schema） | **high** | 每条都能落到具体文件行号；import 计数是穷举统计而非抽样 |
| 行为主张（状态流转、事务、幂等、降级、权限） | **high** | 319 个测试 + 30 项验收（29 passed / 1 blocked）+ 12 个确定性场景各 3 次 |
| 真实模型能力（问答指标、规则抽取、图片识别、整链） | **high** | 冻结数据集（SHA-256 已记入 `docs/acceptance.md`）+ 真实运行 + 阈值达标 |
| Live Provider 真实行为 | **unknown** | 代码结构 high，但从未对真实服务发过请求（AUTH-02 未授权） |

`docs/architecture.md` 内含 **IMP-01～IMP-96 决策日志**，每条记录了「决定 + 理由/依据」，且区分了「实施负责人决定（A）」与「所有者决定」。这让「为什么这样设计」也变成了可追溯的证据 —— 在同类项目里很少见。

---

## 7. 需要你决定的一件事：acceptance.md 有一处事实错误

我在本次取证中发现，先前提交的 `docs/acceptance.md`「契约范围与扩展说明」章节把 Live Provider 描述为**「骨架（返回 `ProviderUnavailable`）」**，这与代码不符：

| 项 | acceptance.md 当前表述 | `providers/live.py` 实际（356 行） |
|---|---|---|
| 交通 | 骨架，返回 `ProviderUnavailable` | 完整实现：Kiwi MCP JSON-RPC over SSE、航段拆分、舱位归一化（`_CABIN_MAP` 12 项）、价格转整数分 |
| 酒店 | 骨架 | `HybridHotelProvider`：**带价候选只来自本地 fixture**，Tavily 仅用于无价参考（title/url/snippet），永不产出带价候选 |
| 天气 | 骨架 | QWeather 城市查找 + 30d/15d/7d 分级回退；无凭据时退回 `_QWeatherStub` |
| 测试 | 未提及 | `tests/test_live_providers.py` 用 mock HTTP 覆盖解析与三类错误路径 |

**准确表述应为**：「Live Provider 已实现并有 mock 单测覆盖解析与错误处理，但**从未对真实外部服务验证过**（AUTH-02 未授权，AC-028 `blocked`）」—— 即**结构完整、行为未验证**，而非骨架。

这个区别对交付评审有实质影响：说「骨架」会低估已完成的工作量，也会让读者以为切到 live 模式什么都拿不到（实际上会真的去调 Kiwi/Tavily/QWeather，只是没人验证过结果对不对）。**建议修订该段落**，我可以代做。

---

## 8. 未知项（validation tasks）

| # | 未知项 | 验证步骤 |
|---|---|---|
| U-1 | Live Provider 真实响应（**顶层限制**） | 授权 AUTH-02 后运行 `scripts/test_ac028_integration.py`；Kiwi 不覆盖国内路线不得伪装成功 |
| U-2 | 「待人工补录」的后续补录流程 | `docs/DESIGN.md` 有设计但**代码未实现**（`domain/states.py` 10 个状态里没有 `PENDING_MANUAL_ENTRY`）。需先决定归属契约 v1.2 还是契约外扩展 |
| U-3 | 真实邮件投递 | 未接（有意）；终点是本地 Outbox 表 |
| U-4 | 多进程部署下的并发正确性 | 锁都是进程内的；跨进程只靠数据库状态守卫与唯一索引兜底（IMP-30 已声明）。多进程部署前需实测 |
| U-5 | 认证 | `X-Demo-User` 只是演示身份切换，不是认证（README 明确声明） |

---

## 9. 如何查看图形

**本机未安装 Graphviz**（`which dot` 无结果），所以 `.dot` 文件**没有渲染成 SVG**。三个 DOT 文件是**可维护的源文件**，本身就是交付物：

- **在 Qoder 里看**：用 Qoder 的 DOT 格式查看器打开 `golden-path-flow.dot` / `trip-lifecycle.dot` / `module-layers.dot`；用 Structurizr DSL 查看器打开 `system-model.structurizr.dsl`（含 4 个视图：`L1-SystemContext`、`L2-Containers`、`L3-FastAPI-Components`、`L3-ModelBoundary`）。
- **要 SVG/PNG 导出**：先装 Graphviz（`winget install Graphviz.Graphviz`），然后 `dot -Tsvg golden-path-flow.dot -o golden-path-flow.svg`。导出的 SVG 是**派生产物**，不要手改 —— 改动应回到 `.dot` 源文件。
- **要在线看 Structurizr**：把 `.dsl` 内容贴到 Structurizr Playground（`https://structurizr.com/dsl`）。

三个 DOT 都用 `rankdir=TB`（密集关系图在 Canvas 里竖向预览更合适，横向容易超出可视区）。

---

## 10. 维护说明

本模型是**快照**，不是活文档。以下变更发生后需要重新生成：

- `domain/states.py` 的 `_TRANSITIONS` 表变化 → 重画 `trip-lifecycle.dot`
- 新增/删除 `src/travel_desk/` 下的模块，或模块间 import 关系变化 → 重跑 import 穷举统计并重画 `module-layers.dot`
- `workflow/graph.py` 增删节点或改条件边 → 重画 `golden-path-flow.dot` 并更新 DSL 的 `workflow` 组件描述
- `db/schema.py` 增删表或改约束 → 更新 `business-architecture.md` §4.1 与 DSL 的 `appDb` 描述
- AUTH-02 授权并完成 AC-028 → 把 `liveProviders` 从 `Unverified` 改为已验证，并关闭 U-1

重新生成 import 计数的命令：

```bash
cd src/travel_desk && for d in api services workflow domain planning policy providers llm db; do
  echo "=== $d ==="; grep -rhoP "from travel_desk\.\K[a-z_]+" $d/*.py | sort | uniq -c | sort -rn
done
```
