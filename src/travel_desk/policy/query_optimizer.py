"""Query optimization for the enhanced policy-agent chain.

One metered structured call both decides whether a question needs splitting and
rewrites it into retrieval queries. Simple questions return `split=false` and one
rewritten query; compound questions return 2-4 sub-queries, one per policy clause.

The original question is never replaced: `EnhancedRetriever` always searches it as a
separate path, so a bad rewrite can only add candidates — it cannot lose the ones the
delivered retriever would have found.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.llm.dashscope import UsageMeter, structured_call

MAX_QUERIES = 4
MAX_QUERY_CHARS = 200

SYSTEM_PROMPT = "你是差旅制度检索专家。你的任务是把口语化的问题改写成检索关键词，不要回答问题。"

REWRITE_PROMPT = """先把下面的问题改写成适合检索公司差旅制度文档的关键词，并判断是否需要拆分。

规则：
1. 简单问题（只涉及一个制度条款）：split 为 false，queries 只放 1 条改写结果。
2. 复杂问题（同时问多个条款、多个城市/角色/费用，或需要组合多个条款才能计算）：split 为 true，
   拆成 2-4 个相互独立的子查询，每个子查询只覆盖一个制度主题，并分别改写成关键词。
3. 保留问题中的关键实体：城市、角色、金额、天数、舱位、费用类型等；补上制度里的主题词
   （例如住宿标准、伙食补贴、交通标准、审批、自动通过、出差时长、其他费用）。
4. 用空格分隔关键词，不要解释，不要编造制度内容，不要重复子查询。

问题：{question}
"""


class QueryPlan(BaseModel):
    split: bool = Field(default=False, description="问题是否被拆成多个检索查询")
    queries: list[str] = Field(default_factory=list, description="用于检索的查询，按优先级排序")


class QueryOptimizer:
    """Rewrite (Phase 1) and, in Phase 4, split a question into retrieval queries."""

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.rewrite_model

    async def optimize(self, question: str) -> QueryPlan:
        out: QueryPlan = await structured_call(
            self.settings, self.meter, purpose="policy_query_rewrite", model=self.model,
            schema=QueryPlan,
            messages=[("system", SYSTEM_PROMPT), ("human", REWRITE_PROMPT.format(question=question))])
        queries, seen = [], set()
        for raw in out.queries:
            query = " ".join(str(raw).split())[:MAX_QUERY_CHARS]
            if query and query not in seen:
                seen.add(query)
                queries.append(query)
        if not out.split:
            queries = queries[:1]
        if not queries:  # an empty rewrite must not lose the search path
            queries = [" ".join(question.split())[:MAX_QUERY_CHARS]]
        return QueryPlan(split=bool(out.split), queries=queries[:MAX_QUERIES])
