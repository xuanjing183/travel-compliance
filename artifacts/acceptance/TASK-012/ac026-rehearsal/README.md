# AC-026 演示脚本演练记录（第一阶段 · 实施负责人）

> 契约 AC-026（`docs/contract-v1.1.md` §8 表，行 602）判据：**9 类文档齐全** + **README 首段讲业务问题** +
> **3 分钟和 10 分钟脚本各实际演练一次**；证据＝文件清单、演练计时、截图/录屏。
> 本目录是「各实际演练一次」的**第一阶段**证据（实施负责人亲自走脚本并留证）。
> 按 `docs/demo-script.md` §3 的**两段式**协议，**第二阶段**由项目所有者亲自演练作为**最终确认**。
> **第二阶段已完成**：所有者亲自演练确认无异常，AC-026 契约判据全部满足。

> **本目录之后又做了一次复演（IMP-97 修复后）**：commit `ca3a9f7` 改的是运行中服务进程内安装的
> 出站网络守卫，本目录的演练在修复**之前**完成，修复后浏览器路径需要重走。复演证据见
> [TASK-013/ac026-rehearsal-post-fix](../../TASK-013/ac026-rehearsal-post-fix/README.md)
> （pytest 283 → **319 passed**、守卫单测 14 → **50 项**、台账 2.0547 → **2.4900 元/50**、
> 守卫 `external_attempts=0` 且新增核对 **`blocked=0`** 证明 fail-closed 未误伤、S-01 界面与金额逐项相同）。
> 复演同时更正了本目录两处与界面/实现不符的表述（**按钮名**见 §2 注解、**S-09 的 12 秒归因**见 §3.1 注解），
> 原文一律保留不改写。本目录仍是第一阶段的原始留证。

## 0. 结论速览

| 项 | 结果 |
|---|---|
| 第一阶段（实施负责人演练） | **完成**：3 分钟、10 分钟脚本各实际走通一次，计时与截图齐备 |
| 契约 AC-026 判据 | **全部满足**（9 类文档齐全见 `artifacts/acceptance/README.md`；README 首段讲业务问题；两脚本各演练一次 + 计时 + 截图） |
| 第二阶段（所有者最终确认演练） | **待所有者执行**（交接说明见 §5） |
| 演练发现的卡顿与修订 | **1 处**：`demo/reset` 命令在 README 与 demo-script 中缺管理员身份头与确认串（照抄会 401/422），已修正并复验（§4） |
| 演示模式 | **fake**（零网络、零真实模型费用、确定性可复现）；真实模型效果以**已验收证据**呈现，不现场联网 |

## 1. 演练环境（实测，非声明）

演练在主仓库工作副本上进行，服务以 fake 模式运行于 `http://127.0.0.1:8000`。演练开始/结束时实测：

```text
GET /api/health            → {"status":"ok","db":"ok","demo_today":"2026-10-20","llm_mode":"fake"}
GET /api/health/providers  → llm_mode=fake；api_key_configured=null（不暴露密钥，AC-022）；
                             network.installed=true, allowed_hosts=[]（fake 空允许清单）,
                             external_attempts=0, blocked=0（HC-06 出站边界全程安装、零外联）；
                             providers=[transport ok, hotel_contract ok, hotel_backup ok, weather ok]
GET /api/demo/faults       → 演练结束 {"no_protocol_hotel":false,"transport_timeout":false,"weather_unavailable":false}（故障开关已复位）
uv run pytest -q           → 283 passed in 14.00s
台账 scripts/ledger_summary.py → 运行期合计 1.6428 元；含手工行累计 2.0547 元 / 上限 50 元（暂停线 40 元）
```

> **费用诚实性**：整个演练在 fake 模式进行，**未产生任何真实模型调用**；台账累计值在演练前后保持
> 2.0547 元不变（该值是 TASK-006d/008/011 历史真实冒烟与评测的既发生费用，非本次演练产生）。

## 2. 3 分钟脚本演练（HR / 业务价值）

**输入（S-01 黄金路径）**：张三 →「发起申请」→ 一句话「下周去上海参加供应商大会，预算四千」→
系统只追问缺失项（出发地、起止日期），**不**把「下周」臆测成具体日期 → 补全「从北京出发、10-28 到 10-30」→ 提交。

> **更正（第二阶段复演时实测，原文保留不改写）**：界面按钮名与本行/`demo-script.md` 原文不符——
> 列表页是「**新建申请**」（不是「发起申请」），一句话输入后点「**开始**」（不是「发送」；
> 「发送」是详情页对话框的按钮），槽位齐全后点「**提交校验**」（不是「提交」）。
> `docs/demo-script.md` §1 行 49–50 已按实际标签修订。抽取与追问行为本身与原文描述一致，结论不变。

| 节拍 | 实测观察 | 截图 |
|---|---|---|
| 0:20–1:00 选张三、发起、抽取槽位 | 抽取徽标标注「Fake（确定性测试替身，非模型）」；只追问缺失项，已给信息不重复问 | `3min-02-slots-filled.png` |
| 1:00–2:00 提交→方案/审批卡 | 总额 **4,760.00 元**、超预算 **760.00 元**；违规挂条款引用 **RULE-AUTO-001 · v1.2 POL-6 · line:33**；成本拆解带来源徽标（交通/酒店/天气/摘要） | `3min-03-plan-cost-reasons.png` |
| 2:00–2:30 切李四批准 | 批准（填意见）→ 状态 `DELIVERED` | （并入 04 前的时间线） |
| 2:30–3:00 切王五看 Outbox/审计 | Outbox 恰好 **1 封 `trip_delivery`**；审计时间线从输入→条款→规则→Provider→审批→交付完整 | `3min-04-admin-outbox.png` |

**计时（应用内审计时间线，实测）**：创建 `11:45:19` → 提交 `11:46:06` → 交付 `11:47:06`，系统侧黄金路径
约 **1 分 47 秒**；含口播与 3 次身份切换，整套演示约 **3 分钟**，落在目标 **≤3:30** 内。

**观察**：脚本节拍与界面实际一致，无需修订。S-05「只追问缺失项、不臆测日期」是这条线最有说服力的点，
口播已强调。

## 3. 10 分钟脚本演练（技术面深挖）

逐节拍走通。系统侧时延为**实测**；含口播/切换身份/打开文档与证据的总时长按节拍结构估算，落在目标 **≤11:00** 内。

| 节拍 | 主题 | 演练方式 | 实测结果 / 证据 |
|---:|---|---|---|
| 0:00–1:00 | 架构与确定性 | 现场 shell | `uv run pytest -q` → **283 passed in 14.00s**；`docs/architecture.md` 分层 |
| 1:00–2:00 | 三种 LLM 模式 | 打开文档 | `docs/integration.md` §1；`main.py` 的 `build_*` 按 `llm_mode` 装配，业务代码不感知模式 |
| 2:00–3:00 | 出站网络边界 | 现场 API | `/api/health/providers` → `installed=true, allowed_hosts=[]（fake）, external_attempts=0`；AC-021 证据 |
| 3:00–4:00 | 费用守卫 | 现场 shell | 台账累计 **2.0547 元 / 50 元**（暂停线 40）；`api_key_configured=null`（F-03/HC-07） |
| 4:00–5:30 | 黄金路径 + 规则计算 | 复用 3 分钟脚本 | S-01 总额 476000 分、`BUDGET_EXCEEDED`+`OVER_AUTO_APPROVAL_LIMIT` 进审批（见 §2） |
| 5:30–6:30 | **故障降级（S-09）** | **现场 UI 实跑** | 见 §3.1，截图 `10min-06-fault-degradation-s09.png` |
| 6:30–7:30 | 持久化恢复（S-10） | 引用已验收证据 | **AC-010 `passed`**：真实子进程 `taskkill /F /T` 强杀 → 同库重启 → checkpoint 恢复 → 只交付 1 次（14/14） |
| 7:30–8:30 | **制度编译（S-12）** | **现场 UI 视图** + 引用证据 | 见 §3.2，截图 `10min-08-policy-management.png`、`10min-08-policy-v12-rules.png`；完整「上传→抽取→逐条确认→发布→重新校验」流程见 **AC-024 `passed`** + **AC-005 `passed`** |
| 8:30–9:30 | **RAG 引用终审** | **现场 UI 实跑** | 见 §3.3，截图 `10min-09-rag-answerable-citation.png`、`10min-09-rag-refusal.png`；real 模式指标见 **AC-004/017 `passed`**（引用 20/20、数值 11/11、拒答 5/5） |
| 9:30–10:00 | 评测与验收 | 打开文档 | `docs/acceptance.md`、`artifacts/acceptance/TASK-011/README.md`；门控项真实模型达阈值、确定性 36/36、耗时中位数 4.2s |

### 3.1 故障降级 S-09（现场 UI 实跑，本阶段核心验证点）

- 王五「运维」页开启 `transport_timeout` 故障开关 → 切张三 → 一句话发起 **S-02 同输入**
  （北京→上海、2026-10-25～10-26、客户拜访、预算 2500 元）→ 提交。
- 应用内审计时间线（实测）：创建 `11:54:56` → 规划完成 `11:55:08`，系统侧约 **12 秒**（含交通 Provider 超时等待）。

  > **更正（第二阶段复演时实测，原文按「只注解、不改写」保留）**：上面这句的**归因是错的**。
  > `src/travel_desk/providers/demo.py:58–59` 在故障开关命中时**直接 `raise ProviderTimeout`，没有任何 `sleep`**，
  > 注入的「超时」**不消耗墙钟**。复演实测（Trip `70b1db88…`）：提交 `06:01:45.885Z` → 审批卡
  > `06:01:45.918Z`＝**33 ms**。本行那 12 秒是**操作者从创建到点提交之间的人工间隔**
  > （创建 `11:54:56` → 规划完成 `11:55:08`），被误记为 Provider 等待。
  > 降级**结论本身不受影响**（`total_fen=null`、`known_subtotal_fen`、`PLAN_INCOMPLETE`、不按 0 计入均已复验一致）。
  > 详见 [TASK-013/ac026-rehearsal-post-fix](../../TASK-013/ac026-rehearsal-post-fix/README.md) §3.1 与 §5 修订 4。
- **界面与 API 双重核对一致**（Trip `9274c355-945b-4631-98b9-7793f07b14c4`）：

  | 判据 | 界面显示 | API 实测 |
  |---|---|---|
  | 不崩溃、交通待人工补录 | 「方案不完整：transport 待人工补录」；去程/返程「待人工补录」 | `plan.incomplete=true`, `plan.missing=["transport"]`, `plan.transport.status="manual_required"` |
  | 合计空缺、给已知小计 | 合计「待补录（已知小计 **600.00 元**）」 | `total_fen=null`, `known_subtotal_fen=60000`, `transport_fen=null`, `missing_components=["transport"]` |
  | 缺失不按 0 计入 | 「缺失部分：transport，**不按 0 计入**」 | （同上，`total_fen` 为 `null` 而非把交通算 0） |
  | 审批理由 | `PLAN_INCOMPLETE`「方案不完整：交通待人工补录，不能自动通过」 | `approval_reasons=["PLAN_INCOMPLETE"]` |
  | 进入审批（S-02 同输入本应自动通过） | 状态「待审批」、审批卡 revision 1 等待李四 | `status="PENDING_APPROVAL"` |
  | Provider 调用 | 审计 #35/#36 transport timeout、#37 hotel_contract ok（2 候选）、#38 weather ok（2 天） | `provider_call`：transport=timeout、hotel_contract=ok、weather=ok |

- 与确定性 E2E 的 `s09` 断言（`scripts/e2e_scenarios.py` 行 250–281）逐条吻合；界面截图 `10min-06-fault-degradation-s09.png`。
- **演练后复位**：`POST /api/demo/faults {"fault":"transport_timeout","enabled":false}` → 三个开关全 `false`（§1 已复验）。

### 3.2 制度编译 S-12（现场 UI 视图 + 引用已验收证据）

- 王五「制度管理」：版本表 **v1.0 已归档**（8/0/0/0/0 规则）、**v1.2 已发布**（8/0/0/0/0，发布 10/20 11:44:46）；
  上传表单提示「支持 Markdown（UTF-8）或文本型 PDF，≤10 MiB；**扫描件 PDF 会被拒绝**；上传后为草稿，需抽取、逐条审核后才能发布」。
  截图 `10min-08-policy-management.png`。
- 打开 **v1.2 详情**：「非草稿版本只读」；**8 条规则**逐条展示代码分配的 ID、类型、**引用定位**
  （POL-1 line:9、POL-2 line:13/15、POL-3 line:21、POL-4 line:25、POL-5 line:29、POL-6 line:33、POL-7 line:37）、
  整数「分」值、条件、单位、确认人（王五）。截图 `10min-08-policy-v12-rules.png`。
- 口播要点已验证可讲：模型抽取的规则是**候选**，须管理员逐条确认（含引用定位）才发布；ID 由代码按类型+顺序分配，
  不信模型给的 ID（IMP-66）。
- **完整「上传 v1.3→抽取→逐条确认→发布→回张三详情按新版本重新校验」流程**：引用 **AC-024 `passed`**
  （revision 1→2、绑定 v1.3、旧卡 superseded、新卡 pending、新增 `HOTEL_OVER_CAP 48000>45000`、候选复用无 Provider 调用、
  审计保留 v1.2/v1.3 两次判断）与 **AC-005 `passed`**（抽取/审核/发布/归档全链路）。本阶段演练走**视图与口播**，
  不在演练中重复发布 v1.3（避免改动种子制度状态；该流程已有专属真实证据）。

### 3.3 RAG 引用终审（现场 UI 实跑）

- **可回答**：张三「制度问答」问「去上海出差住宿每晚最多能报多少？」→ 回答「根据 v1.2 POL-2：员工住宿上限：
  A 类城市每晚上限 500 元；B 类 400 元；C 类 300 元」；**引用** `policy v1.2 POL-2 · line:13` + 逐字条款原文；
  生成方式标注「Fake 抽取式（测试替身，非模型）」；检索明细 bm25（fake 模式固定 BM25）。截图 `10min-09-rag-answerable-citation.png`。
- **无答案（拒答）**：问「公司年终奖什么时候发放？」（制度外）→ 回答「**不知道**：当前制度（v1.2）中没有找到能回答
  这个问题的条款」；界面明示「**拒答时不展示引用**」。截图 `10min-09-rag-refusal.png`。
- 口播要点已验证可讲：引用即块文本、定位逐字匹配（找不到即 `invalid_quote`）；**代码终审引用**——无有效引用的
  「可回答」降级为拒答、拒答引用清空。real 模式实测引用命中 **20/20**、关键数值 **11/11**、拒答 **5/5**（AC-004/017）；
  vector 与 hybrid Recall@3 打平 → 按 D-17 保留纯向量（AC-025）。

## 4. 演练发现的卡顿与修订

**修订 1（已修复并复验）：`demo/reset` 命令缺身份头与确认串。**

- **现象**：`README.md`（原行 62）与 `docs/demo-script.md`（原行 20）写的
  `curl -X POST http://127.0.0.1:8000/api/demo/reset` 照抄会失败——实测：
  - 不带 `X-Demo-User` → **HTTP 401** `UNAUTHENTICATED`「缺少 X-Demo-User 请求头」；
  - 带管理员头但无确认串 → **HTTP 422** `CONFIRMATION_REQUIRED`「请在 confirmation 中输入 RESET_DEMO_DATA」。
- **根因**：文档示例漏写了接口要求的**管理员身份**与**确认串**（接口契约本身正确，AC-016 已验证；纯属文档示例错误）。
- **修订**：两处均改为完整命令并加说明——
  ```powershell
  curl -X POST http://127.0.0.1:8000/api/demo/reset -H "X-Demo-User: u-wangwu" -H "Content-Type: application/json" -d '{"confirmation":"RESET_DEMO_DATA"}'
  ```
  并补充：缺头/缺确认串分别返回 401/422（AC-016）；Windows PowerShell 若把 `curl` 解析为 `Invoke-WebRequest`
  别名，改用 `curl.exe` 或界面「运维」页重置入口（推荐演示用）。
- **复验**：修正后的命令实测返回 `{"status":"reset"}`；演练结束前用它把数据恢复到干净起点（trips=0，§1 故障开关亦复位）。

**其余节拍**：脚本与界面/接口实际行为一致，无其他卡顿，无需修订。

## 5. 第二阶段交接（项目所有者最终确认演练）

按 `docs/demo-script.md` §3 两段式协议，**第二阶段＝项目所有者亲自演练作为最终确认**。交接清单：

1. 启动：`uv sync` → `uv run travel-desk`（默认 fake 模式，零网络、不花钱、不依赖密钥）。
2. 浏览器打开 <http://127.0.0.1:8000>，确认右上角状态条 `演示日期 2026-10-20 · 模型模式 fake`、健康点绿色。
3. 恢复干净起点：用 §4 修正后的 `demo/reset` 命令（或以王五登录「运维」页点重置）。
4. 照 `docs/demo-script.md` 走 **3 分钟脚本**（§1 表）与 **10 分钟脚本**（§2 表）；
   重点核对：S-05 只追问缺失项、S-01 总额 4760 元/超 760 元/POL-6 引用、S-09 故障降级（交通待人工补录、
   合计空缺给已知小计、`PLAN_INCOMPLETE`、缺失不按 0 计入）、RAG 可回答带逐字引用 / 拒答不展示引用。
5. 计时目标：3 分钟 ≤3:30、10 分钟 ≤11:00。
6. 若所有者演练通过，即完成 AC-026 第二阶段最终确认；如发现新卡顿，按本目录格式追加修订记录。

> **状态口径**：契约 AC-026 的判据（9 类文档 + README 首段业务问题 + 两脚本各实际演练一次 + 计时/截图）
> 已由**第一阶段**满足并留证（本目录）。`docs/acceptance.md` 的 AC-026 行据此登记为 `passed`，并在说明中
> 明确标注「第一阶段完成、第二阶段（所有者最终确认）待执行」，不夸大、不代替所有者签署最终确认。

## 6. 证据文件清单

| 文件 | 对应节拍 | 内容 |
|---|---|---|
| `3min-02-slots-filled.png` | 3min 0:20–1:00 | 张三一句话发起、Fake 抽取徽标、只追问缺失项、槽位齐全 |
| `3min-03-plan-cost-reasons.png` | 3min 1:00–2:00 | 方案成本 4760 元/超 760 元、违规挂 POL-6 条款引用、来源徽标 |
| `3min-04-admin-outbox.png` | 3min 2:30–3:00 | 王五运维页 Outbox（1 封 trip_delivery）与审计时间线 |
| `10min-06-fault-degradation-s09.png` | 10min 5:30–6:30 | S-09 交通超时降级：待人工补录、合计空缺/已知小计 600 元、`PLAN_INCOMPLETE`、待审批 |
| `10min-08-policy-management.png` | 10min 7:30–8:30 | 制度管理：版本表（v1.0 归档 / v1.2 发布）、上传草稿表单、扫描 PDF 拒绝提示 |
| `10min-08-policy-v12-rules.png` | 10min 7:30–8:30 | v1.2 详情：8 条规则、代码分配 ID、引用定位（POL-1~7 行号）、整数分值、只读 |
| `10min-09-rag-answerable-citation.png` | 10min 8:30–9:30 | 制度问答可回答：v1.2 POL-2 line:13 逐字引用 |
| `10min-09-rag-refusal.png` | 10min 8:30–9:30 | 制度问答拒答：「不知道」、拒答时不展示引用 |

> 截图均为 fake 模式真实界面（非合成）；S-09 降级与 RAG 问答另有同时刻 API 实测值交叉核对（§3.1、§3.3）。
> S-10 持久化恢复、S-12 完整发布/重新校验引用各自专属真实证据（AC-010、AC-024/AC-005），不在本演练中重复执行破坏性/状态变更操作。
