# 基线记录（TASK-001）

| 项 | 内容 |
|---|---|
| 依据 | `docs/contract-v1.1.md` 第 2.2 节、第 0.2 节 F-01～F-05 |
| 核实日期 | 2026-10-19 |
| 核实人 | 实施与交付负责人（执行 AI） |
| 授权状态 | AUTH-01 已批准（DashScope，50 元上限）；AUTH-02 未授权（暂缓）；AUTH-03、AUTH-04 未授权 |

## 1. 环境（F-01、F-02）

| 项 | 实测结果 | 与契约记录对比 |
|---|---|---|
| 操作系统 | Windows 10.0.26200（Git Bash / MINGW64 作为 shell） | 一致 |
| Python | 3.11.0（`C:\lyl_kaifa\python3.11\python.exe`） | 一致 |
| uv | 0.12.2 | 一致 |
| Node | v24.19.0（仅工具用途，项目不依赖） | 一致 |
| Docker | 未安装 | 一致，不作为运行或验收条件 |
| pytest | 全局 Python 未安装；可通过 uv 装入项目环境（实测 9.1.1） | 一致 |
| Git | 2.45.1.windows.1 | — |

素材环境的依赖版本（来自素材 `uv.lock`，仅作参考，目标项目自行锁定）：langgraph 1.2.10、langgraph-checkpoint 4.1.1、langgraph-checkpoint-sqlite 3.1.1、langchain 1.3.14、langchain-core 1.5.4、langchain-openai 1.4.1、aiosqlite 0.22.1、fastapi 0.141.1、sse-starlette 3.3.4、pydantic 2.13.4。

## 2. 目标目录与 Git（F-04）

- 目标目录：`C:\Users\22316\Desktop\1\lc-course\travel-compliance-desk\`，独立 Git 仓库，分支 `main`。
- 基线提交：`1bd364b`（docs: lock implementation contract v1.0），标签 `contract-v1.0`。
- 核实时的工作区：`docs/contract.md` 已修改（加“已被取代”说明），`docs/contract-v1.1.md` 未跟踪；尚未提交 v1.1（后续已由规划侧提交为 `2392bf4`，见第 8 节）。
- TASK-001 开始前已有文件：`.gitignore`、`docs/contract.md`、`docs/contract-v1.1.md`。无其他用户文件，后续工作不存在覆盖风险。
- 素材目录 `C:\Users\22316\Desktop\1\lc-course\` 不是 Git 仓库。TASK-001 只对其执行了只读操作（列目录、grep、读取 `uv.lock`）。

## 3. 素材 Demo 清单（F-05）

所有路径相对于素材目录。“复用”指借鉴模式并在目标项目中重写，不直接导入或复制素材代码。

| Demo | 准确路径与入口 | 用途 | 可借鉴的内容 | 不复用的部分及理由 |
|---|---|---|---|---|
| email_agent | `app/agents/email_agent.py`；经 `app/main.py`、`app/api/v1/chat.py` 由 FastAPI 提供 SSE | 邮件助手：认证后查收、发信 | `HumanInTheLoopMiddleware` 的 `interrupt_on` 人工确认；`AsyncSqliteSaver` 持久化；`dynamic_prompt` 与按状态动态选工具；SSE 流式输出 | 由 Agent 自主决定发信，不符合 HC-04（交付必须由固定工作流节点完成）；真实发信由 Outbox 替代 |
| personal_chief | `app/agents/personal_chief.py`（app 版）；`src/lc_course/agents/personal_chief.py`（langgraph.json 中的 `personal_chief`） | 私人厨师：看图识食材、联网搜菜谱 | 多模态 HumanMessage（qwen3.5-plus）；`SqliteSaver` | 图片走 OSS 预签名 URL，违反 D-05；Tavily 联网不属于 Demo 模式 |
| marry_chief | `src/lc_course/agents/marry_agents.py`（`coordinator`） | 婚礼筹备：supervisor 协调场地、歌单、行程子 Agent | Agent-as-tool 的组织方式；Kiwi MCP 客户端延迟创建 | 多 Agent 自由协商结果，违反第 3.4 节 OUT 和 HC-01 |
| rag_agent | `src/trail/RAG.py`（Naive RAG）、`src/trail/RAG_Agent.py`（`agentic_agent`）、`src/trail/profile-rag.py`（混合检索） | 文档问答，三种 RAG 形态 | DashScope embedding；Chroma；bm25s 稀疏检索 + RRF 融合；gte-rerank-v2；Agentic RAG 中把检索做成工具 | pkuseg 需要编译构建，在 Windows 上有风险，改用纯 Python 的 jieba（见第 5 节）；素材的 `text-embedding-v3` 不在当前可用模型列表中 |
| travel_agent | `src/lc_course/agents/travel_agents.py`（`coordinator`） | 旅行规划：航班、酒店、景点子 Agent | Kiwi MCP 航班查询；分领域专家拆分 | 价格和方案由 LLM 汇总，违反 HC-01 和 HC-03；Kiwi 属于 Live（AUTH-02 未授权） |

## 4. 环境变量（F-03，仅核对变量名）

只用 `grep -o` 提取变量名，未读取或输出任何值。

- 素材 `.env` 中的变量名：`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`DEEPSEEK_API_KEY`、`HF_ENDPOINT`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`、`LANGSMITH_TRACING`、`MINERU_TOKEN`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_BUCKET`、`TAVILY_API_KEY`。
- 进程环境中存在的相关变量名：`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`TAVILY_API_KEY`。
- 未发现任何 SMTP 变量。

由此得到的约束：

1. **不得加载素材 `.env`**。其中含 `LANGSMITH_TRACING`，一旦加载就可能把调用轨迹上传到 LangSmith，构成外部写入，违反 HC-06 和 AUTH-04。目标项目和所有尖峰脚本都显式设置 `LANGSMITH_TRACING=false`。
2. **进程环境里有 `OPENAI_API_KEY`/`OPENAI_BASE_URL`**。OpenAI 兼容客户端必须显式传入 `api_key` 和 `base_url`，避免误用这两个变量。
3. `DASHSCOPE_API_KEY` 已在进程环境中，目标项目通过环境变量读取。`.env.example` 只列变量名。

## 5. 高风险能力验证

| 编号 | 假设/风险 | 方法 | 结果 | 证据 |
|---|---|---|---|---|
| RISK-003 | LangGraph `interrupt()` + `AsyncSqliteSaver` 能在进程被强制终止后恢复 | 最小图 plan→approval(interrupt)→deliver。进程 A 运行到 interrupt 后用 `taskkill /F` 强杀；进程 B 用同一数据库 `Command(resume=approve)`；进程 C 重复 resume | **通过**。B 恢复出待审批状态和 interrupt 载荷，执行完成，状态为 DELIVERED；plan 执行 1 次，deliver 执行 1 次，Outbox 1 行；C 看到图已完成，未重复执行 | `artifacts/spikes/risk003-interrupt-restart.log`；脚本 `spikes/risk003_interrupt_restart.py` |
| H-01 / RISK-001 | qwen3.8-max 的结构化输出稳定，槽位抽取 ≥9/10 | 10 条冻结样例，`with_structured_output(method="function_calling")`，temperature=0，关闭思考模式，逐字段比对 | **通过，10/10**。模糊日期（“下周”“月底”）均返回 null，未用常驻地补全出发地，中文数字预算正确转换 | `artifacts/spikes/model-spike.json` |
| RISK-002 | 多模态模型能直接接收本地 base64 图片 | qwen3.5-plus，S-11 fixture 转 data URL，不经 OSS，运行 3 次 | **通过，3/3**。城市、起止日期、地址正确，3 次输出一致 | 同上；fixture `tests/fixtures/invitations/shanghai_supplier_conference.png`，生成脚本 `scripts/make_invitation_fixture.py` |
| embedding | DashScope embedding 可用 | `qwen3.7-text-embedding`，OpenAI 兼容接口 | **通过**，维度 1024 | 同上 |
| RISK-007 | Windows 上核心依赖可安装 | 通过 uv 安装 langgraph 1.2.10、checkpoint-sqlite 3.1.1、aiosqlite、langchain-openai 1.4.1、fastapi 0.141.1、uvicorn、sse-starlette 3.3.4、pypdf、bm25s、jieba、pytest、httpx、pillow | **通过**。全部安装成功，jieba 中文分词可用；未引入 torch | 本文件第 1 节；安装输出见执行记录 |

### 5.1 验证中发现、影响设计的事实

1. **审批节点会在恢复时重新执行。** 实测 `approval_enter` 执行了 2 次（中断前一次、恢复时一次）。因此审批节点在 `interrupt()` 之前不得有副作用；所有写操作放在 interrupt 之后的独立节点，并依靠唯一约束保证幂等。
2. **重复决策必须在 API 层拦截。** 图完成后再次 resume 不会重复执行节点，但 HTTP 层仍需按第 4.6 节返回 409，或按同一 Idempotency-Key 返回首次结果，不能依赖图的行为。
3. **金额由代码换算。** 模型只抽取以“元”为单位的整数预算，由代码乘以 100 转为分，与 HC-01 一致。
4. **模型抽取的事由比预期更长。** 图片抽取的事由是“供应商大会：年度合作评审与新品对接”，地址中保留了空格（“2727 号”）。S-11 和 AC-029 的“与预期一致”需要明确判定口径，见第 7 节 Q-B1。

## 6. 费用

TASK-001 累计估算 0.41 元（保守口径），明细见 `artifacts/cost-ledger.md`。

## 7. 未验证项与待决问题

| 编号 | 内容 | 状态 |
|---|---|---|
| U-1 | `AsyncSqliteSaver` 与 FastAPI 在同一事件循环中、配合 SSE 流式输出的组合 | 未验证；TASK-002/005 中通过 AC-010 验证 |
| U-2 | 文本型 PDF 抽取后引用定位（RISK-005） | 未验证；TASK-008 |
| U-3 | 中文问答能否达到 AC-004 阈值（RISK-004） | 未验证；M3 真实评测 |
| U-4 | Live Provider（Kiwi、Tavily、Open-Meteo） | blocked：AUTH-02 未授权 |
| Q-B1 | S-11 和 AC-029 中“事由一致”的判定口径。建议：抽取的事由包含“供应商大会”即算一致；地址比对前去除空白，包含“滨江大道2727号”和“星衡国际会议中心”即算一致 | 已关闭：契约 §7 S-11/AC-029 采用该口径（提交 `2392bf4`） |

## 8. 实施负责人复核（TASK-001 验收）

复核日期：2026-10-19。以下为复核时重新执行的可观察检查，不是对原记录的复述。

| 检查 | 复核结果 |
|---|---|
| Git 基线 | `HEAD=2392bf4`，标签 `contract-v1.1` 指向该提交，工作区干净；另有 `contract-v1.0` 标签指向 `1bd364b` |
| 素材路径 | 第 3 节列出的 8 个入口文件逐一存在（email_agent、personal_chief×2、marry_agents、RAG×3、travel_agents） |
| 环境变量 | `DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`TAVILY_API_KEY` 在进程环境中存在；仅检查存在性，未读取值 |
| 工具版本 | Python 3.11.0、uv 0.12.2、Git 2.45.1，与第 1 节一致 |
| 尖峰证据 | `artifacts/spikes/model-spike.json` 为 10/10 结构化、3/3 多模态、embedding 1024 维，且逐行 token 与费用台账一致；`artifacts/spikes/risk003-interrupt-restart.log` 显示强杀后恢复、plan/deliver 各 1 次、Outbox 1 行 |
| 图片 fixture | 实际打开 `tests/fixtures/invitations/shanghai_supplier_conference.png`：城市上海、日期 2026-10-28～10-30、事由含“供应商大会”、地址含“滨江大道 2727 号”与“星衡国际会议中心”；按 S-11 去空白口径可命中 |
| 费用 | 台账累计 0.4119 元，未接近 40 元暂停线 |

结论：TASK-001 的核实项均有可复现证据，状态 `passed`。唯一待决项 Q-B1 已由契约关闭。进入 TASK-002。
