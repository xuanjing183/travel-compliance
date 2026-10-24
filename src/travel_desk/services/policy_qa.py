"""Policy Q&A (#18, REQ-004): answers from one policy version, with code-checked citations.

Order: 401 (route) → 422 body/question/version format → 404 version (unknown, or a
draft for a non-admin) → 503 model/embedding unavailable. The default version is the
published one; archived versions can be asked explicitly (Trips stay bound to theirs).

Every answer — including refusals — is audited as `policy_question` (trip_id=null)
with the retrieval mode, the retrieved clauses, the generator and what code dropped.
"""
from __future__ import annotations

from typing import Any

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.policy.rag import Answer, PolicyQA, Retrieval, clause_links
from travel_desk.services import audit
from travel_desk.services.errors import ServiceError
from travel_desk.services.policies import VERSION_RE, _document_of, _format_error, _rules, visible_version

MAX_QUESTION = 500
ASK_FIELDS = {"question", "version"}
AUDIT_QUESTION_CHARS = 200


def check_ask(body: Any) -> tuple[str, str | None]:
    if not isinstance(body, dict):
        raise ServiceError(422, "VALIDATION_ERROR", "请求体必须是 JSON 对象", {})
    unknown = sorted(set(body) - ASK_FIELDS)
    if unknown:
        raise ServiceError(422, "VALIDATION_ERROR", "只接受 question 和 version 字段",
                           {"errors": [{"loc": ["body", name], "type": "extra_forbidden",
                                        "msg": "not allowed"} for name in unknown]})
    question = body.get("question")
    if not isinstance(question, str) or not question.strip() or len(question.strip()) > MAX_QUESTION:
        raise _format_error("question", f"non-empty string <= {MAX_QUESTION}",
                            f"question 必须是 1～{MAX_QUESTION} 字的问题")
    version = body.get("version")
    if version is not None and (not isinstance(version, str) or not VERSION_RE.fullmatch(version.strip())):
        raise _format_error("version", "pattern v<major>[.<minor>…][-tag]",
                            "version 格式应为 v1.2 这样的版本号")
    return " ".join(question.split()), version.strip() if isinstance(version, str) else None


def citation(version: str, chunk) -> dict[str, Any]:
    return {"version": version, "clause": chunk.clause, "quote": chunk.quote, "locator": chunk.locator,
            "source": "policy"}


def _response(version: str, answer: Answer, retrieval: Retrieval) -> dict[str, Any]:
    return {
        "answer": answer.answer, "answerable": answer.answerable,
        "citations": [citation(version, c) for c in answer.citations],
        "version": version, "source": "model", "generator": answer.generator,
        "retrieval": {"mode": retrieval.mode,
                      "chunks": [{"rank": h.rank, "clause": h.chunk.clause, "locator": h.chunk.locator,
                                  "score": h.score} for h in retrieval.hits],
                      "supplements": [{"clause": c.clause, "locator": c.locator, "reason": "rule_dependency"}
                                      for c in retrieval.supplements]},
    }


async def ask(settings: Settings, engine: PolicyQA, actor: dict[str, Any], body: Any, *,
              trace_id: str) -> dict[str, Any]:
    question, requested = check_ask(body)
    with read_session(settings.db_path) as conn:
        if requested is None:
            row = conn.execute("SELECT * FROM policy_versions WHERE status = 'published'").fetchone()
            if row is None:
                raise ServiceError(404, "POLICY_VERSION_NOT_FOUND", "当前没有已发布的制度版本",
                                   {"version": None})
        else:
            row = visible_version(conn, requested, actor)
        version, document_hash = row["version"], row["document_hash"]
        document = _document_of(settings, row)
        links = clause_links(_rules(conn, version))

    refs = [{"kind": "policy", "id": version}]
    try:
        answer, retrieval = await engine.ask(version, document_hash, document, question, links=links)
    except ModelUnavailable as exc:
        with db_session(settings.db_path) as conn:
            audit.record(conn, trip_id=None, revision=None, actor="system", event_type="error",
                         payload={"stage": "policy_qa", "version": version, "message": exc.reason,
                                  "error_type": exc.error_type, "retryable": exc.retryable},
                         trace_id=trace_id, refs=refs)
        raise ServiceError(503, "MODEL_UNAVAILABLE", exc.reason,
                           {"retryable": exc.retryable, "stage": "policy_qa"}) from None

    with db_session(settings.db_path) as conn:
        audit.record(conn, trip_id=None, revision=None, actor=actor["id"], event_type="policy_question",
                     payload={"version": version, "requested_version": requested,
                              "question": question[:AUDIT_QUESTION_CHARS],
                              "question_truncated": len(question) > AUDIT_QUESTION_CHARS,
                              "answerable": answer.answerable, "generator": answer.generator,
                              "retrieval_mode": retrieval.mode,
                              "retrieved": [{"rank": h.rank, "clause": h.chunk.clause,
                                             "locator": h.chunk.locator} for h in retrieval.hits],
                              "supplements": [{"clause": c.clause, "locator": c.locator}
                                              for c in retrieval.supplements],
                              "cited": [{"clause": c.clause, "locator": c.locator} for c in answer.citations],
                              "dropped_citation_ids": answer.dropped_citation_ids,
                              "downgraded": answer.downgraded},
                     trace_id=trace_id, refs=refs)
    return _response(version, answer, retrieval)
