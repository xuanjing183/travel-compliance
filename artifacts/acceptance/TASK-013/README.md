# TASK-013 证据：AC-027 真实模型路径（SHOULD）

**结论：S-01 黄金路径已用真实模型「整链」执行并通过——从员工自然语言原话输入、真实槽位抽取、
真实行程摘要，到生成审批卡；状态 `PENDING_APPROVAL`、总额 `476000` 分、选中的交通与酒店候选
与确定性（fake）模式逐项一致。正式运行 + 备用运行各 1 次，各 16 项断言全通过；IMP-97 修复落地后
再交叉复核 1 次，同样 16/16（见 §6），故有效运行共 3 次、结论一致。**

本目录不含密钥；三份报告均通过内置脱敏扫描（`desensitised.clean=true`，`hits=[]`）。

## 1. 为什么需要这次运行（与 AC-020 的区别）

AC-020（[TASK-011/ac020-timing](../TASK-011/ac020-timing/README.md)）度量的是契约 D-13 的计时边界
「员工显式提交 → 审批卡事件」，为了把计时窗口对准该边界，它**先以 `PATCH /slots` 预填了全部槽位**，
因此窗口内只有 `itinerary_summary` 一类真实调用（其 `usage.by_purpose` 即为 `["itinerary_summary"]`）。
真实**槽位抽取**从未被整链执行过。

所有者据此判定 AC-027 不可由 AC-020 覆盖，要求补一次整链真实运行。本目录即该运行的证据。

| | AC-020 | 本运行（AC-027） |
|---|---|---|
| 入口 | `PATCH /slots` 预填 S-01 | 自然语言原话 `POST /api/trips {text}` |
| 真实槽位抽取 | ✗ 未覆盖 | ✓ 2 次 `slot_extraction` |
| 真实行程摘要 | ✓ | ✓ 1 次 `itinerary_summary` |
| 度量目标 | 耗时中位数 ≤ 90s | 状态 / 金额 / 候选与确定性一致 |

## 2. 真实链路（逐轮实录）

脚本 [scripts/eval_fullchain.py](../../../scripts/eval_fullchain.py)，两个阶段、两个真实服务子进程
（`python -m travel_desk`，与 `uv run travel-desk` 同一入口）：

- **Phase A** — `llm_mode=fake`、子进程环境中移除 `DASHSCOPE_API_KEY`：**确定性基线**，零成本、零外联。
  基线不是硬编码常量，而是当场实测，因此「与确定性模式一致」是真实比对而非自我印证。
- **Phase B** — `llm_mode=real`：被测链路。继承环境使子进程可达 AUTH-01 端点；脚本**只核对密钥
  变量名存在**，从不读取/打印/写入其值（F-03 / HC-07），报告里只留 `api_key_configured` 布尔标志。

| 轮次 | 输入（员工原话） | 真实抽取结果 | 仍缺失 |
|---|---|---|---|
| 1 | `下周去上海参加供应商大会，预算四千` | `destination=上海`、`purpose=供应商大会`、`declared_budget_fen=400000`；`start_date/end_date=null` | `origin`、`start_date`、`end_date` |
| 2 | `从北京出发，10月28日到10月30日` | 补出 `origin=北京`、`start_date=2026-10-28`、`end_date=2026-10-30` | 无 |

- 第 1 轮**没有**把「下周」猜成具体日期（`start_date/end_date` 保持 `null`），也没有把「四千」
  当成 4000 分——它正确换算为整数分 `400000`（HC-02）。
- 追问文本只覆盖真正缺失的三项：`已记录：目的城市 上海，出差事由 供应商大会，申报预算 4,000.00 元。
  还需要：出发城市、出发日期（具体到日，如 10月28日）、返回日期（具体到日）。`
- **未使用兜底话术**（`topup_used=false`）：两轮自然语言即填满，第 3 轮显式兜底话术虽在脚本中备好，
  但两次运行都没有触发。
- 最终槽位与 S-01 逐项相同，故规划输入与确定性基线完全一致。

## 3. 断言结果（16 项，正式、备用与修复后复核三次运行均全通过）

| # | 断言 | 观测 |
|---|---|---|
| 1 | 原话抽取出 `destination=上海` | `上海` |
| 2 | 抽取出 `purpose=供应商大会` | `供应商大会` |
| 3 | 抽取出 `declared_budget_fen=400000`（四千→整数分） | `400000` |
| 4 | 不把「下周」猜成具体日期 | `(None, None)` |
| 5 | 补齐后无缺失/非法槽位 | `missing=[] invalid=[]` |
| 6 | 最终槽位与 S-01 逐项一致 | 6/6 相同 |
| 7 | 状态 `PENDING_APPROVAL` | `PENDING_APPROVAL` |
| 8 | 总额 `476000` 分 | `476000` |
| 9 | **选中候选与确定性基线一致** | `T-BJS-SHA-1028-A` / `T-SHA-BJS-1030-A` / `H-SHA-CONTRACT-01` |
| 10 | 成本拆解与基线一致 | 晚2/天3、交通350000、酒店96000、补贴30000、超预算76000 |
| 11 | 违规与审批理由与基线一致 | `violations=[]`、`[BUDGET_EXCEEDED, OVER_AUTO_APPROVAL_LIMIT]` |
| 12 | 审批卡可获取且总额一致 | 卡在李四待办、`total_fen=476000`、`policy_version=v1.2` |
| 13 | 行程摘要 `status=ok` | `ok`（127 字） |
| 14 | 摘要来自真实模型（`generator` 非 fake/recorded） | `qwen3.8-max` |
| 15 | SSE 流含 `approval_required` 帧 | `trip_status→status→plan→violations→approval_required→trip_status→done` |
| 16 | 审计含恰好 1 条 `approval_requested` | 1 |

第 15/16 项分别校验**接口面**与**审计链**两侧：SSE 帧名是 `approval_required`，审计事件名是
`approval_requested`，二者不同名，都必须在场。

真实摘要原文（`generator=qwen3.8-max`）：

> 本次差旅前往上海参加供应商大会，行程为2026年10月28日至30日。去程乘XC1028于09:30从北京出发，
> 12:45抵沪；返程乘XC1030于19:00离沪，21:30抵京。入住星衡协议酒店·外滩店共2晚。期间天气依次为
> 小雨、阴、多云，请备好雨具。

同场景 fake 模式的模板输出为 `2026-10-28至2026-10-30赴上海，事由：供应商大会。去程乘 XC1028 于
09:30 出发、12:45 抵达。…`——**措辞明显不同而事实一致**，且金额只来自代码计算（IMP-31），
可据此排除「回放录制输出」。

## 4. 真实调用与费用（AUTH-01）

| 项 | 值 |
|---|---|
| 模型 | 文本 `qwen3.8-max`（槽位抽取 + 行程摘要）；本链路未触发视觉/嵌入模型 |
| 每次运行调用数 | 3（`slot_extraction` ×2、`itinerary_summary` ×1） |
| 每次运行 tokens / 估算费用 | 正式与备用各 2015 tokens / 0.07254 元；修复后复核 2018 tokens / 0.072648 元 |
| 提交→审批卡耗时 | 正式 2.312s、备用 2.703s、修复后复核 2.367s |
| 本 AC 累计（含 3 次脚本调试运行） | 6 次运行、18 次调用、**0.4353 元** |
| 台账累计 | **2.4900 元 / 上限 50 元**（暂停线 40 元） |

记账见 [artifacts/llm-usage.jsonl](../../llm-usage.jsonl) 与 [artifacts/cost-ledger.md](../../cost-ledger.md)；
台账中 `2026-10-20 slot_extraction qwen3.8-max 12 …` 一行含本 AC 的 12 次抽取调用（6 次运行 ×2）。
密钥只读环境名，从不打印（F-03 / HC-07）。

**过程诚实说明**：本 AC 共发生 6 次真实运行。前 3 次的链路结果与正式运行完全相同（状态、金额、
候选、摘要均一致），但因**脚本侧**两个缺陷被记为 `valid=false`，与产品无关：

1. 断言把 SSE 帧名 `approval_required` 误写成审计事件名 `approval_requested`；
2. 先调用 `Check.take()`（它会清空收集器）再读 `checks.passed`，导致判定恒为 `False`。

两处均已修复；第 4、5 次（正式 + 备用）为记录在案的运行，第 6 次是 IMP-97 修复落地后的交叉复核
（§6.2）。前 3 次的调用同样已记账，未从台账剔除。

## 5. 顺带实测到的 IMP-97（bytes 主机名误判）

`real/network-guard.jsonl` 只有 1 行，且内容为：

```json
{"ts":"2026-10-20T05:03:47Z","via":"getaddrinfo","host":"b'dashscope.aliyuncs.com'","port":443,
 "decision":"local","caller":"thread.py:58"}
```

- **httpcore 确实以 bytes 形式把主机名传给 `getaddrinfo`**（此前只是代码审阅推断，现有实测证据）。
- 现行 `network_guard._local()` 对非 `str` 主机一律返回 `True`，于是该外部主机被记为 `local`，
  导致 `external_attempts=0`、`allowed=0`——**允许清单判定在这条路径上被绕过**。
- 本次被绕过的主机恰好就是允许清单内的 `dashscope.aliyuncs.com`，故**无实际安全后果**；
  但这证明 fail-open 通道经由应用真实使用的 HTTP 客户端**可达**，不再是纯潜伏风险：
  换成非清单外网主机、同样以 bytes 传入，也会被当作 `local` 放行。
- 只有 1 行是因为 httpx 连接池复用了同一条 TCP 连接，3 次模型调用共用 1 次域名解析。

因此本脚本的 `egress` 字段不直接采信守卫计数，而是**读日志、把 `b'…'` 解码后按允许清单重新判定**，
并单列 `imp97_bytes_hosts_misclassified_as_local` 与说明，避免 `external_attempts=0` 被误读为
「守卫已核验只访问了清单主机」。实质结论 `only_allowlisted_hosts=true` 成立（无 blocked、
实际外联主机名集合 ⊆ 允许清单）。

修复提案（IMP-97，fail-closed，四条拦截途径共用一处归一化）已提交所有者复核并**获批落地**
（commit `ca3a9f7`，见 §6）。落地后同一次调用**实测记为 `decision=allowed`、`external_attempts=1`、
`external_hosts={"dashscope.aliyuncs.com:443":{allowed:1,blocked:0}}`**——即当初写下的验收判据已满足。

对 AC-021 的影响：其**主结论**（fake 模式一次全 12 场景真实进程运行，`external_attempts=0`）
不受影响，因为 fake 模式根本不建任何外联 socket；受影响的是 real 模式下「允许清单放行」这一条
**从未被真实执行过**（走的是 bytes→local 的误判路径）。AC-021 已在落地后重跑取证并加注说明（§6）。

## 6. IMP-97 修复落地后的取证（commit `ca3a9f7`）

所有者复核 diff 后批准 fail-closed 方向并附四项条件，全部按要求落地（技术细节见
[`docs/integration.md`](../../../docs/integration.md) §4）：单一归一化点 `_host_text()`（四条途径共用）、
`None` 为唯一例外且只留在 `_local()`（通配绑定，无远端主机；不新增 decision 取值，靠
`host_form: "NoneType"` 区分）、日志新增 `host_form` 字段、不可归一主机以 `repr(host)[:120]` 记录且
`_host_repr()` 包 try/except 退回 `<unrepresentable {type}>`。`int`/`tuple` 等形态改判 `blocked`，不退让。

### 6.1 回归与 AC-021 重跑（`ac021-post-fix/`）

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `tests/test_network_guard.py` | 14 项 | **50 项** |
| 全量 `uv run pytest -q` | 283 passed | **319 passed** |
| AC-021 fake 模式 S-01～S-12 | 12/12 场景、90/90 断言 | **12/12 场景、90/90 断言**（结论不变） |
| `external_attempts` / `egress.attempts` | `0` / `[]` | **`0` / `[]`** |

新增单测的关键点（按所有者条件 ④ 的要求）：四途径的 bytes 阻断与 bytes 放行**均经被补丁的真实 hook**
（`socket.getaddrinfo` / `socket.create_connection` / `socket.socket.connect` / `urllib.request.urlopen`），
**不是**直接调 `record()`——因为「把什么当 host 交给 `record()`」正是各 hook 自己决定的，只有真实 hook
用例才能排除「某个 hook 把地址元组整体当 host 传入、修复后所有 connect 被判 blocked」的风险。为此还补了
**正向**四途径用例（允许清单内主机以 bytes 到达时必须放行并抵达被打桩的原始函数），以及 `connect()` 中
`getpeername()` 失败后 `host = address` 的回退分支、`repr()` 抛异常仍可留证、`str` 判定回归三类用例。

### 6.2 real 模式整链重跑（`ac027-post-imp97-fix/`）

同时交叉复核 AC-027：**16/16 断言通过**、`PENDING_APPROVAL`、总额 `476000` 分、选中候选与确定性基线
一致、真实模型佐证 `passed`、脱敏扫描 `clean=true`；3 次调用 2018 tokens **0.072648 元**（AUTH-01 内）。

首次真正证明允许清单命中——守卫日志由 §5 的误判变为：

```json
{"ts": "2026-10-20T05:32:19Z", "via": "getaddrinfo", "host": "dashscope.aliyuncs.com",
 "host_form": "bytes", "port": 443, "decision": "allowed", "caller": "thread.py:58"}
```

`host` 由 `b'dashscope...'` 变为归一化后的可读名，`host_form` 保留「它以 bytes 到达」这一事实，
`decision` 由 `local` 变为 **`allowed`**（经允许清单判定，而非绕过）。报告
`egress.imp97_bytes_hosts_misclassified_as_local=[]`、`only_allowlisted_hosts=true`。

AC-020 依所有者决定**不重跑**，只在 `docs/acceptance.md` 对应行加注解；已提交的三份修复前守卫日志
（AC-020 一份、本任务 §5 两份）**一律不改写**。

复现：

```powershell
env -u DASHSCOPE_API_KEY uv run --offline python scripts/e2e_scenarios.py artifacts/acceptance/TASK-013/ac021-post-fix --runs 1 --mode fake
uv run --offline python scripts/eval_fullchain.py artifacts/acceptance/TASK-013/ac027-post-imp97-fix --runs 1
```

### 6.3 修复后重走演示脚本（`ac026-rehearsal-post-fix/`）

§6.1/§6.2 覆盖的是 pytest 与脚本化 E2E；守卫装在**运行中的服务进程**里，所以修复后还需要重走一遍
**浏览器路径**——第一阶段演练（`TASK-012/ac026-rehearsal/`）是在修复之前做的。详见
[ac026-rehearsal-post-fix/README.md](ac026-rehearsal-post-fix/README.md)，要点：

- 3 分钟脚本（S-01）与 10 分钟脚本（10 个节拍）各**实际走通一次**；S-01 界面与金额与第一阶段**逐项相同**
  （4,760.00 元 / 超 760.00 元 / `RULE-AUTO-001 · v1.2 POL-6 · line:33` / 批准后 Outbox 恰好 1 封）。
- **本次最关键的新增核对项是 `blocked=0`**：IMP-97 把「不可归一化 host」从放行改为拒绝，若过严就会在真实
  服务里误伤本地调用。整轮演练（S-01 全流程、S-09 降级、制度管理、RAG、Demo 重置）实测
  `attempts=0, external_attempts=0, blocked=0`，`var/network-guard.jsonl` 未生成——修复只收紧了
  「外部 + 不可归一化」这一格。
- S-09 故障降级按所有者要求做**现场 UI + API 双核对**，八条判据逐项一致（含「缺失不按 0 计入」：
  `total_fen=null` 且 `budget_delta_fen=null`，而非把交通算 0）。
- 全量回归 **319 passed in 14.26s**；演练全程 fake 模式**零真实调用、零费用**，台账仍为 2.4900 元/50 元。
- 复演发现 4 处**文案/归因**问题（非产品缺陷）并已最小化修订：`demo-script.md` 按钮名与界面不符、
  台词「判定违规」与界面「违规 0 项」矛盾、费用数字过期（2.05→2.49）、第一阶段把 S-09 的 12 秒人工间隔
  误归因为「Provider 超时等待」（实测提交→审批卡 **33 ms**，`demo.py:58–59` 直接 raise、无 sleep）。
  第一阶段原文**只加注解、不改写**。
- 另实测到一项**环境注意**：8000 端口被一个无关 uvicorn 应用（PID 17184，`0.0.0.0:8000`）共用；
  IPv4 回环具体绑定优先，`127.0.0.1:8000` 与 `localhost:8000` 均返回本项目，`[::1]:8000` 无监听。
  未触碰该无关进程；已在复演记录 §7 建议第二阶段统一用 `http://127.0.0.1:8000`。
- 演练结束已 `demo/reset` 复位到干净起点并逐端点核对（复验文档声明的 401/422 两个负向用例）。
  **AC-026 第二阶段（所有者亲自演练）仍待执行。**

## 7. 文件

| 文件 | 内容 |
|---|---|
| `ac027-fullchain/fullchain-report.json` | **正式运行**：基线、逐轮抽取、16 项断言、模型名、`api_key_configured`、usage、egress（含 IMP-97 观测）、脱敏扫描 |
| `ac027-fullchain/scenario.json` | 场景 JSON：S-01 槽位、三句自然语言输入、期望状态与总额、`DEMO_TODAY` |
| `ac027-fullchain/chain-log.jsonl` | **脱敏链路日志**：每轮原话、抽取结果、缺失字段、时间戳（不含密钥、不含请求原文） |
| `ac027-fullchain/baseline/boot-1.log` | Phase A（fake）服务启动日志 |
| `ac027-fullchain/real/boot-1.log` | Phase B（real）服务启动日志 |
| `ac027-fullchain/real/network-guard.jsonl` | 出站记录；即 §5 的 IMP-97 实测证据 |
| `ac027-fullchain-spare/` | **备用运行**，同一套文件，16 项断言同样全通过 |
| `ac027-post-imp97-fix/` | **IMP-97 修复后交叉复核运行**（§6.2），同一套文件；`real/network-guard.jsonl` 为 `decision: "allowed"` 的首次真实取证 |
| `ac021-post-fix/` | **AC-021 修复后重跑**（§6.1）：`e2e-report.json`（12/12 场景、90/90 断言、`external_attempts=0`）+ 两份启动日志 |
| `ac026-rehearsal-post-fix/` | **修复后重走演示脚本**（§6.3，AC-026 第一阶段第二次）：`README.md`（逐节拍实测、分段计时、S-01/S-09 界面与 API 双核对、4 处文案修订、端口共用注意、复位核对）+ 10 张真实界面截图 + 10 份 API 原始响应（9 份 JSON + 1 份复位核对 TXT） |

复现（真实运行**不加** `env -u` 前缀，让子进程继承密钥名）：

```powershell
uv run --offline python scripts/eval_fullchain.py artifacts/acceptance/TASK-013/ac027-fullchain --runs 1
uv run --offline python scripts/ledger_summary.py --write
```

## 8. AC-027 判据对照

| 契约判据 | 满足情况 |
|---|---|
| 前置：AUTH-01 已授权且模型可用 | 已授权；`api_key_configured=true`，3 次调用均成功返回 |
| S-01 真实模型执行 1 次 | 执行 1 次（另备 1 次，IMP-97 修复后再交叉复核 1 次），且为**整链**真实（超出契约字面要求） |
| 状态正确 | `PENDING_APPROVAL` |
| 金额正确 | `476000` 分；成本拆解与确定性基线逐项一致 |
| 失败不得影响确定性结果 | 未失败；且确定性结果由 Phase A 独立实测，不依赖真实模型 |
| 证据：模型名 | `qwen3.8-max`（报告 `real.text_model` 与 `usage.models`） |
| 证据：脱敏日志 | `chain-log.jsonl` + 两份 `boot-1.log` + `network-guard.jsonl`；脱敏扫描 `clean=true` |
| 证据：场景证据 | `scenario.json` + `fullchain-report.json` 逐轮实录 |

**AC-027 → `passed`（无保留）。**
