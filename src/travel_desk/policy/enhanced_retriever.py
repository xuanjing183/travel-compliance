"""Multi-query retrieval for the enhanced policy-agent chain.

`Retriever.search()` is deliberately untouched: the delivered `/api/policy/ask` path
and its AC-025 metrics keep using it as-is. This wrapper runs the same retriever once
per query (the original question plus the optimizer's rewrites and split sub-queries),
adds one BM25 ranking built from synonym-expanded terms, and fuses the rankings:

- the original question's top `original_keep` hits stay first, in their delivered
  order, with RRF scores `1/(k+rank)`;
- every alternative path (rewrites, split sub-queries, synonyms) contributes
  `weight/(k+rank)` to each chunk it returns; chunks are deduplicated by `Chunk.id`
  (stable per document), never by object identity;
- alternative candidates fill the remaining slots by descending fused score. The
  weight is capped so their total can never reach the lowest pinned original score,
  keeping the delivered top hits authoritative.

The extra candidates exist for the Phase 2 cross-encoder: it sees 10 candidates and
picks the final top 3, so a rewrite or a synonym hit can still win the answer even
though it starts below the original top 5.
"""
from __future__ import annotations

from dataclasses import dataclass

from travel_desk.policy.documents import PolicyDocument
from travel_desk.policy.rag import RRF_K, TOP_K, Chunk, Hit, Index, Retrieval, Retriever
from travel_desk.policy.synonyms import tokenize_with_synonyms

CANDIDATES = 10


@dataclass
class EnhancedRetriever:
    base: Retriever
    original_keep: int = TOP_K
    rewrite_weight: float = 0.5
    synonym_weight: float = 0.5
    use_synonyms: bool = True

    def index(self, version: str, document_hash: str, document: PolicyDocument) -> Index:
        return self.base.index(version, document_hash, document)

    async def search_multi(self, index: Index, queries: list[str], *,
                           k_per_query: int = CANDIDATES,
                           k_final: int = CANDIDATES) -> Retrieval:
        ordered: list[str] = []
        for query in queries:
            query = " ".join(str(query).split())
            if query and query not in ordered:
                ordered.append(query)
        if not ordered:
            raise ValueError("search_multi needs at least one non-empty query")

        paths = [await self.base.search(index, query, k=k_per_query) for query in ordered]
        terms: list[str] = []
        for path in paths:
            for term in path.query_terms:
                if term not in terms:
                    terms.append(term)

        hits: list[Hit] = []
        seen: set[str] = set()

        def add(chunk: Chunk, score: float) -> None:
            if len(hits) < k_final and chunk.id not in seen:
                seen.add(chunk.id)
                hits.append(Hit(chunk, len(hits) + 1, round(score, 6)))

        for hit in paths[0].hits[:self.original_keep]:  # delivered order is authoritative
            add(hit.chunk, 1.0 / (RRF_K + hit.rank))

        # Alternative paths: every rewrite/split path and one synonym-expanded BM25
        # ranking. Contributions are summed (weighted RRF), so agreement across paths
        # matters instead of a single best rank. The scale keeps their total below the
        # lowest pinned original score, whatever the caller passes as `queries`.
        pinned_floor = 1.0 / (RRF_K + self.original_keep)
        synonym_budget = self.synonym_weight if self.use_synonyms else 0.0
        budget = ((len(paths) - 1) * self.rewrite_weight + synonym_budget) / (RRF_K + 1)
        scale = min(1.0, 0.98 * pinned_floor / budget) if budget else 1.0
        rewrite_weight = self.rewrite_weight * scale
        synonym_weight = self.synonym_weight * scale

        fused: dict[str, tuple[Chunk, float]] = {}
        order: dict[str, int] = {}

        def accumulate(chunk: Chunk, contribution: float) -> None:
            if chunk.id in seen:
                return
            previous = fused.get(chunk.id)
            if previous is None:
                order[chunk.id] = len(order)
                fused[chunk.id] = (chunk, contribution)
            else:
                fused[chunk.id] = (chunk, previous[1] + contribution)

        for path in paths[1:]:
            for hit in path.hits:
                accumulate(hit.chunk, rewrite_weight / (RRF_K + hit.rank))

        expanded = tokenize_with_synonyms(" ".join(ordered)) if self.use_synonyms else []
        if self.use_synonyms:
            synonym_scores = index.bm25.scores(expanded)
            ranking = sorted(range(len(synonym_scores)),
                             key=lambda i: (-synonym_scores[i], i))[:k_per_query]
            for rank, i in enumerate(ranking, start=1):
                if synonym_scores[i] <= 0:
                    break
                accumulate(index.chunks[i], synonym_weight / (RRF_K + rank))

        for chunk, score in sorted(fused.values(), key=lambda pair: (-pair[1], order[pair[0].id])):
            add(chunk, score)
        for hit in paths[0].hits[self.original_keep:]:  # fill if alternatives found little
            add(hit.chunk, 0.0)

        for term in expanded:
            if term not in terms:
                terms.append(term)
        return Retrieval(f"{self.base.mode}+multi", hits, terms)


def query_paths(question: str, plan) -> list[str]:
    """The original question first, then the optimizer's queries (deduplicated)."""
    paths: list[str] = []
    for query in [question, *plan.queries]:
        query = " ".join(str(query).split())
        if query and query not in paths:
            paths.append(query)
    return paths
