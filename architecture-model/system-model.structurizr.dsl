workspace "Travel Compliance Desk" "当前架构模型（current-state）。依据 src/travel_desk 代码与 docs/architecture.md 取证；置信度与未知项见 system-model.evidence.md。" {

    model {

        # ============================ L1: 参与者 ============================
        # 角色由 db/schema.py 的 employees.role CHECK 约束与 README 演示账号表共同确认。

        employee = person "员工" "发起差旅申请、用自然语言补全槽位、上传会议邀请函、查询制度、设置个人出行偏好。示例账号：张三（销售部，直属经理李四）。" "Role: employee"

        manager = person "直属经理（审批人）" "审批 / 驳回 / 要求修改直属下属的申请；本人也可发起申请（但不得审批自己的申请）。示例账号：李四（销售部）、赵六（采购部）。" "Role: manager"

        admin = person "管理员" "制度编译：上传 → 抽取 → 审核 → 发布版本。运维：故障注入开关、Outbox 查看、交付重试、Demo reset。不能创建 Trip、不能审批。示例账号：王五（行政财务部）。" "Role: admin"

        # ============================ 外部系统 ============================
        # 关键事实：默认 demo 配置下（llm_mode=fake, provider_mode=demo）本系统
        # 对外部系统的调用数为 0 —— 这是 network_guard 允许清单为空集强制的，
        # 不是约定。AC-021 已取证 external_attempts=0。

        dashscope = softwareSystem "DashScope（阿里云百炼）" "OpenAI 兼容接口。文本模型 qwen3.8-max（槽位抽取、行程摘要、制度问答回答、规则抽取）、视觉模型 qwen3.5-plus（邀请函识别）、embedding qwen3.7-text-embedding（制度向量检索）。仅 llm_mode=real 时调用，受 AUTH-01 费用守卫约束。" "External / LLM" {
            tags "External"
        }

        liveProviders = softwareSystem "Live Provider 组（Kiwi MCP / Tavily / QWeather）" "真实交通搜索（mcp.kiwi.com，JSON-RPC over SSE）、酒店参考（api.tavily.com，仅无价参考）、天气预报（QWeather，30d/15d/7d 分级回退）。仅 provider_mode=live 时进入允许清单。AUTH-02 未授权：实现存在且有 mock 单测，但从未对真实服务验证（AC-028 blocked）。" "External / Unverified" {
            tags "External" "Unverified"
        }

        # ============================ 目标系统 ============================

        tcd = softwareSystem "Travel Compliance Desk" "把差旅制度编译成可确认、可执行、可追溯的规则，在规划阶段就计算成本、识别例外，把需要判断的事项交给直属经理审批，并保留从输入、条款、规则、供应商数据到审批与交付的完整证据链。核心架构原则（HC-01/HC-02）：模型只做理解与文字生成，金额、合规结论、候选选择、状态转换全部由代码决定。" {

            # ------------------------ L2: 容器 ------------------------

            workbench = container "工作台 SPA" "三角色工作台：员工（申请/对话/图片/行程单）、经理（审批队列/决定）、管理员（制度编译/运维）。所有文本以文本节点插入（el()，从不使用 innerHTML），金额与合规结论直接显示服务端字段、前端不重算。" "原生 HTML/CSS/ES module，无构建步骤、无第三方前端依赖" "SPA" {
                tags "Frontend"
            }

            api = container "FastAPI 应用（travel_desk）" "单进程 uvicorn。承载全部业务逻辑：HTTP/SSE 接口、用例服务、LangGraph 工作流、确定性规则核心、规划与候选选择、制度编译与 RAG、LLM 三模式适配、进程内出站网络守卫。lifespan 顺序：装守卫 → 初始化数据库 → 打开 Workflow → 启动恢复 → 开始服务。" "Python 3.11 / FastAPI / uvicorn / LangGraph" "Application" {

                # ------------------------ L3: 组件 ------------------------

                routes = component "api/ — 路由层" "很薄：只做身份依赖、读 body/header、调用服务、序列化。请求体统一按 Any 接收，形状与字段格式由服务层校验，因此同一用例从 HTTP 与从测试直接调用得到相同错误。含 SSE 帧封装（sse-starlette，15 秒心跳）与统一错误体 + trace_id 中间件。" "routes_trips / approvals / policies / admin / users / reports / health / sse / deps / errors"

                services = component "services/ — 用例服务层" "业务用例与事务边界：conversation（对话→抽取→格式检查→写入→追问）、trips（状态转换 apply_transition）、approvals（建卡与决定）、delivery（三段事务交付 + Outbox）、policies（上传/抽取/审核/发布）、policy_qa、preferences、revalidation、uploads、audit、idempotency、access、views。" "Python 模块，同步 sqlite3 短事务"

                workflow = component "workflow/ — LangGraph 工作流" "graph.py 定义 8 个节点的 StateGraph；engine.py 的 Workflow 拥有 AsyncSqliteSaver、编译图、每 Trip 进程内锁，入口为 submit / decide / revalidate / retry_delivery / recover / reset。approval 节点只含 interrupt()（无副作用，恢复时从头重跑，RISK-003）。业务运行是后台任务，客户端断开不中断写入。" "LangGraph StateGraph + AsyncSqliteSaver"

                domain = component "domain/ — 确定性规则核心" "纯函数、无 I/O。输入槽位、已选候选、绑定版本的已确认规则、申请人角色、Demo 日期；输出 Evaluation{outcome, blocks, cost_breakdown, violations, approval_reasons, refs}。含状态机（所有状态变化必须经 transition()）、共享值类型（金额 StrictInt 拒 float/bool/str、时间必须带时区、未知字段拒绝）。" "Pydantic + 纯 Python，零依赖"

                planning = component "planning/ — 规划与候选选择" "planner.build_plan 只收集与选择、不判定合规；selector 负责过滤排序与防伪造（verify_choice 要求选中候选与本次响应中同 ID 候选完全相等，未知 ID / 改价 / 跨次候选一律 ForgedCandidateError）。降级从不编造数据：失败或空结果使该部分为 None（待人工补录）。" "Python 模块"

                policy = component "policy/ — 制度编译与 RAG" "documents（分段、规范化、引用定位 locate）、extraction（RuleExtractor + compile_candidates，代码分配规则 ID、判定封闭类型）、rag（切块 600 字 / BM25 手写 / 向量 / RRF 混合 / 条款依赖补充 / 引用代码终审）。" "jieba + DashScope embeddings + 内存索引"

                llm = component "llm/ — 模型适配与费用守卫" "dashscope（客户端、UsageMeter、structured_call/text_call、BudgetPaused 40 元暂停线、异常文本从不外露只给类型名）、slots（Fake 正则 / Recorded 冻结 / Real 结构化输出）、summary（模板 / 冻结 / 真实，输出经检查：空、超 400 字或含金额即丢弃）、vision（base64 data URL，不经 OSS）。" "langchain-openai ChatOpenAI + openai AsyncOpenAI"

                providers = component "providers/ — Provider 抽象" "base.py 定义 Protocol（Transport/Hotel/Weather/Clock）与三类错误（Timeout/Unavailable/InvalidResponse）+ Providers 集合；demo.py 读 fixture 并每次逐条 Schema 校验（任一不合格整份作废）；live.py 实现 Kiwi MCP / Tavily 参考 / QWeather。模式由 TRAVEL_DESK_PROVIDER_MODE 切换。" "Protocol + fixture / httpx"

                db = component "db/ — 持久化" "schema.py（13 张表 + append-only 触发器）、database.py（db_session 短事务 / read_session / WAL / PRAGMA user_version 版本闸门）、repositories.py、seed.py（4 名演示账号、v1.0 archived + v1.2 published、各 8 条规则）。" "SQLite"

                guard = component "network_guard — 出站边界" "进程内补丁 socket.getaddrinfo / create_connection / socket.connect / urllib.request.urlopen。允许清单由配置推导而非运维开关。所有主机名经单一归一化点 _host_text()；无法归一化的一律判为外部并拒绝（fail closed，IMP-97）。每次尝试追加 JSONL 证据。这是证据与纵深防御，不是沙箱。" "Python socket 猴子补丁"
            }

            appDb = container "业务数据库" "13 张表：employees、policy_versions、policy_rules、trips、approvals、idempotency_keys、audit_events、outbox_emails、itineraries、uploads、image_extractions、faults、meta。金额列均为整数分。audit_events 与 outbox_emails 由触发器保证 append-only（UPDATE 一律拒绝；DELETE 仅在 Demo reset 事务的 meta.reset_in_progress 标志下允许）。" "SQLite（WAL），var/app.sqlite" "Database" {
                tags "Database"
            }

            checkpointDb = container "LangGraph Checkpoint 库" "工作流图中断点与恢复状态，按 thread_id 隔离（<trip_id>:rev-<n>:run-<8hex>）。与业务库物理分离：业务库是权威状态，checkpoint 缺失时 decide() 走直接路径调用同一 apply_decision + deliver。Demo reset 会 adelete_thread 清空全部线程。" "SQLite / AsyncSqliteSaver（aiosqlite 长连接），var/checkpoints.sqlite" "Database" {
                tags "Database"
            }

            uploadStore = container "上传文件存储" "图片与制度原件，文件名由服务端生成（uuid.ext / uploads/policies/）。只通过数据库登记的路径访问；Demo reset 只删除登记过且位于 upload_dir 内的文件，不清空整个目录。" "文件系统，var/uploads/" "FileStore" {
                tags "Store"
            }

            fixtures = container "确定性 Fixture" "Provider 响应（transport.json / hotels.json / weather.json）、录制模型输出（invitations.json / slot_extractions.json / summaries.json / extract-*.json）、制度文件（v1.0 / v1.2 / v1.3）。是 fake 与 recorded 模式的唯一外部数据来源，使 36/36 场景可复现且零网络。" "JSON / Markdown / PDF，tests/fixtures/" "Fixtures" {
                tags "Store" "Deterministic"
            }

            ledger = container "费用与证据台账" "llm-usage.jsonl（每次真实调用一行：用途、模型、token、保守估算、延迟）+ cost-ledger.md（manual_total_cny 标记）。费用守卫在每次调用前把两者相加，达 40 元暂停线即拒绝（台账不可读同样拒绝，fail closed）。" "JSONL / Markdown，artifacts/" "EvidenceLog" {
                tags "Store" "Evidence"
            }

            guardLog = container "出站守卫日志" "每次出站尝试一行：ts、via（哪条被补丁的途径）、host（归一化后名称或转义 repr）、host_form（str/bytes/NoneType）、port、decision（local/allowed/blocked）、caller（调用点文件:行号）。是 AC-021 网络边界与 IMP-97 修复的取证来源。" "JSONL，var/network-guard.jsonl" "EvidenceLog" {
                tags "Store" "Evidence"
            }
        }

        # ============================ 关系 ============================

        # --- 参与者 → 系统（业务语言）---
        employee -> workbench "发起申请、补全信息、上传邀请函、查询制度、设置偏好" "HTTPS / JSON"
        manager -> workbench "查看待审批队列、作出审批决定、发起自己的申请" "HTTPS / JSON"
        admin -> workbench "编译并发布制度版本、注入故障、重试交付、重置演示数据" "HTTPS / JSON"

        # --- 容器内部 ---
        workbench -> routes "调用 REST 接口；用 fetch + ReadableStream 读 SSE（EventSource 不能发 POST 与自定义头）" "HTTP + SSE"

        routes -> services "调用用例服务（薄路由，错误顺序由服务层统一决定）" "in-process"
        services -> workflow "submit / decide / revalidate / retry_delivery（每 Trip 一把进程内 asyncio.Lock）" "in-process"
        services -> domain "状态转换与格式校验（apply_transition 带源状态守卫的 UPDATE）" "in-process"
        services -> appDb "读写业务数据（db_session 每用例一个短事务）" "SQL"
        services -> uploadStore "登记并写入上传文件（类型按文件头魔数判定，不信任文件名与 Content-Type）" "file"
        services -> policy "制度编译（上传/抽取/审核/发布，共用一把进程内 threading.Lock）与制度问答" "in-process"
        services -> llm "槽位抽取、邀请函识别（模型只提议，代码终审）" "in-process"

        workflow -> domain "规则评估 evaluate_trip、硬阻断 check_blocks、状态转换" "in-process"
        workflow -> planning "build_plan（只在 check_blocks 无阻断后调用，S-06 不触达 Provider）" "in-process"
        workflow -> llm "make_summary 行程摘要（叠加到 plan.summary，只追加文字）" "in-process"
        workflow -> checkpointDb "写入/读取图中断点与恢复状态" "SQL (aiosqlite)"
        workflow -> appDb "写 provider_call / candidates_selected / plan_created / rules_evaluated / status_changed 等审计" "SQL"

        planning -> providers "search / search_contract / search_backup / forecast（每次调用产生 ProviderCall 审计）" "in-process"
        planning -> domain "hotel_level / tier_of / 排序规则" "in-process"

        providers -> fixtures "demo 模式：每次调用重新读取并逐条 Schema 校验" "file (JSON)"
        providers -> liveProviders "live 模式：真实交通 / 酒店参考 / 天气（AUTH-02 未授权，行为未验证）" "HTTPS / JSON-RPC over SSE"

        policy -> fixtures "recorded 模式：按文档规范化全文 SHA-256 或图片 SHA-256 匹配冻结输出" "file (JSON)"
        policy -> llm "real 模式：规则抽取（policy_extraction）与问答回答（policy_answer）、embedding" "in-process"
        policy -> appDb "读已确认规则以推导条款依赖（clause_links）" "SQL"

        llm -> dashscope "real 模式：结构化输出（function calling）、文本生成、embedding、多模态图片" "HTTPS (OpenAI 兼容)"
        llm -> ledger "每次真实调用前检查累计费用、调用后追加一行用量" "file (JSONL/MD)"

        guard -> guardLog "追加每次出站尝试的分类结果" "file (JSONL)"
        llm -> guard "所有出站连接都经被补丁的 socket 途径（守卫在 lifespan 内全程安装）" "in-process hook"
        providers -> guard "同上" "in-process hook"

        db -> appDb "建表、版本闸门（PRAGMA user_version）、种子" "SQL"
    }

    views {

        systemContext tcd "L1-SystemContext" "系统边界：三类角色、两个外部系统组。注意默认 demo 配置下对外部系统的调用数为 0（允许清单空集强制，AC-021 已取证）。" {
            include *
            autolayout tb
        }

        container tcd "L2-Containers" "容器视图：一个前端 SPA、一个单进程 FastAPI 应用、两个物理分离的 SQLite 库、三个文件存储、一个确定性 fixture 源、两个证据台账。" {
            include *
            autolayout tb
        }

        component api "L3-FastAPI-Components" "FastAPI 应用内部组件与依赖方向。关键性质：domain 是纯函数核心、不依赖任何外层；llm 与 providers 是可替换适配层（三模式 / 双模式）；network_guard 横切所有出站路径。" {
            include *
            autolayout tb
        }

        # --- 聚焦视图：模型边界（HC-01/HC-02 的架构落点）---
        component api "L3-ModelBoundary" "模型边界聚焦：哪些组件会触达 LLM，哪些是纯代码决定。用于回答『模型能影响什么、不能影响什么』。" {
            include llm policy planning domain workflow services
            autolayout lr
        }

        styles {
            element "Person" {
                shape person
                background #1168bd
                color #ffffff
            }
            element "External" {
                background #8c7a6b
                color #ffffff
                shape cloud
            }
            element "Unverified" {
                border dashed
                opacity 70
            }
            element "Database" {
                shape cylinder
                background #2e7d32
                color #ffffff
            }
            element "Store" {
                shape folder
                background #6a6a6a
                color #ffffff
            }
            element "Evidence" {
                background #b8860b
                color #ffffff
            }
            element "Deterministic" {
                background #4a5d8a
                color #ffffff
            }
            element "Frontend" {
                shape webBrowser
                background #7b6ca6
                color #ffffff
            }
            element "Application" {
                background #d64541
                color #ffffff
            }
        }
    }
}
