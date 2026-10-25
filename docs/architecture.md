# 架构说明（Travel Compliance Desk）

> 依据 `docs/contract-v1.1.md`。本文件记录实现结构与实施期决定；随阶段增量维护，未实现部分明确标注“计划”。最终内容在 TASK-012 依据实际结果复核。

## 1. 总体结构

```text
浏览器（原生 HTML/CSS/ES module SPA，/static；三角色工作台，已实现，TASK-007，见 §7d）
   │  X-Demo-User 演示身份；JSON + SSE（fetch 流式读取）
   ▼
FastAPI（travel_desk.main / api/*）── 错误体、trace_id、权限依赖、SSE（已实现，TASK-006）
   │
   ├─ services/      用例服务：Trip 创建/槽位、审计、幂等、审批卡与决定、交付（TASK-005）；对话、上传与图片确认（TASK-006）；偏好与重新校验（TASK-009a）；制度上传/抽取/审核/发布（TASK-008a）
   ├─ workflow/      LangGraph 图 + AsyncSqliteSaver、提交/决定/恢复入口（已实现，TASK-005）
   ├─ llm/           槽位抽取、邀请函图片抽取、行程摘要（fake / recorded / real 均已实现，TASK-006）；DashScope 客户端与费用守卫
   ├─ domain/        确定性规则核心：值类型、规则集、槽位、规则评估、状态机（已实现，TASK-003）
   ├─ providers/     Transport / Hotel / Weather / Clock 接口与 Demo 实现（已实现，TASK-004；LLM 计划）
   ├─ planning/      候选过滤、排序、防伪造与方案组装（已实现，TASK-004）
   ├─ policy/        制度文件解析、规范化与引用定位、候选规则抽取（已实现，TASK-008a）；制度问答 RAG（`rag.py`，已实现，TASK-008b，见 §7c）；增强 Agent RAG（`query_optimizer.py`、`synonyms.py`、`enhanced_retriever.py`、`reranker.py`、`rag_agent.py`，已实现，见 §7e）
   └─ db/            SQLite schema、种子、仓储（已实现）
```

原则：**模型只做理解与文字生成，金额、合规结论、候选选择和状态转换全部由代码决定**（HC-01/HC-02，§4.3）。模型输出进入系统前都经过 Pydantic Schema 校验；校验失败返回可重试错误，不写入伪造的结构化结果。

## 2. 存储

| 文件 | 内容 | 访问方式 |
|---|---|---|
| `var/app.sqlite` | 业务数据：员工、制度版本与规则、Trip、审批、幂等键、审计、Outbox、行程单、上传、故障开关 | 同步 `sqlite3`，每个用例一个短事务（`db_session`）；WAL 模式 |
| `var/checkpoints.sqlite` | LangGraph checkpoint | `AsyncSqliteSaver`（aiosqlite 长连接，`Workflow.open()` 时 `setup()`） |
| `var/uploads/` | 图片与制度原件，文件名由服务端生成 | 只通过数据库登记的路径访问 |

- `TRAVEL_DESK_DATA_DIR` 可整体改位置；测试一律使用临时目录。
- Schema 版本写入 `PRAGMA user_version`（当前 1）。打开版本不符的文件时启动失败并提示，不做静默迁移。
- `audit_events`、`outbox_emails` 由触发器保证只追加：UPDATE 一律拒绝；DELETE 只在 Demo reset 事务设置的 `meta.reset_in_progress` 标志下允许，标志随 `meta` 表在同一事务内清除（REQ-020）。
- Demo reset 只删除数据库登记过、且位于 `upload_dir` 内的文件，不清空整个目录，避免误删无关用户文件（HC-08）。`Workflow.reset()` 先等待进行中的运行结束，再重置业务库并 `adelete_thread` 删除全部 checkpoint 线程，最后写一条 `demo_reset` 审计（`trip_id=null`）。
- 金额列均为整数分；JSON 列在仓储层解码。

## 3. 配置与运行模式

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRAVEL_DESK_LLM_MODE` | `fake` | `fake`（确定性、零外部请求）/ `recorded`（读取 `tests/fixtures/recorded/`）/ `real`（DashScope，AUTH-01） |
| `DASHSCOPE_API_KEY` | — | 仅 `real` 模式读取；只从进程环境读取，从不记录或返回 |
| `TRAVEL_DESK_TEXT_MODEL` / `_VISION_MODEL` / `_EMBED_MODEL` | qwen3.8-max / qwen3.5-plus / qwen3.7-text-embedding | TASK-001 尖峰验证过的组合 |
| `DEMO_TODAY` | `2026-10-20` | Demo 时钟，严格 `YYYY-MM-DD` |
| `TRAVEL_DESK_RETRIEVAL` | `vector` | 老端点 `POST /api/policy/ask` 在 `real` 模式下的检索方式：`vector` / `hybrid`（§7c）；`fake`/`recorded` 固定 BM25，不读此变量 |
| `TRAVEL_DESK_AGENT_RETRIEVAL` | `hybrid` | 增强 Agent 端点 `POST /api/policy/ask-agent` 的底层检索方式：`bm25` / `vector` / `hybrid`（§7e）；只有 `real` 模式使用 |
| `TRAVEL_DESK_REWRITE_MODEL` | `qwen3.5-plus` | Query Rewrite + Split 的小模型（§7e） |
| `TRAVEL_DESK_RERANK_MODEL` | `gte-rerank-v2` | 交叉编码器 Reranker 模型（§7e） |
| `TRAVEL_DESK_RERANK_MIN_SCORE` | `0.06` | Reranker 分数阈值；低于阈值的候选被丢弃（48 题 paraphrase 校准，见 `artifacts/paraphrase/`） |
| `TRAVEL_DESK_PROVIDERS_DIR` | `tests/fixtures/providers` | Demo Provider fixture 目录 |
| `TRAVEL_DESK_DATA_DIR`、`TRAVEL_DESK_HOST`、`TRAVEL_DESK_PORT` | `var/`、127.0.0.1、8000 | — |

- 非法配置（未知 LLM 模式、非法日期、非整数）在启动时报 `ConfigError`，不回退默认值。
- 进程启动即强制 `LANGSMITH_TRACING=false`、`LANGCHAIN_TRACING_V2=false`：素材目录的 `.env` 打开了 LangSmith 上传，本项目不加载任何 `.env`，也不上传 trace。
- 调用 DashScope 时显式传入 `api_key` 与 `base_url`，避免环境中的 `OPENAI_*` 变量把请求导向其他服务。
- 网络边界（AC-021，已实现，`travel_desk/network_guard.py`）：应用启动即在进程内安装出站拦截（`socket.getaddrinfo` / `create_connection` / `socket.connect` / `urllib.request.urlopen`）。允许清单由配置推导而非运维开关：`fake`/`recorded` 模式允许清单为空，任何外部请求在 DNS 离开机器前被拒（`NetworkBoundaryError`，是 `OSError` 子类，LLM 层归约为 `ModelUnavailable`、Provider 层归约为方案降级）；`real` 模式只允许 `dashscope_base_url` 的主机名，以及该主机**在 300 秒内解析出的 IP**（urllib3 等 SDK 先 `getaddrinfo` 拿到 IP、再用 IP `connect`；未解析过的 IP 仍被拒，解析被拒时不会记住任何地址）。回环/本地地址放行并单独归类为 `local`。每次尝试追加到 `<data_dir>/network-guard.jsonl`，`GET /api/admin/provider-health` 的 `network` 字段汇总（外部尝试数、被拒数、外部主机、被拒调用点）。这是证据与纵深防御，不是沙箱：无法阻止子进程或 C 扩展自建 socket。正向对照见 `tests/test_network_guard.py`，运行期负向证据见 `artifacts/acceptance/TASK-010/ac021-network-boundary/`。

## 4. 确定性规则核心（TASK-003，已实现）

代码：`src/travel_desk/domain/`。纯函数，无 I/O；输入是槽位、已选候选、绑定版本的已确认规则、申请人角色、Demo 日期；输出 `Evaluation{outcome, blocks, cost_breakdown, violations, approval_reasons, refs}`（Pydantic，`model_dump(mode="json")` 后金额全为整数）。

| 模块 | 职责 |
|---|---|
| `models.py` | 共享值类型（M1 冻结）：`TransportCandidate`、`HotelCandidate`、`Preferences`、`TripSlots`。金额 `StrictInt`，拒绝 float/bool/字符串；时间必须带时区；未知字段拒绝 |
| `policy.py` | `RuleSet.from_rules`：只取 `confirmed` 规则，按封闭类型校验形状；缺类型、缺员工/经理任一住宿档、同类重复、城市档位未覆盖、`invalid_quote` 一律 `PolicyRuleError`（发布时同一检查返回 422） |
| `slots.py` | 格式校验与规范化（D-14），`missing_fields` 按契约顺序 |
| `rules.py` | 阻断、成本、违规、审批理由、结论 |
| `states.py` | 状态机：所有状态变化必须经 `transition()`，非法边抛 `InvalidTransition` |
| `dates.py` | UTC+8 本地日期、早班判定（D-10）、入住晚列表 |

1. **硬阻断**（先于任何 Provider 调用）：`start_date < DEMO_TODAY` → `START_DATE_IN_PAST`；`end_date < start_date` → `END_BEFORE_START`；否则若 `天数 > 15` → `DURATION_EXCEEDED`（RULE-DURATION-001）。任一命中 → `POLICY_BLOCKED`，不计价。
2. **成本**：晚数 = end − start；天数 = 晚数 + 1；交通 = 去程 + 返程；酒店 = 每晚价 × 晚数；补贴 = 目的地等级日额 × 天数。任一程或酒店缺失 → 对应项 null、`total_fen` null、`known_subtotal_fen` = 所有已定价部分之和（D-18、IMP-01、IMP-14）。当天往返（0 晚）不需要酒店，`hotel_fen=0`（IMP-15）。
3. **违规**（只有三类，D-11）：酒店每晚价 > 按申请人级别（employee → RULE-HOTEL-001，manager → RULE-HOTEL-002）与目的地等级的上限；`mode=air` 的每一程，**出发地当地日期** − DEMO_TODAY < 规定天数；任一程舱位不在允许列表。每条违规带 `rule_id/policy_version/clause/quote/locator/actual_value/limit_value/subject/source=policy`。
4. **审批理由**：`BUDGET_EXCEEDED`（total > 预算；total 为 null 时用已知下界，`lower_bound=true`）、`OVER_AUTO_APPROVAL_LIMIT`（total 非 null 且 > 自动阈值，带 `basis`→POL-6）、`PLAN_INCOMPLETE`（total 为 null，`missing` 列出缺失部分）。
5. **结论**：有阻断 → `POLICY_BLOCKED`；有违规或理由 → `PENDING_APPROVAL`；否则 `AUTO_APPROVED`。所有上限均为“不超过”：等于上限合规，超 1 分（或少 1 天）即命中。
6. **防串用**：候选的路线、出发本地日期、酒店城市必须与 Trip 一致，否则 `PlanMismatchError`（防止 revalidate 或调用方传错候选）。

**状态机**（§6.8）：事件 → 允许的源状态 → 目标状态、revision 增量。

| 事件 | 源 → 目标 | revision |
|---|---|---|
| submit | COLLECTING → VALIDATING | — |
| block / validated | VALIDATING → POLICY_BLOCKED / PLANNING | — |
| auto_approve / require_approval | PLANNING → AUTO_APPROVED / PENDING_APPROVAL | — |
| no_approver（D-21） | PLANNING → COLLECTING | — |
| abort（意外失败） | VALIDATING/PLANNING → COLLECTING | — |
| edit_after_block（D-09） | POLICY_BLOCKED → COLLECTING | — |
| approve / reject | PENDING_APPROVAL → APPROVED / REJECTED | — |
| request_changes | PENDING_APPROVAL → COLLECTING | +1 |
| revalidate（D-12） | PENDING_APPROVAL → VALIDATING，再走 block/validated → … | +1 |
| start_delivery / delivered | APPROVED/AUTO_APPROVED → DELIVERY_PENDING → DELIVERED | — |

验证（AC-006）：`tests/test_rules.py`、`tests/test_domain_basics.py`；覆盖每类规则、等于上限/超 1 分、日期边界、v1.0/v1.2/v1.3 差异与合同场景金额。对六个边界比较做过变异检查（`>`→`>=` 等），每个变异都被测试捕获。

## 5. Provider 与候选选择（TASK-004，已实现）

代码：`src/travel_desk/providers/`（`base.py` 接口与错误、`demo.py` fixture 实现）、`src/travel_desk/planning/`（`selector.py` 过滤排序与防伪造、`planner.py` 组装方案、`plan.py` Plan JSON 模型）。Live Provider 未实现（AUTH-02 未授权）。

| 组件 | Demo 实现 | 失败表现 |
|---|---|---|
| Transport `search(origin, destination, date)` | `tests/fixtures/providers/transport.json`；按规范化城市名 + **出发地当地日期**过滤 | `transport_timeout` → `ProviderTimeout` |
| Hotel `search_contract(city)` / `search_backup(city)` | `hotels.json`，`kind=contract/backup` 分库 | `no_protocol_hotel` → 协议库 `ProviderUnavailable`，备用目录照常 |
| Weather `forecast(city, dates)` | `weather.json`，未列日期用 `default` | `weather_unavailable` → `ProviderUnavailable` |
| Clock | `FixedClock(DEMO_TODAY)`；Live 用 `SystemClock`（UTC+8） | — |

- Fixture 每次调用重新读取并逐条 Schema 校验（金额严格整数、未知字段拒绝、ID 不得重复），任一不合格整份响应作废 → `ProviderInvalidResponse`，不部分采信。目录可由 `TRAVEL_DESK_PROVIDERS_DIR` 指定（测试用临时目录注入坏数据）。
- 故障开关存于 `faults` 表（`active_faults/list_faults/set_fault`）；每次规划开始时取一次快照传给 Provider，同一次规划看到一致的故障配置。
- 每次 Provider 调用产生 `ProviderCall{provider, implementation, request, status: ok|timeout|unavailable|invalid, candidate_ids, error, duration_ms}`，由工作流写成 `provider_call` 审计（TASK-005）。
- `build_plan` 只在 `check_blocks` 无阻断后调用（S-06 不触达 Provider，由工作流保证）。

**过滤与排序（REQ-013）**

- 酒店可订判断：一次停留占用 [入住日, 退房日) 的每一晚；`available_date_ranges` 为闭区间 `[首晚, 末晚]` 列表，null 表示全程可订；任一占用晚不在区间内或晚于 `valid_until` 即过滤，记入 `ranking.filtered_out`（含不可订晚）。
- **交通排序**（每一程独立，因各程价格可加，与组合排序等价）：
  `(不合规, 价格, 偏好惩罚, 当地出发时刻, 候选 ID)` 升序。合规 = 舱位允许且（该交通方式无提前规则，或提前天数满足）。偏好惩罚 = 启用“回避早班”且当地出发时刻早于 08:00 时为 1。
  **稳定次级排序键 = 当地出发时刻升序，再按候选 ID 字典序**（AC-023：未设偏好选 `T-BJS-SHA-1105-EARLY`；回避早班后选 `T-BJS-SHA-1105-LATE`；清除后回到 EARLY）。
  无合规候选时取排序第一（价格最低的不合规候选），记入 `transport.exception_legs`；规则核心据此产生违规（S-04）。
- **酒店排序**：`(阶段, 超标金额, 价格, -评分, 协议优先, ID)`。阶段 0 = 上限内协议酒店，1 = 上限内备用酒店（D-15，不构成违规），2 = 超上限（按超标金额最少）。阶段 2 被选中时 `hotel.exception=true`，规则核心产生 `HOTEL_OVER_CAP`（S-03）。住宿上限按申请人级别与目的地等级取自绑定版本规则。
- 只有在协议库无“可订且上限内”的候选（含协议库不可用）时才查询备用目录，避免无谓调用；查询后与协议候选合并排序。
- 无任何可订候选 → `hotel.status=manual_required`（`PLAN_INCOMPLETE`）；当天往返 → `not_needed`，不查询酒店。
- `preference_applied` = 本次使用了“回避早班”；`preference_affected` = 使用偏好的选择与去掉偏好后的选择是否不同（REQ-014）。`notes` 只写入行程，不参与排序。
- **防伪造（AC-007）**：`verify_choice` 要求每个选中候选与本次响应中同 ID 的候选**完全相等**——未知 ID、改价、跨次规划的候选一律 `ForgedCandidateError`。`build_plan` 返回前自检一次；工作流接受任何外部给出的候选时也必须调用。
- **行程骨架（REQ-012 的确定性部分）**：按日生成，交通项带当地时刻与候选来源；入住、事由（含会议地址）、雨天提示、偏好备注不编造时刻（`time=null`）；来自申请人槽位的条目 `source=null`。行程摘要段落由工作流 `plan` 节点叠加到 `plan.summary`（§6），只追加文字，不改动这些字段。

验证（AC-007/AC-013/AC-023）：`tests/test_planning.py`，覆盖 S-01～S-04、AC-023 三段、三种故障与组合故障、坏 fixture、伪造/改价/跨次候选、排序各级键与等于上限边界、当天往返、经理住宿档、确定性。

## 6. 工作流、审批与交付（TASK-005，已实现；HTTP 路由见 §7）

代码：`src/travel_desk/workflow/`（`graph.py` 节点、`engine.py` 入口、`events.py` SSE 事件信封）、`src/travel_desk/services/`（`trips.py` 状态转换、`approvals.py`、`delivery.py`、`idempotency.py`、`audit.py`、`views.py`）、`src/travel_desk/llm/summary.py`。

**图**（每次提交一个线程，`thread_id = "<trip_id>:rev-<n>:run-<8 位 hex>"`，IMP-28）：

```text
START → validate ─(阻断)→ END                      POLICY_BLOCKED，不调用任何 Provider（S-06）
            └→ plan ─(自动)→ auto_approve → deliver → END
                  ├(需审批)→ request_approval → approval[interrupt] → decide ─(approve)→ deliver → END
                  │                                                     └(reject / request_changes)→ END
                  └(无直属上级, D-21)→ no_approver → END            回 COLLECTING，方案只进审计（IMP-06）
```

| 节点 | 做什么 |
|---|---|
| `validate` | 绑定当前发布版本（`policy_bound` 审计，含 `previous/changed`），执行 `check_blocks`；有阻断 → `POLICY_BLOCKED`，写 `validation_blocked` |
| `plan` | 取故障快照调用 Demo Provider → `build_plan` → `evaluate_trip` → 行程摘要；一个事务写 `provider_call`×n、`candidates_selected`、`plan_created`、`rules_evaluated`（摘要失败另写 `error`，stage=summary） |
| `auto_approve` / `request_approval` | `AUTO_APPROVED` + `auto_approved` 审计；或 `PENDING_APPROVAL` + 唯一一张审批卡 + `approval_requested` 审计（重复执行是 no-op） |
| `approval` | **只有** `interrupt()`（RISK-003：恢复时节点从头重跑，因此其中不得有副作用） |
| `decide` | `apply_decision`：一个事务内关闭审批卡、写 `decision_recorded`、需要时写 `workflow_notification`、转换状态、保存幂等键 |
| `deliver` | 调用 `deliver()`，图节点、管理员重试、启动自动重试共用同一函数 |

**入口**（`Workflow`，FastAPI lifespan 中 `initialize_database()` → `open()` → `recover()` → 开始服务 → `close()`）：

- `submit()`：校验顺序 Idempotency-Key（400）→ body 形状与 `expected_revision`（422）→ 404/403 → 幂等重放/Key 复用（422）→ 409 `STALE_REVISION`/`INVALID_STATE` → 422 `INVALID_SLOT`/`SLOTS_INCOMPLETE`。通过后一个事务内 `COLLECTING → VALIDATING`、清空旧方案、写偏好快照（D-20）与 `submitted`/`preference_snapshot` 审计、保存幂等键；随后后台任务运行图，事件经队列流向 SSE。流总以恰好一个终止事件结束：`done`，或 `error`（`NO_APPROVER` / `WORKFLOW_FAILED`）。
- `decide()`：Key → 决定取值 → revision → 意见类型 → 404 → 非经理 403 `FORBIDDEN` → 本人 403 `SELF_APPROVAL` → 非直属 403 `NOT_DIRECT_MANAGER` → 重放 → 409 `STALE_REVISION`/`ALREADY_DECIDED`/`INVALID_STATE` → 422 `COMMENT_REQUIRED`。线程处于 interrupt 时以 `Command(resume=...)` 恢复图；否则（例如线程已被删除）直接调用同一个 `apply_decision`（`via="direct"`）再交付。最终响应 `{trip, approval}` 覆盖写入幂等记录。
- 同一 Trip 的提交运行与决定共用一把进程内 `asyncio.Lock`，提交运行持锁直到流结束，因此决定不会抢在审批节点 interrupt 之前执行（IMP-30）。数据库层另由 `UPDATE … WHERE status='pending'` 与状态守卫保证只执行一次。
- `retry_delivery()`：仅 admin；仅 `DELIVERY_PENDING`（否则 409）。
- `recover()`（启动时）：`VALIDATING/PLANNING` → `abort` 回 `COLLECTING` 并写 `error` 审计；`APPROVED/AUTO_APPROVED/DELIVERY_PENDING` → 重新交付；`PENDING_APPROVAL` 不动，等待决定恢复 checkpoint（S-10）。

**交付**（§4.7、AC-011）：三段事务。① `APPROVED/AUTO_APPROVED → DELIVERY_PENDING`；② 同一事务写行程单（`(trip_id, revision)` 主键）与 `trip_delivery` Outbox（`unique_key = trip_id:revision:kind`，`INSERT OR IGNORE`）并转 `DELIVERED`；③ ② 失败时回滚，只记录 `delivery_error`（异常类型 + 固定文案，不含异常文本，IMP-34）、`delivery_attempts` 与 `delivery_failed` 审计，Trip 停在 `DELIVERY_PENDING`。测试钩子 `delivery_fault_hook` 只在构造 `Workflow` 时注入，不进入管理员故障面板。

**行程摘要**（§4.7、§7.1.1）：`llm/summary.py`。输入只有代码已决定的上下文（已选候选、日期、天气）；输出经检查——空、超过 400 字或含金额（¥、元、RMB 等）即丢弃，`plan.summary.status="unavailable"` 并写 `error` 审计，方案其余部分不受影响（IMP-31）。`fake` 为确定性模板（`generator="fake"`）；`recorded` 读 `tests/fixtures/recorded/summaries.json`（按上下文指纹，条目必须标注 `source`）；`real` 调用 DashScope 文本模型（`RealSummarizer`，计入费用台账），输出同样经上述检查（IMP-32 的“如实报告不可用”在接入后仍适用于调用失败）。

**状态与审计**：所有状态变化经 `apply_transition`（带源状态守卫的 UPDATE），并写一条 `status_changed` 审计 `{from, to, event}`（IMP-29）。已实现的审计事件类型：`trip_created`、`slots_changed`、`blocked_cleared`、`submitted`、`preference_snapshot`、`policy_bound`、`validation_blocked`、`provider_call`、`candidates_selected`、`plan_created`、`rules_evaluated`、`auto_approved`、`approval_requested`、`no_approver`、`decision_recorded`、`notification_written`、`delivery_written`、`delivery_failed`、`status_changed`、`demo_reset`、`error`，以及 TASK-006 的 `message_processed`、`file_uploaded`（`trip_id=null`）、`image_extracted`、`image_confirmed`、`fault_changed`（`trip_id=null`）。TASK-009a 的 `approval_superseded`、`revalidated`、`preferences_changed`（`trip_id=null`），以及 TASK-008a 的 `policy_uploaded`、`policy_extracted`、`policy_rule_updated`、`policy_published`（均 `trip_id=null`，payload 带 `version`）；制度抽取失败写 `error`（stage=`policy_extraction`，`trip_id=null`）。TASK-008b 的 `policy_question`（`trip_id=null`，§7c）；问答模型不可用写 `error`（stage=`policy_qa`，`trip_id=null`）。

验证（AC-008/009/010/011 服务层）：`tests/test_workflow.py`；HTTP/SSE 与真实进程终止见 §7，均列于 `docs/acceptance.md`。

## 7. HTTP 层、对话、上传与模型调用（TASK-006，已实现）

代码：`src/travel_desk/api/`（`routes_trips.py`、`routes_approvals.py`、`routes_admin.py`、`sse.py`、`deps.py`、`errors.py`）、`src/travel_desk/services/conversation.py`、`services/uploads.py`、`src/travel_desk/llm/`（`dashscope.py`、`slots.py`、`vision.py`、`summary.py`）。

**路由层很薄**：只做身份依赖、读取 body/header、调用服务、序列化。请求体统一按 `Any` 接收，形状与字段格式由服务层按 `docs/api.md` §1.3 的顺序校验，因此同一个用例从 HTTP 与从测试直接调用得到相同的错误。`app.state.slot_extractor` / `invitation_extractor` 在 lifespan 外构造，可在测试中替换。

**SSE**（`api/sse.py`）：`sse-starlette` 的 `EventSourceResponse`，每个事件一帧，`id`/`event` 取自事件信封，`data` 为完整信封 JSON；15 秒注释心跳。流来自 `SubmitRun.events()`：业务运行是 `Workflow.spawn()` 登记的后台任务，客户端断开只停止推帧，不中断写入（§1.5）。对话与提交共用同一把每 Trip 锁。

**对话（#3/#6）**：抽取器只*提议*值 → 每个值走与 `PATCH /slots` 相同的格式检查 → 合法值写入（`slots_changed`，origin=`message`），不合法值不写入、在 `invalid_fields` 中带 `value` 返回并重新追问（D-14）→ 写 `message_processed` 审计 → 可选的幂等键在同一事务保存。追问文本由代码根据槽位状态拼出（IMP-43），所以“只追问缺失或非法项”“不重复追问已填项”由构造保证，而不是依赖提示词。

**抽取器**（三种模式同一接口）：

| 模式 | 槽位抽取 | 邀请函图片 | 行程摘要 |
|---|---|---|---|
| `fake` | 确定性正则解析器（`generator="fake"`，测试替身，不是模型） | 读录制输出（IMP-48） | 确定性模板 |
| `recorded` | `recorded/slot_extractions.json`（按文本+日期指纹） | `recorded/invitations.json`（按图片 SHA-256） | `recorded/summaries.json` |
| `real` | DashScope 结构化输出（`SlotsOut`，模糊日期必须为 null） | 视觉模型 + base64 data URL | 文本模型 |

截至 TASK-006，`tests/fixtures/recorded/` 只有 `invitations.json`（1 条，来自真实视觉模型的冻结输出）；`slot_extractions.json` 与 `summaries.json` 的读取代码已就绪，录制文件在 TASK-011 生成。文件缺失时如实报告不可用，不会回退到 fake 或真实调用：槽位抽取返回 `MODEL_UNAVAILABLE`（不可重试），行程摘要降级为 `summary.status="unavailable"`。

模型结构化输出先经 Pydantic Schema，再经槽位格式检查；预算由模型给“元”，代码换算为整数分。录制条目必须标注 `source`（`model` / `hand_written`），`generator` 透出为 `recorded:<source>`。

**上传与图片确认（#9–#11）**：类型只按文件头魔数判定（PNG/JPEG），不信任文件名与 Content-Type；路由最多读取上限 + 1 字节即判 413。文件存 `upload_dir/<uuid>.<ext>`。识别结果是 `pending` 提议，**不修改 Trip**；确认时员工可改值，只有确认的值写入（origin=`image`），`image_confirmed` 记录 `edited_fields`。

**DashScope 客户端与费用守卫**（`llm/dashscope.py`，AUTH-01）：

- 密钥只在调用时从进程环境读出交给客户端，不记录、不返回；SDK 异常文本从不外露（认证错误可能回显掩码密钥），调用方只看到 `ModelUnavailable`（固定原因 + 异常类型名）。`/api/health/providers` 只给 `api_key_configured` 布尔。
- 每次真实调用追加一行到 `artifacts/llm-usage.jsonl`（用途、模型、token、保守估算费用、延迟；按所用模型最高单价 36 元/百万 token 统一估算）。调用前把台账 `manual_total_cny` 标记与 JSONL 合计相加，达到 40 元暂停线即拒绝调用（`BudgetPaused`，不可重试）；台账缺失或不可读同样拒绝（fail closed）。`scripts/ledger_summary.py --write` 把 JSONL 汇总回写台账。
- 显式传 `api_key`/`base_url`，`enable_thinking=False`，温度 0。

**启动命令**：`uv run travel-desk`（`travel_desk.__main__`）配置日志后启动 uvicorn；启动恢复报告以 `startup recovery aborted=… delivered=… still_pending=…` 写入日志（只含计数）。

**验证**：`tests/test_api.py`（AC-002/003/009 的 HTTP 路径、SSE 帧严格解析、权限矩阵、错误体与 trace_id）；`scripts/ac010_restart.py`（真实子进程、`taskkill /F /T` 终止、同库重启后审批与只交付一次）；`scripts/export_api_evidence.py`（确定性模式下 AC-002/003/009/011 的 HTTP 证据导出，28 项检查）；`scripts/real_smoke.py`（真实模型各路径一次，计入台账）。

## 7a. 个人偏好与按新版本重新校验（TASK-009a，已实现）

代码：`src/travel_desk/services/preferences.py`、`services/revalidation.py`、`Workflow.revalidate`（`workflow/engine.py`），路由在 `api/routes_users.py` 与 `api/routes_trips.py`。

**偏好（#24，REQ-014/D-20）**：只有本人（employee/manager）可读写，admin 一律 403。PUT 是整体替换，先做格式检查（422 先于 404/403），存储形式为 `model_dump(exclude_none=True)`，notes 压缩空白、空 notes 省略，所以 `{}` 就是“无偏好”。每次 PUT/DELETE 写 `preferences_changed`（trip_id=null，before/after/changed）。提交时才把当时的偏好快照进 Trip（§6），已提交的 Trip 不受之后修改的影响。

**重新校验（#14，D-12，IMP-04/05）**：检查顺序为 400 缺 Key → 422 revision 格式 → 404 不可见（含非直属 manager）→ 403 不是本人也不是直属经理（admin 在此被拒）→ 幂等回放 → 409 revision/状态。之后在**一个事务**内完成：

1. 旧的 pending 审批卡置 `superseded`；`REVALIDATE` 转换（PENDING_APPROVAL→VALIDATING，revision+1），绑定当前发布版本，审计 `approval_superseded`、`policy_bound`。
2. 从当前方案 JSON 取回已选候选（经共享模型重新校验），不调用任何 Provider；按发布版本重算成本与判断，审计 `rules_evaluated`（`providers_called=false`）。
3. 命中硬阻断 → `POLICY_BLOCKED`（方案清空，旧方案留在 `revalidated.previous_plan`）；否则 VALIDATED→PLANNING，方案 JSON 只改 `revision` 与 `policy_version`，成本、违规、审批理由用重判结果。
4. `revalidated` 审计带 before/after 两次判断（版本、成本、违规、理由、阻断）与复用的候选 ID，refs 同时指向新旧两个版本。
5. 结论为自动通过 → `AUTO_APPROVED`（`auto_approved.via="revalidate"`），事务提交后走共享的幂等 `deliver()`；需审批 → `create_approval` 建新 revision 的卡；D-21（审批期间直属经理被移除）→ 方案只进 `no_approver` 审计，回 `COLLECTING`。

新 revision 使用一个**没有 checkpoint 的 thread_id**（`<trip>:rev-<n>:revalidate-<hex>`）：图中断点属于旧 revision，不能复用。因此对新卡的决定由 `decide()` 的直接路径处理（`apply_decision` + `deliver`，与图节点共用同一函数，`decision_recorded.via="direct"`），重启恢复同样适用（待审批是数据库中的卡，不依赖 checkpoint）。

**验证**：`tests/test_preferences_revalidate.py`（AC-023 三段 HTTP、AC-024/S-12、IMP-04 交付与交付失败重试、IMP-05、D-21、错误顺序与幂等、偏好权限与格式）；`scripts/export_task009_evidence.py`（13 项检查）。这些用例里的 IMP-04/05 等变体需要录制抽取不会产生的规则值，仍用仅限测试的 `publish_direct` 直接写库发布；S-12 本身已在 TASK-008a 改为走管理员发布接口（§7b）。

## 7b. 制度编译：上传、抽取、审核与发布（TASK-008a，已实现）

代码：`src/travel_desk/policy/documents.py`、`policy/extraction.py`、`services/policies.py`，路由 `api/routes_policies.py`（#19–#23）。

**文件（#20）**：按内容判定类型——`%PDF-` 魔数为 PDF，文件名 `.md`/`.markdown` 且为不含 NUL 的 UTF-8 为 Markdown，其余 415。PDF 用 pypdf 提取文本，加密或无法解析 415 `UNSUPPORTED_MEDIA_TYPE`，所有页都没有文字 415 `SCANNED_PDF`（不做 OCR）。文档切成段（每个非空行一段，PDF 记页码），段文本按 §4.8 规范化（NFC、统一换行、空白折叠为一个空格）。原件以服务端生成的名字存入 `uploads/policies/`，任何后续检查失败都会删除刚写入的文件；Demo reset 只删除登记过的制度文件，种子制度文件不在 uploads 内、不受影响。

**引用定位**：引用规范化后在“各段以单个空格相连”的全文中逐字查找（这与对原文整体规范化等价），命中位置映射回段，得到 `line:N`/`line:N-M` 或 `page:N`/`page:N-M`；找不到即 `invalid_quote`。不做模糊匹配、不忽略标点（IMP-64）。

**抽取（#21）**：`RuleExtractor` 只负责产出原始候选（类型、条件、值、单位、条款号、原文引用）；`compile_candidates` 由代码分配规则 ID、定位引用、判断类型是否属于 7 个封闭类型（IMP-66）。实现：

- `RecordedRuleExtractor`（fake / recorded 模式）：读 `tests/fixtures/recorded/extract-*.json`，按**规范化全文的 SHA-256** 匹配文档，同一内容以任何版本号上传都得到同一输出；没有录制的文档 → 503 `MODEL_UNAVAILABLE`（`retryable=false`），不臆造规则（IMP-65）。`generator="recorded:<source>"`，当前录制文件 `source=hand_written`，按 §7.1.1 标注，不能作为 AC-018 的抽取质量证据。
- `RealRuleExtractor`（real 模式）：DashScope 文本模型结构化输出（`policy_extraction`，计入费用台账），候选上限 60 条；输出经同一 `compile_candidates`。

**审核（#22）与问题提示**：`problems` 在读取时计算——`INVALID_QUOTE`、`UNSUPPORTED_RULE_TYPE`、结构问题（`validate_rule_shape`，与运行时 `RuleSet` 同一组 Pydantic 模型）、`UNIT_MISMATCH`（与类型的规范单位不符）。确认时依次检查修改理由、引用、类型、结构；`invalid_quote` 永远不能确认，只能驳回或标 unsupported。

**发布（#23）**：检查 Key → 404 → 403 → 幂等重放 → 409 非 draft → 409 仍有 `candidate` → 用 `RuleSet.from_rules` 校验已确认规则（缺少封闭类型 422 `MISSING_RULE_TYPES`，冲突、档位未覆盖等 422 `RULE_SET_INVALID`）。通过后一个事务内：旧 published → archived、新版本 → published（`published_at`）、写 `policy_published` 审计与幂等记录。数据库另有“最多一个 published”的唯一部分索引兜底。已提交的 Trip 继续绑定原版本，读取时显示 `policy_change_notice`，由 #14 按新版本重新校验（§7a）。

**并发**：制度写操作（上传、抽取写入、审核、发布）共用一把进程内 `threading.Lock`，抽取在锁外调用模型、写入前在锁内重新检查状态（IMP-73）。

**验证**：`tests/test_policies.py`（AC-005 上传/抽取/审核/发布与错误顺序、读取可见性、reset 清理、S-12 走发布接口）；`scripts/export_task008_evidence.py`（21 项检查，AC-005 与 AC-024 各用独立临时数据库）。夹具：`scripts/make_policy_pdfs.py` 确定性生成 `v1.2-text.pdf`（STSong-Light，不嵌入字体，两页）与 `scanned.pdf`（只有图像、无文本层）。

## 7c. 制度问答（#18，TASK-008b，已实现）

代码：`src/travel_desk/policy/rag.py`（切块、检索、回答、引用校验）、`services/policy_qa.py`（请求校验、版本选择、审计），路由 `POST /api/policy/ask` 在 `api/routes_policies.py`；`app.state.policy_qa` 在启动时按模式构造。

**切块**（IMP-75）：以 §7b 的文档段为输入。标题是 Markdown `#` 行，或不超过 40 字、带 `POL-n` 且不含句读的短行（文本 PDF 丢了 `#`）。块是一个条款下“连续行”的段落（Markdown 段落之间有空行；PDF 一页通常没有空行，所以一个条款是一块），上限 600 字。块的条款号取上方标题的 `POL-n`；一级标题下、第一个条款之前的文字归 `制度信息`。**引用原文就是块文本本身**，所以总能被 `PolicyDocument.locate` 重新定位（§4.8），引用永远不由模型书写。

**检索**（IMP-76/77/78）：

| 方式 | 实现 | 使用场景 |
|---|---|---|
| `bm25` | jieba `lcut_for_search` 分词 + 停用词，手写 Okapi BM25（k1=1.5，b=0.75），零分块不返回 | `fake`/`recorded` 模式固定使用（无网络，HC-06） |
| `vector` | DashScope `qwen3.7-text-embedding`（OpenAI 兼容 `/embeddings`，1024 维），余弦相似度 | `real` 模式默认 |
| `hybrid` | 对 BM25 与向量两个排名做 RRF（k=60） | `real` 模式 `TRAVEL_DESK_RETRIEVAL=hybrid` |

取前 5 块。嵌入每批 10 条，每次调用计入费用台账（用途 `policy_embedding` / `policy_query_embedding`）；结果按“模型 + 文本 SHA-256”缓存在内存与 `data_dir/cache/embeddings-<model>.json`（只存向量，不存原文）。默认 `vector` 是因为 AC-025 尚未运行：只有当混合检索的 Recall@3 严格更好时才切换，打平保留纯向量（REQ-029、AC-025）。

**关联条款补充**（IMP-79）：POL-2/POL-3 的分档上限（“A 类城市每晚 500 元”）必须结合 POL-1 的城市分类才能回答，而问“上海住宿标准”时 POL-1 的段落很少被检索到（真实冒烟第 1 轮实测：向量排第 10、混合排第 7，两题都被拒答）。`clause_links` 从该版本**已确认规则**推出依赖（分档规则类型 `hotel_cap`、`meal_allowance` 依赖 `city_tier` 规则的条款），排名结果之后追加未命中的关联条款块（最多 3 块），响应中标为 `reason="rule_dependency"`。补充块只追加、不参与排序，因此 AC-025 的 Recall@3 仍只按排名计算；draft 版本没有已确认规则时不补充。

**回答与引用校验**（IMP-80/81）：

- `ModelAnswerer`（real）：文本模型看到标号 C1…Cn 的块（排名块在前、补充块在后），结构化输出 `{answerable, answer, citation_ids}`（用途 `policy_answer`）。
- `ExtractiveAnswerer`（fake/recorded，`generator="fake:extractive"`）：首块命中至少 2 个不同的查询词即以该块原文作答，否则拒答。它只用来覆盖接口与拒答路径，**不是质量基线，不得用于 AC-004/017/025 指标**（D-08）。
- 代码终审（`finalize`）：只保留模型看到过的块 ID，其余记入 `dropped_citation_ids`；声称可回答但没有有效引用 → 降级为拒答（`downgraded=true`）；任何拒答的 `citations` 一律为空、文本固定为 `不知道：当前制度（{version}）中没有找到能回答这个问题的条款。`。

**版本与权限**（IMP-82/83）：任意已登录角色可问。缺省 `version` 用当前 published 版本（没有 → 404 `POLICY_VERSION_NOT_FOUND`）；指定版本时可问 archived 版本（用于对比旧制度），draft 仅 admin 可见，其他角色 404（与 #19 相同，IMP-72）。请求体校验：必须为对象、无未知字段、`question` 去空白后 1–500 字、`version` 符合版本格式，否则 422 `VALIDATION_ERROR`。模型或嵌入不可用 → 503 `MODEL_UNAVAILABLE`（`details.stage="policy_qa"`，带 `retryable`）并写 `error` 审计。

**响应与审计**（IMP-84/85）：除契约字段 `answer`/`answerable`/`citations`/`version`/`source` 外，额外返回 `generator`（区分 fake 与真实模型）与 `retrieval`（方式、排名块的 rank/clause/locator/score、补充块），便于前端展示与评测。每次成功回答写一条 `policy_question` 审计：版本、请求版本、问题前 200 字（`question_truncated`）、是否可回答、生成器、检索方式、检索与补充的条款位置、引用、丢弃的 ID、是否降级。

**验证**：`tests/test_policy_qa.py`（32 项：切块定位、BM25/RRF 计算、fake 模式 HTTP 行为与拒答、版本差异 v1.0/v1.2 的 POL-5、权限与 9 种 422、版本可见性、审计内容；用桩替身覆盖真实模式的丢弃/降级/拒答清空、503 与错误审计、向量与混合检索、嵌入缓存与缺密钥、关联条款补充）；`scripts/policy_qa_smoke.py`（真实模型 3 题冒烟，计入台账，证据见 `artifacts/acceptance/TASK-008/`）。AC-004/017/018/025 的质量指标由 TASK-011 用冻结题集运行。

## 7e. 增强 Agent 制度问答（#18b，已实现）

老端点 `POST /api/policy/ask` 与 AC-004/017/025 的口径完全不动。增强链路是独立的 `POST /api/policy/ask-agent`：只在 `real` 模式启用；`fake`/`recorded` 直接复用老链路（BM25 + 抽取式回答，零网络，HC-06），响应标 `agent.mode="fallback"`、`generator="fake:extractive"`，并补齐 `grounded=true`、`confidence=0.0`、`needs_clarification=false`。

代码：`policy/query_optimizer.py`（一次结构化调用完成 Rewrite + Split）、`policy/synonyms.py`（差旅领域同义词表）、`policy/enhanced_retriever.py`（多路检索 + 加权 RRF）、`policy/reranker.py`（DashScope `gte-rerank-v2` 交叉编码器）、`policy/rag_agent.py`（LangGraph ReAct Agent、Tool、结构化回答、引用终审）、`services/policy_agent.py`（版本/权限/审计/回退）。`app.state.policy_agent` 在启动时构造（非 `real` 为 `None`，不加载 LangChain）。

**链路**：Agent 判断问题是否需要制度依据 → 需要则调用 `search_policy`（闲聊不调用）→ Tool 内一次 `optimize()` 产出 1 条改写；复杂问题同一次调用拆成 2–4 条子查询 → `query_paths()` 始终把原始问题放在第一路 → `EnhancedRetriever.search_multi()` 每路跑一次 `Retriever.search()`（`Retriever` 本身一字未改），再加一路同义词扩展 BM25 → 加权 RRF 合并、按 `Chunk.id` 去重；原始问题的前 5 名固定在前（交付顺序），改写/拆分/同义词只填充剩余候选位，权重上限保证补充候选的总分不超过最低的固定分 → 交叉编码器用**原始问题**（不是改写）重排，取 top 3 → 按已确认规则补充关联条款（POL-1 等）→ Tool 返回带 `citation_id=clause@locator` 的片段。

**同义词与 Split**：`SYNONYM_MAP` 只覆盖差旅高频词（住宿/酒店、伙食/餐补、审批/签字、出租车/打车等），双向展开且不递归；扩展只进入额外 BM25 一路，不改文档索引、不改老检索器。`optimize()` 的 `QueryPlan{split, queries}` 一次调用返回：简单问题 `split=false` 且 1 条查询；复合问题 `split=true`、2–4 条互不重复的子查询。`EnhancedRetriever.use_synonyms` 仅用于消融实验。

**回答契约**：`EnhancedAnswerOut{grounded, answerable, answer, citation_ids, confidence, needs_clarification}`。
- `grounded=true`：制度回答。代码只接受本次 Tool 实际展示过的 `citation_id`；声称可答但没有有效引用或正文为空 → 降级为固定拒答（`downgraded=true`）。
- `grounded=false` 且非澄清：闲聊，直接返回，引用一律忽略。调用过 Tool 后仍声称闲聊（通常是模型自己写“制度里没有”）→ 视为无依据，返回固定拒答。
- `needs_clarification=true`：缺少目的地/职级/天数/金额等关键信息，返回模型提出的澄清问题（无引用，200）。
- `confidence` 是模型自评（0–1），只用于展示与审计，不参与代码判定。
- 正文中模型回显的内联 `【citation_id: …】` 由代码清理；拒答文本始终为 `refusal_text(version)`。

**响应与审计**：在 PolicyAnswer 字段之外增加 `grounded`、`confidence`、`needs_clarification`、`agent{mode, tool_calls, queries, split, paths}`；`retrieval` 仍是 `hybrid+multi+rerank` 的排名块与补充块。审计仍为 `policy_question`，`extra` 带 `chain="agent"`、grounded、tool_calls、queries、split、paths、confidence、needs_clarification。模型不可用 → 503 `MODEL_UNAVAILABLE`（`stage="policy_agent"`，写 `error` 审计）。

**验证**：`tests/test_policy_agent.py`、`tests/test_policy_agent_api.py`（Phase 3/4/5 共 37 项：改写/Split、同义词、RRF、Rerank 缺省/错误/阈值、引用接受与丢弃、闲聊/澄清/拒答、fake 回退、503 审计、API 字段）；`scripts/eval_paraphrase.py`（48 题 paraphrase 检索评测）、`scripts/eval_agent_paraphrase.py`（11 题 + 2 个闲聊探针的端到端评测；`evals/compound_probes.json` 为 4 题 Split 探针），证据在 `artifacts/paraphrase/`。Phase 5 实测：agent 小集 grounded/tool_called/primary_cited/numeric/refused/chat 全部 100%，平均自评置信度 0.984；48 题检索 Recall@3/5/10=40/40，empty=3；4 题复合问题 split/引用/数值全部通过。已知限制：`gte-rerank-v2` 阈值无法区分拒答题（P23/P24 的 top1 分数与可答题重叠），由 Agent 判断与代码拒答兜底；paraphrase 集在 rewrite+rerank 下已饱和，同义词对 top3 无可测增益（候选顺序有变化，消融见 `phase4-norewrite-*.json`）；端到端平均延迟约 10 秒/题（含 2 次模型调用与多次检索）。

## 7d. 三角色工作台（TASK-007，已实现）

代码：`src/travel_desk/static/`。`index.html` 只是外壳，`css/app.css` 是样式，`js/` 下 8 个原生 ES module，无构建步骤、无第三方前端依赖：

| 模块 | 职责 |
|---|---|
| `app.js` | hash 路由、导航、身份切换（`#/login`）、页面角色守卫、健康状态 |
| `api.js` | HTTP 与 SSE 客户端：`X-Demo-User`（localStorage `tcd.user`）、`Idempotency-Key`、统一 `ApiError`（含 `trace_id`、`retryable`） |
| `ui.js` | `el()` DOM 构造、金额（元）与本地时间格式化、状态 / 来源 / 生成器徽标 |
| `parts.js` | 共用视图：方案、成本明细、规则判断、审批卡、审计时间线 |
| `trips.js` | 员工：Trip 列表、对话创建、槽位编辑、邀请函图片抽取与确认、提交（SSE 进度）、详情、行程单、重新校验 |
| `approvals.js` | 经理：待审批列表、审批决定、制度已更新提示 |
| `ask.js` | 制度问答（任意角色）、个人偏好（员工 / 经理） |
| `admin.js` | 管理员：制度版本上传 / 抽取 / 审核 / 发布、运维页（故障开关、Provider 健康、Outbox、交付重试） |

**安全与一致性**：所有文字都以文本节点插入（`el()`，从不使用 `innerHTML`），API 返回的数据不能注入标记。金额、合规结论、来源都直接显示服务端字段，前端不重新计算（HC-01/HC-02）；分→元只做格式化。每个用户动作生成一个幂等键：连接中断时，再次点击用同一个键重试（不会重复提交或审批），收到任何应答（成功或业务错误）后释放（`docs/api.md` §1.5）。SSE 用 `fetch` + `ReadableStream` 读取（`EventSource` 不能发 POST 和自定义头）；完成前断开时提示“服务端仍会继续处理”，可刷新查看最新状态（与 AC-010 的服务端行为一致）。

**页面角色守卫**（IMP-87）：路由表的第三列是允许的角色，与服务端的权限判断一一对应（`/trips/new`、`/prefs` 员工与经理；`/approvals` 经理；`/admin/*` 管理员）。越权打开显示“无权访问”和允许的角色，不渲染表单。这只是界面便利，接口权限仍由服务端判定（越权调用返回 403）。`tests/test_workbench.py` 从 `app.js` 解析路由表，用每个受限页面实际调用的接口对 4 个演示用户逐一探测，断言“界面允许”与“服务端不返回 403”完全一致，不一致时测试失败。

**来源展示**（REQ-024、AC-015）：方案中的交通、酒店、天气、摘要，规则判断与引用，行程单条目，问答回答与引用，都显示 §6.1 的 `source` 徽标；员工自填的行程条目显示“申请人填写”（`source=null`，IMP-26）；fake 模式的摘要和回答另有生成器徽标（IMP-84）。审计时间线显示事件自己声明的 `source` / `sources`（IMP-91）。

**其他界面决定**：审批页提示制度已更新（IMP-88）；要求修改后，员工在详情页看到经理意见（IMP-89）；酒店例外说明用元（IMP-90）；天气 Provider 调用显示请求天数（IMP-93）。

**静态资源 MIME 与缓存**（IMP-86/94）：`main.py` 启动时把 `.js`、`.css` 注册为 `text/javascript`、`text/css`；外壳与静态文件带 `Cache-Control: no-cache`，浏览器每次按 ETag 重新验证，更新代码后无需强制刷新。

**验证**：`tests/test_workbench.py`（代替打包器的检查：模块集合、外壳与资源的 MIME、具名导入都能解析且被使用、所有模块可从入口到达、不解析标记且不加载外部资源、客户端只发送演示身份头、页面角色守卫与服务端一致）、`tests/test_sources.py`（AC-015 JSON 扫描及扫描器自检）、`scripts/source_scan_real.py`（同一扫描的真实模型运行）；浏览器演练截图与服务端检查见 `artifacts/acceptance/TASK-007/`。

## 8. 决策日志

| 编号 | 类型 | 决定 | 理由 / 依据 |
|---|---|---|---|
| IMP-01 | A（实施负责人） | D-18 的 null 口径同时适用于交通缺失和酒店缺失：任一缺失则 `total_fen=null`，给出 `known_subtotal_fen` | §4.3 只写了交通，但 S-09/AC-013 分别要求交通与酒店缺失时 `total_fen=null`（AC-013 known_subtotal=150000） |
| IMP-02 | A | `preference_source`（`personal_snapshot`/`trip_override`/`none`）为独立字段，不加入 §6.1 的 `source` 枚举 | §6.1 source 枚举是数据来源，D-20 的偏好来源是另一维度；扩充枚举会违反 REQ-024 的封闭集合 |
| IMP-03 | A | `POLICY_BLOCKED` 状态下接受对话和 `PATCH slots`；第一次有效修改把 Trip 转回 `COLLECTING`（revision 不变）并写 `blocked_cleared` 审计 | §6.8 状态图“员工修改 → COLLECTING”与 §6.5 “仅 COLLECTING（含被阻断后返回）可用”需要一个入口 |
| IMP-04 | 所有者决定（2026-10-19） | 重新校验后不再需要审批 → `AUTO_APPROVED` 并照常交付；旧审批卡作废并审计 | 契约 §4.1/D-12 未规定此结果 |
| IMP-05 | 所有者决定（2026-10-19） | 重新校验命中硬阻断 → `POLICY_BLOCKED`，revision 仍 +1 | 同上 |
| IMP-06 | 所有者决定（2026-10-19） | D-21 无直属上级时，计算出的方案只写入审计，不保留为当前方案；revision 不变，Trip 回 `COLLECTING`，下次提交重新规划 | 避免 COLLECTING 状态挂着一个看似有效的方案 |
| IMP-07 | A | 交通候选的稳定次级排序键 = 当地出发时刻升序，再按候选 ID | AC-023 要求在 architecture.md 记录；结果可复现 |
| IMP-08 | A | §6.4 中“审批：admin 否”在代码中实现为 `require_manager` 仅允许 `manager` 角色；直属关系与本人校验在服务层按 Trip 执行 | 防止管理员绕过直属审批 |
| IMP-09 | A | 修改候选规则的 `value`/`condition` 一律要求 `modification_reason` | REQ-007 要求“数值与原文不一致时”必须填写；代码无法可靠判定“一致”，统一要求更严格且可审计 |
| IMP-10 | A | 当前发布版本与绑定版本相同时，重新校验接口仍执行（revision+1）；工作台只在出现版本差异时展示按钮 | 契约只列出两类 409，新增拒绝条件会改变错误语义 |
| IMP-11 | A | 幂等首次请求以 4xx 失败时不保存 Key；SSE 接口重放只返回当前快照 + `done` | §4.6 要求“同 Key 返回第一次结果、无重复副作用”；失败请求本无副作用 |
| IMP-12 | A | 上传类型按文件头魔数判定（PNG/JPEG/PDF），不信任扩展名和 Content-Type | 415 判定可靠 |
| IMP-13 | A | 配置非法（未知 LLM 模式、非法日期、非整数）时启动失败（`ConfigError`），不静默回退默认值 | 协议要求不吞错误 |
| IMP-14 | A | 只缺一程交通时，交通整体视为缺失（`transport_fen=null`），但已定价的那一程计入 `known_subtotal_fen` | 契约口径是“往返交通”；`known_subtotal` 按 api.md 定义为“已知部分之和”，计入真实价格仍是下界 |
| IMP-15 | A | 当天往返（0 晚）不需要酒店：`hotel_fen=0`，不算缺失，不产生酒店违规 | §4.3 公式下酒店 = 每晚价 × 0；把“无需酒店”当成缺失会错误地产生 `PLAN_INCOMPLETE` |
| IMP-16 | A | 城市名规范化：去空白、去末尾“市”；不在 A/B 名单的城市按 `fallback_tier`（C） | §4.4 “使用规范城市名”；RULE-CITY-001 “其他城市为 C” |
| IMP-17 | A | `OVER_AUTO_APPROVAL_LIMIT` 只在 `total_fen` 非 null 时判定；不按已知下界扩展 | D-18 只为 `BUDGET_EXCEEDED` 规定下界口径；方案不完整时已由 `PLAN_INCOMPLETE` 强制审批，结论不变 |
| IMP-18 | A | 酒店某晚晚于 `valid_until` 也视为不可订 | §6.3 `valid_until` 是协议有效期；超期价格不可信 |
| IMP-19 | A | 规则集可用的条件：7 个封闭类型各有确认规则、住宿上限同时覆盖 employee 与 manager、同类同条件无重复、每个城市档位都有住宿和补贴金额；否则拒绝评估（发布 422） | 运行时不猜测；与 §6.5 发布 422 同一检查 |
| IMP-20 | A | admin 不能作为申请人参与规则评估（无对应住宿级别） | §6.4 admin 不能创建 Trip |
| IMP-21 | A | 状态机新增 `abort`：VALIDATING/PLANNING 期间出现意外失败时回到 COLLECTING（revision 不变）并写 `error` 审计，不保留半成品方案 | §6.8 未规定异常路径；避免 Trip 卡在中间状态 |
| IMP-22 | A | 无合规交通候选时选价格最低的不合规候选并记 `exception_legs` | REQ-013“选择超标最少的可信候选并标记例外”；交通违规（舱位/提前）无可比的超标金额，价格是唯一可量化维度 |
| IMP-23 | A | 酒店超标比较时，超标金额相同的情况下协议酒店先于备用酒店 | 协议库优先（REQ-011）；结果可复现 |
| IMP-24 | A | Provider 响应中任一条目 Schema 不合格，整份响应作废并降级为待人工补录 | §6.6 要求 Schema 校验；部分采信会让被篡改的响应影响候选集合 |
| IMP-25 | A | 当天往返时酒店 `status=not_needed` 且不调用酒店 Provider | 与 IMP-15 一致；api.md 增加该枚举值 |
| IMP-26 | A | 行程骨架不编造时刻：只有交通项带时刻，其余 `time=null`；来自申请人槽位的条目 `source=null` | §6.1 `source` 是数据来源枚举，申请人输入不属于其中任何一项；不增加枚举值（同 IMP-02） |
| IMP-27 | A | `verify_choice` 比较整条候选而非仅 ID | §6.6 同时禁止“修改价格”和“伪造 ID” |
| IMP-28 | A | 每次提交新建 LangGraph 线程：`thread_id = <trip_id>:rev-<n>:run-<8 位 hex>` | 同一 revision 可能多次提交（阻断后修改、D-21、意外中止后重提）；复用线程会让新运行接上旧 checkpoint |
| IMP-29 | A | 每次状态变化写一条 `status_changed` 审计（from/to/event/revision），与业务审计并存 | REQ-020 要求可还原全过程；状态守卫与审计在同一函数，不会漏记 |
| IMP-30 | A | 同一 Trip 的提交运行与审批决定共用进程内锁，提交运行持锁直到流结束 | 防止决定抢在审批节点 interrupt 之前执行；Demo 为单进程部署，跨进程由数据库状态守卫兜底 |
| IMP-31 | A | 行程摘要输出为空、超过 400 字或含金额时丢弃，`summary.status=unavailable` 并写 `error` 审计 | §4.7 模型只能写摘要段落，金额只来自代码计算；检查失败不伪造段落 |
| IMP-32 | A | `real` 模式的行程摘要在接入 DashScope 前如实报告“不可用”，不以模板冒充模型输出 | 协议要求模拟与真实集成明确区分；接入在 TASK-006，调用计入成本台账 |
| IMP-33 | A | 当前 revision 的审批卡已处理时返回 409 `ALREADY_DECIDED`（带首次决定），优先于 `INVALID_STATE` | §4.6“不同 Key 对已处理 revision 再决策返回 409”；更具体的错误码便于客户端解释 |
| IMP-34 | A | `delivery_error` 与 `delivery_failed` 审计只记录异常类型和固定文案，不记录异常文本 | 异常文本可能包含路径、SQL 或其他内部信息（HC-07 的保守做法） |
| IMP-35 | A | 提交流对每次实际状态转换发 `trip_status`（含 `DELIVERY_PENDING`），`VALIDATING→PLANNING` 用 `status(planning)` 表示；`approval_required` 后紧跟 `trip_status(PENDING_APPROVAL)` | 工作台只按 `trip_status` 更新状态徽标即可与服务端一致 |
| IMP-36 | A | Plan 增加 `summary` 对象（status/text/source/generator/note） | §7.1.1 录制输出含行程摘要；需要可区分 fake / recorded / 模型的承载字段 |
| IMP-37 | A | Demo reset 完成后写一条 `trip_id=null` 的 `demo_reset` 审计（含删除的 checkpoint 线程数） | reset 会清空审计表；留一条记录说明清空是有意操作 |
| IMP-38 | A | AuditEvent 视图暴露 `seq`（自增主键），审计列表按 `seq` 排序 | 毫秒级时间戳可能相同，`seq` 给出确定顺序 |
| IMP-39 | A | 审批决定时若线程没有待恢复的 interrupt（如 checkpoint 丢失），直接调用同一个 `apply_decision` 与 `deliver`（审计 `via="direct"`） | 业务库是权威状态；决定不应因 checkpoint 缺失而无法执行，且两条路径共用同一事务函数 |
| IMP-40 | A | 启动恢复：`VALIDATING/PLANNING` 中止回 `COLLECTING`；`APPROVED/AUTO_APPROVED/DELIVERY_PENDING` 重新交付；`PENDING_APPROVAL` 保持，等待决定恢复 checkpoint | 规划无外部副作用，重提成本低；交付受 `(trip_id, revision, kind)` 幂等约束；审批等待人工 |
| IMP-41 | A | reject 与 request_changes 的意见去除首尾空白后必须非空，否则 422 `COMMENT_REQUIRED`；approve 的意见可选 | §4.6 两种决定都要求“必须填写非空意见” |
| IMP-42 | A | 审批 403 先后：非 manager `FORBIDDEN` → 本人 `SELF_APPROVAL` → 非直属 `NOT_DIRECT_MANAGER` | 经理审批自己的申请时给出更准确的原因；均为 403，不改变状态码语义 |
| IMP-43 | A | 助手追问/确认文本由代码按槽位状态模板生成，`token` 事件 `generator="template"`；模型只做抽取 | “只追问缺失或非法项、不重复追问”可由构造保证并可测试；避免模型文字与槽位状态不一致；减少一次模型调用 |
| IMP-44 | A | 对话中抽取出的格式非法值不写入槽位，放入 slots 事件的 `invalid_fields`（`{field, code, message, value}`）并重新追问；Trip 中已有的合法值不被覆盖 | D-14：格式非法留在 COLLECTING 追问；不写入可避免把错误值固化进审计后的槽位 |
| IMP-45 | A | 对话接口的 Idempotency-Key 可选；提供时与槽位写入、`message_processed` 审计同一事务保存；抽取失败不保存 | §1.4 规定 messages 可选；失败不保存与“4xx 不保存”的语义一致，客户端可用同一 Key 重试 |
| IMP-46 | A | 对话使 `POLICY_BLOCKED` 回到 `COLLECTING` 时，对话流在 `slots` 之后发 `trip_status` | §1.5 要求提交流对每次实际状态转换发 `trip_status`；对话流同样适用，前端据此刷新按钮状态 |
| IMP-47 | A | `POST /trips {text}` 抽取失败时仍返回 201，Trip 已创建，错误放在 `assistant.error`；text 格式非法（空/超长）则 422 且不创建 | api.md #3 “503 模型不可用（Trip 仍创建，返回 assistant.error）”两句并存，取“已创建资源用 201”的一致解释；员工可继续用 #6 或 #7 |
| IMP-48 | A | `fake` 模式的图片抽取读录制输出（`recorded/invitations.json`），无对应条目即 503 | 图片无法用确定性规则解析；录制条目来自 TASK-001 真实调用并标注 `source: model`，不是伪造 |
| IMP-49 | A | 新审计类型：`message_processed`、`file_uploaded`（`trip_id=null`）、`image_extracted`、`image_confirmed`、`fault_changed`（`trip_id=null`） | REQ-020 要求关键操作可追溯；上传与故障开关不属于某个 Trip |
| IMP-50 | A | 上传校验顺序 413 → 415 → 403（admin 不能上传） | 与 §1.3 “字段格式先于权限”一致；格式错误无需进入身份语义 |
| IMP-51 | A | 图片确认时值为 null 的字段跳过（不清空已有槽位）；`fields` 只允许邀请函五个字段；省略 `fields` 表示按识别结果原样确认 | 邀请函通常不含出发地与预算，null 表示“图片里没有”，不应抹掉员工已填的值 |
| IMP-52 | A | 图片识别失败返回 503 `MODEL_UNAVAILABLE`（details 带 `retryable`），并写 `error` 审计（stage=image_extraction） | §1.2 的 503 语义；不可重试的情形（无录制条目、预算暂停）`retryable=false` |
| IMP-53 | A | `GET /trips` 默认 scope：employee/manager 为 `mine`，admin 为 `all`；非法取值 422，角色不允许的 scope 403；响应带 `scope` | api.md #4 未给默认值；admin 只有 `all` |
| IMP-54 | A | 上传文件只对上传者本人可见：用他人的 upload_id 发起识别返回 404 `UPLOAD_NOT_FOUND` | 与“不可见即 404”的读取语义一致，避免借别人的图片 |
| IMP-55 | A | 行程单接口返回 `{trip_id, revision, created_at, itinerary}`，该 revision 未交付时 404 `ITINERARY_NOT_FOUND` | api.md #17 只写“行程单 JSON”；带 revision 便于邮件链接核对版本 |
| IMP-56 | A | `uv run travel-desk` 配置 INFO 日志（httpx/httpcore/aiosqlite 降为 WARNING），启动恢复报告写日志 | 运维与 AC-010 证据需要看到重启后恢复了什么；日志只含 ID、计数与异常类型 |
| IMP-57 | A | 浏览器黄金路径（§14 第 5 条）与各 AC 的截图放在 TASK-007 三角色工作台完成，TASK-006 不另做临时 UI | 契约 §10 中 TASK-006 的产出是 API/权限/上传，界面属于 TASK-007；临时 UI 会被替换，其截图也不能作为最终证据 |
| IMP-58 | A | AC-011 的“交付写入失败”通过测试钩子 `Workflow.delivery_fault_hook` 注入（`export_api_evidence.py` 在进程内设置），重试走公开的 `POST /delivery/retry` | 按契约 §4.7 与 REQ-022：交付写失败只通过自动化测试钩子注入，不进入管理员故障面板 |
| IMP-59 | A | 重新校验后的 revision 使用无 checkpoint 的新 thread_id，对其审批卡的决定走 `decide()` 的直接路径 | 旧 revision 的图中断点不能用于新卡；直接路径与图节点共用 `apply_decision` + `deliver`，语义一致 |
| IMP-60 | A | 重新校验复用方案 JSON，只更新 `revision`/`policy_version`；交通、酒店的例外标记保留选择时的结论，违规与审批理由取重判结果 | D-12 要求复用已选候选、不重新选择；例外标记描述的是“选择时没有合规候选”，与制度重判是两件事 |
| IMP-61 | A | #14 响应的 `outcome` 是规则结论（`PENDING_APPROVAL`/`AUTO_APPROVED`/`POLICY_BLOCKED`，D-21 时 `no_approver`），`trip.status` 是包含交付在内的最终状态 | api.md 只给出字段名；自动通过后同一请求内已交付，两者需要区分 |
| IMP-62 | A | 重新校验时若申请人已无直属经理（D-21），按 IMP-06 处理：不建卡、方案只进审计、回 `COLLECTING`（新 revision），200 且 `outcome="no_approver"` | 契约未覆盖；与提交路径的 D-21 处理保持一致，不留下无人可批的卡 |
| IMP-63 | A | 偏好接口响应为 `{user_id, preferences}`；PUT 整体替换；每次写入都审计（含 `changed`）；格式 422 先于 404/403；用户不存在 404 `NOT_FOUND` | api.md #24 只写“当前偏好”；遵循 §1.3 错误顺序 |
| IMP-64 | A | 引用定位只做 §4.8 允许的规范化后逐字匹配，可跨行/跨页（定位为 `line:N-M`/`page:N-M`）；不做模糊匹配、不忽略标点，找不到即 `invalid_quote` | HC-02 要求引用可追溯；宽松匹配会让改写过的“引用”通过 |
| IMP-65 | A | 录制抽取按文档规范化全文的 SHA-256 匹配；未录制的文档在 fake/recorded 模式下返回 503 `MODEL_UNAVAILABLE`（`retryable=false`） | AC-005 要求使用独立测试版本号，录制输出不能绑定版本号；未录制时如实报告不可用，不臆造候选 |
| IMP-66 | A | 规则 ID 由代码按类型前缀与出现顺序分配（`RULE-HOTEL-001`…，非封闭类型为 `RULE-OTHER-nnn`），不采用模型给出的 ID | 与种子 ID 一致，保证 S-12 中 v1.2/v1.3 的同一条规则 ID 相同；模型输出不决定标识 |
| IMP-67 | A | 7 类以外的候选在抽取时由系统直接标为 `unsupported`，视为已处理，不阻止发布 | §4.2 要求标记而非丢弃；它们本就不能被确认，若要求管理员逐条再点一次没有信息增量 |
| IMP-68 | A | value 或 condition 与当前值不同即视为修改，必须给出去空白后非空的 `modification_reason`；提交与当前值相同的 value 不算修改 | 契约写“数值修改理由”；condition 改变同样改变规则含义 |
| IMP-69 | A | `PATCH` 的 status 取值不在 `confirmed\|rejected\|unsupported` 内 → 400 `INVALID_RULE_STATUS`（与 `INVALID_DECISION` 同属“非法操作值”）；多余字段、缺 status、value 非对象 → 422 `VALIDATION_ERROR` | 遵循 api.md §1.3 的 400/422 划分 |
| IMP-70 | A | 新增错误码：404 `POLICY_VERSION_NOT_FOUND`、`RULE_NOT_FOUND`；409 `POLICY_NOT_DRAFT`（对非 draft 抽取、审核、发布）；422 `EMPTY_DOCUMENT`、`INVALID_RULE_SHAPE`、`RULE_SET_INVALID` | api.md 只写“404”“非 draft”，前端需要可区分的 code；`RULE_SET_INVALID` 覆盖 `MISSING_RULE_TYPES` 以外的规则集问题（如同一档位两条已确认规则） |
| IMP-71 | A | 发布前校验与运行时完全一致：直接调用 `RuleSet.from_rules`；任何封闭类型（含 hotel_cap 的 employee/manager 两个级别）缺已确认规则 → `MISSING_RULE_TYPES`，其余问题 → `RULE_SET_INVALID` | 契约要求“每类≥1条 confirmed”；若发布校验比运行时宽松，发布后的版本会让 Trip 校验失败 |
| IMP-72 | A | #19 对所有角色开放：非 admin 看不到 draft（404），且只看到 `confirmed` 规则；admin 看到全部候选与 `rule_counts` | 契约写“任意 / admin（含规则）”；已发布规则本就出现在违规引用中，未审核的候选与草稿不应外泄 |
| IMP-73 | A | 制度写操作用一把进程内锁串行化，模型调用在锁外、写入前在锁内复查状态 | 单进程 Demo（§3）；避免“抽取覆盖”与“发布”交错，同时不让模型调用阻塞其他写操作 |
| IMP-74 | A | 上传接口中 413/415 位于 422 字段格式之后、404/403 之前（#9 已采用同一位置） | 与 #9 保持一致；文件本身不可用时先告诉调用方文件问题 |
| IMP-75 | A | 制度问答按“条款下的连续行段落”切块（上限 600 字），引用原文即块文本、定位复用 `locate`；条款前与一级标题下的文字归 `制度信息` | 引用必须能逐字回到原文（§4.8）；块与段落对齐使每条引用都是一段完整的规定，且不需要模型书写原文 |
| IMP-76 | A | BM25 手写实现（jieba 分词 + 停用词），不引入新的检索依赖 | jieba 已在依赖中；BM25 只有几十行，可确定性测试，fake 模式零网络 |
| IMP-77 | A | 嵌入走 openai `AsyncOpenAI.embeddings.create`（1024 维、每批 10），按“模型 + 文本 SHA-256”在内存与 `data_dir/cache/` 缓存，只存向量 | 真实响应带 usage 可精确记账；缓存避免同一版本重复计费；不落原文，缓存文件不扩散制度内容 |
| IMP-78 | A | `real` 默认 `vector`，`TRAVEL_DESK_RETRIEVAL=hybrid` 可切换；`fake`/`recorded` 固定 BM25 | REQ-029/AC-025：混合检索只有主指标严格更好才保留，打平保留纯向量；HC-06 不允许 fake 访问网络 |
| IMP-79 | A | 由已确认规则推出条款依赖（分档上限 → 城市分类），排名后追加未命中的关联条款块，不参与排序 | 真实冒烟第 1 轮两道城市题因 POL-1 未被检索而拒答；追加而不重排保证 Recall@3 口径不变，依赖来自已审核的规则而非人工词表 |
| IMP-80 | A | fake/recorded 用确定性抽取式回答器（`generator="fake:extractive"`），明确不能作为指标 | 接口与拒答路径需要零网络可测；D-08 禁止用 fake 输出计算质量指标 |
| IMP-81 | A | 引用由代码终审：只认模型看到过的块 ID；无有效引用的“可回答”降级为拒答；拒答引用一律清空、文本固定 | REQ-029 要求可回答必带引用、拒答不得附引用；固定文案使“不知道”可被评测与前端稳定识别 |
| IMP-82 | A | #18 请求体拒绝未知字段（422），`question` 去空白后 1–500 字 | 与其余写接口一致（`docs/api.md` §1.3）；500 字足够任何制度问题，限制模型输入成本 |
| IMP-83 | A | 缺省版本为当前 published；可指定 archived 版本；draft 仅 admin 可问；无 published → 404 `POLICY_VERSION_NOT_FOUND` | AC-017 需要按版本作答；与 #19 的可见性一致（IMP-72），未发布草稿不向员工外泄 |
| IMP-84 | A | #18 响应额外返回 `generator` 与 `retrieval`（方式、排名块、补充块） | 区分 fake 与真实输出（§7.1.1 的标注要求）；评测与前端需要知道依据来自哪些块，额外字段不改变契约字段语义 |
| IMP-85 | A | 每次回答写 `policy_question` 审计（`trip_id=null`），问题只保留前 200 字并标注截断 | 问答是员工获取制度依据的操作，需可追溯（哪一版、引用了哪些条款）；截断限制审计体积，审计不保存模型原始输出 |
| IMP-86 | A | 启动时显式注册 `.js` → `text/javascript`、`.css` → `text/css` | Windows 上 Python `mimetypes` 会读取注册表的扩展名关联，部分机器把 `.js` 映射为 `text/plain`，浏览器会拒绝加载 ES module；显式注册使结果与机器配置无关（预防性措施，本机未复现该问题），`tests/test_workbench.py` 断言响应类型 |
| IMP-87 | A | 前端路由带角色列，与服务端权限一一对应；越权页面只显示“无权访问”，接口仍由服务端判定；测试逐页比对界面与服务端 | AC-014 要求“各角色只能看到和完成权限矩阵允许的操作”；只靠导航隐藏时，直接输入地址仍会显示用不了的表单 |
| IMP-88 | A | 审批卡的 `policy_version` 不是当前发布版本时，审批页提示“制度已更新”，并链接到可重新校验的详情页；不阻止审批 | D-12 的重新校验由申请人或直属经理手动触发，契约没有规定旧版本审批卡在发布后失效；提示让经理知道有新版本，审批语义不变 |
| IMP-89 | A | 要求修改后（Trip 回 `COLLECTING`、revision +1），员工详情页从审计中最近一条 `decision_recorded`（前一 revision）显示经理意见 | 意见记录在旧 revision 的审批卡上，当前 revision 没有审批卡；不新增接口字段，只读已有审计 |
| IMP-90 | A | 规划器的酒店例外说明用元显示上限（`format_yuan`），不再写“分/晚” | 说明文字直接展示给员工和经理；金额字段本身仍是整数分（HC-02） |
| IMP-91 | A | `rules_evaluated` 审计增加 `source="policy"`；`approval_requested` 审计增加 `sources`（与审批卡 `sources` 相同） | AC-015 要求审计中出现规定来源；之前只有 `plan_created` 快照带来源，没有违规的 Trip 的审计里看不到 `policy`。只增加字段，已有字段和事件类型不变 |
| IMP-92 | A | AC-015 的真实模式部分用 `scripts/source_scan_real.py` 验证：S-01/S-03 槽位直接 PATCH（不调用抽取），真实摘要 2 次 + 问答 1 题，结果用与自动测试相同的扫描器检查 | “真实模式结果”需要完整的 API 与审计 JSON；TASK-006/008 的冒烟只保存了摘要字段。最小化调用次数控制费用（本次 0.0853 元，计入台账） |
| IMP-93 | A | 审计时间线中天气 Provider 调用显示“请求 N 天”，不显示“候选 0 个” | 天气返回逐日数据而不是带 ID 的候选，`candidate_ids` 恒为空；只改前端文字，审计内容不变 |
| IMP-94 | A | `/` 与 `/static/*` 响应带 `Cache-Control: no-cache`（静态文件按 ETag 返回 304） | 无构建步骤、文件名不带版本号；演练中浏览器两次启发式缓存旧模块，更新后仍运行旧代码。API 响应不受影响 |
| IMP-95 | A | `request_changes` 把旧审批卡置为 `superseded`（不再是 `decided`）并追加一条 `approval_superseded` 审计事件（`reason="request_changes"`），与 `revalidate` 留下的失效语义一致 | S-08 场景要求“旧审批失效”可审计；此前 `request_changes` 只把卡标记为 `decided`，审计里看不出哪张卡不再当前、为什么。schema 的 `status` CHECK 已含 `superseded`；只影响 `request_changes` 分支，`approve`/`reject` 仍为 `decided`，既有事件类型与字段不变（HC-11 追加式） |
| IMP-96 | A | 修正 `network_guard._caller()` 用 `sys._getframe()` 取代不存在的 `threading.currentframe()` | 正向对照测试 `tests/test_network_guard.py` 暴露：fake 模式因从不外联而不触发该分支，但 real 模式每次调用 DashScope 都会 `record()`→`_caller()` 崩溃。修复后 blocked/allowed 两条路径都验证通过 |
