"""Cross-encoder reranking for the enhanced policy-agent chain (Phase 2).

DashScope `gte-rerank-v2` scores each (original question, candidate chunk) pair. The
SDK's `TextReRank.call` is synchronous, so it runs in a worker thread and cannot block
the event loop. Every call goes through the AUTH-01 meter (`policy_rerank`); the key
is read from the environment only, and neither the key nor exception text is surfaced.

Candidates below `settings.rerank_min_score` (default 0.06, calibrated on the 48-question
draft paraphrase set: relevant first-hits scored 0.146–0.599, the noise floor sat at
~0.006; 0.3 would have emptied 23/40 answerable questions) are dropped. An empty
result is a legitimate outcome: it tells the
agent/tool that the policy has no relevant clause, which must lead to a refusal — not
to a guess. The caller decides what to do with fewer than `top_k` results.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from travel_desk.config import Settings
from travel_desk.llm.dashscope import API_KEY_ENV, ModelUnavailable, UsageMeter
from travel_desk.policy.rag import Chunk

RERANK_TOP_K = 3


@dataclass(frozen=True)
class RankedChunk:
    chunk: Chunk
    score: float


class CrossEncoderReranker:
    def __init__(self, settings: Settings, meter: UsageMeter, *, model: str | None = None,
                 min_score: float | None = None):
        self.settings = settings
        self.meter = meter
        self.model = model or settings.rerank_model
        self.min_score = settings.rerank_min_score if min_score is None else min_score

    def _call_blocking(self, api_key: str, query: str, documents: list[str]):
        import dashscope  # imported lazily: fake/recorded mode never loads the SDK

        return dashscope.TextReRank.call(model=self.model, query=query, documents=documents,
                                         top_n=len(documents), api_key=api_key)

    async def rerank(self, query: str, chunks: list[Chunk], *,
                     top_k: int = RERANK_TOP_K) -> list[RankedChunk]:
        if not chunks or top_k <= 0:
            return []
        self.meter.check()
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            raise ModelUnavailable(f"未配置 {API_KEY_ENV}，rerank 不可用", retryable=False)
        documents = [chunk.search_text for chunk in chunks]
        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(self._call_blocking, api_key, query, documents)
        except Exception as exc:  # noqa: BLE001 - SDK errors are reduced to their type name
            raise ModelUnavailable("rerank 调用失败，请稍后重试", error_type=type(exc).__name__) from None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            raise ModelUnavailable("rerank 返回错误", error_type=f"RerankHTTP{response.status_code}")
        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None) or sum(len(text) for text in documents) + len(query)
        self.meter.record(purpose="policy_rerank", model=self.model,
                          usage={"input_tokens": total, "output_tokens": 0, "total_tokens": total},
                          latency_ms=latency_ms)

        try:
            results = [(int(item.index), float(item.relevance_score))
                       for item in response.output.results]
        except (AttributeError, TypeError, ValueError):
            raise ModelUnavailable("rerank 返回格式不符", error_type="RerankShape") from None
        ranked: list[RankedChunk] = []
        for index, score in results:
            if not 0 <= index < len(chunks):
                raise ModelUnavailable("rerank 返回的候选编号越界", error_type="RerankShape")
            if score >= self.min_score:
                ranked.append(RankedChunk(chunks[index], score))
        ranked.sort(key=lambda item: (-item.score, item.chunk.id))
        return ranked[:top_k]
