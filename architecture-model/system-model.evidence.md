# 架构模型证据索引（system-model.evidence.md）

> 配套 `system-model.structurizr.dsl`、`trip-lifecycle.dot`、`golden-path-flow.dot`、`module-layers.dot`。
> 状态：**current-state only**（不含 target/proposed）。生成日期：2026-10-23。
> 置信度口径：`high` = 代码/配置/schema/权威文档直接证据；`medium` = 多个间接信号一致；`low` = 命名或结构推断；`unknown` = 有意保留待查。

---

## 1. 参与者（L1 People）

| 节点 ID | 标签 | 证据 | 置信度 |
|---|---|---|---|
| `employee` | 员工 | `db/schema.py:15` `employees.role CHECK (role IN ('employee','manager','admin'))`；`README.md:75-80` 演示账号表（张三/销售部/直属经理李四）；`static/js/trips.js` 员工页面；`services/conversation.py` 对话入口 | high |
| `manager` | 直属经理（审批人） | `workflow/engine.py:276-281` 三重 403 校验（非 manager `FORBIDDEN` → 本人 `SELF_APPROVAL` → 非直属 `NOT_DIRECT_MANAGER`）；`db/schema.py:17` `manager_id REFERENCES employees(id)`；`README.md` 李四/赵六 | high |
| `admin` | 管理员 | `api/routes_admin.py`（outbox / demo reset / faults / provider health）；`services/policies.py` 制度编译；`db/schema.py:15` role 枚举；`IMP-20`（admin 不能作为申请人参与规则评估） | high |

**权限边界性质**：角色不是从目录名推断的 —— `employees.role` 有 CHECK 约束，且服务端在每个用例入口显式校验。前端另有页面角色守卫（`static/js/app.js` 路由表第三列），但 `tests/test_workbench.py` 断言「界面允许」与「服务端不返回 403」完全一致，说明前端守卫只是便利、权威判定在服务端（IMP-87）。

---

## 2. 外部系统

| 节点 ID | 标签 | 证据 | 置信度 | 关键限定 |
|---|---|---|---|---|
| `dashscope` | DashScope（阿里云百炼） | `llm/dashscope.py:28` `API_KEY_ENV="DASHSCOPE_API_KEY"`、`:22` `DEFAULT_DASHSCOPE_BASE_URL`；`config.py:117-119` 三个模型名默认值；`network_guard.py:249-252` real 模式只允许 `dashscope_base_url` 的主机名 | high | **仅 `llm_mode=real` 时调用**。`fake`/`recorded` 模式允许清单为空集，物理上无法外联（HC-06） |
| `liveProviders` | Live Provider 组（Kiwi MCP / Tavily / QWeather） | `providers/live.py:235-338` `LiveTransportProvider`（JSON-RPC over SSE，`search-flight`，`flyFrom/flyTo`，`dd/mm/yyyy`）；`:161-232` `HybridHotelProvider`；`:71-158` `LiveWeatherProvider`（`_FORECAST_TIERS=("30d","15d","7d")` 分级回退）；`network_guard.py:253-261` live 模式追加 `mcp.kiwi.com`、`api.tavily.com`、QWeather 主机 | high（代码结构）/ **unknown（真实行为）** | **AUTH-02 未授权，AC-028 `blocked`**。`tests/test_live_providers.py` 用 mock HTTP 验证解析与错误处理，**从未对真实服务发过请求**。图中以虚线边框 + `Unverified` 标签表示 |

### 2.1 关于 Live Provider 的一处事实更正（重要）

先前提交在 `docs/acceptance.md` 「契约范围与扩展说明」章节中写道 `build_live_providers()` 是「骨架（返回 `ProviderUnavailable`）」。**这与代码不符**，需更正：

| 项 | 先前表述 | 代码实际（`providers/live.py`，356 行） |
|---|---|---|
| 交通 | 骨架，返回 `ProviderUnavailable` | 完整实现：Kiwi MCP JSON-RPC 调用、SSE 响应解析、航段拆分、舱位归一化（`_CABIN_MAP` 12 项映射）、价格转整数分、产出 `TransportCandidate(source="live")` |
| 酒店 | 骨架 | `HybridHotelProvider`：**带价候选只来自本地 fixture 目录**，Tavily 仅用于「无价参考」（title/url/snippet/score），永不产出带价候选（`:161-167` docstring 明确此设计意图） |
| 天气 | 骨架 | `LiveWeatherProvider`：QWeather 城市查找 + 30d/15d/7d 分级回退（402 降级）；无凭据时退回 `_QWeatherStub` |
| 测试 | — | `tests/test_live_providers.py` 存在，用 `AsyncMock`/`patch` 覆盖解析与 `ProviderTimeout`/`ProviderUnavailable`/`ProviderInvalidResponse` 路径 |

**准确表述应为**：Live Provider 已实现并有 mock 单测覆盖解析与错误处理，但**从未对真实外部服务验证过**（AUTH-02 未授权，AC-028 `blocked`）。这是「结构完整、行为未验证」，不是「骨架」。此区别对交付评审有实质影响 —— 建议修订 `docs/acceptance.md` 对应段落。

---

## 3. 容器（L2）

| 节点 ID | 证据 | 置信度 |
|---|---|---|
| `workbench` | `main.py:107` `app.mount("/static", StaticFiles(...))`、`:109-111` `GET /` → `index.html`；`static/js/` 8 个 ES module（app/api/ui/parts/trips/approvals/ask/admin/reports）；`docs/architecture.md` §7d；`main.py:34-36` 显式注册 `.js`/`.css` MIME（IMP-86）；`:94-95` `Cache-Control: no-cache`（IMP-94） | high |
| `api`（FastAPI 应用） | `main.py:39-117` `create_app()`；`pyproject.toml` 入口 `travel-desk`；`__main__.py` 启动 uvicorn | high |
| `appDb` | `config.py:107` `db_path=data_dir/"app.sqlite"`；`db/schema.py` 13 张表；`:122-130` `audit_events` append-only 触发器；`:147-155` `outbox_emails` append-only 触发器 | high |
| `checkpointDb` | `config.py:108-110` `checkpoint_db_path=data_dir/"checkpoints.sqlite"`；`workflow/engine.py:118-127` `open()` 建 aiosqlite 长连接 + `AsyncSqliteSaver.setup()`；`:389-391` `reset()` 遍历 `saver.alist(None)` 并 `adelete_thread` | high |
| `uploadStore` | `config.py:111` `upload_dir=data_dir/"uploads"`；`db/schema.py:166-175` `uploads` 表（服务端生成文件名 + sha256 + size_bytes）；`services/uploads.py` 魔数判类型；`docs/architecture.md` §7b（制度原件存 `uploads/policies/`） | high |
| `fixtures` | `config.py:69-70` `providers_dir=tests/fixtures/providers`、`recorded_dir=tests/fixtures/recorded`；`providers/demo.py:30-47` 每次调用重读 + 逐条 Schema 校验；`llm/slots.py:177-196`、`llm/summary.py:108-127`、`llm/vision.py:52-71`、`policy/extraction.py` 按 SHA-256 匹配录制输出 | high |
| `ledger` | `config.py:72-73` `usage_log_path=artifacts/llm-usage.jsonl`、`cost_ledger_path=artifacts/cost-ledger.md`；`llm/dashscope.py:68-120` `UsageMeter`（`manual_total_cny` 正则 + JSONL 求和 + `PAUSE_AT_CNY=40.0`）；`:84-86` 台账不可读则 `BudgetPaused`（fail closed） | high |
| `guardLog` | `network_guard.py:39` `LOG_NAME="network-guard.jsonl"`；`:147-163` `record()` 追加 JSONL（ts/via/host/host_form/port/decision/caller）；`main.py:50` `log_path=resolved.data_dir/LOG_NAME` | high |

### 3.1 双库分离的架构意义（证据支持的性质，非推断）

`workflow/engine.py:301-306`：当图中断点不存在（例如 checkpoint 丢失）时，`decide()` 不失败，而是直接调用**同一个** `apply_decision(...)` + `deliver(...)`，审计标 `via="direct"`（图路径标 `via="graph"`）。`docs/architecture.md` §6 与 IMP-39 记录此决定，理由是「业务库是权威状态」。

⇒ 这使 `appDb` 与 `checkpointDb` 不是主从关系而是**权威/可丢弃**关系：checkpoint 库整体丢失不会让待审批的 Trip 无法处理。AC-010（真实子进程 `taskkill /F /T` 后重启）验证了恢复路径。

---

## 4. 组件（L3，FastAPI 应用内部）

| 节点 ID | 证据 | 置信度 |
|---|---|---|
| `routes` | `api/` 10 个文件；38 个路由装饰器（穷举见 §6）；`docs/architecture.md` §7「路由层很薄」；`api/errors.py` `install_error_handlers` + `new_trace_id`；`api/sse.py` 18 行 `EventSourceResponse` | high |
| `services` | `services/` 15 个文件、2050 行；`db_session` 短事务边界；`docs/architecture.md` §1 | high |
| `workflow` | `workflow/graph.py:285-305` `StateGraph(RunState)` + 8 个 `add_node` + 条件边；`workflow/engine.py:105-395` `Workflow` 类 6 个公开入口；`:237-240` `approval` 节点只含 `interrupt()` | high |
| `domain` | `domain/` 7 个文件、1004 行；`rules.py:345 行`、`states.py:97 行`；`states.py:46-61` `_TRANSITIONS` 表；`models.py` 金额 `StrictInt` | high |
| `planning` | `planning/planner.py:127-243` `build_plan`；`planning/selector.py` `select_leg`/`select_hotel`/`verify_choice`；`planner.py:219` 返回前自检一次 | high |
| `policy` | `policy/rag.py:512 行`（`chunk_document`/`BM25`/`rrf`/`DashScopeEmbedder`/`Retriever`/`finalize`/`ModelAnswerer`/`ExtractiveAnswerer`）；`policy/documents.py` 分段与 `locate`；`policy/extraction.py` `compile_candidates` | high |
| `llm` | `llm/` 4 个文件、702 行；三模式工厂函数 `build_slot_extractor`/`build_summarizer`/`build_invitation_extractor`/`build_rule_extractor`；`dashscope.py:134-150` `structured_call`（function calling） | high |
| `providers` | `providers/base.py:49-72` 三个 Protocol + `Clock`；`:21-36` 三类错误；`:96-105` `Providers` 集合；`demo.py:118-128` `build_demo_providers`；`live.py:341-356` `build_live_providers`；`workflow/graph.py:144-157` 按 `settings.provider_mode` 分支 | high |
| `db` | `db/schema.py:6-194` `SCHEMA_SQL`；`db/database.py` `db_session`/`read_session`/`initialize_database`/`reset_database`；`db/seed.py` 310 行 | high |
| `guard` | `network_guard.py:188-233` `install()` 补丁四条途径；`:245-262` `allowed_hosts_for(llm_mode, dashscope_base_url, provider_mode, qweather_api_host)`；`main.py:46-52` lifespan 内安装、`:64` `uninstall()` | high |

---

## 5. 关系（边）证据

只列需要特别说明的边；其余为直接 import 或 SQL 访问，见 `module-layers.dot` 的计数。

| 边 | 类型 / 协议 | 证据 | 置信度 |
|---|---|---|---|
| `workbench → routes` | HTTP + SSE | `static/js/api.js` 用 `fetch` + `ReadableStream` 读 SSE（`docs/architecture.md` §7d：`EventSource` 不能发 POST 与自定义头）；`api/sse.py` | high |
| `services ↔ workflow` | **环**（compile-time import） | `services/conversation.py:37-38` 导入 `workflow.events` 与 `SubmitRun`/`Workflow`；反向 `workflow/*.py` 导入 services 共 15 处 | high |
| `llm → db / services / planning` | 层序倒置（import） | `llm/slots.py:26` `from db.seed import CITY_TIERS`、`:28` `from services.idempotency import fingerprint`；`llm/summary.py:26` `from planning.plan import Plan, PlanSummary`、`:27` 同 `fingerprint` | high |
| `providers → liveProviders` | HTTPS / JSON-RPC over SSE | `providers/live.py:208` `httpx.AsyncClient` POST `api.tavily.com/search`；`:253-255` POST Kiwi MCP；`:261-268` 解析 SSE `data:` 行 | high（代码）/ **unknown（真实响应）** |
| `llm → dashscope` | HTTPS（OpenAI 兼容） | `llm/dashscope.py:124-131` `ChatOpenAI(api_key, base_url, temperature=0, max_retries=1, extra_body={"enable_thinking": False})`；`:254-259` `AsyncOpenAI.embeddings.create(dimensions=1024)` | high |
| `guard` 横切 `llm` / `providers` | in-process 猴子补丁 | `network_guard.py:229-232` 替换 `socket.getaddrinfo`/`create_connection`/`socket.socket.connect`/`urllib.request.urlopen`；**没有业务模块 import `network_guard`**（穷举确认），只有 `main.py` 与 `api/routes_admin.py`（读 summary） | high |
| `policy → appDb` | SQL（读已确认规则） | `policy/rag.py:319-332` `clause_links(rules)`；`services/policy_qa.py:77` `links = clause_links(_rules(conn, version))` | high |

---

## 6. 接口面（38 个路由，穷举自 `api/*.py`）

| 分组 | 端点 |
|---|---|
| 健康 | `GET /api/health`、`GET /api/health/providers` |
| 用户与偏好 | `GET /api/users`、`GET/PUT/DELETE /api/users/{id}/preferences` |
| Trip（员工） | `POST /api/trips`、`GET /api/trips`、`GET /api/trips/{id}`、`PATCH /api/trips/{id}/slots`、`POST /api/trips/{id}/messages`、`POST /api/trips/{id}/submit`、`POST /api/trips/{id}/image-extractions`、`POST .../image-extractions/{eid}/confirm`、`POST /api/trips/{id}/revalidate`、`GET /api/trips/{id}/audit`、`GET /api/trips/{id}/itinerary` |
| 审批（经理） | `GET /api/approvals`、`POST /api/trips/{id}/decision` |
| 制度（管理员 + 问答全员） | `POST /api/policy/ask`、`GET /api/policies`、`GET /api/policies/{version}`、`POST /api/policies`、`POST /api/policies/{version}/extract`、`PATCH /api/policies/{version}/rules/{rule_id}`、`POST /api/policies/{version}/publish` |
| 运维（管理员） | `GET /api/admin/outbox`、`POST /api/admin/demo/reset`、`GET/POST /api/admin/demo/faults`、`POST /api/trips/{id}/delivery/retry` |
| 上传 | `POST /api/uploads` |
| 报表 | `GET /api/reports` |

---

## 7. 数据与状态（Business Application Fitness 维度）

### 7.1 生命周期状态（high confidence，单一权威来源）

`domain/states.py:13-24` `TripStatus` 10 个状态、`:26-41` `TripEvent` 14 个事件、`:46-61` `_TRANSITIONS` 完整边表（含 revision 增量）、`:63` `TERMINAL_STATES={DELIVERED, REJECTED}`、`:64` `EDITABLE_STATES={COLLECTING, POLICY_BLOCKED}`。

**行为已验证**（不只是结构）：`services/trips.py apply_transition` 用带源状态守卫的 UPDATE；`tests/test_workflow.py`、`tests/test_api.py` 覆盖；AC-019 以 36/36 场景验证状态轨迹。详见 `trip-lifecycle.dot`。

### 7.2 事务边界与幂等（high）

| 关注点 | 机制 | 证据 |
|---|---|---|
| 短事务 | `db_session` 每用例一个事务 | `db/database.py`；`services/*.py` 全部写路径 |
| 审批唯一性 | 部分唯一索引 `approvals_one_pending ON approvals(trip_id, revision) WHERE status='pending'` | `db/schema.py:93-94` |
| 已发布版本唯一 | 部分唯一索引 `policy_versions_one_published WHERE status='published'` | `db/schema.py:32-33` |
| 交付只一次 | `outbox_emails.unique_key UNIQUE` + `INSERT OR IGNORE`，`unique_key = trip_id:revision:kind` | `db/schema.py:142`；`services/delivery.py`；`docs/architecture.md` §6 |
| HTTP 幂等 | `idempotency_keys(scope, key)` 主键 + `request_hash`；4xx 首次失败不保存 Key | `db/schema.py:97-105`；`services/idempotency.py`；IMP-11 |
| 乐观锁 | `trips.revision CHECK (revision >= 1)`，`expected_revision` 不符 → 409 `STALE_REVISION` | `db/schema.py:62`；`workflow/engine.py:174-175` |
| 并发 | 每 Trip 进程内 `asyncio.Lock`（提交运行与决定共用，提交持锁到流结束）；制度写操作一把 `threading.Lock` | `workflow/engine.py:140-145`、IMP-30；`docs/architecture.md` §7b、IMP-73 |
| 审计不可变 | 触发器拒绝 UPDATE；DELETE 仅在 `meta.reset_in_progress='1'` 事务内 | `db/schema.py:122-130`、`:147-155` |

### 7.3 敏感数据处理（high）

- API Key 只在调用时从进程环境读出交给客户端，不记录、不返回；SDK 异常文本从不外露，调用方只见 `ModelUnavailable`（固定原因 + 异常类型名）。证据：`llm/dashscope.py:1-14` docstring、`:143-144`、`:260-261`。
- `GET /api/health/providers` 只返回 `api_key_configured` 布尔（`llm/dashscope.py:49-51` `api_key_present()` 只检查存在性，从不取值）。
- `delivery_error` 只记异常类型 + 固定文案（IMP-34）。
- embedding 缓存只存向量、不存原文（IMP-77；`policy/rag.py:218`）。
- 守卫日志对无法归一化的主机名用 `repr(host)[:120]` 并包 try/except（`network_guard.py:75-86`）。
- AC-022 已验收：git 跟踪文件与 `artifacts/` 下全部 `*.log`/`*.jsonl` 密钥值扫描零匹配。

---

## 8. 假设与未知项（validation tasks）

| # | 项 | 现状 | 验证步骤 |
|---|---|---|---|
| U-1 | Live Provider 真实行为 | **unknown**。代码结构 high confidence，但从未对真实 Kiwi/Tavily/QWeather 发过请求（AUTH-02 未授权，AC-028 `blocked`）。mock 单测只覆盖解析与错误分支 | 所有者授权 AUTH-02 后运行 `scripts/test_ac028_integration.py` / `scripts/test_live_providers.py`，记录各 Provider 真实响应或明确 `degraded`；Kiwi 不覆盖国内路线不得伪装成功 |
| U-2 | 人工补录（PENDING_MANUAL_ENTRY）后续流程 | **未实现**。`docs/DESIGN.md` 有设计文档，代码中无此状态（`domain/states.py` 的 10 个状态里没有它） | 若要实现，需先决定它属于契约 v1.2 还是契约外扩展；当前 `PLAN_INCOMPLETE` + 进入审批已是契约规定的处理方式 |
| U-3 | 真实邮件投递 | **不适用/未接**。邮件终点是本地 `outbox_emails` 表，无 SMTP 集成 | 契约明确不连真实邮件系统（README「明确未接/阻塞」）。如需真接，属契约外扩展 |
| U-4 | 多进程部署下的并发正确性 | **unknown（超出当前部署形态）**。`asyncio.Lock` 与 `threading.Lock` 都是进程内的；跨进程只靠数据库状态守卫与唯一索引兜底（IMP-30 已声明此限制） | 若要多进程部署，需验证 `UPDATE ... WHERE status='pending'` 与 `unique_key` 在并发下的实际表现；当前 Demo 为单进程 |
| U-5 | 认证机制 | **不适用**。`X-Demo-User` 请求头只是演示身份切换，不是认证（README 明确声明） | 生产化需替换为真实认证；这是契约范围内的有意省略，不是缺陷 |
| U-6 | `services ↔ workflow` 环的实际影响 | **结构已确认（high），影响未评估** | 属 `dependency-impact-analyzer` / `risk-quality-reviewer` 职责；本模型只报告结构事实 |
| U-7 | `hybrid` 检索是否有价值 | **已决策关闭**。AC-025 实测 Recall@3 两者均为 1.0000 打平，按 D-17 保留纯向量；`TRAVEL_DESK_RETRIEVAL` 默认 `vector` | 无需验证；`hybrid` 代码路径保留但非交付配置 |

---

## 9. 视图与证据强度总览

| 视图文件 | 回答的问题 | 证据强度 |
|---|---|---|
| `system-model.structurizr.dsl` `L1-SystemContext` | 系统边界外有谁、有什么外部依赖 | high（外部系统调用数在默认配置下为 0，AC-021 已取证） |
| `system-model.structurizr.dsl` `L2-Containers` | 可独立部署/拥有的单元有哪些、数据存在哪 | high |
| `system-model.structurizr.dsl` `L3-FastAPI-Components` | 单进程应用内部的模块职责 | high |
| `system-model.structurizr.dsl` `L3-ModelBoundary` | 模型能影响什么、不能影响什么 | high（HC-01/HC-02 有依赖结构与代码终审双重证据） |
| `trip-lifecycle.dot` | 状态如何流转、revision 何时递增 | high（单一权威表 + 行为测试） |
| `golden-path-flow.dot` | 一次差旅申请从头到尾经过哪些业务步骤 | high（代码路径 + AC-019/AC-027/AC-013 验收互证） |
| `module-layers.dot` | 谁依赖谁、依赖方向是否健康 | high（穷举 import 计数） |

**整体判断**：这是一个**证据密度极高**的代码库 —— 每个架构主张都能落到具体文件行号，且有 319 个测试与 30 项验收（29 passed / 1 blocked）作为行为证据。唯一的行为未知项集中在 U-1（Live Provider 真实响应），且该未知项已被契约正确标记为 `blocked` 而非伪装成功。
