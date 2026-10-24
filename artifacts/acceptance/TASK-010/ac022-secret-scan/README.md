# AC-022 证据：密钥与日志（契约 §8 / REQ-030）

验收要求：**Git 跟踪文件无密钥**；`.env.example` 仅变量名；**日志无密钥值**。证据为密钥扫描命令、
结果与抽样日志。

## 1. Git 跟踪文件扫描

命令（仅跟踪文件，模式针对密钥**值**而非变量名）：

```bash
git grep -nI -E "sk-[A-Za-z0-9]{16,}|(API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD)[\"']?[[:space:]]*[:=][[:space:]]*[\"'][^\"']{8,}[\"']|Bearer[[:space:]]+[A-Za-z0-9._-]{16,}|(ghp|gho|github_pat|xox[baprs]|AKIA)[A-Za-z0-9_-]{8,}" -- .
```

结果：`scan-tracked.txt`，`git grep` 退出码 **1（零匹配）**。即仓库所有 Git 跟踪文件中不存在任何
形如 `sk-…`/`Bearer …`/`ghp_…`/`AKIA…` 的密钥值，也不存在给 `*_KEY/SECRET/TOKEN/PASSWORD` 赋非空字面量的语句。
（读取 `os.environ["DASHSCOPE_API_KEY"]` 之类的**变量名**引用不属于密钥值，不在匹配范围；HC-07。）

`.env` 由 `.gitignore` 排除（`!.env.example` 例外），不被跟踪。`.env.example` 中密钥项为
`DASHSCOPE_API_KEY=`（**空值，仅变量名**），其余为模型名/base URL/`DEMO_TODAY` 等非敏感默认值。

## 2. 抽样日志扫描

对 `artifacts/` 下全部 `*.log` 与 `*.jsonl`（含真实模型台账 `llm-usage.jsonl` 与各证据目录的 boot 日志）
用同类模式扫描：

```bash
grep -rnI -E "sk-[A-Za-z0-9]{16,}|DASHSCOPE_API_KEY[\"']?[[:space:]]*[:=][[:space:]]*[\"']?[A-Za-z0-9]{8,}|Bearer[[:space:]]+[A-Za-z0-9._-]{16,}|(ghp|gho|github_pat|xox[baprs]|AKIA)[A-Za-z0-9_-]{8,}" artifacts/ --include="*.log" --include="*.jsonl"
```

结果：`scan-logs.txt`，退出码 **1（零匹配）**。抽样日志清单：

- `artifacts/llm-usage.jsonl`（真实模型逐次用量/费用台账，AUTH-01）
- `artifacts/acceptance/TASK-006/AC-010/boot-{1,2,3}.log`
- `artifacts/acceptance/TASK-010/ac012-audit-integrity/boot-1.log`
- `artifacts/acceptance/TASK-010/ac013-fault-injection/boot-1.log`
- `artifacts/acceptance/TASK-010/ac016-demo-reset/boot-1.log`
- `artifacts/acceptance/TASK-010/ac021-network-boundary/boot-{1,2}.log`
- `artifacts/spikes/risk003-interrupt-restart.log`

即真实模式运行虽从进程环境读取 `DASHSCOPE_API_KEY`，但台账与日志只记录模型名、token 数与估算费用，
**不含密钥值**（HC-07）。

## 3. 运行时佐证

- `GET /api/health/providers` 返回 `llm.api_key_configured: null`（fake 模式）且响应体不含 `sk-`
  （`tests/test_api.py::test_faults_toggle_and_show_in_provider_health` 断言 `"sk-" not in json.dumps(health)`）。
- 网络边界守卫（AC-021）的 `network-guard.jsonl` 只记录主机名/调用方/计数，不记录任何凭据。

## 结论

Git 跟踪文件无密钥值、`.env.example` 仅变量名、抽样日志无密钥值，AC-022 通过。
