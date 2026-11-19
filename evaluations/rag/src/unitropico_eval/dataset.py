"""Dataset loading, schema validation and seeded dev/test assignment.

The schema is documented in ``datasets/schema.md``. Validation is strict on
purpose: unknown keys, out-of-range pages or answerable items without gold
evidence are errors, because a silently malformed item would bias metrics.
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

SPLITS = ("dev", "test")
CATEGORIES = ("in_scope", "out_of_scope", "validity", "contradiction", "follow_up")
BEHAVIORS = ("answer", "abstain")
SOURCES = ("draft_from_corpus", "validated_by_staff")
FLAGS = ("blocked_by_missing_ocr", "ocr_defect_on_gold_page", "multi_document")
ROLES = ("user", "assistant")
DEFAULT_SEED = 20261118
DEFAULT_TEST_FRACTION = 0.3

_ITEM_KEYS = {
    "id",
    "split",
    "category",
    "question",
    "history",
    "expected_behavior",
    "gold",
    "reference_answer",
    "source",
    "validated",
    "notes",
    "flags",
}
_REQUIRED_KEYS = {
    "id",
    "split",
    "category",
    "question",
    "expected_behavior",
    "gold",
    "source",
    "validated",
    "notes",
}
_GOLD_KEYS = {"document", "pages", "section", "note"}


@dataclass(frozen=True)
class Gold:
    """One gold evidence location: stable document id plus physical PDF pages."""

    document: str
    pages: tuple[int, ...]
    section: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Item:
    """One evaluation question with its expected behavior and gold evidence."""

    id: str
    split: str
    category: str
    question: str
    expected_behavior: str
    gold: tuple[Gold, ...]
    source: str
    validated: bool
    notes: str
    history: tuple[dict, ...] = ()
    reference_answer: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        """Whether gold evidence lies on pages without extracted text."""
        return "blocked_by_missing_ocr" in self.flags

    @property
    def scorable_gold(self) -> tuple[Gold, ...]:
        """Gold entries with at least one page (blocked ones have none)."""
        return tuple(g for g in self.gold if g.pages)


class DatasetError(ValueError):
    """Raised when a dataset file does not satisfy the schema."""


def validate_record(record: object, page_counts: dict[str, int]) -> list[str]:
    """Return human-readable schema errors for one raw JSON record."""
    if not isinstance(record, dict):
        return ["el ítem no es un objeto JSON"]
    errors: list[str] = []
    rid = record.get("id", "<sin id>")
    extra = set(record) - _ITEM_KEYS
    if extra:
        errors.append(f"{rid}: claves no permitidas {sorted(extra)}")
    missing = _REQUIRED_KEYS - set(record)
    if missing:
        errors.append(f"{rid}: faltan claves {sorted(missing)}")
        return errors
    if not isinstance(rid, str) or not rid.strip():
        errors.append("id vacío o no textual")
    if record["split"] not in SPLITS:
        errors.append(f"{rid}: split inválido {record['split']!r}")
    if record["category"] not in CATEGORIES:
        errors.append(f"{rid}: category inválida {record['category']!r}")
    if record["expected_behavior"] not in BEHAVIORS:
        errors.append(f"{rid}: expected_behavior inválido")
    if record["source"] not in SOURCES:
        errors.append(f"{rid}: source inválido {record['source']!r}")
    if not isinstance(record["validated"], bool):
        errors.append(f"{rid}: validated debe ser booleano")
    elif record["source"] == "draft_from_corpus" and record["validated"]:
        errors.append(f"{rid}: un borrador no puede estar validated=true")
    if not isinstance(record["notes"], str):
        errors.append(f"{rid}: notes debe ser texto")
    question = record["question"]
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        errors.append(f"{rid}: question debe tener 1..500 caracteres tras strip")
    flags = record.get("flags", [])
    if not isinstance(flags, list) or any(f not in FLAGS for f in flags):
        errors.append(f"{rid}: flags inválidos {flags!r}")
        flags = []
    errors.extend(_validate_history(rid, record))
    if "reference_answer" in record and not (
        isinstance(record["reference_answer"], str) and record["reference_answer"]
    ):
        errors.append(f"{rid}: reference_answer debe ser texto no vacío si existe")
    errors.extend(_validate_gold(rid, record, flags, page_counts))
    return errors


def _validate_history(rid: str, record: dict) -> list[str]:
    errors: list[str] = []
    history = record.get("history")
    if record["category"] == "follow_up" and not history:
        errors.append(f"{rid}: follow_up requiere history")
    if history is None:
        return errors
    if not isinstance(history, list) or not 1 <= len(history) <= 6:
        return [*errors, f"{rid}: history debe ser lista de 1..6 mensajes"]
    for turn in history:
        if (
            not isinstance(turn, dict)
            or set(turn) != {"role", "content"}
            or turn["role"] not in ROLES
            or not isinstance(turn["content"], str)
            or not 1 <= len(turn["content"].strip()) <= 2000
        ):
            errors.append(f"{rid}: turno de history inválido {turn!r}")
    return errors


def _validate_gold(
    rid: str, record: dict, flags: list[str], page_counts: dict[str, int]
) -> list[str]:
    errors: list[str] = []
    gold = record["gold"]
    if not isinstance(gold, list):
        return [f"{rid}: gold debe ser lista"]
    if record["expected_behavior"] == "answer" and not gold:
        errors.append(f"{rid}: un ítem answer requiere evidencia de oro")
    for entry in gold:
        if not isinstance(entry, dict) or not {"document", "pages"} <= set(entry):
            errors.append(f"{rid}: entrada de oro sin document/pages")
            continue
        if set(entry) - _GOLD_KEYS:
            errors.append(f"{rid}: claves de oro no permitidas")
        document = entry["document"]
        if document not in page_counts:
            errors.append(f"{rid}: documento desconocido {document!r}")
            continue
        pages = entry["pages"]
        if not isinstance(pages, list) or any(
            not isinstance(p, int) or isinstance(p, bool) for p in pages
        ):
            errors.append(f"{rid}: pages debe ser lista de enteros")
            continue
        if not pages and "blocked_by_missing_ocr" not in flags:
            errors.append(
                f"{rid}: pages vacío solo se admite con blocked_by_missing_ocr"
            )
        if any(not 1 <= p <= page_counts[document] for p in pages):
            errors.append(f"{rid}: página fuera de rango en {document}")
        if entry.get("section") is not None and not isinstance(entry["section"], str):
            errors.append(f"{rid}: section debe ser texto o null")
        if not isinstance(entry.get("note", ""), str):
            errors.append(f"{rid}: note debe ser texto")
    return errors


def _to_item(record: dict) -> Item:
    return Item(
        id=record["id"],
        split=record["split"],
        category=record["category"],
        question=record["question"],
        expected_behavior=record["expected_behavior"],
        gold=tuple(
            Gold(
                document=g["document"],
                pages=tuple(g["pages"]),
                section=g.get("section"),
                note=g.get("note", ""),
            )
            for g in record["gold"]
        ),
        source=record["source"],
        validated=record["validated"],
        notes=record["notes"],
        history=tuple(record.get("history") or ()),
        reference_answer=record.get("reference_answer"),
        flags=tuple(record.get("flags", ())),
    )


def load_records(path: Path) -> list[dict]:
    """Read a JSONL file, ignoring blank lines; raise on invalid JSON."""
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise DatasetError(f"{path}:{number}: JSON inválido ({error})") from None
    return records


def validate_dataset(records: list[dict], page_counts: dict[str, int]) -> list[str]:
    """Validate every record plus dataset-level constraints (unique ids)."""
    errors: list[str] = []
    seen: set[str] = set()
    for record in records:
        errors.extend(validate_record(record, page_counts))
        rid = record.get("id") if isinstance(record, dict) else None
        if rid in seen:
            errors.append(f"{rid}: id duplicado")
        seen.add(rid)
    return errors


def load_dataset(path: Path, page_counts: dict[str, int]) -> list[Item]:
    """Load and validate a dataset; raise ``DatasetError`` listing all problems."""
    records = load_records(path)
    errors = validate_dataset(records, page_counts)
    if errors:
        raise DatasetError("\n".join(errors))
    return [_to_item(r) for r in records]


def assign_splits(
    items: list[tuple[str, str]],
    seed: int = DEFAULT_SEED,
    test_fraction: float = DEFAULT_TEST_FRACTION,
) -> dict[str, str]:
    """Stratified, seeded dev/test assignment from ``(id, category)`` pairs.

    Within each category ids are sorted, shuffled with ``random.Random(seed)``
    and the first ``round(n * test_fraction)`` go to test. Only meant for the
    initial draft: once the test split is frozen it must not be reassigned.
    """
    by_category: dict[str, list[str]] = {}
    for item_id, category in items:
        by_category.setdefault(category, []).append(item_id)
    assignment: dict[str, str] = {}
    rng = random.Random(seed)
    for category in sorted(by_category):
        ids = sorted(by_category[category])
        rng.shuffle(ids)
        n_test = round(len(ids) * test_fraction)
        for index, item_id in enumerate(ids):
            assignment[item_id] = "test" if index < n_test else "dev"
    return assignment


def select(
    items: list[Item],
    split: str | None = None,
    categories: tuple[str, ...] | None = None,
    include_blocked: bool = False,
) -> list[Item]:
    """Filter items by split/category; blocked items are excluded by default."""
    return [
        i
        for i in items
        if (split in (None, "all") or i.split == split)
        and (not categories or i.category in categories)
        and (include_blocked or not i.blocked)
    ]
