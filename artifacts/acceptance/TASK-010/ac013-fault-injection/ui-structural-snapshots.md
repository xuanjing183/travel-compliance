# AC-013 界面证据：降级方案在工作台中的渲染（结构化快照）

> 捕获方式：实施负责人用 browser-use 驱动本机浏览器打开工作台（`http://127.0.0.1:8137/`，
> `TRAVEL_DESK_LLM_MODE=fake`、临时数据目录 `var/ac013-ui`、无 `DASHSCOPE_API_KEY`、不访问外部网络，HC-06），
> 以张三（员工）身份查看三个降级 Trip 的详情页。下面每一段都是 `take_snapshot`（无障碍树）或
> `evaluate_script`（渲染后 DOM 文本）**逐字**摘出的关键内容，证明界面确实按 §6.6 渲染降级状态。
>
> 三个 Trip 由 `scripts/ac013_faults.py` 同源的 HTTP 驱动预置（注入对应故障 + S-02 输入 + 提交），
> 因此界面读取的是与 API 证据完全相同的真实数据。
>
> **像素截图（PNG）**：本文件最初捕获时内置浏览器面板 `visibilityState=hidden`，PNG 无法生成；项目所有者
> 随后打开内置浏览器，三个降级 Trip 的整页 PNG 已补拍（`ui-01/02/03-*.png`，见 README）。本文件保留为
> 可独立复核渲染结果的补充证据（无障碍树 / DOM 文本逐字摘录），与 PNG 互为印证。

---

## 1. 交通超时（transport_timeout）→ 交通待人工补录

URL：`#/trips/911ffaba-7d3d-4548-b842-d02098518cd0`（张三 · revision 1 · 制度 v1.2）

页头状态：**北京 → 上海 待审批**

出行方案区（逐字）：

```text
方案不完整：transport 待人工补录。
交通
  去程  待人工补录
  返程  待人工补录
  去程：交通 Provider 超时，待人工补录；返程：交通 Provider 超时，待人工补录
住宿  星衡协议酒店·滨江店（协议酒店）  1 晚 × 400.00 元  demo_fixture · 距会议中心约 1.2 公里
天气  10-25 晴 17–24℃ demo_fixture ｜ 10-26 多云 17–23℃ demo_fixture
```

成本明细区（逐字）：

```text
去程交通  （空）
返程交通  待人工补录
住宿      1 晚 × 400.00 元 = 400.00 元
伙食补贴  2 天 × 100.00 元 = 200.00 元
合计      待补录（已知小计 600.00 元）
申报预算  2,500.00 元
预算差    —（方案不完整）
目的地 A 类城市；金额由代码按已发布规则计算。
缺失部分：transport，不按 0 计入。
```

需要审批的理由（逐字）：

```text
方案不完整  PLAN_INCOMPLETE
方案不完整：交通待人工补录，不能自动通过
缺失：transport
```

审批卡：`审批卡 · revision 1`，`等待 李四 审批`，来源徽标 `demo_fixture / model / policy`。

审计时间线中的 Provider 调用（逐字）：

```text
#10 provider_call  transport/demo_transport timeout，候选 0 个
#11 provider_call  transport/demo_transport timeout，候选 0 个
#12 provider_call  hotel_contract/demo_hotel ok，候选 2 个
#13 provider_call  weather/demo_weather ok，请求 2 天
#16 rules_evaluated  policy  v1.2 → PENDING_APPROVAL
#17 status_changed  PLANNING → PENDING_APPROVAL（require_approval）
```

界面结论：交通两程均显示“待人工补录”、合计为“待补录（已知小计 600.00 元）”、明确“缺失部分：transport，不按 0 计入”，并以 `PLAN_INCOMPLETE` 进入审批；酒店与天气照常。**未出现任何伪造的航班/价格。**

---

## 2. 无协议酒店（no_protocol_hotel，S-02 上海不设备用酒店）→ 酒店待人工补录

URL：`#/trips/2400b351-7ce6-48bf-b6c4-1880ccacc0eb`（张三 · revision 1 · 制度 v1.2）

页头状态：**北京 → 上海 待审批**

出行方案区（逐字）：

```text
方案不完整：hotel 待人工补录。
交通
  去程  XC1025 星衡航空  飞机 · 经济舱  北京 → 上海  09:00 – 11:20  2026-10-25  650.00 元  demo_fixture
  返程  XC1026 星衡航空  飞机 · 经济舱  上海 → 北京  18:00 – 20:20  2026-10-26  650.00 元  demo_fixture
住宿  待人工补录
      协议酒店库不可用；无可订酒店候选，待人工补录
天气  10-25 晴 17–24℃ demo_fixture ｜ 10-26 多云 17–23℃ demo_fixture
```

成本明细区（逐字）：

```text
去程交通  650.00 元
返程交通  650.00 元
住宿      待人工补录
伙食补贴  2 天 × 100.00 元 = 200.00 元
合计      待补录（已知小计 1,500.00 元）
申报预算  2,500.00 元
预算差    —（方案不完整）
缺失部分：hotel，不按 0 计入。
```

需要审批的理由（逐字）：

```text
方案不完整  PLAN_INCOMPLETE
方案不完整：酒店待人工补录，不能自动通过
缺失：hotel
```

审计时间线中的 Provider 调用（逐字）：

```text
#28 provider_call  transport/demo_transport ok，候选 1 个
#29 provider_call  transport/demo_transport ok，候选 1 个
#30 provider_call  hotel_contract/demo_hotel unavailable，候选 0 个
#31 provider_call  hotel_backup/demo_hotel ok，候选 0 个
#32 provider_call  weather/demo_weather ok，请求 2 天
#35 rules_evaluated  policy  v1.2 → PENDING_APPROVAL
```

界面结论：协议酒店库不可用且上海无备用候选（`hotel_backup ok，候选 0 个`），住宿显示“待人工补录”并给出原因“协议酒店库不可用”；合计“待补录（已知小计 1,500.00 元）”= 交通 1,300.00 + 补贴 200.00；以 `PLAN_INCOMPLETE` 进入审批；交通与天气照常。

---

## 3. 天气不可用（weather_unavailable）→ 省略并注明，不阻断规划

URL：`#/trips/e12f9774-f1ad-4f22-bd46-42f10f916615`（张三 · revision 1 · 制度 v1.2）

页头状态：**北京 → 上海 已交付**（`evaluate_script` 取 `main h2` = “北京 → 上海 已交付”）

天气区（逐字，取自渲染后 DOM）：

```text
天气  天气 Provider 不可用，已省略天气信息
```

`document.body.innerText.includes("已省略") === true`；交通与住宿照常选中
（去程 XC1025 650.00 元 / 返程 XC1026 650.00 元 / 住宿 星衡协议酒店·滨江店 1 晚 × 400.00 元）。

成本明细（逐字，取自渲染后 DOM）：

```text
合计      1,900.00 元
申报预算  2,500.00 元
预算差    结余 600.0…
```

界面结论：天气缺失被**省略并注明**（“天气 Provider 不可用，已省略天气信息”），规划**未被阻断**——S-02 同输入仍自动通过并交付，合计 1,900.00 元、状态“已交付”。符合 §6.6“省略天气并注明，不阻断规划”。

---

## 与 API/自动测试证据的对应

| 故障 | 界面（本文件） | API 结果 | 自动测试 |
|---|---|---|---|
| transport_timeout | 交通待人工补录、已知小计 600.00 元、PLAN_INCOMPLETE、待审批 | `api-transport-timeout.json` | `scripts/ac013_faults.py` 5 项 + `tests/test_planning.py::test_s09_*` |
| no_protocol_hotel | 住宿待人工补录（协议酒店库不可用）、已知小计 1,500.00 元、PLAN_INCOMPLETE、待审批 | `api-no-protocol-hotel.json` | `scripts/ac013_faults.py` 6 项 + `tests/test_planning.py::test_ac013_*` |
| weather_unavailable | 天气已省略并注明、合计 1,900.00 元、已交付（未阻断） | `api-weather-unavailable.json` | `scripts/ac013_faults.py` 4 项 + `tests/test_planning.py::test_weather_*` |

三者界面渲染与 API/审计完全一致，均无崩溃、无伪造数据。
