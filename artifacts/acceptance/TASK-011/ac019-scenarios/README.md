# AC-019 — 12 个确定性端到端场景（S-01～S-12 各 3 次）

## 结论

**passed** — `scenarios 36/36 passed; checks 270/270`，无任一硬约束违反，满足契约 §14.3「S-01～S-12 确定性模式达到 36/36」与 AC-019「各运行 3 次，共 36/36 达到精确预期」。

## 命令

```
env -u DASHSCOPE_API_KEY uv run --offline python scripts/e2e_scenarios.py \
    artifacts/acceptance/TASK-011/ac019-scenarios --runs 3 --mode fake
```

- `--mode fake`：确定性验收模式，使用 Fake LLM 与固定 fixture，不受模型随机性影响（契约 §7.2 末、§14.1）。
- `--runs 3`：每个场景连跑 3 次，场景之间以 `POST /api/demo/reset` 隔离，验证可重复性。
- `env -u DASHSCOPE_API_KEY`：显式移除真实模型凭证，确保本次运行不产生任何外部调用（HC-06）。
- 运行方式：脚本启动真实 `travel_desk` 子进程，经 HTTP/SSE 驱动，非进程内 mock。

## 证据文件

| 文件 | 内容 |
|---|---|
| `e2e-report.json` | 完整报告：36 条 `results`（每条含 `scenario`/`run`/`passed`/`duration_s`/`trajectory`/`checks`），270 条扁平 `checks`（含 `observed` 实测值），顶层 `passed=true` |
| `boot-1.log`～`boot-4.log` | 子进程启动日志（S-10 在第 3 次迭代中终止并重启服务，故多于 3 份） |

## 报告关键字段（实测）

- `mode = fake`，`runs_requested = 3`，`scenarios = [S-01 … S-12]`（12 个）。
- `results` 行数 = 36；逐场景通过次数：S-01～S-12 各 3/3；`any failed run = []`。
- 顶层 `passed = true`；`checks` 共 270 条，全部 `passed=true`。
- `egress.attempts = []`：整个 36 次运行期间出站守卫未记录任何外联尝试（与 AC-021 网络边界一致）。
- `provider_health`：`transport/hotel_contract/hotel_backup/weather` 全部 `ok`，`llm_mode = fake`。

## 精确预期覆盖（每场景状态轨迹节选）

报告为每次运行保留了状态轨迹与实测值，覆盖契约 §7.2 的精确预期，例如：

- **S-01 黄金路径**：选中 `T-BJS-SHA-1028-A`/`T-SHA-BJS-1030-A`/`H-SHA-CONTRACT-01`（`H-SHA-CONTRACT-02` 满房被过滤）；晚数 2、天数 3；交通 350000、酒店 96000、补贴 30000、总额 476000；无违规；审批理由含 `BUDGET_EXCEEDED`、`OVER_AUTO_APPROVAL_LIMIT`；超预算 76000；`PENDING_APPROVAL`；李四批准后 `DELIVERED`，1 封 `trip_delivery`。
- **S-02 自动通过**：B 组交通 + `H-SHA-CONTRACT-02`，总额 190000，`AUTO_APPROVED→DELIVERED`，无 Approval。
- **S-03 酒店超标**：`HOTEL_OVER_CAP`（45000>40000），总额 274000，来源 `backup_fixture`。
- **S-09 交通故障**：`transport_timeout` 下交通待人工补录、`total_fen=null`、`known_subtotal_fen=60000`、`PLAN_INCOMPLETE`、进入 `PENDING_APPROVAL`（对照 S-02 同输入本应自动通过），未伪造交通 ID。
- **S-10 重启恢复**：终止子进程后用同一数据库重启，Trip/revision/审批待办未丢失，`via=graph` 从审批后节点恢复，只交付 1 次。
- **S-12 制度变更**：发布 v1.3 后重新校验，revision+1、绑定 v1.3、旧卡 superseded、新增 `HOTEL_OVER_CAP 48000>45000`、候选复用无 Provider 调用、审计保留 v1.2/v1.3 两次判断。

其余场景（S-04 提前不足、S-05 缺失信息追问、S-06 硬性阻断、S-07 驳回、S-08 要求修改、S-11 图片入口）的逐条检查与实测值见 `e2e-report.json`。

## 与其他验收的关系

- 本项为 **非授权门控 MUST**（REQ-028），使用 Fake/Recorded LLM 验证业务流程，符合 §14.1「确定性 E2E 允许并要求使用本地 fixture」。
- 网络边界（`egress.attempts=[]`）与 AC-021 互为佐证。
- 真实模型黄金路径与耗时属 SHOULD（AC-020），单独执行，不代替本项确定性验收。
