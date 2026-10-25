"""Enhanced policy-agent chain (Phase 1: rewrite + multi-query retrieval).

The delivered `/api/policy/ask` path is covered by `test_policy_qa.py`; these tests
only exercise the new modules, with stubs where a real model/embedding would be used
(HC-06: fake tests never touch the network).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from langchain_core.messages import AIMessage, ToolMessage

from travel_desk.config import Settings
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter
from travel_desk.policy import documents, query_optimizer, rag, rag_agent, synonyms
from travel_desk.policy.enhanced_retriever import EnhancedRetriever, query_paths
from travel_desk.policy.query_optimizer import QueryOptimizer, QueryPlan
from travel_desk.policy.rag import Hit, Retrieval, Retriever, refusal_text
from travel_desk.policy.rag_agent import (AgentAnswer, AgentUsageCallback, EnhancedAnswerOut, PolicyAgent,
                                          citation_key, finalize_agent)
from travel_desk.policy.reranker import CrossEncoderReranker, RankedChunk


class CharEmbedder:
    """Deterministic 18-dim character-count vectors (same idea as test_policy_qa)."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def embed(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        self.calls.append((purpose, len(texts)))
        alphabet = "住宿伙食补贴机票提前出租车经理城市天元"
        return [[float(t.count(ch)) + 0.01 for ch in alphabet] for t in texts]


def doc(settings: Settings):
    return documents.load(settings.policies_dir / "v1.2.md", documents.kind_of_path("v1.2.md"))


def meter(settings: Settings) -> UsageMeter:
    return UsageMeter(settings.data_dir / "usage.jsonl", settings.cost_ledger_path)


def local_meter(settings: Settings) -> UsageMeter:
    ledger = settings.data_dir / "ledger.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("<!-- manual_total_cny: 0 -->\n", encoding="utf-8")
    return UsageMeter(settings.data_dir / "rerank-usage.jsonl", ledger)


def chunks(settings: Settings):
    return rag.chunk_document("v1.2", doc(settings))


class FakeReRankCall:
    def __init__(self, results, *, status_code=200, total_tokens=77):
        self.results = results
        self.status_code = status_code
        self.total_tokens = total_tokens
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status_code=self.status_code,
                               output=SimpleNamespace(results=[
                                   SimpleNamespace(index=i, relevance_score=s) for i, s in self.results]),
                               usage=SimpleNamespace(total_tokens=self.total_tokens))


def test_agent_config_defaults_and_validation(settings: Settings, monkeypatch):
    from travel_desk.config import ConfigError

    assert settings.agent_retrieval_mode == "hybrid"
    assert settings.rerank_min_score == 0.06
    monkeypatch.setenv("TRAVEL_DESK_AGENT_RETRIEVAL", "dense")
    with pytest.raises(ConfigError, match="TRAVEL_DESK_AGENT_RETRIEVAL"):
        Settings.from_env()


def stub_structured(monkeypatch, plan: QueryPlan, calls: list[dict[str, Any]]):
    async def fake(settings, meter, *, purpose, model, schema, messages, timeout=60.0):
        calls.append({"purpose": purpose, "model": model, "schema": schema, "messages": messages})
        return plan

    monkeypatch.setattr(query_optimizer, "structured_call", fake)


async def test_rewrite_is_one_metered_structured_call(settings: Settings, monkeypatch):
    calls: list[dict[str, Any]] = []
    stub_structured(monkeypatch, QueryPlan(split=False, queries=["住宿 酒店 每晚 上限 报销"]), calls)
    out = await QueryOptimizer(settings, meter(settings)).optimize("住酒店一晚最多报多少")

    assert out == QueryPlan(split=False, queries=["住宿 酒店 每晚 上限 报销"])
    assert len(calls) == 1
    assert calls[0]["purpose"] == "policy_query_rewrite"
    assert calls[0]["model"] == settings.rewrite_model
    assert calls[0]["schema"] is QueryPlan
    assert "住酒店一晚最多报多少" in calls[0]["messages"][1][1]


async def test_rewrite_normalizes_queries_and_falls_back_to_the_original(settings: Settings, monkeypatch):
    calls: list[dict[str, Any]] = []
    stub_structured(monkeypatch, QueryPlan(split=False, queries=[" 住宿 ", "住宿", "餐补"]), calls)
    out = await QueryOptimizer(settings, meter(settings)).optimize("住酒店怎么报")
    assert out.queries == ["住宿"]  # a simple question keeps exactly one path

    stub_structured(monkeypatch, QueryPlan(split=False, queries=["", "   "]), calls)
    out = await QueryOptimizer(settings, meter(settings)).optimize("  住酒店   怎么报  ")
    assert out.queries == ["住酒店 怎么报"]


async def test_rewrite_caps_split_queries(settings: Settings, monkeypatch):
    calls: list[dict[str, Any]] = []
    stub_structured(monkeypatch, QueryPlan(split=True, queries=["a", "b", "c", "d", "e"]), calls)
    out = await QueryOptimizer(settings, meter(settings)).optimize("复杂问题")
    assert out == QueryPlan(split=True, queries=["a", "b", "c", "d"])


def test_query_paths_keep_the_original_first_and_dedupe():
    plan = QueryPlan(split=True, queries=["住宿 上限", "住宿 上限", "餐补"])
    assert query_paths("住酒店能报多少", plan) == ["住酒店能报多少", "住宿 上限", "餐补"]


async def test_multi_query_retrieval_keeps_original_top5_and_adds_rewrite_candidates(settings: Settings):
    retriever = Retriever("hybrid", CharEmbedder())
    enhanced = EnhancedRetriever(retriever)
    index = enhanced.index("v1.2", "hash", doc(settings))

    original = "住酒店能报多少"
    rewrite = "住宿 上限 报销"
    base = await retriever.search(index, original)
    fused = await enhanced.search_multi(index, [original, rewrite])

    assert fused.mode == "hybrid+multi"
    assert base.hits[0].chunk.clause != "POL-2"  # the colloquial wording alone misses the clause
    assert [h.chunk.id for h in fused.hits[:5]] == [h.chunk.id for h in base.hits]  # no eviction
    assert "POL-1" not in [h.chunk.clause for h in base.hits]
    assert "POL-1" in [h.chunk.clause for h in fused.hits[5:]]  # the rewrite supplies a new candidate
    assert [h.rank for h in fused.hits] == list(range(1, len(fused.hits) + 1))
    assert [h.score for h in fused.hits] == sorted((h.score for h in fused.hits), reverse=True)


async def test_original_path_order_is_authoritative(settings: Settings):
    retriever = Retriever("hybrid", CharEmbedder())
    enhanced = EnhancedRetriever(retriever)
    index = enhanced.index("v1.2", "hash", doc(settings))
    original = "住宿"
    fused = await enhanced.search_multi(index, [original, "伙食补贴", "出租车"])
    base = await retriever.search(index, original)
    assert [h.chunk.id for h in fused.hits[:5]] == [h.chunk.id for h in base.hits]


async def test_multi_query_retrieval_dedupes_by_chunk_id(settings: Settings):
    retriever = Retriever("bm25", None)
    enhanced = EnhancedRetriever(retriever)
    index = enhanced.index("v1.2", "hash", doc(settings))
    fused = await enhanced.search_multi(index, ["住宿", "住宿费 上限", "住宿"], k_final=10)
    ids = [hit.chunk.id for hit in fused.hits]
    assert len(ids) == len(set(ids))
    assert fused.mode == "bm25+multi"


async def test_multi_query_retrieval_requires_a_query(settings: Settings):
    enhanced = EnhancedRetriever(Retriever("bm25", None))
    index = enhanced.index("v1.2", "hash", doc(settings))
    with pytest.raises(ValueError):
        await enhanced.search_multi(index, ["  ", ""])


# ------------------------------------------------------------------ synonyms + RRF (Phase 4)


def test_synonyms_expand_both_directions_without_recursion():
    assert "住宿" in synonyms.tokenize_with_synonyms("住酒店")
    assert "酒店" in synonyms.tokenize_with_synonyms("住宿标准")
    assert "酒店" in synonyms.tokenize_with_synonyms("酒店")
    assert "住宿" in synonyms.synonyms_of("酒店")


async def test_synonym_path_recalls_the_clause_the_colloquial_word_misses(settings: Settings):
    enhanced = EnhancedRetriever(Retriever("bm25", None))
    index = enhanced.index("v1.2", "hash", doc(settings))
    assert await enhanced.base.search(index, "住酒店") == Retrieval("bm25", [], ["住", "酒店"])
    fused = await enhanced.search_multi(index, ["住酒店"])
    assert "POL-2" in [hit.chunk.clause for hit in fused.hits]
    assert "住宿" in fused.query_terms


async def test_synonym_path_can_be_disabled_for_the_ablation(settings: Settings):
    enhanced = EnhancedRetriever(Retriever("bm25", None), use_synonyms=False)
    index = enhanced.index("v1.2", "hash", doc(settings))
    fused = await enhanced.search_multi(index, ["住酒店"])
    assert fused.hits == [] and fused.query_terms == ["住", "酒店"]


class _PathRetriever:
    mode = "fake"

    def __init__(self, results):
        self.results = results

    async def search(self, index, question, *, k=10):
        return self.results[question]


async def test_alternative_paths_sum_their_weighted_rrf_scores(settings: Settings):
    chunks = rag.chunk_document("v1.2", doc(settings))
    pol2 = next(c for c in chunks if c.clause == "POL-2")
    pol3 = next(c for c in chunks if c.clause == "POL-3")
    pol4 = next(c for c in chunks if c.clause == "POL-4")
    base = _PathRetriever({
        "q-original": Retrieval("fake", [Hit(pol2, 1, 9.0)], []),
        "q-rewrite-a": Retrieval("fake", [Hit(pol3, 1, 5.0), Hit(pol4, 2, 4.0)], []),
        "q-rewrite-b": Retrieval("fake", [Hit(pol3, 2, 3.0)], []),
    })
    enhanced = EnhancedRetriever(base)
    index = Retriever("bm25", None).index("v1.2", "hash", doc(settings))
    fused = await enhanced.search_multi(
        index, ["q-original", "q-rewrite-a", "q-rewrite-b"], k_final=10)

    # The same alternative chunk found by two paths outranks a single-path chunk.
    assert [hit.chunk.clause for hit in fused.hits[1:3]] == ["POL-3", "POL-4"]
    assert fused.hits[1].score > fused.hits[2].score
    # Pinned original path still leads regardless of its fusion score.
    assert fused.hits[0].chunk == pol2


# ------------------------------------------------------------------ reranker (Phase 2)


async def test_rerank_sorts_filters_and_meters(settings: Settings, monkeypatch):
    import dashscope

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    candidates = chunks(settings)[:4]
    fake = FakeReRankCall([(2, 0.1), (0, 0.9), (1, 0.5)])
    monkeypatch.setattr(dashscope.TextReRank, "call", fake)
    meter = local_meter(settings)
    reranker = CrossEncoderReranker(settings, meter)

    ranked = await reranker.rerank("住宿标准", candidates, top_k=2)
    assert [(r.chunk.id, r.score) for r in ranked] == [(candidates[0].id, 0.9), (candidates[1].id, 0.5)]
    call = fake.calls[0]
    assert call["model"] == settings.rerank_model
    assert call["query"] == "住宿标准"
    assert call["documents"] == [c.search_text for c in candidates]
    assert call["top_n"] == len(candidates)
    assert call["api_key"] == "test-key"

    entry = json.loads((settings.data_dir / "rerank-usage.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (entry["purpose"], entry["model"], entry["total_tokens"]) == (
        "policy_rerank", settings.rerank_model, 77)


async def test_rerank_can_drop_everything_below_the_threshold(settings: Settings, monkeypatch):
    import dashscope

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    fake = FakeReRankCall([(0, 0.05), (1, 0.0)])
    monkeypatch.setattr(dashscope.TextReRank, "call", fake)
    ranked = await CrossEncoderReranker(settings, local_meter(settings)).rerank(
        "年假", chunks(settings)[:2])
    assert ranked == []


async def test_rerank_without_candidates_makes_no_call(settings: Settings, monkeypatch):
    import dashscope

    def explode(**kwargs):
        raise AssertionError("must not call the API without candidates")

    monkeypatch.setattr(dashscope.TextReRank, "call", explode)
    assert await CrossEncoderReranker(settings, local_meter(settings)).rerank("x", []) == []


async def test_rerank_missing_key_fails_closed_without_metering(settings: Settings, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    reranker = CrossEncoderReranker(settings, local_meter(settings))
    with pytest.raises(ModelUnavailable) as missing:
        await reranker.rerank("住宿", chunks(settings)[:2])
    assert missing.value.retryable is False
    assert not (settings.data_dir / "rerank-usage.jsonl").exists()


async def test_rerank_http_error_is_safe(settings: Settings, monkeypatch):
    import dashscope

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    fake = FakeReRankCall([], status_code=429)
    monkeypatch.setattr(dashscope.TextReRank, "call", fake)
    with pytest.raises(ModelUnavailable) as failed:
        await CrossEncoderReranker(settings, local_meter(settings)).rerank("住宿", chunks(settings)[:2])
    assert failed.value.error_type == "RerankHTTP429"
    assert "sk-" not in failed.value.reason


async def test_rerank_shape_error_is_safe(settings: Settings, monkeypatch):
    import dashscope

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(dashscope.TextReRank, "call", FakeReRankCall([(9, 0.9)]))
    with pytest.raises(ModelUnavailable) as malformed:
        await CrossEncoderReranker(settings, local_meter(settings)).rerank("住宿", chunks(settings)[:2])
    assert malformed.value.error_type == "RerankShape"


# ------------------------------------------------------------------ agent (Phase 3)


def policy_chunks(settings: Settings):
    return chunks(settings)


def shown_chunk(settings: Settings) -> rag.Chunk:
    return next(c for c in policy_chunks(settings) if c.clause == "POL-2")


def test_agent_finalize_accepts_only_keys_a_tool_showed(settings: Settings):
    chunk = shown_chunk(settings)
    key = citation_key(chunk)
    out = EnhancedAnswerOut(grounded=True, answerable=True, answer="A 类城市每晚 500 元。",
                            citation_ids=[key, "POL-9@line:99"])
    answer = finalize_agent(out, {key: chunk}, "v1.2", "test-model", tool_calls=1, queries=["住宿"])
    assert (answer.grounded, answer.answerable) == (True, True)
    assert answer.citations == [chunk]
    assert answer.dropped_citation_ids == ["POL-9@line:99"]
    assert answer.tool_calls == 1 and answer.queries == ["住宿"]


def test_agent_finalize_downgrades_an_uncited_policy_answer(settings: Settings):
    out = EnhancedAnswerOut(grounded=True, answerable=True, answer="500 元", citation_ids=[])
    answer = finalize_agent(out, {}, "v1.2", "test-model", tool_calls=1, queries=[])
    assert (answer.grounded, answer.answerable, answer.downgraded) == (True, False, True)
    assert answer.answer == refusal_text("v1.2") and answer.citations == []


def test_agent_finalize_keeps_chat_out_of_the_policy_refusal_path(settings: Settings):
    out = EnhancedAnswerOut(grounded=False, answerable=False, answer="你好，我是差旅助手。",
                            citation_ids=["POL-2@line:13"])
    answer = finalize_agent(out, {}, "v1.2", "test-model", tool_calls=0, queries=[])
    assert (answer.grounded, answer.answerable, answer.answer) == (False, True, "你好，我是差旅助手。")
    assert answer.citations == [] and not answer.downgraded


def test_agent_finalize_turns_an_empty_out_of_scope_answer_into_a_refusal():
    """A model that says grounded=false with no text has no basis, not a provider fault."""
    out = EnhancedAnswerOut(grounded=False, answerable=False, answer="  ", citation_ids=[])
    answer = finalize_agent(out, {}, "v1.2", "test-model", tool_calls=0, queries=[])
    assert (answer.grounded, answer.answerable) == (True, False)
    assert answer.answer == refusal_text("v1.2")


def test_agent_finalize_does_not_accept_an_ungrounded_answer_after_a_tool_call(settings: Settings):
    """A searched policy question that comes back as "chat" is an uncited policy answer."""
    out = EnhancedAnswerOut(grounded=False, answerable=True,
                            answer="制度里没有加班费的规定，建议咨询 HR。", citation_ids=[])
    answer = finalize_agent(out, {}, "v1.2", "test-model", tool_calls=1, queries=["加班费"])
    assert (answer.grounded, answer.answerable, answer.downgraded) == (True, False, True)
    assert answer.answer == refusal_text("v1.2") and answer.citations == []


def test_agent_finalize_keeps_a_clarifying_question_after_a_tool_call(settings: Settings):
    out = EnhancedAnswerOut(grounded=False, answerable=True, needs_clarification=True,
                            answer="请问您去哪个城市出差？员工级别是什么？", citation_ids=[])
    answer = finalize_agent(out, {}, "v1.2", "test-model", tool_calls=1, queries=["住宿标准"])
    assert (answer.grounded, answer.answerable, answer.needs_clarification) == (False, True, True)
    assert answer.answer.startswith("请问") and answer.citations == []
    assert answer.confidence == 0.0


def test_agent_finalize_carries_confidence_for_a_cited_answer(settings: Settings):
    chunk = shown_chunk(settings)
    key = citation_key(chunk)
    out = EnhancedAnswerOut(grounded=True, answerable=True, answer="每晚 500 元。",
                            citation_ids=[key], confidence=0.87)
    answer = finalize_agent(out, {key: chunk}, "v1.2", "test-model",
                            tool_calls=1, queries=["住宿"])
    assert answer.confidence == 0.87 and answer.needs_clarification is False
    with pytest.raises(ValueError):
        EnhancedAnswerOut(grounded=True, answerable=True, answer="x", confidence=1.2)


def test_agent_finalize_strips_inline_citation_keys(settings: Settings):
    chunk = shown_chunk(settings)
    key = citation_key(chunk)
    out = EnhancedAnswerOut(
        grounded=True, answerable=True,
        answer=f"上海属于 A 类城市【citation_id: POL-1@line:9】，普通员工每晚最多 500 元【{key}】。",
        citation_ids=[key])
    answer = finalize_agent(out, {key: chunk}, "v1.2", "test-model", tool_calls=1, queries=[])
    assert "citation_id" not in answer.answer and "POL-1@" not in answer.answer
    assert answer.answer == "上海属于 A 类城市，普通员工每晚最多 500 元。"


class StubOptimizer:
    async def optimize(self, question: str) -> QueryPlan:
        return QueryPlan(split=False, queries=[question])


class KeepReranker:
    async def rerank(self, query: str, candidates, *, top_k: int = 3):
        return [RankedChunk(chunk, round(1.0 - index * 0.1, 3))
                for index, chunk in enumerate(candidates[:top_k])]


class FakeGraph:
    def __init__(self, handler):
        self.handler = handler
        self.config = None

    async def ainvoke(self, state, config=None):
        self.config = config
        return await self.handler(state)


def install_agent_graph(monkeypatch, handler, captured: dict[str, Any]) -> None:
    def factory(model, tools, *, prompt, response_format, name):
        captured.update({"model": model, "tools": tools, "prompt": prompt,
                         "response_format": response_format, "name": name})

        async def run(state):
            return await handler(tools.tools_by_name["search_policy"], response_format)

        return FakeGraph(run)

    monkeypatch.setattr(rag_agent, "create_react_agent", factory)
    monkeypatch.setattr(rag_agent, "chat_model", lambda *args, **kwargs: object())


async def agent_engine(settings: Settings, monkeypatch, handler, captured=None) -> PolicyAgent:
    install_agent_graph(monkeypatch, handler, captured if captured is not None else {})
    return PolicyAgent(settings=settings, retriever=EnhancedRetriever(Retriever("bm25", None)),
                       optimizer=StubOptimizer(), reranker=KeepReranker(), meter=local_meter(settings))


async def test_agent_ask_runs_the_tool_and_returns_its_citation(settings: Settings, monkeypatch):
    captured: dict[str, Any] = {}

    async def handler(tool, schema):
        payload = json.loads(await tool.ainvoke({"query": "住宿标准"}))
        keys = [item["citation_id"] for item in payload["chunks"]]
        return {"messages": [
            AIMessage(content="", tool_calls=[{"name": "search_policy", "args": {"query": "住宿标准"},
                                               "id": "call-1"}]),
            ToolMessage(content="...", tool_call_id="call-1"),
            AIMessage(content=""),
        ], "structured_response": schema(grounded=True, answerable=True,
                                         answer="A 类城市每晚最多 500 元。", citation_ids=keys[:1])}

    engine = await agent_engine(settings, monkeypatch, handler, captured)
    answer, retrieval = await engine.ask("v1.2", "hash", "住宿标准是多少", doc(settings),
                                         links={"POL-2": ["POL-1"]})

    assert isinstance(answer, AgentAnswer)
    assert (answer.grounded, answer.answerable, answer.tool_calls) == (True, True, 1)
    assert answer.citations and answer.citations[0].clause == "POL-2"
    assert answer.queries == ["住宿标准"]
    assert retrieval.hits and retrieval.mode.endswith("+rerank")
    assert any(c.clause == "POL-1" for c in retrieval.supplements)
    assert captured["response_format"] is EnhancedAnswerOut
    assert captured["name"] == "policy_agent"
    assert "v1.2" in captured["prompt"]


async def test_agent_ask_downgrades_an_invented_citation(settings: Settings, monkeypatch):
    async def handler(tool, schema):
        return {"messages": [AIMessage(content="")],
                "structured_response": schema(grounded=True, answerable=True, answer="每晚 999 元。",
                                              citation_ids=["POL-9@line:99"])}

    engine = await agent_engine(settings, monkeypatch, handler)
    answer, _ = await engine.ask("v1.2", "hash", "住宿标准", doc(settings))
    assert answer.answerable is False and answer.downgraded is True
    assert answer.answer == refusal_text("v1.2")
    assert answer.dropped_citation_ids == ["POL-9@line:99"]


async def test_agent_ask_answers_chat_without_a_tool_call(settings: Settings, monkeypatch):
    async def handler(tool, schema):
        return {"messages": [AIMessage(content="你好")],
                "structured_response": schema(grounded=False, answerable=True,
                                              answer="你好，我是差旅制度助手。", citation_ids=[])}
    engine = await agent_engine(settings, monkeypatch, handler)
    answer, retrieval = await engine.ask("v1.2", "hash", "你好", doc(settings))
    assert (answer.grounded, answer.answerable, answer.citations) == (False, True, [])
    assert answer.tool_calls == 0 and retrieval.mode == "agent:none"


async def test_agent_ask_grounded_without_a_tool_call_is_refused(settings: Settings, monkeypatch):
    async def handler(tool, schema):
        return {"messages": [AIMessage(content="")],
                "structured_response": schema(grounded=True, answerable=True, answer="500 元",
                                              citation_ids=["POL-2@line:13"])}
    engine = await agent_engine(settings, monkeypatch, handler)
    answer, _ = await engine.ask("v1.2", "hash", "住宿标准", doc(settings))
    assert answer.tool_calls == 0 and answer.answerable is False
    assert answer.answer == refusal_text("v1.2")


async def test_agent_usage_callback_records_tokens(settings: Settings):
    meter = local_meter(settings)
    callback = AgentUsageCallback(meter, "test-model")
    await callback.on_chat_model_start({}, [], run_id="run-1")
    await callback.on_llm_end(SimpleNamespace(
        llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        generations=[]), run_id="run-1")
    entry = json.loads((settings.data_dir / "rerank-usage.jsonl").read_text(encoding="utf-8").strip())
    assert (entry["purpose"], entry["model"], entry["total_tokens"]) == (
        "policy_agent", "test-model", 15)


async def test_agent_usage_callback_reads_usage_metadata(settings: Settings):
    meter = local_meter(settings)
    callback = AgentUsageCallback(meter, "test-model")
    await callback.on_llm_start({}, [], run_id="run-2")
    await callback.on_llm_end(SimpleNamespace(
        llm_output=None,
        generations=[[SimpleNamespace(message=SimpleNamespace(
            usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}))]]
    ), run_id="run-2")
    entry = json.loads((settings.data_dir / "rerank-usage.jsonl").read_text(encoding="utf-8").strip())
    assert (entry["input_tokens"], entry["output_tokens"], entry["total_tokens"]) == (7, 3, 10)
