"""Policy Q&A retrieval and answering (#18, REQ-004, REQ-029).

Chunking — one chunk per paragraph of a section:
- a *heading* is a Markdown `#` line, or a short line ending in a clause id such as
  `五、提前预订（POL-5）` (text PDFs lose the `#`);
- the clause of a chunk is the `POL-n` id of the heading above it; text before the
  first clause heading and under a level-1 `#` title is `制度信息`, any other heading
  without an id uses its own text;
- a paragraph is a run of segments on consecutive lines (Markdown keeps blank lines
  between paragraphs; a PDF page usually has none, so a PDF section becomes one
  chunk), capped at `MAX_CHUNK_CHARS`;
- the citation quote is the paragraph text itself, so it is always found again by
  `PolicyDocument.locate` (§4.8) — citations are never written by the model.

Retrieval:
- `bm25`   — jieba (`lcut_for_search`) tokens, hand-written Okapi BM25 (k1=1.5, b=0.75);
- `vector` — DashScope embeddings (OpenAI-compatible `/embeddings`, 1024 dims), cosine;
- `hybrid` — RRF (k=60) over the BM25 and vector rankings.
Fake and recorded modes have no network (HC-06), so they always use `bm25`; real mode
uses `settings.retrieval_mode` (`vector` until AC-025 shows hybrid is strictly better).

Supporting clauses: some clauses are only meaningful together with another one — the
tiered caps of POL-2/POL-3 ("A 类城市每晚 500 元") need the city classification of
POL-1 — and a question naming a city rarely retrieves the classification paragraph.
`clause_links` derives these links from the version's *confirmed rules* (a tiered
rule type depends on the `city_tier` rule) and the retrieved set is followed by the
chunks of linked clauses that were not retrieved (`supplements`). Supplements are
shown to the answerer after the ranked hits; they never change the ranking, so
retrieval metrics (AC-025 Recall@3) are computed on the ranked hits only.

Answering:
- `ExtractiveAnswerer` (fake/recorded, `generator="fake:extractive"`) — deterministic:
  answers with the top chunk's quote when enough query terms hit it, otherwise refuses.
  It exists to exercise the API and the refusal path; it is not a quality baseline
  and must not be used for AC-004/017/025 metrics (D-08).
- `ModelAnswerer` (real) — the text model sees the top chunks labelled C1…Cn and returns
  `{answerable, answer, citation_ids}`. Code then keeps only ids it was shown, downgrades
  an "answerable" reply without a valid citation to a refusal, and empties the
  citations of every refusal (`finalize`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from pydantic import BaseModel, Field

from travel_desk.config import Settings
from travel_desk.llm.dashscope import API_KEY_ENV, ModelUnavailable, UsageMeter, structured_call
from travel_desk.policy.documents import PolicyDocument, Segment

MAX_CHUNK_CHARS = 600
TOP_K = 5
RRF_K = 60
BM25_K1, BM25_B = 1.5, 0.75
EMBED_DIMENSIONS = 1024
EMBED_BATCH = 10
FAKE_GENERATOR = "fake:extractive"
FAKE_MIN_HITS = 2  # distinct query terms that must occur in the top chunk
MAX_SUPPLEMENTS = 3
# Rule types whose values are keyed by city tier, and therefore read together with `city_tier`.
DEPENDS_ON = {"hotel_cap": "city_tier", "meal_allowance": "city_tier"}
_CLAUSE = re.compile(r"[（(]\s*(POL-\d+)\s*[)）]")
_TERM = re.compile(r"[0-9a-z\u4e00-\u9fff]")
STOPWORDS = frozenset(
    "的 了 吗 呢 吧 啊 么 是 有 和 与 或 及 在 为 对 要 可以 能 能否 会 去 到 我 我们 你 请问 请 "
    "多少 什么 怎么 怎样 如何 哪些 哪个 几 几天 一下 这 那 这个 那个 时候 什么时候 如果 是否 需要 "
    "公司 规定 制度".split())
PREAMBLE_CLAUSE = "制度信息"


def refusal_text(version: str) -> str:
    return f"不知道：当前制度（{version}）中没有找到能回答这个问题的条款。"


# ------------------------------------------------------------------ chunking


@dataclass(frozen=True)
class Chunk:
    id: str  # "<version>#<n>", stable for a given document
    clause: str
    heading: str
    quote: str
    locator: str

    @property
    def search_text(self) -> str:
        return f"{self.heading} {self.quote}"


def _is_heading(segment: Segment) -> bool:
    text = segment.text
    return text.startswith("#") or (len(text) <= 40 and _CLAUSE.search(text) is not None
                                     and not re.search(r"[。；;]", text))


def chunk_document(version: str, document: PolicyDocument) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading, clause = "", PREAMBLE_CLAUSE
    run: list[Segment] = []

    def flush() -> None:
        if not run:
            return
        quote = " ".join(s.text for s in run)
        locator = document.locate(quote)
        assert locator is not None  # the quote is a contiguous slice of the document text
        chunks.append(Chunk(f"{version}#{len(chunks) + 1}", clause, heading, quote, locator))
        run.clear()

    for segment in document.segments:
        if _is_heading(segment):
            flush()
            title = segment.text.startswith("# ")
            heading = segment.text.lstrip("#").strip()
            match = _CLAUSE.search(heading)
            clause = match.group(1) if match else (PREAMBLE_CLAUSE if title else heading)
            continue
        previous = run[-1] if run else None
        if previous is not None:
            contiguous = previous.page == segment.page and segment.line == previous.line + 1
            too_long = sum(len(s.text) + 1 for s in run) + len(segment.text) > MAX_CHUNK_CHARS
            if not contiguous or too_long:
                flush()
        run.append(segment)
    flush()
    return chunks


# ------------------------------------------------------------------ BM25

_jieba = None


def tokenize(text: str) -> list[str]:
    global _jieba
    if _jieba is None:
        import jieba

        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    tokens = []
    for token in _jieba.lcut_for_search(text.lower()):
        token = token.strip()
        if token and token not in STOPWORDS and _TERM.search(token):
            tokens.append(token)
    return tokens


class BM25:
    def __init__(self, documents: list[list[str]]):
        self.docs = [Counter(d) for d in documents]
        self.lengths = [len(d) for d in documents]
        self.avg = (sum(self.lengths) / len(documents)) if documents else 0.0
        df: Counter[str] = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        n = len(documents)
        self.idf = {term: math.log((n - f + 0.5) / (f + 0.5) + 1) for term, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        result = []
        for doc, length in zip(self.docs, self.lengths, strict=True):
            score = 0.0
            for term in set(query):
                tf = doc.get(term, 0)
                if tf:
                    norm = BM25_K1 * (1 - BM25_B + BM25_B * length / (self.avg or 1))
                    score += self.idf[term] * tf * (BM25_K1 + 1) / (tf + norm)
            result.append(score)
        return result


def _ranking(scores: list[float]) -> list[int]:
    """Indices by descending score; ties keep document order (deterministic)."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion score of every index that appears in any ranking."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (k + rank)
    return fused


# ------------------------------------------------------------------ embeddings


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], *, purpose: str) -> list[list[float]]: ...


class DashScopeEmbedder:
    """Metered embeddings with an in-memory and on-disk cache keyed by (model, text hash).

    The disk cache lives under `data_dir` (never committed) and stores vectors only, no text.
    """

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.embedding_model
        self.cache_path = settings.data_dir / "cache" / f"embeddings-{_safe(self.model)}.json"
        self._cache: dict[str, list[float]] | None = None

    def _load(self) -> dict[str, list[float]]:
        if self._cache is None:
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}
        return self._cache

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load()), encoding="utf-8")
        tmp.replace(self.cache_path)

    async def embed(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        cache = self._load()
        keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        missing = [(k, t) for k, t in dict(zip(keys, texts, strict=True)).items() if k not in cache]
        for start in range(0, len(missing), EMBED_BATCH):
            batch = missing[start:start + EMBED_BATCH]
            vectors = await self._call([t for _, t in batch], purpose=purpose)
            for (key, _), vector in zip(batch, vectors, strict=True):
                cache[key] = vector
            self._save()
        return [cache[k] for k in keys]

    async def _call(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        from openai import AsyncOpenAI  # lazily: fake/recorded mode never loads the SDK

        self.meter.check()
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            raise ModelUnavailable(f"未配置 {API_KEY_ENV}，真实 embedding 不可用", retryable=False)
        client = AsyncOpenAI(api_key=key, base_url=self.settings.dashscope_base_url, timeout=30.0,
                             max_retries=1)
        started = time.perf_counter()
        try:
            response = await client.embeddings.create(model=self.model, input=texts,
                                                      dimensions=EMBED_DIMENSIONS, encoding_format="float")
        except Exception as exc:  # noqa: BLE001 - reduced to the type name, never the text
            raise ModelUnavailable("embedding 调用失败，请稍后重试", error_type=type(exc).__name__) from None
        finally:
            await client.close()
        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None) or 2 * sum(len(t) for t in texts)  # conservative
        self.meter.record(purpose=purpose, model=self.model, usage={"input_tokens": total, "output_tokens": 0,
                                                                     "total_tokens": total},
                          latency_ms=int((time.perf_counter() - started) * 1000))
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if len(vectors) != len(texts) or any(len(v) != EMBED_DIMENSIONS for v in vectors):
            raise ModelUnavailable("embedding 返回的数量或维度不符", error_type="EmbeddingShape")
        return vectors


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ------------------------------------------------------------------ index and retrieval


@dataclass
class Index:
    version: str
    document_hash: str
    chunks: list[Chunk]
    bm25: BM25
    vectors: list[list[float]] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    rank: int
    score: float  # BM25 score / cosine / RRF score, depending on the mode


@dataclass(frozen=True)
class Retrieval:
    mode: str
    hits: list[Hit]
    query_terms: list[str]
    supplements: tuple[Chunk, ...] = ()  # linked clauses not retrieved (shown after the hits)

    def shown(self) -> list[tuple[str, Chunk]]:
        """What the answerer sees, labelled C1…Cn: ranked hits first, then supplements."""
        chunks = [hit.chunk for hit in self.hits] + list(self.supplements)
        return [(f"C{n}", chunk) for n, chunk in enumerate(chunks, start=1)]


def clause_links(rules: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """clause → clauses it must be read with, from confirmed rules (see module docstring)."""
    confirmed = [r for r in rules if r.get("status") == "confirmed"]
    clauses_of: dict[str, list[str]] = {}
    for rule in confirmed:
        clauses_of.setdefault(rule["type"], [])
        if rule["clause"] not in clauses_of[rule["type"]]:
            clauses_of[rule["type"]].append(rule["clause"])
    links: dict[str, list[str]] = {}
    for rule in confirmed:
        for target in clauses_of.get(DEPENDS_ON.get(rule["type"], ""), []):
            if target != rule["clause"] and target not in links.setdefault(rule["clause"], []):
                links[rule["clause"]].append(target)
    return links


def with_supplements(retrieval: Retrieval, chunks: list[Chunk], links: dict[str, list[str]]) -> Retrieval:
    present = {hit.chunk for hit in retrieval.hits}
    extra: list[Chunk] = []
    for hit in retrieval.hits:
        for clause in links.get(hit.chunk.clause, []):
            for chunk in chunks:
                if chunk.clause == clause and chunk not in present and chunk not in extra:
                    extra.append(chunk)
    return replace(retrieval, supplements=tuple(extra[:MAX_SUPPLEMENTS]))


class Retriever:
    def __init__(self, mode: str, embedder: Embedder | None):
        if mode not in ("bm25", "vector", "hybrid"):
            raise ValueError(mode)
        if mode != "bm25" and embedder is None:
            raise ValueError("vector/hybrid retrieval needs an embedder")
        self.mode = mode
        self.embedder = embedder
        self._indexes: dict[tuple[str, str], Index] = {}

    def index(self, version: str, document_hash: str, document: PolicyDocument) -> Index:
        key = (version, document_hash)
        if key not in self._indexes:
            chunks = chunk_document(version, document)
            self._indexes[key] = Index(version, document_hash, chunks,
                                       BM25([tokenize(c.search_text) for c in chunks]))
        return self._indexes[key]

    async def _vectors(self, index: Index) -> list[list[float]]:
        async with index.lock:
            if index.vectors is None:
                assert self.embedder is not None
                index.vectors = await self.embedder.embed([c.search_text for c in index.chunks],
                                                          purpose="policy_embedding")
        return index.vectors

    async def search(self, index: Index, question: str, *, k: int = TOP_K) -> Retrieval:
        terms = tokenize(question)
        bm25 = index.bm25.scores(terms)
        if self.mode == "bm25":  # chunks sharing no term with the question are not "retrieved"
            order, scores = [i for i in _ranking(bm25) if bm25[i] > 0], bm25
        else:
            assert self.embedder is not None
            vectors = await self._vectors(index)
            query = (await self.embedder.embed([question], purpose="policy_query_embedding"))[0]
            dense = [cosine(query, v) for v in vectors]
            if self.mode == "vector":
                order, scores = _ranking(dense), dense
            else:
                fused = rrf([_ranking(bm25), _ranking(dense)])
                scores = [fused.get(i, 0.0) for i in range(len(index.chunks))]
                order = _ranking(scores)
        hits = [Hit(index.chunks[i], rank, round(scores[i], 6))
                for rank, i in enumerate(order[:k], start=1)]
        return Retrieval(self.mode, hits, terms)


# ------------------------------------------------------------------ answering


@dataclass(frozen=True)
class Draft:
    answerable: bool
    answer: str
    citation_ids: list[str]  # "C1"… labels of the hits shown to the answerer
    generator: str


@dataclass(frozen=True)
class Answer:
    answerable: bool
    answer: str
    citations: list[Chunk]
    dropped_citation_ids: list[str]
    downgraded: bool
    generator: str


class Answerer(Protocol):
    name: str

    async def draft(self, question: str, version: str, retrieval: Retrieval) -> Draft: ...


def finalize(draft: Draft, retrieval: Retrieval, version: str) -> Answer:
    """Code-side judgement of a draft: only shown chunks can be cited; no citation → refusal."""
    shown = dict(retrieval.shown())
    cited: list[Chunk] = []
    dropped: list[str] = []
    for label in draft.citation_ids:
        chunk = shown.get(label)
        if chunk is None:
            dropped.append(label)
        elif chunk not in cited:
            cited.append(chunk)
    answerable = draft.answerable and bool(cited) and bool(draft.answer.strip())
    downgraded = draft.answerable and not answerable
    if not answerable:
        return Answer(False, refusal_text(version), [], dropped, downgraded, draft.generator)
    return Answer(True, " ".join(draft.answer.split()), cited, dropped, False, draft.generator)


class ExtractiveAnswerer:
    name = "fake"

    async def draft(self, question: str, version: str, retrieval: Retrieval) -> Draft:
        if not retrieval.hits or retrieval.hits[0].score <= 0:
            return Draft(False, "", [], FAKE_GENERATOR)
        top = retrieval.hits[0].chunk
        hits = set(tokenize(top.search_text)) & set(retrieval.query_terms)
        if len(hits) < min(FAKE_MIN_HITS, len(set(retrieval.query_terms))):
            return Draft(False, "", [], FAKE_GENERATOR)
        return Draft(True, f"根据 {version} {top.clause}：{top.quote}", ["C1"], FAKE_GENERATOR)


class AnswerOut(BaseModel):
    answerable: bool = Field(description="给出的制度片段能否回答该问题；不能回答时为 false")
    answer: str = Field(description="中文回答；只能使用制度片段中的信息；不能回答时留空")
    citation_ids: list[str] = Field(default_factory=list,
                                    description="回答所依据的片段编号，如 [\"C1\", \"C3\"]；不能回答时为空")


SYSTEM_PROMPT = (
    "你是星衡科技差旅制度问答助手。只能依据用户消息中给出的制度片段回答，不得使用片段以外的知识或常识推测。"
    "回答要直接给出结论和关键数值（含单位），并在 citation_ids 中列出所依据的全部片段编号。"
    "需要组合多个片段时（例如先按城市分类确定城市类别，再查该类别的标准），引用所有用到的片段。"
    "如果片段中没有能回答问题的规定，answerable 设为 false、answer 留空、citation_ids 为空，不要猜测。"
)


class ModelAnswerer:
    name = "real"

    def __init__(self, settings: Settings, meter: UsageMeter):
        self.settings = settings
        self.meter = meter
        self.model = settings.text_model

    async def draft(self, question: str, version: str, retrieval: Retrieval) -> Draft:
        context = "\n".join(f"[{label}] 条款 {chunk.clause}（{chunk.heading}）：{chunk.quote}"
                            for label, chunk in retrieval.shown())
        human = f"制度版本：{version}\n制度片段：\n{context}\n\n问题：{question}"
        out: AnswerOut = await structured_call(
            self.settings, self.meter, purpose="policy_answer", model=self.model, schema=AnswerOut,
            messages=[("system", SYSTEM_PROMPT), ("human", human)])
        ids = [str(i).strip().upper() for i in out.citation_ids if str(i).strip()]
        return Draft(bool(out.answerable), out.answer or "", ids, self.model)


# ------------------------------------------------------------------ engine


@dataclass
class PolicyQA:
    retriever: Retriever
    answerer: Answerer

    async def ask(self, version: str, document_hash: str, document: PolicyDocument, question: str, *,
                  links: dict[str, list[str]] | None = None) -> tuple[Answer, Retrieval]:
        index = self.retriever.index(version, document_hash, document)
        retrieval = with_supplements(await self.retriever.search(index, question), index.chunks, links or {})
        draft = await self.answerer.draft(question, version, retrieval)
        return finalize(draft, retrieval, version), retrieval


def build_policy_qa(settings: Settings) -> PolicyQA:
    if settings.llm_mode != "real":
        return PolicyQA(Retriever("bm25", None), ExtractiveAnswerer())
    meter = UsageMeter.from_settings(settings)
    return PolicyQA(Retriever(settings.retrieval_mode, DashScopeEmbedder(settings, meter)),
                    ModelAnswerer(settings, meter))


def chunk_dump(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """Plain-data view of a chunking (used by the evaluation scripts)."""
    return [{"id": c.id, "clause": c.clause, "heading": c.heading, "quote": c.quote, "locator": c.locator}
            for c in chunks]
