# Esquema del dataset de evaluación RAG

Formato: JSON Lines (`*.jsonl`), un objeto por línea, UTF-8. La validación está en
`src/unitropico_eval/dataset.py` (`python -m unitropico_eval validate`) y es
estricta: claves desconocidas, páginas fuera de rango o un ítem `answer` sin oro
son errores.

## Ítem

| Campo | Tipo | Obligatorio | Regla |
|---|---|---|---|
| `id` | str | sí | Único en el archivo. Convención: `in-`, `val-`, `con-`, `fu-`, `oos-`, `blk-` + número. |
| `split` | `"dev"` \| `"test"` | sí | `test` se congela (ver README). |
| `category` | `"in_scope"` \| `"out_of_scope"` \| `"validity"` \| `"contradiction"` \| `"follow_up"` | sí | |
| `question` | str | sí | 1..500 caracteres tras `strip` (mismo límite que `/answer` y `/retrieval/query`). Redactada como la escribiría un estudiante. |
| `history` | lista de `{role: "user"\|"assistant", content: str}` | solo `follow_up` | 1..6 turnos, `content` 1..2000 caracteres (contrato `/answer` v1). Obligatorio y no vacío en `follow_up`. |
| `expected_behavior` | `"answer"` \| `"abstain"` | sí | `answer` exige `gold` no vacío. |
| `gold` | lista de entradas de oro | sí | Vacía en ítems `abstain`. |
| `reference_answer` | str | no | Solo si puede extraerse **literalmente** del texto (copia del texto extraído, incluidos sus defectos de OCR). Nunca una paráfrasis inventada. |
| `source` | `"draft_from_corpus"` \| `"validated_by_staff"` | sí | |
| `validated` | bool | sí | Un `draft_from_corpus` no puede tener `validated: true`. Al validar, el revisor cambia ambos campos. |
| `notes` | str | sí | Contexto para el revisor (vigencia, ambigüedades, calidad OCR). |
| `flags` | lista | no | Valores: `blocked_by_missing_ocr` (evidencia en páginas sin texto; se excluye de las métricas por defecto), `ocr_defect_on_gold_page`, `multi_document`. |

## Entrada de oro

| Campo | Tipo | Obligatorio | Regla |
|---|---|---|---|
| `document` | str | sí | Identificador estable del documento: clave de `datasets/document_map.json` (p. ej. `res-cbu-002-2025`). |
| `pages` | lista de int | sí | **Páginas físicas del PDF** (1-based), no la numeración impresa. Deben estar en `1..page_count`. Vacía solo con `blocked_by_missing_ocr`. |
| `section` | str \| null | no | Artículo, numeral o apartado tal como aparece en el texto. `null` si el documento no tiene sección identificable. |
| `note` | str | no | Qué contiene la página que respalda la respuesta. |

## Coincidencia con la salida del RAG

Un fragmento recuperado (`/retrieval/query`) o una cita (`/answer`) coincide con
una entrada de oro si **(a)** se resuelve al mismo `document` mediante
`document_map.json` y **(b)** el rango inclusivo `[page_start, page_end]` interseca
`pages`. Resolución del documento, en orden: `source_sha256` (solo lo expone
`/retrieval/query`), `document_id`, `version_id`, título normalizado
(minúsculas, sin tildes ni signos). Lo que no se resuelve cuenta como "sin
mapear" y nunca como acierto.

## `document_map.json`

```json
{"documents": {"<id estable>": {
  "source_name": "archivo original.pdf",
  "sha256": ["<sha256 del PDF>"],
  "page_count": 16,
  "document_ids": ["<documents.id en D1>"],
  "version_ids": ["<document_versions.id en D1>"],
  "titles": ["<documents.title en D1>", "alias"]}}}
```

Los `sha256` se tomaron de `evaluations/ocr/.artifacts/extraction-corpus-v2/documents.json`
(`direct_document.sha256_before`). Tras la ingesta real hay que añadir
`document_ids`/`version_ids` (y el título exacto que registre el administrador):
las citas de `/answer` no llevan `source_sha256`, así que sin esos campos solo
se resolverían por título. Un mismo valor asignado a dos documentos es un error.
