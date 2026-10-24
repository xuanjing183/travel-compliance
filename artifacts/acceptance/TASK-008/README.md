# TASK-008 验收证据：制度问答（#18）与制度上传、抽取、审核、发布（#19–#23）

依据 `docs/contract-v1.1.md` §8（AC-005、AC-024）、§7.1.1、S-12、REQ-004/029。除“制度问答”一节中明确标为真实模型的冒烟外，全部证据在**确定性验收模式**下生成：`llm_mode=fake`、临时数据目录、不访问网络、不调用模型（运行时环境中移除了 `DASHSCOPE_API_KEY`）。

## 复现

```bash
env -u DASHSCOPE_API_KEY uv run --offline pytest tests/test_policies.py tests/test_policy_qa.py -v   # → pytest-v.txt / junit.xml
uv run --offline python scripts/export_task008_evidence.py artifacts/acceptance/TASK-008   # 21/21 PASS → summary.json
```

脚本中的 AC-005 和 AC-024 各自使用一个全新的临时数据库：AC-005 的测试版本号会把 v1.2 归档，不能和 S-12 共用。

## 模拟与真实的边界

- 规则抽取使用 §7.1.1 的录制输出 `tests/fixtures/recorded/extract-v1.2.json`、`extract-v1.3.json`。文件标注 `source: "hand_written"`，即按制度原文手写的期望输出，**不是**真实模型的输出，不能作为 AC-018（抽取质量）的证据。响应中的 `generator` 字段为 `recorded:hand_written`。
- 录制输出按文档规范化全文的 SHA-256 匹配（IMP-65）。因此 `v9.1-test` 上传的 v1.2 原文能拿到 v1.2 的录制结果；没有录制的文档返回 503，不会臆造规则。
- 真实模型抽取（`RealRuleExtractor`）已实现，但本目录没有调用它。真实运行属于 AC-018，在 TASK-011 的授权评测中进行。

## AC-005 制度上传与发布

使用独立测试版本号 `v9.1-test`（Markdown）、`v9.2-test`（文本 PDF）、`v9.3-test`（扫描 PDF），不占用 v1.0/v1.2/v1.3。

| 步骤 | 证据文件 | 结果 |
|---|---|---|
| 发布前数据库 | `AC-005/db-before.json` | 只有种子：v1.0 archived、v1.2 published |
| 上传 | `AC-005/01-upload.json` | MD → 201 draft；文本 PDF → 201 draft（kind=pdf，2 页）；扫描 PDF → 415 `SCANNED_PDF` |
| 抽取 | `AC-005/02-extract-markdown.json`、`02-extract-pdf.json` | 10 条候选：8 条封闭类型；RULE-DURATION-002 为 `invalid_quote`（引用在原文中不存在）；RULE-OTHER-001（`taxi_expense`，POL-8）为 `unsupported`。Markdown 定位为 `line:N`，PDF 为 `page:1`/`page:2` |
| 提前发布 | `AC-005/03-publish-before-review.json` | 409 `UNPROCESSED_CANDIDATES`，`rule_ids` 列出 9 条未处理的候选 |
| 确认无效引用 | `AC-005/04-confirm-invalid-quote.json` | 422 `QUOTE_INVALID` |
| 缺封闭类型 | `AC-005/05-publish-missing-closed-type.json` | 驳回 RULE-MEAL-001 后发布 → 422 `MISSING_RULE_TYPES`，`missing_types=["meal_allowance"]`；v1.2 仍为 published |
| 审核 | `AC-005/06-review-status-codes.json` | 每次 PATCH 的状态码 |
| 发布 | `AC-005/07-publish.json` | 200；`archived_version=v1.2`；同一个 Key 重放返回相同响应 |
| 发布后数据库 | `AC-005/db-after.json` | v1.0 archived、v1.2 archived、v9.1-test published、v9.2-test draft；规则状态与确认人 |
| 审计 | `AC-005/audit.json` | `policy_uploaded`×2、`policy_extracted`×2、`policy_rule_updated`×10、`policy_published`×1（actor=u-wangwu，refs 指向新旧版本） |

数据库快照中，临时上传目录的绝对路径替换成了 `<data_dir>`，证据里不出现本机用户路径。

自动测试还覆盖以下情形（`pytest-v.txt`）：

- **上传：** `.txt`、损坏的 PDF → 415；超过 10 MiB → 413；版本号或日期格式错 → 422；重复版本 → 409 `VERSION_EXISTS`；employee → 403；无身份 → 401。被拒绝的上传不留下文件。
- **抽取：** 已有候选时不带 `overwrite` 原样返回、不写审计；`overwrite=true` 替换并审计。未录制的文档 → 503。对已发布版本抽取 → 409 `POLICY_NOT_DRAFT`。
- **审核：** 修改 value 不给理由（或理由只有空格）→ 422 `MODIFICATION_REASON_REQUIRED`；结构不符 → 422 `INVALID_RULE_SHAPE`；`unsupported` 候选不能确认 → 422 `UNSUPPORTED_TYPE`；非法 status → 400 `INVALID_RULE_STATUS`。审计中记录 before/after、修改字段与理由。
- **发布：** 缺 Key → 400；manager 只有一个级别的住宿上限时 → 422 `MISSING_RULE_TYPES`；非 admin 读 draft → 404；重复发布 → 409 `POLICY_NOT_DRAFT`；发布后的版本不能再修改；发布后新提交的 Trip 绑定新版本并按其已确认规则判断（REQ-008）。
- **读取：** 非 admin 看不到 draft，只能看到 confirmed 规则。
- **Demo reset：** 删除上传的制度文件，恢复种子版本。

## AC-024 / S-12 通过发布接口创建 v1.3，再重新校验

TASK-009 的证据中，v1.3 由测试专用的直接写库发布。本次改为 S-12 原本要求的流程：管理员王五上传 `v1.3.md` → 抽取 → 确认 8 条封闭类型规则、驳回无效引用 → 发布。

| 阶段 | 证据文件 |
|---|---|
| S-01 提交后 | `AC-024/trip-before.json`、`approval-cards-before.json`、`db-policies-before.json` |
| 创建并发布 v1.3 | `01-create-and-publish-v1.3.json`、`db-policies-after-publish.json`、`policy-audit.json` |
| 提示 | `trip-with-notice.json` |
| 重新校验 | `revalidate-response.json` |
| 校验后 | `trip-after.json`、`approval-cards-after.json`、`audit.json` |

检查结果：

- **v1.3 的内容：** 与 v1.2 相比，已确认规则中只有 RULE-HOTEL-001 不同，员工 A 类上限为 45000 分/晚。引用是 v1.3 原文“员工住宿上限：A 类城市每晚上限 450 元；…”，定位 `line:13`。v1.2 被归档。
- **提示：** `policy_change_notice = {bound_version: v1.2, published_version: v1.3}`。
- **重新校验：** revision 1→2，`policy_version=v1.3`，状态仍为 `PENDING_APPROVAL`。
- **旧审批失效：** revision 1 的卡 `superseded`，revision 2 有新的 pending 卡；李四待办中只有新卡；对旧 revision 的决定 → 409 `STALE_REVISION`。
- **新增违规：** `HOTEL_OVER_CAP`，48000 > 45000，依据 RULE-HOTEL-001（v1.3，POL-2），引用为 v1.3 原文。
- **复用候选：** 交通与酒店候选不变，没有新的 Provider 调用（`providers_called=false`）。
- **审计：** 同时保留 v1.2 与 v1.3 两次判断。
- **幂等：** 同一个 Idempotency-Key 重放，返回相同响应。

自动测试 `test_ac024_s12_publish_v13_through_the_api_then_revalidate` 走的是同一条路径。

## 制度问答 #18：实现与真实冒烟

设计见 `docs/architecture.md` §7c（IMP-75–85）。

### 确定性测试

`tests/test_policy_qa.py` 共 32 项，结果见 `pytest-v.txt`：

- **切块：** Markdown 定位（`line:3-5`、`line:9`、`line:13` …）和 PDF 条款号。
- **检索计算：** BM25 与 RRF 的计算。
- **fake 模式 HTTP：** 缺省使用 v1.2，引用 POL-2 `line:13`；“年假”“家属”两题拒答，且引用为空；v1.0 与 v1.2 的 POL-5 分别回答 5 天和 3 天。
- **权限：** 任意角色都能问，无身份 → 401；9 种 422；未知版本或 draft → 404，admin 可以问 draft；没有 published 版本 → 404。
- **审计：** 检查 `policy_question` 的内容。
- **真实模式逻辑：** 用桩替身验证，不联网。覆盖：丢弃未展示的引用 ID、无有效引用时降级为拒答、拒答时清空引用；503 并写 `error` 审计；向量与混合检索；嵌入分批与磁盘缓存（缓存不含原文），缺少密钥时不可重试、不计费；关联条款补充。

**fake 模式的回答器（`fake:extractive`）只用来测试接口和拒答路径，不能作为 AC-004/017/025 的质量证据（D-08）。**

### 真实模型冒烟

这是 AUTH-01 授权下的计费调用，**不是评测**。AC-004/017/025 要在 TASK-011 用冻结的 25 题集运行。

```bash
uv run --offline python scripts/policy_qa_smoke.py artifacts/acceptance/TASK-008/qa-real-smoke.json
```

运行配置：

- 文本模型 `qwen3.8-max`，嵌入模型 `qwen3.7-text-embedding`，检索方式 `vector`（默认）。
- 使用临时数据目录，所以每次运行都会重新嵌入 v1.2 全文。
- 脚本只检查 `DASHSCOPE_API_KEY` 这个变量名是否存在，不读取它的值。

| 轮次 | 证据 | 结果 | token / 估算费用 |
|---|---|---|---|
| 第 1 轮（没有关联条款补充） | `qa-real-smoke-run1.json` | “员工去上海”和“经理去成都”两题都被拒答：前 5 块是 POL-2/POL-3 的分档上限和制度信息，没有 POL-1 的城市分类，模型无法判断上海、成都属于哪类城市。“年假”题拒答正确 | 3056 / 0.110016 元 |
| 第 2 轮（加入 IMP-79 补充，调整提示词） | `qa-real-smoke.json` | 3 题都符合预期。上海员工：500 元，引用 POL-2 `line:13` 和 POL-1 `line:9`。成都经理：500 元，引用 POL-2 `line:15` 和 POL-1 `line:9`。年假：拒答，引用为空 | 3375 / 0.1215 元 |

第 1 轮之后，没有再发起真实调用来排查原因。我用第 1 轮缓存下来的真实向量离线算了排名：上海题中，POL-1 段落在纯向量检索里排第 10，在混合检索里排第 7。也就是说，换成混合检索仍然覆盖不到；单靠加大 top-k，要加到 7–10 才行，还会把无关段落一起塞进上下文。所以最后改为从已确认规则推导条款依赖、补充关联条款（IMP-79）。补充块不进入排名，所以不影响 AC-025 的 Recall@3。

两轮费用都已经记入 `artifacts/llm-usage.jsonl` 和 `artifacts/cost-ledger.md`（运行时合计 0.3609 元，累计 0.7728 / 50 元）。
