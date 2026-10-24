# 实施契约 v1.1：差旅合规与审批工作台（Travel Compliance Desk）

> 本文件是对 `docs/contract.md` v1.0 的独立修订版，不覆盖原文件。
> 本契约用于交给未参与前序讨论的执行 AI 独立实施；执行者不得依赖隐含对话补全业务规则。

| 项 | 内容 |
|---|---|
| 契约版本 | v1.1 正式稿（2026-10-19，依据 v1.0 评审意见、v1.1 草稿评审和执行者可执行性复核修订，见第 16 节） |
| 状态 | 正式稿，取代 `docs/contract.md` v1.0；第 0.4 节授权问题解决前，仅可开展不产生外部费用或外部写入的任务 |
| 日期 | 2026-10-19 |
| 项目所有者 | 用户：决定业务语义、范围、费用授权、验收豁免和对外发布 |
| 实施与交付负责人 | 执行 AI：在契约边界内自主决定实现细节，对交付、验证和证据负责 |
| 目标项目位置 | `C:\Users\22316\Desktop\1\lc-course\travel-compliance-desk\` |
| 只读素材位置 | `C:\Users\22316\Desktop\1\lc-course\` 中目标项目目录以外的学习 Demo |
| 默认运行环境 | Windows、Python 3.11、uv；不要求 Docker |

---

## 0. 输入、事实状态与本版修订决定

### 0.1 用户明确要求

| 编号 | 要求 |
|---|---|
| U-01 | 项目用于 FDE / AI 应用开发实习作品集，支持 HR 3 分钟演示和技术面 10 分钟深挖。 |
| U-02 | 避免教程 Demo 同质化，优先展示真实业务闭环、确定性边界、人工审批和可追溯性。 |
| U-03 | 前端使用原生 HTML、CSS、JavaScript 单页，由 FastAPI 托管。 |
| U-04 | Demo 模式为主要交付；真实订票、支付和 SMTP 不在范围内。 |
| U-05 | 邮件交付终点是本地 Outbox。 |
| U-06 | 素材目录只读，目标项目作为独立项目开发。 |
| U-07 | 本轮规划要求自包含、可验证，不让执行 AI 猜测关键业务规则。 |

### 0.2 继承自 v1.0 的已核实事实

以下内容记录为 v1.0 调查结论。v1.1 的编写过程没有重新执行环境核查；执行 AI 必须在 TASK-001 中复核并保存证据。

| 编号 | v1.0 记录的事实 | 当前状态 |
|---|---|---|
| F-01 | Windows；Python 3.11.0；uv 0.12.2；Node 24.19；无 Docker。 | 待 TASK-001 复核 |
| F-02 | 已存在 LangChain、LangGraph、FastAPI、Chroma、Ragas、SSE 等学习环境依赖；pytest 未安装。 | 待 TASK-001 复核；不得假设目标项目已具备相同依赖 |
| F-03 | 环境变量名涉及 DashScope、DeepSeek、Tavily、LangSmith、OSS、MinerU；未发现 SMTP。 | 只允许核对变量名和是否存在，不得读取、打印或提交值 |
| F-04 | 素材目录不是 Git 仓库；目标目录已于 2026-10-19 初始化为独立 Git 仓库，分支 `main`，基线提交 `1bd364b`（v1.0 契约与 `.gitignore`），标签 `contract-v1.0`。 | 待 TASK-001 复核；以复核时的 `git log`/`git status` 为准 |
| F-05 | 原调查识别到五个学习 Demo。 | 具体路径、功能和可复用性尚未写入基线，TASK-001 必须补齐 |

### 0.3 本版为消除歧义采用的决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | Demo 模式是 MUST；Live 模式是 SHOULD，Live 失败或外部服务不覆盖国内路线，不阻止 Demo 交付通过，但必须如实记录。 | 与“Live 可选”的原始要求保持一致。 |
| D-02 | 原 v1.0 中被验收场景强制覆盖的 S 级需求统一提升为 MUST；真正可选的 Live 集成单独标为 SHOULD。 | 避免“标为应当、实际必须”的冲突。 |
| D-03 | Outbox 邮件分为 `trip_delivery` 与 `workflow_notification`。只有前者受“必须批准后写入”的限制；驳回和要求修改可以写后者。 | 消除驳回通知与交付硬约束冲突。 |
| D-04 | Demo 酒店 Provider 包含“协议酒店库”和“已验证备用酒店目录”两类本地 fixture；Tavily 只提供无价格参考信息，绝不作为价格候选。 | 保证无协议酒店时仍有可信、可追溯的价格来源。 |
| D-05 | Demo 图片识别只允许本地文件转 base64 调用模型，不得上传 OSS；OSS 回退不纳入本次范围。 | 遵守 Demo 外部网络和外部写入边界。 |
| D-06 | 核心确定性行为采用 100% 场景通过门槛；模型质量指标单独评估，不用“允许核心流程偶发失败”的总通过率掩盖缺陷。 | 保证验收可判定。 |
| D-07 | 内部和 API 金额统一使用人民币“分”的整数，界面显示两位小数。 | 避免浮点误差。 |
| D-08 | 验收分两类证据：确定性验收使用 Fake/Recorded LLM 验证业务流程；模型质量类验收（问答指标、规则抽取指标、检索对比、真实图片识别）必须使用真实模型或真实 embedding，不得用 Fake/Recorded 结果计算或宣称通过，统称“授权门控验收”，完成要求见第 14 节。真实模型耗时（AC-020）同样不得使用 Fake/Recorded，但属于 SHOULD。 | Fake 模型下的质量指标和耗时没有意义；同时避免未授权时完成定义无法达成。 |
| D-09 | `POLICY_BLOCKED → COLLECTING` 不增加 revision，只追加审计事件；revision 仅在 `request_changes` 和“按新版本重新校验”时 +1。 | 被阻断的 Trip 尚未生成方案或进入审批，不需要版本隔离。 |
| D-10 | “早班”定义为出发时刻早于当地时间 08:00（不含 08:00）。 | 使偏好验收可判定。 |
| D-11 | 制度违规仅指可追溯到条款的三类：酒店超上限、提前天数不足、交通舱位不符；`BUDGET_EXCEEDED`、`OVER_AUTO_APPROVAL_LIMIT`、`PLAN_INCOMPLETE` 属于审批理由，不是违规。 | 违规必须满足 HC-02 的条款追溯要求；超预算来自员工申报，无制度条款可引。 |
| D-12 | “按新版本重新校验”复用当前 revision 已选候选，仅重算成本并按新发布版本重判，不重新调用 Provider；申请人和直属经理均可触发。 | 使 S-12/AC-024 可判定，避免 Provider 数据漂移引入不确定性。 |
| D-13 | 槽位完整仅表示允许提交；进入 `VALIDATING` 必须由员工显式提交动作触发。 | 与 AC-002、S-08 的“提交/重新提交”一致，并为 AC-020 提供明确计时起点。 |
| D-14 | 格式非法（无法解析、必填文本空白、预算 ≤ 0）留在 `COLLECTING` 追问；语义非法（出发日已过、结束早于开始、超 15 天）进入 `POLICY_BLOCKED`。 | 消除 §4.4“追问非法字段”与 §4.5 硬阻断的冲突。 |
| D-15 | 从备用目录选择价格合规的酒店本身不构成违规或审批理由，仅标记 `backup_fixture` 来源并写审计。 | 制度没有“必须住协议酒店”条款；S-03 的审批由其超标本身触发。 |
| D-16 | `effective_date` 仅作记录，不参与运行时判断；运行时以版本发布状态为准。 | 避免发布未来生效日期产生歧义。 |
| D-17 | 门控指标口径：引用命中 = gold 条款出现在引用列表即命中；数值统一换算为“分”后比较；gold 规则以 §4.2 的 8 条为单位（允许一对多）；无效引用采用评测脚本注入样本（N≥5）测拦截率；AC-025 主指标为引用条款 Recall@3，打平保留纯向量；题集与 gold 在首次真实运行前冻结并记录 SHA-256。 | AUTH-01 已批准，指标必须事先定死且可判定，避免 0/0 与出题人自评漂移。 |
| D-18 | 关键 Provider 数据缺失（交通或酒店待人工补录）时方案 `total_fen` 为 `null`，另给 `known_subtotal_fen`；缺失部分不得按 0 计入，`total_fen` 为 `null` 时不得自动通过；`known_subtotal_fen` 已超过申报预算时仍标记 `BUDGET_EXCEEDED`（保守下界）。 | 使 S-09/AC-013 的判定字段明确。 |
| D-19 | 显式提交走 `POST /api/trips/{id}/submit`（返回 SSE 流）；员工另可用 `PATCH /api/trips/{id}/slots` 直接修改槽位；两者都携带 expected_revision，改动写审计。 | D-13 需要明确提交动作；确认摘要页需要非对话的改槽位途径。 |
| D-20 | 提交时把个人偏好快照进行程（来源 `personal_snapshot`）；行程级 `preferences` 非空时以行程级为准（`trip_override`）；审计记录本次规划实际使用的偏好及来源；修改个人偏好不影响已提交行程，直到再次提交。 | 消除个人偏好与行程槽位偏好的冲突歧义。 |
| D-21 | 判定结果为需审批且申请人没有直属上级时，不创建审批卡，SSE 返回 `error`（code=`NO_APPROVER`），Trip 保持 `COLLECTING` 并写审计；可自动通过的行程照常。 | 经理以员工身份出差时避免无人可批的悬挂状态。 |

### 0.4 执行前必须取得的授权

| 编号 | 问题 | 默认边界 | 决定人 |
|---|---|---|---|
| AUTH-01 | 是否允许使用现有模型/API 凭证产生少量开发与验收调用费用 | **已批准（2026-10-19，项目所有者）**：仅限 DashScope 模型与 embedding；累计费用上限 50 元；预计累计达到 40 元（上限 80%）时暂停并请示；禁止批量压测。每次真实调用的模型、token 用量和按公开单价估算的费用须记入 `artifacts/cost-ledger.md`；无法取得单价时按同系列最高公开单价保守估算。影响范围：第 14 节列出的授权门控验收、AC-020、AC-027 及 TASK-001 高风险验证 | 项目所有者 |
| AUTH-02 | 是否允许 Live 模式调用 Kiwi、Tavily、Open-Meteo | 未明确前不调用；不影响 Demo 主交付 | 项目所有者 |
| AUTH-03 | 是否公开至 GitHub | M5 后单独决定；本契约不授权发布或推送 | 项目所有者 |
| AUTH-04 | 是否允许任何 OSS 上传或其他外部写入 | 默认禁止；本次 Demo 不需要 OSS | 项目所有者 |

执行 AI 可以在授权未解决时直接开展 TASK-001～TASK-008 中所有本地工作、Fake/Recorded 测试和 Demo fixture 开发，但不得把 Mock 结果声明为真实集成通过。

---

## 1. 项目目标

### 1.1 目标用户与场景

| 角色 | 演示账号 | 场景 | 要获得的结果 |
|---|---|---|---|
| 员工 | 张三，销售部，北京 | 提交差旅需求、补齐信息、查看方案和依据 | 在申请前知道是否合规、超出多少以及下一步动作 |
| 审批人 | 李四，销售部经理，张三直属上级 | 审批例外方案 | 看到成本、违规原因、制度依据和版本后作出决定 |
| 管理员 | 王五，行政/财务 | 维护制度、确认规则、查看审计和演示状态 | 确保运行时只使用已确认规则，并能追溯每次决定 |
| 非直属审批人（权限负例） | 赵六，采购部经理，非张三直属 | 尝试审批张三的 Trip | 收到 403，不能审批（AC-009） |

### 1.2 要解决的问题

50～500 人的中小企业通常将差旅制度保存在 Word、Markdown 或 PDF 中。员工靠自己翻制度，审批人靠经验判断，行政和财务常在报销阶段才发现违规。本项目要把制度转换为可确认、可执行的规则，使系统能够在规划阶段计算成本并识别例外，把需要判断的事项交给直属经理审批，同时保留从输入、条款、规则、Provider 数据到审批和交付的完整证据链。

### 1.3 必须完成的业务闭环

```text
员工提交需求
→ 系统只追问缺失信息
→ 获取可追溯的交通、酒店和天气数据
→ 代码计算成本并按已发布规则判断
→ 合规且满足自动通过条件时自动批准
→ 存在制度违规或非违规审批理由时由直属经理决定
→ 批准后生成行程单并写入 trip_delivery Outbox
→ 驳回或要求修改时写入 workflow_notification
→ 全过程可在审计时间线中复盘
```

制度侧闭环：

```text
管理员上传制度
→ 系统抽取候选规则并保留原文引用
→ 管理员逐条确认、修改、拒绝或标记不支持
→ 所有候选均处理后发布
→ 发布操作原子化，旧版本归档
→ 新规划只使用新发布版本，既有行程保留原绑定版本
```

---

## 2. 当前基线

### 2.1 已知状态

- 目标目录：`C:\Users\22316\Desktop\1\lc-course\travel-compliance-desk\`。
- 已有规划文件：`docs/contract.md` v1.0 和本文件 `docs/contract-v1.1.md`。
- 目标技术方向：Python 3.11、uv、FastAPI、LangGraph、SQLite、原生 HTML/CSS/JavaScript。
- Docker 不作为运行或验收条件。
- Demo 模式使用本地 fixture；除模型 API 外不得访问外部服务。

### 2.2 待 TASK-001 核实并写入 `docs/baseline.md` 的内容

1. 目标目录 Git 状态、当前分支和基线提交；若未初始化，记录“未初始化”。
2. Python、uv、操作系统版本与可用命令。
3. 五个素材 Demo 的准确路径、入口、用途、可复用内容和不复用理由。
4. 目标目录已有文件，避免覆盖非本次工作。
5. 环境变量名是否存在，不读取或记录变量值。
6. 模型、结构化输出、多模态、LangGraph 中断恢复等外部或高风险能力的最小验证结果。

基线未核实项必须标记为“未验证”，不得在 README 中写成已通过能力。

---

## 3. 范围、优先级与非目标

### 3.1 优先级定义

- **MUST**：不满足则 Demo 主交付不能验收。
- **SHOULD**：应实现或验证，但因外部覆盖、授权或成本受阻时，可记录为“阻塞/未完成”，不使 Demo 主交付失败。
- **MAY**：可选增强，不属于本次交付义务。
- **OUT**：明确不做。

### 3.2 MUST 范围

1. 自然语言需求接入、缺失信息追问和槽位修改。
2. 会议邀请图片识别与人工确认。
3. 当前制度问答及条款引用。
4. Markdown 和文本型 PDF 制度上传、候选规则抽取、人工确认、发布和归档。
5. 确定性成本计算、规则判断和审批判定。
6. Demo 交通、协议/备用酒店、天气和时钟 Provider。
7. 每日行程生成、代码选择候选、数据来源标记。
8. 一级人工审批、服务重启恢复、重复操作保护。
9. 行程单、两类 Outbox 邮件和审计时间线。
10. 员工、审批人、管理员三角色工作台。
11. 员工偏好、故障注入和一键重置。
12. 问答、规则抽取、端到端场景评测和检索对比。
13. README、产品、架构、集成、API、演示和验收文档。

### 3.3 SHOULD 范围

1. Kiwi、Tavily、Open-Meteo 等 Live Provider 的最小真实验证。
2. 至少一次真实模型驱动的黄金路径和图片识别；需满足 AUTH-01。
3. Live 模式黄金路径；仅在 AUTH-02 已授权且外部服务可用时执行。

### 3.4 OUT

- 真实订票、出票、改签、支付、报销和发票 OCR。
- 真实 SMTP 发信。
- 登录认证、SSO、多租户和生产级安全体系。
- 二级或多级审批。
- 扫描型 PDF OCR；此类文件必须返回明确的“不支持”错误，不得静默抽取空内容。
- 让多个 Agent 自由对话决定金额、合规结论或工作流走向。
- Kafka、Kubernetes、微服务和 Docker 验收。
- OSS 上传及其他外部写入。
- 修改只读素材目录。
- 未经授权发布 GitHub、部署公网或购买外部资源。

---

## 4. 业务规则口径

### 4.1 制度版本

| 版本 | 初始状态 | 差异 |
|---|---|---|
| v1.0 | archived | 机票提前 5 天；自动通过阈值 2000 元 |
| v1.2 | published | 机票提前 3 天；自动通过阈值 3000 元 |
| v1.3 | 仅在 S-12 中创建 | 将员工 A 类城市住宿上限从 500 元降至 450 元，其他规则不变 |

规则：

- 同一时刻只能有一个 `published` 制度版本。
- 发布必须在单个数据库事务内完成：新版本变为 `published`，原版本变为 `archived`。
- Trip 每个 revision 在规划开始时绑定当时的发布版本。
- `request_changes` 决定作出时 revision +1（旧审批卡同时失效）；重新提交规划时绑定规划开始时的当前发布版本。
- “按新版本重新校验”（D-12）：由申请人或直属经理触发，复用当前 revision 已选定的交通/酒店候选，仅重算成本并按新发布版本重新判断，不重新调用交通/酒店/天气 Provider；创建新 revision、使旧审批卡失效，并绑定新版本；旧方案和旧规则判断保留在审计中。
- `effective_date` 仅作记录，不参与运行时判断；运行时以版本发布状态为准（D-16）。
- `POLICY_BLOCKED` 后员工修改槽位回到 `COLLECTING` 时 revision 不变（D-09）；重新提交时仍按规划开始时的当前发布版本绑定，若绑定版本发生变化，必须在审计中记录。

### 4.2 v1.2 规则

| 规则 ID | 条款 | 内容 |
|---|---|---|
| RULE-CITY-001 | POL-1 | A：北京、上海、广州、深圳；B：杭州、南京、成都、武汉、西安、重庆、苏州、天津；C：其他 |
| RULE-HOTEL-001 | POL-2 | 员工住宿上限：A 50000 分/晚、B 40000 分/晚、C 30000 分/晚 |
| RULE-HOTEL-002 | POL-2 | 经理及以上：A 65000 分/晚、B 50000 分/晚、C 40000 分/晚 |
| RULE-MEAL-001 | POL-3 | 伙食补贴：A 10000 分/天、B 8000 分/天、C 6000 分/天 |
| RULE-CLASS-001 | POL-4 | 全员经济舱或高铁二等座 |
| RULE-ADVANCE-001 | POL-5 | 机票出发日距申请日不少于 3 个自然日 |
| RULE-AUTO-001 | POL-6 | 总额不超过 300000 分、无违规且不超申报预算时自动通过 |
| RULE-DURATION-001 | POL-7 | 单次出差不超过 15 个自然日 |

规则引擎只支持上述封闭类型：城市等级、住宿上限、伙食补贴、交通舱位、提前预订、自动通过阈值和单次时长。抽取到其他类型时标为 `unsupported`，不得静默丢弃或参与运行时判断。

### 4.3 计算口径

- 晚数 = 结束日期 − 开始日期。
- 天数 = 晚数 + 1。
- 总额（分）= 往返交通 + 酒店每晚价格 × 晚数 + 伙食补贴 × 天数。
- 交通缺失（待人工补录）时 `total_fen` 为 `null`，另给 `known_subtotal_fen` 表示已知部分小计；缺失交通不得按 0 计入（D-18），`total_fen` 为 `null` 时不得自动通过。
- 景点、餐饮活动等建议不计入总额。
- 所有金额由代码使用整数分计算；模型不得进行最终金额计算。
- Demo 时钟固定 `DEMO_TODAY=2026-10-20`。
- 提前天数 = 出发日期 − Demo 当前日期。

### 4.4 必填槽位

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| origin | string | 是 | 不因员工 home_city 自动确认；可作为建议值展示 |
| destination | string | 是 | 使用规范城市名 |
| start_date | ISO date | 是 | 必须是明确日期，模糊表达需追问 |
| end_date | ISO date | 是 | 不得早于 start_date |
| purpose | string | 是 | 非空白文本 |
| declared_budget_fen | integer | 是 | 大于 0 |
| meeting_address | string | 否 | 可由图片识别后确认 |
| preferences | object | 否 | 用户可以修改或忽略 |

系统只追问缺失或格式非法字段（D-14）：无法解析为所需类型、必填文本空白、`declared_budget_fen` ≤ 0 属于格式非法，留在 `COLLECTING` 追问；字段可解析但违反制度语义（出发日早于当前日、结束早于开始、时长超过 15 天）属于语义非法，按 §4.5 进入 `POLICY_BLOCKED`。槽位完整后系统展示确认摘要，员工显式提交后才进入校验（D-13）。模型提取的槽位必须允许员工修改；图片识别结果未经员工确认不得直接进入规划。

### 4.5 规则结果与审批判定

| 类型 | 条件 | 状态结果 |
|---|---|---|
| 硬性阻断 | 语义非法（D-14）：出发日早于当前日、结束早于开始、时长超过 15 天 | `POLICY_BLOCKED`，不得调用交通、酒店和天气 Provider |
| 制度违规（violations） | 酒店超上限、提前天数不足、交通舱位不符 | `PENDING_APPROVAL` |
| 审批理由（approval_reasons，非违规） | `BUDGET_EXCEEDED`（总额超申报预算）、`OVER_AUTO_APPROVAL_LIMIT`（总额 > 300000 分）、`PLAN_INCOMPLETE`（关键 Provider 不可用导致方案不完整） | `PENDING_APPROVAL` |
| 无违规且满足自动条件 | 无制度违规、`total_fen` 非 null、总额 ≤ 300000 分且不超预算 | `AUTO_APPROVED` |

任一制度违规或审批理由存在即 `PENDING_APPROVAL`。二者区分见 D-11：制度违规必须可追溯到条款，每项至少包含 `code`、`rule_id`、`policy_version`、`clause`、`quote`、`actual_value`、`limit_value`、`source=policy`（HC-02）；审批理由无制度条款可引，不携带 `clause`/`quote`，其中 `OVER_AUTO_APPROVAL_LIMIT` 引用 POL-6 说明阈值依据。

违规 `code` 取值：`HOTEL_OVER_CAP`、`ADVANCE_BOOKING_VIOLATION`、`CABIN_CLASS_VIOLATION`；审批理由 `code` 取值：`BUDGET_EXCEEDED`、`OVER_AUTO_APPROVAL_LIMIT`、`PLAN_INCOMPLETE`。

### 4.6 审批决定

审批人只能是申请人的直属经理，且不能是申请人本人。审批请求必须携带 Trip 当前 revision。

- `approve`：无需意见；进入 `APPROVED`，然后执行幂等交付。
- `reject`：必须填写非空意见；写 `workflow_notification`，进入 `REJECTED` 终态。
- `request_changes`：必须填写非空意见；写 `workflow_notification`，旧审批卡失效，revision +1，回到 `COLLECTING`。

对旧 revision、已处理审批或重复 Idempotency-Key 的请求不得重复执行：

- 同一 Idempotency-Key 重试返回第一次的结果；
- 不同 Key 对已处理 revision 再决策返回 HTTP 409；
- 旧 revision 的审批返回 HTTP 409 和当前 revision；
- 缺少 `Idempotency-Key` 返回 HTTP 400；
- 同一 `Idempotency-Key` 但请求内容不同返回 HTTP 422。

### 4.7 交付与通知

OutboxEmail 增加 `kind`：

- `trip_delivery`：只能在 `APPROVED` 或 `AUTO_APPROVED` 后创建；收件人为员工，抄送直属经理；包含结构化行程单链接和模板正文。
- `workflow_notification`：用于 `reject` 或 `request_changes`；收件人为员工，可抄送审批人；必须标注决定、意见和 revision。

交付幂等键为 `(trip_id, revision, kind)`。相同键只能存在一条记录。

若批准后交付事务失败，Trip 保持 `DELIVERY_PENDING` 并记录错误；重试不得生成重复邮件。重试入口为管理员接口 `POST /api/trips/{id}/delivery/retry` 和服务启动时的自动重试，二者都受 `(trip_id, revision, kind)` 幂等键约束；交付写失败故障只通过自动化测试钩子注入，不进入管理员故障面板。只有行程单和 `trip_delivery` Outbox 均写入成功后才能进入 `DELIVERED`。

模型只能生成邮件中的摘要段落，不能改变金额、日期、Provider 候选、审批结果和制度引用。

关键 Provider 不可用但经理仍批准时，行程单必须将缺失部分标为“待人工补录”，不得编造交通或酒店详情。

### 4.8 制度引用校验

制度抽取时允许以下文本规范化：Unicode NFC、统一换行、连续空白折叠为一个空格。候选规则的 `quote` 必须能在规范化后的制度全文中找到，并保存条款号、页码或段落定位；否则标为 `invalid_quote`，不得确认或发布。

---

## 5. 需求清单

### 5.1 需求接入

- **REQ-001 (MUST)** 员工可以使用自然语言提交需求；系统抽取槽位，只追问缺失或非法项。
- **REQ-002 (MUST)** 员工可以多轮补充或修改槽位；每次变更记录变更前后值、actor、时间和 revision。
- **REQ-003 (MUST)** 支持 PNG/JPG 会议邀请图片，抽取城市、明确日期、事由和会议地址；员工确认后才能写入槽位。

### 5.2 制度知识与规则

- **REQ-004 (MUST)** 制度问答默认基于当前发布版本，回答包含条款号和可定位原文；无依据时明确回答不知道。
- **REQ-005 (MUST)** 管理员可以上传 Markdown 或文本型 PDF，指定版本号和生效日期，生成 draft；扫描型 PDF 返回明确错误。
- **REQ-006 (MUST)** 系统抽取候选规则，包含类型、条件、数值、单位、来源条款、原文引用和引用定位；无效引用和不支持类型明确标记。
- **REQ-007 (MUST)** 管理员逐条确认、修改、拒绝或标记不支持；修改后的数值与原文引用不一致时必须填写修改理由并记入审计；全部候选处理完且 §4.2 的 7 类封闭类型每类至少有 1 条已确认规则才能发布，否则发布接口返回 422。
- **REQ-008 (MUST)** 运行时只使用 published 版本中 confirmed 规则；发布和旧版本归档原子化。

### 5.3 规划与规则引擎

- **REQ-009 (MUST)** 代码按第 4 节计算成本、识别违规和决定自动通过/审批/阻断。
- **REQ-010 (MUST)** 交通 Provider 根据路线和日期返回结构化候选；每个候选有稳定 ID、班次、舱位、价格和来源。
- **REQ-011 (MUST)** 酒店 Provider 优先返回协议酒店；无合规协议酒店时可返回本地已验证备用目录候选，并明确来源。
- **REQ-012 (MUST)** 行程模块根据事由、会议安排、天气和偏好生成逐日安排；不得修改确定性字段。
- **REQ-013 (MUST)** 最终候选由代码选择：先过滤必要条件（含酒店 `available_date_ranges` 可订日期，见 §7.1），再按合规、总价和偏好排序；无合规候选时选择超标最少的可信候选并标记例外。
- **REQ-014 (MUST)** 员工可以查看、修改和清除偏好；下一次规划使用当前偏好，并在审计中记录偏好是否影响排序。

### 5.4 审批、恢复与交付

- **REQ-015 (MUST)** 需要审批时工作流暂停，审批卡展示成本拆解、预算差额、规则违规、非违规审批理由、数据来源和方案摘要。
- **REQ-016 (MUST)** 服务端执行角色、直属关系、本人审批、revision 和重复请求校验。
- **REQ-017 (MUST)** 服务进程重启后待审批 Trip 仍存在，合法审批后从正确节点恢复。
- **REQ-018 (MUST)** 批准后幂等生成行程单和 `trip_delivery`；驳回/要求修改生成 `workflow_notification`。
- **REQ-019 (MUST)** 待审批期间发布新版本时提示制度变化；重新校验会创建新 revision 并使旧审批失效。

### 5.5 审计、Provider 与工作台

- **REQ-020 (MUST)** 审计时间线追加记录槽位变化、检索条款、规则结果、Provider 调用摘要与来源、方案版本、审批决定、交付/通知和错误；除 Demo reset 外不允许修改既有事件。
- **REQ-021 (MUST)** 所有外部数据通过 Provider 接口访问；Demo Provider 必须本地可复现，Live Provider 独立配置。
- **REQ-022 (MUST)** 管理员可注入航班超时、无协议酒店、天气不可用，并按第 6.6 节降级；交付写失败只通过自动化测试钩子注入，不进入管理员故障面板。
- **REQ-023 (MUST)** 提供员工、审批人和管理员三角色工作台，遵循第 6.4 节权限矩阵。
- **REQ-024 (MUST)** 界面、API、方案和审计标明数据来源：`demo_fixture`、`backup_fixture`、`live`、`policy` 或 `model`。
- **REQ-025 (MUST)** 管理员可一键重置 Demo 数据；重置恢复种子用户、v1.0/v1.2、酒店和 Provider fixture，删除 Trip、审批、审计、Outbox、故障设置和偏好。

### 5.6 评测与文档

- **REQ-026 (MUST)** 制度问答评测不少于 25 题，其中不少于 5 题无制度答案。
- **REQ-027 (MUST)** 使用 v1.2 黄金规则集评测规则抽取召回率、引用有效性和不支持类型识别；同时报告 precision，但本版不设置 precision 硬阈值。
- **REQ-028 (MUST)** 第 7 节 12 个场景在确定性验收模式下全部通过。
- **REQ-029 (MUST)** 在同一问答集上对比纯向量和 BM25+RRF 混合检索；只有主指标更好才保留混合检索，记录结果和决定。
- **REQ-030 (MUST)** 完成 README、`product.md`、`architecture.md`、`integration.md`、`api.md`、`demo-script.md`、`acceptance.md`、`baseline.md` 和验收证据索引。
- **REQ-031 (SHOULD)** 在获得授权后验证真实模型、多模态和 Live Provider；结果必须区分 passed、blocked 和 degraded。

---

## 6. 对外接口和关键数据契约

### 6.1 通用类型

- ID：非空字符串，建议 UUID；同一实体稳定不变。
- 日期：`YYYY-MM-DD`。
- 时间：UTC RFC 3339 字符串；界面可本地化显示。
- 金额：字段名以 `_fen` 结尾，JSON integer，禁止 float。
- revision：从 1 开始的正整数。
- source：`demo_fixture | backup_fixture | live | policy | model`。
- 错误体：

```json
{
  "error": {
    "code": "STALE_REVISION",
    "message": "审批对象不是当前版本",
    "details": {"requested_revision": 1, "current_revision": 2},
    "trace_id": "..."
  }
}
```

### 6.2 HTTP 错误语义

| 状态 | 使用场景 |
|---|---|
| 400 | JSON 无法解析、非法操作值、缺少 `Idempotency-Key` |
| 401 | 缺少或未知 `X-Demo-User` |
| 403 | 角色或直属关系不允许操作；非直属经理审批一律 403，不因不可见而返回 404 |
| 404 | 实体不存在或当前角色不可见 |
| 409 | revision 过期、状态不允许、重复发布、审批已处理 |
| 413 | 上传文件超过限制 |
| 415 | 不支持的文件类型或扫描型 PDF |
| 422 | 字段格式正确但业务校验失败；同一 `Idempotency-Key` 携带不同请求内容；发布时封闭规则类型缺少已确认规则 |
| 502 | Live Provider 返回无效结果 |
| 503 | 必需 Provider/模型暂时不可用且操作可重试 |

### 6.3 核心实体最小字段

| 实体 | 必需字段 |
|---|---|
| Employee | id, name, role, department, manager_id, home_city, preferences |
| PolicyVersion | version, status, effective_date, document_path, document_hash, created_at, published_at |
| PolicyRule | id, version, type, condition, value/value_fen, unit, clause, quote, locator, status, confirmed_by |
| Trip | id, employee_id, status, revision, slots, policy_version, plan, cost_breakdown, violations, approval_reasons, thread_id |
| Approval | trip_id, revision, approver_id, decision, comment, decided_at, idempotency_key |
| AuditEvent | id, trip_id, revision, ts, actor, type, payload, refs, trace_id |
| OutboxEmail | id, trip_id, revision, kind, to, cc, subject, body, created_at, unique_key |
| ContractHotel | id, city, name, price_fen, rating, distance_note, valid_until, available_date_ranges, source |
| TransportCandidate | id, origin, destination, mode(air/rail), depart_at, arrive_at, cabin, price_fen, source |

### 6.4 演示身份和权限矩阵

请求头 `X-Demo-User` 仅用于演示身份切换，不代表生产认证。

| 能力 | employee | manager | admin |
|---|---:|---:|---:|
| 创建/修改自己的 Trip | 是 | 作为员工时仅自己 | 否，除非另建员工账号 |
| 查看 Trip | 仅自己 | 自己及直属下属 | 全部 |
| 审批 | 否 | 仅当前直属下属且不能本人 | 否 |
| 按新版本重新校验 | 是，仅自己的 Trip | 是，仅直属下属的 Trip | 否 |
| 制度问答 | 是 | 是 | 是 |
| 制度上传、规则确认、发布 | 否 | 否 | 是 |
| 查看审计 | 仅自己的 Trip | 直属下属 Trip | 全部 |
| 查看 Outbox、故障注入、重置 | 否 | 否 | 是 |
| 修改偏好 | 仅自己 | 仅自己 | 否 |

### 6.5 API 语义

| 能力 | 方法和路径 | 关键输入 | 关键输出/状态变化 |
|---|---|---|---|
| 演示账号 | `GET /api/users` | 无需身份头 | 全部演示账号（id、姓名、角色、部门、直属关系），供登录页选择 |
| 创建 Trip | `POST /api/trips` | 可选初始文本 | 新 Trip，状态 `COLLECTING`，revision=1 |
| Trip 列表/详情 | `GET /api/trips`；`GET /api/trips/{id}` | 权限过滤 | 可见 Trip 摘要/完整详情 |
| 对话 | `POST /api/trips/{id}/messages` | text, expected_revision | SSE；槽位、状态、引用、方案或错误 |
| 槽位修改 | `PATCH /api/trips/{id}/slots` | expected_revision；待改字段 | 仅 `COLLECTING`（含被阻断后返回）可用；格式非法返回 422；变更前后值写入审计 |
| 提交校验 | `POST /api/trips/{id}/submit` | expected_revision；`Idempotency-Key` | D-13 的显式提交动作；槽位不完整返回 422；返回 SSE 流，状态/方案/审批卡事件由此推送；需审批且申请人无直属上级时不建审批卡，SSE 返回 `error`（code=NO_APPROVER），Trip 保持 `COLLECTING`（D-21） |
| 图片上传 | `POST /api/uploads` | PNG/JPG，≤10 MiB | 临时本地文件 ID、hash；不外传 OSS |
| 图片抽取确认 | `POST /api/trips/{id}/image-extractions`；`POST .../{extraction_id}/confirm` | upload_id；确认后的字段 | 未确认结果不修改 Trip；确认后更新槽位并审计 |
| 待办 | `GET /api/approvals?status=pending` | manager 身份 | 仅直属下属当前 revision |
| 决策 | `POST /api/trips/{id}/decision` | revision, decision, comment；`Idempotency-Key` | 状态转换或 409 |
| 重新校验 | `POST /api/trips/{id}/revalidate` | 当前 revision；`Idempotency-Key` | 按 D-12 重判并 revision+1；非 `PENDING_APPROVAL` 或旧 revision 返回 409 |
| 交付重试 | `POST /api/trips/{id}/delivery/retry` | admin；Trip 处于 `DELIVERY_PENDING` | 幂等重试交付；成功进入 `DELIVERED`，仍失败保持 `DELIVERY_PENDING` 并记录错误 |
| 审计 | `GET /api/trips/{id}/audit` | 权限过滤 | 按时间升序、不可修改的事件 |
| 制度问答 | `POST /api/policy/ask` | question, optional version | answer, citations, version |
| 上传制度 | `POST /api/policies` | Markdown/PDF ≤10 MiB、version、effective_date | draft；重复 version 返回 409 |
| 抽取规则 | `POST /api/policies/{version}/extract` | draft | candidates；重复调用覆盖前需显式确认或保持幂等 |
| 处理规则 | `PATCH /api/policies/{version}/rules/{id}` | status/修订字段 | 更新 candidate 状态并审计 |
| 发布制度 | `POST /api/policies/{version}/publish` | `Idempotency-Key` | 原子发布；未处理候选返回 409；封闭类型缺少已确认规则返回 422 |
| 偏好 | `GET/PUT/DELETE /api/users/{id}/preferences` | owner 校验 | 当前偏好 |
| Outbox | `GET /api/outbox` | admin | 两类邮件，可按 kind/trip 过滤 |
| 重置 | `POST /api/demo/reset` | `confirmation="RESET_DEMO_DATA"` | 恢复种子状态；admin only |
| 故障 | `POST /api/demo/faults` | fault type, enabled | 当前故障配置；admin only |
| Provider 健康 | `GET /api/health/providers` | admin | mode、状态、最近错误；不得泄露密钥 |

`docs/api.md` 可以调整路径和字段命名，但不得改变上述输入、权限、错误、幂等和状态语义。

### 6.6 Provider 与降级

| Provider | Demo 实现 | Live 实现 | 失败行为 |
|---|---|---|---|
| Transport | 本地交通 fixture | Kiwi MCP（SHOULD） | 方案交通标记待人工补录，`incomplete=true`，必须审批，不得自动通过 |
| Hotel | 协议酒店 fixture（含可订日期）+ 已验证备用酒店 fixture | 本地协议库 + Tavily 无价格参考（SHOULD） | 无协议酒店时从备用目录选可信候选，标 `backup_fixture` 来源并写审计；备用候选价格合规时不构成违规或审批理由（D-15）；连备用候选也无时标记待人工补录并进入审批 |
| Weather | 本地天气 fixture | Open-Meteo（SHOULD） | 省略天气并注明，不阻断规划 |
| Clock | `DEMO_TODAY` | 系统日期 | Demo 必须固定可复现 |
| Email | 本地 Outbox | 本地 Outbox | 数据库失败时进入 `DELIVERY_PENDING`，幂等重试 |
| LLM | Fake/Recorded 用于确定性测试；获授权后使用可配置真实模型 | 同左 | 返回可重试错误，不写入伪造结构化结果 |

Provider 响应必须经过 Schema 校验。交通和酒店候选 ID 必须存在于本次 Provider 响应中，模型不得新增、修改价格或伪造 ID。

### 6.7 SSE 事件

事件类型：`token`、`status`、`slots`、`citation`、`plan`、`violations`、`approval_required`、`trip_status`、`error`、`done`。

每个事件至少包含：`event_id`、`trip_id`、`revision`、`type`、`ts`、`trace_id`、`payload`。每次请求只能有一个终结事件：成功为 `done`，失败为 `error`。客户端断线重连不得重复执行业务写操作；恢复策略写入 `docs/api.md`。

### 6.8 状态机

```text
COLLECTING
  └─ 员工显式提交（槽位完整时才允许，D-13）→ VALIDATING
       ├─ 输入/时长硬阻断 → POLICY_BLOCKED ──员工修改（revision 不变）→ COLLECTING
       └─ 合法 → PLANNING
            ├─ 自动通过 → AUTO_APPROVED → DELIVERY_PENDING → DELIVERED
            └─ 需审批 → PENDING_APPROVAL
                 ├─ approve → APPROVED → DELIVERY_PENDING → DELIVERED
                 ├─ reject → REJECTED（终态）
                 └─ request_changes → revision+1 → COLLECTING
```

`DELIVERED` 和 `REJECTED` 是终态，不能修改方案。`DELIVERY_PENDING` 可重试交付但不能重新审批。制度问答独立于 Trip 状态；Trip 内展示与合规有关的引用时，必须使用该 revision 绑定的制度版本。

---

## 7. 确定性 Fixture 与端到端场景

### 7.1 固定 Provider 数据

以下为验收所需最小数据，执行者可以添加其他候选，但不得改变这些 ID、价格和验收预期。

| ID | 类型 | 路线/城市 | 日期 | 价格 | 其他 |
|---|---|---|---|---:|---|
| T-BJS-SHA-1028-A | 去程交通 | 北京→上海 | 2026-10-28 | 180000 分 | 经济舱，demo_fixture |
| T-SHA-BJS-1030-A | 返程交通 | 上海→北京 | 2026-10-30 | 170000 分 | 经济舱，demo_fixture |
| H-SHA-CONTRACT-01 | 协议酒店 | 上海 | 有效 | 48000 分/晚 | demo_fixture；未标注区间，默认全程可订 |
| T-BJS-SHA-1025-B | 去程交通 | 北京→上海 | 2026-10-25 | 65000 分 | 经济舱，demo_fixture |
| T-SHA-BJS-1026-B | 返程交通 | 上海→北京 | 2026-10-26 | 65000 分 | 经济舱，demo_fixture |
| H-SHA-CONTRACT-02 | 协议酒店 | 上海 | 有效 | 40000 分/晚 | demo_fixture；满房晚为 2026-10-28、2026-10-29；需占用任一满房晚的停留不可订，其余日期可订 |
| T-BJS-CTU-1027-A | 去程交通 | 北京→成都 | 2026-10-27 | 80000 分 | 经济舱，demo_fixture |
| T-CTU-BJS-1029-A | 返程交通 | 成都→北京 | 2026-10-29 | 80000 分 | 经济舱，demo_fixture |
| H-CTU-BACKUP-01 | 备用酒店 | 成都 | 有效 | 45000 分/晚 | backup_fixture，无合规协议酒店时使用 |
| T-BJS-SHA-1105-EARLY | 去程交通 | 北京→上海 | 2026-11-05 07:30 出发 | 65000 分 | 经济舱，demo_fixture；AC-023 早班候选 |
| T-BJS-SHA-1105-LATE | 去程交通 | 北京→上海 | 2026-11-05 14:00 出发 | 65000 分 | 经济舱，demo_fixture；AC-023 晚班候选，与早班价格、舱位相同 |
| T-SHA-BJS-1106-P | 返程交通 | 上海→北京 | 2026-11-06 18:00 出发 | 65000 分 | 经济舱，demo_fixture；AC-023 返程 |

S-04 使用额外固定候选：往返交通共 120000 分，上海协议酒店 40000 分/晚（即 `H-SHA-CONTRACT-02`，2026-10-22 可订）。

酒店候选包含 `available_date_ranges`（可订日期区间），它是 REQ-013 的过滤必要条件。占用按入住晚计算：一次停留占用 [入住日, 退房日) 的每一晚，退房日不占用；需占用任一不可订晚的候选不得参与排序。`H-SHA-CONTRACT-01` 默认全程可订。

所有交通候选必须包含 `mode`（`air` 或 `rail`）、`depart_at` 和 `arrive_at`；上表候选均为 `mode=air`，RULE-ADVANCE-001 只约束 `mode=air` 的候选。除 `T-BJS-SHA-1105-EARLY` 外，上表及执行者新增候选的出发时刻由执行者设定，但不得早于 08:00，以免偏好排序意外影响 S-01～S-12 的验收预期。

AC-023 固定输入：张三；北京→上海；2026-11-05～11-06；客户拜访；预算 250000 分；酒店使用 `H-SHA-CONTRACT-02`。未设偏好时，两个去程候选同价同舱位，代码须按执行者在 `docs/architecture.md` 中记录的稳定次级排序键选择；设置“回避早班”后必须选择 `T-BJS-SHA-1105-LATE`。总额 190000 分，`AUTO_APPROVED`。

### 7.1.1 制度文件与录制模型输出

以下文件必须随仓库交付于 `tests/fixtures/`，供确定性验收使用：

| 路径 | 内容 | 用途 |
|---|---|---|
| `policies/v1.0.md` | 星衡科技差旅制度 v1.0 全文，与 §4.1 v1.0 差异一致（机票提前 5 天、自动通过阈值 2000 元） | 种子数据（archived 版本问答与重置） |
| `policies/v1.2.md` | 星衡科技差旅制度 v1.2 全文，条款 POL-1～POL-7 与第 4.2 节一致，并至少包含 1 条不支持类型的条款（如“出租车费用实报实销”） | 种子数据、问答语料、AC-005、AC-018 |
| `policies/v1.3.md` | 与 v1.2 相同，仅员工 A 类城市住宿上限改为 45000 分/晚 | S-12、AC-024 |
| `policies/v1.2-text.pdf` | 由 v1.2 生成的文本型 PDF | AC-005 |
| `policies/scanned.pdf` | 无文本层的扫描型 PDF | AC-005（期望 415） |
| `recorded/extract-v1.2.json`、`recorded/extract-v1.3.json` | 对应制度的录制规则抽取输出，含至少 1 条 `invalid_quote` 和 1 条 `unsupported` 候选 | AC-005、S-12、AC-024 的确定性运行 |
| `recorded/` 下其他文件 | S-01～S-12 中所有模型调用（槽位抽取、追问、图片抽取、行程摘要）的录制输出 | AC-019 |

录制输出在获得 AUTH-01 后可由真实模型生成后冻结，未获授权时可手工编写；两种来源都必须在文件元数据中标注 `source`（`model` 或 `hand_written`），不得把手工编写的录制输出用作授权门控验收的证据。

### 7.2 场景

| 编号 | 固定输入与操作 | 精确预期 |
|---|---|---|
| S-01 黄金路径 | 张三；北京→上海；2026-10-28～10-30；供应商大会；预算 400000 分（期间 `H-SHA-CONTRACT-02` 满房） | 选中 `T-BJS-SHA-1028-A`、`T-SHA-BJS-1030-A`、`H-SHA-CONTRACT-01`（02 满房被过滤）；晚数2、天数3；交通350000、酒店96000、补贴30000、总额476000；无制度违规，审批理由含 `BUDGET_EXCEEDED` 与 `OVER_AUTO_APPROVAL_LIMIT`；`PENDING_APPROVAL`；李四批准后 `DELIVERED`；1 封 `trip_delivery` |
| S-02 自动通过 | 张三；北京→上海；2026-10-25～10-26；客户拜访；预算250000 | 选中 B 组交通与 `H-SHA-CONTRACT-02`（40000 < 48000，总价最低合规）；晚数1、天数2；总额190000；无违规且低于预算/阈值；`AUTO_APPROVED→DELIVERED`；无 Approval；1 封 `trip_delivery` |
| S-03 酒店超标 | 张三；北京→成都；2026-10-27～10-29；培训；预算400000；成都无协议候选，仅 `H-CTU-BACKUP-01` | 违规 `HOTEL_OVER_CAP`（RULE-HOTEL-001：员工 B 类上限40000，实际45000，超5000分/晚）；总额274000；`PENDING_APPROVAL`；来源 `backup_fixture` |
| S-04 提前不足 | 张三；北京→上海；2026-10-22～10-23；紧急会议；预算300000 | 提前2天，小于3天；总额180000；`ADVANCE_BOOKING_VIOLATION`；进入审批而非硬阻断 |
| S-05 缺失信息 | 新 Trip 只输入“下周去杭州” | 识别 destination=杭州；不把“下周”猜成具体日期；一次回复只询问 origin、start_date、end_date、purpose、declared_budget_fen，不重复询问杭州 |
| S-06 硬性阻断 | 2026-10-19～10-20，北京→上海，其他字段合法 | `POLICY_BLOCKED`；不得调用交通、酒店、天气 Provider；审计记录阻断原因 |
| S-07 驳回 | 对 S-01 当前 revision 由李四 `reject`，意见“预算过高，请改为线上参会” | `REJECTED`；无 `trip_delivery`；恰好1封 `workflow_notification`；再次审批返回409 |
| S-08 要求修改 | 对 S-01 由李四 `request_changes`；张三将预算改为500000后重新提交；李四再次批准 | revision=2；旧审批失效；总额仍476000且超过自动阈值，所以再次 `PENDING_APPROVAL`；第二次批准后交付；旧方案和两次决定均可审计 |
| S-09 交通故障 | 开启 `transport_timeout`；其余输入与 S-02 完全相同（张三；北京→上海；2026-10-25～10-26；客户拜访；预算250000） | 不崩溃；交通标为待人工补录，`incomplete=true`；成本拆解中交通显示“待补录”，`known_subtotal_fen`=60000（酒店40000+补贴20000），`total_fen`=null，不得把缺失交通按0计入后判定自动通过；审批理由包含 `PLAN_INCOMPLETE`；进入 `PENDING_APPROVAL`（对照 S-02 同输入本应自动通过）；不得出现伪造交通 ID/价格；天气、酒店照常 |
| S-10 重启恢复 | S-01 到达 `PENDING_APPROVAL` 后终止服务进程，使用同一数据库重新启动，再由李四批准 | 重启后 Trip/revision/审批待办不丢失；从审批后节点恢复；只生成1封 `trip_delivery` |
| S-11 图片入口 | 上传 fixture `tests/fixtures/invitations/shanghai_supplier_conference.png` | 抽取上海、2026-10-28～10-30、供应商大会和会议地址；判定口径：事由包含“供应商大会”即一致，地址去除空白后须同时包含“滨江大道2727号”和“星衡国际会议中心”；确认前不修改 Trip；确认后写入并审计；fixture 文件须随仓库交付 |
| S-12 制度变更 | S-01 待审批时创建并发布 v1.3；点击“按新版本重新校验” | revision+1；policy_version=v1.3；旧审批失效；48000/晚超过新上限45000，新增酒店违规；审计保留 v1.2 与 v1.3 两次结果 |

确定性验收模式使用 Fake/Recorded LLM，以便验证业务流程而不受模型随机性影响。真实模型验证单独执行，不得代替确定性验收。

---

## 8. 验收标准

### 8.1 状态和证据约定

验收状态只能是：`passed`、`failed`、`blocked`、`not_run`。

- `passed`：全部预期均满足并有证据。
- `failed`：已执行但至少一项预期不满足。
- `blocked`：因未授权、外部不可用或缺少非项目可控条件无法执行；必须记录阻塞原因。
- `not_run`：尚未执行，不得写成通过。

证据统一保存在 `artifacts/acceptance/<AC-ID>/`，并在 `docs/acceptance.md` 中索引。证据可以是命令及完整输出、测试报告、JSON、截图、录屏片段或数据库查询结果；不得仅写口头结论。

### 8.2 验收用例

| 编号 | 验证对象与前置条件 | 操作/输入 | 可观察预期与通过判定 | 验证方式与证据 |
|---|---|---|---|---|
| AC-001 | 安装与启动；一台未安装项目依赖但有 Python 3.11 和 uv 的 Windows 环境 | 把仓库全新克隆到临时目录（不带现有虚拟环境），严格按 README 执行 `uv sync` 和唯一启动命令 | 服务启动、健康接口成功、浏览器可打开工作台；步骤与 README 一致即通过 | 命令、版本、启动日志、工作台截图 |
| AC-002 | 槽位接入；重置后张三账号 | 执行 S-05，再补齐并修改预算 | 只追问指定缺失项；修改前后值进入审计；无重复追问即通过 | API/SSE 记录、审计 JSON、自动测试 |
| AC-003 | 图片入口流程；S-11 fixture 与对应录制抽取输出存在 | 上传、查看抽取结果、确认；另上传错误类型和超过 10 MiB 的文件 | 确认前 Trip 不变；确认后字段与 S-11 一致并审计；错误类型返回415、超限返回413。本项验证流程，抽取结果来自录制输出；真实识别能力见 AC-029 | 自动测试、前后 Trip JSON、截图 |
| AC-004 | 制度问答（授权门控）；v1.2 已发布；使用真实模型与真实 embedding | 运行25题黄金问答集 | 引用命中率≥90%；关键数值正确率≥85%；5个无答案题至少4个正确拒答（正确拒答=结构化输出标记无法回答且未给出任何引用，口径随题集冻结） | 评测 JSON/CSV、公式、逐题结果、模型与 embedding 名称 |
| AC-005 | 制度上传与发布；管理员账号；抽取使用 §7.1.1 录制输出；使用独立测试版本号（不占用 v1.0/v1.2/v1.3）与独立测试数据库 | 上传 MD、文本PDF、扫描PDF；抽取并处理规则；尝试提前发布 | MD/PDF生成draft；扫描PDF返回415；`invalid_quote` 候选不可确认；未处理完发布返回409；封闭类型缺已确认规则发布返回422；完成后原子发布 | API测试、数据库前后快照、审计 |
| AC-006 | 规则引擎；固定政策和边界数据 | 运行每种规则及等于上限、超1分、日期边界测试 | 代码结果与第4节一致；模型输出不能改变结论；全部用例通过 | `pytest` 报告和用例清单 |
| AC-007 | Provider候选与代码选择；固定fixture | 运行 S-01～S-04、无合规候选和伪造ID用例 | 所有选中ID存在于响应；排序符合REQ-013；伪造ID被拒绝 | 自动测试、Provider响应和plan JSON |
| AC-008 | 审批卡；S-01待审批 | 获取审批卡 | 成本明细476000、预算差76000、规则/理由、引用、来源、revision齐全 | API断言、浏览器截图 |
| AC-009 | 权限、revision和幂等；S-01待审批 | 张三审批、非直属经理审批、旧revision审批、同/不同Key重复审批 | 前两者403；旧revision和第二次不同Key为409；同Key返回相同结果且无重复副作用 | API测试、数据库计数 |
| AC-010 | 重启恢复；持久化数据库 | 完整执行 S-10，真实终止并重启进程 | 待办不丢失，正确恢复，只交付一次 | 重启前后日志、DB查询、Outbox计数 |
| AC-011 | 交付和通知 | 执行 S-01、S-07、S-08；模拟一次交付写失败后重试 | 邮件kind和数量符合场景；失败停在DELIVERY_PENDING；重试不重复 | 自动测试、Outbox表、状态时间线 |
| AC-012 | 审计完整性 | 执行 S-01、S-08、S-09、S-12 | 包含槽位、引用、规则、Provider、方案、审批、通知/交付、错误；既有事件不可编辑 | 事件类型自动断言、审计导出 |
| AC-013 | 故障降级 | 分别注入交通超时、无协议酒店、天气不可用；无协议酒店用 S-02 输入且上海不设备用酒店 | 交通超时与 S-09 一致；无协议酒店时酒店待人工补录，`total_fen`=null、`known_subtotal_fen`=150000（交通130000+补贴20000），审批理由含 `PLAN_INCOMPLETE`，进入审批，交通天气照常；天气不可用时省略并注明；行为逐项符合6.6；无崩溃、无伪造数据 | 自动测试、API结果、界面截图 |
| AC-014 | 三角色工作台 | 浏览器执行S-01和制度发布流程 | 各角色只能看到和完成权限矩阵允许的操作；黄金路径可走通 | 浏览器截图/录屏、服务端403证据 |
| AC-015 | 来源标记 | 检查S-01、S-03、问答和真实模式结果 | API、界面、审计均出现规定source；无来源数据判失败 | JSON扫描、截图、自动断言 |
| AC-016 | Demo reset | 先制造Trip、审批、偏好、故障和Outbox，再重置 | 动态数据清空，种子用户、政策、酒店和fixture恢复；非admin为403 | DB前后快照、API测试 |
| AC-017 | 问答指标定义（授权门控） | 使用冻结的25题集，与 AC-004 同一次真实运行 | 引用命中率=答案引用列表中出现 gold 条款的题数/需引用题数（出现即命中，不要求逐字一致）；关键数值正确率=数值与 gold 一致的题数/数值题数（先统一换算为“分”再比较，“500 元”=50000 分）；达到AC-004阈值 | 评测脚本、数据集hash、报告 |
| AC-018 | 规则抽取指标（授权门控）；使用真实模型 | 使用冻结v1.2全文和gold规则集；gold以§4.2的8条规则为单位，模型候选与gold允许一对多，类型、条件、分档数值（统一为分）、单位、条款全部一致才计命中 | recall=匹配gold规则数/8≥80%；无效引用拦截率=评测脚本注入的N≥5条无效引用（篡改quote构造）中被标记者/注入总数，须100%（D-17，不依赖模型自发产生）；不支持规则100%标记；precision=匹配到任一gold规则的候选数/全部候选数（重复与冗余候选计入分母），报告但不设门槛；模型自发invalid_quote数量另行报告 | gold文件、匹配明细、注入样本清单、报告、模型名称 |
| AC-019 | 12个确定性E2E场景 | Demo reset；Fake/Recorded LLM；固定时钟和fixture | S-01～S-12各运行3次，共36/36达到精确预期；任一硬约束违反即失败 | 自动场景报告、每次状态轨迹 |
| AC-020 | 黄金路径耗时（SHOULD，需 AUTH-01） | 真实模型模式，与AC-019相同机器；从员工显式提交（最后一个必填槽位已齐，D-13）到审批卡事件；运行3次 | 三次中位数≤90秒；同时报告最大值、机器信息、模型名称和计时边界。Fake/Recorded 模式下的计时不得作为本项证据 | 计时日志和环境说明 |
| AC-021 | Demo网络边界 | Demo模式运行AC-019 | 除经授权的模型域名外无外部请求；确定性Fake模式应为零外部请求 | 应用HTTP出口拦截/记录、域名清单 |
| AC-022 | 密钥与日志 | 完成实现后 | Git跟踪文件无密钥；`.env.example`仅变量名；日志无密钥值 | 密钥扫描命令、结果、抽样日志 |
| AC-023 | 偏好 | §7.1 中 AC-023 固定输入与早晚班候选；“早班”按 D-10 定义 | 张三设置“回避早班”后，下一次规划选择 `T-BJS-SHA-1105-LATE`；审计说明偏好影响排序；清除偏好后不再应用偏好，选择结果回到稳定次级排序键的结果 | 自动测试、plan/audit JSON |
| AC-024 | 制度变更 | 执行S-12 | 提示、revision、版本绑定、旧审批失效和新增违规均符合S-12 | 自动测试、前后审批卡和审计 |
| AC-025 | 检索对比（授权门控）；使用真实 embedding | 相同切分语料、同一25题集；主指标=引用条款Recall@3（gold条款出现在前3条检索引用中） | 报告两种检索的相同指标；只有主指标严格更好才保留混合检索，打平保留纯向量；并记录取舍与次要指标 | 配置、数据集hash、报告、架构决定 |
| AC-026 | 文档与演示 | 所有MUST实现完成 | 要求的9类文档齐全；README首段讲业务问题；3分钟和10分钟脚本各实际演练一次 | 文件清单、演练计时、截图/录屏 |
| AC-027 | 真实模型路径（SHOULD） | AUTH-01已授权且模型可用 | S-01真实模型执行1次，状态和金额正确；若失败不得影响确定性结果，但记录failed/blocked | 模型名、脱敏日志、场景证据 |
| AC-028 | Live Provider（SHOULD） | AUTH-02已授权 | 记录各Provider真实响应或明确degraded/blocked；Kiwi不覆盖国内路线不得伪装成功 | 脱敏响应、错误和降级报告 |
| AC-029 | 真实图片识别（授权门控）；使用真实多模态模型，本地 base64，不经 OSS | 对 S-11 fixture 执行真实抽取 | 城市、日期、事由、会议地址与 S-11 预期一致（判定口径见 S-11）；结果仍须员工确认后才写入 Trip | 模型名称、脱敏请求摘要、抽取 JSON |

授权门控验收：AC-004、AC-017、AC-018、AC-025、AC-029。其完成要求见第 14 节第 2 条。门控评测使用的题集、gold 文件与注入样本必须在首次真实运行前冻结，SHA-256 记入 `docs/acceptance.md`；冻结后不得修改，确需修改须重新冻结并记录原因（D-17）。评测顺序：先运行 AC-025 确定保留的检索配置，再以该最终配置运行 AC-004/AC-017，使指标反映实际交付配置。

### 8.3 需求—验收追踪矩阵

| 需求 | 验收 |
|---|---|
| REQ-001～REQ-002 | AC-002、AC-019 |
| REQ-003 | AC-003、AC-019(S-11)、AC-029 |
| REQ-004、REQ-026 | AC-004、AC-017 |
| REQ-005～REQ-008 | AC-005、AC-018、AC-024 |
| REQ-009 | AC-006、AC-019 |
| REQ-010～REQ-013 | AC-007、AC-013、AC-019 |
| REQ-014 | AC-023 |
| REQ-015 | AC-008 |
| REQ-016 | AC-009、AC-014 |
| REQ-017 | AC-010 |
| REQ-018 | AC-011 |
| REQ-019 | AC-024、AC-019(S-12) |
| REQ-020 | AC-012 |
| REQ-021～REQ-022 | AC-013、AC-021 |
| REQ-023 | AC-014 |
| REQ-024 | AC-015 |
| REQ-025 | AC-016 |
| REQ-027 | AC-018 |
| REQ-028 | AC-019 |
| REQ-029 | AC-025 |
| REQ-030 | AC-001、AC-022、AC-026 |
| REQ-031 | AC-020、AC-027、AC-028（SHOULD） |

任何新增 MUST 需求都必须同时新增或关联可判定的验收标准。

---

## 9. 实施建议与分阶段交付

### 阶段 P0：基线和高风险验证

完成后可以观察到：`docs/baseline.md` 记录真实环境和素材；Fake Provider 能运行；高风险假设有最小实验结论；未授权外部调用未发生。

### 阶段 M1：确定性核心和冻结契约

完成后可以观察到：规则计算和状态机离线测试通过；SQLite实体可落库；Demo fixture固定；`docs/api.md` 与第6节一致。

### 阶段 M2：最小端到端闭环

完成后可以观察到：使用最小界面完成 S-01、S-06、S-07、S-10；审批中断、进程重启恢复、两类Outbox和审计可验证。

### 阶段 M3：工作台与制度知识

完成后可以观察到：三角色视图完整；S-02～S-05、S-08通过；制度问答、偏好和来源标识可见。

### 阶段 M4：制度编译与可靠性

完成后可以观察到：管理员完成上传—抽取—确认—发布；S-09、S-11、S-12通过；故障降级不崩溃。

### 阶段 M5：评测和交付

完成后可以观察到：MUST验收全部有状态和证据；真实模型/Live按授权执行或标记blocked；文档和演示脚本可直接使用。

推荐原则：先验证高不确定性，再完成一个纵向闭环，再并行扩展；不得先堆多个Agent或页面，再补确定性核心。

---

## 10. 任务拆分与并行边界

| 任务 | 目标 | 输入 | 输出 | 前置依赖 | 对应需求/验收 | 建议修改范围 |
|---|---|---|---|---|---|---|
| TASK-001 | 核实基线和授权状态 | 本契约、目标目录、素材目录 | `docs/baseline.md`、素材清单、假设验证记录 | 无 | AC-022、REQ-030 | 只读调查；仅新增docs，不改素材 |
| TASK-002 | 建立项目骨架和共享契约 | TASK-001、§6 | uv项目、配置、SQLite迁移/初始化、`docs/api.md`、种子数据 | TASK-001 | AC-001、AC-005 | pyproject、src基础、db、docs |
| TASK-003 | 实现确定性规则核心 | §4、gold规则、fixture | 金额计算、规则判断、状态转换及单元测试 | TASK-002接口草案 | REQ-007～009；AC-006 | domain/rules/tests |
| TASK-004 | 实现Demo Provider和选择器 | §6.6、§7 | 交通/酒店/天气/时钟fixture、Schema校验、排序器 | TASK-002；与TASK-003统一类型 | REQ-010～013；AC-007、013 | providers/planning/tests/fixtures |
| TASK-005 | 实现工作流持久化和审批 | TASK-003、004、状态机 | LangGraph工作流、中断恢复、revision、幂等审批、交付 | TASK-003、TASK-004 | REQ-015～019；AC-008～011 | workflow/persistence/outbox |
| TASK-006 | 实现API和权限层 | §6、TASK-005服务接口 | FastAPI、SSE、错误体、权限矩阵、上传 | TASK-002；审批相关依赖TASK-005 | REQ-001～003、016、023；AC-002、003、009 | api/auth-demo/uploads |
| TASK-007 | 实现三角色工作台 | API契约、场景 | 原生SPA、三个角色视图、来源与审计展示 | TASK-006稳定接口；可先用fixture并行 | REQ-023～025；AC-014～016 | static/templates/frontend |
| TASK-008 | 实现制度问答和编译 | v1.2制度、规则Schema | 文档导入、引用定位、RAG、候选规则审核和发布 | TASK-002；与TASK-003统一规则Schema | REQ-004～008、026～027；AC-004、005、017、018 | policy/rag/evals |
| TASK-009 | 实现偏好、图片和制度变更 | TASK-005～008 | 偏好、图片确认、重新校验 | TASK-005、006、008 | REQ-003、014、019；AC-003、023、024、029 | preferences/vision/revalidation |
| TASK-010 | 可靠性和故障注入 | 完整Demo闭环 | 故障控制、网络边界、交付重试、审计完整性 | TASK-005～009 | REQ-020～022；AC-010～013、021 | reliability/audit/tests |
| TASK-011 | 评测与验收自动化 | §7、§8 | 25题集、gold规则、§7.1.1录制输出、12场景runner、报告和证据索引；题集与gold在首次门控运行前冻结并记录SHA-256 | 所有功能任务；授权门控项的真实运行需AUTH-01 | REQ-026～029；AC-017～025 | evals/tests/artifacts |
| TASK-012 | 文档与演示交付 | 实际实现和验收结果 | 9类文档、3/10分钟脚本、最终限制清单 | TASK-011 | REQ-030；AC-026 | README/docs |
| TASK-013 | 真实集成验证（SHOULD） | AUTH-01/02、本地已通过版本 | 真实模型和Live报告 | Demo MUST全部稳定；相关授权已取得 | REQ-031；AC-020、027、028 | integrations/artifacts，不改变核心语义 |

### 10.1 必须串行或先统一接口的部分

- TASK-001 → TASK-002 必须串行。
- 规则类型、金额类型、Provider候选Schema必须在 TASK-003/004 并行前冻结。
- TASK-005 必须建立在确定性规则和Provider类型之上。
- TASK-011 不能用未冻结的场景和指标。

### 10.2 可以并行的部分

- TASK-003 与 TASK-004 在共享类型冻结后可并行。
- TASK-007 可在 API 契约冻结后使用 fixture 与 TASK-005/008 并行，但最终必须连真实本地 API 回归。
- TASK-008 与工作台开发可并行。
- 文档可以随阶段增量维护，但 TASK-012 的最终内容必须来自实际结果，不得预写未验证成功。

---

## 11. 风险、假设和待决事项

| 编号 | 风险/假设 | 发现方式与时点 | 处理 | 是否阻塞 | 决定权 |
|---|---|---|---|---|---|
| RISK-001 | 可配置真实模型的结构化输出不稳定 | TASK-001，用冻结10样例测试 | 更换已授权模型、收紧Schema、重试；核心测试继续使用Fake/Recorded | 不阻塞本地核心；阻塞AC-027 | 实施负责人；产生新费用时项目所有者 |
| RISK-002 | 多模态模型不能直接接收本地base64图片 | 获得AUTH-01后在TASK-001最小验证 | 保留上传和人工确认UI（AC-003不受影响）；若无可用模型则AC-029标blocked，但不得使用OSS绕过 | 可能阻塞AC-029 | 项目所有者决定是否授权其他方案 |
| RISK-003 | LangGraph中断、异步SQLite和SSE组合无法跨进程恢复 | M1前做最小技术尖峰 | 调整持久化实现；可换同步边界或工作流封装，但必须满足AC-010 | 阻塞M2 | 实施负责人 |
| RISK-004 | 中文问答达不到原阈值 | M3首轮真实评测（需AUTH-01） | 调整切分、embedding、检索；再对比混合检索；不得为达标改用本地torch模型 | 阻塞AC-004，不阻塞规则引擎 | 实施负责人 |
| RISK-005 | PDF文本提取后引用无法逐字匹配 | 制度上传测试 | 使用§4.8规范化和定位；扫描PDF明确拒绝 | 阻塞制度发布用例 | 实施负责人 |
| RISK-006 | Kiwi MCP不可用或不覆盖国内航线 | 仅在AUTH-02后TASK-013验证 | 标记degraded/blocked，保留Demo Provider；不得伪装成功 | 不阻塞MUST | 项目所有者接受记录 |
| RISK-007 | Windows依赖安装失败 | TASK-001/002 | 优先纯Python轻量依赖；记录版本调整；不引入与需求无关的torch | 阻塞AC-001 | 实施负责人 |
| RISK-008 | 项目范围过大但无完成日期 | 每个里程碑结束检查 | MUST优先，Live最后；未经所有者批准不得删减MUST | 可能影响工期 | 项目所有者决定范围/日期 |
| RISK-009 | API调用产生未预期费用 | TASK-001核实授权 | 未授权用Fake；禁止批量调用和购买资源 | 阻塞真实集成 | 项目所有者 |
| RISK-010 | Demo身份头被误认为生产认证 | 文档和UI检查 | 明确标注“仅演示”；integration文档说明生产接入需替换 | 不阻塞Demo | 实施负责人 |
| RISK-011 | 并发审批或重试导致重复邮件 | AC-009/011并发和重试测试 | revision锁、唯一约束、事务和幂等键 | 阻塞交付 | 实施负责人 |

必须在开工初期解决：RISK-003、RISK-007，以及 AUTH-01/02 的状态记录。RISK-006 可以边实施边验证，因为 Live 不属于 MUST。

---

## 12. 硬约束

- **HC-01** 金额、日期、晚数、阈值、规则结果和审批判定只能由代码计算。
- **HC-02** 每个违规必须追溯到当前 revision 绑定的已发布、已确认规则和原文。
- **HC-03** 交通和酒店价格/班次只能来自通过Schema校验的Provider响应；模型不得新增或修改。
- **HC-04** `trip_delivery` 只能在 `APPROVED` 或 `AUTO_APPROVED` 后由固定工作流节点创建；不得暴露给自由决策Agent。
- **HC-05** 服务端必须校验当前直属经理、禁止本人审批，并校验revision与幂等键。
- **HC-06** Demo模式除获授权的模型API外不得访问外部网络；Fake验收模式不得访问任何外部网络。
- **HC-07** 仓库不提交密钥；日志、证据和错误不得包含密钥值。
- **HC-08** 素材目录只读；不得覆盖目标目录中无关的用户文件。
- **HC-09** Mock/fixture必须标注来源，不能宣称真实集成通过。
- **HC-10** 未经项目所有者明确授权，不得发布、部署公网、购买资源或向外部系统写入数据。
- **HC-11** 审计事件追加写；除管理员明确执行Demo reset外不得修改或删除既有事件。
- **HC-12** 核心写操作必须具有事务或幂等保护，不得因重试产生重复审批、重复发布或重复邮件。

任何 HC 违反都使相关验收直接失败，不能用总体通过率抵消。

---

## 13. 执行 AI 自主决定区

执行 AI可以自主决定以下事项，但重要取舍必须记录在 `docs/architecture.md`：

- 代码目录、模块拆分、类和函数命名。
- LangGraph图结构，以及使用 `interrupt()`、子图或其他兼容机制。
- 专家能力使用 Agent、普通链或确定性服务的具体组合。
- Prompt、结构化输出库、重试策略。
- 前端视觉风格和不改变业务语义的交互细节。
- SQLite访问层、迁移工具、测试组织、日志格式。
- 向量库、embedding、切分参数和检索实现；必须通过AC-025决定是否保留混合检索。
- 新增轻量依赖；必须说明用途并写入`pyproject.toml`，不得无需求引入torch等重型依赖。
- API路径和字段的局部命名；不得改变第6节定义的类型、权限、错误、状态和幂等语义。

执行 AI无权自行改变：MUST范围、业务规则、验收阈值、外部费用授权、发布权限、真实数据边界和硬约束。

---

## 14. 全局完成定义

项目只有同时满足以下条件才能标记“Demo主交付完成”：

1. 所有 MUST 需求均有实现和至少一个 `passed` 验收；不存在 `failed` 或 `not_run` 的 MUST 验收。例外：仅由授权门控验收覆盖的 REQ-026、REQ-027、REQ-029，在 AUTH-01 未授权时，以交付冻结的数据集、gold 文件和可运行评测脚本作为实现证据（脚本可用 Fake 跑通流程，但不得产出或报告指标结论）。
2. 除授权门控验收和 SHOULD 验收外，AC-001～AC-026 全部 `passed`。授权门控验收（AC-004、AC-017、AC-018、AC-025、AC-029）按 AUTH-01 状态判定：
   - AUTH-01 已授权：必须 `passed`；真实运行后未达阈值为 `failed`，不得以 `blocked` 规避。仅当模型能力本身不可用（如 RISK-002）时可为 `blocked`，并须项目所有者确认。
   - AUTH-01 未授权：可为 `blocked`，此时交付状态只能写为“Demo 主交付完成（模型评测待授权）”，README 和 `docs/acceptance.md` 不得出现这些指标的数值或“已通过”字样。
   - AC-020、AC-027～AC-028 为 SHOULD，可以是 `passed`、`blocked` 或有理由的 `degraded`，但不能虚报。
3. S-01～S-12 确定性模式达到36/36，且没有硬约束违反。
4. 规则、金额、权限、状态机、持久化恢复、幂等和交付均有自动测试证据。
5. 黄金路径至少在浏览器中完整执行一次并保留证据。
6. README中的安装、启动和演示步骤由实施负责人实际执行过。
7. 所有验收状态、命令、报告、截图和已知限制汇总在 `docs/acceptance.md`。
8. 仓库无密钥；`.env.example`完整；素材目录未被修改。
9. 提供实际可用的运行方法、Demo reset方法、测试方法和演示账号说明。
10. 已知限制明确区分：未验证、外部阻塞、降级通过和已通过。

### 14.1 允许使用模拟的验收

- 单元测试、确定性E2E、故障注入、Demo交通/酒店/天气：允许并要求使用本地fixture。
- 核心状态机和规则计算：可使用Fake LLM，但不能Fake规则结果。
- AC-003、AC-005、AC-019、AC-024 中的模型调用：使用 §7.1.1 的录制输出。
- 不允许使用模拟的：授权门控验收和 AC-020（D-08）。

### 14.2 必须真实验证的部分

- SQLite跨进程重启恢复。
- 浏览器三角色黄金路径。
- 文件上传、制度发布、权限403/409、Outbox唯一约束。
- 安装和启动命令。
- 密钥扫描和Demo网络边界。
- 授权门控验收（需AUTH-01，完成要求见本节第 2 条）。

Live Provider、真实模型黄金路径和真实模型耗时属于SHOULD，只有获得授权后执行；未授权必须标记`blocked`而不是`passed`。

---

## 15. 交接摘要

### 15.1 方案版本与适用基线

- 方案版本：v1.1，适用于 `C:\Users\22316\Desktop\1\lc-course\travel-compliance-desk\`。
- 本文件独立于v1.0，实施时以本文件为业务和验收依据。
- 环境事实继承自v1.0调查，但必须由TASK-001重新核实并记录。

### 15.2 执行前必须解决的问题

1. 记录AUTH-01和AUTH-02是否获批，以及AUTH-01的费用上限；未获批不影响本地MUST工作，但禁止真实调用，授权门控验收按第14节第2条处理。
2. 核实目标目录Git和现有文件状态，防止覆盖用户工作。
3. 验证LangGraph中断恢复可行性；失败时在M2前调整持久化方案。
4. 冻结规则Schema、金额类型和Provider候选Schema后再并行开发。

### 15.3 可以直接开工的任务

- TASK-001：基线与高风险调查。
- TASK-002中的本地项目骨架、配置、数据库和API契约。
- TASK-003、TASK-004的类型设计与本地测试准备。
- 所有不访问外部服务、不产生费用、不修改素材的文档和fixture工作。

### 15.4 留给执行 AI 自主决定的事项

第13节列出的代码组织、具体LangGraph结构、Prompt、前端视觉、依赖选择和测试组织；执行者应以满足可观察验收为目标，不为展示技术而增加无需求复杂度。

---

## 16. 变更记录

| 版本 | 日期 | 变更 | 原因 |
|---|---|---|---|
| v1.0 | 2026-10-19 | 原始锁定规划 | 初版 |
| v1.1 | 2026-10-19 | 统一MUST/SHOULD语义；Live改为非阻塞；拆分Outbox类型；修复酒店和图片降级；补充精确fixture、接口错误/权限/幂等、需求验收矩阵、任务拆分、风险、全局完成定义和交接摘要 | 降低执行AI猜测空间并满足项目级实施契约规范 |
| v1.1 正式稿 | 2026-10-19 | 新增D-08～D-10；第14节按AUTH-01状态判定授权门控验收（AC-004/017/018/025/029）；AC-003拆分，真实图片识别移至新增AC-029；AC-020改为真实模型模式并降为SHOULD；S-09固定为S-02输入；新增AC-023早晚班fixture；新增§7.1.1制度文件与录制输出；明确POLICY_BLOCKED不增加revision；更新F-04 Git状态；AUTH-01按DashScope、50元上限批准并要求费用台账；`contract.md` v1.0标记为已取代 | v1.1草稿评审：消除完成定义与授权的冲突、补齐确定性验收所需材料和边界规则 |
| v1.1 正式稿（第二批修订） | 2026-10-19 | 新增D-11～D-18：拆分制度违规与审批理由；定义重新校验语义与权限并新增 revalidate 接口；槽位完整后须显式提交才校验；区分格式非法（追问）与语义非法（阻断）；备用酒店合规不构成违规；effective_date 仅记录；门控指标口径定死（AC-017 引用命中与分换算、AC-018 gold 8条粒度与注入式无效引用拦截、AC-025 主指标 Recall@3）并要求题集/gold 冻结加 SHA-256；H-SHA-CONTRACT-02 在 2026-10-28～10-30 满房以修复 S-01/S-12 与 REQ-013 的选择冲突；酒店候选增加 available_date_ranges；交通候选增加 mode 字段；total_fen=null 与 known_subtotal_fen 口径；新增交付重试接口与启动自动重试；发布前置校验（7类规则每类≥1条confirmed，否则422）与数值修改理由；幂等键缺失400/内容不一致422；GET /api/users 免身份头；新增赵六账号与 policies/v1.0.md；S-01/S-02/S-03/S-09、AC-005/AC-013 措辞同步 | 执行者可执行性复核：S-01/S-12 在 REQ-013 下必选 40000 分/晚酒店导致验收必败；多项业务语义与门控指标未定义，真实运行后无法判定 |
| v1.1 正式稿（第三批修订） | 2026-10-19 | 新增 D-19～D-21：submit/PATCH slots 接口、偏好提交时快照合并（personal_snapshot/trip_override）、无直属上级需审批行程返回 NO_APPROVER；D-18 扩展到酒店缺失并规定已知小计超预算仍标 BUDGET_EXCEEDED；revision+1 时点明确为 request_changes 决定作出时；酒店占用按入住晚计（退房日不占用）；违规代码补 CABIN_CLASS_VIOLATION；AC-004 正确拒答口径；评测顺序 AC-025→AC-004/017；AC-001 改为全新克隆执行；S-11/AC-029 图片判定口径（事由含“供应商大会”；地址去空白后含“滨江大道2727号”与“星衡国际会议中心”） | 执行会话可执行性提问第二轮：D-13 缺少提交接口、双重偏好未定、经理自差无人审批 |
