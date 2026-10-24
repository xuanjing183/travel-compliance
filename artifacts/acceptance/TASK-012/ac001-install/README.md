# TASK-012 证据：AC-001 安装与启动（README 步骤实测）

**结论：passed。** 把仓库**全新克隆**到临时目录（不带现有虚拟环境），严格按 README 执行
`uv sync` 与唯一启动命令 `uv run travel-desk`：服务启动、健康接口成功、浏览器可打开工作台。
步骤与 README 一致。

## 契约要求（AC-001）

> 安装与启动；一台未安装项目依赖但有 Python 3.11 和 uv 的 Windows 环境。把仓库全新克隆到临时目录
> （不带现有虚拟环境），严格按 README 执行 `uv sync` 和唯一启动命令。服务启动、健康接口成功、
> 浏览器可打开工作台；步骤与 README 一致即通过。证据：命令、版本、启动日志、工作台截图。

## 执行步骤与观察

1. **全新克隆**（无 `.venv`、无 `var/`，二者均被 `.gitignore` 排除，克隆即「未安装依赖」状态）：

   ```bash
   git clone --quiet travel-compliance-desk C:/Users/22316/AppData/Local/Temp/ac001-clone
   # 确认：ls .venv → 不存在
   ```

2. **`uv sync`（README 命令）**：`exit 0`。`Resolved 67 packages in 0.62ms`、
   `Prepared 65 packages in 10ms`、安装 66 个包（含 `travel-compliance-desk==0.1.0`）。
   解析与准备耗时为毫秒级，证明全部依赖来自**本机 uv 缓存**（无任何网络下载行）。
   完整输出见 `uv-sync.log`。

3. **`uv run travel-desk`（README 唯一启动命令）**：约 3 秒内健康接口可用。
   启动日志见 `boot.log`：`startup recovery aborted=0 delivered=0 still_pending=0`、
   `Application startup complete`、`Uvicorn running on http://127.0.0.1:8000`。

4. **健康接口成功**：`GET /api/health` →
   `{"status":"ok","db":"ok","demo_today":"2026-10-20","llm_mode":"fake"}`（见 `health.json`）。
   全新克隆首次启动即自动初始化数据库并写入种子数据（`GET /api/users` 返回 4 名演示账号）。

5. **浏览器可打开工作台**：`GET /` → `200 text/html`（见 `index.html`）。浏览器打开
   <http://127.0.0.1:8000> 显示身份选择页（4 名演示账号、顶栏「演示日期 2026-10-20 · 模型模式
   fake」、健康点绿色），见 `workbench-login.png`；点击「张三」进入员工「我的申请」列表页，
   见 `workbench-employee-trips.png`。

## 环境（environment.txt）

| 项 | 值 |
|---|---|
| 平台 | Windows（AMD64） |
| uv | 0.12.2 |
| Python（venv） | 3.11.0 |
| 克隆目录 | `C:/Users/22316/AppData/Local/Temp/ac001-clone`（全新克隆、无 `.venv`） |

## 文件

| 文件 | 内容 |
|---|---|
| `uv-sync.log` | `uv sync` 完整输出（exit 0，66 包，全部来自本地缓存） |
| `boot.log` | `uv run travel-desk` 启动日志（恢复报告 + startup complete + uvicorn 监听） |
| `health.json` | `GET /api/health` 响应（status/db ok，demo_today，llm_mode=fake） |
| `index.html` | `GET /` 工作台首页 HTML（200 text/html） |
| `workbench-login.png` | 浏览器打开工作台：身份选择页 |
| `workbench-employee-trips.png` | 以张三登录后的员工「我的申请」页 |
| `environment.txt` | 平台 / uv / Python 版本与克隆目录 |

## 说明

- 依赖解析完全来自本机 uv 缓存（毫秒级 prepare），未发生网络下载；这与项目「不擅自联网」的纪律
  一致，同时证明 README 的 `uv sync` 在预热缓存环境下可离线完成。
- 验证结束后已终止该临时服务器进程；临时克隆目录不含密钥（`.env` 不随仓库分发）。
