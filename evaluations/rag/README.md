# Evaluación del RAG (recuperación y respuesta)

Harness sin dependencias de runtime (stdlib + `urllib`) que ejecuta un dataset
de preguntas contra el contrato HTTP del RAG (`POST /retrieval/query`,
`POST /answer`; o la API: `/retrieval/query`, `/chat`) y calcula métricas por
ítem, por categoría y por split.

> **Estado (2026-11-18): no hay resultados de evaluación.** El dataset es un
> **borrador sin validar** (`validated: false` en los 55 ítems) y el RAG no
> tiene todavía el corpus indexado (D1 remota, Vectorize y gateway no
> existen). Los informes en `reports/` etiquetados `smoke-…-NOT-A-RESULT` solo
> demuestran que el harness funciona de punta a punta contra
> `pywrangler dev --local` con un chunk sintético; sus cifras no significan nada.

## Comandos

```sh
cd evaluations/rag
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check . && uv run ruff format --check . && uv run pytest
uv run python -m unitropico_eval validate                 # esquema + split sembrado

# Recuperación (el modo se cambia en el SERVIDOR con RETRIEVAL_MODE; --mode solo lo declara)
uv run python -m unitropico_eval retrieval --base-url http://127.0.0.1:8790 \
    --label lexical-k5 --mode lexical -k 5 --split dev
# Respuesta (RAG directo: /answer; vía API: --path /chat)
uv run python -m unitropico_eval answer --base-url http://127.0.0.1:8790 \
    --label hybrid --mode hybrid --split dev
# Comparar corridas del mismo subcomando
uv run python -m unitropico_eval compare reports/*-retrieval-*.json --out reports/compare.md
# Fidelidad: exportar para anotación y agregar la hoja anotada
uv run python -m unitropico_eval export-annotations reports/<run>.json --out anotar.csv
uv run python -m unitropico_eval score-annotations anotar.csv
```

Opciones comunes: `--split dev|test|all` (defecto `dev`), `--category` (repetible),
`--include-blocked` (incluye ítems con evidencia en páginas sin OCR, excluidos
por defecto), `--timeout`, `--out-dir`, `--note` (texto visible en el informe).
Cada corrida escribe `reports/<timestamp>-<subcomando>-<label>.json` (meta,
resumen y resultado por ítem) y el `.md` correspondiente con: base_url (sin
credenciales ni query), ruta, modo **declarado**, k, split, sha256 y tamaño del
dataset, ítems validados, fechas UTC, commit git y si el árbol tenía cambios.
Si algún ítem no está validado, el informe lo marca en cabecera.

Comparar modos: arrancar el RAG con `RETRIEVAL_MODE=lexical`, correr
`retrieval --label lexical --mode lexical`; repetir con `semantic` e `hybrid`; luego
`compare`. `compare` avisa si las corridas difieren en dataset, k o split. El
harness no puede verificar el modo efectivo (el contrato no lo expone; `hybrid`
degrada a léxico si falta Vectorize, ver `apps/rag/README.md`): revisar el log
`retrieval_degraded` del servidor y anotarlo con `--note`.

## Dataset

- Esquema: [`datasets/schema.md`](datasets/schema.md). Candidatas:
  [`datasets/candidates.jsonl`](datasets/candidates.jsonl). Mapa de documentos:
  [`datasets/document_map.json`](datasets/document_map.json).
- 55 candidatas redactadas como preguntas de estudiante, todas
  `source: "draft_from_corpus"`, `validated: false`:

  | Categoría | dev | test | Total |
  |---|---|---|---|
  | in_scope (incl. 2 `blocked_by_missing_ocr`) | 22 | 10 | 32 |
  | out_of_scope (`abstain`) | 7 | 3 | 10 |
  | validity | 3 | 2 | 5 |
  | contradiction | 2 | 1 | 3 |
  | follow_up (con `history`) | 3 | 2 | 5 |
  | **Total** | **37** | **18** | **55** |

  Split estratificado por categoría con `random.Random(20261118)`
  (`dataset.assign_splits`), ~30 % test.

### Cómo se obtuvo el oro

1. Texto por página física de `evaluations/ocr/.artifacts/extraction-corpus-v2/documents.json`
   con la misma regla que el chunker (`workers/ingestion/scripts/chunk_report.py`:
   Markdown OCR si existe, si no `pdftotext`).
2. Cada pregunta se redactó **después** de leer la página y su oro (documento +
   página física + artículo/sección) se comprobó en ese texto. `reference_answer`
   solo aparece cuando la frase se pudo copiar literalmente (incluye los defectos
   del texto extraído).
3. Las discrepancias candidatas y las páginas OCR dudosas se contrastaron con la
   **imagen del PDF original** (renderizado local con PyMuPDF, sin servicios).
   Así se descartó una falsa contradicción: el OCR de la Res. 00137 p18 dice
   "promedio acumulado igual a 3.0", pero el original dice "igual o superior a
   3.0" (igual que el Manual p65). Es un **defecto de OCR** que el corpus v2
   arrastra y que conviene corregir antes de publicar esa página.
4. Se evitaron las 15 páginas de la Convocatoria 2026-1 sin texto (1-12, 15-17);
   solo se usan las páginas 13-14 (cronograma). Dos ítems (`blk-001`, `blk-002`)
   quedan marcados `blocked_by_missing_ocr` con `pages: []` y se excluyen de las
   métricas salvo `--include-blocked`.

### Contradicciones: solo 3 reales

No se inventaron contradicciones. Se encontraron y comprobaron en imagen:

- `con-001`: Res. CBU 002-2025, primer pago "dentro de los primeros 25 días
  **hábiles**" (cronograma, p14) frente a "primeros Veinticinco (25) días
  **calendario**" (art. 15, p15).
- `con-002`: Res. 00137 art. 1.4.1 (p10) exige no estar vinculado a la vez a
  Jóvenes en Paz como condición de permanencia; el Documento Técnico (p45) dice
  que ese joven "podrá permanecer en el programa Renta Joven" sin recibir la transferencia.
- `con-003`: Documento Técnico p45 dice "grupos A, B o C" y, en el mismo
  apartado, "jóvenes de grupos A01 a B18" (la Res. CBU 002-2025 p6 define B1–B7).

No se halló una cuarta ni una quinta discrepancia real; por eso hay 3 y no ~5.

### Antes de reportar resultados

1. **Validación humana obligatoria**, idealmente por Bienestar Universitario:
   redacción natural, respuesta correcta, oro completo (puede haber más páginas
   que respalden), vigencia y si cada `out_of_scope` debe abstenerse o derivar.
   El revisor cambia `source` a `validated_by_staff` y `validated` a `true`.
   Las respuestas esperadas no son FAQs institucionales; no existen FAQs verificadas.
2. **Congelar `test`** tras la validación: no se reasigna, no se usa para
   ajustar prompts, k, chunking ni RRF. Las iteraciones se hacen sobre `dev`.
   Si se añaden ítems después, van a `dev` o a un test nuevo versionado; el
   test `test_candidate_splits_match_seeded_assignment` se adaptará en ese
   momento (`validate` ya avisa de deriva en lugar de fallar).
3. Completar `document_map.json` con los `document_id`/`version_id`/títulos
   reales de D1: las citas de `/answer` no traen `source_sha256`.
4. Indexar el corpus completo con el chunker aprobado y registrar versiones de
   corpus, chunker, embeddings, modelo, prompt y parámetros en `--note`.
5. Ítems de vigencia: la respuesta correcta depende de la fecha de consulta
   (redactados el 2026-11-18); revisar si se evalúan en otra fecha.

## Métricas

**Coincidencia**: un fragmento o una cita acierta si se resuelve al mismo
documento estable (`document_map.json`: `source_sha256` → `document_id` →
`version_id` → título normalizado) y su rango `[page_start, page_end]` interseca
las páginas de oro. Lo no mapeado nunca cuenta como acierto y se informa
("sin mapear").

**Recuperación** (`retrieval`, solo ítems `answer` con oro con páginas):
- Recall@k: 1 si al menos una de las k primeras evidencias acierta (hit rate).
- MRR: 1/rango del primer acierto dentro de k.
- nDCG@k binario: cada entrada de oro gana como máximo una vez (más chunks de
  la misma página no inflan la métrica); ideal = `min(k, |oro|)` aciertos al inicio.
- Cobertura de oro@k: fracción de entradas de oro alcanzadas.
- Fuera de alcance: fracción de `abstained` en `/retrieval/query` (solo
  informativa: la recuperación no decide el alcance).
- Los `follow_up` se consultan solo con la pregunta actual (la ruta no admite
  historial y `/answer` v1 recupera solo con la pregunta actual), así que se
  espera un Recall bajo; mide la necesidad de reformulación.
- 503/errores de transporte se cuentan como **errores**, no como fallos.

**Respuesta** (`answer`):
- Conducta: `answer`→`answered` y `abstain`→`abstained` son aciertos; se
  informan tasa de respuesta en answerables, tasa de abstención en fuera de
  alcance y acierto global (503/errores aparte).
- Exactitud de citas: fracción de citas cuyo documento+páginas intersecan el oro
  (micro sobre todas las citas y macro por ítem).
- Cobertura de citas: fracción de entradas de oro citadas.
- "Sin cita de oro": respuestas `answered` (ítems answerables) donde ninguna
  cita acierta; **proxy** de respuesta sin respaldo, no medida de grounding.
- Distribución de `reason` en las abstenciones.
- Latencia p50/p95 medida en cliente (interpolación lineal; incluye red local).
- Costo: si la respuesta trae `usage`, se suman sus contadores; si no, el
  informe dice **"no medible desde el contrato actual"**. Para medirlo haría
  falta que `/answer` devolviera `usage` (tokens de entrada, salida y
  razonamiento del gateway, más tokens del embedding de la consulta) y el
  modelo efectivo, o leer el registro de uso del proveedor por intervalo de la
  corrida; el precio por token debe tomarse del proveedor en la fecha de la corrida.

### Fidelidad (no automatizada)

`export-annotations` genera un CSV con pregunta, historial, respuesta, citas
(documento, páginas y quote), oro y referencia, más columnas vacías:
`annotator, claims_total, claims_supported, claims_unsupported,
faithfulness_0_2, citations_support_answer, validity_warning_ok, comments`.
Rúbrica: descomponer la respuesta en afirmaciones atómicas; una afirmación está
respaldada si se sigue del texto citado (no del conocimiento del anotador);
`faithfulness_0_2` = 2 todas respaldadas, 1 alguna sin respaldo menor, 0
afirmación central sin respaldo o contradicha. `score-annotations` calcula la
tasa de afirmaciones sin respaldo y la media. Recomendado: dos anotadores en una
muestra y acuerdo (kappa) antes de anotar todo.

**Juez LLM (documentado, no implementado):** misma rúbrica como prompt, con la
respuesta, las quotes y el texto completo de los chunks citados; salida JSON
estricta con la lista de afirmaciones y su veredicto. Usar un modelo distinto del
generador, temperatura baja, y calibrar contra la muestra anotada por humanos
antes de confiar en él. Coste aproximado por corrida =
`N × (T_in × p_in + T_out × p_out)`, con N ítems respondidos, T_in ≈ 2 000–6 000
tokens (5 chunks de ≤1 800 caracteres + respuesta + rúbrica) y T_out ≈ 300–800;
para 55 ítems son unos 0,1–0,35 M tokens de entrada y 0,02–0,05 M de salida. Los
precios `p_in`/`p_out` no se fijan aquí: hay que tomarlos del catálogo del
gateway el día de la corrida y requiere autorización de gasto.

## Estructura

```
src/unitropico_eval/
  dataset.py     esquema, carga, split sembrado, filtros
  docmap.py      resolución documento RAG → id estable
  metrics.py     métricas puras (Recall@k, MRR, nDCG, citas, percentiles)
  client.py      POST JSON con urllib, sin seguir redirecciones, latencia
  runners.py     ejecuta y puntúa cada ítem
  report.py      agregados por grupo, Markdown, comparación
  annotation.py  CSV de fidelidad y su agregado
  cli.py         subcomandos
tests/           métricas a mano, cliente contra http.server local, esquema del dataset, CLI e2e
```

CI: `.github/workflows/evaluations-rag.yml` (sync, Ruff, formato, `validate`,
pytest). No ejecuta evaluaciones contra servicios ni usa secretos.
