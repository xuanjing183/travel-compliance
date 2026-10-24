# AC-029 — 真实图片识别（真实多模态，本地 base64，不经 OSS）

## 结论

**passed** — 真实多模态模型 `qwen3.5-plus` 对 S-11 fixture 抽取，城市/日期/事由/会议地址四项判定口径（METRICS.md §4，冻结）**全部一致**：

| 判定项 | 口径 | 真实抽取值 | 判定 |
|---|---|---|---|
| 城市 | 含「上海」 | `上海市` | ✓ |
| 起始日期 | = 2026-10-28 | `2026-10-28` | ✓ |
| 结束日期 | = 2026-10-30 | `2026-10-30` | ✓ |
| 事由 | 含「供应商大会」 | `2026 年度华东区供应商大会` | ✓ |
| 会议地址 | 去空白后同时含「滨江大道2727号」与「星衡国际会议中心」 | `上海市浦东新区滨江大道 2727 号星衡国际会议中心 3 号厅` | ✓ |

- `all_pass=true`；脚本退出码 0。
- **RISK-002 已消解**：多模态模型可直接接收本地文件的 base64 data URL，无需 OSS 上传（契约禁止用 OSS 绕过）。因此本项为 `passed` 而非 `blocked`。
- 抽取结果仍只是*提议*：写入 Trip 前须员工确认（确认流程由 AC-003 确定性验证），本项只判定原始抽取能力。

## 真实性佐证（非录制回放）

同模型（`qwen3.5-plus`）在 TASK-001 的录制输出（`tests/fixtures/recorded/invitations.json`，3 次调用一致）与本次真实运行**措辞不同但语义一致**，证明这是实时调用而非回放：

| 字段 | 录制输出（TASK-001） | 本次真实运行 |
|---|---|---|
| destination | `上海` | `上海市` |
| purpose | `供应商大会：年度合作评审与新品对接` | `2026 年度华东区供应商大会` |

两项均满足冻结口径（含「上海」/含「供应商大会」），差异体现真实模型的表述波动。

## 运行配置

- `--mode real`：真实多模态模型 `qwen3.5-plus`（`temperature=0`，function-calling 结构化输出 `InvitationOut`）。门控要求（D-08）：**不得**用 Fake/Recorded 代替。
- fixture：`tests/fixtures/invitations/shanghai_supplier_conference.png`（121325 字节，SHA-256 `e88ef3b9c44b4584…`）。
- 传输：本地文件 → base64 data URL，直连 DashScope OpenAI 兼容端点；**不经 OSS**（RISK-002）。

## 命令

```
uv run --offline python scripts/eval_vision.py \
    artifacts/acceptance/TASK-011/ac029-vision/real-s11.json --mode real
```

输出：
```
mode=real extractor=qwen3.5-plus passed=True
  city             PASS  actual='上海市'
  start_date       PASS  actual='2026-10-28'
  end_date         PASS  actual='2026-10-30'
  purpose          PASS  actual='2026 年度华东区供应商大会'
  meeting_address  PASS  actual='上海市浦东新区滨江大道 2727 号星衡国际会议中心 3 号厅'
  usage: 1 calls, 1577 tokens, 0.056772 CNY
```

## 脱敏请求摘要（HC-07：不含图片原始字节与密钥）

`real-s11.json` 的 `request_summary` 记录：模型名、purpose=`image_extraction`、端点（DashScope OpenAI 兼容 chat/completions，function-calling）、传输方式（base64 data URL，无 OSS）、图片元数据（content_type=image/png、sha256、字节数，`raw_bytes_included=false`）、system/user 提示词文本、schema 字段、以及 `api_key` 项仅注明「变量名 `DASHSCOPE_API_KEY` 存在，值从不读取/记录/提交」。**不含** base64 图片字节，**不含**密钥值。

## 费用与授权（AUTH-01）

本次真实运行 1 次调用（purpose=`image_extraction`，model=`qwen3.5-plus`），1577 token，估算 **0.056772 元**，已追加到 `artifacts/llm-usage.jsonl` 并由 `scripts/ledger_summary.py --write` 汇总到 `artifacts/cost-ledger.md`。含手工行累计估算约 2.01 元 / 上限 50 元（暂停线 40 元）。仅核对 `DASHSCOPE_API_KEY` 变量名存在，未读取/打印其值（F-03/HC-07）。

## 证据文件

| 文件 | 内容 |
|---|---|
| `real-s11.json` | 模型名、fixture SHA-256 与字节数、脱敏 `request_summary`、`extraction`（5 字段原始值）、`verdict`（逐项判定 + `all_pass`）、`usage` |
| `../../../scripts/eval_vision.py` | 评测脚本（真实抽取 + 冻结 S-11 口径判定；口径见 `evals/METRICS.md` §4） |
