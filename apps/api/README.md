# API Unitrópico

Primer entregable autónomo sobre **CPython 3.14**, FastAPI y Pydantic.
Aplicación no empaquetada, gestionada con uv y lockfile propio; no requiere
workspace ni configuración en la raíz del monorepo.

## Preparación y calidad

Desde `apps/api`, con uv disponible:

```sh
uv sync --locked --group dev
uv run python --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Para aplicar formato: `uv run ruff format .`. Tras un cambio autorizado de
versiones, regenerar el lock con `uv lock` y sincronizar con
`uv sync --group dev`.

Ruff verifica E, F, I, UP, B y D con convención Google. Solo se desactivan
D100/D104: los docstrings de módulo/paquete no se exigen si no aportan contexto,
según la arquitectura. Toda función y clase propia lleva docstring.

## Contrato y composición

- `unitropico_api.main.create_app(*, settings=None, rag_client=None,
  chat_client=None, documents=None, blobs=None, ingestion=None,
  chat_limiter=None, retrieval_limiter=None)` es la factory
  ASGI (sin limitadores no hay rate limiting, salvo `RATE_LIMIT_REQUIRED`); sin puertos admin, `/admin/*` responde 503 `storage_unavailable`. Sin `settings` usa
  `Settings.from_mapping(os.environ)`; el Worker pasa settings propios. Solo
  `main.py` y `worker.py` conocen `Settings`; servicios e infra reciben por
  constructor lo que necesitan. Se usa `src/` vía pythonpath de pytest.
- `GET /health`: 200 `{"status":"ok"}`, solo salud HTTP local (no es readiness
  de RAG). Rutas inexistentes: 404 JSON de FastAPI.
- `/docs`, `/redoc` y `/openapi.json` están **desactivados** salvo
  `EXPOSE_DOCS=true`.
- `POST /retrieval/query` (interno/diagnóstico): `{question 1..500, limit 1..20}`
  -> `{status: unavailable|abstained|evidence_found, evidence: [...]}`. Cada
  fragmento: `document_id`, `document_title`, `version_id`, `version_label|null`,
  `chunk_id`, `source_sha256`, `page_start`, `page_end` (>= page_start),
  `section|null`, `text`. Sin binding/fallo: 503; payload inválido: 502
  (ambos `{"status":"unavailable","evidence":[]}`). 429 `rate_limited` y
  413/411 como `/chat` (ver Rate limiting).
- `POST /chat` (público), contrato v1 de `docs/IMPLEMENTATION_STATUS.md` sec. 4.
  Petición: `question` (1..500 code points tras strip), `history` opcional
  (máx. 6; `role` user|assistant, `content` 1..2000), campos extra prohibidos.
  Se reenvía a `POST https://rag.internal/answer` y la respuesta se valida
  estrictamente (invariantes `answered`/`abstained`, `page_end >= page_start`,
  `reason` en el conjunto controlado):
  - 200: payload validado.
  - 503 `{"status":"unavailable","referral":...}`: sin binding, timeout, error
    de transporte, no-2xx o 503 del RAG. Usa el `referral` del RAG si es válido;
    si no, un texto genérico en español sin datos de contacto.
  - 502: payload del RAG inválido (mismo cuerpo que 503).
  - 422: validación de la petición.
  - 429 `{"error":{"code":"rate_limited","message":"Has realizado muchas
    consultas seguidas. Espera un momento e inténtalo de nuevo."}}` con
    `Retry-After: 60`. Ampliación del contrato v1 (que solo define
    200/422/502/503): **el frontend debe tratarlo** mostrando el mensaje y sin
    reintentar automáticamente antes de `Retry-After`.
  - 413 `payload_too_large` (Content-Length > 64 KiB) / 411 `length_required`
    (cuerpo chunked sin Content-Length), mismo sobre `{"error":...}`, decididos
    en el Worker antes de leer el cuerpo.
  El historial no se persiste.

## Administración (`/admin`, contrato v1 de `docs/IMPLEMENTATION_STATUS.md` sec. 4b)

**Auth**: todas las rutas exigen `Authorization: Bearer <ADMIN_TOKEN>`, comparado
con `hmac.compare_digest` (bytes UTF-8). Sin token válido: 401 con
`WWW-Authenticate: Bearer`. Si `ADMIN_TOKEN` no está configurado o está vacío,
**todas** las rutas admin responden 503 `admin_disabled` (nunca quedan abiertas).
La autenticación se resuelve antes de leer o validar el cuerpo.

**Rechazo temprano de `/admin/*`** (`src/worker.py` +
`api/rate_limit.early_admin_rejection` y `api/admin.early_upload_rejection`): el
puente ASGI del SDK de Workers lee todo el cuerpo antes de invocar FastAPI, así
que el Worker decide con las cabeceras, **antes de leer el cuerpo**, para toda
ruta `/admin/*` (ruta normalizada: `//` y `/` final):
- Sin `ADMIN_TOKEN`: 503 `admin_disabled`.
- Bearer ausente o inválido: 401 `unauthorized` (con `WWW-Authenticate: Bearer`;
  misma `check_admin_token`/`hmac.compare_digest` que FastAPI).
- Con token válido, `POST`/`PUT`/`PATCH`/`DELETE`: 413 `payload_too_large` si
  `Content-Length` > 64 KiB; 411 `length_required` si falta en un cuerpo
  `chunked`. Única excepción explícita: `POST /admin/versions/{id}/page-overrides`
  admite hasta **256 KiB** (`MAX_PAGE_OVERRIDE_BODY_BYTES`, `admin_body_limit`):
  20 000 caracteres son hasta ~80 KB en UTF-8 crudo (caracteres de 4 bytes) y
  ~240 KB si el cliente escapa todo como `\uXXXX`; el resto de rutas (incluido
  `DELETE …/page-overrides/{oid}`) sigue en 64 KiB. `POST /admin/uploads`
  conserva su propio límite (`ADMIN_MAX_UPLOAD_BYTES`; 411 si falta
  `Content-Length`, 413 si lo supera).
- Cualquier otro método o ruta sigue a FastAPI, que repite auth y límites como
  defensa en profundidad.

Los intentos fallidos (401) consumen la cuota de `ADMIN_AUTH_LIMITER` (ver
«Rate limiting»). No verificado en workerd real (solo con dobles del SDK). El
token nunca se registra en logs (`Settings.admin_token` es `SecretStr`). Sin
cookies ni sesión.

Errores: `{"error": {"code": "...", "message": "texto en español"}}`; también los
422 de validación bajo `/admin` (las rutas públicas conservan el 422 de FastAPI).

| Ruta | Éxito | Errores |
| --- | --- | --- |
| `POST /admin/uploads?title&document_type&version_label[&document_id]` | 201 nuevo / 200 mismo sha | 404 `document_not_found`, 409 `source_registered_elsewhere`/`version_label_taken`, 411 `length_required`, 413 `payload_too_large`, 415 `unsupported_media_type`, 422 `invalid_pdf`/`invalid_request` |
| `GET /admin/documents` | `{documents: [{id,title,document_type,created_at,versions:[...]}]}` | |
| `GET /admin/versions/{id}` | versión + documento + `error_detail` + `processing_events` + `workflow` (o null) | 404 |
| `PATCH /admin/versions/{id}` | `{document, version}` | 404, 409 `version_published`/`document_has_published_version`/`version_label_taken`/`review_not_available`/`corrections_pending`/`supersedes_cycle`, 422 `invalid_request`/`invalid_effective_range`/`invalid_supersedes`/`supersedes_other_document` |
| `POST /admin/versions/{id}/publish` | `{document, version}` | 409 `invalid_status_transition`/`corrections_pending`/`review_required`/`publish_without_chunks`/`chunk_page_out_of_range`/`publish_requirements_unmet` |
| `POST /admin/versions/{id}/unpublish` | `{document, version}` | 409 si no está `published` |
| `POST /admin/versions/{id}/reprocess` | `{document, version, ingestion}` | 409 `invalid_status_transition`/`ingestion_in_progress`, 503 `workflow_launch_failed` |
| `GET /admin/versions/{id}/chunks?offset&limit` | `{version_id, status, total, offset, limit, chunks: [...]}` | 404 `version_not_found`, 422 `invalid_request` |
| `GET /admin/versions/{id}/corrections` | `{version_id, corrections: [...], match_rule}` (activas y borradas) | 404 `version_not_found` |
| `POST /admin/versions/{id}/corrections` | 201 `{correction, version_status, requires_review, corrections_dirty, reprocess_required, message}` | 404, 409 `version_published`/`duplicate_correction`/`too_many_corrections`, 422 `invalid_request`/`page_out_of_range` |
| `DELETE /admin/versions/{id}/corrections/{cid}` | 200, mismo sobre que el POST | 404 `version_not_found`/`correction_not_found`, 409 `version_published` |
| `GET /admin/versions/{id}/pages?page=N` | `{version_id, page_number, page_count, text, source: "ocr"\|"override", override_id, corrections_applied, chunker_version}` | 404 `version_not_found`/`page_text_unavailable`, 422 `invalid_request`/`page_out_of_range` |
| `GET /admin/versions/{id}/page-overrides` | `{version_id, overrides: [...]}` (activas y borradas) | 404 `version_not_found` |
| `POST /admin/versions/{id}/page-overrides` | 201 `{override, version_status, requires_review, corrections_dirty, reprocess_required, message}` | 404, 409 `version_published`/`override_exists`, 422 `invalid_request`/`page_out_of_range` |
| `DELETE /admin/versions/{id}/page-overrides/{oid}` | 200, mismo sobre que el POST (`active: false`) | 404 `version_not_found`/`override_not_found`, 409 `version_published` |

Detalles:
- **Upload**: cuerpo binario `application/pdf` (se aceptan parámetros de media
  type), ≤ `ADMIN_MAX_UPLOAD_BYTES` (25 MB por defecto, máximo 50 MB; se rechaza antes por `Content-Length`),
  magic `%PDF-`. `version_label` 1..80 y, sin `document_id`, `title` 1..200 (tras strip) y
  `document_type` obligatorios; con `document_id` existente `title` y
  `document_type` son opcionales y se **ignoran** (no modifican el documento),
  `document_type` ∈ `resolucion, reglamento, manual, convocatoria, politica,
  documento_tecnico, boletin, otro`, `document_id` opcional `[A-Za-z0-9_-]{1,64}`;
  parámetros extra prohibidos. Orden: validar → si `raw/{sha256}.pdf` ya está
  registrado, 200 con esa versión solo si pertenece al mismo documento (con
  `document_id`: mismo id; sin él: mismos `title` y `document_type`, reintento
  idempotente), si no 409 `source_registered_elsewhere` (sin escribir ni lanzar nada) → R2 put
  (idempotente: si existe un objeto del mismo tamaño no se re-sube; se envía el
  `sha256` para que R2 verifique la integridad) → D1 (documento + versión
  `uploaded` en un `batch`, `ON CONFLICT (document_id, source_sha256) DO NOTHING`
  y relectura) → Workflow. Respuesta: `{created, document, version, ingestion}`;
  `ingestion.outcome` ∈ `created|existing|restarted|failed`. Si el lanzamiento
  falla, la versión queda `uploaded`, se registra el evento
  `upload/failed/workflow_launch_failed` y `reprocess` la recupera.
- **Unicidad**: `source_r2_key` es UNIQUE global en el esquema y la clave es
  `raw/{sha256}.pdf`, así que los mismos bytes no pueden registrarse en dos
  documentos (409 `source_registered_elsewhere`), aunque el esquema solo exija
  unicidad de sha por documento.
- **PATCH**: JSON estricto (sin campos extra, sin coerción de tipos); al menos un
  campo; `null` borra fechas/`supersedes_version_id`; `title`, `document_type`,
  `version_label` y `requires_review` no admiten null. Fechas `YYYY-MM-DD` reales;
  `effective_until >= effective_from` se valida también contra los valores ya
  guardados. `supersedes_version_id` debe existir y no ser la propia versión.
  `requires_review=false` solo en `review_required|ready|unpublished` y mueve
  `review_required → ready`; `true` mueve `ready|unpublished → review_required`.
  Con `corrections_dirty = true`, `requires_review=false` → 409
  `corrections_pending` (también lo impone la condición del UPDATE, por si la
  marca cambia entre la comprobación y la escritura).
  Versión `published` → 409 (despublicar primero). Documento y versión se
  actualizan en un único `batch`.
- **Publish**: desde `ready` (y `unpublished`, que el esquema permite, para volver
  a publicar tras editar) con `requires_review=false` y `corrections_dirty =
  false` (si no, 409 `corrections_pending`, comprobado en el servicio, en el
  UPDATE y en el trigger `document_versions_publish_guard`); fija
  `published_at`. Los triggers/CHECK del esquema (chunks, rango de páginas,
  `page_count`, etc.) se traducen a 409.
- **Reprocess**: desde `uploaded|failed|review_required|ready|unpublished`. Pone
  `requires_review=1` (la nueva extracción debe revisarse), registra
  `reprocess_requested` y crea la instancia o la reinicia si ya terminó. No cambia
  `status`: las transiciones de procesamiento son del Workflow.
- **Workflow**: instancia `ver_{version_id}` (saneada a `[A-Za-z0-9_-]`, ≤100;
  si hubo que reescribirla se añade un sufijo de hash). Params:
  `{version_id, document_id, source_r2_key, source_sha256}`. `already_exists` →
  `get` + `status`: `errored|terminated` → `restart`; activo
  (`queued|running|waiting|paused|waitingForPause`) → reutiliza en upload y 409 en
  reprocess; `complete` → reutiliza en upload y `restart` en reprocess.
- **Reprocess**: estados `uploaded|processing|extracted|failed|review_required|ready|
  unpublished` (así una versión atascada en `processing`/`extracted` se recupera).
  Orden: (1) consultar la instancia: activa → 409 `ingestion_in_progress` sin tocar
  la base; (2) `mark_for_reprocess` (`requires_review=1` + evento
  `reprocess_requested`); (3) `relaunch`. Si (3) pierde una carrera (409) o falla,
  queda un evento `upload/failed` (`ingestion_in_progress` / `workflow_launch_failed`).
- **PATCH**: `title`/`document_type` cambian el documento; se rechazan con 409
  `document_has_published_version` si otra versión del documento está publicada
  (no se aplica ningún cambio). `supersedes_version_id` debe existir, ser del mismo
  documento (422 `supersedes_other_document`), distinto de sí misma y sin ciclos
  (409 `supersedes_cycle`).
- **processing_events**:
  `upload` para alta (`source_stored`), lanzamientos (`workflow_*` o
  `failed/workflow_launch_failed`) y `reprocess_requested`; `review` para
  `metadata_updated:<campos>`; `correct` para `correction_created id=<cid>
  page=<n>` y `correction_deleted id=<cid>` (la ingesta añade
  `correction_applied id=<cid> page=<n>`); `publish`/`unpublish`. Los eventos de
  transiciones se insertan en el mismo `batch` que el UPDATE, condicionados a que
  este se aplicara.

### Revisión y corrección del texto extraído

Flujo: el Workflow deja la versión en `review_required` → el administrador lee
los chunks → registra correcciones literales → **reprocesa** (reutiliza el OCR
de R2, sin pagarlo otra vez; el paso `chunk` aplica las correcciones, ver
`workers/ingestion/README.md`) → vuelve a leer los chunks → marca revisada →
publica. Crear o borrar una corrección **nunca** lanza el reproceso.

**Campo `corrections_dirty` (bool) en `VersionSummary`** (`GET /admin/documents`
→ `documents[].versions[]`, `GET /admin/versions/{id}` → `version`, y toda
respuesta `{document, version}`), y también en la respuesta de POST/DELETE de
correcciones. `true` = hay correcciones creadas o borradas que el paso `chunk`
de la ingesta aún no ha aplicado: los chunks que se ven no las reflejan.
Mientras sea `true`, `PATCH requires_review=false` y `publish` responden **409
`corrections_pending`**; hay que reprocesar. Lo mantiene D1: cada alta o borrado
lógico pone la columna a 1 e incrementa `corrections_rev` (triggers, misma
transacción), y solo la ingesta la limpia cuando aplica exactamente el conjunto
que leyó (compare-and-set sobre `corrections_rev`). Una corrección creada
mientras el Workflow ya había leído las correcciones deja la marca a 1 aunque
la versión acabe en `review_required`. El panel puede usarla para mostrar
«correcciones pendientes de reprocesar» y deshabilitar «marcar revisada» y
«publicar».

- **`GET /admin/versions/{id}/chunks`**: query `offset` (entero ≥ 0, por
  defecto 0) y `limit` (1..100, por defecto 50); otros parámetros → 422.
  Cualquier estado (también `published`); sin chunks devuelve `total: 0` y
  `chunks: []`. Cada chunk:
  `{ordinal, id, section|null, page_start, page_end, text, chunker_version|null}`
  ordenados por `ordinal` (`chunker_version` es el de la versión).
- **`GET /admin/versions/{id}/corrections`**: todas, por `created_at` y, a
  igualdad, orden de inserción (`rowid`; el id es aleatorio y no desempata).
  Cada una: `{id, version_id, page_number, find_text, replace_text, reason,
  created_at, deleted_at|null, active}` (`active = deleted_at is null`).
  `match_rule` es el texto de ayuda con la regla de coincidencia
  (`domain/admin.MATCH_RULE_HELP`, también en la descripción OpenAPI de
  `find_text`).
- **`POST /admin/versions/{id}/corrections`**: JSON estricto
  `{page_number, find_text, replace_text, reason}`, sin campos extra ni
  coerción (`page_number` entero ≥ 1, no `"2"`, `2.0` ni `true`). `find_text`
  1..500 caracteres tras recortar espacios; `replace_text` 0..500, se guarda tal
  cual (vacío = borrar ruido) y debe ser distinto de `find_text`; `reason`
  1..300 tras recortar. Sin caracteres de control (Unicode `Cc`) salvo
  tabulador y saltos de línea, ni de formato (`Cf`): controles bidi
  (U+202A–U+202E, U+2066–U+2069), espacios de ancho cero, guion suave, BOM y
  también ZWJ/ZWNJ (U+200D/U+200C). Decisión: son invisibles en el panel (una
  corrección podría parecer distinta de lo que hace), el español no los
  necesita y la normalización de la ingesta los elimina del texto de la
  página, así que un `find_text` con ellos nunca correspondería a lo que vio
  el administrador. `page_number > page_count` (si ya se conoce) → 422 `page_out_of_range`
  (el esquema lo vuelve a comprobar). Versión `published` → 409
  `version_published` (despublicar primero; también lo impone un trigger). Una
  corrección activa con la misma página y el mismo `find_text` → 409
  `duplicate_correction`. Máximo 100 activas por versión → 409
  `too_many_corrections`. Ambas reglas se comprueban primero en el servicio
  (mensaje claro) y otra vez de forma atómica en D1: índice único parcial
  `(version_id, page_number, find_text) WHERE deleted_at IS NULL` y un
  `INSERT … SELECT … WHERE (count activas) < 100 RETURNING id` en una sola
  sentencia, así que dos peticiones concurrentes no los superan (la violación
  se traduce al mismo 409). Id `cor_<uuid hex>`. Inserta la fila, un evento
  `correct` (solo si se insertó) y, en el mismo `batch`, pone
  `corrections_dirty = 1` (trigger), `requires_review = 1` y pasa
  `ready`/`unpublished` → `review_required`: con una corrección pendiente la
  versión no puede publicarse sin una nueva revisión (decisión añadida al
  diseño para no publicar por descuido un texto sin corregir). Respuesta 201:
  `{"correction": {...}, "version_status": "review_required",
  "requires_review": true, "corrections_dirty": true, "reprocess_required": true,
  "message": "El cambio se aplicará cuando se reprocese la versión ..."}`.
- **`DELETE /admin/versions/{id}/corrections/{cid}`**: borrado lógico
  (`deleted_at`), idempotente: repetirlo devuelve 200 con la misma corrección sin
  nuevo evento ni cambio de estado. La primera vez registra `correct`
  (`correction_deleted`) y aplica la misma regla de revisión que el POST. `cid`
  de otra versión o inexistente → 404 `correction_not_found`; versión
  `published` → 409 `version_published`. Las filas nunca se borran físicamente.
- **Regla de coincidencia** (`MATCH_RULE_HELP`): `find_text` se busca en el
  texto preparado de su página física, el mismo que muestran los chunks:
  normalizado, sin cabeceras/pies repetidos (solo queda su primera aparición) y
  con las tablas HTML convertidas a filas Markdown (`| celda | celda |`,
  entidades decodificadas, sin etiquetas). Se copia desde un chunk de esa
  página; literal salvo espacios (cualquier secuencia de espacios o saltos
  equivale a otra); exactamente una aparición, contando las solapadas.
- La aplicación la hace la ingesta: si una corrección no aplica, el reproceso deja la versión en
  `failed` con `error_code = correction_not_applicable` y `error_detail =
  "step=chunk correction=<cid> page=<n> matches=<k>"` (visible en
  `GET /admin/versions/{id}`); se borra o se rehace la corrección y se reprocesa.

### Sustitución de página completa (contrato 4c)

Para páginas que las correcciones literales no arreglan (diagramas, tablas
como imagen, texto inventado por el OCR). Mismo Bearer, rechazo temprano y
sobre de error que el resto de `/admin/*`.

- **`GET /admin/versions/{id}/pages?page=N`**: texto preparado final de la
  página tal como lo dejó el último paso `chunk` (sustitución + correcciones),
  leído de R2 en `pages/{version_id}/{chunker_version}.json` (puerto
  `BlobStore.get_json`; `chunker_version` sale de D1). `page` entero ≥ 1
  obligatorio (si no, 422 `invalid_request`); por encima del `page_count` → 422
  `page_out_of_range`. Sin `chunker_version`, sin JSON (versión procesada antes
  de este pipeline) o con un JSON que no corresponde a la versión/chunker o está
  mal formado → 404 `page_text_unavailable` (hay que reprocesar). Una página sin
  texto visible devuelve `text: ""`.
- **`GET /admin/versions/{id}/page-overrides`**: todas, por `created_at, rowid`;
  `O = {id, version_id, page_number, text, reason, created_at, deleted_at, active}`.
- **`POST /admin/versions/{id}/page-overrides`**: JSON estricto
  `{page_number ≥ 1, text, reason}` (extras prohibidos, sin coerción). `text` y
  `reason` se recortan en los extremos y deben quedar en 1..20000 y 1..300
  caracteres (code points). Caracteres `Cc` salvo `\n` y `\t` (también `\r`) y
  cualquier `Cf` → 422 `invalid_request`. Página > `page_count` conocido → 422
  `page_out_of_range` (con `page_count` desconocido se acepta; la ingesta falla
  con `correction_not_applicable` si la página no existe). Versión publicada →
  409 `version_published`. Ya hay una activa para esa página → 409
  `override_exists` (bórrela primero): se comprueba en el servicio y, de forma
  atómica, con el índice único parcial `page_overrides_active_unique`; la
  inserción, la invalidación de la revisión (`requires_review = 1`,
  `ready`/`unpublished` → `review_required`) y el evento `correct`
  `override_created id=… page=…` van en un único batch. Los triggers ponen
  `corrections_dirty = 1` y suben `corrections_rev`, así que revisar/publicar
  da 409 `corrections_pending` hasta reprocesar. Id `pov_<32 hex>`.
- **`DELETE /admin/versions/{id}/page-overrides/{oid}`**: borrado lógico
  idempotente (evento `override_deleted id=…` solo la primera vez, misma regla
  de revisión); 404 `override_not_found` si no existe o es de otra versión, 409
  `version_published`.
- Reglas de aplicación (ingesta): ver `workers/ingestion/README.md`.

**Capas**: `domain/admin.py` (DTOs Pydantic, errores, ids), `services/admin.py`
(`AdminService` y los puertos `DocumentRepository`, `BlobStore`,
`IngestionLauncher` como `Protocol`), `infra/d1.py`, `infra/r2.py`,
`infra/workflow.py` (adaptadores; reciben un *provider* del binding),
`api/admin.py` (router, auth y manejadores de error).

## Configuración (env del Worker)

| Variable | Default | Uso |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL exactos; otro valor falla al componer |
| `EXPOSE_DOCS` | `false` | Expone `/docs`, `/redoc`, `/openapi.json` |
| `RAG_BASE_URL` | `https://rag.internal` | Base de las URLs del service binding |
| `RAG_RETRIEVAL_TIMEOUT_SECONDS` | `10` | Timeout de `/retrieval/query` |
| `RAG_ANSWER_TIMEOUT_SECONDS` | `45` | Timeout de `/chat`: cubre retrieval + LLM |
| `ADMIN_TOKEN` | sin valor | **Secret** de Wrangler; sin él `/admin/*` responde 503 |
| `ADMIN_MAX_UPLOAD_BYTES` | `26214400` | Límite del PDF subido (≤ 50 MB: la ingesta rechaza PDFs mayores) |
| `RATE_LIMIT_SALT` | `unitropico-rate-limit-v1` | **Secret** opcional: salt del hash de la IP usado como clave del limitador |
| `RATE_LIMIT_REQUIRED` | `false` | `true`: limitador ausente o fallido → 503 (fail-closed) en vez de dejar pasar |

El secret se crea con `wrangler secret put ADMIN_TOKEN` (no ejecutado; requiere
login y el Worker desplegado). Para desarrollo local, en `.dev.vars` (ignorado
por Git). Los secretos del proveedor LLM viven en `apps/rag`.

Bindings (`wrangler.jsonc`): `RAG` (service binding), `DB` (D1 `unitropico`,
`migrations_dir: ../../infra/d1/migrations`; **`database_id` es un placeholder**
`REPLACE_WITH_D1_DATABASE_ID` hasta crear la base), `DOCUMENTS` (R2
`unitropico-documents`) e `INGESTION` (Workflow `ingestion`, clase
`IngestionWorkflow`, script `unitropico-ingestion`), `CHAT_LIMITER` y
`RETRIEVAL_LIMITER` y `ADMIN_AUTH_LIMITER` (Rate Limiting, ver abajo). Claves verificadas contra
`config-schema.json` de Wrangler 4.165.0 (cache npx local; el archivo valida
con `jsonschema` contra ese esquema).

Logging: el logger `unitropico_api` registra fallos de upstream con ruta y tipo
de error/estado (`route=answer reason=TimeoutError`), nunca la pregunta, el
historial, el cuerpo ni textos de evidencia.

## Rate limiting de rutas públicas

Contexto: chat público sin autenticación (~100 consultas/día, ~10 usuarios
simultáneos) y cada `/chat` cuesta una llamada al LLM (ARQUITECTURA.md §6).

| Binding | Ruta | Cuota por defecto | `namespace_id` |
| --- | --- | --- | --- |
| `CHAT_LIMITER` | `POST /chat` | 20 peticiones / 60 s por cliente | `"1001"` |
| `RETRIEVAL_LIMITER` | `POST /retrieval/query` | 30 peticiones / 60 s por cliente | `"1002"` |
| `ADMIN_AUTH_LIMITER` | `/admin/*`, solo intentos fallidos (401) | 10 / 60 s por cliente | `"1003"` |

Trade-off del NAT de campus: el chat sube de 10 a 20 por minuto porque muchos
estudiantes pueden compartir una IP pública; si aparecen 429 legítimos, ajustar
`limit` en `wrangler.jsonc`.

`ADMIN_AUTH_LIMITER` solo se consulta cuando la autenticación temprana de
`/admin/*` falla (401). Al excederse responde 429 `rate_limited` con
`Retry-After: 60`. Es fail-open con un WARNING en el log; los 503
(`admin_disabled`) y los 413/411 no cuentan. Las peticiones admin autenticadas
no pasan por ningún limitador.

**Cambiar la cuota**: editar `ratelimits[].simple` en `wrangler.jsonc`
(`limit` entero; `period` solo admite **10 o 60** segundos según el esquema de
Wrangler 4.165.0 y la documentación) y redesplegar; el código no conoce los
valores. `namespace_id` es una cadena con un entero positivo **único en la
cuenta** (dos bindings con el mismo id comparten contadores, incluso entre
Workers): revisar que `1001`/`1002`/`1003` no estén en uso antes de desplegar.
`Retry-After` es fijo a 60 (el periodo máximo); si se usa `period: 10` sigue
siendo una cota superior válida.

**Diseño** (capas como el resto del API):
- `services/rate_limit.py`: puerto `RateLimiter` (`Protocol`, `async allow(key) -> bool`,
  lanza `RateLimiterUnavailable`), `client_key` y `RateLimitGuard` (política
  fail-open/fail-closed; recibe salt y flag como valores, no `Settings`).
- `infra/rate_limit.py`: `WorkersRateLimiter(provider)` llama
  `await binding.limit(key=...)` (el wrapper del SDK 1.9.6 convierte los kwargs
  en el objeto de opciones `{key}` y el resultado `{success}` a dict; también se
  acepta acceso por atributo). Sin binding → `BindingMissing`; excepción del
  binding → nombre del tipo; `success` no booleano → `InvalidResult`.
- `api/rate_limit.py`: dependencia FastAPI añadida a las dos rutas, manejador del
  429 y chequeo temprano de tamaño. `main.py` compone; `worker.py` pasa
  `WorkersRateLimiter(lambda: request_binding("CHAT_LIMITER" | "RETRIEVAL_LIMITER"))`.
- El límite se evalúa como dependencia de la ruta: antes del servicio y por tanto
  **antes de llamar al RAG/LLM** (también antes de la validación del cuerpo: un
  cliente sobre la cuota recibe 429 aunque el JSON sea inválido). `/health` y
  `/docs` no se limitan; en `/admin/*` solo cuentan los 401 (`ADMIN_AUTH_LIMITER`).

**Clave del cliente**: HMAC-SHA256 (sal `RATE_LIMIT_SALT`), truncado a 32 hex,
de `CF-Connecting-IP` (la pone Cloudflare con la IP del cliente) normalizada:
- IPv4: la IP completa.
- IPv6: el prefijo /64 (un cliente suele controlar un /64 entero).
- IPv4-mapped (`::ffff:a.b.c.d`) se trata como IPv4; si falta o es inválida, la
  clave es `anonymous`.

El salt es el secret
`RATE_LIMIT_SALT` o, si falta/está vacío, el valor por defecto documentado
`unitropico-rate-limit-v1` (no es secreto: solo evita pasar IPs en claro al
limitador). Sin cabecera (desarrollo local) todos comparten la cuota de
`anonymous`. La IP y la clave **nunca se registran**: los logs son
`rate limited route=chat` (INFO) y `rate limiter unavailable route=... reason=...
fail=open|closed` (WARNING). Limitaciones conocidas: Cloudflare desaconseja
claves por IP porque NAT/redes móviles/proxys comparten IP (un campus detrás de
una sola IP compartiría cuota; subir `limit` si ocurre); a falta de cuentas de
usuario es el único identificador estable disponible.

**Proxy desde la web**: si `apps/web` reenvía la petición por service binding,
debe **preservar `CF-Connecting-IP`** (reenviar el `Request` original o copiar
la cabecera). Si no, todas las peticiones compartirían una clave (`anonymous` o
la IP del Worker: en subrequests entre zonas Cloudflare la sustituye por
`2a06:98c0:3600::103`). Con `workers_dev: false` y sin ruta pública el API solo
es alcanzable por service binding, así que la cabecera la controla la web.

**Fallo del limitador**: por defecto **fail-open** con WARNING (un fallo o la
ausencia del binding no debe tumbar el chat; el coste queda acotado por el bajo
tráfico y el timeout del RAG). Con `RATE_LIMIT_REQUIRED=true`, **fail-closed**:
503 con el cuerpo propio de cada ruta (`/chat`: `{"status":"unavailable",
"referral":...}`; `/retrieval/query`: `{"status":"unavailable","evidence":[]}`),
sin llamar al RAG. Ese flag también cierra las rutas si `create_app` se compone
sin limitadores.

**Tamaño del cuerpo**: `ChatRequest` ya limita `question` ≤ 500 y `history`
≤ 6 × 2000 code points, pero esa validación ocurre después de que el puente
ASGI haya leído todo el cuerpo. Por eso `Default.fetch` aplica
`early_public_body_rejection` junto a `early_upload_rejection`: `POST /chat` y
`POST /retrieval/query` con `Content-Length` > 64 KiB → 413; sin
`Content-Length` (o inválido) con `Transfer-Encoding: chunked` → 411. Sin
ninguna de las dos cabeceras se deja pasar (cuerpo vacío → 422). 64 KiB cubre un
historial máximo en UTF-8 (~50 KB en el peor caso); un cliente que escape todo a
`\uXXXX` con emojis podría superarlo, caso que se acepta.

**Semántica del binding** (documentación oficial): el límite es **por
ubicación** de Cloudflare (cada PoP cuenta por separado), los contadores son
**eventualmente consistentes** y la cuota es **aproximada** (puede superarse
mientras se sincroniza); un límite estricto global requeriría Durable Objects.
Una petición rechazada no incrementa el contador. El binding no aparece en el
dashboard: observar los 429 en Workers Logs. Estado: estable (GA) desde
2025-09-19. En `wrangler dev --local`, Miniflare de Wrangler 4.165.0 incluye una
emulación (`miniflare/dist/src/workers/ratelimit/`, contador por ventana fija en
un Durable Object local; valida que las opciones solo sean
`key|limit|period|delta` y `period` ∈ {10, 60}), leída en el código de la cache
npx, **no ejecutada**.

Fuentes consultadas:
- https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
- https://developers.cloudflare.com/changelog/2025-09-19-ratelimit-workers-ga/
- https://developers.cloudflare.com/fundamentals/reference/http-headers/
  (`CF-Connecting-IP` y su valor en subrequests de Workers)
- `config-schema.json` de Wrangler 4.165.0 (`ratelimits`: `name`,
  `namespace_id`, `simple.{limit, period ∈ {10, 60}}`, sin propiedades extra) y
  `workers/rpc.py` del SDK 1.9.6 (`Ratelimit` está en `_EnvWrapper._BINDING_TYPES`,
  se envuelve con `_BindingWrapper`).

**No verificado**: el binding real en Cloudflare y en `pywrangler dev --local`
(solo dobles en CPython), la conversión real de `limit(key=...)` en Pyodide, la
disponibilidad/precio por plan (la documentación no lo indica), que el puente
ASGI no lea el cuerpo cuando el Worker responde antes, y la presencia de
`CF-Connecting-IP` al llegar por service binding desde la web.

## Workers

Los SDK `workers-py==1.17.9` y `workers-runtime-sdk==1.9.6` están en el grupo
`dev`. `src/worker.py` exporta `Default(WorkerEntrypoint)`: compone una vez por
isolate (lazy) Settings (desde las variables del `env`) y la app FastAPI,
envuelta en `RequestEnvMiddleware`. `asgi.fetch` coloca el `env` de la petición
en `scope["env"]`; el middleware lo expone en un `ContextVar` y
`RequestScopedRagClient` resuelve `env.RAG` por llamada (sin binding:
`UpstreamUnavailable`, es decir 503). Los adaptadores admin reciben
`lambda: request_binding("DB" | "DOCUMENTS" | "INGESTION")` y fallan cerrados
(503) si el binding falta. Solo este bridge importa el SDK.

`wrangler.jsonc` conserva `2026-11-08`, `python_workers`, `workers_dev: false` y
`services: [{"binding":"RAG","service":"unitropico-rag"}]`, y añade D1, R2 y
Workflow y los dos `ratelimits` (ver Configuración). Es JSON sin comentarios (lo lee un test). No despliega.

`RagServiceBindingClient(binding, *, base_url, retrieval_timeout, answer_timeout)`
hace un único `await binding.fetch` (POST JSON) por llamada, sin retries ni HTTPX.
`ServiceBinding` es un `Protocol`. Fallos de transporte, timeout, status no entero
o no-2xx lanzan `UpstreamUnavailable(reason, referral=None)`; JSON no decodificable
lanza `MalformedUpstream`. El `except Exception` solo envuelve `binding.fetch`,
porque las excepciones JS no comparten base.

**Compatibilidad con apps/rag**: `RagServiceBindingClient(binding)` sigue
aceptando un solo argumento. `apps/rag/tests/test_api_integration.py` espera
evidencia con `page` (formato antiguo) y que no se emita ningún log en fallo, lo
cual contradice el formato nuevo y el logging de fallos de upstream; hay que
actualizarla.

Herramientas observadas durante la preparación: Node **24.15.0**, npm **11.12.1**,
Wrangler **4.158.0**, workerd **1.20261104.1** (metadatos del paquete instalado).
Wrangler requiere Node >=22. Pywrangler invoca `npx --yes wrangler`, por lo que
su versión efectiva depende del entorno/cache npm y no queda fijada en uv.lock.
El código instalado de pywrangler 1.17.9 fija ese comando y no ofrece una opción
pequeña de configuración para seleccionar Wrangler 4.158.0. No se añadieron
package.json, parches del SDK ni herramientas alternativas; comprobar la versión
efectiva antes de repetir la PoC.

Workers-py 1.17.9 requiere uv >=**0.13.4** para preparar Pyodide y Wrangler
>=**4.127.1**. Se verificó usando uv **0.13.7** aislado mediante uvx, sin
actualizar el uv global **0.13.0**. Desde `apps/api`:

```sh
uvx --from uv==0.13.7 uv --version
uv sync --locked --group dev
uv run pywrangler --version
uv run pywrangler --help
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.7 uv run pywrangler dev --local --ip 127.0.0.1 --port 8787
```

El último comando histórico mantiene un servidor: no se ejecutó para este
transporte. Para una futura prueba API→RAG, el operador deberá disponer de ambos
Workers en desarrollo local y enlazar el nombre exacto `unitropico-rag`.
Usar únicamente Wrangler ya disponible en cache y modo local; el wrapper
pywrangler sincroniza dependencias e invoca `npx --yes`, así que no garantiza
una ejecución offline. No se verificó aquí un comando de arranque multiworker.
Sin login, tokens, `--remote` ni deploy. La prueba anterior consistió en probar
`GET http://127.0.0.1:8787/health` (200) y `/missing` (404), y detenerlo al terminar.
No requiere Uvicorn ni un cliente HTTP Python adicional.

La PoC fue ejecutada por el operador padre: `/health` devolvió **200** con
`{"status":"ok"}` y `/missing` **404** con `{"detail":"Not Found"}`. El job local
terminó con exit 0 en 14 segundos. El builder Pyodide utilizó Python **3.14.2**;
el host de pruebas CPython utilizó **3.14.6**. Son verificaciones distintas.

Pywrangler prepara `.venv-workers/`, `python_modules/` y estado `.wrangler/`
(ignorados por Git y excluidos explícitamente mediante `tool.ruff.extend-exclude`,
también en copias autónomas sin `.git`).
Se conserva **pylock.toml** como evidencia reproducible, sin ignorarlo:

- **uv.lock** fija dependencias de aplicación y desarrollo para el host CPython.
- **pylock.toml** fija la resolución para Pyodide/WASM, con URLs y hashes de
  wheels. Contiene FastAPI **0.162.2**, Pydantic **2.14.3**, pydantic-core
  **2.50.4** con wheel `cp314-cp314-pyemscripten_2026_0_wasm32`, y
  workers-runtime-sdk **1.9.6**.

No son locks intercambiables. Pywrangler resuelve automáticamente el SDK y
reutiliza pylock.toml como restricciones en siguientes resoluciones; revisar
sus cambios, especialmente tras regenerarlo o solicitar una actualización.
El pin del grupo dev por sí solo no fija el SDK del runtime.

## Límites de este entregable

**Probado únicamente en Workers local (workerd), no en Cloudflare remoto.**
La PoC anterior verifica el bootstrap y dos rutas HTTP, no el transporte
API→RAG nuevo ni la compatibilidad de dependencias futuras. El transporte solo
se verificó mediante pruebas deterministas ASGI con binding de test; falta la
prueba funcional con ambos Workers locales. No hubo login, deploy ni creación
de recursos remotos. Ingesta, OCR, frontend, generación LLM y publicación de
corpus quedan fuera de este cambio.

**Admin**: probado solo con dobles en memoria. El adaptador D1 se ejecuta contra
sqlite3 con la migración real `infra/d1/migrations/0001_initial.sql` (triggers,
CHECK y UNIQUE reales), imitando la forma `{"results": [...]}` de D1. No se ha
verificado en workerd ni contra bindings reales: la conversión de `bytes` a
`Uint8Array` en `R2.put`, los kwargs como objeto de opciones, el texto exacto de
los errores de D1/Workflow (se detectan por subcadena: códigos de trigger,
`UNIQUE/CHECK/FOREIGN KEY constraint failed`, `already_exists`, `not_found`) y
`RETURNING` en D1.
