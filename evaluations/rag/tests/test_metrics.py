import math

import pytest

from unitropico_eval import metrics
from unitropico_eval.dataset import Gold
from unitropico_eval.metrics import Located

GOLD = (Gold("doc-a", (5,)), Gold("doc-b", (10, 11)))


def test_pages_intersect_handles_ranges_and_edges():
    assert metrics.pages_intersect(4, 6, [5])
    assert metrics.pages_intersect(5, 5, [5])
    assert metrics.pages_intersect(9, 10, [10, 11])  # edge of range
    assert metrics.pages_intersect(11, 12, [10, 11])
    assert not metrics.pages_intersect(6, 9, [5, 10])
    assert not metrics.pages_intersect(1, 3, [])
    assert metrics.pages_intersect(6, 4, [5])  # inverted range tolerated


def test_matching_requires_same_document_and_page():
    assert metrics.matching_gold(Located("doc-a", 5, 5), GOLD) == [0]
    assert metrics.matching_gold(Located("doc-b", 5, 5), GOLD) == []
    assert metrics.matching_gold(Located(None, 5, 5), GOLD) == []
    assert metrics.matching_gold(Located("doc-b", 11, 13), GOLD) == [1]


def test_recall_mrr_ndcg_first_hit_at_rank_two():
    results = [Located("doc-x", 1, 1), Located("doc-a", 4, 5), Located("doc-b", 10, 10)]
    assert metrics.recall_at_k(results, GOLD, 1) == 0.0
    assert metrics.recall_at_k(results, GOLD, 2) == 1.0
    assert metrics.reciprocal_rank(results, GOLD, 5) == 0.5
    assert metrics.reciprocal_rank(results, GOLD, 1) == 0.0
    dcg = 1 / math.log2(3) + 1 / math.log2(4)
    ideal = 1 + 1 / math.log2(3)
    assert metrics.ndcg_at_k(results, GOLD, 3) == pytest.approx(dcg / ideal)
    assert metrics.gold_coverage_at_k(results, GOLD, 2) == 0.5
    assert metrics.gold_coverage_at_k(results, GOLD, 3) == 1.0


def test_ndcg_counts_each_gold_entry_once():
    gold = (Gold("doc-a", (5,)),)
    perfect = [Located("doc-a", 5, 5)]
    duplicates = [
        Located("doc-a", 5, 5),
        Located("doc-a", 5, 6),
        Located("doc-a", 4, 5),
    ]
    assert metrics.ndcg_at_k(perfect, gold, 5) == 1.0
    assert metrics.ndcg_at_k(duplicates, gold, 5) == 1.0
    late = [Located("x", 1, 1), Located("x", 2, 2), Located("doc-a", 5, 5)]
    assert metrics.ndcg_at_k(late, gold, 5) == pytest.approx(1 / math.log2(4))
    assert metrics.ndcg_at_k([], gold, 5) == 0.0
    assert metrics.ndcg_at_k(perfect, (), 5) == 0.0


def test_no_results_scores_zero():
    assert metrics.recall_at_k([], GOLD, 5) == 0.0
    assert metrics.reciprocal_rank([], GOLD, 5) == 0.0


def test_citation_accuracy_and_coverage():
    citations = [
        Located("doc-a", 5, 5),
        Located("doc-a", 7, 8),
        Located("doc-b", 11, 11),
    ]
    assert metrics.citation_accuracy(citations, GOLD) == pytest.approx(2 / 3)
    assert metrics.citation_coverage(citations, GOLD) == 1.0
    assert metrics.citation_coverage(citations[:2], GOLD) == 0.5
    assert metrics.citation_accuracy([], GOLD) is None
    assert metrics.citation_coverage(citations, ()) is None


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([10, 20, 30, 40], 50, 25.0),
        ([10, 20, 30, 40], 95, 38.5),
        ([10, 20, 30, 40], 0, 10.0),
        ([10, 20, 30, 40], 100, 40.0),
        ([7], 95, 7.0),
        ([3, 1, 2], 50, 2.0),
    ],
)
def test_percentile_linear_interpolation(values, q, expected):
    assert metrics.percentile(values, q) == pytest.approx(expected)


def test_percentile_edge_cases():
    assert metrics.percentile([], 50) is None
    with pytest.raises(ValueError):
        metrics.percentile([1], 101)


def test_mean_ignores_none():
    assert metrics.mean([1.0, None, 0.0]) == 0.5
    assert metrics.mean([None]) is None


@pytest.mark.parametrize(
    ("expected", "status", "outcome"),
    [
        ("answer", "answered", "correct_answer"),
        ("answer", "abstained", "wrong_abstention"),
        ("abstain", "abstained", "correct_abstention"),
        ("abstain", "answered", "wrong_answer"),
        ("answer", "unavailable", "error"),
        ("abstain", None, "error"),
    ],
)
def test_behavior_outcome(expected, status, outcome):
    assert metrics.behavior_outcome(expected, status) == outcome
