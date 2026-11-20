"""Proposed full-page override of DOCUMENTO TÉCNICO p54 (contract 4c).

The final source of DT p54 is the Azure pilot markdown (``azure_pilot_v1``):
words and BPIN are exact, but the "Insumos" diagram (five columns) comes out
interleaved. Contract 4c only replaces a WHOLE page, so the override text is
the PREPARED page (what the admin sees: ``StructuralChunker().prepare``) with
the interleaved ``<figure>`` block replaced by ``INSUMOS_TRANSCRIPTION``.

``INSUMOS_TRANSCRIPTION`` is a PROPOSAL transcribed from the PyMuPDF render
of the original page (see README, "Caché final y semillas"): it must be
checked by a person against the PNG before registering it
(``requires_human_verification``). The top flow labels other than "Insumos"
have very low contrast in the original; their reading is proposed.
The pilot correction "argo plaza" -> "largo plazo" lies inside the replaced
block, so it is integrated here and the correction is registered only when
the override is NOT used.
"""

import sys

sys.dont_write_bytecode = True
from final_sources import require

DOCUMENT = "documento-tecnico"
PAGE = 54
BLOCK_START = "<figure>\n\nInsumos\n"
BLOCK_END_AFTER = "DNP: Dirección de Desarrollo Social\n\n</figure>"
REASON = (
    "Diagrama de Insumos (5 columnas) intercalado por el OCR: transcripción "
    "propuesta desde la imagen; requiere verificación humana."
)
INSUMOS_TRANSCRIPTION = """\
Insumos → Actividades → Resultados de corto plazo → Resultados de mediano y largo plazo

Insumos

Normatividad
- Constitución política
- Ley 1532 de 2012, modificada parcialmente por la Ley 1948 de 2019.
- Resolución No 01970 de 2012 Decreto 4290 de 2005
- Ley 2294 de 2023. Plan Nacional de Desarrollo 2022-2026
- Ley 2307 de 2023
- Decreto Ley 1960 de 2023
- Resolución No 0037 del 25 de enero de 2024

Presupuesto
- Presupuesto General de la Nación. Proyecto de inversión “BPIN 202300000000204 - Implementación de Transferencias Monetarias para población en situación de pobreza o vulnerabilidad a nivel Nacional - $4.1 billones COP.
- Marco de Gasto de Mediano Plazo
- Marco Fiscal de Mediano Plazo

Recursos
- Fuentes de información: Registros Social de Hogares (RSH), Sisbén, otras; SNIES
- Sistema de información del Programa Renta Joven
- Sistema Integrado de Información de Transferencias Monetarias (SIITM)
- Humano:
✓ DPS: Subdirección General de Programas y Proyectos, Dirección de Transferencias Monetarias (DTM), Subdirección de Transferencias Monetarias Condicionadas (STMC), Grupo Interno de Trabajo (GIT) Renta Joven
✓ Direcciones Regionales
✓ Alcaldías: Coordinadores municipales de programas de transferencias monetarias, Promotores social y comunitario, Gestores operativos y Gestores indígenas
✓ DNP: Dirección de Desarrollo Social

Documentos
- Evaluación del Programa Jóvenes en Acción- 2017
- Documento Técnico
- Manual Operativo
- Mapeo de oferta
- Módulos de capacitación de Habilidades para la Vida Socioemocional
- Guías Impulso Voluntario

Institucional
- Departamento Administrativo para la Prosperidad Social
- Departamento Nacional de Planeación
- Ministerio de Educación Nacional
- Ministerio del Trabajo
- Registraduría Nacional del Estado Civil
- Ministerio de la Igualdad
- SENA
- Universidades en Convenio
- Normales Superiores
- Entidades Territoriales"""
# Facts the transcription must keep (checked by tests and the assembly).
KEY_FACTS = (
    "BPIN 202300000000204",
    "$4.1 billones COP",
    "Resolución No 0037 del 25 de enero de 2024",
    "Resultados de mediano y largo plazo",
    "Gestores indígenas",
    "Registraduría Nacional del Estado Civil",
)
# The pilot correction integrated in the override (registered only without it).
INTEGRATED_FIND = "argo plaza"


def override_text(prepared_page: str) -> str:
    """Prepared p54 with the interleaved Insumos ``<figure>`` block replaced."""
    require(prepared_page.count(BLOCK_START) == 1, "Insumos block start not unique")
    start = prepared_page.index(BLOCK_START)
    require(prepared_page.count(BLOCK_END_AFTER) == 1, "Insumos block end not unique")
    end = prepared_page.index(BLOCK_END_AFTER) + len(BLOCK_END_AFTER)
    require(start < end, "Insumos block order")
    text = prepared_page[:start] + INSUMOS_TRANSCRIPTION + prepared_page[end:]
    require(INTEGRATED_FIND not in text, "Uncorrected label left in override")
    require(all(fact in text for fact in KEY_FACTS), "Override lost a key fact")
    require(1 <= len(text.strip()) <= 20000, "Override length")
    return text
