"""Run dataset items against the RAG HTTP contract and score each item."""

from collections.abc import Callable

from unitropico_eval import metrics
from unitropico_eval.client import HttpResult
from unitropico_eval.dataset import Item
from unitropico_eval.docmap import DocumentMap

Poster = Callable[[str, dict], HttpResult]
TEXT_PREVIEW = 300


def _located(record: dict, docmap: DocumentMap) -> metrics.Located | None:
    start, end = record.get("page_start"), record.get("page_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return metrics.Located(docmap.resolve(record), start, end)


def _base(item: Item) -> dict:
    return {
        "id": item.id,
        "split": item.split,
        "category": item.category,
        "expected_behavior": item.expected_behavior,
        "validated": item.validated,
        "flags": list(item.flags),
        "question": item.question,
        "gold": [
            {"document": g.document, "pages": list(g.pages), "section": g.section}
            for g in item.gold
        ],
    }


def score_retrieval(
    item: Item, result: HttpResult, docmap: DocumentMap, k: int
) -> dict:
    """Score one ``/retrieval/query`` response for one item."""
    record = _base(item) | {
        "http_status": result.status,
        "latency_ms": round(result.latency_ms, 1),
        "error": result.error,
        "status": (result.body or {}).get("status"),
        "results": [],
        "unmapped_results": 0,
        "metrics": None,
    }
    evidence = (result.body or {}).get("evidence")
    if result.status != 200 or not isinstance(evidence, list):
        if result.status is not None and result.error is None:
            record["error"] = f"HTTP {result.status}"
        return record
    gold = item.scorable_gold
    located: list[metrics.Located] = []
    for rank, entry in enumerate(evidence, 1):
        if not isinstance(entry, dict):
            continue
        loc = _located(entry, docmap)
        if loc is None:
            continue
        located.append(loc)
        record["unmapped_results"] += loc.document is None
        record["results"].append(
            {
                "rank": rank,
                "document": loc.document,
                "document_id": entry.get("document_id"),
                "document_title": entry.get("document_title"),
                "page_start": loc.page_start,
                "page_end": loc.page_end,
                "section": entry.get("section"),
                "chunk_id": entry.get("chunk_id"),
                "relevant": bool(metrics.matching_gold(loc, gold)),
                "text_preview": str(entry.get("text", ""))[:TEXT_PREVIEW],
            }
        )
    if item.expected_behavior == "answer" and gold:
        record["metrics"] = {
            "recall_at_k": metrics.recall_at_k(located, gold, k),
            "reciprocal_rank": metrics.reciprocal_rank(located, gold, k),
            "ndcg_at_k": metrics.ndcg_at_k(located, gold, k),
            "gold_coverage_at_k": metrics.gold_coverage_at_k(located, gold, k),
        }
    return record


def run_retrieval(
    items: list[Item],
    post: Poster,
    docmap: DocumentMap,
    k: int,
    path: str = "/retrieval/query",
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Query retrieval for each item (question only: the route has no history)."""
    records = []
    for item in items:
        result = post(path, {"question": item.question, "limit": k})
        records.append(score_retrieval(item, result, docmap, k))
        if progress:
            progress(item.id)
    return records


def score_answer(item: Item, result: HttpResult, docmap: DocumentMap) -> dict:
    """Score one ``/answer`` (or API ``/chat``) response for one item."""
    body = result.body or {}
    status = body.get("status") if result.status in (200, 503) else None
    record = _base(item) | {
        "http_status": result.status,
        "latency_ms": round(result.latency_ms, 1),
        "error": result.error,
        "status": status,
        "reason": body.get("reason"),
        "answer": body.get("answer"),
        "reference_answer": item.reference_answer,
        "history": list(item.history),
        "citations": [],
        "usage": body.get("usage") if isinstance(body.get("usage"), dict) else None,
    }
    if result.status != 200 and result.error is None:
        record["error"] = f"HTTP {result.status}"
    record["outcome"] = metrics.behavior_outcome(item.expected_behavior, status)
    gold = item.scorable_gold
    located: list[metrics.Located] = []
    citations = body.get("citations") if status == "answered" else None
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            continue
        loc = _located(citation, docmap)
        if loc is None:
            continue
        located.append(loc)
        record["citations"].append(
            {
                "document": loc.document,
                "document_id": citation.get("document_id"),
                "document_title": citation.get("document_title"),
                "page_start": loc.page_start,
                "page_end": loc.page_end,
                "section": citation.get("section"),
                "chunk_id": citation.get("chunk_id"),
                "quote": citation.get("quote"),
                "matches_gold": bool(metrics.matching_gold(loc, gold)),
            }
        )
    scorable = status == "answered" and item.expected_behavior == "answer" and gold
    record["citation_accuracy"] = (
        metrics.citation_accuracy(located, gold) if scorable else None
    )
    record["citation_coverage"] = (
        metrics.citation_coverage(located, gold) if scorable else None
    )
    record["has_gold_citation"] = (
        any(c["matches_gold"] for c in record["citations"]) if scorable else None
    )
    return record


def run_answer(
    items: list[Item],
    post: Poster,
    docmap: DocumentMap,
    path: str = "/answer",
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Ask each item (with its history, if any) and score the response."""
    records = []
    for item in items:
        payload: dict = {"question": item.question}
        if item.history:
            payload["history"] = [dict(turn) for turn in item.history]
        records.append(score_answer(item, post(path, payload), docmap))
        if progress:
            progress(item.id)
    return records
