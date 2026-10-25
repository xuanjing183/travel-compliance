"""Travel-domain synonym expansion for the enhanced retrieval path.

`Retriever.search()` is delivered behaviour and stays untouched, so expansion cannot
happen inside its BM25 tokenization. `EnhancedRetriever` instead builds one extra BM25
ranking from `tokenize_with_synonyms()` and fuses it with the query paths; an expanded
term can only add candidates, never rewrite the document index or the delivered path.

The map is deliberately small and domain-specific: generic words ("标准", "费用") are
not expanded because they occur in every clause and only add noise for the reranker.
"""
from __future__ import annotations

from travel_desk.policy.rag import tokenize

# canonical term -> everyday synonyms that never appear verbatim in the policy text
SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "住宿": ("酒店", "宾馆", "住店", "房费", "住宿费", "过夜"),
    "伙食": ("餐补", "餐饮", "饭钱", "餐费", "吃饭", "膳食", "伙食费"),
    "补贴": ("补助", "津贴"),
    "交通": ("出行", "坐车"),
    "机票": ("飞机票", "航班", "订票"),
    "高铁": ("动车", "火车"),
    "经理": ("主管", "领导", "管理层"),
    "审批": ("审核", "批准", "签字"),
    "报销": ("报账", "报销额度", "实报实销"),
    "上限": ("封顶", "限额", "最多"),
    "出差": ("差旅", "公务"),
    "城市": ("地区",),
    "出租车": ("打车", "的士", "taxi"),
    "提前": ("预定", "预订"),
    "家属": ("配偶", "子女", "老婆", "孩子"),
}


def _expandable_groups() -> dict[str, tuple[str, ...]]:
    """Every word in a group expands to the other words of that group (both directions)."""
    groups: dict[str, set[str]] = {}
    for canonical, variants in SYNONYM_MAP.items():
        group = {canonical, *variants}
        for word in group:
            groups.setdefault(word, set()).update(group - {word})
    return {word: tuple(sorted(others)) for word, others in groups.items()}


_EXPANSIONS = _expandable_groups()


def synonyms_of(token: str) -> tuple[str, ...]:
    return _EXPANSIONS.get(token, ())


def tokenize_with_synonyms(text: str) -> list[str]:
    """Original tokens first, then one level of domain synonyms (deduplicated).

    Synonym values are tokenized again so a phrase such as "报销额度" also contributes
    the vocabulary the policy document actually uses. Expansion is not recursive: a
    synonym of a synonym is not added, which keeps the term set bounded.
    """
    tokens = tokenize(text)
    expanded = list(tokens)
    seen = set(tokens)
    for token in tokens:
        for synonym in synonyms_of(token):
            for part in tokenize(synonym) or [synonym]:
                if part not in seen:
                    seen.add(part)
                    expanded.append(part)
    return expanded
