# AC-013 证据：Provider 故障注入与降级（契约 §6.6 / §7.2）

分别注入**交通超时、无协议酒店、天气不可用**三种故障，用 S-02 输入（北京→上海
2026-10-25~26 客户拜访 预算 250000，且上海不设备用酒店）驱动，验证降级行为逐项符合 §6.6：
不崩溃、不伪造数据、缺失项不按 0 计入、需要人工补录的方案不得自动通过。

服务以文档命令 `python -m travel_desk`（= `uv run travel-desk`）在**真实子进程**中启动，
`TRAVEL_DESK_LLM_MODE=fake`、临时数据目录、`DASHSCOPE_API_KEY` 从子进程环境移除：无模型费用、
不访问任何外部网络（HC-06）。故障经管理员接口 `POST /api/demo/faults` 注入，每次注入前后都复位，
互不污染。

## 方法

`scripts/ac013_faults.py` 对每种故障：`POST /api/demo/reset` → 注入故障 → 建 Trip → 填 S-02 槽位 →
`POST /api/trips/{id}/submit`（SSE）→ 读取 `GET /api/trips/{id}` 与审计，导出 API 结果并断言降级行为。
规划层的同款行为另由 `tests/test_planning.py` 覆盖（`test_s09_*`、`test_ac013_*`、`test_weather_*`、
`test_all_faults_together_never_invent_data`）。界面渲染见 `ui-structural-snapshots.md`。

## 结果（17/17 检查通过）

| 故障 | 关键降级行为（API 结果，逐字段核对） | Provider 调用状态 |
|---|---|---|
| transport_timeout | `transport.status=manual_required`、`incomplete=true`、`missing=["transport"]`；`total_fen=null`、`known_subtotal_fen=60000`（酒店 40000+补贴 20000）、`transport_fen=null`、`missing_components=["transport"]`；`approval_reasons` 含 `PLAN_INCOMPLETE`；`status=PENDING_APPROVAL`；outbound/return 均为 `null`（未伪造交通 ID）；酒店 `selected`、天气 `ok` | transport `timeout` / hotel_contract `ok` / weather `ok` |
| no_protocol_hotel | `hotel.status=manual_required`、`candidate=null`、`incomplete=true`、`missing=["hotel"]`、note 含“协议酒店库不可用”；`total_fen=null`、`known_subtotal_fen=150000`（交通 130000+补贴 20000）、`hotel_fen=null`、`missing_components=["hotel"]`；`approval_reasons` 含 `PLAN_INCOMPLETE`；`status=PENDING_APPROVAL`；交通照常选中 B 组（T-BJS-SHA-1025-B / T-SHA-BJS-1026-B）、天气 `ok` | transport `ok` / hotel_contract `unavailable` / hotel_backup `ok`（候选 0）/ weather `ok` |
| weather_unavailable | `weather.status=unavailable`、`days=[]`、note 含“已省略”；`incomplete=false`、`missing=[]`（**不阻断规划**）；交通与酒店照常 `selected`；`total_fen=190000`、`approval_reasons=[]`、`violations=[]`、`status=DELIVERED`（S-02 同输入仍自动通过并交付） | transport `ok` / hotel_contract `ok` / weather `unavailable` |

跨故障：三种降级选中的交通 ID 都存在于 `tests/fixtures/providers/transport.json`（未伪造），且全部产出可读 API 结果（未崩溃）。

## 证据文件

| 文件 | 内容 |
|---|---|
| `AC-013-faults.json` | 汇总报告：三种故障的完整 API 结果（plan/cost/approval_reasons/violations/provider_calls/trajectory）+ 17 项检查逐项结果 |
| `api-transport-timeout.json` | 交通超时 Trip 的 API 结果（逐字段） |
| `api-no-protocol-hotel.json` | 无协议酒店 Trip 的 API 结果（逐字段） |
| `api-weather-unavailable.json` | 天气不可用 Trip 的 API 结果（逐字段） |
| `ui-structural-snapshots.md` | 三个降级 Trip 在工作台详情页的渲染（无障碍树 / DOM 文本逐字摘录） |
| `ui-01-transport-timeout.png` | 交通超时 Trip 详情页整页截图：交通两程“待人工补录”、合计“待补录（已知小计 600.00 元）”、PLAN_INCOMPLETE、审批卡与 provider_call timeout 审计 |
| `ui-02-no-protocol-hotel.png` | 无协议酒店 Trip 详情页整页截图：住宿“待人工补录（协议酒店库不可用）”、合计“待补录（已知小计 1,500.00 元）”、PLAN_INCOMPLETE |
| `ui-03-weather-unavailable.png` | 天气不可用 Trip 详情页整页截图：天气“已省略”注明、合计 1,900.00 元、状态“已交付”（未阻断规划） |
| `boot-1.log` | 被测子进程启动日志 |

## 复现

```bash
env -u DASHSCOPE_API_KEY uv run --offline python scripts/ac013_faults.py \
    artifacts/acceptance/TASK-010/ac013-fault-injection
# → AC-013 PASSED（17/17）
env -u DASHSCOPE_API_KEY uv run --offline pytest -q -p no:cacheprovider tests/test_planning.py
```

## 界面截图（PNG）

契约 AC-013 的证据列为“自动测试、API 结果、界面截图”，三者现已齐备。

**自动测试**：`scripts/ac013_faults.py`（17/17，真实子进程 HTTP 驱动）+ 回归
`tests/test_api.py::test_ac013_fault_injection_degrades_the_plan_over_http` + `tests/test_planning.py`。
**API 结果**：`AC-013-faults.json` 与三个 `api-*.json`。
**界面截图**：`ui-01/02/03-*.png`。实施负责人用 browser-use 驱动 Qoder 内置浏览器打开工作台
（fake 模式、固定数据目录 `var/ac013-ui`、无 `DASHSCOPE_API_KEY`、不访问外部网络，HC-06），以张三身份
逐个打开三个降级 Trip 的详情页整页截图；`ui-01` 已逐像素复核，确认渲染出“待人工补录”“已知小计
600.00 元”“PLAN_INCOMPLETE”“审批卡”与 `provider_call transport timeout` 审计。`ui-structural-snapshots.md`
另以无障碍树 / DOM 文本逐字摘录三种降级渲染，作为可独立复核的补充证据。

> 过程说明：首次尝试截图时内置浏览器面板 `visibilityState=hidden`（`bringToFront` 无效），PNG 无法生成，
> AC-013 曾暂记 `blocked`；项目所有者随后打开内置浏览器，PNG 补拍完成，状态改为 `passed`。
