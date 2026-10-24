# AC-026 演示脚本复演记录（第一阶段 · 第二次 · IMP-97 修复后）

> **为什么再演一次**：IMP-97 修复（commit `ca3a9f7`）改的是**运行中服务进程内安装的出站网络守卫**
> （`network_guard._local` / `_classify` / `record`，并新增 `_host_text` 归一化与 fail-closed 判定）。
> 第一阶段演练（[TASK-012/ac026-rehearsal](../../TASK-012/ac026-rehearsal/README.md)）在修复**之前**完成，
> 修复后只跑过 pytest 与脚本化 E2E，**浏览器路径没有再走过**。本目录是修复后由实施负责人
> 按 `docs/demo-script.md` §1／§2 **重新实际走一遍两套脚本**的留证。
>
> 按 §3 两段式协议，**第二阶段仍由项目所有者亲自演练作为最终确认**；本目录不代替所有者签署。
> **第二阶段已完成**：所有者亲自演练确认无异常，AC-026 契约判据全部满足。

## 0. 结论速览

| 项 | 结果 |
|---|---|
| 3 分钟脚本 | **走通**：S-01 黄金路径 一句话 → 只追问缺失项 → 提交 → 4,760.00 元 / 超 760.00 元 / 审批理由 2 项 → 李四批准 → `DELIVERED` → Outbox 恰好 1 封 |
| 10 分钟脚本 | **走通**：10 个节拍逐条实测或引用专属证据；S-09 故障降级**现场 UI + API 双核对一致** |
| 计时 | 演示动作分段实测合计 **约 2 分 25 秒**（3 分钟脚本，不含口播与取证）；10 分钟脚本执行段 **6 分 27 秒**（不含口播）。两者均在目标 **≤3:30 / ≤11:00** 内 |
| IMP-97 修复后的副作用检查 | **无**：全程 `attempts=0`、`external_attempts=0`、**`blocked=0`**——fail-closed 没有在真实服务里误伤任何一次本地/合法调用；`var/network-guard.jsonl` 未生成（无尝试即无行） |
| 全量回归 | `env -u DASHSCOPE_API_KEY uv run --offline pytest -q` → **319 passed in 14.26s**（第一阶段为 283；IMP-97 把守卫单测从 14 增到 50） |
| 费用 | 演练全程 **fake 模式，零真实模型调用、零费用**；台账累计仍为 **2.4900 元 / 上限 50 元**（暂停线 40 元） |
| 演练发现的卡顿与修订 | **4 处**（3 处脚本文案与界面不符、1 处第一阶段计时归因错误）——见 §5，均已最小化修订并复验 |
| 演示终态 | 已 `demo/reset` 复位到**干净起点**并逐项核对（§6），第二阶段可直接开演 |

## 1. 演练环境（实测，非声明）

服务为 IMP-97 修复后重启的进程（PID 13416，`.venv\Scripts\travel-desk.exe`），fake 模式，
`http://127.0.0.1:8000`。演练**开始**（13:54:07）与**结束复位后**（14:07:02）分别实测：

```text
GET /api/health            → {"status":"ok","db":"ok","demo_today":"2026-10-20","llm_mode":"fake"}
GET /api/health/providers  → llm_mode=fake；api_key_configured=null（不暴露密钥，AC-022 / F-03 / HC-07）；
                             network.installed=true, allowed_hosts=[]（fake 空允许清单）,
                             attempts=0, external_attempts=0, blocked=0, allowed=0, local=0,
                             external_hosts={}, blocked_by_caller=[]；
                             providers=[transport ok, hotel_contract ok, hotel_backup ok, weather ok]
GET /api/demo/faults       → 开始与结束均为 {"no_protocol_hotel":false,"transport_timeout":false,"weather_unavailable":false}
uv run pytest -q           → 319 passed in 14.26s
scripts/ledger_summary.py  → 运行期合计 2.0781 元；含手工行累计 2.4900 元 / 上限 50 元（暂停线 40 元）
```

原始响应留存：`api-health-providers-3min.json`（演练中段）、
`api-health-providers-post-rehearsal.json`（复位后）、`post-reset-clean-state.txt`（复位后七个端点逐项）。

> **费用诚实性**：整个复演在 fake 模式进行，**未产生任何真实模型调用**；台账累计值演练前后保持
> 2.4900 元不变（该值是 TASK-006d/008/011/013 历史真实冒烟与评测的既发生费用，非本次演练产生）。

> **`blocked=0` 是本次复演最关键的一项**：IMP-97 把「无法归一化的 host」从放行改为**拒绝**（fail closed）。
> 如果这个改动过严，真实服务里会出现**误伤**——本地调用被判 blocked、界面报错。实测整轮
> （S-01 全流程、S-09 降级、制度管理、RAG 问答、Demo 重置）**`blocked=0`、`attempts=0`**，
> 说明修复只收紧了「外部 + 不可归一化」这一格，没有改变 fake 模式下的任何既有行为。

## 2. 3 分钟脚本复演（HR / 业务价值）

**输入（S-01 黄金路径，与脚本逐字一致）**：张三 →「新建申请」→ 一句话
「下周去上海参加供应商大会，预算四千」→ 点「开始」→ 系统只追问缺失项 →
对话框补全「从北京出发，10月28日到10月30日」→ 点「发送」→ 槽位齐全 → 点「提交校验」。

Trip `c8ef1e16-42b2-4302-80c1-234a641510fc`，revision 1，绑定制度 **v1.2**。

### 2.1 逐节拍实测

| 节拍 | 实测观察 | 截图 |
|---|---|---|
| 0:00–0:20 身份选择页 | 标题「先知道是否合规，再决定怎么走。」；四个演示身份（张三/李四/赵六/王五）；右上状态条 `演示日期 2026-10-20 · 模型模式 fake`；页面明示「请求头 X-Demo-User 仅用于演示身份切换，不代表生产认证」 | `3min-01-identity-fake-mode.png` |
| 0:30–1:00 一句话抽取 | 系统回「已记录：目的城市 上海，出差事由 供应商大会，申报预算 4,000.00 元。还需要：出发城市、出发日期（具体到日，如 10月28日）、返回日期（具体到日）。」——**只追问缺失的三项，没有把「下周」猜成任何具体日期**；徽标「抽取：Fake（确定性测试替身，非模型）」 | `3min-02-slots-filled-no-date-guess.png` |
| 1:00–1:30 补全 + 提交 | 第二轮回「信息已齐全…请核对，确认无误后点击『提交』」；徽标追加「写入 出发城市、出发日期、返回日期」；「提交校验」按钮由 disabled 变为可点（必填齐全才放开） | 同上 |
| 1:30–2:00 方案 / 成本 / 理由 | 总额 **4,760.00 元**、申报预算 4,000.00 元、预算差**超出 760.00 元**；「阻断 0 项，违规 0 项，审批理由 2 项」；`OVER_AUTO_APPROVAL_LIMIT` 挂**依据 RULE-AUTO-001 · v1.2 POL-6**、**来源徽标 `policy`**、**定位 `v1.2 POL-6 · line:33`** 与条款逐字原文；交通/酒店/天气/日程各带 `demo_fixture`、摘要带 `model`、事由带「申请人填写」；摘要下方明示「生成方式：Fake（确定性测试替身，非模型）。摘要只概括文字，安排与金额以上文为准。」 | `3min-03-plan-cost-reasons.png` |
| 2:00–2:30 切李四批准 | 「待我审批」出现这张卡（成本明细、方案摘要、审批理由与条款引用与员工侧**完全一致**）；填意见 → 点「批准」→ 状态 `DELIVERED` | `3min-04-approval-card-lisi.png` |
| 2:30–3:00 切王五看运维 | Outbox **恰好 1 封**：`[差旅行程单] 北京→上海 2026-10-28～2026-10-30（revision 1） · trip_delivery · 收件 张三 · 抄送 李四 · 10/20 13:58:08`；审计时间线 #58–#76 共 **19 条**，从输入 → 绑定条款 → Provider → 规则 → 审批完整 | `3min-05-admin-outbox-one-email.png` |

### 2.2 计时（分段实测）

系统侧时延取自**应用内审计时间线**（毫秒精度，`api-trip-s01-audit.json`）：

```text
#64 submitted          2026-10-20T05:55:51.127Z
#66 policy_bound       2026-10-20T05:55:51.146Z   （+19 ms，绑定 v1.2）
#68–#71 provider_call  2026-10-20T05:55:51.161Z   （transport×2 / hotel_contract / weather）
#73 plan_created       2026-10-20T05:55:51.161Z
#74 rules_evaluated    2026-10-20T05:55:51.162Z   （引用 7 条规则 RULE-CITY/MEAL/HOTEL/CLASS/ADVANCE/AUTO/DURATION @v1.2）
#76 approval_requested 2026-10-20T05:55:51.169Z
                       ────────────────────────
                       提交 → 审批卡：42 ms
```

演练墙钟（含取证动作，本地时间）：

| 段 | 起 | 止 | 实测 | 属于演示动作？ |
|---|---|---|---:|---|
| 身份页 → 选张三 → 新建申请 → 输入一句话 → 点「开始」 | 13:54:42 | 13:55:14 | 32s | 是 |
| 对话框补全 → 点「发送」→ 槽位齐全 | 13:55:14 | 13:55:31 | 17s | 是 |
| 点「提交校验」→ 审批卡生成 | 13:55:31 | 13:55:51 | 20s（系统侧 42 ms） | 是 |
| 整页截图 + 落盘 2 份 API JSON + 结构核对 | 13:55:51 | 13:57:28 | 97s | **否（取证）** |
| 切李四 → 打开卡 → 填意见 → 批准 | 13:57:28 | 13:58:09 | 41s | 是 |
| API 核对 `DELIVERED` + Outbox 计数 | 13:58:09 | 13:58:20 | 11s | **否（取证）** |
| 切王五 → 运维 → 看 Outbox/审计（含截图） | 13:58:20 | 13:59:28 | 68s | 部分（截图约占 30s） |

- **墙钟合计 4 分 46 秒**（13:54:42 → 13:59:28），其中**取证动作约 1 分 38 秒**（演示时不做）。
- **演示动作分段相加约 2 分 25 秒**；按脚本台词量估算加口播后约 3 分钟，落在目标 **≤3:30** 内。
  > 口径说明：2 分 25 秒是**分段实测相加**，「加口播约 3 分钟」是**估算**，不是实测——
  > 本阶段用自动化点击走脚本，没有真的念台词。第一阶段（人工走 + 口播）实测整套约 3 分钟，两者一致。

### 2.3 界面与 API 双核对（逐项一致）

| 判据 | 界面显示 | API 实测（`api-trip-s01-pending.json` / `-delivered.json`） |
|---|---|---|
| 状态流转 | 收集信息 → 待审批 →（批准后）已交付 | `COLLECTING → VALIDATING → PLANNING → PENDING_APPROVAL → DELIVERED` |
| 总额 | 合计 4,760.00 元 | `cost_breakdown.total_fen = 476000` |
| 成本拆解 | 去程 1,800.00 / 返程 1,700.00 / 住宿 2 晚 × 480.00 = 960.00 / 伙食 3 天 × 100.00 = 300.00 | `outbound_fen=180000, return_fen=170000, hotel_per_night_fen=48000, hotel_fen=96000, meal_per_day_fen=10000, meal_fen=30000, nights=2, days=3` |
| 城市档位 | 「目的地 A 类城市；金额由代码按已发布规则计算。」 | `destination_tier="A"` |
| 预算差 | 超出 760.00 元 | `declared_budget_fen=400000, budget_delta_fen=76000` |
| 违规 / 阻断 | 「阻断 0 项，违规 0 项，审批理由 2 项」 | `violations=[]`, `blocks=[]`, `approval_reasons` 长度 2 |
| 理由 1 | `BUDGET_EXCEEDED`「总额 4,760.00 元 超过申报预算 4,000.00 元（超 760.00 元）」 | `code=BUDGET_EXCEEDED, actual_value=476000, limit_value=400000, unit=fen, lower_bound=false` |
| 理由 2 | `OVER_AUTO_APPROVAL_LIMIT`「…超过自动通过阈值 3,000.00 元」+ 依据 RULE-AUTO-001 · v1.2 POL-6 | `code=OVER_AUTO_APPROVAL_LIMIT, actual_value=476000, limit_value=300000, basis={rule_id:RULE-AUTO-001, policy_version:v1.2, clause_ref:POL-6}` |
| 条款引用 | `policy` 徽标 + `v1.2 POL-6 · line:33` + 条款逐字原文 | `rules_evaluated.refs` 含 `rule:RULE-AUTO-001@v1.2 POL-6` 等 7 条 |
| 摘要生成方式 | 「生成方式：Fake（确定性测试替身，非模型）」 | `plan.summary.generator = "fake"` |
| 方案完整性 | （无「不完整」提示） | `plan.incomplete=false, plan.missing=[], missing_components=[]` |
| 交付 | Outbox 1 封 `trip_delivery`，收件张三、抄送李四 | `delivery={error:null, attempts:1, itinerary_url:"…/itinerary?revision=1"}`；`/api/outbox` → `emails` 长度 1 |
| 审计条数 | #58–#76 | `audit.events` 长度 **19**，类型含 `trip_created / slots_changed / message_processed / status_changed / submitted / preference_snapshot / policy_bound / provider_call×4 / candidates_selected / plan_created / rules_evaluated / approval_requested` |

**SSE 帧与审计事件名不同、两侧都成立**：界面「最近一次提交过程」逐步渲染到「审批卡已发给 李四」，
对应 SSE 帧 `approval_required`；审计时间线落的是事件 `approval_requested`（#76）。
这与 AC-027 的断言口径一致（第一阶段曾把两者写混，见 TASK-013 README §4）。

## 3. 10 分钟脚本复演（技术面深挖）

墙钟 **14:00:03 → 14:06:30＝6 分 27 秒**（执行段，不含口播；目标 ≤11:00）。逐节拍：

| 节拍 | 主题 | 复演方式 | 实测结果 / 证据 |
|---:|---|---|---|
| 0:00–1:00 | 架构与确定性 | 现场 shell | `env -u DASHSCOPE_API_KEY uv run --offline pytest -q` → **319 passed in 14.26s**；`docs/architecture.md` 分层 |
| 1:00–2:00 | 三种 LLM 模式 | 打开文档 + 源码 | `docs/integration.md` §1 三行表（fake 零网络 / recorded 按 SHA-256 回放 / real 仅 dashscope）；`main.py:75–78` 四个 `build_*`（`build_slot_extractor`、`build_invitation_extractor`、`build_rule_extractor`、`build_policy_qa`）由**同一** `resolved` 配置装配 → 业务代码不感知模式 |
| 2:00–3:00 | 出站网络边界 | 现场 API | `/api/health/providers` → `installed=true, allowed_hosts=[], attempts=0, external_attempts=0, blocked=0`；**整轮演练结束后仍为 0**，`var/network-guard.jsonl` 未生成；AC-021 修复后证据见 [../ac021-post-fix/](../ac021-post-fix/) |
| 3:00–4:00 | 费用守卫 | 现场 shell | `scripts/ledger_summary.py` → 运行期 2.0781 元、累计 **2.4900 元 / 50 元**（暂停线 40）；`api_key_configured=null`（只读环境名、从不打印，F-03/HC-07） |
| 4:00–5:30 | 黄金路径 + 规则计算 | 复用 §2 的 S-01 | 总额 476000 分；`BUDGET_EXCEEDED` + `OVER_AUTO_APPROVAL_LIMIT` 进审批；7 条规则引用逐条落审计 |
| 5:30–6:30 | **故障降级（S-09）** | **现场 UI 实跑 + API 双核对** | 见 §3.1，截图 `10min-06-s09-fault-degradation.png` |
| 6:30–7:30 | 持久化恢复（S-10） | 引用专属真实证据 | [TASK-006/AC-010/](../../TASK-006/AC-010/)：`AC-010-restart.json` + `boot-1/2/3.log`（真实子进程强杀 → 同库重启 → checkpoint 恢复 → 只交付 1 次）。**演练中不杀进程**，避免破坏现场服务 |
| 7:30–8:30 | **制度编译（S-12）** | **现场 UI 视图 + API 核对** | 见 §3.2，截图 `10min-08-policy-management.png`、`10min-08-policy-v12-rules-citations.png` |
| 8:30–9:30 | **RAG 引用终审** | **现场 UI 实跑** | 见 §3.3，截图 `10min-09-rag-answerable-verbatim-citation.png`、`10min-09-rag-refusal-no-citation.png` |
| 9:30–10:00 | 评测与验收 | 打开文档 | `docs/acceptance.md`（含 IMP-97 已修复条目与 AC-027 整链真实模型行）、`artifacts/acceptance/TASK-011/README.md` 均在位且已更新 |

### 3.1 故障降级 S-09（现场 UI + API 双核对，本阶段核心验证点）

**操作**：王五「运维」页勾选 `transport_timeout`（界面开关，实测 `GET /api/demo/faults` →
`transport_timeout:true`）→ 切张三 → 一句话发起 **S-02 同输入**
「10月25日到26日从北京去上海客户拜访，预算两千五」（一次抽全六项槽位）→ 点「提交校验」。

Trip `70b1db88-84c7-42f2-b250-9270d11903c2`，revision 1，绑定 v1.2。

| 判据 | 界面显示 | API 实测（`api-trip-s09-degraded.json`） |
|---|---|---|
| 不崩溃 | 页面正常渲染，SSE 走到「完成：待审批」 | HTTP 200，`status="PENDING_APPROVAL"` |
| 交通待人工补录 | 「方案不完整：transport 待人工补录。」；去程/返程均显示「待人工补录」；附注「去程：交通 Provider 超时，待人工补录；返程：交通 Provider 超时，待人工补录」 | `plan.incomplete=true`, `plan.missing=["transport"]`, `plan.transport.status="manual_required"`, `outbound=null`, `return=null` |
| 合计空缺、给已知小计 | 合计「待补录（已知小计 **600.00 元**）」 | `total_fen=null`, `known_subtotal_fen=60000`, `transport_fen=null`, `missing_components=["transport"]` |
| **缺失不按 0 计入** | 「缺失部分：transport，**不按 0 计入**。」；预算差显示「—（方案不完整）」 | `budget_delta_fen=null`（**不是** 250000−60000 之类的假差值） |
| 已定价部分仍算清 | 住宿 1 晚 × 400.00 = 400.00；伙食 2 天 × 100.00 = 200.00 | `hotel_per_night_fen=40000, hotel_fen=40000, meal_per_day_fen=10000, meal_fen=20000, nights=1, days=2` |
| 审批理由 | 「方案不完整」`PLAN_INCOMPLETE`「方案不完整：交通待人工补录，不能自动通过」「缺失：transport」 | `approval_reasons=[{code:"PLAN_INCOMPLETE", message:"…不能自动通过", missing:["transport"]}]`；`violations=[]`, `blocks=[]` |
| 进入审批（S-02 同输入本应自动通过） | 状态「待审批」；审批卡 revision 1 等待李四 | `status="PENDING_APPROVAL"`, `current_approval` 指向 `u-lisi` |
| Provider 调用逐条 | 审计 #91/#92 `transport/demo_transport timeout，候选 0 个`；#93 `hotel_contract/demo_hotel ok，候选 2 个`；#94 `weather/demo_weather ok，请求 2 天` | `provider_call` payload：`{provider:"transport", status:"timeout", error:"交通 Provider 超时（故障注入 transport_timeout）"}` ×2；`hotel_contract ok`；`weather ok` |
| 摘要如实反映缺失 | 「…去程交通待人工补录。入住星衡协议酒店·滨江店，共 1 晚。返程交通待人工补录。」 | `plan.summary.generator="fake"`；摘要**没有**编造任何航班或金额 |

**系统侧时延（审计时间线，毫秒）**：

```text
#87 submitted          2026-10-20T06:01:45.885Z
#91/#92 provider_call  2026-10-20T06:01:45.910Z / .912Z   transport timeout
#93 hotel_contract ok  2026-10-20T06:01:45.912Z
#94 weather ok         2026-10-20T06:01:45.912Z
#97 rules_evaluated    2026-10-20T06:01:45.912Z
#99 approval_requested 2026-10-20T06:01:45.918Z
                       ────────────────────────
                       提交 → 审批卡：33 ms
```

**演练后复位**：`POST /api/demo/faults {"fault":"transport_timeout","enabled":false}` →
三个开关全 `false`（§1 复位后再次复验）。

### 3.2 制度编译 S-12（现场 UI 视图 + API 核对）

- 王五「制度管理」版本表：**v1.0 已归档**（生效 2025-01-01，markdown，sha256 `1b2d0566…`，规则 **8/0/0/0/0**）、
  **v1.2 已发布**（生效 2026-01-01，sha256 `72f941b6…`，规则 **8/0/0/0/0**，发布 `2026-10-20T05:44:59Z`）。
  上传表单提示「支持 Markdown（UTF-8）或文本型 PDF，≤10 MiB；**扫描件 PDF 会被拒绝**；上传后为草稿，
  需要抽取、逐条审核后才能发布」。截图 `10min-08-policy-management.png`。
- 打开 **v1.2 详情**：「非草稿版本只读」；`GET /api/policies/v1.2` → `rule_counts`
  `{candidate:0, confirmed:8, rejected:0, unsupported:0, invalid_quote:0}`，八条规则逐条为
  `status=confirmed`、`quote_status=valid`、`confirmed_by=u-wangwu`，**ID 由代码按类型+顺序分配**（IMP-66），
  引用定位精确到行：

  | 规则 ID | 类型 | 条款 | 定位 | 值（整数「分」/天） |
  |---|---|---|---|---|
  | `RULE-CITY-001` | city_tier | POL-1 | `line:9` | A=北京/上海/广州/深圳，B=杭州/南京/… |
  | `RULE-HOTEL-001` | hotel_cap | POL-2 | `line:13` | A 50000 / B 40000 / C 30000（员工） |
  | `RULE-HOTEL-002` | hotel_cap | POL-2 | `line:15` | A 65000 / B 50000 / C 40000（经理及以上） |
  | `RULE-MEAL-001` | meal_allowance | POL-3 | `line:21` | A 10000 / B 8000 / C 6000 |
  | `RULE-CLASS-001` | cabin_class | POL-4 | `line:25` | allowed = economy, second_class |
  | `RULE-ADVANCE-001` | advance_booking_days | POL-5 | `line:29` | days = 3 |
  | `RULE-AUTO-001` | auto_approve_limit | POL-6 | `line:33` | value_fen = 300000 |
  | `RULE-DURATION-001` | trip_duration_limit | POL-7 | `line:37` | days = 15 |

  截图 `10min-08-policy-v12-rules-citations.png`；原始响应 `api-policy-v1.2.json`。
- **完整「上传 v1.3 → 抽取 → 逐条确认 → 发布 → 回张三详情按新版本重新校验」流程不在演练中执行**
  （会改动种子制度状态）；引用专属真实证据 **AC-024 `passed`**（revision 1→2、绑定 v1.3、旧卡 superseded、
  新卡 pending、新增 `HOTEL_OVER_CAP 48000>45000`、审计保留 v1.2/v1.3 两次判断）与 **AC-005 `passed`**。

### 3.3 RAG 引用终审（现场 UI 实跑）

- **可回答**：王五「制度问答」（版本下拉＝「当前发布版本（v1.2）」）问
  「去上海出差住宿每晚最多能报多少？」→ 回答
  「根据 v1.2 POL-2：员工住宿上限：A 类城市每晚上限 500 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。」；
  **引用**区带 `policy` 徽标 + `v1.2 POL-2 · line:13` + **逐字条款原文**；
  生成方式标注「Fake 抽取式（测试替身，非模型）」；检索明细可展开为 `bm25`（fake 模式固定 BM25，零网络）。
  截图 `10min-09-rag-answerable-verbatim-citation.png`。
- **无答案（拒答）**：问「公司年终奖什么时候发放？」（制度外）→ 回答
  「**不知道**：当前制度（v1.2）中没有找到能回答这个问题的条款。」；界面明示
  「**拒答时不展示引用。**」，且**渲染结果里确实没有任何引用块**（对照上一条的引用区）。
  截图 `10min-09-rag-refusal-no-citation.png`。
- 页面顶部口径与实现一致：「回答只依据所选版本的制度原文；找不到依据时会明确回答『不知道』，不做猜测。
  引用原文与定位由代码给出。」
- real 模式指标不在演练中联网重跑，引用已验收证据 **AC-004 / AC-017 `passed`**
  （引用命中 20/20、关键数值 11/11、拒答 5/5）与 **AC-025**（vector 与 hybrid Recall@3 打平 → 按 D-17 保留纯向量）。

## 4. 与第一阶段演练的差异（IMP-97 修复前 → 修复后）

| 项 | 第一阶段（修复前，TASK-012） | 本次（修复后，commit `ca3a9f7`） |
|---|---|---|
| `uv run pytest -q` | 283 passed | **319 passed in 14.26s** |
| `tests/test_network_guard.py` | 14 项 | **50 项**（新增 bytes 四途径阻断、四途径允许清单**正向**透传、`connect` 兜底 fail-closed、`repr` 抛异常仍可取证、`None` 通配绑定记 `host_form`） |
| 台账累计 | 2.0547 元 / 50 | **2.4900 元 / 50**（差额来自 AC-027 整链真实运行，非本次演练） |
| 守卫 `external_attempts` | 0 | **0**（结论不变） |
| 守卫 `blocked` | 0 | **0**（fail-closed **未误伤**任何合法调用——本次新增的关键核对项） |
| 守卫日志 schema | 无 `host_form` | 新增 `host_form`（附加字段，旧日志不改写） |
| 浏览器路径 | 修复前走过 | **修复后首次完整走过**（S-01 全流程 + S-09 + 制度管理 + RAG + 重置） |
| S-01 界面/金额结论 | 4,760.00 元、超 760.00 元、POL-6 引用、1 封交付 | **逐项相同** |

## 5. 演练发现的卡顿与修订

四处，全部是**文案与界面不符**或**归因错误**，没有一处是产品缺陷；均按最小化修订处理并复验。

**修订 1（已修复）：`demo-script.md` §1 的按钮名与界面不符。**
脚本写「点『发起申请』…→ 发送」「槽位齐后点 **提交**」，界面实际是
「**新建申请**」→ 输入后点「**开始**」→ 第二轮对话框才是「**发送**」→ 最后点「**提交校验**」。
照脚本念会在界面上找不到对应按钮。已改为实际标签（`docs/demo-script.md` 行 49–50）。

**修订 2（已修复）：`demo-script.md` §1 台词「判定违规」与界面矛盾。**
界面明确显示「阻断 **0** 项，违规 **0** 项，审批理由 **2** 项」；S-01 的正确说法是
「超申报预算 + 超自动通过阈值 → 需人工审批」，**不是**「违规」。台上说「判定违规」会被屏幕当场否证。
已改为「给出判断——**违规 0 项、审批理由 2 项**（超申报预算、超自动通过阈值）」。

**修订 3（已修复）：`demo-script.md` §2 费用数字过期。**
「累计约 2.05 元 / 50 元」→「累计约 **2.49** 元 / 50 元」（AC-027 整链真实运行后台账已变）。

**修订 4（第一阶段证据的计时归因错误，已加注解、未改写原文）：S-09 不存在「12 秒超时等待」。**
第一阶段 README §3.1 写「系统侧约 **12 秒**（含交通 Provider 超时等待）」。本次实测：
提交 `06:01:45.885Z` → 审批卡 `06:01:45.918Z`＝**33 ms**；且 `src/travel_desk/providers/demo.py:58–59`
在故障开关命中时**直接 `raise ProviderTimeout`，没有任何 `sleep`**——注入的「超时」不消耗墙钟。
第一阶段那 12 秒是**操作者从创建到点提交之间的人工间隔**（创建 11:54:56 → 规划完成 11:55:08），
被误归因为 Provider 等待。已在第一阶段 README 该处**追加更正注解**（原文保留不改写，
与所有者对旧守卫日志「只注解、不改写」的处置一致），避免台上讲出一个被审计时间线否证的数字。

**其余节拍**：脚本与界面/接口实际行为一致，无其他卡顿。

## 6. 演示终态：已复位到干净起点（第二阶段可直接开演）

用 `docs/demo-script.md` §0 的**完整命令**复位（该命令是第一阶段修订 1 的产物，本次再次实测有效）：

```bash
curl -X POST http://127.0.0.1:8000/api/demo/reset \
  -H "X-Demo-User: u-wangwu" -H "Content-Type: application/json" \
  -d '{"confirmation":"RESET_DEMO_DATA"}'
# → {"status":"reset"}
```

顺带复验文档声明的两个负向用例（照抄缺项会失败）：**缺 `X-Demo-User` → HTTP 401**、
**缺 `confirmation` → HTTP 422**，与文档一致。

复位后逐项核对（原始响应见 `post-reset-clean-state.txt`）：

| 端点 | 实测 |
|---|---|
| `GET /api/health` | `{"status":"ok","db":"ok","demo_today":"2026-10-20","llm_mode":"fake"}` |
| `GET /api/demo/faults` | 三个开关全 `false` |
| `GET /api/trips`（张三） | `{"scope":"mine","trips":[]}` |
| `GET /api/approvals`（李四） | `{"approvals":[]}` |
| `GET /api/outbox`（王五） | `{"emails":[]}` |
| `GET /api/policies`（王五） | v1.0 `archived`（8 confirmed）+ v1.2 `published`（8 confirmed），无草稿残留 |
| `GET /api/health/providers` | `network.installed=true, allowed_hosts=[], attempts=0, external_attempts=0, blocked=0`；`api_key_configured=null` |
| `GET /` | `200 text/html; charset=utf-8` |
| `var/network-guard.jsonl` | **不存在**（整轮零出站尝试，无行可写） |

> 演练产生的两个 Trip（S-01 `c8ef1e16…` 已交付、S-09 `70b1db88…` 待审批）与其审计、Outbox
> 已随重置清除——这是文档明示的**不可撤销**演示数据重置，属既定演示流程，不影响任何已留证证据
> （本目录的 JSON 与截图在重置前已落盘）。

## 7. 环境注意（需告知所有者，第一阶段未发现）

**8000 端口被另一个无关应用共用。** 实测 `Get-NetTCPConnection -LocalPort 8000 -State Listen`：

| 本地地址 | PID | 进程 |
|---|---|---|
| `127.0.0.1:8000` | 13416 | **本项目** `.venv\Scripts\travel-desk.exe`（Python 3.11） |
| `0.0.0.0:8000` | 17184 | **无关应用** `python -m uvicorn app.api.server:app --host 0.0.0.0 --port 8000`（uv 托管 Python 3.12） |

- 具体的回环绑定优先于通配绑定，实测 `http://127.0.0.1:8000/api/health` 与
  `http://localhost:8000/api/health` **都返回本项目**（`llm_mode:"fake"`）；
  `http://[::1]:8000` **无监听**（连接失败）。
- 风险：若浏览器把 `localhost` 优先解析为 IPv6 `::1`，会连不上（本项目只监听 IPv4 回环）。
  **建议第二阶段一律用 `http://127.0.0.1:8000`**，不要用 `localhost`。
- 处置：本次只重启/只杀过**本项目**的进程，**PID 17184 全程未动**（属另一个项目，未经授权不触碰）。
  若希望演示期间端口独占，需要所有者决定是否停掉那个无关服务——**我没有这个授权，也没有动它**。

## 8. 第二阶段交接（项目所有者最终确认演练）

服务已在运行、数据已复位到干净起点，可直接开演：

1. 浏览器打开 **<http://127.0.0.1:8000>**（用 `127.0.0.1`，理由见 §7），
   确认右上状态条 `演示日期 2026-10-20 · 模型模式 fake`。
2. 照 `docs/demo-script.md` 走 **3 分钟脚本**（§1 表，按钮名已按本次修订更正）与
   **10 分钟脚本**（§2 表）。重点核对：
   - S-05：只追问缺失的三项，**不**把「下周」猜成日期；
   - S-01：4,760.00 元 / 超 760.00 元 / **违规 0 项、审批理由 2 项** / `RULE-AUTO-001 · v1.2 POL-6 · line:33` 引用；
   - 批准后 `DELIVERED`，王五 Outbox **恰好 1 封** `trip_delivery`；
   - **S-09 现场 UI + API 双核对**（本次已按所有者要求逐条对照，见 §3.1）：交通「待人工补录」、
     合计空缺但给「已知小计 600.00 元」、「缺失部分：transport，**不按 0 计入**」、`PLAN_INCOMPLETE`、进待审批；
   - RAG：可回答带**逐字引用**与 `line:13` 定位、拒答**不展示引用**；
   - 制度管理：v1.2 **八条规则**逐条 `confirmed` / `quote_status=valid` / 引用定位到行。
3. 计时目标：3 分钟 ≤3:30、10 分钟 ≤11:00（本次实测演示动作约 2:25 / 执行段 6:27，均有余量）。
4. 演练完请把**所有者的计时、截图与最终确认**登记到本目录（或新建 `ac026-stage2-owner/`）；
   登记后 `docs/acceptance.md` AC-026 行的「第二阶段待所有者执行」保留点即可删除。
5. 若发现新卡顿，按 §5 的格式追加修订记录（区分「文案问题」与「产品缺陷」，后者需要证据 + 最小修订）。

## 9. 证据文件清单

| 文件 | 对应节拍 | 内容 |
|---|---|---|
| `3min-01-identity-fake-mode.png` | 3min 0:00–0:20 | 身份选择页：业务主张标题、四个演示身份、`模型模式 fake` 状态条、X-Demo-User 免责声明 |
| `3min-02-slots-filled-no-date-guess.png` | 3min 0:30–1:00 | 一句话抽取 + 只追问缺失三项（未猜日期）+ 第二轮补全后槽位齐全 + Fake 抽取徽标 + 审计 #58–#62 |
| `3min-03-plan-cost-reasons.png` | 3min 1:30–2:00 | 方案全文：交通/住宿/天气/日程、成本明细 4,760.00 元、审批理由 2 项 + POL-6 条款引用、来源徽标、审计 #58–#76 |
| `3min-04-approval-card-lisi.png` | 3min 2:00–2:30 | 李四「待我审批」的审批卡 revision 1（成本明细、方案摘要、审批理由与条款引用）与批准/要求修改/驳回三按钮 |
| `3min-05-admin-outbox-one-email.png` | 3min 2:30–3:00 | 王五「运维」页：故障开关（全关）、运行状态（Provider 全 ok）、Outbox 恰好 1 封 `trip_delivery`、重置入口 |
| `10min-06-s09-fault-degradation.png` | 10min 5:30–6:30 | S-09 交通超时降级：待人工补录、合计「待补录（已知小计 600.00 元）」、「不按 0 计入」、`PLAN_INCOMPLETE`、待审批、审计 #91/#92 timeout |
| `10min-08-policy-management.png` | 10min 7:30–8:30 | 制度管理版本表（v1.0 归档 / v1.2 发布，各 8/0/0/0/0，含 sha256 前缀）+ 上传草稿表单 + 扫描件 PDF 拒绝提示 |
| `10min-08-policy-v12-rules-citations.png` | 10min 7:30–8:30 | v1.2 详情：八条规则的代码分配 ID、类型、条款与行号定位、整数「分」值、确认人、只读 |
| `10min-09-rag-answerable-verbatim-citation.png` | 10min 8:30–9:30 | 制度问答可回答：`policy` 徽标 + `v1.2 POL-2 · line:13` + 逐字原文 + Fake 抽取式声明 + bm25 检索明细 |
| `10min-09-rag-refusal-no-citation.png` | 10min 8:30–9:30 | 制度问答拒答：「不知道」+「拒答时不展示引用。」且无任何引用块 |
| `api-trip-s01-pending.json` | 3min | 提交后 Trip 全量（`cost_breakdown` / `violations` / `approval_reasons` / `plan`），§2.3 双核对的 API 侧 |
| `api-trip-s01-delivered.json` | 3min | 批准后 Trip 全量（`status=DELIVERED`、`delivery.attempts=1`、`current_approval.decision=approve` 与意见原文） |
| `api-trip-s01-audit.json` | 3min | S-01 审计 19 条（毫秒时间戳），§2.2 的 42 ms 时延即取自此 |
| `api-outbox-after-approve.json` | 3min | 批准后 Outbox（1 封 `trip_delivery`，收件张三、抄送李四） |
| `api-trip-s09-degraded.json` | 10min | S-09 降级后 Trip 全量（`incomplete=true`、`total_fen=null`、`known_subtotal_fen=60000`、`budget_delta_fen=null`） |
| `api-trip-s09-audit.json` | 10min | S-09 审计 17 条，含两次 `transport timeout` 的 payload 与 33 ms 时延 |
| `api-policy-v1.2.json` | 10min | v1.2 全量：`rule_counts` 与八条规则的 ID/类型/条款/定位/`quote_status`/确认人/整数值 |
| `api-health-providers-3min.json` | 3min / 10min 2:00–3:00 | 演练中段守卫快照（`installed=true, allowed_hosts=[], external_attempts=0, blocked=0`） |
| `api-health-providers-post-rehearsal.json` | 复位后 | 整轮演练结束后的守卫快照（仍全 0，`api_key_configured=null`） |
| `post-reset-clean-state.txt` | §6 | 复位后七个端点的原始响应逐项留存 |

> 截图均为 fake 模式下**真实界面**（非合成、非拼接）；S-01 与 S-09 的关键结论另有**同时刻 API 实测值**
> 逐条交叉核对（§2.3、§3.1）。S-10 持久化恢复与 S-12 完整发布/重新校验引用各自专属真实证据
> （AC-010、AC-024/AC-005），不在演练中重复执行破坏性/状态变更操作。
> 本目录不含任何凭证；所有 JSON 已核对不含密钥值（`api_key_configured` 仅以 `null` 出现，HC-07）。
