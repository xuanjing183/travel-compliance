# Hugging Face Spaces 免费部署指南（Travel Compliance Desk）

目标：把本项目部署到 Hugging Face Spaces（免费 Docker 规格），得到一个可以发给 HR 的公开网址。

部署包已准备好：[`deploy/hf-space/`](./hf-space/)，约 7.3 MB，包含应用源码、制度/供应商 fixture、演示数据库（59 条差旅 + 审批数据）。

## 0. 开始前须知（重要）

- **绝不要把 `.env` 或任何密钥文件上传到 Space**。Space 仓库是公开的。密钥只通过 HF 的"Space Secrets"注入运行环境——本项目（HC-07）恰好只从进程环境读取密钥，天然兼容。
- **Space 是公开的**：拿到网址的任何人都能使用这个应用（包括 HR，也包括陌生人）。真实模型调用有内置预算闸（累计 ¥40 自动暂停），可以控制被刷的风险。
- **数据是临时的**：免费 Space 休眠/重启后，磁盘重置，恢复为部署包里自带的演示数据库——正好实现"每次打开都是干净的演示状态"。
- **冷启动**：48 小时无访问后 Space 休眠，下次访问需等 1~3 分钟构建/唤醒，第一次请提前打开一次。

## 1. 准备密钥（从本地 `.env` 抄，不要上传文件本身）

打开项目根目录的 `.env`，把值抄下来，稍后粘贴到 HF 网页上：

| 用途 | 变量名 | 必填？ |
| --- | --- | --- |
| 真实 LLM（通义千问） | `DASHSCOPE_API_KEY` | **必填**（没有它 LLM 功能不可用，其余功能仍正常） |
| 酒店搜索（Live 供应商） | `TRAVEL_DESK_TAVILY_API_KEY` | 可选 |
| 天气预报（Live 供应商） | `TRAVEL_DESK_QWEATHER_API_HOST` | 可选 |
| 天气预报（Live 供应商） | `TRAVEL_DESK_QWEATHER_API_KEY` | 可选 |

## 2. 创建 Space（网页操作，约 5 分钟）

1. 注册/登录 [huggingface.co](https://huggingface.co)。
2. 右上角头像 → **New Space**。
3. 填写：Space name 如 `travel-compliance-desk`；License 随意；SDK 选 **Docker** → Blank 模板；可见性 **Public**（Private 的话 HR 打不开）。
4. 创建后进入 Space 页面，接下来上传文件。

## 3. 上传部署包（两种方式任选）

**方式 A：网页拖拽（最简单）**

打开本机 `travel-compliance-desk/deploy/hf-space/` 文件夹，把里面的**全部内容**（`Dockerfile`、`README.md`、`pyproject.toml`、`src/`、`tests/`、`var/`）拖拽上传到 Space 的 **Files** 页签根目录，提交 `main` 分支。上传后 Space 会自动开始构建。

**方式 B：git 推送（方便以后更新）**

```bash
cd deploy/hf-space
git init
git remote add space https://huggingface.co/spaces/<你的用户名>/travel-compliance-desk
git add . && git commit -m "Deploy to HF Spaces"
git push --force space main
```

（首次推送会用浏览器登录 HF 账号。）

## 4. 配置密钥与开关（Settings 页签）

进入 Space → **Settings** → **Variables and secrets**：

1. **Secret**（选 New secret，值不会公开可见）：
   - `DASHSCOPE_API_KEY` = （第 1 步抄的值）
   - 如果要启用 Live 供应商演示，再加 `TRAVEL_DESK_TAVILY_API_KEY`、`TRAVEL_DESK_QWEATHER_API_HOST`、`TRAVEL_DESK_QWEATHER_API_KEY`。
2. **Variable**（非敏感，公开可见，用于改运行模式）：
   - 想切到 Live 供应商模式：加 `TRAVEL_DESK_PROVIDER_MODE` = `live`（不加则默认 `demo`，酒店/天气走内置演示数据，演示最稳定）。
   - LLM 模式默认已是 `real`，无需额外配置。

每次增删 Secret/Variable 都会触发 Space 重启，等它变绿即可。

## 5. 验证部署

Space 状态变为 **Running** 后打开网址，检查：

- [ ] 页面能打开，出现"差旅合规工作台"登录页（身份卡片）
- [ ] 以张三（员工）进入，差旅列表有数据（自带 59 条演示差旅）
- [ ] 制度问答能回答（说明 `DASHSCOPE_API_KEY` 生效）
- [ ] 新建一条差旅申请，观察合规判定
- [ ] 切换李四（经理）处理一条超标审批

常见问题：

- **构建失败**：Space 页面 Logs 页签看报错。本项目依赖均为纯 Python 轮子，正常 3~5 分钟构建完成。本机没有 Docker，构建结果以 HF 实际日志为准。
- **LLM 功能报"未配置 DASHSCOPE_API_KEY"**：检查 Secret 名称拼写（必须完全一致）。
- **通义千问接口从海外访问不稳**：HF 服务器在海外，如 DashScope 主站接口受限，可在 Settings 加 Variable `TRAVEL_DESK_DASHSCOPE_BASE_URL` = `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` 并换用国际版 Key。
- **页面 502/唤醒慢**：免费 Space 休眠后的冷启动，等 1~3 分钟刷新。

## 6. 发给 HR

直接发送 Space 网址（`https://huggingface.co/spaces/<你的用户名>/travel-compliance-desk`），附一句演示指引：

> 打开后点击身份卡片即可体验（无需注册）：员工张三可提交差旅申请、上传会议通知看 AI 抽取与合规判定；经理李四在审批中心处理超标申请；管理员王五可看制度管理、报表分析与运维面板。

## 7. 以后怎么更新

改动代码后：重新执行第 3 步（重新同步/上传 `deploy/hf-space/` 的内容）即可。`deploy/hf-space/var/` 里是部署时的演示数据库快照——想重置 Space 数据，把本地最新 `var/app.sqlite`、`var/checkpoints.sqlite*` 拷进去重新上传，或直接用应用内管理员"运维"页的重置功能。
