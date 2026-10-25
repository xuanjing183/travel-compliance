"""`POST /api/policy/ask-agent`: fallback contract and the real-mode response/audit.

The agent itself is stubbed here: its retrieval quality is measured by the paraphrase
evaluation, and its tool/finalize logic is covered by `test_policy_agent.py`. These
tests pin the HTTP boundary — old fields preserved, `grounded` for chat, fallback in
fake mode, and the same `policy_question`/`error` audit evidence as `/ask`.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient
from test_api import ZHANG, error_code, h

from travel_desk.config import Settings
from travel_desk.db.database import read_session
from travel_desk.llm.dashscope import ModelUnavailable
from travel_desk.main import create_app
from travel_desk.policy import documents, rag
from travel_desk.policy.rag import Hit, Retrieval
from travel_desk.policy.rag_agent import AgentAnswer


def ask_agent(client: TestClient, body: Any):
    return client.post("/api/policy/ask-agent", json=body, headers=h(ZHANG))


def sample(settings: Settings):
    document = documents.load(settings.policies_dir / "v1.2.md", documents.kind_of_path("v1.2.md"))
    return document, rag.chunk_document("v1.2", document)


def audit_rows(settings: Settings, event_type: str) -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_events WHERE type = ? ORDER BY seq", (event_type,)).fetchall()
    return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]


class FakeEngine:
    def __init__(self, answer: AgentAnswer | None = None, retrieval: Retrieval | None = None,
                 error: ModelUnavailable | None = None):
        self.answer = answer
        self.retrieval = retrieval
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def ask(self, version: str, document_hash: str, question: str, document, *, links=None):
        self.calls.append({"version": version, "question": question, "links": links})
        if self.error is not None:
            raise self.error
        return self.answer, self.retrieval


def real_client(settings: Settings, engine: FakeEngine) -> tuple[Settings, TestClient]:
    real = replace(settings, llm_mode="real")
    app = create_app(real)
    app.state.policy_agent = engine
    return real, TestClient(app)


def test_fake_mode_falls_back_to_the_delivered_chain(client: TestClient):
    response = ask_agent(client, {"question": "员工去上海出差，住宿每晚最多能报多少？"})
    assert response.status_code == 200
    body = response.json()
    assert (body["generator"], body["source"], body["answerable"]) == ("fake:extractive", "model", True)
    assert (body["grounded"], body["confidence"], body["needs_clarification"]) == (True, 0.0, False)
    assert body["agent"] == {"mode": "fallback", "llm_mode": "fake"}
    assert "500 元" in body["answer"]
    assert body["citations"] and body["citations"][0]["clause"] == "POL-2"


def test_real_mode_agent_response_keeps_the_delivered_fields(settings: Settings):
    document, chunks = sample(settings)
    chunk = next(c for c in chunks if c.clause == "POL-2")
    supplement = next(c for c in chunks if c.clause == "POL-1")
    answer = AgentAnswer(True, True, "A 类城市每晚最多 500 元。", [chunk], [], False,
                         settings.text_model, 2, ["上海住宿标准", "住宿上限"],
                         True, ["上海住宿标准 上限", "上海 住宿标准"], 0.9)
    retrieval = Retrieval("hybrid+multi+rerank", [Hit(chunk, 1, 0.91)], ["住宿", "上海"],
                          (supplement,))
    engine = FakeEngine(answer, retrieval)
    real, client = real_client(settings, engine)
    with client:
        response = ask_agent(client, {"question": "上海住宿每晚最多能报多少？"})
        assert response.status_code == 200
        body = response.json()
        assert (body["version"], body["source"], body["generator"]) == ("v1.2", "model", settings.text_model)
        assert (body["grounded"], body["answerable"]) == (True, True)
        assert (body["confidence"], body["needs_clarification"]) == (0.9, False)
        assert body["citations"] == [{"version": "v1.2", "clause": "POL-2", "locator": chunk.locator,
                                      "quote": chunk.quote, "source": "policy"}]
        assert body["retrieval"]["mode"] == "hybrid+multi+rerank"
        assert body["retrieval"]["chunks"] == [{"rank": 1, "clause": "POL-2",
                                                "locator": chunk.locator, "score": 0.91}]
        assert body["retrieval"]["supplements"] == [{"clause": "POL-1", "locator": supplement.locator,
                                                     "reason": "rule_dependency"}]
        assert body["agent"] == {"mode": "react", "tool_calls": 2,
                                 "queries": ["上海住宿标准", "住宿上限"], "split": True,
                                 "paths": ["上海住宿标准 上限", "上海 住宿标准"]}

    event = audit_rows(real, "policy_question")[-1]["payload"]
    assert (event["chain"], event["grounded"], event["tool_calls"]) == ("agent", True, 2)
    assert event["queries"] == ["上海住宿标准", "住宿上限"]
    assert (event["split"], event["paths"]) == (True, ["上海住宿标准 上限", "上海 住宿标准"])
    assert (event["confidence"], event["needs_clarification"]) == (0.9, False)
    assert event["cited"] == [{"clause": "POL-2", "locator": chunk.locator}]
    assert engine.calls[0]["question"] == "上海住宿每晚最多能报多少？"
    assert engine.calls[0]["version"] == "v1.2"


def test_real_mode_agent_failure_is_503_and_audited(settings: Settings):
    engine = FakeEngine(error=ModelUnavailable("模型暂时不可用", retryable=True, error_type="FakeDown"))
    real, client = real_client(settings, engine)
    with client:
        response = ask_agent(client, {"question": "住宿标准"})
        assert response.status_code == 503
        assert error_code(response) == "MODEL_UNAVAILABLE"
        assert response.json()["error"]["details"]["stage"] == "policy_agent"

    event = audit_rows(real, "error")[-1]["payload"]
    assert (event["stage"], event["error_type"], event["retryable"]) == ("policy_agent", "FakeDown", True)
    assert "sk-" not in json.dumps(event, ensure_ascii=False)


def test_real_mode_agent_validates_the_body_before_calling_the_model(settings: Settings):
    engine = FakeEngine()
    _, client = real_client(settings, engine)
    with client:
        response = ask_agent(client, {"question": ""})
        assert response.status_code == 422
    assert engine.calls == []
