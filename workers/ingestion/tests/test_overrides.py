import asyncio
import json

import pytest
from fakes import FakeD1, FakeOcr, FakeStep
from test_corrections import WRONG, _body, _resolution_body, add_correction
from test_workflow import VERSION_ID, World

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.domain.corrections import CorrectionSet, PageOverride
from unitropico_ingestion.domain.ingestion import IngestionError
from unitropico_ingestion.infra.d1_repository import D1VersionRepository
from unitropico_ingestion.services.chunker import StructuralChunker
from unitropico_ingestion.services.ingestion import chunks_key, pages_key
from unitropico_ingestion.services.overrides import apply_overrides

OVERRIDE = (
    "ARTÍCULO 9. PERMANENCIA. Texto  reescrito​ por el administrador.\n\n"
    "<table><tr><th>Requisito</th><th>Valor</th></tr>"
    "<tr><td>Promedio</td><td>igual o superior a 3.0</td></tr></table>"
)


def add_override(
    db: FakeD1, override_id: str, *, page: int = 18, text: str = OVERRIDE
) -> None:
    """Insert a page override as the admin API would."""
    db.conn.execute(
        "INSERT INTO page_overrides (id, version_id, page_number, text, reason) "
        "VALUES (?, ?, ?, ?, 'Cotejo con el PDF')",
        (override_id, VERSION_ID, page, text),
    )


def delete_override(db: FakeD1, override_id: str) -> None:
    """Logically delete an override as the admin API would."""
    db.conn.execute(
        "UPDATE page_overrides SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', "
        "'now', '+1 second') WHERE id = ?",
        (override_id,),
    )


def _world(body: bytes | None = None) -> World:
    return World(ocr=FakeOcr(body=body or _resolution_body()))


def _texts(world: World) -> str:
    rows = world.db.query("SELECT text FROM chunks ORDER BY ordinal")
    return "\n".join(row["text"] for row in rows)


def _details(world: World) -> list[str]:
    rows = world.db.query(
        "SELECT detail FROM processing_events WHERE version_id = ? AND step = "
        "'correct' ORDER BY id",
        VERSION_ID,
    )
    return [row["detail"] for row in rows]


def _pages(world: World) -> dict:
    return json.loads(world.blobs.objects[pages_key(VERSION_ID, "structural-v1")])


def _dirty(world: World) -> tuple[int, int]:
    row = world.db.version(VERSION_ID)
    return row["corrections_dirty"], row["corrections_rev"]


# -- pure rules -------------------------------------------------------------------
def test_override_is_prepared_as_one_page_and_keeps_repeated_lines():
    """Normalization and tables apply; running headers are not removed."""
    header = "UNIVERSIDAD FICTICIA DEL TRÓPICO"
    chunker = StructuralChunker()
    pages = chunker.prepare(
        [PageText(n, f"{header}\n\nContenido de la página {n}.") for n in range(1, 5)]
    )
    assert header not in pages[2].text  # stripped from the OCR pages after p1
    result, applied = apply_overrides(
        pages,
        [PageOverride("pov-1", 3, f"{header}\n\n{OVERRIDE}")],
        prepare=chunker.prepare,
        page_count=4,
    )
    text = {page.page_number: page.text for page in result}[3]
    assert text.startswith(header)
    assert "Texto reescrito por el administrador." in text  # spaces, invisible
    assert "| Promedio | igual o superior a 3.0 |" in text  # table to Markdown
    assert "<table>" not in text
    assert [(a.id, a.page_number) for a in applied] == [("pov-1", 3)]


def test_override_adds_a_page_without_ocr_text_in_order():
    """A scanned page without OCR text gains the override text."""
    pages = [PageText(1, "uno"), PageText(3, "tres")]
    result, _ = apply_overrides(
        pages,
        [PageOverride("pov-2", 2, "dos")],
        prepare=StructuralChunker().prepare,
        page_count=3,
    )
    assert [(p.page_number, p.text) for p in result] == [
        (1, "uno"),
        (2, "dos"),
        (3, "tres"),
    ]


def test_override_beyond_page_count_is_not_applicable():
    """Only possible for overrides recorded before the page count was known."""
    with pytest.raises(IngestionError) as info:
        apply_overrides(
            [PageText(1, "uno")],
            [PageOverride("pov-9", 5, "cinco")],
            prepare=StructuralChunker().prepare,
            page_count=3,
        )
    assert info.value.code == "correction_not_applicable"
    assert info.value.detail == "override=pov-9 page=5 page_count=3"
    assert info.value.permanent


def test_no_overrides_is_identity():
    """Without overrides the prepared pages are returned unchanged."""
    pages = [PageText(1, "uno")]
    result, applied = apply_overrides(
        pages, [], prepare=StructuralChunker().prepare, page_count=1
    )
    assert result == pages and applied == []


def test_correction_set_rejects_two_overrides_for_one_page():
    """The domain mirrors the unique active index of D1."""
    with pytest.raises(ValueError, match="duplicate"):
        CorrectionSet(1, (), (PageOverride("a", 1, "x"), PageOverride("b", 1, "y")))


def test_repository_reads_active_overrides_with_the_revision():
    """Overrides, corrections and revision come from one batch."""
    db = FakeD1()
    db.seed(version_id=VERSION_ID, pdf=b"%PDF-1.7 x")
    add_override(db, "pov-b", page=5, text="cinco")
    add_override(db, "pov-a", page=2, text="dos")
    add_override(db, "pov-del", page=7, text="siete")
    delete_override(db, "pov-del")
    add_correction(db, "cor-1", page=2, find="dos", replace="DOS")
    found = asyncio.run(D1VersionRepository(db).correction_set(VERSION_ID))
    assert [(o.id, o.page_number, o.text) for o in found.overrides] == [
        ("pov-a", 2, "dos"),
        ("pov-b", 5, "cinco"),
    ]
    assert [c.id for c in found.corrections] == ["cor-1"]
    assert found.revision == 5  # four overrides events and one correction


# -- workflow ---------------------------------------------------------------------
def test_override_is_applied_traced_and_stored_in_the_pages_json():
    """The page text comes from the override in D1, FTS, JSONL, events and R2."""
    world = _world()
    add_override(world.db, "pov-18")
    result = world.run()
    assert result["status"] == "review_required"
    assert result["overrides_applied"] == 1 and result["corrections_pending"] is False
    texts = _texts(world)
    assert "Texto reescrito por el administrador." in texts
    assert WRONG not in texts  # the OCR text of p18 is gone
    assert "| Promedio | igual o superior a 3.0 |" in texts
    assert _details(world) == ["override_applied id=pov-18 page=18"]
    assert _dirty(world) == (0, 1)

    lines = [
        json.loads(line)
        for line in world.blobs.objects[chunks_key(VERSION_ID, "structural-v1")]
        .decode()
        .splitlines()
    ]
    for line in lines:
        covers = line["page_start"] <= 18 <= line["page_end"]
        assert line["override_ids"] == (["pov-18"] if covers else [])

    document = _pages(world)
    assert document["version_id"] == VERSION_ID
    assert document["chunker_version"] == "structural-v1"
    assert document["page_count"] == 18 and document["corrections_rev"] == 1
    assert [p["page_number"] for p in document["pages"]] == list(range(1, 19))
    page18 = document["pages"][17]
    assert page18["source"] == "override" and page18["override_id"] == "pov-18"
    assert page18["corrections_applied"] == []
    assert page18["text"].startswith("ARTÍCULO 9. PERMANENCIA. Texto reescrito")
    assert "| Requisito | Valor |" in page18["text"]
    page3 = document["pages"][2]
    assert page3 == {
        "page_number": 3,
        "text": "ARTÍCULO 3. TEMA 3. Texto ficticio de la página 3.",
        "source": "ocr",
        "override_id": None,
        "corrections_applied": [],
    }
    # The cached OCR is never rewritten.
    ocr_key = world.db.version(VERSION_ID)["extraction_r2_key"]
    assert world.blobs.objects[ocr_key] == _resolution_body()


def test_override_then_correction_on_the_same_page():
    """Corrections of an overridden page match the override text."""
    world = _world()
    add_override(world.db, "pov-18")
    add_correction(world.db, "cor-18", find="Texto reescrito", replace="Texto revisado")
    result = world.run()
    assert result["overrides_applied"] == 1 and result["corrections_applied"] == 1
    assert "Texto revisado por el administrador." in _texts(world)
    assert _details(world) == [
        "override_applied id=pov-18 page=18",
        "correction_applied id=cor-18 page=18",
    ]
    page18 = _pages(world)["pages"][17]
    assert page18["source"] == "override"
    assert page18["corrections_applied"] == ["cor-18"]
    assert "Texto revisado" in page18["text"]


def test_correction_of_the_replaced_ocr_text_is_not_applicable():
    """After an override the OCR text no longer exists in the page."""
    world = _world()
    add_override(world.db, "pov-18")
    add_correction(world.db, "cor-old")  # targets the OCR wording of p18
    assert world.fail() == "correction_not_applicable"
    assert world.db.version(VERSION_ID)["error_detail"] == (
        "step=chunk correction=cor-old page=18 matches=0"
    )


def test_deleted_override_is_ignored():
    """A logically deleted override leaves the OCR text."""
    world = _world()
    add_override(world.db, "pov-18")
    delete_override(world.db, "pov-18")
    result = world.run()
    assert result["overrides_applied"] == 0
    assert "igual a 3.0" in _texts(world) and "reescrito" not in _texts(world)
    assert _details(world) == []
    assert _pages(world)["pages"][17]["source"] == "ocr"
    assert _dirty(world) == (0, 2)


def test_override_of_a_page_without_ocr_text():
    """A page the OCR left empty (e.g. a diagram) gets indexed text."""
    pages = {n: f"ARTÍCULO {n}. Texto ficticio de la página {n}." for n in (1, 2, 4)}
    world = _world(_body(pages, 4))
    world.run()  # page_count = 4 is known now; page 3 has no text
    assert _pages(world)["pages"][2]["text"] == ""
    add_override(world.db, "pov-3", page=3, text="Diagrama: flujo de admisión.")
    world.run(FakeStep())
    rows = world.db.query(
        "SELECT page_start, page_end FROM chunks WHERE text LIKE '%Diagrama%'"
    )
    assert rows and all(r["page_start"] <= 3 <= r["page_end"] for r in rows)
    assert _pages(world)["pages"][2]["text"] == "Diagrama: flujo de admisión."


def test_override_beyond_the_page_count_fails_the_version():
    """Recorded before the first extraction (page_count unknown in D1)."""
    world = _world()
    add_override(world.db, "pov-99", page=99, text="fuera de rango")
    assert world.fail() == "correction_not_applicable"
    version = world.db.version(VERSION_ID)
    assert version["error_detail"] == "step=chunk override=pov-99 page=99 page_count=18"
    assert not world.chunk_ids()


def test_reprocess_with_override_reuses_ocr():
    """Adding an override and reprocessing pays no OCR again."""
    world = _world()
    world.run()
    assert world.ocr.calls == 1
    first_ids = world.chunk_ids()
    add_override(world.db, "pov-18")
    assert _dirty(world) == (1, 1)
    result = world.run(FakeStep())
    assert world.ocr.calls == 1 and result["ocr_reused"] is True
    assert result["overrides_applied"] == 1
    assert set(world.chunk_ids()) != set(first_ids)
    assert set(world.index.vectors) == set(world.chunk_ids())
    assert _dirty(world) == (0, 1)


class _RacingRepository(D1VersionRepository):
    """Creates an override right after the chunk step read the snapshot."""

    def __init__(self, db: FakeD1):
        super().__init__(db)
        self._fake = db
        self._pending = True

    async def correction_set(self, version_id: str):
        found = await super().correction_set(version_id)
        if self._pending:
            add_override(self._fake, "pov-late", page=3, text="Texto tardío.")
            self._pending = False
        return found


def test_override_created_during_the_chunk_step_stays_pending():
    """The revision changed after the read: the dirty flag is not cleared."""
    world = _world()
    world.run()
    add_override(world.db, "pov-18")
    result = world.run(FakeStep(), repository=_RacingRepository(world.db))
    assert result["overrides_applied"] == 1 and result["corrections_pending"] is True
    assert "Texto tardío" not in _texts(world)
    assert _dirty(world) == (1, 2)
    assert _pages(world)["corrections_rev"] == 1
    result = world.run(FakeStep())
    assert result["overrides_applied"] == 2 and result["corrections_pending"] is False
    assert _dirty(world) == (0, 2)
