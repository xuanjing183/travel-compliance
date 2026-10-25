"""Agentic policy Q&A for `/api/policy/ask-agent` (Phase 3, real mode only).

The delivered `/api/policy/ask` path is untouched. This module adds a LangGraph ReAct
agent that decides whether a question is about company travel policy, calls the
`search_policy` tool when it is, and returns a structured `EnhancedAnswerOut`.

The tool owns the whole retrieval optimisation chain (one rewrite call -> multi-query
retrieval -> gte-rerank-v2 -> rule-dependent supplements). It labels every shown
chunk with a stable `clause@locator` key; code accepts a citation only if that key was
actually shown by a tool call in this request. A `grounded=true` answer with no valid
key is downgraded to the delivered refusal text. A chat answer (`grounded=false`) has
no citations and never enters that downgrade path.

Budget/secrets (AUTH-01/HC-07): every model call is metered through a LangChain
callback (the agent, its tool-call loop and the structured-output call); the tool's
rewrite/embedding/rerank calls use the same meter. Only the key's presence is checked.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolNode, create_react_agent
from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter, chat_model
from travel_desk.policy.enhanced_retriever import CANDIDATES, EnhancedRetriever, query_paths
from travel_desk.policy.query_optimizer import QueryOptimizer
from travel_desk.policy.rag import (Chunk, DashScopeEmbedder, Hit, Index, Retrieval, Retriever,
                                    clause_links, refusal_text, with_supplements)
from travel_desk.policy.reranker import RERANK_TOP_K, CrossEncoderReranker

logger = logging.getLogger(__name__)

AGENT_GENERATOR = "agent:react"


class EnhancedAnswerOut(BaseModel):
    """The agent's answer contract; `grounded` separates policy answers from chat."""

    grounded: bool = Field(
        description="问题是否属于公司差旅制度范畴并已调用 search_policy。制度问题必须为 true；"
                    "与制度无关的闲聊为 false")
    answerable: bool = Field(
        description="search_policy 返回的制度片段能否回答该问题；闲聊时为 true；不能回答时为 false")
    answer: str = Field(
        description="中文回答；制度问题只能使用工具返回的片段中的信息；不能回答时留空；闲聊直接回答")
    citation_ids: list[str] = Field(
        default_factory=list,
        description="制度回答所依据片段的 citation_id（形如 POL-2@line:13），必须原样复制工具返回的值；"
                    "闲聊或不能回答时为空")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="回答置信度 0-1：有直接条款且数值明确时 0.8-1.0，需要组合推断时 0.5-0.8；"
                    "拒答、澄清或闲聊为 0")
    needs_clarification: bool = Field(
        default=False,
        description="问题缺少决定结论的关键信息、需要用户补充时由模型提出一个澄清问题并置为 true")


SYSTEM_PROMPT = """你是星衡科技差旅制度问答助手，当前制度版本为 {version}。你可以使用 search_policy 工具检索制度原文。

规则：
1. 任何关于公司差旅制度的问题（住宿/伙食/交通/报销/审批/提前预订/出差时长/其他费用等），必须先调用 search_policy；回答只能依据工具返回的片段，不得使用片段以外的知识或常识推测。
2. 可能属于公司制度、但未必写在差旅制度里的问题（如年假、加班费、家属同行等），也必须先调用 search_policy 确认；工具没有依据时按第 3 条拒答，不要凭常识回答或给出“建议咨询 HR”之类的替代答案。
3. 制度回答必须把 grounded 设为 true。citation_ids 只能原样填写工具返回的 citation_id（形如 POL-2@line:13），不得编造或改写；需要组合多个片段时引用全部用到的片段。
4. 如果工具没有返回相关片段，或片段不足以回答问题：grounded=true、answerable=false、answer 留空、citation_ids 为空，不要猜测，也不要用自己的话写“制度里没有”之类的解释。
5. 只有完全不需要检索制度的寒暄或通用问题（如问候、天气、闲聊），才直接回答，grounded=false、answerable=true、citation_ids 为空，不要调用 search_policy；一旦调用过 search_policy 并回答制度内容，grounded 必须为 true；若只是需要用户补充信息，按第 6 条处理。
6. 如果问题缺少决定金额或结论的关键信息（目的地、员工级别、出差天数、总金额等），且无法通过列举条款完整回答，只提出一个简短的中文澄清问题：grounded=false、answerable=true、needs_clarification=true、answer 为澄清问题、citation_ids 为空；不要猜测缺失信息。
7. confidence 为 0-1 的自评：有直接条款且数值明确时 0.8-1.0，需要组合多个条款推断时 0.5-0.8；拒答、澄清或闲聊为 0。
8. 回答直接给出结论和关键数值（含单位），不要提及“根据工具/片段编号”等实现细节。"""

TOOL_PROMPT = """检索星衡科技差旅制度原文。凡涉及住宿标准、伙食补贴、交通标准、提前报销、审批与自动通过、出差时长、其他费用等制度问题，必须先调用本工具。输入是一个完整的中文问题；如果有多个子问题，可以分别调用。"""


@dataclass(frozen=True)
class AgentAnswer:
    grounded: bool
    answerable: bool
    answer: str
    citations: list[Chunk]
    dropped_citation_ids: list[str]
    downgraded: bool
    generator: str
    tool_calls: int
    queries: list[str]
    split: bool = False
    paths: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_clarification: bool = False


@dataclass
class ToolCall:
    query: str
    retrieval: Retrieval
    shown: list[tuple[str, Chunk, str]]  # citation key, chunk, "retrieved" | "supplement"
    paths: list[str] = field(default_factory=list)
    split: bool = False


@dataclass
class ToolTrace:
    calls: list[ToolCall] = field(default_factory=list)

    def shown(self) -> dict[str, Chunk]:
        chunks: dict[str, Chunk] = {}
        for call in self.calls:
            for key, chunk, _ in call.shown:
                chunks.setdefault(key, chunk)
        return chunks

    def combined(self) -> Retrieval:
        """All chunks a tool call showed, in first-shown order; used by the response/audit."""
        if not self.calls:
            return Retrieval("agent:none", [], [])
        hits: list[Hit] = []
        seen: set[str] = set()
        supplements: list[Chunk] = []
        seen_supplements: set[str] = set()
        for call in self.calls:
            for hit in call.retrieval.hits:
                if hit.chunk.id not in seen:
                    seen.add(hit.chunk.id)
                    hits.append(Hit(hit.chunk, len(hits) + 1, hit.score))
            for chunk in call.retrieval.supplements:
                if chunk.id not in seen and chunk.id not in seen_supplements:
                    seen_supplements.add(chunk.id)
                    supplements.append(chunk)
        terms: list[str] = []
        for call in self.calls:
            for term in call.retrieval.query_terms:
                if term not in terms:
                    terms.append(term)
        return Retrieval(f"{self.calls[-1].retrieval.mode}", hits, terms, tuple(supplements))

    @property
    def split(self) -> bool:
        return any(call.split for call in self.calls)

    def paths(self) -> list[str]:
        paths: list[str] = []
        for call in self.calls:
            for path in call.paths:
                if path not in paths:
                    paths.append(path)
        return paths


def citation_key(chunk: Chunk) -> str:
    return f"{chunk.clause}@{chunk.locator}"


# Models sometimes echo the citation key inline (e.g. 【citation_id: POL-2@line:13】); the
# structured `citations` list is the contract, so bracketed keys are removed from the text.
_INLINE_CITATION = re.compile(
    r"[【\[（(]\s*(?:citation_id\s*[:：]\s*)?(?:POL-\d+|制度信息)@[^】\]）)]+[】\]）)]", re.IGNORECASE)


def clean_answer(text: str) -> str:
    return " ".join(_INLINE_CITATION.sub("", text or "").split())


def finalize_agent(out: EnhancedAnswerOut, shown: dict[str, Chunk], version: str,
                   generator: str, *, tool_calls: int, queries: list[str],
                   split: bool = False, paths: list[str] | None = None) -> AgentAnswer:
    """Code-side citation check for the agent contract.

    Only keys actually shown by a tool call are accepted. `grounded=false` with no tool
    call is a chat answer: citations are ignored and it is never downgraded to a policy
    refusal. Once the agent consulted the tool, an ungrounded reply is a policy answer
    without verifiable support, so it becomes the canonical refusal instead.
    """
    trace = {"split": split, "paths": list(paths or [])}
    if not out.grounded:
        text = clean_answer(out.answer)
        if out.needs_clarification and text:
            # A clarifying question is chat-shaped: it is not a policy claim, so it
            # needs no citation and must not be replaced by the canonical refusal.
            return AgentAnswer(False, True, text, [], [], False, generator, tool_calls, queries,
                               **trace, needs_clarification=True)
        if tool_calls:
            # The model treated a policy question as chat after searching (usually
            # writing its own "制度里没有" explanation). Only the delivered refusal
            # text may answer a policy question without citations.
            return AgentAnswer(True, False, refusal_text(version), [], [], bool(out.answerable),
                               generator, tool_calls, queries, **trace)
        if not text:
            # The model judged the question out of scope (grounded=false) but had nothing
            # to say. That is a policy refusal, not a model failure: answer 200 with the
            # delivered refusal text instead of turning a legitimate "no basis" into 503.
            return AgentAnswer(True, False, refusal_text(version), [], [], False,
                               generator, tool_calls, queries, **trace)
        return AgentAnswer(False, True, text, [], [], False, generator, tool_calls, queries, **trace)

    cited: list[Chunk] = []
    dropped: list[str] = []
    for raw in out.citation_ids:
        key = str(raw).strip()
        chunk = shown.get(key)
        if chunk is None:
            dropped.append(key)
        elif chunk not in cited:
            cited.append(chunk)
    answerable = bool(out.answerable and cited and clean_answer(out.answer))
    downgraded = bool(out.answerable and not answerable)
    if not answerable:
        return AgentAnswer(True, False, refusal_text(version), [], dropped, downgraded,
                           generator, tool_calls, queries, **trace)
    return AgentAnswer(True, True, clean_answer(out.answer), cited, dropped, False,
                       generator, tool_calls, queries, **trace,
                       confidence=out.confidence,
                       needs_clarification=bool(out.needs_clarification))


def _token_usage(response: Any) -> dict[str, Any] | None:
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage")
    if usage:
        return {"input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
                "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens")}
    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return {"input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens")}
    return None


class AgentUsageCallback(AsyncCallbackHandler):
    """Records every agent/LLM call into the AUTH-01 ledger (async handler)."""

    def __init__(self, meter: UsageMeter, model: str):
        self.meter = meter
        self.model = model
        self._started: dict[str, float] = {}

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):  # noqa: D102
        self._started[str(run_id)] = time.perf_counter()
        self.meter.check()

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):  # noqa: D102
        self._started[str(run_id)] = time.perf_counter()
        self.meter.check()

    async def on_llm_end(self, response, *, run_id, **kwargs):  # noqa: D102
        started = self._started.pop(str(run_id), None)
        latency_ms = int((time.perf_counter() - started) * 1000) if started else 0
        usage = _token_usage(response)
        if usage is None:
            logger.warning("agent model call returned no token usage; recording zero for %s", self.model)
        self.meter.record(purpose="policy_agent", model=self.model, usage=usage, latency_ms=latency_ms)


def build_search_tool(*, version: str, question: str, index: Index, enhanced: EnhancedRetriever,
                      optimizer: QueryOptimizer, reranker: CrossEncoderReranker,
                      links: dict[str, list[str]], trace: ToolTrace) -> BaseTool:
    """One tool instance per request: it closes over the version-specific index and trace."""

    @tool("search_policy", description=TOOL_PROMPT)
    async def search_policy(query: str) -> str:
        text = " ".join(str(query or "").split())
        if not text:
            return json.dumps({"version": version, "chunks": [],
                               "message": "查询为空，请传入一个完整的中文问题"}, ensure_ascii=False)
        plan = await optimizer.optimize(text)
        paths = query_paths(text, plan)
        candidates = await enhanced.search_multi(index, paths, k_per_query=CANDIDATES,
                                                 k_final=CANDIDATES)
        # The cross-encoder judges the user's actual question, not the rewrite.
        ranked = await reranker.rerank(question, [hit.chunk for hit in candidates.hits],
                                       top_k=RERANK_TOP_K)
        retrieval = Retrieval(
            f"{candidates.mode}+rerank",
            [Hit(item.chunk, rank, round(item.score, 6)) for rank, item in enumerate(ranked, start=1)],
            candidates.query_terms)
        retrieval = with_supplements(retrieval, index.chunks, links)
        shown: list[tuple[str, Chunk, str]] = []
        payload: list[dict[str, Any]] = []
        for hit in retrieval.hits:
            key = citation_key(hit.chunk)
            shown.append((key, hit.chunk, "retrieved"))
            payload.append({"citation_id": key, "clause": hit.chunk.clause,
                            "heading": hit.chunk.heading, "quote": hit.chunk.quote,
                            "kind": "retrieved", "score": hit.score})
        for chunk in retrieval.supplements:
            key = citation_key(chunk)
            shown.append((key, chunk, "supplement"))
            payload.append({"citation_id": key, "clause": chunk.clause,
                            "heading": chunk.heading, "quote": chunk.quote,
                            "kind": "supplement", "score": None})
        trace.calls.append(ToolCall(text, retrieval, shown, paths, bool(plan.split and len(paths) > 1)))
        return json.dumps({"version": version, "split": bool(plan.split and len(paths) > 1),
                           "queries": paths, "chunks": payload,
                           "message": ("只能依据上面的片段回答；引用时原样使用 citation_id。"
                                       if payload else "没有检索到相关制度条款。")},
                          ensure_ascii=False)

    return search_policy


@dataclass
class PolicyAgent:
    settings: Settings
    retriever: EnhancedRetriever
    optimizer: QueryOptimizer
    reranker: CrossEncoderReranker
    meter: UsageMeter

    async def ask(self, version: str, document_hash: str, question: str, document, *,
                  links: dict[str, list[str]] | None = None) -> tuple[AgentAnswer, Retrieval]:
        index = self.retriever.index(version, document_hash, document)
        trace = ToolTrace()
        search = build_search_tool(version=version, question=question, index=index,
                                   enhanced=self.retriever, optimizer=self.optimizer,
                                   reranker=self.reranker, links=links or {}, trace=trace)
        graph = create_react_agent(
            chat_model(self.settings, self.settings.text_model),
            ToolNode([search], handle_tool_errors=False),
            prompt=SYSTEM_PROMPT.format(version=version),
            response_format=EnhancedAnswerOut,
            name="policy_agent",
        )
        self.meter.check()
        callback = AgentUsageCallback(self.meter, self.settings.text_model)
        try:
            state = await graph.ainvoke(
                {"messages": [("human", question)]},
                config={"callbacks": [callback], "recursion_limit": 12})
        except ModelUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK/graph errors reduced to their type name
            raise ModelUnavailable("模型调用失败，请稍后重试", error_type=type(exc).__name__) from None

        raw = state.get("structured_response")
        if isinstance(raw, dict):
            try:
                out = EnhancedAnswerOut(**raw)
            except Exception:  # noqa: BLE001 - malformed model output is a parsing failure
                raise ModelUnavailable("模型输出无法解析为结构化结果",
                                       error_type="ParsingError") from None
        elif isinstance(raw, EnhancedAnswerOut):
            out = raw
        else:
            raise ModelUnavailable("模型输出无法解析为结构化结果", error_type="ParsingError")

        tool_calls = sum(len(getattr(message, "tool_calls", None) or [])
                         for message in state.get("messages", []))
        queries = [call.query for call in trace.calls]
        answer = finalize_agent(out, trace.shown(), version, self.settings.text_model,
                                tool_calls=tool_calls, queries=queries,
                                split=trace.split, paths=trace.paths())
        return answer, trace.combined()


def build_policy_agent(settings: Settings) -> PolicyAgent:
    meter = UsageMeter.from_settings(settings)
    base = Retriever(settings.agent_retrieval_mode, DashScopeEmbedder(settings, meter))
    return PolicyAgent(settings=settings, retriever=EnhancedRetriever(base),
                       optimizer=QueryOptimizer(settings, meter),
                       reranker=CrossEncoderReranker(settings, meter), meter=meter)
