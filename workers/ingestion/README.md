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
| `chunk` | Parte el OCR por página física (`data.pages[]`, o `<!-- page N -->` como respaldo) con `StructuralChunker`. Guarda `chunks/{version_id}/{chunker_version}.jsonl` y reemplaza los chunks en D1 en lotes atómicos; FTS se actualiza por triggers. Con 0 chunks falla con `ocr_empty`. | 3 |
| `embed_{n}` | Lotes de ≤ 96 chunks. Reutiliza `embeddings/{version_id}/{chunker_version}/{model}/{n}.json` si los ids, el modelo y las dimensiones coinciden. Si no, llama al gateway, valida las dimensiones y guarda el resultado. | 3 |
| `index` | Upsert en Vectorize (≤ 1000 por lote) con `id = chunks.id` y metadata `{version_id, document_id}`. Borra con `deleteByIds` los ids que quedaron de ejecuciones anteriores, calculados desde el manifiesto `vectors/{version_id}/manifest.json`. El manifiesto se amplía a «viejos ∪ nuevos» antes del upsert y se reduce a «nuevos» al terminar, así que un fallo a mitad no pierde ids huérfanos. | 3 |
| `finalize` | `extracted → review_required` (si `requires_review=1`) o `ready`. Evento `review`. | 3 |

Cada paso devuelve solo claves y conteos (< 1 MiB). Los pasos no reciben
parámetros: el resultado de un paso anterior se toma de su valor de retorno, que
el motor repite desde su caché al reanudar.

**Errores**: los errores tipados (`IngestionError`) tienen códigos estables:
`invalid_source`, `invalid_state`, `config_missing`, `ocr_failed`, `ocr_empty`,
`chunk_failed`, `embedding_failed`, `index_failed` e `internal_error`. Los
permanentes (4xx del proveedor salvo 408/409/425/429, respuesta inválida,
dimensión incorrecta, fuente inválida) lanzan `NonRetryableError` dentro del
paso. `run()` captura cualquier fallo y ejecuta el paso duradero `mark_failed`:
`failed` + `error_code` + `error_detail` + evento `failed`. Después relanza
`NonRetryableError(code)`. `error_detail` se construye con fragmentos seguros
(paso, status HTTP, nombre del tipo de excepción, conteos), se limpia de los
valores de los secretos y se trunca. Nunca contiene texto del proveedor ni del
documento.

Nombres de paso en `processing_events`: `extract`, `chunk`, `index_fts`,
`embed`, `index_vectors` y `review`. La API usa `upload`, `review`, `publish` y
`unpublish`.

## Estructura

- `src/worker.py`: entrypoints. Es el único sitio donde se construye `Settings`
  y se importa `workers`.
- `core/settings.py` (`Settings.from_mapping`, `Secret` con repr oculto) y
  `core/logger.py`.
- `domain/ingestion.py`: `IngestionParams`, `VersionRecord`, `OcrPage`,
  `OcrResult`, `ErrorCode`, `IngestionError` y `sanitize_detail`.
- `domain/chunking.py`: `PageText`, `Chunk` y el esquema de ids (C1).
- `ports.py`: `BlobStore`, `OcrClient`, `Embedder`, `VersionRepository` y
  `VectorIndex`.
- `services/ingestion.py`: casos de uso por etapa (`mark_processing`, `run_ocr`,
  `chunk_version`, `embed_chunks`, `index_chunks`, `finalize`).
  `services/ocr_parser.py` interpreta la respuesta de Firecrawl.
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

`LOG_EVENT` es una copia del bloque `log_event` de `infra/d1/queries.sql`; un
test comprueba que siguen siendo iguales. Las demás sentencias son propias de la
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
