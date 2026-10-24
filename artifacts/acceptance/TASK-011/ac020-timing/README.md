# AC-020 — 黄金路径耗时（SHOULD，真实模型模式）

## 结论

**passed** — 真实模型模式下运行 S-01 黄金路径 3 次，计时边界「员工显式提交 → 审批卡事件」的**中位数 4.204 秒 ≤ 90 秒**：

| 运行 | 耗时（秒） | 状态 | 审批卡 | 有效 |
|---|---:|---|---|---|
| 1 | 4.204 | PENDING_APPROVAL | 总额 476000、v1.2 | ✓ |
| 2 | 10.915 | PENDING_APPROVAL | 总额 476000、v1.2 | ✓ |
| 3 | 2.785 | PENDING_APPROVAL | 总额 476000、v1.2 | ✓ |

- **中位数 = 4.204 秒**，最大值 = 10.915 秒，最小值 = 2.785 秒；阈值 ≤ 90 秒 → 通过。
- 3 次运行全部有效（均到达 `PENDING_APPROVAL`、审批卡可获取、`approval_requested` 审计事件存在、卡内总额 476000 分与 S-01 一致）。

## 计时边界（D-13 / METRICS.md §5）

- **起点**：员工显式提交（`POST /api/trips/{id}/submit`），此前所有必填槽位已通过 `PATCH /slots` 填齐（槽位填充*不计入*计时窗口）。
- **终点**：审批卡事件（审计 `approval_requested`，审批卡经 `GET /api/approvals` 可获取）。submit 的 SSE 流在 LangGraph 运行于审批 `interrupt` 处挂起时返回，紧随 `request_approval` 发出 `approval_requested`。
- 计时窗口内含**真实模型调用**：plan 节点先执行 `make_summary`（真实 `qwen3.8-max` 行程摘要），再进入审批。因此测得的是真实模型时延，Fake/Recorded 计时不作为本项证据（D-08）。
- 每次运行 SSE 事件序列：`trip_status → status → plan → violations → approval_required → trip_status → done`。

## 运行环境（与 AC-019 同一台机器）

| 项 | 值 |
|---|---|
| 平台 | Windows-10-10.0.26100-SP0（AMD64） |
| 处理器 | Intel64 Family 6 Model 183 Stepping 1（24 逻辑核） |
| Python | 3.11.0 |
| 启动命令 | `python -m travel_desk`（= `uv run travel-desk`），真实模式子进程 |
| 文本模型 | `qwen3.8-max`（`api_key_configured=true`，仅存在标志，不含密钥值） |
| 视觉模型 | `qwen3.5-plus` |
| 时钟 | `DEMO_TODAY=2026-10-20`（固定） |

服务以**真实子进程**启动（与 AC-019 相同方法学：经真实 HTTP/SSE 驱动已发布进程，而非进程内测试客户端），子进程继承父环境（API Key 转发给子进程用于真实调用，但脚本从不读取/打印/提交其值，仅核对变量名存在，F-03/HC-07）。

## 命令

```
uv run --offline python scripts/eval_timing.py \
    artifacts/acceptance/TASK-011/ac020-timing --runs 3
```

输出：
```
run 1: 4.204s  status=PENDING_APPROVAL  card_ok=True  valid=True
run 2: 10.915s  status=PENDING_APPROVAL  card_ok=True  valid=True
run 3: 2.785s  status=PENDING_APPROVAL  card_ok=True  valid=True
runs=3 valid=3 median=4.204s max=10.915s min=2.785s (<= 90.0s) passed=True
  usage: 3 calls, 1280 tokens, 0.04608 CNY purposes=['itinerary_summary'] models=['qwen3.8-max']
```

## 费用与授权（AUTH-01）

3 次运行各 1 次 `itinerary_summary` 调用（model=`qwen3.8-max`），合计 3 次、1280 token、估算 **0.04608 元**，已由子进程追加到持久 `artifacts/llm-usage.jsonl` 并经 `scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。含手工行累计估算约 2.05 元 / 上限 50 元（暂停线 40 元）。

## 关于 `network-guard.jsonl` 的一条观察（不影响本项结论）

本子进程的出站守卫记录里，对 DashScope 的那一次解析被记为：

```json
{"via": "getaddrinfo", "host": "b'dashscope.aliyuncs.com'", "port": 443, "decision": "local", ...}
```

`decision="local"` 是 `network_guard._local()` 的一个分类缺陷：httpcore 以 **bytes** 形式（`b'...'`）把主机名传给 `socket.getaddrinfo`，而 `_local()` 对任何非 `str` 主机一律返回 `True`（见 `network_guard.py` 第 46–50 行），于是这个真实的外部主机被误判为“本地”并放行，没有走允许清单核对。

- **对 AC-020 无影响**：该调用本就应当被放行（real 模式允许清单含 `dashscope.aliyuncs.com`），调用成功、行程摘要正常生成，计时窗口与中位数有效。缺陷只体现在*日志分类*（记成 `local` 而非 `allowed`）与 `external_attempts` 计数偏低。
- **潜在安全边界问题**：同一缺陷意味着，若有任何代码以 bytes 主机名尝试访问*不在允许清单内*的外部地址，守卫会把它当“本地”放过而非拦截。当前验收配置下这是**潜伏**的——fake/recorded 模式业务代码从不外联（AC-021 负向证据 `external_attempts=0` 仍成立），real 模式只访问已授权的 DashScope。AC-021 的正向对照 `tests/test_network_guard.py` 用的是 `str` 主机名，未覆盖 bytes 路径。

因该修复会改变安全边界控件的运行时行为，按既定约束（改变安全边界的问题需提出差异/影响/选项并等待负责人决定），我已将其作为 **IMP-97 提案**上报所有者，暂不擅自修改 `network_guard.py`。建议的修复：在 `_local`/`_classify` 前用 `_host_str()` 把 bytes 主机解码为 `str` 再判定，并补一条回归测试（fake 模式 bytes 外部主机 → `blocked`；real 模式 bytes 允许主机 → `allowed`），修复后重跑 AC-021 证据。

## 证据文件

| 文件 | 内容 |
|---|---|
| `timing-report.json` | 机器信息、计时边界、模型名、3 次逐运行耗时与状态/SSE 事件/审批卡校验、中位数/最大/最小、`passed`、`usage`（3 次调用明细）、provider 健康、boot 日志尾部 |
| `boot-1.log` | 真实子进程启动日志 |
| `network-guard.jsonl` | 该子进程出站守卫记录（真实模式仅允许 `dashscope.aliyuncs.com`；其中一条 bytes 主机被误分类为 `local`，见上节，已作为 IMP-97 提案上报） |
| `../../../scripts/eval_timing.py` | 计时脚本（真实子进程 + S-01 黄金路径 + 中位数；口径见 `evals/METRICS.md` §5） |
