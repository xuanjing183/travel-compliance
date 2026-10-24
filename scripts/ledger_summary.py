"""Summarise `artifacts/llm-usage.jsonl` into the runtime table of `artifacts/cost-ledger.md` (AUTH-01).

Usage: uv run --offline python scripts/ledger_summary.py [--write]

Without `--write` the table is printed. With `--write` the block between the
`runtime-table` markers in the ledger is replaced; nothing else is touched.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from travel_desk.llm.dashscope import PAUSE_AT_CNY, UsageMeter  # noqa: E402

USAGE = ROOT / "artifacts" / "llm-usage.jsonl"
LEDGER = ROOT / "artifacts" / "cost-ledger.md"
BEGIN, END = "<!-- runtime-table:begin -->", "<!-- runtime-table:end -->"


def build_table(usage_path: Path, ledger_path: Path) -> str:
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0, 0, 0.0])
    if usage_path.is_file():
        for line in usage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            group = groups[(entry["ts"][:10], entry["purpose"], entry["model"])]
            group[0] += 1
            group[1] += int(entry["total_tokens"])
            group[2] += float(entry["est_cost_cny"])
    rows = ["| 日期（UTC） | 用途 | 模型 | 调用次数 | token 数 | 估算费用（元） |",
            "|---|---|---|---:|---:|---:|"]
    for (day, purpose, model), (calls, tokens, cost) in sorted(groups.items()):
        rows.append(f"| {day} | {purpose} | {model} | {int(calls)} | {int(tokens)} | {cost:.4f} |")
    meter = UsageMeter(usage_path, ledger_path)
    runtime, manual = meter.runtime_total_cny(), meter.manual_total_cny()
    rows.append("")
    rows.append(f"**运行期合计：{runtime:.4f} 元；含手工行累计估算：{manual + runtime:.4f} 元 / 上限 50 元"
                f"（暂停线 {PAUSE_AT_CNY:.0f} 元）**")
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    table = build_table(USAGE, LEDGER)
    if not args.write:
        print(table)
        return 0
    text = LEDGER.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        print("ledger has no runtime-table markers", file=sys.stderr)
        return 1
    LEDGER.write_text(pattern.sub(lambda _: f"{BEGIN}\n{table}\n{END}", text), encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
