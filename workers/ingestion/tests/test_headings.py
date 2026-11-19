import pytest

from unitropico_ingestion.services.headings import detect_heading
from unitropico_ingestion.services.tables import parse_html_table


@pytest.mark.parametrize(
    ("line", "label"),
    [
        ("ARTÍCULO 14. CRONOGRAMA DE LA CONVOCATORIA PÚBLICA. En el", "Artículo 14"),
        ("ARTICULO 2. AMBITO DE APLICACION. La presente", "Artículo 2"),
        ("ARliCULO 1. DBJETG. El oresente actc", "Artículo 1"),
        ("ARTÍCULO 5°. Requisitos.", "Artículo 5"),
        ("ARTÍCULO N° 7. Monto.", "Artículo 7"),
        ("Art. 3. Alcance del programa.", "Artículo 3"),
        ("Artículo 1.1.3. Definiciones. Para efectos", "Artículo 1.1.3"),
        ("ARTÍCULO PRIMERO. OBJETO. Expedir la Política", "Artículo primero"),
        ("ARTÍCULO DÉCIMO PRIMERO. Vigencia.", "Artículo décimo primero"),
        ("**ARTÍCULO 9.** Criterios de asignación.", "Artículo 9"),
        ("PARAGRAFO 2. Los estudiantes que se encuentren", "Parágrafo 2"),
        ("Parágrafo. Prosperidad Social podrá recurrir", "Parágrafo"),
        ("PARÁGRAFO ÚNICO. El incumplimiento", "Parágrafo único"),
        ("PARÁGRAFO TRANSITORIO: Durante 2026", "Parágrafo transitorio"),
        ("CAPÍTULO 2", "Capítulo 2"),
        ("## CAPÍTULO 3", "Capítulo 3"),
        ("CAPITULO III", "Capítulo III"),
        ("TlTULO 1", "Título 1"),
        ("SECCIÓN 2. Disposiciones comunes", "Sección 2"),
        ("1. INTRODUCCIÓN", "1 INTRODUCCIÓN"),
        ("5.1.2. Objetivos Específicos", "5.1.2 Objetivos Específicos"),
        ("## CONSIDERANDO", "Considerando"),
        ("RESUELVE:", "Resuelve"),
        ("## LA DIRECTORA DEL DEPARTAMENTO", "LA DIRECTORA DEL DEPARTAMENTO"),
    ],
)
def test_detects_spanish_normative_headings_and_ocr_variants(line, label):
    """Accents, case, ordinal words and common OCR confusions are tolerated."""
    heading = detect_heading(line)
    assert heading is not None
    assert heading.label == label


@pytest.mark.parametrize(
    ("line", "previous"),
    [
        ("Artículos 45, 67, 69 y 70 de la Constitución.", None),
        ("artículo 5 del presente acto.", None),
        ("Artículo 2 de la Resolución No. 12703 de 2021", None),
        ("Artículo 45. de la Ley 1437 de 2011", "de conformidad con el"),
        ("1. Identificación de los efectos atribuibles al Programa", None),
        ("1. JUSTIFICACION ..................................... 4", None),
        ("9 Cra 19 N. 39 - 40 Ciudadela Universitaria, Yopal", None),
        ("2015 en el Sistema de Información de Matrícula", None),
        ("a) Para el SENA", None),
        ("título profesional universitario y o posgrado.", None),
    ],
)
def test_rejects_citations_lists_toc_and_footers(line, previous):
    """Inline citations, enumerations, TOC lines and footers are not headings."""
    assert detect_heading(line, previous) is None


def test_html_table_repeats_spanned_cells():
    """Spanned cells (colspan, rowspan) repeat content into every covered cell."""
    table = parse_html_table(
        "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
        '<tr><td rowspan="2">x</td><td colspan="2">y</td></tr>'
        "<tr><td>p|q</td><td>r</td></tr></table>",
        page=7,
    )
    assert table is not None
    assert table.render() == (
        "| A | B | C |\n| --- | --- | --- |\n| x | y | y |\n| x | p\\|q | r |"
    )
    assert (table.page_start, table.page_end) == (7, 7)
