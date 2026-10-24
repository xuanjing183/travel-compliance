"""Policy Q&A #18 (REQ-004): retrieval, code-checked citations, refusals, versions, audit.

Fake mode is BM25 + the deterministic extractive answerer (no network, HC-06). The
real-mode logic (vector / hybrid retrieval, model drafts) is exercised here with
in-process stubs; its *quality* is only measured by the gated evaluations (D-08).
"""
from __future__ import annotations

import json
import math
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import LISI, WANG, ZHANG, error_code, h
from test_policies import upload

from travel_desk.config import Settings
from travel_desk.db.database import db_session, read_session
from travel_desk.db.seed import QUOTES_V12, _rules_for
from travel_desk.llm.dashscope import ModelUnavailable, UsageMeter
from travel_desk.policy import documents, rag
from travel_desk.policy.rag import Draft, PolicyQA, Retriever


def ask(client: TestClient, body: Any, user: str | None = ZHANG):
    return client.post("/api/policy/ask", json=body, headers=h(user))


def question_events(settings: Settings, event_type: str = "policy_question") -> list[dict[str, Any]]:
    with read_session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_events WHERE type = ? ORDER BY seq", (event_type,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]


def doc(settings: Settings, name: str) -> documents.PolicyDocument:
    return documents.load(settings.policies_dir / name, documents.kind_of_path(name))


# ------------------------------------------------------------------ chunking


def test_markdown_chunks_carry_clause_quote_and_locator(settings: Settings):
    document = doc(settings, "v1.2.md")
    chunks = rag.chunk_document("v1.2", document)
    assert [(c.clause, c.locator) for c in chunks] == [
        ("制度信息", "line:3-5"), ("POL-1", "line:9"), ("POL-2", "line:13"), ("POL-2", "line:15"),
        ("POL-2", "line:17"), ("POL-3", "line:21"), ("POL-4", "line:25"), ("POL-5", "line:29"),
        ("POL-6", "line:33"), ("POL-7", "line:37"), ("POL-8", "line:41")]
    for chunk in chunks:  # every quote is found again in the normalized document (§4.8)
        assert document.locate(chunk.quote) == chunk.locator
    assert chunks[2].quote.startswith("员工住宿上限：A 类城市每晚上限 500 元")
    assert chunks[2].heading == "二、住宿标准（POL-2）"


def test_pdf_headings_without_hash_are_detected(settings: Settings):
    chunks = rag.chunk_document("v1.2", doc(settings, "v1.2-text.pdf"))
    assert [c.clause for c in chunks] == ["制度信息"] + [f"POL-{n}" for n in range(1, 9)]
    assert all(c.locator.startswith("page:") for c in chunks)


def test_bm25_and_rrf_are_deterministic():
    bm25 = rag.BM25([["住宿", "上限"], ["伙食", "补贴"], ["住宿"]])
    scores = bm25.scores(["住宿", "上限"])
    assert scores[0] > scores[2] > scores[1] == 0
    fused = rag.rrf([[0, 1, 2], [2, 0, 1]])
    assert fused[0] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[0] > fused[2] > fused[1]


# ------------------------------------------------------------------ fake mode over HTTP


def test_answer_defaults_to_published_version_with_located_citation(client: TestClient, settings: Settings):
    response = ask(client, {"question": "员工去上海出差，住宿每晚最多能报多少？"})
    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["answerable"], body["source"]) == ("v1.2", True, "model")
    assert body["generator"] == "fake:extractive"
    assert body["retrieval"]["mode"] == "bm25"
    assert body["citations"] == [{
        "version": "v1.2", "clause": "POL-2", "locator": "line:13", "source": "policy",
        "quote": "员工住宿上限：A 类城市每晚上限 500 元；B 类城市每晚上限 400 元；C 类城市每晚上限 300 元。"}]
    assert "500 元" in body["answer"] and "POL-2" in body["answer"]
    ranks = [c["rank"] for c in body["retrieval"]["chunks"]]
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) <= rag.TOP_K


def test_no_basis_is_refused_without_citations(client: TestClient):
    for question in ("公司年假有几天？", "出差可以带家属吗"):
        body = ask(client, {"question": question}).json()
        assert body["answerable"] is False, question
        assert body["citations"] == []
        assert body["answer"].startswith("不知道") and "v1.2" in body["answer"]


def test_explicit_archived_version_answers_from_that_document(client: TestClient):
    v10 = ask(client, {"question": "机票需要提前几天预订？", "version": "v1.0"}).json()
    v12 = ask(client, {"question": "机票需要提前几天预订？"}).json()
    assert v10["version"] == "v1.0" and v10["citations"][0]["quote"] == "机票出发日距申请日不少于 5 个自然日。"
    assert v12["version"] == "v1.2" and v12["citations"][0]["quote"] == "机票出发日距申请日不少于 3 个自然日。"
    assert {v10["citations"][0]["clause"], v12["citations"][0]["clause"]} == {"POL-5"}


def test_any_role_can_ask_but_identity_is_required(client: TestClient):
    for user in (ZHANG, LISI, WANG):
        assert ask(client, {"question": "出租车费用怎么报销？"}, user).status_code == 200
    anonymous = ask(client, {"question": "出租车费用怎么报销？"}, None)
    assert anonymous.status_code == 401


@pytest.mark.parametrize("body", [
    {}, {"question": ""}, {"question": "   "}, {"question": 12}, {"question": "住" * 501},
    {"question": "住宿标准", "version": "1.2"}, {"question": "住宿标准", "version": 2},
    {"question": "住宿标准", "versoin": "v1.0"}, ["住宿标准"],
])
def test_malformed_requests_are_422(client: TestClient, body: Any):
    response = ask(client, body)
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"


def test_unknown_and_draft_versions(client: TestClient, settings: Settings):
    assert error_code(ask(client, {"question": "住宿标准", "version": "v8.8"})) == "POLICY_VERSION_NOT_FOUND"
    assert upload(client, settings, "v9.1-test").status_code == 201
    hidden = ask(client, {"question": "住宿标准是多少", "version": "v9.1-test"}, ZHANG)
    assert hidden.status_code == 404 and error_code(hidden) == "POLICY_VERSION_NOT_FOUND"
    preview = ask(client, {"question": "住宿标准是多少", "version": "v9.1-test"}, WANG)
    assert preview.status_code == 200 and preview.json()["version"] == "v9.1-test"


def test_no_published_version_is_404(client: TestClient, settings: Settings):
    with db_session(settings.db_path) as conn:
        conn.execute("UPDATE policy_versions SET status = 'archived' WHERE status = 'published'")
    response = ask(client, {"question": "住宿标准是多少"})
    assert response.status_code == 404 and error_code(response) == "POLICY_VERSION_NOT_FOUND"


def test_every_question_is_audited(client: TestClient, settings: Settings):
    ask(client, {"question": "伙食补贴怎么算？"}, LISI)
    ask(client, {"question": "公司年假有几天？"}, ZHANG)
    answered, refused = question_events(settings)
    assert answered["trip_id"] is None and answered["actor"] == LISI
    assert answered["payload"]["answerable"] is True
    assert answered["payload"]["cited"] == [{"clause": "POL-3", "locator": "line:21"}]
    assert answered["payload"]["retrieval_mode"] == "bm25"
    assert answered["payload"]["generator"] == "fake:extractive"
    assert json.loads(answered["refs_json"]) == [{"kind": "policy", "id": "v1.2"}]
    assert refused["payload"]["answerable"] is False and refused["payload"]["cited"] == []
    assert refused["payload"]["question"] == "公司年假有几天？"


def test_fake_mode_never_builds_an_embedder(settings: Settings):
    engine = rag.build_policy_qa(settings)
    assert engine.retriever.mode == "bm25" and engine.retriever.embedder is None
    assert isinstance(engine.answerer, rag.ExtractiveAnswerer)


# ------------------------------------------------------------------ real-mode logic with stubs


class StubAnswerer:
    name = "stub"

    def __init__(self, draft: Draft | Exception):
        self.result = draft
        self.seen: list[rag.Retrieval] = []

    async def draft(self, question: str, version: str, retrieval: rag.Retrieval) -> Draft:
        self.seen.append(retrieval)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def with_answerer(client: TestClient, answerer: StubAnswerer) -> None:
    client.app.state.policy_qa = PolicyQA(Retriever("bm25", None), answerer)


def test_citations_outside_the_retrieved_set_are_dropped(client: TestClient, settings: Settings):
    with_answerer(client, StubAnswerer(Draft(True, "每晚上限 500 元。", ["C1", "C9", "c1", "X"], "stub-model")))
    body = ask(client, {"question": "员工住宿每晚上限是多少"}).json()
    assert body["answerable"] is True and body["generator"] == "stub-model"
    assert [c["locator"] for c in body["citations"]] == ["line:13"]  # C1 once; C9/X never shown
    payload = question_events(settings)[-1]["payload"]
    assert payload["dropped_citation_ids"] == ["C9", "c1", "X"] and payload["downgraded"] is False


def test_answerable_draft_without_valid_citation_is_downgraded(client: TestClient, settings: Settings):
    with_answerer(client, StubAnswerer(Draft(True, "每晚上限 800 元。", ["C7"], "stub-model")))
    body = ask(client, {"question": "员工住宿每晚上限是多少"}).json()
    assert body["answerable"] is False and body["citations"] == [] and body["answer"].startswith("不知道")
    assert "800" not in body["answer"]
    payload = question_events(settings)[-1]["payload"]
    assert payload["downgraded"] is True and payload["dropped_citation_ids"] == ["C7"]


def test_refusal_draft_never_keeps_citations(client: TestClient):
    with_answerer(client, StubAnswerer(Draft(False, "可能是 10 天", ["C1"], "stub-model")))
    body = ask(client, {"question": "员工住宿每晚上限是多少"}).json()
    assert body["answerable"] is False and body["citations"] == [] and "10 天" not in body["answer"]


def test_model_unavailable_is_503_and_audited(client: TestClient, settings: Settings):
    with_answerer(client, StubAnswerer(ModelUnavailable("模型调用失败，请稍后重试", error_type="APITimeoutError")))
    response = ask(client, {"question": "员工住宿每晚上限是多少"})
    assert response.status_code == 503 and error_code(response) == "MODEL_UNAVAILABLE"
    assert response.json()["error"]["details"] == {"retryable": True, "stage": "policy_qa"}
    error = question_events(settings, "error")[-1]["payload"]
    assert error["stage"] == "policy_qa" and error["error_type"] == "APITimeoutError"
    assert question_events(settings) == []


class CharEmbedder:
    """Deterministic stand-in: character-count vectors (no network)."""

    model = "stub-embedding"

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def embed(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        self.calls.append((purpose, len(texts)))
        alphabet = "住宿伙食补贴机票提前出租车经理城市天元"
        return [[float(t.count(ch)) + 0.01 for ch in alphabet] for t in texts]


async def test_vector_and_hybrid_retrieval_with_stub_embedder(settings: Settings):
    document = doc(settings, "v1.2.md")
    for mode in ("vector", "hybrid"):
        embedder = CharEmbedder()
        retriever = Retriever(mode, embedder)
        index = retriever.index("v1.2", "hash", document)
        first = await retriever.search(index, "伙食补贴标准")
        await retriever.search(index, "出租车")
        assert first.mode == mode and first.hits[0].chunk.clause == "POL-3"
        assert len(first.hits) == rag.TOP_K
        # chunk vectors are computed once per (version, document hash); each query embeds once
        assert embedder.calls == [("policy_embedding", 11), ("policy_query_embedding", 1),
                                  ("policy_query_embedding", 1)]
    with pytest.raises(ValueError):
        Retriever("vector", None)


async def test_rrf_scores_in_hybrid_hits(settings: Settings):
    retriever = Retriever("hybrid", CharEmbedder())
    index = retriever.index("v1.2", "hash", doc(settings, "v1.2.md"))
    hits = (await retriever.search(index, "住宿")).hits
    assert all(h.score <= round(2 / (rag.RRF_K + 1), 6) for h in hits)  # scores are rounded to 6 places
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_embedder_cache_and_missing_key(settings: Settings, monkeypatch):
    meter = UsageMeter(settings.data_dir / "usage.jsonl", settings.cost_ledger_path)
    embedder = rag.DashScopeEmbedder(settings, meter)
    calls: list[list[str]] = []

    async def fake_call(texts: list[str], *, purpose: str) -> list[list[float]]:
        calls.append(texts)
        return [[float(len(t))] * rag.EMBED_DIMENSIONS for t in texts]

    monkeypatch.setattr(embedder, "_call", fake_call)
    texts = [f"条款{i}" for i in range(12)]
    vectors = await embedder.embed(texts + ["条款0"], purpose="policy_embedding")
    assert [len(batch) for batch in calls] == [10, 2]  # batches of EMBED_BATCH, duplicates once
    assert len(vectors) == 13 and vectors[0] == vectors[12]
    cache_text = embedder.cache_path.read_text(encoding="utf-8")
    assert "条款" not in cache_text  # vectors keyed by hash; no policy text on disk

    again = rag.DashScopeEmbedder(settings, meter)  # a restart reuses the disk cache
    monkeypatch.setattr(again, "_call", fake_call)
    await again.embed(texts, purpose="policy_embedding")
    assert len(calls) == 2

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ModelUnavailable) as missing:
        await rag.DashScopeEmbedder(settings, meter)._call(["x"], purpose="policy_embedding")
    assert missing.value.retryable is False
    assert not (settings.data_dir / "usage.jsonl").exists()  # nothing was metered


def test_cosine():
    assert rag.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert rag.cosine([1.0, 0.0], [0.0, 2.0]) == pytest.approx(0.0)
    assert rag.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert math.isclose(rag.cosine([1.0, 1.0], [2.0, 2.0]), 1.0)


# ------------------------------------------------------------------ supporting clauses (rule dependency)


def test_clause_links_come_from_confirmed_rules():
    rules = [{**r, "status": "confirmed"} for r in _rules_for("v1.2", QUOTES_V12)]
    assert rag.clause_links(rules) == {"POL-2": ["POL-1"], "POL-3": ["POL-1"]}
    unconfirmed = [{**r, "status": "candidate"} if r["type"] == "city_tier" else r for r in rules]
    assert rag.clause_links(unconfirmed) == {}


def test_linked_clause_is_supplied_after_the_ranked_hits(client: TestClient, settings: Settings):
    stub = StubAnswerer(Draft(True, "员工住宿每晚上限：A 类 500 元。", ["C1"], "stub-model"))
    with_answerer(client, stub)
    hits = ask(client, {"question": "员工住宿每晚上限是多少"}).json()["retrieval"]
    assert "POL-1" not in [c["clause"] for c in hits["chunks"]]
    assert hits["supplements"] == [{"clause": "POL-1", "locator": "line:9", "reason": "rule_dependency"}]
    shown = stub.seen[-1].shown()
    assert shown[-1][1].clause == "POL-1" and shown[-1][0] == f"C{len(hits['chunks']) + 1}"

    # citing the supplement is valid; it is a shown chunk
    label = shown[-1][0]
    with_answerer(client, StubAnswerer(Draft(True, "上海属于 A 类城市，员工每晚上限 500 元。", ["C1", label],
                                             "stub-model")))
    body = ask(client, {"question": "员工住宿每晚上限是多少"}).json()
    assert [c["clause"] for c in body["citations"]] == ["POL-2", "POL-1"]
    payload = question_events(settings)[-1]["payload"]
    assert payload["supplements"] == [{"clause": "POL-1", "locator": "line:9"}]
    assert payload["dropped_citation_ids"] == []


def test_no_supplement_when_the_linked_clause_was_retrieved(client: TestClient):
    body = ask(client, {"question": "员工去上海出差，住宿每晚最多能报多少？"}).json()
    assert "POL-1" in [c["clause"] for c in body["retrieval"]["chunks"]]  # BM25 matched 上海
    assert body["retrieval"]["supplements"] == []


def test_draft_without_confirmed_rules_has_no_supplements(client: TestClient, settings: Settings):
    assert upload(client, settings, "v9.1-test").status_code == 201
    body = ask(client, {"question": "员工住宿每晚上限是多少", "version": "v9.1-test"}, WANG).json()
    assert body["retrieval"]["supplements"] == []
