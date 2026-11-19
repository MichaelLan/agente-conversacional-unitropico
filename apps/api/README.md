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
  chat_client=None, documents=None, blobs=None, ingestion=None)` es la factory
  ASGI; sin puertos admin, `/admin/*` responde 503 `storage_unavailable`. Sin `settings` usa
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
  (ambos `{"status":"unavailable","evidence":[]}`).
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
  El historial no se persiste.

## Administración (`/admin`, contrato v1 de `docs/IMPLEMENTATION_STATUS.md` sec. 4b)

**Auth**: todas las rutas exigen `Authorization: Bearer <ADMIN_TOKEN>`, comparado
con `hmac.compare_digest` (bytes UTF-8). Sin token válido: 401 con
`WWW-Authenticate: Bearer`. Si `ADMIN_TOKEN` no está configurado o está vacío,
**todas** las rutas admin responden 503 `admin_disabled` (nunca quedan abiertas).
La autenticación se resuelve antes de leer o validar el cuerpo.

**Chequeo temprano de `POST /admin/uploads`** (`src/worker.py` +
`api/admin.early_upload_rejection`): el puente ASGI del SDK de Workers lee todo el
cuerpo antes de invocar FastAPI, así que el Worker decide solo con las cabeceras,
antes de `asgi.fetch` y sin tocar el cuerpo (ruta normalizada: `//` y `/` final).
Orden: 503 `admin_disabled` (sin token) → 401 `unauthorized` (con
`WWW-Authenticate: Bearer`; misma `check_admin_token`/`hmac.compare_digest` que
FastAPI) → 411 `length_required` (falta `Content-Length`) → 413 `payload_too_large`
(supera `ADMIN_MAX_UPLOAD_BYTES`). FastAPI repite auth y límite como defensa en
profundidad. No verificado en workerd real (solo con dobles del SDK). El token nunca se
registra en logs (`Settings.admin_token` es `SecretStr`). Sin cookies, sesión ni
rate limiting (fuera de alcance).

Errores: `{"error": {"code": "...", "message": "texto en español"}}`; también los
422 de validación bajo `/admin` (las rutas públicas conservan el 422 de FastAPI).

| Ruta | Éxito | Errores |
| --- | --- | --- |
| `POST /admin/uploads?title&document_type&version_label[&document_id]` | 201 nuevo / 200 mismo sha | 404 `document_not_found`, 409 `source_registered_elsewhere`/`version_label_taken`, 411 `length_required`, 413 `payload_too_large`, 415 `unsupported_media_type`, 422 `invalid_pdf`/`invalid_request` |
| `GET /admin/documents` | `{documents: [{id,title,document_type,created_at,versions:[...]}]}` | |
| `GET /admin/versions/{id}` | versión + documento + `error_detail` + `processing_events` + `workflow` (o null) | 404 |
| `PATCH /admin/versions/{id}` | `{document, version}` | 404, 409 `version_published`/`document_has_published_version`/`version_label_taken`/`review_not_available`/`supersedes_cycle`, 422 `invalid_request`/`invalid_effective_range`/`invalid_supersedes`/`supersedes_other_document` |
| `POST /admin/versions/{id}/publish` | `{document, version}` | 409 `invalid_status_transition`/`review_required`/`publish_without_chunks`/`chunk_page_out_of_range`/`publish_requirements_unmet` |
| `POST /admin/versions/{id}/unpublish` | `{document, version}` | 409 si no está `published` |
| `POST /admin/versions/{id}/reprocess` | `{document, version, ingestion}` | 409 `invalid_status_transition`/`ingestion_in_progress`, 503 `workflow_launch_failed` |

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
  Versión `published` → 409 (despublicar primero). Documento y versión se
  actualizan en un único `batch`.
- **Publish**: desde `ready` (y `unpublished`, que el esquema permite, para volver
  a publicar tras editar) con `requires_review=false`; fija `published_at`. Los
  triggers/CHECK del esquema (chunks, rango de páginas, `page_count`, etc.) se
  traducen a 409.
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
- **processing_events** (el esquema no tiene pasos propios para acciones admin):
  `upload` para alta (`source_stored`), lanzamientos (`workflow_*` o
  `failed/workflow_launch_failed`) y `reprocess_requested`; `review` para
  `metadata_updated:<campos>`; `publish`/`unpublish`. Los eventos de transiciones
  se insertan en el mismo `batch` que el UPDATE, condicionados a que este se
  aplicara.

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

El secret se crea con `wrangler secret put ADMIN_TOKEN` (no ejecutado; requiere
login y el Worker desplegado). Para desarrollo local, en `.dev.vars` (ignorado
por Git). Los secretos del proveedor LLM viven en `apps/rag`.

Bindings (`wrangler.jsonc`): `RAG` (service binding), `DB` (D1 `unitropico`,
`migrations_dir: ../../infra/d1/migrations`; **`database_id` es un placeholder**
`REPLACE_WITH_D1_DATABASE_ID` hasta crear la base), `DOCUMENTS` (R2
`unitropico-documents`) e `INGESTION` (Workflow `ingestion`, clase
`IngestionWorkflow`, script `unitropico-ingestion`). Claves verificadas contra
`config-schema.json` de Wrangler 4.165.0 (cache npx local).

Logging: el logger `unitropico_api` registra fallos de upstream con ruta y tipo
de error/estado (`route=answer reason=TimeoutError`), nunca la pregunta, el
historial, el cuerpo ni textos de evidencia.

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
`services: [{"binding":"RAG","service":"unitropico-rag-poc"}]`, y añade D1, R2 y
Workflow (ver Configuración). Es JSON sin comentarios (lo lee un test). No despliega.

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
Workers en desarrollo local y enlazar el nombre exacto `unitropico-rag-poc`.
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
