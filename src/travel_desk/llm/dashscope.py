"""DashScope (OpenAI-compatible) client, usage metering and the AUTH-01 budget guard.

Secrets (HC-07): the API key is read from the process environment at call time
and handed to the client only. It is never logged, stored, or put in an error.
Exception *text* from the SDK is never surfaced either (an authentication error
can echo a masked key); callers only see `ModelUnavailable` with a fixed reason
and the exception type name.

Cost (AUTH-01): every real call is appended to `usage_log_path` (JSONL, committed
evidence) with tokens and a conservative estimate (36 CNY / 1M tokens for every
token type, the highest unit price among the models used). Before each call the
guard adds the manual total recorded in `artifacts/cost-ledger.md` (the
`manual_total_cny` marker) to the JSONL sum and refuses to call once the pause
line (40 CNY) is reached. A missing or unreadable ledger also refuses: fail closed.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from travel_desk.config import Settings

API_KEY_ENV = "DASHSCOPE_API_KEY"
PRICE_PER_M_TOKENS_CNY = 36.0
PAUSE_AT_CNY = 40.0
_MANUAL_TOTAL = re.compile(r"<!--\s*manual_total_cny:\s*([0-9]+(?:\.[0-9]+)?)\s*-->")


class ModelUnavailable(Exception):
    """A model call could not produce a usable result. `reason` is safe to show; nothing else is."""

    def __init__(self, reason: str, *, retryable: bool = True, error_type: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.error_type = error_type


class BudgetPaused(ModelUnavailable):
    def __init__(self, reason: str):
        super().__init__(reason, retryable=False, error_type="BudgetPaused")


def api_key_present() -> bool:
    """F-03: only the variable's presence is checked, never its value."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


@dataclass(frozen=True)
class UsageEntry:
    purpose: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int
    est_cost_cny: float


def estimate_cny(total_tokens: int) -> float:
    return round(total_tokens * PRICE_PER_M_TOKENS_CNY / 1_000_000, 6)


class UsageMeter:
    def __init__(self, usage_log_path: Path, ledger_path: Path, *, pause_at_cny: float = PAUSE_AT_CNY):
        self.usage_log_path = usage_log_path
        self.ledger_path = ledger_path
        self.pause_at_cny = pause_at_cny

    @classmethod
    def from_settings(cls, settings: Settings) -> UsageMeter:
        return cls(settings.usage_log_path, settings.cost_ledger_path)

    def manual_total_cny(self) -> float:
        try:
            match = _MANUAL_TOTAL.search(self.ledger_path.read_text(encoding="utf-8"))
        except OSError:
            match = None
        if match is None:
            raise BudgetPaused("无法读取费用台账（artifacts/cost-ledger.md 的 manual_total_cny 标记），"
                               "已停止真实调用")
        return float(match.group(1))

    def runtime_total_cny(self) -> float:
        if not self.usage_log_path.is_file():
            return 0.0
        total = 0.0
        for line in self.usage_log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += float(json.loads(line)["est_cost_cny"])
        return total

    def spent_cny(self) -> float:
        return round(self.manual_total_cny() + self.runtime_total_cny(), 6)

    def check(self) -> None:
        spent = self.spent_cny()
        if spent >= self.pause_at_cny:
            raise BudgetPaused(f"累计估算费用 {spent:.2f} 元已达暂停线 {self.pause_at_cny:.0f} 元，"
                               "需项目所有者批准后继续")

    def record(self, *, purpose: str, model: str, usage: dict[str, Any] | None,
               latency_ms: int) -> UsageEntry:
        usage = usage or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total = int(usage.get("total_tokens") or (input_tokens or 0) + (output_tokens or 0))
        entry = UsageEntry(purpose, model, input_tokens, output_tokens, total, estimate_cny(total))
        self.usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "purpose": purpose, "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total,
                "est_cost_cny": entry.est_cost_cny, "latency_ms": latency_ms,
            }, ensure_ascii=False) + "\n")
        return entry


def chat_model(settings: Settings, model: str, *, timeout: float = 60.0):
    from langchain_openai import ChatOpenAI  # imported lazily: fake/recorded mode never loads the SDK

    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise ModelUnavailable(f"未配置 {API_KEY_ENV}，真实模型不可用", retryable=False)
    # Explicit api_key/base_url so OPENAI_* variables in the environment are ignored.
    return ChatOpenAI(model=model, api_key=key, base_url=settings.dashscope_base_url, temperature=0,
                      timeout=timeout, max_retries=1, extra_body={"enable_thinking": False})


async def structured_call(settings: Settings, meter: UsageMeter, *, purpose: str, model: str, schema: type,
                          messages: list[Any], timeout: float = 60.0) -> Any:
    """One function-calling structured output call; returns the parsed pydantic object."""
    meter.check()
    llm = chat_model(settings, model, timeout=timeout).with_structured_output(
        schema, method="function_calling", include_raw=True)
    started = time.perf_counter()
    try:
        result = await llm.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001 - SDK errors are reduced to their type name (no text)
        raise ModelUnavailable("模型调用失败，请稍后重试", error_type=type(exc).__name__) from None
    meter.record(purpose=purpose, model=model, usage=getattr(result["raw"], "usage_metadata", None),
                 latency_ms=int((time.perf_counter() - started) * 1000))
    parsed = result.get("parsed")
    if parsed is None:
        raise ModelUnavailable("模型输出无法解析为结构化结果", error_type="ParsingError")
    return parsed


async def text_call(settings: Settings, meter: UsageMeter, *, purpose: str, model: str,
                    messages: list[Any], timeout: float = 60.0) -> str:
    meter.check()
    llm = chat_model(settings, model, timeout=timeout)
    started = time.perf_counter()
    try:
        message = await llm.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001
        raise ModelUnavailable("模型调用失败，请稍后重试", error_type=type(exc).__name__) from None
    meter.record(purpose=purpose, model=model, usage=getattr(message, "usage_metadata", None),
                 latency_ms=int((time.perf_counter() - started) * 1000))
    content = message.content
    return content if isinstance(content, str) else ""
