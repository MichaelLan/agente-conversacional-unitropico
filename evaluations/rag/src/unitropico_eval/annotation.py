"""Faithfulness annotation: export answers to CSV and score filled sheets.

No model is called here. The CSV is meant for human annotators (ideally from
Bienestar Universitario) or for an external judge run under its own protocol;
see the README for the judging rubric and the LLM-judge cost estimate.
"""

import csv
from pathlib import Path

JUDGMENT_COLUMNS = [
    "annotator",
    "claims_total",
    "claims_supported",
    "claims_unsupported",
    "faithfulness_0_2",
    "citations_support_answer",
    "validity_warning_ok",
    "comments",
]
CONTEXT_COLUMNS = [
    "id",
    "split",
    "category",
    "expected_behavior",
    "question",
    "history",
    "status",
    "reason",
    "answer",
    "citations",
    "gold",
    "reference_answer",
]


def _format_citations(citations: list[dict]) -> str:
    return "\n".join(
        f"[{c.get('document') or c.get('document_title')} p{c['page_start']}-"
        f"{c['page_end']}] {c.get('quote') or ''}"
        for c in citations
    )


def _format_history(history: list[dict]) -> str:
    return "\n".join(f"{t['role']}: {t['content']}" for t in history)


def export_annotations(run: dict, out: Path, only_answered: bool = True) -> int:
    """Write the annotation CSV; return the number of rows written."""
    if run["meta"]["command"] != "answer":
        raise ValueError("la anotación de fidelidad requiere una corrida de `answer`")
    rows = [
        r for r in run["items"] if not only_answered or r.get("status") == "answered"
    ]
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTEXT_COLUMNS + JUDGMENT_COLUMNS)
        writer.writeheader()
        for record in rows:
            writer.writerow(
                {
                    "id": record["id"],
                    "split": record["split"],
                    "category": record["category"],
                    "expected_behavior": record["expected_behavior"],
                    "question": record["question"],
                    "history": _format_history(record.get("history") or []),
                    "status": record.get("status"),
                    "reason": record.get("reason"),
                    "answer": record.get("answer") or "",
                    "citations": _format_citations(record.get("citations") or []),
                    "gold": "; ".join(
                        f"{g['document']} p{','.join(map(str, g['pages']))}"
                        for g in record["gold"]
                    ),
                    "reference_answer": record.get("reference_answer") or "",
                }
                | dict.fromkeys(JUDGMENT_COLUMNS, "")
            )
    return len(rows)


def score_annotations(path: Path) -> dict:
    """Aggregate a filled annotation CSV; blank judgment rows are skipped."""
    claims = supported = unsupported = 0
    scores: list[int] = []
    annotated = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("faithfulness_0_2", "").strip():
                continue
            annotated += 1
            score = int(row["faithfulness_0_2"])
            if score not in (0, 1, 2):
                raise ValueError(f"{row['id']}: faithfulness_0_2 debe ser 0, 1 o 2")
            scores.append(score)
            claims += int(row.get("claims_total") or 0)
            supported += int(row.get("claims_supported") or 0)
            unsupported += int(row.get("claims_unsupported") or 0)
    return {
        "annotated_rows": annotated,
        "mean_faithfulness_0_2": sum(scores) / len(scores) if scores else None,
        "fully_faithful_rate": scores.count(2) / len(scores) if scores else None,
        "claims_total": claims,
        "unsupported_claim_rate": unsupported / claims if claims else None,
        "supported_claim_rate": supported / claims if claims else None,
    }
