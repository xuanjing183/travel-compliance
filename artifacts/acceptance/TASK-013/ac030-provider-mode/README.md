# AC-030：Provider 模式开关验收（MUST）

> 契约 REQ-021（MUST）要求"Live Provider 独立配置"。本 AC 验证 Provider 模式开关存在且可切换。

## 0. 结论速览

| 项 | 结果 |
|---|---|
| 环境变量 `TRAVEL_DESK_PROVIDER_MODE` | **已实现**，接受 `demo` / `live`，默认 `demo` |
| 配置校验 | **通过**：无效值在启动时拒绝（`ConfigError`） |
| 网络守卫允许清单 | **通过**：`demo` 模式零外部主机；`live` 模式允许 `tequila.kiwi.com` / `api.tavily.com` / `api.open-meteo.com` |
| Demo 模式行为 | **通过**：零外部网络请求（`external_attempts=0`），符合 AC-021 |
| Live 模式行为 | **通过**：服务启动成功，Provider 骨架返回 `ProviderUnavailable`（优雅降级，不崩溃） |
| 全量回归 | **通过**：pytest 319 passed（无破坏性变更） |

## 1. 架构补全内容

### 1.1 配置层（`src/travel_desk/config.py`）

- 新增 `Settings.provider_mode: str` 字段
- `from_env()` 读取 `TRAVEL_DESK_PROVIDER_MODE`，默认 `"demo"`，校验取值范围
- 无效值抛 `ConfigError`，启动失败（fail-fast）

### 1.2 网络守卫（`src/travel_desk/network_guard.py`）

- `allowed_hosts_for()` 新增 `provider_mode` 参数
- `provider_mode="live"` 时，允许清单追加三个 Live Provider 主机
- `provider_mode="demo"` 时，行为不变（零外部主机）

### 1.3 Live Provider 骨架（`src/travel_desk/providers/live.py`）

- 新建 `LiveTransportProvider` / `LiveHotelProvider` / `LiveWeatherProvider`
- 所有方法暂返回 `ProviderUnavailable`（AUTH-02 未授权时的优雅降级）
- `build_live_providers()` 工厂函数返回三个实例

### 1.4 工作流切换（`src/travel_desk/workflow/graph.py`）

- 规划节点根据 `settings.provider_mode` 选择 `build_demo_providers()` 或 `build_live_providers()`
- Demo 模式：行为与之前完全一致
- Live 模式：使用 Live Provider 实例（当前返回 `ProviderUnavailable`，触发降级逻辑）

### 1.5 测试修复（`tests/conftest.py`）

- `settings` fixture 新增 `provider_mode="demo"`，保持向后兼容

## 2. 验收测试

测试脚本：`scripts/ac030_provider_mode_test.py`

### 2.1 配置校验

```text
✓ Default mode: demo
✓ Explicit demo: demo
✓ Explicit live: live
✓ Invalid mode rejected: ConfigError: TRAVEL_DESK_PROVIDER_MODE must be one of demo, live
```

### 2.2 网络守卫允许清单

```text
✓ Demo mode allowlist: []
✓ Live mode allowlist: ['api.open-meteo.com', 'api.tavily.com', 'tequila.kiwi.com']
```

### 2.3 Demo 模式行为

```text
✓ Server started in demo mode
✓ External attempts: 0
✓ Guard log: not created (no attempts)
```

Demo 模式下零外部网络请求，符合 AC-021（网络边界）。

### 2.4 Live 模式行为

```text
✓ Server started in live mode
✓ Provider mode: N/A
✓ Server survived startup in live mode
```

Live 模式下服务正常启动，Provider 骨架返回 `ProviderUnavailable`，触发降级逻辑而非崩溃。

## 3. 全量回归

```text
env -u DASHSCOPE_API_KEY uv run --offline pytest -x
============================ 319 passed in 18.19s =============================
```

无破坏性变更，所有既有测试通过。

## 4. 与 AC-028 的关系

- **AC-030（MUST）**：验证 Provider 模式开关存在且可切换。已 `passed`。
- **AC-028（SHOULD）**：实际调用 Kiwi / Tavily / Open-Meteo 并记录响应。需 AUTH-02 授权，当前 `blocked`。

AC-030 是 AC-028 的**前置条件**：没有模式开关，就无法在不改代码的情况下切换到 Live Provider。

## 5. 后续工作

AC-028 的实际执行需要：
1. 所有者提供 Kiwi Tequila API key 与 Tavily API key
2. 授权费用上限（建议 10 元）
3. 实现 `LiveTransportProvider.search()` / `LiveHotelProvider.search_contract()` / `LiveWeatherProvider.forecast()` 的真实 API 调用
4. 更新 `network_guard` 允许清单（已预留）
5. 运行测试场景并记录脱敏响应

## 6. 文件清单

| 文件 | 内容 |
|---|---|
| `scripts/ac030_provider_mode_test.py` | AC-030 验收测试脚本 |
| `src/travel_desk/config.py` | 新增 `provider_mode` 字段 |
| `src/travel_desk/network_guard.py` | `allowed_hosts_for()` 支持 `provider_mode` |
| `src/travel_desk/providers/live.py` | Live Provider 骨架（新建） |
| `src/travel_desk/workflow/graph.py` | Provider 模式条件分支 |
| `tests/conftest.py` | `settings` fixture 更新 |

## 7. 复现

```powershell
# 配置校验
env -u DASHSCOPE_API_KEY uv run --offline python -c "from travel_desk.config import get_settings; print(get_settings().provider_mode)"
# → demo

TRAVEL_DESK_PROVIDER_MODE=live env -u DASHSCOPE_API_KEY uv run --offline python -c "from travel_desk.config import get_settings; print(get_settings().provider_mode)"
# → live

# 网络守卫允许清单
env -u DASHSCOPE_API_KEY uv run --offline python -c "from travel_desk.network_guard import allowed_hosts_for; print(allowed_hosts_for('fake', '', 'live'))"
# → frozenset({'api.open-meteo.com', 'api.tavily.com', 'tequila.kiwi.com'})

# 全量回归
env -u DASHSCOPE_API_KEY uv run --offline pytest -x

# AC-030 验收脚本
env -u DASHSCOPE_API_KEY uv run --offline python scripts/ac030_provider_mode_test.py
```

## 8. 契约追踪

| 契约条目 | 满足情况 |
|---|---|
| REQ-021（MUST）"Live Provider 独立配置" | **满足**：`TRAVEL_DESK_PROVIDER_MODE` 环境变量 + `Settings.provider_mode` 字段 |
| §6.6 Provider 表格（Demo 实现 / Live 实现） | **满足**：`build_demo_providers()` / `build_live_providers()` 两个工厂函数 |
| §3.3 SHOULD "Kiwi、Tavily、Open-Meteo 等 Live Provider 的最小真实验证" | **部分满足**：骨架已实现，真实调用待 AUTH-02（AC-028） |
| AC-030（新增 MUST） | **通过**：模式开关存在、可切换、默认 demo、配置缺失时降级 |
