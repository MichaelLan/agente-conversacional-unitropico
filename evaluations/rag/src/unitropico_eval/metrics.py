"""Pure metric functions; no I/O so they can be tested with hand-built cases.

A retrieved chunk or a citation is a ``Located`` value: the resolved stable
document id (``None`` if unmapped) and its physical page range. It matches a
gold entry when the document is the same and ``[page_start, page_end]``
intersects the gold pages.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from unitropico_eval.dataset import Gold


@dataclass(frozen=True)
class Located:
    """Document id plus inclusive physical page range."""

    document: str | None
    page_start: int
    page_end: int


def pages_intersect(page_start: int, page_end: int, pages: Iterable[int]) -> bool:
    """Whether the inclusive range ``[page_start, page_end]`` hits any page."""
    low, high = min(page_start, page_end), max(page_start, page_end)
    return any(low <= p <= high for p in pages)


def matching_gold(located: Located, gold: Sequence[Gold]) -> list[int]:
    """Indexes of gold entries that ``located`` satisfies."""
    if located.document is None:
        return []
    return [
        index
        for index, entry in enumerate(gold)
        if entry.document == located.document
        and pages_intersect(located.page_start, located.page_end, entry.pages)
    ]


def relevance_vector(results: Sequence[Located], gold: Sequence[Gold]) -> list[bool]:
    """Per-rank relevance (True if the result matches any gold entry)."""
    return [bool(matching_gold(r, gold)) for r in results]


def recall_at_k(results: Sequence[Located], gold: Sequence[Gold], k: int) -> float:
    """Hit-rate Recall@k: 1.0 if at least one of the top-k matches gold."""
    return 1.0 if any(relevance_vector(results[:k], gold)) else 0.0


def gold_coverage_at_k(
    results: Sequence[Located], gold: Sequence[Gold], k: int
) -> float:
    """Fraction of gold entries matched by at least one top-k result."""
    if not gold:
        return 0.0
    covered: set[int] = set()
    for result in results[:k]:
        covered.update(matching_gold(result, gold))
    return len(covered) / len(gold)


def reciprocal_rank(results: Sequence[Located], gold: Sequence[Gold], k: int) -> float:
    """1/rank of the first relevant result within top-k, else 0."""
    for rank, relevant in enumerate(relevance_vector(results[:k], gold), 1):
        if relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(results: Sequence[Located], gold: Sequence[Gold], k: int) -> float:
    """Binary nDCG@k where each gold entry can earn gain only once.

    A result has gain 1 if it matches a gold entry not matched by a better
    ranked result; further chunks of the same gold location earn 0. The ideal
    ranking places ``min(k, len(gold))`` relevant results first, so the value
    stays in ``[0, 1]`` even when many chunks come from one gold page.
    """
    if not gold or k <= 0:
        return 0.0
    used: set[int] = set()
    dcg = 0.0
    for rank, result in enumerate(results[:k], 1):
        fresh = [i for i in matching_gold(result, gold) if i not in used]
        if fresh:
            used.add(fresh[0])
            dcg += 1.0 / math.log2(rank + 1)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(gold)) + 1))
    return dcg / ideal


def citation_accuracy(
    citations: Sequence[Located], gold: Sequence[Gold]
) -> float | None:
    """Fraction of citations whose document and pages intersect gold.

    ``None`` when there are no citations (undefined, not zero).
    """
    if not citations:
        return None
    return sum(bool(matching_gold(c, gold)) for c in citations) / len(citations)


def citation_coverage(
    citations: Sequence[Located], gold: Sequence[Gold]
) -> float | None:
    """Fraction of gold entries cited at least once; ``None`` without gold."""
    if not gold:
        return None
    covered: set[int] = set()
    for citation in citations:
        covered.update(matching_gold(citation, gold))
    return len(covered) / len(gold)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (same convention as numpy's default)."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q debe estar entre 0 y 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def mean(values: Iterable[float | None]) -> float | None:
    """Arithmetic mean ignoring ``None``; ``None`` if nothing remains."""
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


def behavior_outcome(expected: str, status: str | None) -> str:
    """Classify an /answer outcome against the expected behavior.

    Returns one of ``correct_answer``, ``correct_abstention``,
    ``wrong_abstention`` (should answer, abstained), ``wrong_answer`` (should
    abstain, answered) or ``error`` (HTTP failure / unavailable / unknown).
    """
    if status == "answered":
        return "correct_answer" if expected == "answer" else "wrong_answer"
    if status == "abstained":
        return "correct_abstention" if expected == "abstain" else "wrong_abstention"
    return "error"
