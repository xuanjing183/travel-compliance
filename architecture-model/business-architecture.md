# 业务架构说明（business-architecture.md）

> 面向读者：需要理解「这个系统在业务上做什么、边界在哪、谁能做什么」的人 —— 产品、评审、接手实现的工程师，或需要在此仓库安全工作的 AI agent。
> 视角：**current-state**（当前实现），不含 target/proposed。
> 取证方式：`system-modeler` + `business-application-fitness` 参考；逐条 sourceRefs 见 `system-model.evidence.md`。
> 技术分层与容器视图见 `system-model.structurizr.dsl`；流程与状态机见 `golden-path-flow.dot`、`trip-lifecycle.dot`。

---

## 1. 架构摘要

**Travel Compliance Desk 是一个把「写在文档里的差旅制度」编译成「可执行、可追溯的规则」的合规审批系统。**

它解决的业务问题是：50～500 人的中小企业把差旅制度放在 Word/Markdown/PDF 里，员工靠自己翻制度、审批人靠经验判断、财务到报销阶段才发现超标。本系统把判定前移到**规划阶段** —— 员工还没提交就已经知道成本、是否超标、需要谁批。

### 系统最重要的架构性质：模型与代码的职责被硬性分开

这不是文档上的原则声明，而是**在类型系统与依赖结构上都被强制**的：

| 模型（LLM）被允许做的 | 代码独占的 |
|---|---|
| 从自然语言里**提议**槽位值 | 槽位格式校验与规范化 |
| 从邀请函图片里**提议**字段 | 是否写入 Trip（须员工确认） |
| 写一段行程摘要文字 | 金额计算（整数分） |
| 回答制度问题并**指认**引用编号 | 合规结论、违规判定、审批理由 |
| 从制度全文**提议**候选规则 | 候选选择与排序、防伪造校验 |
| — | 状态机转换、引用是否有效 |

三条强制机制（high confidence，均可复核）：

1. **封闭枚举由类型系统把关**：`domain/rules.py:25-27` 把违规码、审批理由码、阻断码定义为 `Literal` 三值枚举。模型无法产出一个新的违规类型 —— 不是「提示词要求它不要」，而是解析阶段就不可能。
2. **`domain/` 在依赖图上是零外部依赖的纯核心**：穷举 import 统计显示 `domain/` 只导入自身（8 处自引用、0 处向外）。规则核心物理上无法被模型层、Provider 层或存储层影响。
3. **模型输出经代码终审**：`policy/rag.py:420-435` `finalize()` 只保留模型「实际看到过」的引用块 ID，声称可回答但无有效引用一律降级为拒答并清空引用；`llm/summary.py:76-85` `check_summary()` 把空、超 400 字、含金额的摘要直接丢弃并标 `unavailable`。

### 第二个关键性质：默认配置下对外部世界的调用数为 0

`network_guard.py` 在进程启动时给 `socket.getaddrinfo`、`socket.create_connection`、`socket.socket.connect`、`urllib.request.urlopen` 打补丁。允许清单**由配置推导，没有运维开关**：`llm_mode=fake` + `provider_mode=demo` 时清单为空集，任何外联在 DNS 离开机器前被拒。AC-021 已取证 `external_attempts=0`（12 场景 / 90 断言）。

⇒ 这意味着 **36/36 确定性场景、319 个测试、以及全部演示都不依赖任何外部服务的可用性**。这是可复现性的架构基础，不是测试技巧。

---

## 2. 业务能力（L0）

| 能力 | 业务含义 | 实现落点 | 置信度 |
|---|---|---|---|
| **制度编译** | 管理员上传制度文件 → 系统提议规则候选 → 管理员逐条确认/驳回/标不支持 → 原子发布新版本 | `policy/documents.py`、`policy/extraction.py`、`services/policies.py` | high |
| **信息收集** | 员工用自然语言或表单提供出差信息；系统只追问缺失与非法项，不重复追问 | `services/conversation.py`、`llm/slots.py`、`domain/slots.py` | high |
| **图片录入** | 上传会议邀请函 → 识别 → 员工确认（可改值）后才写入 | `services/uploads.py`、`llm/vision.py` | high |
| **合规判定** | 绑定制度版本 → 硬阻断检查 → 计价 → 违规与审批理由 → 结论 | `domain/rules.py`、`domain/policy.py` | high |
| **行程规划** | 查询交通/酒店/天气候选 → 过滤排序 → 选出候选 → 防伪造校验 → 组装方案与行程骨架 | `planning/planner.py`、`planning/selector.py`、`providers/` | high |
| **审批** | 生成审批卡（成本明细、预算差、理由、条款引用、来源、revision）→ 直属经理决定 | `services/approvals.py`、`workflow/graph.py` | high |
| **交付** | 写行程单 + 交付邮件到 Outbox，失败可重试且不重复发信 | `services/delivery.py` | high |
| **制度问答** | 任意角色按版本提问；可回答必带引用，无依据则明确拒答 | `policy/rag.py`、`services/policy_qa.py` | high |
| **偏好** | 员工设置出行偏好（如回避早班），影响候选排序并可审计其是否真的改变了结果 | `services/preferences.py`、`planning/selector.py` | high |
| **制度变更响应** | 发布新版本后，待审批的旧卡可按新版本重新校验（复用已选候选、不重查供应商） | `services/revalidation.py` | high |
| **可追溯性** | 全过程 append-only 审计：槽位、引用、规则、供应商调用、方案、审批、通知/交付、错误 | `services/audit.py`、`db/schema.py` 触发器 | high |
| **演示可复现** | Demo reset 恢复干净起点；故障注入验证降级；固定时钟 | `api/routes_admin.py`、`db/database.py reset_database` | high |
| **真实外部数据接入** | 交通/酒店参考/天气来自真实服务 | `providers/live.py` | **结构 high / 行为 unknown**（AUTH-02 未授权，见 §7 U-1） |

---

## 3. 参与者与权限边界

### 3.1 三类角色（high confidence）

角色不是从目录名或文件名推断的：`db/schema.py:15` 有 `CHECK (role IN ('employee','manager','admin'))` 约束，服务端在每个用例入口显式校验。

| 角色 | 能做 | 明确不能做 | 证据 |
|---|---|---|---|
| **员工** | 创建/编辑自己的申请、对话补全、上传图片并确认、提交、查询制度、设置自己的偏好、对自己的申请触发重新校验 | 审批任何申请；读写他人偏好；访问管理员接口（403） | `services/access.py`、`services/preferences.py`（只有本人可读写，admin 一律 403） |
| **直属经理** | 上述全部（本人也是员工）+ 审批**直属下属**的申请 | 审批本人的申请（403 `SELF_APPROVAL`）；审批非直属下属（403 `NOT_DIRECT_MANAGER`）；非 manager 角色一律 403 `FORBIDDEN` | `workflow/engine.py:276-281` 三重校验，顺序即 IMP-42 |
| **管理员** | 上传/抽取/审核/发布制度、故障注入、查看 Outbox、重试交付、Demo reset | **创建 Trip**（IMP-20：admin 无对应住宿级别，不能作为申请人参与规则评估）；**审批**（`require_manager` 只允许 `manager`，IMP-08 明确防管理员绕过直属审批）；上传业务图片（403） | `api/routes_admin.py`、`IMP-08`、`IMP-20` |

### 3.2 「不可见即 404」的读取语义

权限拒绝不是统一返回 403 —— 读取类操作对无权者返回 404，避免泄露资源存在性：

- 用他人的 `upload_id` 发起识别 → 404 `UPLOAD_NOT_FOUND`（IMP-54）
- 非 admin 请求 draft 制度版本 → 404（IMP-72）
- 非直属经理查看 Trip → 404

**例外（有意设计）**：审批操作**不**藏在 404 后面 —— 任何存在的 Trip 对非审批人返回 403，`workflow/engine.py:275` 注释明确写「Approval is never hidden behind 404」。

### 3.3 演示身份 ≠ 认证

`X-Demo-User` 请求头只是演示用的身份切换，**不是认证机制**（README 明确声明）。生产化需替换。这是契约范围内的有意省略，不是缺陷。

### 3.4 无多租户/工作区概念

系统是单组织模型：`employees` 表有 `department` 字段，但**没有 tenant/org/workspace 隔离机制** —— 审批权限走 `manager_id` 直属关系链，不是组织边界。部门字段用于展示与报表分组。这是有意的范围限定（50～500 人中小企业），不是遗漏。

---

## 4. 领域实体与数据所有权

### 4.1 实体（13 张表，`db/schema.py`）

| 实体 | 业务含义 | 关键字段 / 约束 | 生命周期 |
|---|---|---|---|
| `employees` | 员工/经理/管理员 | `role` CHECK 三值、`manager_id` 自引用（直属关系）、`preferences_json` | 种子数据，Demo reset 恢复 |
| `policy_versions` | 制度版本 | `status` CHECK (draft/published/archived)、**部分唯一索引保证最多一个 published** | draft → published → archived |
| `policy_rules` | 规则（版本内） | 复合主键 `(version, id)`、`quote_status` (valid/invalid_quote)、`status` (candidate/confirmed/rejected/unsupported)、`quote` + `locator` | candidate → confirmed/rejected/unsupported |
| `trips` | 差旅申请 | `status` CHECK 10 值、`revision >= 1`、`thread_id`、金额列全为整数分 | 见 `trip-lifecycle.dot` |
| `approvals` | 审批卡 | **部分唯一索引 `(trip_id, revision) WHERE status='pending'`** 保证同一 revision 只有一张待处理卡 | pending → decided/superseded |
| `idempotency_keys` | HTTP 幂等记录 | 主键 `(scope, key)`、存 `request_hash` + 首次响应 | 写入后不可变（4xx 首次失败不保存，IMP-11） |
| `audit_events` | 审计事件 | 自增 `seq` 保证确定顺序、**触发器拒绝 UPDATE**、DELETE 仅 Demo reset | **append-only** |
| `outbox_emails` | 待交付邮件 | `kind` CHECK 二值、**`unique_key` UNIQUE = trip_id:revision:kind**、**触发器拒绝 UPDATE** | **append-only** |
| `itineraries` | 行程单 | 主键 `(trip_id, revision)` | 与交付邮件同事务写入 |
| `uploads` | 上传文件登记 | `sha256`、`size_bytes`、服务端生成文件名 | 登记后只读；reset 只删登记过的 |
| `image_extractions` | 图片识别提议 | `status` (pending/confirmed/discarded)、`result_json` + `applied_json` 分开存 | pending → confirmed/discarded |
| `faults` | 故障注入开关 | 主键 `key`、`enabled` 0/1 | 三个固定键（见 §6.3） |
| `meta` | 元数据 | `PRAGMA user_version` 之外的标志位，含 `reset_in_progress` | — |

### 4.2 数据所有权与两个物理分离的库

| 存储 | 内容 | 权威性 |
|---|---|---|
| `var/app.sqlite` | 全部业务数据 | **权威状态源** |
| `var/checkpoints.sqlite` | LangGraph 图中断点 | **可丢弃** |

这个分离有实际架构后果，且有代码证据：`workflow/engine.py:301-306` —— 当图中断点不存在（例如 checkpoint 库丢失）时，`decide()` **不失败**，而是直接调用同一个 `apply_decision()` + `deliver()`，审计标 `via="direct"`（图路径标 `via="graph"`）。

⇒ **checkpoint 库整体丢失不会让待审批的申请无法处理。** AC-010（真实子进程 `taskkill /F /T` 强制终止后同库重启）验证了恢复路径：待办不丢失、只交付一次。

### 4.3 不可变性是数据库级强制的

`audit_events` 与 `outbox_emails` 的 append-only 不是应用层约定，而是**触发器**：

```sql
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
WHEN (SELECT value FROM meta WHERE key = 'reset_in_progress') IS NOT '1'
BEGIN SELECT RAISE(ABORT, 'audit_events can only be cleared by demo reset'); END;
```

唯一删除例外是 Demo reset，且靠**同一事务内设置又清除**的 `meta.reset_in_progress` 标志把关，reset 完成后写一条 `demo_reset` 审计说明清空是有意操作（IMP-37）。AC-012 已验收：对运行中数据库直接 SQL UPDATE/DELETE 被触发器拒绝，探测事件原样保留。

---

## 5. 关键业务路径

### 5.1 主路径：申请 → 判定 → 审批 → 交付（S-01 黄金路径）

完整流程图见 `golden-path-flow.dot`。业务语言概述：

1. **员工描述出差**（自然语言或表单，可附邀请函图片）→ 模型只**提议**值，代码用与手工填写**完全相同**的规则校验，合法值写入、非法值连同缺失项一起追问。追问文本由代码按槽位状态拼出（`generator=template`，IMP-43），所以「只问缺失项、不重复追问」是**构造保证**而非提示词约定。
2. **员工显式提交**（D-13）→ 槽位必须齐全合法；同时把当时的偏好快照进 Trip（D-20），之后修改偏好不影响已提交的申请。
3. **系统绑定当前已发布制度版本**并审计（记 `previous`/`changed`）→ 硬阻断检查（出发日已过 / 返程早于出发 / 天数 > 15）。**命中即阻断，不计价、不调用任何供应商**（S-06）。
4. **查询候选并计价** → 交通/酒店/天气各取一次故障快照（同一次规划看到一致配置）；过滤排序选出候选；`verify_choice` 防伪造自检；代码计算成本（交通 + 酒店 + 补贴，全为整数分，**等于上限即合规，超 1 分才命中**）；判定三类违规与三类审批理由；模型写行程摘要（经检查，含金额即丢弃）。
5. **三条出口**：自动通过 / 需人工审批（生成唯一一张审批卡）/ 无审批人（D-21：方案只进审计、回收集状态）。
6. **直属经理决定**（图中断点，进程重启后待办不丢失）→ 权限三重校验 + revision 乐观锁 + 幂等键。批准 / 驳回（必须填意见）/ 要求修改（revision +1，旧卡置 superseded）。
7. **交付**（三段事务）→ 转 DELIVERY_PENDING；同事务写行程单 + `trip_delivery` 邮件（`unique_key` 唯一约束 + `INSERT OR IGNORE`）；转 DELIVERED。写入失败则回滚、只记错误、停在 DELIVERY_PENDING 等管理员重试，**重试不重复发信**。

**行为验证强度**：AC-019 以真实子进程 HTTP/SSE 驱动 S-01～S-12 各 3 次，`scenarios 36/36; checks 270/270`，无任一硬约束违反；AC-027 用真实模型跑完整链路（真实槽位抽取 + 真实摘要），状态与金额同确定性基线逐项一致，16/16 断言通过。

### 5.2 制度编译路径（管理员）

上传（按**文件内容**判类型：`%PDF-` 魔数为 PDF，UTF-8 无 NUL 且扩展名 `.md` 为 Markdown，其余 415；扫描版 PDF 无文字层 → 415 `SCANNED_PDF`，**不做 OCR**）→ 抽取（模型只产出原始候选；**规则 ID 由代码按类型前缀与出现顺序分配**，不采用模型给的 ID，IMP-66；引用由代码在规范化全文中逐字定位，找不到即 `invalid_quote`，**不做模糊匹配、不忽略标点**，IMP-64）→ 审核（`invalid_quote` 永远不能确认，只能驳回或标 unsupported；改 value/condition 一律要求填写修改理由，IMP-09）→ 发布（未处理完 409；封闭类型缺已确认规则 422 `MISSING_RULE_TYPES`；发布校验**直接调用运行时的 `RuleSet.from_rules`**，保证发布后的版本不会让 Trip 校验失败，IMP-71；通过后单事务：旧 published → archived、新版本 → published）。

已提交的 Trip 继续绑定原版本，读取时显示 `policy_change_notice`，由重新校验接口按新版本重判。

### 5.3 制度问答路径（任意角色）

问题 → 检索（`bm25` / `vector` / `hybrid` 三选一）→ **条款依赖补充**（POL-2/POL-3 的分档上限必须结合 POL-1 城市分类才能回答，而问「上海住宿标准」时 POL-1 段落很少被检索到 —— 真实冒烟实测向量排第 10、混合排第 7，两题都被拒答；`clause_links` 从**已确认规则**推出依赖，追加未命中的关联条款块，只追加不参与排序，IMP-79）→ 模型看到标号 C1…Cn 的块并返回 `{answerable, answer, citation_ids}` → **代码终审** → 每次问答（含拒答）写 `policy_question` 审计。

**质量指标（真实模型，冻结 25 题集）**：引用命中率 20/20 = 100%（阈值 ≥90%）、关键数值正确率 11/11 = 100%（≥85%）、5 道无答案题 5/5 正确拒答（≥4/5）。检索方式对比 Recall@3 两者均 1.0000 打平，按 D-17 保留纯向量。

---

## 6. 规则词汇表（封闭集合，模型无法扩充）

这是理解本系统业务语义的关键：所有判定结果都来自下面这些**封闭枚举**。

### 6.1 七个封闭规则类型（`domain/policy.py:16-23` `CLOSED_RULE_TYPES`）

| 类型 | 业务含义 | 条件形状 / 值形状 |
|---|---|---|
| `city_tier` | 城市分类（A/B/C） | 全量 / 城市→档位映射 |
| `hotel_cap` | 住宿每晚上限 | **按申请人级别**（employee/manager）/ 按城市档位的金额映射 |
| `meal_allowance` | 出差补贴日额 | 全量 / 按城市档位的金额映射 |
| `cabin_class` | 允许舱位 | 全量 / 舱位集合 |
| `advance_booking_days` | 提前预订天数 | **按交通方式** / 天数 |
| `auto_approve_limit` | 自动通过金额阈值 | 全量 / 金额 |
| `trip_duration_limit` | 单次行程天数上限 | 全量 / 天数 |

规则集**可用的前置条件**（IMP-19）：7 类各有已确认规则、住宿上限同时覆盖 employee 与 manager 两档、同类同条件无重复、每个城市档位都有住宿与补贴金额。任一不满足则拒绝评估（发布时同一检查返回 422）。**运行时不猜测。**

### 6.2 三类判定结果（`domain/rules.py:25-27`，`Literal` 类型）

| 类别 | 取值 | 业务含义 |
|---|---|---|
| **硬阻断** `BlockCode` | `START_DATE_IN_PAST` / `END_BEFORE_START` / `DURATION_EXCEEDED` | 先于任何供应商调用；命中即 `POLICY_BLOCKED`，不计价 |
| **违规** `ViolationCode` | `HOTEL_OVER_CAP` / `ADVANCE_BOOKING_VIOLATION` / `CABIN_CLASS_VIOLATION` | 每条带 `rule_id`/`policy_version`/`clause`/`quote`/`locator`/`actual_value`/`limit_value`/`subject`/`source=policy` |
| **审批理由** `ReasonCode` | `BUDGET_EXCEEDED` / `OVER_AUTO_APPROVAL_LIMIT` / `PLAN_INCOMPLETE` | 有违规或理由 → `PENDING_APPROVAL`；否则 → `AUTO_APPROVED` |

**只有三类违规**（D-11）是刻意的范围限定。所有上限语义均为「不超过」：**等于上限合规，超 1 分（或少 1 天）即命中**。AC-006 以 84 个用例验证，并对六个边界比较做过变异检查（`>` ↔ `>=`），每个变异都被测试捕获。

### 6.3 金额不完整时的口径（D-18，容易误解的地方）

任一程交通或酒店缺失 → 对应项 `null`、`total_fen = null`、`known_subtotal_fen` = 所有已定价部分之和、审批理由含 `PLAN_INCOMPLETE`（`missing` 列出缺失部分），**仍然进入审批**。

关键：`null` **不按 0 计入**。AC-013 已验收此口径（如交通超时时 `total_fen=null`、`known_subtotal_fen=60000`）。当天往返（0 晚）不算缺失，`hotel_fen=0`（IMP-15/IMP-25）。

### 6.4 三个故障注入键（`providers/base.py:18` `FAULT_KEYS`）

`transport_timeout` / `no_protocol_hotel` / `weather_unavailable`。降级行为逐项由 AC-013 验收（17/17 检查），核心性质：**降级从不编造数据** —— 失败或空结果使该部分为 `None`（「待人工补录」），天气不可用只省略天气段、不阻断规划。

---

## 7. 未知项与验证任务

**⚠️ 顶层限制（scenario-critical，不埋在后面的表里）**：

> **真实外部数据接入这条能力，结构完整但行为未验证。** `providers/live.py`（356 行）实现了 Kiwi MCP 交通搜索、Tavily 酒店参考、QWeather 天气预报，`tests/test_live_providers.py` 用 mock HTTP 覆盖了解析与错误处理 —— 但**从未对真实服务发过一次请求**（AUTH-02 未授权，AC-028 `blocked`）。因此「live 模式能正确拿到真实航班/酒店/天气」这一主张**当前无证据**，不得当作已验证能力陈述。

| # | 未知项 | 现状 | 验证步骤 |
|---|---|---|---|
| U-1 | Live Provider 真实响应 | unknown（见上方顶层限制） | 所有者授权 AUTH-02 后运行 `scripts/test_ac028_integration.py`，记录各 Provider 真实响应或明确 `degraded`；**Kiwi 不覆盖国内路线不得伪装成功** |
| U-2 | 「待人工补录」的后续补录流程 | **未实现**。`docs/DESIGN.md` 有设计（`PENDING_MANUAL_ENTRY` 状态 + `POST /api/trips/{id}/manual-entry`），但 `domain/states.py` 的 10 个状态里没有它 | 若要实现需先决定归属（契约 v1.2 还是契约外扩展）。当前契约规定的处理方式就是 `PLAN_INCOMPLETE` + 进入审批，**已实现且已验收** |
| U-3 | 真实邮件投递 | 未接（有意）。终点是本地 `outbox_emails` 表 | 契约明确不连真实邮件系统。如需真接属契约外扩展 |
| U-4 | 多进程部署下的并发正确性 | unknown（超出当前部署形态）。`asyncio.Lock`/`threading.Lock` 都是进程内的；跨进程只靠数据库状态守卫与唯一索引兜底（IMP-30 已声明此限制） | 多进程部署前需实测 `UPDATE ... WHERE status='pending'` 与 `unique_key` 的并发表现 |
| U-5 | 认证 | 不适用。`X-Demo-User` 只是演示身份切换 | 生产化需替换为真实认证（契约范围内的有意省略） |

---

## 8. AI Agent 在此仓库工作的注意事项

本仓库**没有** `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` 等 agent 指令文件。业务上下文分散在 `docs/`（11 个文档）与代码 docstring 中 —— 后者质量异常高（几乎每个模块开头都写明了设计意图与 IMP 编号）。

若 agent 要在此仓库工作，最需要注意的回归风险：

1. **不要让模型输出影响金额或合规结论**。任何「让模型直接算总价」或「让模型判定是否超标」的改动都违反 HC-01/HC-02。金额只能是代码从整数分算出的。
2. **不要绕过 `domain/states.py transition()` 写 `trips.status`**。状态机的唯一入口，非法边必须抛 `InvalidTransition`。
3. **不要放宽引用定位**（`policy/documents.py locate`）。IMP-64 明确「不做模糊匹配、不忽略标点」—— 放宽会让被改写过的「引用」通过，破坏可追溯性。
4. **不要给 `approval` 图节点加副作用**。`workflow/graph.py:237-240` 只含 `interrupt()`，因为 LangGraph 恢复时**从头重跑该节点**（RISK-003）。
5. **不要在 fake/recorded 模式引入任何网络调用**。HC-06 + `network_guard` 会直接拒绝，测试会失败。
6. **不要用 fake 模式的输出计算质量指标**（D-08）。`ExtractiveAnswerer` 的 `generator="fake:extractive"` 明确标注它不是质量基线。
7. **不要改写已提交的验收证据**。IMP-97 的处理方式是全项目的先例：修复前的守卫日志「一律不改写」，只在对应行加注解。
8. **改 `network_guard._local()` 要记住 IMP-97**：`getaddrinfo` 也接受 bytes 主机名（httpcore 就是这么传的），所有主机名必须经单一归一化点 `_host_text()`，无法归一化的一律判外部并拒绝（fail closed）。唯一例外是 `None`（通配绑定，无远端主机）。

**回归验证入口**：`uv run pytest`（319 passed）、`uv run --offline python scripts/e2e_scenarios.py <out> --runs 3 --mode fake`（36/36）。

---

## 9. 阅读顺序建议

1. 本文（业务语言：能力、角色、实体、路径、规则词汇表）
2. `golden-path-flow.dot` — 主路径逐步展开（含降级分支与横切关注点）
3. `trip-lifecycle.dot` — 状态机全图（14 个事件、revision 何时递增）
4. `system-model.structurizr.dsl` 的 `L2-Containers` / `L3-FastAPI-Components` — 技术容器与模块职责
5. `module-layers.dot` — 依赖方向与四条结构性质
6. `system-model.evidence.md` — 每个主张的文件行号、置信度、未知项
7. 项目自有文档：`docs/architecture.md`（含 IMP-01～IMP-96 决策日志，是理解「为什么这样设计」的最佳来源）、`docs/contract-v1.1.md`（业务与验收依据）、`docs/acceptance.md`（30 项验收状态）
