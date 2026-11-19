import asyncio
import json
import sqlite3

import pytest
from fakes import FakeD1, FakeOcr, FakeStep
from test_workflow import VERSION_ID, World

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.domain.corrections import TextCorrection
from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError
from unitropico_ingestion.infra.d1_repository import D1VersionRepository
from unitropico_ingestion.services.chunker import StructuralChunker
from unitropico_ingestion.services.corrections import apply_corrections, count_matches
from unitropico_ingestion.services.ingestion import chunks_key
from unitropico_ingestion.services.preprocess import normalize_text

# Synthetic stand-in for Res. 00137 p18: invented text, same OCR defect shape.
PAGE_18 = (
    "ARTÍCULO 9. PERMANENCIA. Para conservar el apoyo ficticio el estudiante\n"
    "deberá acreditar un promedio\n"
    "igual a 3.0 en el periodo académico inmediatamente anterior."
)
WRONG = "promedio igual a 3.0"
RIGHT = "promedio igual o superior a 3.0"


def _body(pages: dict[int, str], total: int) -> bytes:
    return json.dumps(
        {
            "success": True,
            "data": {
                "pages": [
                    {"pageNumber": n, "markdown": pages.get(n, "")}
                    for n in range(1, total + 1)
                ],
                "metadata": {"numPages": total, "totalPages": total},
            },
        }
    ).encode()


def _resolution_body() -> bytes:
    pages = {
        n: f"ARTÍCULO {n}. TEMA {n}. Texto ficticio de la página {n}."
        for n in range(1, 18)
    }
    pages[1] = "# RESOLUCIÓN FICTICIA DE PRUEBA\n\n" + pages[1]
    pages[18] = PAGE_18
    return _body(pages, 18)


def _world() -> World:
    return World(ocr=FakeOcr(body=_resolution_body()))


def add_correction(
    db: FakeD1,
    correction_id: str,
    *,
    page: int = 18,
    find: str = WRONG,
    replace: str = RIGHT,
    created_at: str | None = None,
) -> None:
    """Insert a correction as the admin API would."""
    columns = "id, version_id, page_number, find_text, replace_text, reason"
    values: list[object] = [correction_id, VERSION_ID, page, find, replace, "Cotejo"]
    if created_at is not None:
        columns += ", created_at"
        values.append(created_at)
    marks = ", ".join("?" * len(values))
    db.conn.execute(
        f"INSERT INTO text_corrections ({columns}) VALUES ({marks})", values
    )


def _texts(world: World) -> str:
    rows = world.db.query("SELECT text FROM chunks ORDER BY ordinal")
    return "\n".join(row["text"] for row in rows)


def _events(world: World) -> list[tuple]:
    rows = world.db.query(
        "SELECT step, status, error_code, detail FROM processing_events "
        "WHERE version_id = ? ORDER BY id",
        VERSION_ID,
    )
    return [tuple(row.values()) for row in rows]


# -- pure rules -------------------------------------------------------------------
def test_whitespace_runs_match_line_breaks_but_text_stays_literal():
    """Spaces in find_text match line breaks; case and accents stay exact."""
    text = normalize_text(PAGE_18)
    assert count_matches(text, WRONG) == 1
    assert count_matches(text, "promedio   igual\na 3.0") == 1
    assert count_matches(text, "Promedio igual a 3.0") == 0
    assert count_matches(text, "deberá acreditar") == 1
    assert count_matches(text, "debera acreditar") == 0
    assert count_matches(text, "deberá acreditar".replace("á", "á")) == 1


def test_overlapping_occurrences_count():
    """Overlapping occurrences make a find_text ambiguous."""
    assert count_matches("aaa", "aa") == 2
    assert count_matches("abc", "​") == 0  # normalizes to nothing


def test_apply_replaces_once_and_traces():
    """The page text is normalized and the single match replaced."""
    pages = [PageText(17, "  otra   página  "), PageText(18, PAGE_18)]
    correction = TextCorrection("cor-1", 18, WRONG, RIGHT)
    corrected, applied = apply_corrections(pages, [correction])
    assert corrected[0] is pages[0]  # untouched pages are passed through
    assert RIGHT in corrected[1].text and WRONG not in corrected[1].text
    assert "\n" in corrected[1].text  # line structure kept outside the match
    assert [(a.id, a.page_number) for a in applied] == [("cor-1", 18)]


def test_same_page_corrections_apply_in_order():
    """A later correction sees the text left by the earlier one."""
    pages = [PageText(1, "valor 3.0 y valor 4.0")]
    corrections = [
        TextCorrection("a", 1, "valor 3.0", "nota 3.0"),
        TextCorrection("b", 1, "valor 4.0", "nota 4.0"),
        TextCorrection("c", 1, "nota 3.0 y", "nota 3.0, y"),
    ]
    corrected, applied = apply_corrections(pages, corrections)
    assert corrected[0].text == "nota 3.0, y nota 4.0"
    assert [a.id for a in applied] == ["a", "b", "c"]


def test_empty_replacement_removes_noise():
    """replace_text may be empty to delete OCR noise."""
    corrected, _ = apply_corrections(
        [PageText(1, "texto ~~ruido~~ útil")], [TextCorrection("x", 1, "~~ruido~~", "")]
    )
    assert normalize_text(corrected[0].text) == "texto útil"


@pytest.mark.parametrize(
    ("pages", "correction", "matches"),
    [
        ([PageText(1, "nada")], TextCorrection("z", 1, "igual a 3.0", "x"), 0),
        ([PageText(1, "a 3.0 b a 3.0")], TextCorrection("z", 1, "a 3.0", "x"), 2),
        ([PageText(1, "igual a 3.0")], TextCorrection("z", 2, "igual a 3.0", "x"), 0),
    ],
)
def test_not_applicable_reports_id_page_and_count_only(pages, correction, matches):
    """0 or >1 matches (or a missing page) raise a permanent, text-free error."""
    with pytest.raises(IngestionError) as info:
        apply_corrections(pages, [correction])
    error = info.value
    assert error.code is ErrorCode.CORRECTION_NOT_APPLICABLE and error.permanent
    assert (
        error.detail == f"correction=z page={correction.page_number} matches={matches}"
    )


def test_no_corrections_is_identity():
    """Without corrections the pages are returned as they are."""
    pages = [PageText(1, " a \n\n\n b ")]
    corrected, applied = apply_corrections(pages, [])
    assert corrected == pages and applied == []


def test_normalize_text_is_idempotent_on_ocr_pages():
    """Applying corrections on normalized text cannot change the chunker input."""
    for text in (PAGE_18, " a  b \r\n\n\n<!-- page 2 -->c​ ", "| x |  y |"):
        once = normalize_text(text)
        assert normalize_text(once) == once


# -- adapter ----------------------------------------------------------------------
def test_repository_returns_active_corrections_in_order():
    """Deleted rows are skipped; order is created_at, then insertion (rowid)."""
    world = World()
    same = "2030-01-02T00:00:00.000Z"
    add_correction(world.db, "cor-b", find="promedio", created_at=same)
    add_correction(world.db, "cor-a", created_at=same)
    add_correction(world.db, "cor-0", page=3, created_at="2030-01-01T00:00:00.000Z")
    add_correction(
        world.db, "cor-del", find="acreditar", created_at="2030-01-03T00:00:00.000Z"
    )
    world.db.conn.execute(
        "UPDATE text_corrections SET deleted_at = '2030-02-01T00:00:00.000Z' "
        "WHERE id = 'cor-del'"
    )
    found = asyncio.run(D1VersionRepository(world.db).correction_set(VERSION_ID))
    # cor-b was inserted before cor-a with the same created_at: rowid decides,
    # never the (random in production) id.
    assert [c.id for c in found.corrections] == ["cor-0", "cor-b", "cor-a"]
    assert found.corrections[0] == TextCorrection("cor-0", 3, WRONG, RIGHT)
    assert found.corrections[1].find_text == "promedio"
    assert found.revision == 5  # four creations and one logical delete


# -- workflow ---------------------------------------------------------------------
def test_page_18_correction_is_applied_and_traced():
    """The synthetic p18 defect is fixed in D1, FTS, JSONL, events and result."""
    world = _world()
    add_correction(world.db, "cor-p18")
    result = world.run()
    assert result["status"] == "review_required"
    assert result["corrections_applied"] == 1

    texts = _texts(world)
    assert "igual o superior a 3.0" in texts and "igual a 3.0" not in texts
    hits = world.db.query(
        "SELECT c.page_start, c.page_end FROM chunks_fts "
        "JOIN chunks c ON c.seq = chunks_fts.rowid WHERE chunks_fts MATCH 'superior'"
    )
    assert hits and all(h["page_start"] <= 18 <= h["page_end"] for h in hits)

    events = _events(world)
    assert ("correct", "succeeded", None, "correction_applied id=cor-p18 page=18") in (
        events
    )
    assert (
        "chunk",
        "succeeded",
        None,
        f"chunks={result['chunk_count']} corrections=1 overrides=0 corrections_rev=1 "
        "corrections_pending=0",
    ) in (events)

    lines = [
        json.loads(line)
        for line in world.blobs.objects[chunks_key(VERSION_ID, "structural-v1")]
        .decode()
        .splitlines()
    ]
    for line in lines:
        expected = ["cor-p18"] if line["page_start"] <= 18 <= line["page_end"] else []
        assert line["correction_ids"] == expected
    assert any(line["correction_ids"] for line in lines)

    # The cached OCR is the provider's response, never the corrected text.
    ocr_key = world.db.version(VERSION_ID)["extraction_r2_key"]
    assert world.blobs.objects[ocr_key] == _resolution_body()
    assert world.blobs.puts[ocr_key] == 1


@pytest.mark.parametrize(("find", "matches"), [("promedio igual a 4.0", 0), ("3.0", 2)])
def test_non_matching_correction_fails_the_version(find, matches):
    """0 or >1 matches: permanent failure, nothing replaced, no document text."""
    world = _world()
    if matches == 2:
        # "3.0" occurs once on p18; an earlier correction adds a second one.
        add_correction(world.db, "cor-0", find="un promedio", replace="un 3.0 promedio")
    add_correction(world.db, "cor-x", find=find, replace="reemplazo")
    step = FakeStep()
    assert world.fail(step) == "correction_not_applicable"
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed"
    assert version["error_code"] == "correction_not_applicable"
    assert version["error_detail"] == (
        f"step=chunk correction=cor-x page=18 matches={matches}"
    )
    assert step.attempts["chunk"] == 1  # permanent: not retried
    assert ("chunk", "failed", "correction_not_applicable") in world.events()
    assert not world.chunk_ids() and world.embedder.calls == 0
    assert not any(k.startswith("chunks/") for k in world.blobs.objects)
    assert not any(e[0] == "correct" for e in world.events())


def test_deleted_corrections_are_ignored():
    """A logically deleted correction is neither applied nor checked."""
    world = _world()
    add_correction(world.db, "cor-bad", find="no existe en la página")
    world.db.conn.execute(
        "UPDATE text_corrections SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', "
        "'now', '+1 second') WHERE id = 'cor-bad'"
    )
    result = world.run()
    assert result["status"] == "review_required"
    assert result["corrections_applied"] == 0
    assert "igual a 3.0" in _texts(world)
    assert not any(e[0] == "correct" for e in world.events())


def test_reprocess_reuses_ocr_applies_correction_and_replaces_vectors():
    """No new OCR call; changed text changes chunk ids; stale vectors deleted."""
    world = _world()
    first = world.run()
    first_ids = world.chunk_ids()
    assert first["corrections_applied"] == 0 and world.ocr.calls == 1
    embedded_before = world.embedder.calls

    add_correction(world.db, "cor-p18")  # page_count is known now: 18 is valid
    result = world.run(FakeStep())
    new_ids = world.chunk_ids()

    assert world.ocr.calls == 1 and result["ocr_reused"] is True
    assert result["corrections_applied"] == 1
    assert "igual o superior a 3.0" in _texts(world)
    changed = set(first_ids) ^ set(new_ids)
    assert changed  # the corrected chunk got a new id (its text hash changed)
    assert set(world.index.vectors) == set(new_ids)
    stale = set(first_ids) - set(new_ids)
    assert result["vectors_deleted"] == len(stale) > 0
    assert sorted(stale) == sorted(i for batch in world.index.deletes for i in batch)
    assert world.embedder.calls == embedded_before + 1  # the batch was re-embedded
    version = world.db.version(VERSION_ID)
    assert version["status"] == "review_required" and version["requires_review"] == 1

    # Deleting the correction and reprocessing restores the original chunks.
    world.db.conn.execute(
        "UPDATE text_corrections SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', "
        "'now', '+1 second')"
    )
    world.run(FakeStep())
    assert world.chunk_ids() == first_ids and world.ocr.calls == 1
    assert set(world.index.vectors) == set(first_ids)


# -- pending corrections (corrections_dirty) ---------------------------------------
def _dirty(world: World) -> tuple[int, int]:
    row = world.db.version(VERSION_ID)
    return row["corrections_dirty"], row["corrections_rev"]


def test_applied_corrections_clear_the_pending_flag():
    """Creating a correction marks the version dirty; the chunk step clears it."""
    world = _world()
    add_correction(world.db, "cor-p18")
    assert _dirty(world) == (1, 1)
    result = world.run()
    assert result["corrections_pending"] is False
    assert _dirty(world) == (0, 1)


class _RacingRepository(D1VersionRepository):
    """Creates a correction right after the chunk step read the corrections."""

    def __init__(self, db: FakeD1, **correction: object):
        super().__init__(db)
        self._db_fake = db
        self._correction = correction

    async def correction_set(self, version_id: str):
        found = await super().correction_set(version_id)
        if self._correction:
            add_correction(self._db_fake, **self._correction)  # type: ignore[arg-type]
            self._correction = {}
        return found


def test_correction_created_during_the_chunk_step_stays_pending():
    """A correction added after the read is not applied: the flag stays set."""
    world = _world()
    world.run()  # page_count = 18 is known now
    add_correction(world.db, "cor-p18")
    racing = _RacingRepository(
        world.db, correction_id="cor-late", page=3, find="TEMA 3", replace="TEMA III"
    )
    result = world.run(FakeStep(), repository=racing)
    assert result["corrections_applied"] == 1  # only cor-p18
    assert result["corrections_pending"] is True
    assert "TEMA 3" in _texts(world)
    assert _dirty(world) == (1, 2)
    events = _events(world)
    assert any(
        e[0] == "chunk" and e[3].endswith("corrections_rev=1 corrections_pending=1")
        for e in events
    )
    # The version cannot be published until a new run applies cor-late.
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'ready', requires_review = 0 "
        "WHERE id = ?",
        (VERSION_ID,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="corrections_pending"):
        world.db.conn.execute(
            "UPDATE document_versions SET status = 'published', "
            "published_at = '2030-01-01T00:00:00.000Z' WHERE id = ?",
            (VERSION_ID,),
        )
    result = world.run(FakeStep())
    assert result["corrections_applied"] == 2 and not result["corrections_pending"]
    assert "TEMA III" in _texts(world) and _dirty(world) == (0, 2)


# -- corrections target the prepared text (what the administrator sees) ------------
TABLE_PAGE = (
    "Texto previo del documento con contenido.\n\n<table><tr><th>Programa</th>"
    "<th>Nota</th></tr><tr><td>Ciencia &amp; Tecnologia</td><td><b>igual</b> a "
    "3.0</td></tr></table>"
)


def _chunk_texts(pages: list[PageText], corrections=()) -> list[str]:
    chunker = StructuralChunker()
    prepared, _ = apply_corrections(chunker.prepare(pages), list(corrections))
    return [c.text for c in chunker.chunk_prepared(prepared, version_id="v1")]


@pytest.mark.parametrize(
    "find",
    [
        "Ciencia & Tecnologia",
        "igual a 3.0",
        "| Ciencia & Tecnologia | igual a 3.0 |",  # a whole row copied from a chunk
    ],
)
def test_text_copied_from_a_table_chunk_matches(find):
    """HTML tables are matched as the Markdown rows shown in the chunks."""
    pages = [PageText(1, TABLE_PAGE)]
    assert "| Ciencia & Tecnologia | igual a 3.0 |" in _chunk_texts(pages)[0]
    replace = find.replace("Tecnologia", "Tecnología").replace("3.0", "3,0")
    texts = _chunk_texts(pages, [TextCorrection("c1", 1, find, replace)])
    assert replace in texts[0] and "<table" not in texts[0]


def test_raw_html_is_not_matchable():
    """Markup the administrator never sees does not count as a match."""
    with pytest.raises(IngestionError, match="correction_not_applicable") as info:
        _chunk_texts([PageText(1, TABLE_PAGE)], [TextCorrection("c1", 1, "&amp;", "y")])
    assert info.value.detail.endswith("matches=0")


HEADER = "Universidad Internacional del Tropico Americano"


def _boilerplate_pages() -> list[PageText]:
    pages = [
        PageText(i, f"{HEADER}\n\nContenido de la pagina {i} sobre becas y apoyos.")
        for i in range(1, 5)
    ]
    pages[2] = PageText(3, f"{HEADER}\n\nLa {HEADER} otorga becas.")
    return pages


def test_hidden_running_header_does_not_count():
    """A repeated header removed from the chunks is not a second match."""
    pages = _boilerplate_pages()
    assert "\n".join(_chunk_texts(pages)).count(HEADER) == 2  # p1 header + p3 body
    texts = _chunk_texts(pages, [TextCorrection("c1", 3, "Tropico", "Trópico")])
    joined = "\n".join(texts)
    assert "La Universidad Internacional del Trópico Americano otorga" in joined
    assert joined.count("Tropico") == 1  # the header kept on page 1


def test_replacement_keeps_untouched_lines_and_normalizes_touched_ones():
    """Spaces of the replacement are collapsed like the chunker's normalization."""
    prepared = StructuralChunker().prepare([PageText(1, TABLE_PAGE)])
    corrected, _ = apply_corrections(
        prepared, [TextCorrection("c1", 1, "previo", "  previo​  y ")]
    )
    lines = [line for line in corrected[0].text.split("\n") if line]
    assert lines[0] == "Texto previo y del documento con contenido."
    assert lines[1:] == [line for line in prepared[0].text.split("\n") if line][1:]


def test_no_corrections_keep_the_exact_chunks():
    """Prepare + chunk_prepared without corrections == chunk (same ids)."""
    for pages in (_boilerplate_pages(), [PageText(1, TABLE_PAGE)]):
        chunker = StructuralChunker(min_chars=20)
        prepared, applied = apply_corrections(chunker.prepare(pages), [])
        assert applied == []
        assert chunker.chunk_prepared(prepared, version_id="v") == chunker.chunk(
            pages, version_id="v"
        )
