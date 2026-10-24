# AC-028 Live Provider 复验状态

**状态：`passed`（SHOULD，2026-10-21 真实复验通过，见文末）**

> 旧证据基于已替换的天气实现与 Tavily+LLM 价格抽取，已不能证明当前实现，故曾不沿用原 `passed` 结论。
> 2026-10-21 凭据配置齐备后按下方「2026-10-21 真实复验」重新验证并解除 `blocked`。

## 当前实现

- 交通：Kiwi MCP `search-flight`，请求 `currency=CNY`；响应不是 CNY 时整份判为 invalid，禁止把外币数值当人民币。
- 酒店：本地协议/备用目录是唯一可信价格来源。Tavily 只返回无价格参考数量，不生成候选 ID、价格、评分或可订状态；参考数量会在方案说明中披露，但不参与费用计算。
- 天气：和风天气 `/geo/v2/city/lookup` + `/v7/weather/30d`。城市查询限定中国范围，精确名称优先并按 rank 消歧；超出 30 日响应范围时明确降级，不估测。
- 网络边界：Live 模式只允许 `mcp.kiwi.com`、`api.tavily.com` 与配置的和风天气项目专属 Host；未配置天气 Host 时 fail closed。
- 健康接口：动态报告 `live_transport`、`live_hotel_hybrid`、`live_weather_qweather`；缺天气配置时报告 `live_weather_qweather_stub` / `unavailable`。接口只返回凭据是否配置的布尔值。

## 自动验证

```text
uv run pytest -q
360 passed in 15.08s
```

覆盖范围包括：

- 和风天气鉴权头、城市同名消歧、30 日解析、日期越界、超时和错误响应。
- 混合酒店本地可信候选、Tavily 无价格参考、参考计数复位、缺 key 不影响本地目录。
- Kiwi MCP/SSE 解析、CNY 参数与非 CNY 响应拒绝。
- Live 工厂降级、网络允许清单、Provider health 实现名/模式/凭据脱敏。
- 规划结果明确显示 Tavily 参考不参与候选与费用。

聚焦回归：`tests/test_live_providers.py tests/test_provider_mode.py tests/test_planning.py` → `76 passed`。
修改后的真实调用脚本与整链脚本均通过 `py_compile`。

运行态已重启为 `TRAVEL_DESK_PROVIDER_MODE=live`、`TRAVEL_DESK_LLM_MODE=fake`。管理员 health 实测返回：
`provider_mode=live`；transport=`live_transport/ok`；hotel=`live_hotel_hybrid/ok`；
weather=`live_weather_qweather_stub/unavailable`；允许清单仅 `mcp.kiwi.com` 与 `api.tavily.com`，旧实现错误未混入当前 implementation。

## 当前真实冒烟（部分通过）

`uv run python scripts/test_live_providers.py` 已执行：

- QWeather：`SKIP`，缺项目 Host/Key。
- 混合酒店：`PASS`，上海本地协议目录返回 2 个可信价格候选（480/400 元每晚）；Tavily 因未配置 key 跳过。
- Kiwi MCP：`PASS`，北京→上海（2026-10-31）返回 15 个 **CNY** 航班候选，示例价格 658～807 元；当前 CNY 请求与响应校验路径真实通过。

脚本总结果为 2 pass / 2 skip 项（天气与可选 Tavily），所以 AC-028 仍不能改为 `passed`。

## 阻塞原因与解除条件

当前进程环境没有：

- `TRAVEL_DESK_QWEATHER_API_HOST`
- `TRAVEL_DESK_QWEATHER_API_KEY`

因此不能真实调用当前 QWeather Provider。获得并在本地进程环境安全设置这两个值后，运行：

```powershell
$env:TRAVEL_DESK_PROVIDER_MODE="live"
uv run python scripts/test_live_providers.py
uv run python scripts/test_ac028_integration.py
```

只有三类当前实现的真实调用与整链结果均留证后，AC-028 才能从 `blocked` 改为 `passed`。密钥不得写入仓库、日志、证据或聊天记录。

## 2026-10-21 真实复验（解除 blocked）

凭据注册与注入：项目所有者提供和风天气新控制台凭据（凭据名 travel）与项目专属 API Host（`*.re.qweatherapi.com`）。密钥只写入仓库根目录 `.env`（Git 忽略，`./start-live.sh` 操作员加载）与 User 级环境变量（`setx`，满足"任何模式下都注入"）；证据与回复中只有布尔状态（HC-07）。老域名 `devapi/api.qweather.com` 对新控制台凭据实测 403，属预期（新凭据绑定专属 Host）。

两个解除条件脚本在配置齐备后的真实运行结果：

```text
uv run python scripts/test_live_providers.py
  weather   : PASS   洛阳 today+7..+9 三天真实预报（阴 8~15℃ / 晴 9~16℃）
  hotel     : PASS   上海 2 家协议酒店（480/400 元）；洛阳 2 家可信备用酒店
                     （380/350 元，本地目录、非 Tavily）；拉萨无本地候选 →
                     Tavily 真实返回 5 条无价格结构化参考（标题/链接/摘要）
  transport : PASS   北京→上海 15 个 CNY 航班候选（658~808 元）

uv run python scripts/test_ac028_integration.py
  ✓ 北京→上海（today+10..+12）整链：创建→提交→规划→合规自动批准→DELIVERED
  ✓ CA1883 live ¥658；酒店=本地协议目录（可信价格来源）；天气 3 天 ok（live）
  ✓ 审计 4 条 provider_call（transport×2、hotel_contract、weather 全 ok）
```

前端浏览器快照（员工 张三 视图）核对：拉萨行程天气区展示 3 天 live 预报（11-02 小雨 2–15℃ 等）、
网络参考区 5 条带链接与免责说明、日程自动出现"天气小雨，建议携带雨具（live）"、审计时间线含
`weather/live_weather_qweather ok，请求 3 天`。洛阳原句（10月20～23日）实测：历史日期 10-20
如实注明"无预报，已省略"，10-21/22/23 显示真实预报；酒店选中备用目录克里斯国际酒店（超标例外），
`total_fen=null` + `PLAN_INCOMPLETE` 保持诚实。

复验中发现并固化的两个真实边界（均有测试）：

1. **凭据"安全限制"拒绝 GeoAPI**：该凭据 `/geo/v2/city/lookup` 返回 HTTP 403（Security Restriction），
   而天气端点正常。Provider 对城市查询 402/403 回退到内置常见商务城市坐标
   （`providers/qweather_geo.py`，仅为请求定位的公开事实数据，非业务数据）；控制台放开 GeoAPI 后自动恢复查询路径。无坐标城市照常降级。
2. **套餐与日期窗口**：预报跨度按 30d→15d→10d→7d→3d 回退（402/403 视为无该跨度权限）；和风只预报
   当日及以后（东八区），请求中的历史日期跳过并在方案中注明，不估测、不编造。

全量回归 `uv run pytest -q` → **379 passed**（含上述两个边界的 mock 测试）。AC-028 自此 `passed`；
遗留提醒：Tavily 旧密钥按项目所有者 2026-10-21 明确决定沿用（曾出现在聊天中，风险由所有者知情接受）。
