import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from unitropico_eval.dataset import (
    DatasetError,
    assign_splits,
    load_dataset,
    load_records,
    select,
    validate_record,
)
from unitropico_eval.docmap import DocumentMap, normalize_title

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "datasets" / "candidates.jsonl"
DOCMAP = DocumentMap.load(ROOT / "datasets" / "document_map.json")

VALID = {
    "id": "x-1",
    "split": "dev",
    "category": "in_scope",
    "question": "¿Qué promedio necesito?",
    "expected_behavior": "answer",
    "gold": [
        {"document": "res-cbu-002-2025", "pages": [5], "section": None, "note": ""}
    ],
    "source": "draft_from_corpus",
    "validated": False,
    "notes": "",
}


def test_candidates_satisfy_schema():
    items = load_dataset(CANDIDATES, DOCMAP.page_counts)
    assert 40 <= len(items) <= 60


def test_every_answer_item_has_gold_and_drafts_are_unvalidated():
    items = load_dataset(CANDIDATES, DOCMAP.page_counts)
    for item in items:
        if item.expected_behavior == "answer":
            assert item.gold, item.id
            assert item.scorable_gold or item.blocked, item.id
        else:
            assert not item.gold, item.id
        assert item.source == "draft_from_corpus" and not item.validated


def test_candidate_mix_and_split_ratio():
    items = load_dataset(CANDIDATES, DOCMAP.page_counts)
    categories = Counter(i.category for i in items)
    assert categories["out_of_scope"] >= 8
    assert categories["validity"] >= 4
    assert categories["follow_up"] >= 4
    assert all(i.history for i in items if i.category == "follow_up")
    assert all(
        i.expected_behavior == "abstain" for i in items if i.category == "out_of_scope"
    )
    test_share = sum(i.split == "test" for i in items) / len(items)
    assert 0.25 <= test_share <= 0.35
    for category in categories:
        assert any(i.split == "test" and i.category == category for i in items), (
            category
        )


def test_candidate_splits_match_seeded_assignment():
    records = load_records(CANDIDATES)
    expected = assign_splits([(r["id"], r["category"]) for r in records])
    assert {r["id"]: r["split"] for r in records} == expected


def test_assign_splits_is_deterministic_and_stratified():
    pairs = [(f"a{i}", "in_scope") for i in range(10)] + [
        (f"b{i}", "validity") for i in range(4)
    ]
    first = assign_splits(pairs, seed=7)
    assert first == assign_splits(list(reversed(pairs)), seed=7)
    assert sum(first[f"a{i}"] == "test" for i in range(10)) == 3
    assert sum(first[f"b{i}"] == "test" for i in range(4)) == 1


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"split": "train"}, "split inválido"),
        ({"category": "otro"}, "category inválida"),
        ({"gold": []}, "requiere evidencia de oro"),
        ({"gold": [{"document": "nope", "pages": [1]}]}, "documento desconocido"),
        ({"gold": [{"document": "res-cbu-002-2025", "pages": [99]}]}, "fuera de rango"),
        ({"gold": [{"document": "res-cbu-002-2025", "pages": []}]}, "pages vacío"),
        ({"validated": True}, "no puede estar validated"),
        ({"question": "  "}, "question"),
        ({"category": "follow_up"}, "follow_up requiere history"),
        ({"history": [{"role": "system", "content": "x"}]}, "turno de history"),
        ({"flags": ["raro"]}, "flags inválidos"),
        ({"extra": 1}, "claves no permitidas"),
    ],
)
def test_validate_record_rejects(change, fragment):
    record = copy.deepcopy(VALID) | change
    errors = validate_record(record, DOCMAP.page_counts)
    assert any(fragment in e for e in errors), errors


def test_blocked_items_may_have_empty_pages_and_are_filtered():
    record = copy.deepcopy(VALID) | {
        "gold": [{"document": "res-cbu-001-2026", "pages": []}],
        "flags": ["blocked_by_missing_ocr"],
    }
    assert validate_record(record, DOCMAP.page_counts) == []
    assert validate_record(VALID, DOCMAP.page_counts) == []


def test_load_dataset_reports_duplicates(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text("\n".join([json.dumps(VALID)] * 2) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="duplicado"):
        load_dataset(path, DOCMAP.page_counts)


def test_select_filters_split_category_and_blocked():
    items = load_dataset(CANDIDATES, DOCMAP.page_counts)
    dev = select(items, "dev")
    assert dev and all(i.split == "dev" and not i.blocked for i in dev)
    assert any(i.blocked for i in select(items, "all", include_blocked=True))
    oos = select(items, "all", ("out_of_scope",))
    assert oos and all(i.category == "out_of_scope" for i in oos)


def test_document_map_resolution_order():
    doc = DocumentMap.from_dict(
        {
            "documents": {
                "a": {
                    "page_count": 3,
                    "sha256": ["ABC"],
                    "document_ids": ["d1"],
                    "titles": ["Resolución N.° 1"],
                },
                "b": {"page_count": 3, "version_ids": ["v9"]},
            }
        }
    )
    assert doc.resolve({"source_sha256": "abc", "document_id": "zzz"}) == "a"
    assert doc.resolve({"document_id": "D1"}) == "a"
    assert doc.resolve({"version_id": "v9"}) == "b"
    assert doc.resolve({"document_title": "resolucion n 1"}) == "a"
    assert doc.resolve({"document_title": "otro"}) is None
    assert (
        normalize_title("  Política  Integral—Bienestar ")
        == "politica integral bienestar"
    )


def test_document_map_rejects_ambiguous_keys():
    with pytest.raises(ValueError):
        DocumentMap.from_dict(
            {
                "documents": {
                    "a": {"page_count": 1, "titles": ["X"]},
                    "b": {"page_count": 1, "titles": ["x"]},
                }
            }
        )


def test_repository_document_map_covers_corpus_sha256():
    assert len(DOCMAP.page_counts) == 7
    assert len(DOCMAP.by_sha) == 7
