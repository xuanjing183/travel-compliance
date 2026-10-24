# 集成说明（integration.md）

> 面向技术面 10 分钟深挖。本文回答「模型与外部能力是怎么接进来的、怎么保证不越界、怎么计费、
> 怎么在 fake / recorded / real 之间切换」。业务与产品见 [product.md](product.md)，模块结构见
> [architecture.md](architecture.md)，接口见 [api.md](api.md)。

## 0. 一句话

应用把所有「会联网 / 会花钱」的能力收敛到**三个可替换的边界**——LLM 模式、Provider 抽象、
出站网络守卫——再用**费用守卫**和**授权开关**兜底。默认配置（fake + Demo Provider）零网络、
零费用、确定性可测；真实集成只在显式授权下打开，且每一次调用都被记账与拦截审计。

## 1. 三种 LLM 模式（`TRAVEL_DESK_LLM_MODE`）

| 模式 | 行为 | 网络 | 用途 |
|---|---|---|---|
| `fake`（默认） | 确定性抽取式/模板实现，`generator="fake:*"` | **零外部网络**（HC-06） | 单元/集成/E2E 测试、AC-019 场景、演示 |
| `recorded` | 按输入 SHA-256 回放 `tests/fixtures/recorded/` 的录制输出；未录制则 503 `MODEL_UNAVAILABLE` | 零外部网络 | AC-003/005/024 等「用录制输出」的验收 |
| `real` | 经 OpenAI 兼容接口调用 DashScope（百炼） | 仅允许 `dashscope.aliyuncs.com` | AUTH-01 授权下的门控评测（AC-004/017/018/025/029/020） |

模式在 `create_app()` 里决定四个可替换组件的实现（`main.py`）：
`build_slot_extractor` / `build_invitation_extractor` / `build_rule_extractor` / `build_policy_qa`。
**fake 与 real 的接口签名完全一致**，业务代码不感知模式差异——这是「模拟与真实集成明确区分、
又可互换」的关键。D-08 规定：门控质量指标**只能**用 real 计算，fake 输出不得作为指标结论。

## 2. 真实模型集成（DashScope / 百炼）

- **客户端**：`src/travel_desk/llm/dashscope.py`，走 OpenAI 兼容端点
  `TRAVEL_DESK_DASHSCOPE_BASE_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`），
  用 `AsyncOpenAI`。
- **模型**（可经环境变量覆盖）：

  | 用途 | 环境变量 | 默认 |
  |---|---|---|
  | 文本（槽位抽取 / 规则抽取 / 制度问答 / 行程摘要） | `TRAVEL_DESK_TEXT_MODEL` | `qwen3.8-max` |
  | 视觉（邀请函图片抽取） | `TRAVEL_DESK_VISION_MODEL` | `qwen3.5-plus` |
  | 嵌入（制度检索） | `TRAVEL_DESK_EMBED_MODEL` | `qwen3.7-text-embedding`（1024 维） |

- **密钥（F-03 / HC-07）**：只从进程环境 `DASHSCOPE_API_KEY` 读取。应用**不自动加载** `.env`；
  代码、日志、接口响应、错误体、验收证据里都**不出现密钥值**，只会出现「变量名是否存在」的
  布尔标志（如 `api_key_configured`）。`.env.example` 只列变量名，值留空。
- **图片不走 OSS（RISK-002 消解）**：邀请函以**本地 base64 data URL** 直接发给视觉模型，
  不上传任何对象存储；脱敏请求摘要里 `raw_bytes_included=False`、`image_url="<base64 data URL omitted>"`。

### 2.1 结构化输出与降级

- 槽位/规则抽取用 `structured_call`（pydantic schema 约束）；摘要/问答用 `text_call`。
- 模型不可用 / 超时 / 越界 → 抛 `ModelUnavailable`（`retryable` 标志），上层降级为
  「摘要不可用」「规则候选不可用」并写 `error` 审计，**不伪造**模型输出（IMP-31/32）。
- 行程摘要有硬性校验：为空、超 400 字、或含金额 → 丢弃并标 `summary.status=unavailable`
  （金额只来自代码计算，模型不得写金额）。

## 3. 费用守卫（AUTH-01）

`UsageMeter`（`llm/dashscope.py`）在**每次真实调用前**检查累计估算、**调用后**记账：

- **记账**：每次调用追加一行到 `artifacts/llm-usage.jsonl`（用途、模型、token、估算费用、耗时；
  **不含输入原文与密钥**）。`scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。
- **累计口径**：`spent = 台账手工行合计（manual_total_cny 标记） + JSONL 运行期合计`。
- **暂停线**：`spent ≥ 40 元` → 抛 `BudgetPaused`（`retryable=False`），拒绝调用；上限 50 元。
- **失败关闭**：台账不可读时同样拒绝调用（fail closed），避免「算不出花了多少就继续花」。
- **估价口径**：所有 token 一律按 36 元/百万 token（项目所用模型最高单价）估算，只会偏高；
  实际以阿里云账单为准。
- 截至 TASK-013（含 IMP-97 修复后的 real 模式整链重跑），含手工行累计估算约 **2.49 元 / 50 元**，
  远低于暂停线。

## 4. 出站网络边界（HC-06 / AC-021）

`src/travel_desk/network_guard.py` 在应用 lifespan 内**全程安装**（`main.py`），拦截 Python 触网的
四条途径：`socket.getaddrinfo`、`socket.create_connection`、`socket.socket.connect`、
`urllib.request.urlopen`。

- **允许清单由配置推导，没有运维开关**：`allowed_hosts_for(mode, base_url)` —— fake/recorded
  返回**空集**（零外部网络），real 只返回 `dashscope.aliyuncs.com`。
- 回环/本地（`localhost`/`127.0.0.1`/`::1`/`*.local`/`fe80*` 等）始终放行并归类 `local`，
  不计入 `external_attempts`——所以 `uv run travel-desk` 与浏览器照常工作。
- 非本地、不在清单内的主机在 **DNS 离开机器前**拒绝（抛 `NetworkBoundaryError`，是 `OSError`
  子类，上层当作普通网络故障降级），并写 `<data_dir>/network-guard.jsonl` 作为证据。
- 这是**证据 + 纵深防御，不是操作系统级沙箱**：挡不住子进程或 C 扩展自己开 socket。项目不加载
  `.env`、启动即关闭 LangSmith/LangChain trace 上传，因此没有 tracing 旁路。
- **IMP-97 已修复（commit `ca3a9f7`，所有者复核后批准）**：httpcore 以 bytes 形式把主机名传给
  `getaddrinfo`，而原 `_local()` 对非 `str` 主机一律返回 `True`，于是该外部主机被误分类为 `local`
  放行、允许清单从未被查询。这**不是潜伏问题**：三份已提交证据日志（AC-020 一份、AC-027 两份）都
  记录了 `host: "b'dashscope.aliyuncs.com'"` + `decision: "local"`，即三次真实运行全部触发。修复的
  四项要点：
  - **单一归一化点 `_host_text()`**：`_local()`、`_classify()`、`record()` 全部经它，因此四条被拦截
    途径（`getaddrinfo`/`create_connection`/`socket.connect`/`urlopen`）判定一致，而非只补
    `getaddrinfo`。严格 ASCII 解码（DNS 名是 ASCII，国际化域名须先 punycode）；`bytes`/`bytearray`/
    `memoryview` 解码后与 `str` 走完全相同的 `_local()` + 允许清单判定。
  - **无法归一即 fail-closed**：不可解码的 bytes、`int`、`tuple`、任意对象一律按 external 处理，不在
    清单即抛 `NetworkBoundaryError` 并留证（原行为是判为 `local` 放行）。
  - **`None` 是唯一例外，且只留在 `_local()`**：`getaddrinfo(None, port, AI_PASSIVE)` 是**通配绑定，
    无远端主机**，拦掉会破坏服务自身监听。不新增 decision 取值，靠 `host_form: "NoneType"` +
    `decision: "local"` 区分。该分支行为与修复前一致（此前非 `str` 也判 `local`），只是显式化。
  - **日志 schema 新增 `host_form`**（记录主机名到达时的类型，正是 IMP-97 的关键信息），属附加字段：
    所有消费方要么整文件拷贝、要么只读 `.get("host")`，无处断言精确键集。无法归一的主机名以
    `repr(host)[:120]` 记录（转义且截断，裸 bytes 不落盘，HC-07），并由 `_host_repr()` 包 try/except，
    失败退回 `<unrepresentable {type}>` —— **写证据本身绝不能崩掉请求路径**。
  - **已提交的旧守卫日志一律不改写**（AC-020、AC-027 修复前的三份），改为在 `acceptance.md` 对应行加
    注解说明其产生于修复前。
- 修复后取证：单测 14 → **50**（四途径的 bytes 阻断与 bytes 放行均经**被补丁的真实 hook**，而非直接调
  `record()`；并覆盖 `connect()` 中 `getpeername()` 失败后 `host = address` 的回退分支、`repr()` 抛异常、
  `str` 判定回归）；全量 **319 passed**；fake 模式 S-01～S-12 重跑 `external_attempts=0`
  （`artifacts/acceptance/TASK-013/ac021-post-fix/`）；real 模式整链重跑首次真正证明允许清单命中：
  `host: "dashscope.aliyuncs.com"` + `host_form: "bytes"` + `decision: "allowed"`、
  `external_hosts={"dashscope.aliyuncs.com:443":{allowed:1,blocked:0}}`
  （`artifacts/acceptance/TASK-013/ac027-post-imp97-fix/`）。

## 5. Provider 抽象（交通 / 酒店 / 天气 / 时钟）

- `providers/base.py` 定义接口，`providers/demo.py` 是**本地 fixture 实现**（确定性、可故障注入）。
- 故障注入经 `POST /api/demo/faults`（如 `transport_timeout`），用于 S-09/AC-013：Provider 响应
  任一条目 Schema 不合格 → **整份响应作废**并降级为「待人工补录」，绝不部分采信被篡改的数据（IMP-24）。
- 规划器（`planning/`）对缺失给「已知下界」：只缺一程交通时交通整体视为缺失（`transport_fen=null`），
  但已定价那程计入 `known_subtotal_fen`；**绝不把缺失按 0 计入**而误判自动通过（IMP-14、S-09）。
- **真实 Live Provider 未授权**（AUTH-02）：AC-028 标记 `blocked`，不是 `passed`。

## 6. 制度检索（RAG）

- 制度按「条款下的连续行段落」切块（上限 600 字），引用即块文本，定位复用 `locate`（§4.8 规范化
  后逐字匹配，可跨行/跨页；不做模糊匹配，找不到即 `invalid_quote`，IMP-64/75）。
- 检索方式 `TRAVEL_DESK_RETRIEVAL`：`vector`（余弦，默认）/ `hybrid`（BM25 + RRF，k=60）。
  fake/recorded 固定 BM25（零网络）；real 用配置值。AC-025 实测 vector 与 hybrid 的 Recall@3
  打平（均 1.0），按 D-17 保留**纯向量**。
- 嵌入经 `AsyncOpenAI.embeddings.create`（1024 维、每批 10），按「模型 + 文本 SHA-256」在内存与
  `data_dir/cache/` 缓存，**只存向量不存原文**，避免同一版本重复计费（IMP-77）。
- 回答由代码终审引用：只认模型看到过的块 ID，无有效引用的「可回答」降级为拒答，拒答引用一律
  清空、文本固定（REQ-029，IMP-81）。

## 7. 工作流与持久化

- LangGraph 工作流（`workflow/graph.py` + `engine.py`），checkpoint 落 SQLite
  （`TRAVEL_DESK_CHECKPOINT_DB`）。审批处用 `interrupt` 挂起，submit 的 SSE 流在此返回。
- 每次提交新建线程 `thread_id=<trip_id>:rev-<n>:run-<8hex>`（同一 revision 可能多次提交，IMP-28）。
- 启动恢复（IMP-40）：`VALIDATING/PLANNING` 中止回 `COLLECTING`；`APPROVED/AUTO_APPROVED/
  DELIVERY_PENDING` 重新交付（受 `(trip_id, revision, kind)` 幂等约束）；`PENDING_APPROVAL` 保持，
  等决定恢复 checkpoint。S-10 验证跨进程重启不丢状态。

## 8. 配置与环境变量

不自动加载 `.env`；在终端/系统环境设置。完整清单见 `.env.example`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRAVEL_DESK_LLM_MODE` | `fake` | `fake`/`recorded`/`real`；非法值启动失败（`ConfigError`，IMP-13） |
| `DASHSCOPE_API_KEY` | —（仅 real 需要） | 只读环境，从不打印/落盘 |
| `TRAVEL_DESK_TEXT_MODEL` | `qwen3.8-max` | 文本模型 |
| `TRAVEL_DESK_VISION_MODEL` | `qwen3.5-plus` | 视觉模型 |
| `TRAVEL_DESK_EMBED_MODEL` | `qwen3.7-text-embedding` | 嵌入模型 |
| `TRAVEL_DESK_DASHSCOPE_BASE_URL` | `…/compatible-mode/v1` | OpenAI 兼容端点 |
| `TRAVEL_DESK_RETRIEVAL` | `vector` | `vector`/`hybrid` |
| `DEMO_TODAY` | `2026-10-20` | 固定时钟（确定性验收） |
| `TRAVEL_DESK_DATA_DIR` | `var/` | 数据库、上传、缓存、network-guard 日志所在 |
| `TRAVEL_DESK_USAGE_LOG` | `artifacts/llm-usage.jsonl` | 真实调用记账（持久） |

## 9. 怎么跑

```powershell
# 默认 fake：零网络、确定性（演示与测试）
uv sync
uv run travel-desk            # http://127.0.0.1:8000

# 真实模型（需 AUTH-01；先在环境里设置 DASHSCOPE_API_KEY，不要写进 .env 提交）
set TRAVEL_DESK_LLM_MODE=real
uv run travel-desk

# 门控评测脚本（真实运行省略 env -u 前缀，让子进程继承密钥名）
uv run --offline python scripts/eval_qa.py    artifacts/acceptance/TASK-011/ac004-qa --stage full --mode real
uv run --offline python scripts/eval_rules.py artifacts/acceptance/TASK-011/ac018-rules --mode real
uv run --offline python scripts/eval_vision.py artifacts/acceptance/TASK-011/ac029-vision
uv run --offline python scripts/eval_timing.py artifacts/acceptance/TASK-011/ac020-timing --runs 3
# 每次真实运行后汇总台账
uv run --offline python scripts/ledger_summary.py --write
```

## 10. 集成边界一览（什么接了、什么没接）

| 能力 | 状态 | 依据 |
|---|---|---|
| DashScope 文本/视觉/嵌入 | **已接**（real 模式，AUTH-01） | 门控评测 AC-004/017/018/025/029/020 |
| Demo Provider（交通/酒店/天气/时钟） | **已接**（本地 fixture，可故障注入） | S-01～S-12、AC-013 |
| 本地 Outbox（交付/通知邮件） | **已接**（终点，不发真实邮件） | `GET /api/outbox` |
| 真实 Live Provider（订票/支付） | **未接**（`blocked`） | AUTH-02 未授权，AC-028 |
| 真实邮件 / 对象存储（OSS） | **未接**（图片走 base64，邮件走 Outbox） | RISK-002、HC-10 |
| 真实身份认证 | **未接**（`X-Demo-User` 仅演示） | 演示边界 |
