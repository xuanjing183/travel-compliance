# TASK-010 证据：AC-021 Demo 网络边界（HC-06 / REQ-021 / REQ-022）

**结论：`fake` 验收模式下，一次覆盖全部 12 个场景（90 项断言）的真实服务器进程运行，对外部网络的访问尝试为 0；出站拦截器已安装并对回环之外的任何主机在 DNS 离开机器前拒绝。`real` 模式只放行 AUTH-01 授权的 DashScope 主机名。**

本目录不含密钥；`e2e-report.json` 已核对不包含任何凭证值（HC-07）。

> **IMP-97 修复后注解（commit `ca3a9f7`）**：本目录取证于修复前，其结论**依然成立且已重跑复核**——
> 修复后以 fake 模式重跑 S-01～S-12 仍为 12/12 场景、90/90 断言，`installed=true`、`allowed_hosts=[]`、
> `external_attempts=0`、`egress.attempts=[]`，见
> [`TASK-013/ac021-post-fix/`](../../TASK-013/ac021-post-fix/)。正向对照由本文所述 **14 项扩至 50 项**
> （bytes 主机名在四条途径的阻断与放行均经被补丁的真实 hook、`connect()` 回退分支、`repr()` 抛异常、
> `str` 判定回归）。本目录内文件按所有者决定**保持原样不改写**。
> 另：本文「real 只放行 DashScope 主机名」在修复前**未被真实执行过**——httpcore 以 bytes 传主机名，
> `_local()` 判其为 `local` 而绕过清单。修复后首次真正取证 `decision: "allowed"`，见
> [`TASK-013/ac027-post-imp97-fix/`](../../TASK-013/ac027-post-imp97-fix/) 与
> [TASK-013 README](../../TASK-013/README.md)。

## 契约要求

- **HC-06**：Demo 模式除获授权的模型 API 外不得访问外部网络；Fake 验收模式不得访问任何外部网络。
- **REQ-021/022 / AC-021**：给出允许访问的域名清单与拦截记录作为证据。

## 两类证据（负向 + 正向对照）

单看“外部尝试 0 次”不足以证明边界有效——也可能只是根本没有任何代码尝试外联。因此本证据由两部分组成：

### 1. 负向：真实进程全场景运行，外部尝试为 0

`scripts/e2e_scenarios.py` 启动一个真实的 `python -m travel_desk`（等价 `uv run travel-desk`）子进程，以 `TRAVEL_DESK_LLM_MODE=fake` 运行，并从子进程环境中移除 `DASHSCOPE_API_KEY`；随后通过 HTTP/SSE 依次驱动 S-01～S-12（提交、审批、要求修改、驳回、故障注入、真实进程终止后重启恢复、图片抽取、制度 v1.3 发布与重新校验）。整个过程覆盖规划、审批、交付、制度问答与图片抽取的全部代码路径。

守卫安装在应用进程内、贯穿整个应用生命周期（`main.py` 的 lifespan 中 `install()`，关闭时 `uninstall()`），因此它统计的是**真实服务器进程**的出站，而不只是 TestClient。

`e2e-report.json` 关键字段：

```json
"egress": { "log": ".../var/network-guard.jsonl", "attempts": [] },
"provider_health": { "network": {
  "installed": true, "allowed_hosts": [], "attempts": 0,
  "external_attempts": 0, "blocked": 0, "allowed": 0, "local": 0,
  "external_hosts": {}, "blocked_by_caller": [] } }
```

- `installed: true` — 拦截器确实在运行（不是“没装所以没记录”）。
- `allowed_hosts: []` — fake 模式允许清单为空（`allowed_hosts_for("fake", ...)` 返回空集）。
- `external_attempts: 0` — 整轮运行没有任何一次对回环之外主机的尝试。
- 每个场景的 `egress_external` 均为空（报告 `results[*].egress_external`）。
- `attempts: []` — 连本地尝试都未被记录，因为业务代码在 fake 模式不主动建任何 socket；`network-guard.jsonl` 因此为空/未生成（无尝试即无行）。

`boot-1.log` / `boot-2.log` 是两次启动（S-10 真实终止进程后重启）的服务器日志，证明恢复流程也在守卫下运行。

复现：

```bash
env -u DASHSCOPE_API_KEY uv run --offline python scripts/e2e_scenarios.py \
  artifacts/acceptance/TASK-010/ac021-network-boundary --runs 1 --mode fake
# → scenarios 12/12 passed; checks 90/90
```

### 2. 正向对照：拦截器确实会拒绝并记录外联（`tests/test_network_guard.py`，14 项）

- `allowed_hosts_for`：`fake`/`recorded`/`off`/`""` 均返回空集；`real` 只返回 `dashscope.aliyuncs.com`；非法 URL 返回空集。
- 对 `api.example-internet.com` 分别经 `getaddrinfo` / `create_connection` / `socket.connect` / `urlopen` 发起外联：全部抛 `NetworkBoundaryError`，`summary()` 计 `blocked=1`、`external_attempts=1`、`allowed=0`，并写入 `network-guard.jsonl`（含 `"decision": "blocked"` 与触发它的调用点 `blocked_by_caller`）。
- 允许清单内的主机（模拟 real 模式的 DashScope）：`record()` 归类为 `allowed`，调用透传到原始解析器，`external_hosts` 记 `{allowed:1, blocked:0}`。
- 回环 `127.0.0.1` / `localhost`：归类 `local`，不计入 `external_attempts`（这解释为何 `uv run travel-desk` 与浏览器仍能工作）。
- `uninstall()` 还原原始 `socket.getaddrinfo`。

复现：

```bash
env -u DASHSCOPE_API_KEY uv run --offline pytest tests/test_network_guard.py -q
# → 14 passed
```

## 允许访问的域名清单（REQ-022）

| 模式 | 允许的外部域名 | 依据 |
|---|---|---|
| `fake`（验收模式） | 无（空集） | HC-06：Fake 不得访问任何外部网络 |
| `recorded` | 无（空集） | 同上 |
| `real` | 仅 `dashscope.aliyuncs.com`（取自 `TRAVEL_DESK_DASHSCOPE_BASE_URL` 的主机名） | AUTH-01 只授权该模型端点 |

回环与本地地址（`localhost`/`127.0.0.1`/`::1`/`0.0.0.0`/`[::]`/空、`*.local`/`*.internal`、`fe80*`）始终放行并归类 `local`，不计入外部尝试。清单由配置推导，**没有**运维开关可临时放开外部访问。

## 过程中发现并修复的真实缺陷（IMP-96）

正向对照测试暴露了 `network_guard._caller()` 调用不存在的 `threading.currentframe()`（应为 `sys._getframe()`）。该缺陷在 fake 模式下**潜伏**：因业务代码从不外联，`record()`→`_caller()` 从不被触发；但 `real` 模式下每次调用 DashScope 都会经过 `record()`，会直接崩溃。已修复，blocked 与 allowed 两条路径均由测试覆盖。这正说明“只有负向证据”会漏掉真实缺陷。

## 已知边界

守卫是进程内的证据与纵深防御，不是操作系统级沙箱：它无法阻止子进程或 C 扩展自行打开 socket。项目不加载任何 `.env`，启动即强制关闭 LangSmith/LangChain trace 上传（见 architecture.md §3），因此不存在通过 tracing 外泄的旁路。

## 文件

| 文件 | 内容 |
|---|---|
| `e2e-report.json` | 12 场景 90 断言全通过；`egress.attempts=[]`、`provider_health.network.external_attempts=0`；含每场景轨迹与 S-10 真实终止/重启记录 |
| `boot-1.log` / `boot-2.log` | 两次服务器启动日志（S-10 重启前后） |
| `network-guard.jsonl` | 出站尝试记录；fake 全场景运行为空（无尝试）——正向对照的记录见 pytest 临时目录 |
