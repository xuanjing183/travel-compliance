# API 契约（Travel Compliance Desk）

> 依据：`docs/contract-v1.1.md` §6（md5 `bedf5f94caf725d1a281a7b0967ce156`）。本文件只细化路径、字段命名和返回结构，**不改变** §6.5 规定的输入、权限、错误、幂等和状态语义。
> 状态标记：`已实现` = 代码与自动测试已存在；`契约` = 已冻结、待实现。实现进度以本列为准，不以描述为准。

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 基础路径 | `/api`；工作台 SPA 由 `/` 托管，静态资源在 `/static/` |
| 身份 | 请求头 `X-Demo-User: <employee_id>`，仅用于演示身份切换（§6.4），不是生产认证。`GET /api/users`、`GET /api/health` 无需此头 |
| 编码 | 请求/响应 `application/json; charset=utf-8`；上传为 `multipart/form-data`；流式接口为 `text/event-stream` |
| ID | 字符串；Trip/Approval/AuditEvent/Outbox 用 UUID4，演示账号、规则、Provider 候选用稳定可读 ID |
| 日期 / 时间 | 日期 `YYYY-MM-DD`；时间为 UTC RFC 3339（如 `2026-10-20T01:30:00Z`）。“出发日期”“早班”按出发地当地时间（Demo 统一 UTC+8）判断 |
| 金额 | 字段名以 `_fen` 结尾，JSON integer（人民币分），禁止 float；界面显示两位小数 |
| source | `demo_fixture` \| `backup_fixture` \| `live` \| `policy` \| `model`。偏好来源单独用 `preference_source`（`personal_snapshot` \| `trip_override` \| `none`），不复用 source 枚举 |
| Trace | 每个响应带 `X-Trace-Id` 头。客户端可传入 `X-Trace-Id`，仅当匹配 `^[A-Za-z0-9-]{1,64}$` 时沿用，否则服务端生成。同一请求的错误体、SSE 事件和审计事件使用同一 trace_id |

### 1.1 错误体（已实现）

所有非 2xx 响应（含框架层 404/405、请求体校验失败和未处理异常）统一为：

```json
{"error": {"code": "STALE_REVISION", "message": "审批对象不是当前版本",
           "details": {"requested_revision": 1, "current_revision": 2}, "trace_id": "…"}}
```

500 响应只返回 `INTERNAL_ERROR`，不回显异常文本（避免泄露 Provider 载荷或密钥）。

### 1.2 HTTP 状态与错误码

| 状态 | code（示例） | 场景 |
|---|---|---|
| 400 | `INVALID_JSON`、`IDEMPOTENCY_KEY_REQUIRED`、`INVALID_DECISION`、`UNKNOWN_FAULT`、`INVALID_RULE_STATUS` | JSON 无法解析；缺少 `Idempotency-Key`；非法操作值 |
| 401 | `UNAUTHENTICATED`、`UNKNOWN_DEMO_USER` | 缺少或未知 `X-Demo-User` |
| 403 | `FORBIDDEN`、`NOT_DIRECT_MANAGER`、`SELF_APPROVAL` | 角色或直属关系不允许。**非直属经理审批一律 403，不因不可见返回 404** |
| 404 | `NOT_FOUND`、`TRIP_NOT_FOUND`、`UPLOAD_NOT_FOUND`、`EXTRACTION_NOT_FOUND`、`ITINERARY_NOT_FOUND`、`POLICY_VERSION_NOT_FOUND`、`RULE_NOT_FOUND` | 实体不存在，或当前角色不可见（审批以外的读取/修改）；上传文件只对上传者本人可见；draft 制度版本只对 admin 可见 |
| 409 | `STALE_REVISION`、`INVALID_STATE`、`ALREADY_DECIDED`、`VERSION_EXISTS`、`UNPROCESSED_CANDIDATES`、`EXTRACTION_NOT_PENDING`、`POLICY_NOT_DRAFT`、`CONCURRENT_MODIFICATION` | revision 过期（details 带 `current_revision`）、状态不允许、审批已处理、重复版本、未处理候选（details 带 `rule_ids`）、图片识别结果已确认、制度版本已非 draft；`CONCURRENT_MODIFICATION` 为条件更新落空的兜底（同一 Trip 的写请求已串行化，正常不会出现） |
| 413 | `PAYLOAD_TOO_LARGE` | 上传超过 10 MiB（图片与制度文件同一上限） |
| 415 | `UNSUPPORTED_MEDIA_TYPE`、`SCANNED_PDF` | 不支持的文件类型（含加密或无法解析的 PDF）；无文本层的扫描型 PDF |
| 422 | `VALIDATION_ERROR`、`INVALID_SLOT`、`SLOTS_INCOMPLETE`、`COMMENT_REQUIRED`、`IDEMPOTENCY_KEY_REUSED`、`QUOTE_INVALID`、`MODIFICATION_REASON_REQUIRED`、`UNSUPPORTED_TYPE`、`INVALID_RULE_SHAPE`、`MISSING_RULE_TYPES`、`RULE_SET_INVALID`、`EMPTY_DOCUMENT`、`CONFIRMATION_REQUIRED` | 字段格式正确但业务校验失败；同 Key 不同内容；封闭类型缺少已确认规则（`MISSING_RULE_TYPES`，details 带 `missing_types`、`problems`）；已确认规则集冲突/档位未覆盖等（`RULE_SET_INVALID`，details 带 `problems`）；规则 value/condition 不符合该类型结构（`INVALID_RULE_SHAPE`）；空 Markdown（`EMPTY_DOCUMENT`） |
| 502 | `PROVIDER_INVALID_RESPONSE` | Live Provider 返回无效结果 |
| 503 | `MODEL_UNAVAILABLE`、`PROVIDER_UNAVAILABLE` | 必需模型/Provider 暂时不可用且可重试（details 带 `retryable: true`） |

### 1.3 校验顺序

同一请求同时触发多种错误时按以下顺序返回第一个：
`401 身份` → `400 缺 Idempotency-Key / JSON 无法解析 / 非法操作值` → `422 字段格式` → `404 实体不存在或不可见` → `403 角色/直属关系/本人` → `幂等重放或 422 IDEMPOTENCY_KEY_REUSED` → `409 revision/状态` → `422 业务校验`。

上传接口（#9、#20）特例：413/415 属于“文件本身”的格式错误，位于 422 字段格式之后、404/403 之前，即 employee 上传扫描 PDF 到 #20 得到 415 而不是 403（IMP-74）。

审批接口特例：经理对 Trip 的“可见性”不作为 404 理由；只要 Trip 存在，非直属经理一律 403。三种 403 的先后为：非 manager 角色 `FORBIDDEN` → 审批本人申请 `SELF_APPROVAL` → 不是申请人的直属经理 `NOT_DIRECT_MANAGER`（IMP-42）。

审批接口的 409 中，若当前 revision 的审批卡已被处理，返回 `ALREADY_DECIDED`（details 带 `approval_id`、`decision`、`status`），优先于泛化的 `INVALID_STATE`（IMP-33）。

### 1.4 幂等（Idempotency-Key）

适用：`POST /trips/{id}/submit`、`POST /trips/{id}/decision`、`POST /trips/{id}/revalidate`、`POST /policies/{version}/publish`（必填，缺失 400）；`POST /trips/{id}/messages`（可选，工作台总是发送）。

- 作用域 = `方法 + 路由模板 + 路径参数 + actor`；请求指纹 = 规范化 JSON 请求体（键排序）的 SHA-256。
- 首次请求在**同一数据库事务**内执行业务写入并保存 `(scope, key) → 指纹, 状态码, 响应体`。
- 同 Key 同指纹：直接返回首次保存的状态码和响应体，不重复任何副作用（不写审计、不写 Outbox、不恢复工作流）。
- 同 Key 不同指纹：422 `IDEMPOTENCY_KEY_REUSED`。
- 不同 Key 对已处理 revision 再决策：409 `ALREADY_DECIDED`（这是业务状态校验，与 Key 无关）。
- 首次请求以 4xx 失败时**不保存** Key（客户端修正后可重用同一 Key）；以 2xx 完成或 SSE 已开始推流后保存。
- SSE 接口的重放：返回一个只含 `trip_status`（当前快照，`payload.replayed=true`）和 `done` 的短流，不重新执行业务。
- 对话（#6）的 Key 可选：提供时与本轮槽位写入、`message_processed` 审计在同一事务保存；模型抽取失败（流内 `error`）时不保存，同一 Key 可重试。不带 Key 的重复发送会再执行一轮抽取（槽位写入本身按值比较，相同值不产生变更）。

### 1.5 SSE（§6.7）

事件格式（每个事件一帧）：

```text
id: <event_id>
event: <type>
data: {"event_id":"…","trip_id":"…","revision":1,"type":"plan","ts":"…Z","trace_id":"…","payload":{…}}
```

| type | payload |
|---|---|
| `status` | `{"stage": "extracting"\|"validating"\|"planning"\|"evaluating"\|"delivering", "message": "…"}` 进度提示 |
| `token` | `{"text": "…", "generator": "template"}` 助手回复。追问/确认文本由代码按槽位状态模板生成（IMP-43），不是模型输出；模型只负责抽取 |
| `slots` | `{"slots": {…}, "missing_fields": […], "invalid_fields": [{field, code, message, value?}], "changes": [{field, before, after}], "ask": "…", "generator": "fake"\|"recorded:model"\|"<模型名>", "source": "model"}`。`invalid_fields` 含本轮被拒绝、未写入的抽取值（带 `value`），会在 `ask` 中重新追问 |
| `citation` | `{"version", "clause", "quote", "locator", "source": "policy"}` |
| `plan` | Plan 对象（§2.4）+ `cost_breakdown` |
| `violations` | `{"violations": […], "approval_reasons": […], "blocks": […]}` |
| `approval_required` | 审批卡（§2.6） |
| `trip_status` | `{"status", "revision", "policy_version", "previous_status"}`；重放时另带 `"replayed": true` |
| `error` | `{"code", "message", "details", "retryable"}` — 终结事件 |
| `done` | `{"status", "revision"}` — 终结事件 |

- 每个流**恰好一个**终结事件（`done` 或 `error`），之后服务端关闭连接。
- 提交流对每一次实际状态转换发出 `trip_status`（含 `DELIVERY_PENDING`），唯一例外是 `VALIDATING → PLANNING`，由 `status(planning)` 表示；`approval_required` 之后紧跟 `trip_status(PENDING_APPROVAL)`（IMP-35）。
- 对话流（#6）：`status(extracting)`→`slots`→[`trip_status(COLLECTING)`，仅当本轮修改使 `POLICY_BLOCKED` 回到 `COLLECTING`]→`token`→`done`；抽取失败时 `status`→`error` `MODEL_UNAVAILABLE`（details 只含 `error_type`，Trip 不变）。
- 提交流内的终结错误：`NO_APPROVER`（D-21，`retryable=false`）；`WORKFLOW_FAILED`（意外失败，Trip 已退回 `COLLECTING`，`retryable=true`，details 只含 `error_type`）。
- 请求级前置错误（401/403/404/409/422 等）在开始推流前以普通 JSON 错误体返回，不进入 SSE。
- **断线恢复策略**：服务端不支持 `Last-Event-ID` 续传；业务写入不依赖连接存活（工作流状态由 LangGraph checkpoint 和业务库持久化）。客户端断线后：①调用 `GET /api/trips/{id}` 获取权威状态；②如需重试，使用**同一个** `Idempotency-Key` 重发，服务端按 §1.4 重放而不重复执行。工作台不得用新 Key 自动重试写操作。

## 2. 数据结构

### 2.1 Employee（已实现）

```json
{"id": "u-zhangsan", "name": "张三", "role": "employee", "department": "销售部",
 "manager_id": "u-lisi", "home_city": "北京", "preferences": {}}
```

### 2.2 Slots

| 字段 | 类型 | 必填 | 格式非法（留在 COLLECTING，追问/422） | 语义非法（提交后 POLICY_BLOCKED） |
|---|---|---|---|---|
| `origin` | string | 是 | 空白 | — |
| `destination` | string | 是 | 空白 | — |
| `start_date` | `YYYY-MM-DD` | 是 | 无法解析 | 早于 `DEMO_TODAY` |
| `end_date` | `YYYY-MM-DD` | 是 | 无法解析 | 早于 start_date；天数 > 15 |
| `purpose` | string | 是 | 空白 | — |
| `declared_budget_fen` | integer | 是 | 非整数、≤ 0 | — |
| `meeting_address` | string \| null | 否 | — | — |
| `preferences` | Preferences \| null | 否 | 未知键、类型错误 | — |

`missing_fields` 为必填且为 null 的字段（顺序固定为上表顺序）。`invalid_fields` 元素为 `{field, code, message}`，code ∈ `BLANK`、`INVALID_DATE`、`NOT_INTEGER`、`NOT_POSITIVE`、`NOT_TEXT`、`INVALID_PREFERENCES`、`UNKNOWN_FIELD`。城市名写入时去掉末尾“市”（`上海市`→`上海`）。`origin` 不由 `home_city` 自动填充，工作台可把 home_city 作为建议值展示。

### 2.3 Preferences

封闭键集合（未知键 422）：

```json
{"avoid_early_departure": true, "notes": "靠窗"}
```

- `avoid_early_departure`：bool，早班 = 当地时间 08:00 之前出发（不含 08:00，D-10）。
- `notes`：≤ 200 字自由文本，只供行程模块参考，不参与候选排序。

### 2.4 Trip

```json
{
  "id": "…", "employee_id": "u-zhangsan", "status": "PENDING_APPROVAL", "revision": 1,
  "slots": {…}, "missing_fields": [], "invalid_fields": [],
  "policy_version": "v1.2",
  "preference_source": "personal_snapshot", "preferences_used": {"avoid_early_departure": false},
  "plan": {…} | null, "cost_breakdown": {…} | null,
  "violations": [], "approval_reasons": [], "blocks": [],
  "current_approval": {…} | null,
  "policy_change_notice": {"bound_version": "v1.2", "published_version": "v1.3"} | null,
  "delivery": {"error": null, "attempts": 0, "itinerary_url": null},
  "thread_id": "…:rev-1:run-1a2b3c4d", "created_at": "…", "updated_at": "…"
}
```

- `thread_id`：最近一次提交运行的 LangGraph 线程，每次提交新建（IMP-28）；创建后、首次提交前为占位值 `<trip_id>:rev-1`，不对应任何 checkpoint。
- `delivery.error`：只含异常类型与固定提示文案，不含异常文本（IMP-34）。
- `policy_change_notice`：仅当状态为 `PENDING_APPROVAL` 且当前发布版本 ≠ `policy_version` 时非 null（REQ-019 提示）。
- 列表接口返回摘要：`id, employee_id, employee_name, status, revision, origin, destination, start_date, end_date, total_fen, known_subtotal_fen, policy_version, updated_at`。

**Plan**

```json
{
  "revision": 1, "policy_version": "v1.2", "incomplete": false, "missing": [],
  "transport": {
    "status": "selected" | "manual_required",
    "outbound": TransportCandidate | null, "return": TransportCandidate | null,
    "exception_legs": [] | ["outbound", "return"],
    "note": null | "去程：交通 Provider 超时，待人工补录；…"
  },
  "hotel": {
    "status": "selected" | "manual_required" | "not_needed",
    "candidate": ContractHotel | null, "nights": 2, "exception": false,
    "note": null | "无合规可订协议酒店，选用已验证备用目录候选（backup_fixture）"
  },
  "weather": {"status": "ok" | "unavailable", "days": [{"date", "summary", "temp_low_c", "temp_high_c", "source"}], "note": null},
  "itinerary": [{"date": "2026-10-28", "items": [{"time": "09:30" | null, "title": "…", "source": "demo_fixture" | "backup_fixture" | "model" | null}]}],
  "ranking": {"preference_applied": true, "preference_affected": false, "considered": {"outbound": ["T-…"], "return": [...], "hotel": [...]}, "filtered_out": [{"id": "H-SHA-CONTRACT-02", "reason": "NOT_AVAILABLE_ON_NIGHTS", "nights": ["2026-10-28", "2026-10-29"]}]},
  "summary": {"status": "ok" | "unavailable", "text": "…" | null, "source": "model", "generator": "fake" | "recorded:model" | "recorded:hand_written" | "<模型名>", "note": null | "行程摘要不可用：…"}
}
```

- `summary`：模型撰写的行程摘要段落（§4.7 中模型唯一可写的部分），由工作流在规划后追加（IMP-36）。只含文字；空、超过 400 字或含金额的输出被丢弃，`status="unavailable"`，方案其余部分不受影响（IMP-31）。`generator="fake"` 表示确定性测试替身，不是模型输出。
`TransportCandidate`、`ContractHotel` 字段与 §6.3 相同（ContractHotel 另带 `kind: contract | backup`），候选必须与本次 Provider 响应中的同 ID 候选完全一致（含价格），否则拒绝。

- `exception_legs`：该程无合规候选，选了排序第一的不合规候选（IMP-22）；`hotel.exception`：无住宿上限内的可订候选，选了超标最少者。违规本身由 `violations` 给出。
- `hotel.status = not_needed`：当天往返（0 晚），不查询酒店，`hotel_fen = 0`（IMP-15/25）。
- `considered`：通过过滤后的候选，按排序先后；`filtered_out`：因不可订晚被排除的酒店及其不可订晚。
- 行程条目 `time = null` 表示无固定时刻（不编造）；`source = null` 表示来自申请人填写的槽位（IMP-26）。
- `preference_applied`：本次启用了“回避早班”；`preference_affected`：启用偏好使选择结果发生变化。

**CostBreakdown**（D-18）

```json
{"nights": 2, "days": 3, "destination_tier": "A",
 "transport_fen": 350000, "outbound_fen": 180000, "return_fen": 170000,
 "hotel_per_night_fen": 48000, "hotel_fen": 96000,
 "meal_per_day_fen": 10000, "meal_fen": 30000,
 "total_fen": 476000, "known_subtotal_fen": 476000,
 "declared_budget_fen": 400000, "budget_delta_fen": 76000,
 "missing_components": []}
```

- 交通（任一程）或酒店待补录时，对应 `*_fen` 为 null，`missing_components` 列出 `transport`/`hotel`，`total_fen` 为 null，`known_subtotal_fen` 为所有已定价部分之和（单独定价的一程也计入）。当天往返（0 晚）`hotel_fen=0`、`hotel_per_night_fen=null`，不算缺失。
- `budget_delta_fen = total_fen − declared_budget_fen`（正数表示超出）；`total_fen` 为 null 时 `budget_delta_fen` 为 null，但若 `known_subtotal_fen` 已超预算仍产生 `BUDGET_EXCEEDED`（保守下界）。

**Violation**（制度违规，HC-02 可追溯）

```json
{"code": "HOTEL_OVER_CAP", "rule_id": "RULE-HOTEL-001", "policy_version": "v1.2",
 "clause": "POL-2", "quote": "员工住宿上限：…", "locator": "line:12",
 "actual_value": 45000, "limit_value": 40000, "unit": "fen_per_night",
 "subject": "H-CTU-BACKUP-01", "source": "policy", "message": "…"}
```

`code` ∈ `HOTEL_OVER_CAP`、`ADVANCE_BOOKING_VIOLATION`、`CABIN_CLASS_VIOLATION`。

**ApprovalReason**（非违规，不携带 clause/quote）

```json
{"code": "OVER_AUTO_APPROVAL_LIMIT", "actual_value": 476000, "limit_value": 300000, "unit": "fen",
 "lower_bound": false, "missing": [],
 "basis": {"rule_id": "RULE-AUTO-001", "policy_version": "v1.2", "clause_ref": "POL-6"}, "message": "…"}
```

`code` ∈ `BUDGET_EXCEEDED`（limit = 申报预算；方案不完整时 `actual_value` 为已知下界、`lower_bound=true`）、`OVER_AUTO_APPROVAL_LIMIT`（仅 `total_fen` 非 null 时判定）、`PLAN_INCOMPLETE`（`actual_value`/`limit_value` 为 null，`missing` 列出缺失部分）。只有 `OVER_AUTO_APPROVAL_LIMIT` 带 `basis`。

**Block**（硬阻断）

```json
{"code": "START_DATE_IN_PAST" | "END_BEFORE_START" | "DURATION_EXCEEDED", "actual_value": "2026-10-19",
 "limit_value": "2026-10-20", "rule_id": null | "RULE-DURATION-001", "policy_version": null | "v1.2",
 "clause": null | "POL-7", "message": "…"}
```

`DURATION_EXCEEDED` 引用 RULE-DURATION-001（POL-7）；日期类阻断是输入语义校验，`rule_id` 为 null。

### 2.5 Approval / 审批卡

```json
{"id": "…", "trip_id": "…", "revision": 1, "approver_id": "u-lisi",
 "status": "pending" | "decided" | "superseded",
 "decision": null | "approve" | "reject" | "request_changes", "comment": null,
 "decided_at": null, "idempotency_key": null,
 "card": {"employee": {id, name, department}, "slots": {…}, "policy_version": "v1.2",
          "cost_breakdown": {…}, "budget_delta_fen": 76000,
          "violations": [...], "approval_reasons": [...],
          "sources": ["demo_fixture", "policy"], "plan_summary": {…}}}
```

审批卡在创建时快照（AC-008 要求成本明细、预算差、规则/理由、引用、来源、revision 齐全）。

### 2.6 AuditEvent

```json
{"id": "…", "seq": 42, "trip_id": "…" | null, "revision": 1 | null, "ts": "…Z", "actor": "u-zhangsan" | "system",
 "type": "slots_changed", "payload": {…}, "refs": [{"kind": "rule", "id": "RULE-HOTEL-001", "version": "v1.2"}],
 "trace_id": "…"}
```

- `seq`：全库单调递增的写入序号，审计列表按它排序（同一毫秒内的多条事件也有确定顺序，IMP-38）。
- 每次状态变化都有一条 `status_changed`（`{from, to, event, previous_revision, revision}`，IMP-29）；Demo reset 完成后写一条 `trip_id=null` 的 `demo_reset`（IMP-37）。

审计表由数据库触发器保证只追加：UPDATE 一律拒绝；DELETE 仅在 Demo reset 事务内允许（已实现）。事件类型清单见 `docs/architecture.md` §6。

### 2.8 PolicyVersion / PolicyRule（已实现）

```json
{"version": "v1.3", "status": "draft" | "published" | "archived", "effective_date": "2026-11-01",
 "document": {"kind": "markdown" | "pdf", "sha256": "…"}, "created_at": "…", "published_at": "…" | null,
 "rule_counts": {"candidate": 0, "confirmed": 8, "rejected": 1, "unsupported": 1, "invalid_quote": 1},
 "rules": [PolicyRule]}
```

```json
{"id": "RULE-HOTEL-001", "version": "v1.3", "type": "hotel_cap", "supported": true,
 "condition": {"level": "employee"}, "value": {"caps_fen": {"A": 45000, "B": 40000, "C": 30000}},
 "unit": "fen_per_night", "clause": "POL-2", "quote": "员工住宿上限：…", "locator": "line:13" | "page:1" | null,
 "quote_status": "valid" | "invalid_quote", "status": "candidate" | "confirmed" | "rejected" | "unsupported",
 "problems": [{"code": "INVALID_QUOTE" | "UNSUPPORTED_RULE_TYPE" | "INVALID_RULE_SHAPE" | "UNIT_MISMATCH", "message": "…"}],
 "confirmed_by": "u-wangwu" | null, "modification_reason": "…" | null, "created_at": "…", "updated_at": "…",
 "source": "policy"}
```

- `rule_counts` 只给 admin；`rules` 只在单个版本读取（#19）中出现。
- `problems` 在读取时按当前内容计算，不落库；admin 审核时据此决定确认、修改或驳回。
- `locator`：Markdown 为 `line:N` / `line:N-M`，PDF 为 `page:N` / `page:N-M`；引用按 §4.8 规范化后在原文中逐字匹配，找不到为 null 且 `quote_status=invalid_quote`。

### 2.9 PolicyAnswer（#18，已实现）

```json
{"answer": "员工去上海出差，住宿每晚最多能报500元。", "answerable": true,
 "citations": [{"version": "v1.2", "clause": "POL-2", "quote": "员工住宿上限：A 类城市每晚上限 500 元；…",
                "locator": "line:13", "source": "policy"},
               {"version": "v1.2", "clause": "POL-1", "quote": "A 类城市：北京、上海、广州、深圳。…",
                "locator": "line:9", "source": "policy"}],
 "version": "v1.2", "source": "model", "generator": "qwen3.8-max" | "fake:extractive",
 "retrieval": {"mode": "vector" | "hybrid" | "bm25",
               "chunks": [{"rank": 1, "clause": "POL-2", "locator": "line:13", "score": 0.71}],
               "supplements": [{"clause": "POL-1", "locator": "line:9", "reason": "rule_dependency"}]}}
```

- `citations[].quote` 是制度原文的整段文字（切分片段本身），`locator` 由代码按 §4.8 定位，不由模型书写。
- `answerable=false` 时 `citations=[]`，`answer` 固定为“不知道：当前制度（vX）中没有找到能回答这个问题的条款。”，不展示模型的猜测。
- `retrieval.chunks` 为检索排序结果（最多 5 条，AC-025 的 Recall@3 按它计算）；`supplements` 是按已确认规则的依赖补充给模型的条款（如住宿/伙食分档依赖 POL-1 城市分类），不参与排序。
- `generator="fake:extractive"`：fake/recorded 模式的确定性抽取式回答（BM25，零网络），只用于流程验收，不得用于问答指标（D-08）。

### 2.7 OutboxEmail

```json
{"id": "…", "trip_id": "…", "revision": 1, "kind": "trip_delivery" | "workflow_notification",
 "to": ["u-zhangsan"], "cc": ["u-lisi"], "subject": "…", "body": "…",
 "created_at": "…", "unique_key": "<trip_id>:<revision>:<kind>"}
```

## 3. 端点

| # | 方法 / 路径 | 身份 | 输入 | 输出 / 状态变化 | 主要错误 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `GET /api/health` | 无 | — | `{status, db, demo_today, llm_mode}` | — | 已实现 |
| 2 | `GET /api/users` | 无 | — | `{users: [Employee]}` | — | 已实现 |
| 3 | `POST /api/trips` | employee / manager | `{text?: string}` | 201 `{trip, assistant}`；新 Trip `COLLECTING`、revision=1；有 `text` 时同步执行一次与 #6 相同的槽位抽取，`assistant` = slots 事件 payload + `text`（追问文本）+ `text_generator: "template"`；无 `text` 时为 null | 403 admin；422 text 为空或超过 2000 字；模型不可用时**仍 201**，Trip 已创建，`assistant = {error: {code: "MODEL_UNAVAILABLE", message, retryable, details}}` | 已实现 |
| 4 | `GET /api/trips` | 任意 | `?scope=mine\|reports\|all` | `{scope, trips: [TripSummary]}`（默认 scope：employee/manager 为 `mine`，admin 为 `all`）；employee 仅自己；manager `mine`/`reports`（直属下属）；admin `all` | 403 越权 scope；422 非法 scope 值 | 已实现 |
| 5 | `GET /api/trips/{id}` | 任意 | — | Trip | 404 不存在或不可见 | 已实现 |
| 6 | `POST /api/trips/{id}/messages` | Trip 所有人 | `{text, expected_revision}`；可选 `Idempotency-Key` | SSE：`status`→`slots`→`token`…→(`citation`)→`done` | 404；409 `STALE_REVISION`/`INVALID_STATE`（仅 `COLLECTING`、`POLICY_BLOCKED` 可用）；流内 `error` `MODEL_UNAVAILABLE` | 已实现 |
| 7 | `PATCH /api/trips/{id}/slots` | Trip 所有人 | `{expected_revision, slots: {字段: 新值}}`（只含待改字段；值 null 表示清空） | 200 Trip；审计 `slots_changed`（before/after）；`POLICY_BLOCKED` 时首次修改转 `COLLECTING`（revision 不变，审计 `blocked_cleared`） | 404；409 状态/revision；422 `INVALID_SLOT` | 已实现 |
| 8 | `POST /api/trips/{id}/submit` | Trip 所有人 | `{expected_revision}`；`Idempotency-Key` | SSE：`trip_status(VALIDATING)`→[`violations`+`trip_status(POLICY_BLOCKED)`] 或 [`status(planning)`→`plan`→`violations`→(`approval_required`→`trip_status(PENDING_APPROVAL)`) 或 (`trip_status(AUTO_APPROVED)`→`trip_status(DELIVERY_PENDING)`→`trip_status(DELIVERED)`)]→`done`；D-21 时 `trip_status(COLLECTING)`→`error` `NO_APPROVER` | 400；404；409；422 `SLOTS_INCOMPLETE`（details.missing_fields）/`INVALID_SLOT` | 已实现 |
| 9 | `POST /api/uploads` | employee / manager | multipart `file`（PNG/JPG，按文件头魔数判定，≤10 MiB） | 201 `{id, filename, sha256, size_bytes, content_type}`；仅存本地；审计 `file_uploaded`（trip_id=null） | 413；415；403 admin（格式错误先于角色判断） | 已实现 |
| 10 | `POST /api/trips/{id}/image-extractions` | Trip 所有人 | `{upload_id}` | 201 `{id, trip_id, upload_id, status: "pending", fields: {destination, start_date, end_date, purpose, meeting_address}, source: "model", generator, applied: null, created_at, confirmed_at: null}`；**不修改 Trip**；审计 `image_extracted` | 422 缺 upload_id；404 Trip / `UPLOAD_NOT_FOUND`（不存在或非本人上传）；403 非所有人；409 非 `COLLECTING`；503 `MODEL_UNAVAILABLE`（details 带 `retryable`） | 已实现 |
| 11 | `POST /api/trips/{id}/image-extractions/{eid}/confirm` | Trip 所有人 | `{expected_revision, fields: {…}}`（员工可修改后确认） | 200 Trip；`fields` 省略时按识别结果原样确认；值为 null 的字段**跳过**（不清空已有槽位）；写入 origin=`image` 的 `slots_changed` 与 `image_confirmed`（`edited_fields` 列出员工改动过的字段） | 404 `EXTRACTION_NOT_FOUND`；409 `EXTRACTION_NOT_PENDING`/revision/状态；422 `INVALID_SLOT`（只允许邀请函五个字段） | 已实现 |
| 12 | `GET /api/approvals` | manager | `?status=pending` | `{approvals: [Approval]}`，仅直属下属、当前 revision | 403 非 manager | 已实现 |
| 13 | `POST /api/trips/{id}/decision` | 直属 manager | `{revision, decision, comment?}`；`Idempotency-Key` | 200 `{trip, approval}`；approve→`APPROVED`→交付（同步）→`DELIVERED` 或 `DELIVERY_PENDING`；reject→`REJECTED`+通知；request_changes→revision+1、`COLLECTING`+通知，旧审批卡置 `superseded` 并追加 `approval_superseded` 审计（`reason="request_changes"`，IMP-95） | 400 `INVALID_DECISION`；403 `FORBIDDEN`/`SELF_APPROVAL`/`NOT_DIRECT_MANAGER`；404；409 `STALE_REVISION`/`ALREADY_DECIDED`/`INVALID_STATE`；422 `COMMENT_REQUIRED`（reject 与 request_changes 的意见必须非空） | 已实现 |
| 14 | `POST /api/trips/{id}/revalidate` | Trip 所有人或其直属 manager | `{revision}`；`Idempotency-Key` | 200 `{trip, previous_revision, outcome}`；`outcome` 为按新版本重判的规则结论 `PENDING_APPROVAL`\|`AUTO_APPROVED`\|`POLICY_BLOCKED`，D-21 时为 `no_approver`；`trip` 是含交付在内的最终状态（如 `DELIVERED`）；见 §4.3 | 400 缺 Key；422 revision 格式 / `IDEMPOTENCY_KEY_REUSED`；404 不存在或不可见（含非直属 manager）；403 admin；409 `STALE_REVISION` / `INVALID_STATE`（非 `PENDING_APPROVAL`） | 已实现 |
| 15 | `POST /api/trips/{id}/delivery/retry` | admin | — | 200 Trip；`DELIVERED` 或仍 `DELIVERY_PENDING`（`delivery.error` 更新） | 403；404；409 非 `DELIVERY_PENDING` | 已实现 |
| 16 | `GET /api/trips/{id}/audit` | 可见者 | — | `{events: [AuditEvent]}`，按 seq 升序 | 404 | 已实现 |
| 17 | `GET /api/trips/{id}/itinerary` | 可见者 | `?revision=` | `{trip_id, revision, created_at, itinerary}`（`trip_delivery` 邮件中的链接）；revision 默认当前值 | 404（含 `ITINERARY_NOT_FOUND`：该 revision 未交付） | 已实现 |
| 18 | `POST /api/policy/ask` | 任意（需身份） | `{question: string(1～500), version?: string}`，不接受其他字段 | 200 PolicyAnswer（§2.9）：`{answer, answerable, citations: [Citation], version, source: "model", generator, retrieval}`；默认用当前 published 版本，可显式指定 archived 版本；无依据时 `answerable=false`、citations 为空、answer 为固定的“不知道”文本；引用只能是本次检索给模型看过的片段（代码校验，模型声称可答但没有有效引用 → 降级为拒答）；每次问答审计 `policy_question`（trip_id=null） | 401；422 `VALIDATION_ERROR`（question 空/超长/非字符串、version 格式、未知字段、非对象）；404 `POLICY_VERSION_NOT_FOUND`（版本不存在、非 admin 指定 draft、没有 published 版本）；503 `MODEL_UNAVAILABLE`（details 带 `retryable`、`stage: "policy_qa"`，并写 `error` 审计） | 已实现 |
| 19 | `GET /api/policies` / `GET /api/policies/{version}` | 任意 | — | 列表 `{policies: [PolicyVersion]}`（不含 rules）；单个版本 = PolicyVersion 含 `rules: [PolicyRule]`；admin 看到全部版本与全部候选并带 `rule_counts`；其他角色看不到 draft，且只看到 `confirmed` 规则（IMP-72） | 404 `POLICY_VERSION_NOT_FOUND`（含非 admin 读 draft） | 已实现 |
| 20 | `POST /api/policies` | admin | multipart `file`（`.md`/`.markdown` UTF-8，或文本型 PDF，按 `%PDF-` 魔数判定，≤10 MiB）、`version`（`v1.2`、`v9.1-test` 形式）、`effective_date`（YYYY-MM-DD） | 201 PolicyVersion（`status=draft`、`rules=[]`）；文件存 `uploads/policies/`；审计 `policy_uploaded`（trip_id=null，含 kind、pages、sha256） | 422 `VALIDATION_ERROR` 字段格式 / `EMPTY_DOCUMENT`；413；415 `SCANNED_PDF`/`UNSUPPORTED_MEDIA_TYPE`；403 非 admin；409 `VERSION_EXISTS` | 已实现 |
| 21 | `POST /api/policies/{version}/extract` | admin | `{overwrite?: false}` | 200 `{version, reused, replaced, generator, candidates: [PolicyRule]}`；已有候选且未 `overwrite` 时原样返回（`reused=true`，不再调用模型、不写审计）；`overwrite=true` 时替换（`replaced` 列出被替换的规则摘要）；每次真正抽取审计 `policy_extracted`；规则 ID 由代码按类型与顺序分配；原文找不到的引用标 `invalid_quote`，7 类以外的类型标 `unsupported` | 422 overwrite 非布尔；404；403；409 `POLICY_NOT_DRAFT`；503 `MODEL_UNAVAILABLE`（details 带 `retryable`、`stage`；fake 模式下未录制的文档也走这里） | 已实现 |
| 22 | `PATCH /api/policies/{version}/rules/{rule_id}` | admin | `{status: confirmed\|rejected\|unsupported, value?, condition?, modification_reason?}` | 200 PolicyRule；`confirmed_by` 只在 confirmed 时记录；审计 `policy_rule_updated`（before、after、modified_fields、modification_reason，refs 指向该规则） | 400 `INVALID_RULE_STATUS`；422 多余字段/缺 status/value 非对象；404 `RULE_NOT_FOUND`；403；409 `POLICY_NOT_DRAFT`；422 `MODIFICATION_REASON_REQUIRED`（value/condition 与当前值不同即为修改，理由去空白后非空）→ `QUOTE_INVALID` → `UNSUPPORTED_TYPE` → `INVALID_RULE_SHAPE`（后三者仅在 confirmed 时检查） | 已实现 |
| 23 | `POST /api/policies/{version}/publish` | admin | `Idempotency-Key` | 200 `{policy, archived_version, confirmed_rule_ids}`；单事务内新版本 published、旧版本 archived；审计 `policy_published`；已绑定旧版本的 Trip 不变，显示 `policy_change_notice` | 400；404；403；幂等重放；409 `POLICY_NOT_DRAFT` / `UNPROCESSED_CANDIDATES`（仍有 `candidate`）；422 `MISSING_RULE_TYPES` / `RULE_SET_INVALID`（与运行时 `RuleSet` 同一套校验） | 已实现 |
| 24 | `GET/PUT/DELETE /api/users/{id}/preferences` | 本人（employee/manager） | PUT body = Preferences | 200 `{user_id, preferences}`；PUT 整体替换（notes 去多余空白，空 notes 省略）；DELETE 后为 `{}`；每次 PUT/DELETE 都审计 `preferences_changed`（trip_id=null，含 action、before、after、changed）；已提交的 Trip 不受影响（D-20） | 422 `INVALID_PREFERENCES`/`VALIDATION_ERROR`（先于身份判断）；404 `NOT_FOUND` 用户不存在；403 `FORBIDDEN` 他人或 admin | 已实现 |
| 25 | `GET /api/outbox` | admin | `?kind=&trip_id=` | `{emails: [OutboxEmail]}` | 403；422 非法 kind | 已实现 |
| 26 | `POST /api/demo/reset` | admin | `{confirmation: "RESET_DEMO_DATA"}` | 200 `{status: "reset"}`；恢复种子、清空动态数据与 checkpoint | 403；422 `CONFIRMATION_REQUIRED` | 已实现 |
| 27 | `GET /api/demo/faults`、`POST /api/demo/faults` | admin | POST `{fault: transport_timeout\|no_protocol_hotel\|weather_unavailable, enabled}` | 当前故障配置 | 400 `UNKNOWN_FAULT`；403；422 enabled 非布尔；变更写审计 `fault_changed` | 已实现 |
| 28 | `GET /api/health/providers` | admin | — | `{llm_mode, llm: {mode, text_model, vision_model, api_key_configured}, providers: [{name, implementation, mode, status: ok\|fault_injected, fault, last_error}], network: {installed, allowed_hosts, attempts, external_attempts, blocked, allowed, local, external_hosts, blocked_by_caller}}`；`api_key_configured` 只在 real 模式给出布尔（只核对变量名），其余模式为 null；`network` 是出站边界汇总（AC-021，见 `network_guard.py`）：`allowed_hosts` 为 fake/recorded 空集、real 只含 DashScope 主机名，`external_attempts` 为回环之外的尝试数，明细追加到 `<data_dir>/network-guard.jsonl`；不含密钥或其片段 | 403 | 已实现 |

交付写失败故障**不**提供 HTTP 入口，只通过自动化测试钩子注入（§4.7）。

## 4. 状态语义补充

### 4.1 COLLECTING / POLICY_BLOCKED

- 对话（#6）与槽位修改（#7）只在 `COLLECTING`、`POLICY_BLOCKED` 可用；在 `POLICY_BLOCKED` 下的第一次有效修改把 Trip 转回 `COLLECTING`，revision 不变（D-09），写审计 `blocked_cleared`。
- 提交（#8）只在 `COLLECTING` 可用；槽位必须完整且格式合法（D-13/D-14）。

### 4.2 提交

- 规划开始时绑定当前发布版本；与上次绑定不同则审计 `policy_bound`（`changed: true`）。
- 提交时把个人偏好快照进 Trip（`personal_snapshot`）；Trip 槽位 `preferences` 非空时以其为准（`trip_override`）；两者都空为 `none`（D-20）。
- 需审批但申请人无直属经理（D-21）：不建审批卡；本次计算的方案只写入审计 `no_approver`，**不保留为当前方案**（plan/cost 清空）；revision 不变；Trip 回 `COLLECTING`；SSE `error` `NO_APPROVER`。下次提交重新规划。

### 4.3 按新版本重新校验（D-12）

- 仅 `PENDING_APPROVAL` 且 `revision` 为当前值时可用；复用当前 revision 已选候选，只重算成本并按**当前发布版本**重判，不调用 Provider。
- 旧审批卡置 `superseded`，revision +1，绑定新版本，新旧两次判断都进审计（`revalidated`）。
- 结果：需审批 → 新 revision 的 `PENDING_APPROVAL` 与新审批卡；不再需要审批 → `AUTO_APPROVED` 并照常交付（`DELIVERED` / `DELIVERY_PENDING`），审计 `approval_superseded`；命中硬阻断 → `POLICY_BLOCKED`（新 revision）。
- 当前发布版本与绑定版本相同时接口仍按上述语义执行（契约未定义此情形的拒绝）；工作台只在 `policy_change_notice` 非 null 时展示该按钮。
- 重新校验后的 revision 没有 LangGraph checkpoint 线程，对新审批卡的决定走直接路径（与图节点共用 `apply_decision` + `deliver`，审计 `decision_recorded.via="direct"`）。
- 少见情形：审批期间申请人的直属经理被移除（D-21）→ 不建新卡，方案只进审计 `no_approver`，Trip 回 `COLLECTING`（新 revision），`outcome="no_approver"`。

### 4.4 审批与交付

- 决策只能针对当前 revision 的 pending 审批卡；交付、通知幂等键 `(trip_id, revision, kind)`，数据库唯一约束保证不重复。
- approve 后交付在同一请求内执行；行程单与 `trip_delivery` 在同一事务写入，失败则 `DELIVERY_PENDING` 并记录 `delivery.error`；管理员重试（#15）与服务启动自动重试共用同一幂等交付函数。
- `DELIVERED`、`REJECTED` 为终态；`DELIVERY_PENDING` 不能重新审批。
