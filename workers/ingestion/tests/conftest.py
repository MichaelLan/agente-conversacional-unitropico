import pytest

from unitropico_ingestion.domain.chunking import PageText

VERSION_ID = "sha256:" + "a" * 64

CONVOCATORIA_P13 = """estudiantes que hayan entregado oportunamente la documentación requerida.

ARTÍCULO 14. CRONOGRAMA DE LA CONVOCATORIA PÚBLICA. En el cronograma se describen las etapas, de la siguiente manera:

| Actividad | Descripción | Fecha Inicial | Fecha Final |
| --- | --- | --- | --- |
| Divulgación de la convocatoria. | Publicación en la página web institucional. | 04 de agosto de 2026 | 14 de agosto de 2026 |
| Inscripción. | Diligenciamiento del formulario de postulación. |  | 14 de agosto de 2026 6:00 pm |
"""  # noqa: E501

CONVOCATORIA_P14 = (
    "<table><thead><tr><th>Verificación de requisitos.</th>"
    "<th>Solo serán admitidos quienes acrediten los requisitos.</th>"
    "<th>17 de agosto de 2026</th><th>26 de agosto de 2026</th></tr></thead>"
    "<tbody><tr><td>Reunión del Comité.</td>"
    "<td>Se socializa el listado preliminar.</td>"
    '<td colspan="2">26 de agosto de 2026</td></tr>'
    "<tr><td>Reclamaciones.</td><td>Ante la Oficina de Bienestar Universitario.</td>"
    "<td>26 de agosto de 2026</td><td>28 de agosto de 2026</td></tr></tbody></table>"
)


@pytest.fixture
def convocatoria_pages() -> list[PageText]:
    """Mimic Convocatoria 2026-1: 17 pages, OCR text only on pages 13 and 14."""
    texts = {4: "   \n\n  ", 13: CONVOCATORIA_P13, 14: CONVOCATORIA_P14}
    return [PageText(n, texts.get(n, "")) for n in range(1, 18)]
