import hashlib
from pathlib import Path

import pytest
from conftest import VERSION_ID

from unitropico_ingestion.domain.chunking import MAX_CHUNK_ID_BYTES, PageText
from unitropico_ingestion.services.chunker import (
    STRUCTURAL_CHUNKER_VERSION,
    StructuralChunker,
)
from unitropico_ingestion.services.ocr_parser import parse_ocr_response

RESOLUCION = [
    PageText(
        1,
        "Resolución CBU No. 002 de 2025\n(27 de octubre de 2025)\n\n"
        "CONSIDERANDO\n\nQue la Universidad requiere reglamentar la convocatoria "
        "del apoyo socioeconómico de alimentación.\n\nRESUELVE:\n\n"
        "ARTICULO 1. OBJETO. El presente acto tiene como objeto reglamentar la\n"
        "convocatoria pública 2025-2 del Apoyo Socioeconómico de Alimentación.\n\n"
        "PARAGRAFO 1. La convocatoria inicia el día 27 de octubre de 2025.\n",
    ),
    PageText(
        2,
        "ARTÍCULO 2. ÁMBITO DE APLICACIÓN. La presente convocatoria está dirigida\n"
        "a los estudiantes de pregrado que hayan culminado su primer periodo.\n\n"
        "Art. 3. ALCANCE. Se otorga el apoyo en dinero o mediante bono redimible,\n"
        "de conformidad con el\nArtículo 45. de la Ley 1437 de 2011 y las normas\n"
        "vigentes de la Universidad para el programa Bien-Estar Contigo.\n",
    ),
]


def sections(chunks):
    """Return the section of every chunk."""
    return [chunk.section for chunk in chunks]


def test_detects_articles_paragraphs_and_preamble_without_inventing():
    """Front matter has no section; articles are cited by canonical label."""
    chunks = StructuralChunker(min_chars=20).chunk(RESOLUCION, version_id="v1")
    assert sections(chunks) == [
        None,
        "Considerando",
        "Artículo 1",
        "Artículo 2",
        "Artículo 3",
    ]
    assert chunks[2].text.startswith("RESUELVE:\n\nARTICULO 1. OBJETO.")
    assert "PARAGRAFO 1." in chunks[2].text
    assert "Artículo 45. de la Ley" in chunks[4].text
    assert [(c.page_start, c.page_end) for c in chunks][-2:] == [(2, 2), (2, 2)]


def test_builds_hierarchical_path_with_chapter_names_as_text():
    """TÍTULO/CAPÍTULO nest articles; a Markdown name line is not a section."""
    page = PageText(
        5,
        "TÍTULO 1\n\n## PROGRAMA RENTA JOVEN\n\nCAPÍTULO II\n\n"
        "## DISPOSICIONES GENERALES\n\n"
        "Artículo 1.1.1. Objeto. La presente resolución reglamenta los criterios.\n\n"
        "## RESOLUCIÓN N.° 00137 DEL 25 ENE 2024\n\n"
        "Artículo 14. Finalidad. Contribuir a la inclusión social de los jóvenes.\n\n"
        "CAPÍTULO III\n\nArtículo 15. Componentes. Son componentes los siguientes.",
    )
    chunks = StructuralChunker(min_chars=20).chunk([page], version_id="v1")
    assert sections(chunks) == [
        "Título 1 > Capítulo II > Artículo 1.1.1",
        "Título 1 > Capítulo II > Artículo 14",
        "Título 1 > Capítulo III > Artículo 15",
    ]
    assert chunks[0].text.startswith(
        "TÍTULO 1\n\n## PROGRAMA RENTA JOVEN\n\nCAPÍTULO II"
    )


def test_paragraph_section_when_split_inside_article():
    """A chunk starting inside a PARÁGRAFO reports Artículo > Parágrafo."""
    body = "El estudiante deberá presentar los soportes requeridos. " * 6
    page = PageText(
        3,
        f"ARTÍCULO 5. REQUISITOS GENERALES. {body}\n\nPARÁGRAFO 2. {body}\n\n"
        f"PARÁGRAFO 3. {body}",
    )
    chunks = StructuralChunker(max_chars=400, min_chars=50).chunk(
        [page], version_id="v"
    )
    assert chunks[0].section == "Artículo 5"
    assert "Artículo 5 > Parágrafo 2" in sections(chunks)
    assert sections(chunks)[-1] == "Artículo 5 > Parágrafo 3"


def test_ordinal_words_and_ocr_variants_in_politica_style():
    """ARTÍCULO PRIMERO / ARTICULO TERCERO keep the ordinal word as written."""
    page = PageText(
        1,
        "ARTÍCULO PRIMERO. OBJETO. Expedir la Política Integral de Bienestar.\n\n"
        "ARTICULO SEGUNDO. COMPETENCIA. La implementación corresponde a la oficina.\n\n"
        "ARliCULO TERCERO. DELEGACIÓN. Facultar al Rector para reglamentar.",
    )
    chunks = StructuralChunker(min_chars=10).chunk([page], version_id="v")
    assert sections(chunks) == [
        "Artículo primero",
        "Artículo segundo",
        "Artículo tercero",
    ]


def test_numbered_headings_nest_by_number_and_ignore_toc_and_lists():
    """Dotted headings nest by prefix; TOC pages and enumerations are not headings."""
    toc = PageText(
        2,
        "\n".join(f"{n}. CAPITULO {n} ................ {n + 2}" for n in range(1, 7)),
    )
    body = PageText(
        3,
        "4. OBJETIVOS\n\nEl programa busca la inclusión social.\n\n"
        "4.2 Objetivos específicos\n\n"
        "1. Identificación de los efectos atribuibles al programa en el acceso\n"
        "2. Caracterización del uso del incentivo monetario\n\n"
        "5. FOCALIZACIÓN\n\nSe prioriza a la población vulnerable.\n\n"
        "5.3.1 Cobertura territorial\n\nEl programa opera en todo el país.",
    )
    chunks = StructuralChunker(min_chars=10).chunk([toc, body], version_id="v")
    assert sections(chunks) == [
        None,
        "4 OBJETIVOS",
        "4 OBJETIVOS > 4.2 Objetivos específicos",
        "5 FOCALIZACIÓN",
        "5 FOCALIZACIÓN > 5.3.1 Cobertura territorial",
    ]
    assert "1. Identificación" in chunks[2].text
    assert "2. Caracterización" in chunks[2].text


def test_wrapped_uppercase_title_is_joined():
    """A title ending in a connector word continues on the next short line."""
    page = PageText(
        9,
        "9.5. COMPONENTE DE PROMOCIÓN SOCIAL Y BIENESTAR INTEGRAL PARA LA\n"
        "JUVENTUD\nEl componente agrupa las líneas de acompañamiento.",
    )
    [chunk] = StructuralChunker(min_chars=10).chunk([page], version_id="v")
    assert chunk.section == (
        "9.5 COMPONENTE DE PROMOCIÓN SOCIAL Y BIENESTAR INTEGRAL PARA LA JUVENTUD"
    )


def test_section_is_none_without_headings():
    """Plain prose never receives an invented section."""
    pages = [
        PageText(1, "El boletín informa sobre el programa Renta Joven. " * 30),
        PageText(2, "Los jóvenes reciben transferencias monetarias. " * 30),
    ]
    chunks = StructuralChunker().chunk(pages, version_id="v")
    assert chunks and all(chunk.section is None for chunk in chunks)


def test_cross_page_table_kept_whole_with_real_page_range(convocatoria_pages):
    """The 13–14 schedule table is one unit spanning both physical pages."""
    chunks = StructuralChunker(max_chars=4000).chunk(convocatoria_pages, version_id="v")
    table_chunk = next(c for c in chunks if "| Actividad |" in c.text)
    assert (table_chunk.page_start, table_chunk.page_end) == (13, 14)
    assert table_chunk.section == "Artículo 14"
    assert table_chunk.text.count("| Actividad |") == 1
    assert "<table" not in table_chunk.text
    # OCR promoted the first page-14 row to <thead>: it is kept as data.
    assert "| Verificación de requisitos. |" in table_chunk.text
    # colspan repeats the single date under both date columns.
    assert "| 26 de agosto de 2026 | 26 de agosto de 2026 |" in table_chunk.text


def test_oversized_table_splits_by_rows_repeating_header(convocatoria_pages):
    """When a table exceeds max_chars, every part restates the header."""
    chunks = StructuralChunker(max_chars=420, min_chars=100).chunk(
        convocatoria_pages, version_id="v"
    )
    parts = [c for c in chunks if "| --- |" in c.text]
    assert len(parts) >= 3
    header = (
        "| Actividad | Descripción | Fecha Inicial | Fecha Final |\n"
        "| --- | --- | --- | --- |"
    )
    assert all(header in part.text for part in parts)
    assert all(len(c.text) <= 420 for c in chunks)
    assert all(c.section == "Artículo 14" for c in parts)
    assert parts[-1].page_end == 14 and parts[0].page_start == 13
    lines = [line for part in parts for line in part.text.split("\n")]
    rows = [line for line in lines if line.startswith("|") and line not in header]
    assert len(rows) == len(set(rows)) == 5


def test_pages_without_text_produce_no_chunks(convocatoria_pages):
    """The 15 pages without OCR yield nothing: chunks only cover pages 13–14."""
    chunks = StructuralChunker().chunk(convocatoria_pages, version_id="v")
    assert chunks
    assert all(13 <= c.page_start <= c.page_end <= 14 for c in chunks)
    assert all(c.text.strip() for c in chunks)
    assert chunks[0].section is None  # continues an article from missing page 12
    empty = [PageText(n, " \n\t ") for n in range(1, 16)]
    assert StructuralChunker().chunk(empty, version_id="v") == []
    assert StructuralChunker().chunk([], version_id="v") == []


def test_article_continuing_on_next_page_records_both_pages():
    """A sentence broken by a page break is merged and spans both pages."""
    pages = [
        PageText(3, "ARTÍCULO 8. CAUSALES DE PÉRDIDA. Se pierde el apoyo cuando el"),
        PageText(4, "estudiante cancela el semestre o incumple sus obligaciones."),
    ]
    [chunk] = StructuralChunker(min_chars=10).chunk(pages, version_id="v")
    assert (chunk.page_start, chunk.page_end) == (3, 4)
    assert "cuando el estudiante cancela" in chunk.text
    assert chunk.section == "Artículo 8"


def test_deterministic_ids_within_vectorize_limit(convocatoria_pages):
    """Same input gives identical chunks; ids fit 64 bytes even for long versions."""
    long_version = VERSION_ID + "/" + "x" * 200
    first = StructuralChunker().chunk(RESOLUCION, version_id=long_version)
    second = StructuralChunker().chunk(list(RESOLUCION), version_id=long_version)
    assert first == second
    assert [c.ordinal for c in first] == list(range(len(first)))
    assert len({c.id for c in first}) == len(first)
    assert all(len(c.id.encode()) <= MAX_CHUNK_ID_BYTES for c in first)
    assert all(c.chunker_version == STRUCTURAL_CHUNKER_VERSION for c in first)
    other = StructuralChunker().chunk(RESOLUCION, version_id="otra-version")
    assert {c.id for c in first}.isdisjoint(c.id for c in other)


def test_size_limits_hold_for_unpunctuated_text_and_overlap():
    """No chunk exceeds max_chars, even with overlap or a giant word."""
    text = "palabra " * 900 + "x" * 2500
    chunker = StructuralChunker(max_chars=600, min_chars=100, overlap_chars=80)
    chunks = chunker.chunk([PageText(1, text)], version_id="v")
    assert len(chunks) > 5
    assert all(len(c.text) <= 600 for c in chunks)
    assert chunks[1].text.startswith("palabra")
    joined = "".join(c.text for c in chunks)
    assert joined.count("x") >= 2500


def test_overlap_repeats_tail_of_previous_chunk_only_within_section():
    """Overlap copies context inside a split section, never across articles."""
    body = "Primera oración del artículo. " * 40
    page = PageText(
        1,
        f"ARTÍCULO 1. OBJETO. {body}\n\nARTÍCULO 2. ALCANCE. Texto breve del alcance.",
    )
    chunker = StructuralChunker(max_chars=500, min_chars=50, overlap_chars=60)
    chunks = chunker.chunk([page], version_id="v")
    article_1 = [c for c in chunks if c.section == "Artículo 1"]
    assert len(article_1) >= 2
    tail = article_1[0].text[-40:]
    assert tail.split(" ", 1)[1] in article_1[1].text[:80]
    assert chunks[-1].text.startswith("ARTÍCULO 2.")


def test_page_markers_are_removed_from_text():
    """Inline page markers never leak into chunk text."""
    page = PageText(1, "Texto inicial del documento.<!-- page 2 -->\nTexto final.")
    [chunk] = StructuralChunker(min_chars=10).chunk([page], version_id="v")
    assert "<!--" not in chunk.text


def test_running_headers_are_stripped_after_first_occurrence():
    """A header repeated on most pages is kept once, not in every chunk."""
    header = "Universidad Internacional del Trópico Americano / NIT. 844002071-4"
    pages = [
        PageText(
            n, f"{header}\n\nARTÍCULO {n}. TEMA. Contenido propio del artículo {n}."
        )
        for n in range(1, 6)
    ]
    chunks = StructuralChunker(min_chars=10).chunk(pages, version_id="v")
    assert sum(header in c.text for c in chunks) == 1
    assert sections(chunks)[-1] == "Artículo 5"


@pytest.mark.parametrize(
    "kwargs",
    [{"max_chars": 100, "min_chars": 100}, {"min_chars": 0}, {"overlap_chars": 1000}],
)
def test_rejects_invalid_parameters(kwargs):
    """Inconsistent size parameters fail fast."""
    with pytest.raises(ValueError):
        StructuralChunker(**kwargs)


@pytest.mark.parametrize(
    "after", ["\n\nJUVENTUD corta", "\n| a | b |\n| 1 | 2 |\nJUVENTUD"]
)
def test_wrapped_title_interrupted_by_blank_line_or_table(after):
    """A blank line or a table closes a pending wrapped title (used to crash)."""
    page = PageText(1, "9.5 COMPONENTE DE PROMOCIÓN PARA LA" + after)
    chunks = StructuralChunker(min_chars=10).chunk([page], version_id="v")
    assert "JUVENTUD" in chunks[-1].text
    assert chunks[0].section == "9.5 COMPONENTE DE PROMOCIÓN PARA LA"


# Chunk ids computed with the chunker before ``prepare``/``chunk_prepared``
# existed (HTML tables were converted inside the segmentation): moving table
# conversion into the prepared pages must not change a single id.
FIXTURE = Path(__file__).parent / "fixtures/firecrawl_parse_response.json"
_PINNED_IDS = {
    "fixture": "dd145c132ce307fc142f01175beb0da5bb0d36e45b2993f46c7662e10a06db34",
    "table": "986e157d03dee0ac37f7f1f80a39b6e342e6d128eb90c68e0fe12160cdfae104",
    "merge": "de045d662d53806d4c71833e00dab8c1263c6f5055aaf752df61bc43bac6e77a",
}
_TABLE_PAGES = [
    PageText(
        1,
        "ARTÍCULO 1. OBJETO. Texto previo del documento con contenido suficiente."
        "\n\n<table><tr><th>Programa</th><th>Nota</th></tr><tr><td>Ciencia &amp; "
        "Tecnolog&iacute;a</td><td><b>igual</b> a 3.0</td></tr><tr><td></td><td>"
        "vacío</td></tr></table> texto después\n\nPARÁGRAFO. Otro texto.",
    ),
    PageText(
        2,
        "<table><tr><th>Programa</th><th>Nota</th></tr><tr><td>Artes</td><td>4.0"
        "</td></tr></table>\n\ncontinuación del texto final.",
    ),
]
_MERGE_PAGES = [
    PageText(
        1,
        "ARTÍCULO 2. CUPOS.\n<table><tr><th>Programa</th><th>Cupos</th></tr><tr>"
        "<td>Artes</td><td>10</td></tr></table>",
    ),
    PageText(
        2,
        "<table><tr><th>Programa</th><th>Cupos</th></tr><tr><td>M&uacute;sica</td>"
        "<td>5</td></tr></table>\nARTÍCULO 3. VIGENCIA. Rige desde su expedición.",
    ),
]


def _ids_digest(chunks) -> str:
    return hashlib.sha256("\n".join(c.id for c in chunks).encode()).hexdigest()


def test_chunk_ids_do_not_change_with_prepared_pages():
    """Same ids as before the refactor, on the fixture and on table cases."""
    raw = parse_ocr_response(FIXTURE.read_bytes())
    fixture = [PageText(p.page_number, p.markdown) for p in raw.pages]
    cases = {
        "fixture": (fixture, StructuralChunker()),
        "table": (_TABLE_PAGES, StructuralChunker(min_chars=20)),
        "merge": (_MERGE_PAGES, StructuralChunker(min_chars=20)),
    }
    for name, (pages, chunker) in cases.items():
        chunks = chunker.chunk(pages, version_id="ver-pin")
        assert _ids_digest(chunks) == _PINNED_IDS[name], name
        prepared = chunker.prepare(pages)
        assert all("<table" not in page.text for page in prepared)
        assert chunker.chunk_prepared(prepared, version_id="ver-pin") == chunks
