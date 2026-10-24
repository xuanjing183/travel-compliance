"""TASK-001 real-model spike (AUTH-01 approved: DashScope only, cap 50 CNY).

Checks:
  H-01 / RISK-001  structured slot extraction on 10 frozen samples (target >= 9/10)
  RISK-002         local base64 image -> multimodal extraction (S-11 fixture), 3 runs
  embedding        one call to confirm the embedding model works and its dimension

Secrets: reads DASHSCOPE_API_KEY from the process environment only; never prints it.
Explicit api_key/base_url are passed so OPENAI_* variables in the environment are ignored.
Cost: every call's token usage is priced at a conservative 36 CNY / 1M tokens (all token types).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field

os.environ["LANGSMITH_TRACING"] = "false"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "spikes"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY = os.environ["DASHSCOPE_API_KEY"]
TEXT_MODEL = os.environ.get("SPIKE_TEXT_MODEL", "qwen3.8-max")
VISION_MODEL = os.environ.get("SPIKE_VISION_MODEL", "qwen3.5-plus")
EMBED_MODEL = os.environ.get("SPIKE_EMBED_MODEL", "qwen3.7-text-embedding")
PRICE_PER_M_TOKENS_CNY = 36.0
SPIKE_BUDGET_CNY = 5.0  # hard stop for this spike, far below the 40 CNY pause line

ledger: list[dict] = []


def charge(kind: str, model: str, usage: dict | None) -> None:
    usage = usage or {}
    total = int(usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
    cost = total * PRICE_PER_M_TOKENS_CNY / 1_000_000
    ledger.append({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "model": model,
                   "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                   "total_tokens": total, "est_cost_cny": round(cost, 6)})
    spent = sum(e["est_cost_cny"] for e in ledger)
    if spent > SPIKE_BUDGET_CNY:
        dump({"aborted": f"spike budget {SPIKE_BUDGET_CNY} CNY exceeded"})
        sys.exit("ABORT: spike budget exceeded")


class Slots(BaseModel):
    """差旅申请槽位。无法从用户原话确定的字段必须为 null，不得猜测。"""
    origin: Optional[str] = Field(None, description="出发城市，规范城市名，如 北京；未明确说明则为 null")
    destination: Optional[str] = Field(None, description="目的城市，规范城市名")
    start_date: Optional[str] = Field(None, description="出发日期 YYYY-MM-DD；只有用户给出明确日期时才填写，'下周''月底'等模糊表达为 null")
    end_date: Optional[str] = Field(None, description="返回日期 YYYY-MM-DD；规则同 start_date")
    purpose: Optional[str] = Field(None, description="出差事由，简短名词短语")
    declared_budget_yuan: Optional[int] = Field(None, description="申报预算，单位：元，整数；中文数字需转换为阿拉伯数字")


SYSTEM = ("你是差旅申请信息抽取器。今天是 2026-10-20。只抽取用户明确给出的信息，"
          "未给出或表达模糊的字段一律返回 null，不要根据常识或员工常驻地补全。")

SAMPLES = [
    ("我10月28日到30日从北京去上海参加供应商大会，预算4000元",
     dict(origin="北京", destination="上海", start_date="2026-10-28", end_date="2026-10-30", purpose="供应商大会", declared_budget_yuan=4000)),
    ("下周去杭州",
     dict(origin=None, destination="杭州", start_date=None, end_date=None, purpose=None, declared_budget_yuan=None)),
    ("要去成都培训，10/27出发10/29回来，北京出发，预算四千",
     dict(origin="北京", destination="成都", start_date="2026-10-27", end_date="2026-10-29", purpose="培训", declared_budget_yuan=4000)),
    ("帮我安排去深圳出差，客户拜访，11月3号到11月5号",
     dict(origin=None, destination="深圳", start_date="2026-11-03", end_date="2026-11-05", purpose="客户拜访", declared_budget_yuan=None)),
    ("预算3000，从上海去南京开会",
     dict(origin="上海", destination="南京", start_date=None, end_date=None, purpose="会", declared_budget_yuan=3000)),
    ("10月25日到26日北京到上海客户拜访，预算两千五",
     dict(origin="北京", destination="上海", start_date="2026-10-25", end_date="2026-10-26", purpose="客户拜访", declared_budget_yuan=2500)),
    ("我想月底去广州",
     dict(origin=None, destination="广州", start_date=None, end_date=None, purpose=None, declared_budget_yuan=None)),
    ("从北京去西安参加技术交流会，11月10日去，11月12日回，预算5000元",
     dict(origin="北京", destination="西安", start_date="2026-11-10", end_date="2026-11-12", purpose="技术交流", declared_budget_yuan=5000)),
    ("预算改成5000",
     dict(origin=None, destination=None, start_date=None, end_date=None, purpose=None, declared_budget_yuan=5000)),
    ("出差去武汉，2026-11-15至2026-11-18，事由：项目验收，出发地天津，预算6000元",
     dict(origin="天津", destination="武汉", start_date="2026-11-15", end_date="2026-11-18", purpose="项目验收", declared_budget_yuan=6000)),
]


def field_ok(name: str, got, want) -> bool:
    if want is None:
        return got in (None, "")
    if name == "purpose":  # purpose: expected keyword must be contained
        return isinstance(got, str) and want in got
    return got == want


def text_model() -> ChatOpenAI:
    return ChatOpenAI(model=TEXT_MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=0, timeout=60,
                      max_retries=1, extra_body={"enable_thinking": False})


def spike_structured() -> dict:
    llm = text_model().with_structured_output(Slots, method="function_calling", include_raw=True)
    rows, passed = [], 0
    for text, want in SAMPLES:
        t0 = time.perf_counter()
        try:
            res = llm.invoke([("system", SYSTEM), ("human", text)])
            charge("slots", TEXT_MODEL, getattr(res["raw"], "usage_metadata", None))
            parsed: Slots | None = res["parsed"]
            got = parsed.model_dump() if parsed else None
            err = repr(res["parsing_error"]) if res.get("parsing_error") else None
        except Exception as e:  # noqa: BLE001 - spike records any failure
            got, err = None, f"{type(e).__name__}: {e}"
        bad = [k for k in want if not got or not field_ok(k, got.get(k), want[k])]
        ok = got is not None and not bad
        passed += ok
        rows.append({"input": text, "expected": want, "got": got, "mismatched_fields": bad, "error": err,
                     "passed": ok, "latency_s": round(time.perf_counter() - t0, 2)})
    return {"model": TEXT_MODEL, "passed": passed, "total": len(SAMPLES), "target": 9, "rows": rows}


class Invitation(BaseModel):
    """会议邀请函抽取结果。看不清或不存在的字段返回 null。"""
    city: Optional[str] = Field(None, description="会议所在城市，规范城市名")
    start_date: Optional[str] = Field(None, description="会议开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="会议结束日期 YYYY-MM-DD")
    purpose: Optional[str] = Field(None, description="出差事由，简短名词短语")
    meeting_address: Optional[str] = Field(None, description="会议详细地址，含场馆名")


def spike_vision() -> dict:
    img = ROOT / "tests" / "fixtures" / "invitations" / "shanghai_supplier_conference.png"
    data_url = "data:image/png;base64," + base64.b64encode(img.read_bytes()).decode()
    llm = ChatOpenAI(model=VISION_MODEL, api_key=API_KEY, base_url=BASE_URL, temperature=0, timeout=90,
                     max_retries=1, extra_body={"enable_thinking": False}
                     ).with_structured_output(Invitation, method="function_calling", include_raw=True)
    want = dict(city="上海", start_date="2026-10-28", end_date="2026-10-30", purpose="供应商大会")
    runs = []
    for i in range(3):
        t0 = time.perf_counter()
        try:
            res = llm.invoke([("system", "从会议邀请函图片中抽取差旅信息。"),
                              {"role": "user", "content": [
                                  {"type": "image_url", "image_url": {"url": data_url}},
                                  {"type": "text", "text": "请抽取会议城市、起止日期、事由和详细地址。"}]}])
            charge("vision", VISION_MODEL, getattr(res["raw"], "usage_metadata", None))
            got = res["parsed"].model_dump() if res["parsed"] else None
            err = repr(res["parsing_error"]) if res.get("parsing_error") else None
        except Exception as e:  # noqa: BLE001
            got, err = None, f"{type(e).__name__}: {e}"
        bad = [k for k in want if not got or not field_ok(k, got.get(k), want[k])]
        addr_ok = bool(got and got.get("meeting_address") and "滨江大道" in got["meeting_address"]
                       and "2727" in got["meeting_address"])
        runs.append({"got": got, "mismatched_fields": bad, "address_ok": addr_ok, "error": err,
                     "passed": got is not None and not bad and addr_ok,
                     "latency_s": round(time.perf_counter() - t0, 2)})
    return {"model": VISION_MODEL, "transport": "local file -> base64 data URL (no OSS)",
            "passed": sum(r["passed"] for r in runs), "total": len(runs), "runs": runs}


def spike_embedding() -> dict:
    emb = OpenAIEmbeddings(model=EMBED_MODEL, api_key=API_KEY, base_url=BASE_URL,
                           check_embedding_ctx_length=False)
    texts = ["员工在A类城市住宿上限为每晚500元。", "机票出发日距申请日不少于3个自然日。"]
    try:
        vecs = emb.embed_documents(texts)
        # OpenAIEmbeddings does not expose usage; estimate generously at 2 tokens per character
        charge("embedding", EMBED_MODEL, {"total_tokens": 2 * sum(len(t) for t in texts)})
        return {"model": EMBED_MODEL, "ok": True, "count": len(vecs), "dim": len(vecs[0])}
    except Exception as e:  # noqa: BLE001
        return {"model": EMBED_MODEL, "ok": False, "error": f"{type(e).__name__}: {e}"}


def dump(results: dict) -> None:
    results["ledger_total_est_cny"] = round(sum(e["est_cost_cny"] for e in ledger), 6)
    results["ledger"] = ledger
    (OUT_DIR / "model-spike.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    results = {"run_at": datetime.now(timezone.utc).isoformat(), "price_assumption_cny_per_m_tokens": PRICE_PER_M_TOKENS_CNY}
    results["structured_output"] = spike_structured()
    results["vision_base64"] = spike_vision()
    results["embedding"] = spike_embedding()
    dump(results)
    s, v, e = results["structured_output"], results["vision_base64"], results["embedding"]
    print(f"structured: {s['passed']}/{s['total']} (target >= {s['target']})")
    for r in s["rows"]:
        if not r["passed"]:
            print("  FAIL:", r["input"], "| fields:", r["mismatched_fields"], "| got:", r["got"], "| err:", r["error"])
    print(f"vision: {v['passed']}/{v['total']}")
    for r in v["runs"]:
        print("  run:", r["passed"], r["got"], r["error"] or "")
    print("embedding:", e)
    print("estimated cost (CNY):", results["ledger_total_est_cny"])
