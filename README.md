# 差旅合规工作台（Travel Compliance Desk）

50～500 人的中小企业通常把差旅制度放在 Word、Markdown 或 PDF 里：员工靠自己翻制度，审批人靠经验
判断，财务往往到报销阶段才发现超标。本项目把制度编译成**可确认、可执行、可追溯**的规则，在
**规划阶段**就计算成本、识别例外，把真正需要判断的事项交给直属经理审批，并完整保留从输入、条款、
规则、供应商数据到审批和交付的证据链。这是一个**本地可复现的演示项目**，不连接真实订票、支付或
邮件系统。

> 想了解产品价值看 [docs/product.md](docs/product.md)；想 3 分钟/10 分钟演示看
> [docs/demo-script.md](docs/demo-script.md)；技术结构与集成看
> [docs/architecture.md](docs/architecture.md) 与 [docs/integration.md](docs/integration.md)。

## 当前状态

**Demo 主交付完成。** 契约 v1.1 的 MUST 需求均已实现并有 `passed` 验收；确定性场景 S-01～S-12
达 36/36（AC-019）；授权门控评测（AC-004/017/018/025/029）已在 AUTH-01 下用**真实模型**运行并达
阈值；黄金路径耗时中位数 4.2 秒（AC-020，SHOULD）。逐条状态、命令、报告与已知限制汇总在
[docs/acceptance.md](docs/acceptance.md)，证据索引见
[artifacts/acceptance/README.md](artifacts/acceptance/README.md)。

明确**未接 / 阻塞**的部分（诚实声明，详见 acceptance.md「已知限制」）：真实 Live Provider（订票/
支付）未授权（AUTH-02，AC-028 `blocked`）；真实邮件与对象存储未接（邮件终点是本地 Outbox，图片走
base64）；`X-Demo-User` 仅演示身份切换，不是认证机制。

## 快速开始（AC-001）

要求：Windows、Python 3.11、[uv](https://docs.astral.sh/uv/) 0.12+。

```powershell
uv sync
uv run travel-desk
```

- 工作台：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/health>（返回 `status`、`db`、`demo_today`、`llm_mode`）
- 接口文档（Swagger）：<http://127.0.0.1:8000/api/docs>

默认 `TRAVEL_DESK_LLM_MODE=fake`：零外部网络、确定性、不花钱、不需要密钥，适合演示与测试。
真实模型调用需 AUTH-01 授权，见 [docs/integration.md](docs/integration.md)。

> 上述安装、启动与演示步骤已由实施负责人在全新克隆的临时目录中实际执行验证（AC-001，证据见
> `artifacts/acceptance/TASK-012/`）。

## 运行测试

```powershell
uv run pytest                 # 全部单元 / 集成 / 端到端测试（fake 模式，零网络）
uv run pytest -q              # 静默
```

确定性 12 场景运行器（经真实子进程 HTTP/SSE 驱动 S-01～S-12）：

```powershell
uv run --offline python scripts/e2e_scenarios.py artifacts/acceptance/TASK-011/ac019-scenarios --runs 3 --mode fake
```

## Demo reset（恢复干净演示起点）

清空 Trip / 审计 / Outbox / 偏好等运行期数据，保留种子账号与已发布制度。每次演示前建议执行。
接口要求**管理员身份**与**确认串**（AC-016）：缺 `X-Demo-User` 返回 401，缺确认串返回 422。

```powershell
curl -X POST http://127.0.0.1:8000/api/demo/reset -H "X-Demo-User: u-wangwu" -H "Content-Type: application/json" -d '{"confirmation":"RESET_DEMO_DATA"}'
```

> 若 Windows PowerShell 把 `curl` 解析为 `Invoke-WebRequest` 别名，请改用 `curl.exe`，或直接用下面的界面入口。

界面路径：以**王五（管理员）**登录 →「运维」页亦有重置入口（推荐演示用）。重置会写一条 `demo_reset`
审计说明清空是有意操作（IMP-37），这是审计 append-only 的唯一例外（HC-11）。

## 演示账号

通过请求头 `X-Demo-User` 切换身份（界面右上角「切换身份」）。该请求头**仅用于演示**，不是认证机制。

| id | 姓名 | 角色 | 部门 | 直属经理 |
|---|---|---|---|---|
| `u-zhangsan` | 张三 | 员工 | 销售部 | 李四 |
| `u-lisi` | 李四 | 审批人 | 销售部 | — |
| `u-zhaoliu` | 赵六 | 审批人 | 采购部 | — |
| `u-wangwu` | 王五 | 管理员 | 行政财务部 | — |

## 配置与安全边界

- 默认 `TRAVEL_DESK_LLM_MODE=fake`，确定性测试不访问网络（HC-06）。
- 真实模型调用需项目所有者批准的 AUTH-01；密钥只从进程环境 `DASHSCOPE_API_KEY` 读取，**不写入
  仓库、日志或接口响应**。应用**不会自动加载** `.env`；变量名清单见
  [.env.example](.env.example)，请在启动前于终端或系统环境中设置。
- 费用守卫：每次真实调用前查累计估算，达 40 元暂停线即拒绝调用（上限 50 元），台账不可读也拒绝
  （fail closed）。台账见 [artifacts/cost-ledger.md](artifacts/cost-ledger.md)。
- 出站网络守卫全程安装：允许清单由配置推导（fake 空集、real 仅 `dashscope.aliyuncs.com`），
  无运维开关，非清单主机在 DNS 离开机器前拒绝并记录（AC-021）。
- 素材目录只读；本项目不修改学习 Demo（HC-08）。

## 文档（REQ-030）

| 文档 | 内容 |
|---|---|
| [README.md](README.md) | 本文：安装、启动、测试、Demo reset、演示账号、安全边界 |
| [docs/product.md](docs/product.md) | 产品说明：业务问题、角色、12 个用户故事、范围与边界 |
| [docs/architecture.md](docs/architecture.md) | 技术结构、分层、实施决定登记（IMP-01～） |
| [docs/integration.md](docs/integration.md) | 模型与外部集成：LLM 模式、DashScope、费用/网络守卫、Provider、RAG |
| [docs/api.md](docs/api.md) | 接口契约（REST + SSE + 错误体 + 权限矩阵） |
| [docs/demo-script.md](docs/demo-script.md) | 3 分钟 / 10 分钟演示脚本与演练要求 |
| [docs/acceptance.md](docs/acceptance.md) | 验收状态、命令、报告、已知限制汇总 |
| [docs/baseline.md](docs/baseline.md) | 环境与素材基线、假设验证记录 |
| [artifacts/acceptance/README.md](artifacts/acceptance/README.md) | 验收证据索引（按 AC / TASK） |

契约本身见 [docs/contract-v1.1.md](docs/contract-v1.1.md)（业务与验收依据）。

## 目录

```text
src/travel_desk/   应用代码（api、db、domain、llm、planning、policy、providers、services、workflow、static）
tests/             单元、集成、端到端测试与固定 fixture（制度、Provider、录制输出、邀请函）
docs/              契约、基线、产品、架构、集成、接口、演示、验收文档
evals/             冻结评测数据集（题集、gold 规则、注入样本）与指标口径
scripts/           评测与验收脚本（eval_*、e2e_scenarios、ledger_summary 等）
artifacts/         尖峰证据、验收证据（按 AC/TASK 索引）、费用台账与运行期记账
```
