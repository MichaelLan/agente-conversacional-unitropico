# Ingesta: Python Worker + Cloudflare Workflow (`unitropico-ingestion`)

Worker Python sin FastAPI ni Pydantic (stdlib + `workers-runtime-sdk==1.9.6`).
Exporta `IngestionWorkflow(WorkflowEntrypoint)`, que procesa una versión de
documento (OCR → chunks en D1/FTS5 → embeddings → Vectorize), y `Default`, que
responde 404 a cualquier petición HTTP. **Nunca publica**: termina en
`review_required` (por defecto, `requires_review=1`) o `ready`. La publicación
la hace siempre el administrador a mano desde la API.

## Contrato con la API

- La API crea la instancia `ver_{version_id}` (saneado a `[A-Za-z0-9_-]`, ≤ 100)
  con `params = {"version_id", "document_id", "source_r2_key", "source_sha256"}`.
  Solo `version_id` es obligatorio. Los demás ids, si llegan, se contrastan con
  D1 (la fuente de verdad) antes de cualquier llamada de pago; si no coinciden,
  la versión pasa a `failed` con `invalid_source`.
- Al reprocesar, la API pone `requires_review=1` y reinicia la instancia, pero no
  cambia `status`. El paso `mark_processing` hace la transición a `processing`
  desde `uploaded`, `failed`, `extracted`, `review_required`, `ready` o
  `unpublished`, y vuelve a poner `requires_review = 1` en la misma sentencia:
  toda extracción nueva exige revisión, aunque un PATCH `requires_review=false`
  llegue antes de que arranque el Workflow. Acepta también `processing` (si
  `mark_failed` no pudo escribir en D1 la versión queda ahí, y la API permite
  reprocesarla). El trigger del esquema permite todas esas transiciones.
- La API reinicia instancias en estado `complete`, `errored` o `terminated`.
  Todos los pasos son idempotentes ante `restart()`: las claves R2 son
  deterministas, D1 se reemplaza por versión y Vectorize hace upsert con ids
  estables.

## Pasos del Workflow (`src/unitropico_ingestion/workflow.py`)

| Paso | Qué hace | Reintentos |
|---|---|---|
| `mark_processing` | `→ processing`, `processing_attempts + 1`, evento `extract/started`. Si ya está en `processing` (reanudación), lo acepta sin contar otro intento. Si está en `published`, falla con `invalid_state`. | 3, exponencial |
| `ocr` | Reutiliza `ocr/{sha}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json` de R2 si existe (no se paga otra vez). Si no existe, lee `source_r2_key` y comprueba `%PDF`, ≤ 50 MB y que el sha256 coincida; después hace un POST a Firecrawl Parse v2, valida la respuesta (`parse_ocr_response`: éxito, páginas, sin truncar) y solo entonces guarda el JSON crudo. Una respuesta truncada (`totalPages > numPages`) o inválida no se cachea. Si `mark_extracted` no cambia nada, falla con `invalid_state`. Pasa a `extracted` con `page_count` y `extraction_r2_key`. | **1** (cada intento puede facturarse) |
| `chunk` | Parte el OCR por página física (`data.pages[]`, o `<!-- page N -->` como respaldo). Antes de trocear aplica las **correcciones de texto activas** de la versión sobre las páginas preparadas (ver abajo) y limpia `corrections_dirty` si no cambiaron durante el paso. Después usa `StructuralChunker`, guarda `chunks/{version_id}/{chunker_version}.jsonl` y reemplaza los chunks en D1 en lotes atómicos; FTS se actualiza por triggers. Con 0 chunks falla con `ocr_empty`; con una corrección inaplicable, con `correction_not_applicable`. | 3 (los dos errores anteriores son permanentes) |
| `embed_{n}` | Lotes de ≤ 96 chunks. Reutiliza `embeddings/{version_id}/{chunker_version}/{model}/{n}.json` si los ids, el modelo y las dimensiones coinciden. Si no, llama al gateway, valida las dimensiones y guarda el resultado. | 3 |
| `index` | Upsert en Vectorize (≤ 1000 por lote) con `id = chunks.id` y metadata `{version_id, document_id}`. Borra con `deleteByIds` los ids que quedaron de ejecuciones anteriores, calculados desde el manifiesto `vectors/{version_id}/manifest.json`. El manifiesto se amplía a «viejos ∪ nuevos» antes del upsert y se reduce a «nuevos» al terminar, así que un fallo a mitad no pierde ids huérfanos. | 3 |
| `finalize` | `extracted → review_required` (si `requires_review=1`) o `ready`. Evento `review`. | 3 |

### Correcciones de texto OCR (paso `chunk`)

El administrador registra correcciones literales por página con la API
(`/admin/versions/{id}/corrections`, tabla `text_corrections`), por ejemplo
Res. 00137 p18: `"promedio igual a 3.0"` → `"promedio igual o superior a 3.0"`.
Al reprocesar, el paso `chunk`:

1. Lee el OCR cacheado (`ocr/{sha}/...json`) y construye `PageText` por página.
   **El objeto de R2 no se modifica**: las correcciones no cambian la caché ni
   provocan un OCR nuevo (un reproceso con OCR en R2 hace 0 llamadas a
   Firecrawl).
2. Carga, en **un solo batch de D1** (una instantánea), `corrections_rev` de la
   versión y las correcciones activas (`deleted_at IS NULL`) en orden
   `created_at, rowid` (`CORRECTIONS_REV` y `ACTIVE_CORRECTIONS`, copias
   literales de `corrections_rev_by_version` y `active_corrections_by_version`
   de `infra/d1/queries.sql`; un test lo comprueba). El desempate por `rowid`
   (orden de inserción) es determinista; el `id` es un uuid4 aleatorio. Las
   borradas lógicamente se ignoran.
3. Prepara las páginas con `chunker.prepare(pages)`: es **la misma
   representación que ve el administrador en los chunks**. En orden:
   normalización (`preprocess.normalize_text`: NFC, sin caracteres invisibles
   ni marcadores de página, espacios horizontales colapsados, líneas
   recortadas, como mucho una línea en blanco seguida), eliminación de
   cabeceras y pies repetidos (`strip_repeated_lines`: solo queda la primera
   aparición) y conversión de las tablas HTML a filas Markdown
   (`preprocess.render_html_tables` con `tables.parse_html_table`: entidades
   decodificadas, etiquetas eliminadas, `| celda | celda |`).
4. `services/corrections.apply_corrections` sobre esas páginas preparadas, por
   cada corrección:
   - normaliza `find_text` como el texto de la página y lo busca
     **literalmente** (mayúsculas, tildes y puntuación exactas). Única
     relajación: cualquier secuencia de espacios en blanco de `find_text`
     (espacios o saltos de línea) equivale a cualquier secuencia de espacios en
     blanco de la página. Así se puede copiar el texto de un chunk, donde las
     líneas partidas se unen con espacio, incluidas filas enteras de tabla;
   - cuenta las apariciones (también solapadas). Si no es **exactamente 1**, la
     versión falla con el código permanente `correction_not_applicable` y
     `error_detail = "step=chunk correction={id} page={n} matches={k}"` (sin
     texto del documento). Una página sin texto visible cuenta 0. No se
     escribe nada (ni JSONL ni chunks). El HTML crudo (`&amp;`, `<td>`) y las
     cabeceras repetidas eliminadas ya no existen en ese texto, así que nunca
     cuentan como coincidencias invisibles;
   - sustituye ese único tramo por `replace_text` (NFC, sin caracteres
     invisibles; puede ser vacío para borrar ruido) y vuelve a normalizar solo
     las líneas tocadas (espacios colapsados y recortados), como habría hecho
     el chunker. Varias correcciones de la misma página se aplican en orden,
     cada una sobre el texto que dejó la anterior.
   Las páginas sin correcciones pasan intactas; `chunker.chunk_prepared` trocea
   el resultado. `chunk(pages)` es exactamente
   `chunk_prepared(prepare(pages))`, así que sin correcciones salen los mismos
   chunks e ids que antes del refactor (tests con ids fijados sobre el fixture
   y sobre casos con tablas; además se comparó el chunker anterior y el nuevo
   sobre las 5 respuestas reales de Firecrawl y el corpus de
   `evaluations/ocr/.artifacts`, con 63 tablas HTML: 0 diferencias).
5. Escribe los chunks. El último batch atómico de `replace_chunks` incluye
   `CLEAR_CORRECTIONS_DIRTY` (copia de `clear_corrections_dirty`): pone
   `corrections_dirty = 0` **solo si `corrections_rev` sigue siendo el leído en
   el punto 2** (compare-and-set). Si el administrador creó o borró una
   corrección mientras el paso corría, la revisión cambió, la marca sigue a 1 y
   la versión no se puede marcar como revisada ni publicar hasta otro
   reproceso (409 `corrections_pending` en la API; el trigger
   `document_versions_publish_guard` y un `CHECK` lo impiden también en D1).
6. Trazabilidad: un evento `correct/succeeded` por corrección aplicada
   (`detail = "correction_applied id={id} page={n}"`), `chunk/succeeded` con
   `chunks=N corrections=M overrides=K corrections_rev=R corrections_pending=0|1`, el campo
   `correction_ids` en cada línea del JSONL (correcciones aplicadas a páginas
   que cubre ese chunk; no afirma que el tramo sustituido caiga dentro de ese
   chunk) y `corrections_applied` (lista de ids), `corrections_rev` y
   `corrections_pending` en el resultado del paso; el resultado final del
   Workflow incluye el conteo y `corrections_pending`.

Efectos aguas abajo: al cambiar el texto cambia `text_sha256` y por tanto el id
del chunk (y, si cambian los límites, el de los siguientes). Los lotes de
embeddings cuyos ids ya no coinciden se recalculan (coste de embeddings, no de
OCR) y el manifiesto de Vectorize borra los ids viejos con `deleteByIds`.
Borrar la corrección y reprocesar restaura los chunks originales.

### Sustituciones de página completa (paso `chunk`)

El administrador puede sustituir el texto completo de una página física
(`page_overrides`, como máximo una activa por página; contrato 4c). En el paso
`chunk`, `correction_set()` lee en **el mismo batch** la revisión, las
correcciones activas y las sustituciones activas (una instantánea). Orden:

1. `chunker.prepare(pages)` sobre el OCR (igual que antes).
2. Cada página con sustitución activa toma el texto de la sustitución pasado
   por la **misma** preparación, llamada con esa única página
   (`chunker.prepare([PageText(n, text)])`): normalización y tablas HTML →
   Markdown. **No se eliminan cabeceras/pies repetidos** del texto sustituido:
   esa regla necesita las demás páginas y el administrador lo escribió a
   propósito (con una sola página `strip_repeated_lines` no actúa). Una página
   sin texto del OCR (p. ej. un diagrama escaneado) gana la página; si el texto
   preparado queda vacío la página queda sin texto. Una sustitución por encima
   del `page_count` del OCR (solo posible si se creó antes de la primera
   extracción) falla con `correction_not_applicable`
   (`override=<id> page=<n> page_count=<m>`).
3. Después se aplican las correcciones literales; las de una página sustituida
   se buscan en el texto de la sustitución (las que apuntaban al texto del OCR
   dejan de coincidir y la versión falla con `correction_not_applicable`).
4. Se guarda `pages/{version_id}/{chunker_version}.json` (lo lee
   `GET /admin/versions/{id}/pages`):
   `{version_id, chunker_version, page_count, corrections_rev, pages: [{page_number,
   text, source: "ocr"|"override", override_id, corrections_applied}]}` con las
   páginas 1..`page_count` (las que no tienen texto, con `text: ""`).
5. Trazabilidad: un evento `correct/succeeded` `override_applied id={id}
   page={n}` por sustitución (antes de los de correcciones), `overrides=K` en el
   evento `chunk`, `override_ids` en cada línea del JSONL, `pages_key` y
   `overrides_applied` en el resultado del paso y su conteo en el del Workflow.
   `clear_corrections_dirty` sigue condicionado a la revisión leída: crear o
   borrar una sustitución mientras corría el paso deja la versión pendiente.

El OCR cacheado en R2 nunca se modifica: reprocesar con una sustitución no
vuelve a pagar OCR.

Límites conocidos: las correcciones son por página física. Un chunk que une el
final de una página con el principio de la siguiente se corrige página a
página. La cabecera repetida de una tabla que continúa en la página siguiente
no se muestra en el chunk (el chunker la descarta al unir), pero sigue en el
texto preparado de esa página.

Cada paso devuelve solo claves y conteos (< 1 MiB). Los pasos no reciben
parámetros: el resultado de un paso anterior se toma de su valor de retorno, que
el motor repite desde su caché al reanudar.

**Errores**: los errores tipados (`IngestionError`) tienen códigos estables:
`invalid_source`, `invalid_state`, `config_missing`, `ocr_failed`, `ocr_empty`,
`chunk_failed`, `correction_not_applicable`, `embedding_failed`, `index_failed`
e `internal_error`. Los
permanentes (4xx del proveedor salvo 408/409/425/429, respuesta inválida,
dimensión incorrecta, fuente inválida) lanzan `NonRetryableError` dentro del
paso. `run()` captura cualquier fallo y ejecuta el paso duradero `mark_failed`:
`failed` + `error_code` + `error_detail` + evento `failed`. Después relanza
`NonRetryableError(code)`. `error_detail` se construye con fragmentos seguros
(paso, status HTTP, nombre del tipo de excepción, conteos), se limpia de los
valores de los secretos y se trunca. Nunca contiene texto del proveedor ni del
documento.

Nombres de paso en `processing_events`: `extract`, `correct`, `chunk`,
`index_fts`, `embed`, `index_vectors` y `review`. La API usa `upload`, `review`,
`correct`, `publish` y `unpublish`.

## Estructura

- `src/worker.py`: entrypoints. Es el único sitio donde se construye `Settings`
  y se importa `workers`.
- `core/settings.py` (`Settings.from_mapping`, `Secret` con repr oculto) y
  `core/logger.py`.
- `domain/ingestion.py`: `IngestionParams`, `VersionRecord`, `OcrPage`,
  `OcrResult`, `ErrorCode`, `IngestionError` y `sanitize_detail`.
- `domain/chunking.py`: `PageText`, `Chunk` y el esquema de ids (C1).
- `domain/corrections.py`: `TextCorrection`, `PageOverride`, `CorrectionSet`
  (correcciones + sustituciones + `corrections_rev`), `AppliedCorrection` y
  `AppliedOverride`.
- `ports.py`: `BlobStore`, `OcrClient`, `Embedder`, `VersionRepository` y
  `VectorIndex`.
- `services/ingestion.py`: casos de uso por etapa (`mark_processing`, `run_ocr`,
  `chunk_version`, `embed_chunks`, `index_chunks`, `finalize`).
  `services/ocr_parser.py` interpreta la respuesta de Firecrawl.
  `services/corrections.py` aplica las correcciones de texto y
  `services/overrides.py` las sustituciones de página.
  `services/chunker.py` y los módulos relacionados contienen el chunker
  estructural (C1).
- `infra/`: `r2_store.py`, `firecrawl.py` (multipart con `js.fetch`; el runtime
  se inyecta), `gateway_embeddings.py` (JSON con `workers.fetch`),
  `d1_repository.py` y `vectorize_index.py`. Solo `worker.py` e `infra/`
  importan `js`, `workers` o `pyodide` (lo comprueba `tests/test_structure.py`).

### Firecrawl Parse v2

`POST https://api.firecrawl.dev/v2/parse`, multipart con `file` (PDF) y
`options` (JSON):
`{"formats":["markdown"],"timeout":300000,"parsers":[{"type":"pdf","mode":"ocr","maxPages":OCR_MAX_PAGES,"pages":true,"blocks":true,"pageMarkers":true}]}`.
El límite es de 50 MB por petición, así que no hace falta trocear el PDF. Un
resultado con `totalPages > numPages` está truncado y se rechaza. Se usa
`js.fetch` con `FormData.js_object`, porque `workers.fetch` de la versión 1.9.6
no envía `FormData` correctamente (ver
`docs/research/python-workflows-bindings.md`).

### D1

**Tope de chunks por versión.** `replace_chunks` envía 1 `DELETE` + N `INSERT` +
1 `UPDATE` en lotes `batch()` de 100 sentencias, es decir, ~N/100 invocaciones
de D1 dentro del paso `chunk`. No hay en el repositorio una cifra verificada del
límite de consultas de D1 por invocación (solo está documentado el límite de 50
subrequests por instancia en Free y 10 000 en Paid, en
`docs/research/python-workflows-bindings.md`; no se ha comprobado cuántas
cuentan las sentencias de un `batch`). Por tanto **no se impone un tope de
chunks** en el código: con Workers Paid un documento de miles de chunks cabe
holgadamente en 10 000, pero debe verificarse en D1 real antes de ingerir
documentos grandes.

`LOG_EVENT`, `ACTIVE_CORRECTIONS`, `CORRECTIONS_REV` y
`CLEAR_CORRECTIONS_DIRTY` son copias de los bloques `log_event`,
`active_corrections_by_version`, `corrections_rev_by_version` y
`clear_corrections_dirty` de `infra/d1/queries.sql`; un test comprueba que
siguen siendo iguales. Las demás sentencias son propias de la
ingesta: transiciones con `WHERE status IN (...)` idempotentes, borrado e
inserción de chunks, `chunker_version` y `embedding_model`. Ninguna escribe
`published`. **Observado en D1 local**: `meta.changes` cuenta también las filas
que modifican los triggers (un UPDATE de una fila devuelve 2 por
`document_versions_touch`), así que el adaptador comprueba `> 0`. La D1 falsa
de los tests reproduce ese comportamiento.

## Configuración (`wrangler.jsonc`)

- `workflows`: `ingestion` / binding `INGESTION` / `IngestionWorkflow`. El
  Worker no usa el binding, pero el bloque declara el Workflow. La API declara
  el mismo bloque con `script_name: "unitropico-ingestion"`.
- `d1_databases`: `DB` → `unitropico`. **`database_id` es un placeholder**;
  `migrations_dir` es `../../infra/d1/migrations`.
- `r2_buckets`: `DOCUMENTS` → `unitropico-documents`.
- `vectorize`: `VECTORIZE` → `unitropico-chunks-1536`. No tiene emulación local.
- vars: `EMBEDDING_MODEL` (`openai/text-embedding-3-small`),
  `EMBEDDING_DIMENSIONS` (`1536`), `OCR_VERSION`, `OCR_MAX_PAGES` e
  `INGESTION_LOG_LEVEL`. Opcionales: `FIRECRAWL_URL` y `EMBEDDING_URL`.
- Secrets (**no ejecutados**): `npx wrangler secret put FIRECRAWL_API_KEY` y
  `npx wrangler secret put AI_GATEWAY_API_KEY`. Si falta una clave, el paso que
  la necesita falla con `config_missing`. Si el OCR ya está en R2, no hace falta
  `FIRECRAWL_API_KEY`.

Cambiar `OCR_VERSION` u `OCR_MAX_PAGES` fuerza un OCR nuevo (de pago), porque ambos forman parte de la clave R2. Cambiar el chunker cambia
los ids y vuelve a calcular solo los embeddings cuyo lote ya no coincide.

## Comandos

```bash
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
uv run python scripts/chunk_report.py   # informe offline del chunker (stdout)
```

`uv.lock` (host CPython) se genera con `uvx --from uv==0.13.7 uv lock`.
`pylock.toml` (Pyodide/WASM, solo `workers-runtime-sdk 1.9.6`) lo generó
pywrangler; no se edita a mano. `python_modules/`, `.venv-workers/` y
`.wrangler/` son salida local ignorada.

Los tests (CPython, sin `js` ni `workers`) usan fakes de todos los puertos:

- una D1 sobre `sqlite3` en memoria que aplica la migración real
  `infra/d1/migrations/0001_initial.sql`;
- un motor de `step` falso con caché por nombre, reintentos según `config`,
  `NonRetryableError` y caídas simuladas para reanudar;
- un `js` falso para el multipart de Firecrawl;
- un fixture sintético de Firecrawl (`tests/fixtures/`), con texto inventado.

CI: `.github/workflows/ingestion.yml`, que también se dispara con cambios en
`infra/d1/**`.

### Verificación local en workerd (sin login ni red de pago)

```bash
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.9 uv run pywrangler dev --local --ip 127.0.0.1 --port 8789
npx wrangler d1 migrations apply DB --local
npx wrangler r2 object put unitropico-documents/raw/<sha>.pdf --file=<pdf> --local
npx wrangler d1 execute DB --local --command "INSERT INTO documents ...; INSERT INTO document_versions ..."
curl -X POST http://127.0.0.1:8789/cdn-cgi/local/explorer/api/workflows/ingestion/instances \
  -H 'Content-Type: application/json' -d '{"id":"ver_x","params":{"version_id":"x"}}'
```

Resultado (2026-11-18, wrangler 4.165.0, sin secrets definidos, así que
Firecrawl no se llamó):

- `GET /` devuelve 404.
- El Workflow se registra.
- Una instancia con una versión real pasa por `mark_processing` (D1
  `processing`, intentos +1) y llega a `ocr`, que lee R2 y comprueba el sha. Ahí
  falla con `config_missing` sin reintentar. `mark_failed` deja `failed` +
  `error_detail` + el evento `extract/failed`. La instancia queda en `errored`
  con `NonRetryableError: config_missing`.
- Una versión inexistente termina en `invalid_source`.

## No verificado

- Llamada real a Firecrawl con `js.fetch` + `FormData`. El campo `options` se
  envía como campo de texto; los pilotos lo enviaban como parte con
  `Content-Type: application/json`.
- Llamada real al gateway de embeddings.
- Vectorize (forma real de `upsert`/`deleteByIds`; no tiene emulación local).
- Pasos `chunk`, `embed_{n}`, `index` y `finalize` dentro de workerd: solo están
  probados en CPython con fakes.
- `put` en R2 de `bytes` de Python grandes en producción.
- Límite de 1 MiB por paso, CPU y memoria en producción. `restart()` frente a
  pasos cacheados en remoto.
- `extraction_r2_key` es `UNIQUE` y la clave de OCR va por sha. Dos versiones
  con los mismos bytes en documentos distintos chocarían, igual que
  `source_r2_key = raw/{sha}.pdf`, que también es `UNIQUE`.
