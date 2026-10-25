"""`POST /api/policy/ask-agent`: the enhanced agent chain, isolated from `/ask`.

Real mode only. Fake/recorded mode falls back to the delivered BM25 + extractive
engine (HC-06: no network), and the response keeps the delivered generator label.
Version resolution, citations, refusals and the audit event are shared with `/ask`
(`services.policy_qa`), so both endpoints answer from the same published/visible
version and leave the same `policy_question` evidence.
"""
from __future__ import annotations

from typing import Any

from travel_desk.config import Settings
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.policy.rag import PolicyQA, Retrieval
from travel_desk.services import policy_qa
from travel_desk.services.errors import ServiceError


def build_agent_engine(settings: Settings):
    """The real-mode engine, or None so the endpoint falls back to the delivered chain."""
    if settings.llm_mode != "real":
        return None
    # Imported lazily: fake/recorded mode never loads LangChain/LangGraph.
    from travel_desk.policy.rag_agent import build_policy_agent

    return build_policy_agent(settings)


def _response(version: str, answer, retrieval: Retrieval) -> dict[str, Any]:
    return {
        "answer": answer.answer, "answerable": answer.answerable, "grounded": answer.grounded,
        "confidence": answer.confidence, "needs_clarification": answer.needs_clarification,
        "citations": [policy_qa.citation(version, c) for c in answer.citations],
        "version": version, "source": "model", "generator": answer.generator,
        "retrieval": {"mode": retrieval.mode,
                      "chunks": [{"rank": h.rank, "clause": h.chunk.clause, "locator": h.chunk.locator,
                                  "score": h.score} for h in retrieval.hits],
                      "supplements": [{"clause": c.clause, "locator": c.locator, "reason": "rule_dependency"}
                                      for c in retrieval.supplements]},
        "agent": {"mode": "react", "tool_calls": answer.tool_calls, "queries": answer.queries,
                  "split": answer.split, "paths": answer.paths},
    }


async def ask_agent(settings: Settings, engine, legacy: PolicyQA, actor: dict[str, Any], body: Any, *,
                    trace_id: str) -> dict[str, Any]:
    if settings.llm_mode != "real" or engine is None:
        response = await policy_qa.ask(settings, legacy, actor, body, trace_id=trace_id)
        response["agent"] = {"mode": "fallback", "llm_mode": settings.llm_mode}
        # The delivered chain only answers policy questions (or refuses), so the
        # agent-contract fields have a single honest value here.
        response["grounded"] = True
        response.setdefault("confidence", 0.0)
        response.setdefault("needs_clarification", False)
        return response

    question, requested = policy_qa.check_ask(body)
    version, document_hash, document, links = policy_qa._resolve_version(settings, actor, requested)
    try:
        answer, retrieval = await engine.ask(version, document_hash, question, document, links=links)
    except ModelUnavailable as exc:
        policy_qa._audit_error(settings, version=version, trace_id=trace_id,
                               stage="policy_agent", exc=exc)
        raise ServiceError(503, "MODEL_UNAVAILABLE", exc.reason,
                           {"retryable": exc.retryable, "stage": "policy_agent"}) from None

    policy_qa._audit(settings, actor, version=version, requested=requested, question=question,
                     answer=answer, retrieval=retrieval, trace_id=trace_id,
                     extra={"chain": "agent", "grounded": answer.grounded,
                            "tool_calls": answer.tool_calls, "queries": answer.queries,
                            "split": answer.split, "paths": answer.paths,
                            "confidence": answer.confidence,
                            "needs_clarification": answer.needs_clarification})
    return _response(version, answer, retrieval)
