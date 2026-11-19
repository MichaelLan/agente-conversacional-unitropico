# Estado de implementación

Documento operativo para retomar el proyecto. Se actualiza tras cada grupo de cambios significativo.
Fuente de intención: `ARQUITECTURA.md`. Este archivo prevalece sobre `TRASPASO_SESION.md` (histórico, ignorado por Git) y sobre cualquier "estado observado" de otros documentos cuando discrepen.

Última actualización: 2026-11-18. Trabajo de la sesión commiteado en 8 commits firmados (`df3af9c`..`9b0f138`), sin push.

## 1. Estado por componente

| Componente | Estado | Resumen | Tests locales |
|---|---|---|---|
| `apps/web` | Implementado; verificado con Playwright (Chromium headless) contra API falsa | Chat público estilo Nuxt UI con sidebar de FAQ de ejemplo (`FAQ_IS_PLACEHOLDER=true`), respuestas en texto plano, citas desplegables, reintentar, aria-live, estado solo en memoria. Panel admin en `/admin/` (multipágina Vite; token solo en memoria) con revisión del texto extraído y correcciones OCR. Worker `unitropico-web` (Static Assets + proxy `/api/*`, CSP en `_headers`). Mobile first (≥44 px táctil, inputs 16 px, safe areas, `dvh`; admin en tarjetas en móvil) y PWA instalable por app (`/` y `/admin/`: manifest, iconos propios, SW escrito a mano que solo cachea el shell y nunca `/api`; Lighthouse 11.7.1 PWA/a11y/best-practices = 1 en `/` y login admin). Sin verificar en dispositivos iOS/Android reales. | 84 node:test; lint, typecheck, build |
| `apps/api` | Implementado | `GET /health`, `POST /retrieval/query`, `POST /chat` (contrato v1, proxy al RAG por service binding), `/admin/*` (contrato 4b, Bearer `ADMIN_TOKEN`, D1 + R2 + Workflow binding). App compuesta una vez por isolate. | 444 pytest; Ruff |
| `apps/rag` | Implementado | `POST /answer` (v1) y `/retrieval/query`. Recuperación híbrida: D1 FTS5 (`unicode61 remove_diacritics 2`) + Vectorize + RRF k=60, `RETRIEVAL_MODE` hybrid/lexical/semantic. Generación `openai/gpt-6-luna` vía Vercel AI Gateway (json_schema strict, `reasoning_effort=low`). Validación de citas literales, revalidación de catálogo, abstención con `reason` y `referral`. Orquestación sobre `langchain-core` 1.8.3 (opción B): `ChunkRetriever(BaseRetriever)`, `GatewayEmbeddings`, `GeneratorChatModel(BaseChatModel)`, `ChatPromptTemplate` y cadena LCEL async; métricas por fase en log (`answer_metrics`). Seguimiento conversacional `FOLLOWUP_STRATEGY` none|concat (default, sin coste)|rewrite (1 llamada extra, para evaluación) y umbral opcional `MIN_RELEVANCE` sobre coseno de Vectorize (desactivado; abstiene con `no_evidence`). | 465 pytest; Ruff |
| `workers/ingestion` | Implementado | Chunker estructural `structural-v1` + baseline `fixed-v1`. `IngestionWorkflow`: mark_processing → ocr (Firecrawl; reutiliza R2) → chunk → embed (reutiliza R2) → index Vectorize → finalize `review_required`/`ready`. Nunca publica; toda extracción nueva termina en `review_required`. | 185 pytest; Ruff |
| `infra/d1` | Implementado | Migración `0001_initial.sql`: documentos, versiones con vigencia y sustitución, máquina de estados por trigger, chunks, FTS5, `processing_events`, vista `v_chunk_registry`. SQL canónico en `queries.sql`. | 90 unittest; aplicado en D1 local (antes de `text_corrections`) |
| `infra/vectorize` | Diseñado | Índice `unitropico-chunks-1536`, cosine, metadatos `version_id`/`document_id`. Comandos documentados, no ejecutados. | — |
| `evaluations/ocr` | Activo | Pilotos Firecrawl, corpus v2, auditoría visual de 216 páginas, OCR selectivo v1 (106 páginas) y ensamblado de caché para la ingesta. Ruff/pytest/CI propios. | 97 pytest |
| `evaluations/rag` | Implementado, **sin resultados** | 55 preguntas candidatas **sin validar** (37 dev / 18 test). Harness HTTP: Recall@k, MRR, nDCG, abstención, exactitud de citas, latencia, export CSV para fidelidad humana. | 58 pytest |
| CI | 7 workflows | `web`, `api`, `rag`, `ingestion`, `evaluations` (ocr), `evaluations-rag`, `integration`; filtros de paths (API/RAG/ingesta incluyen `infra/d1/**`). Sin despliegues. Solo api/rag/web han corrido en GitHub (sobre `fa23faf`). | — |

### Verificado vs no verificado
- **Ejecutado en workerd local (`pywrangler dev --local`)**: RAG `/health`, `/retrieval/query` contra D1 local con FTS5 y catálogo; Workflow de ingesta registrado y fallo `config_missing` persistido en D1 local; migración D1 aplicada en local.
- **Solo con fakes/sqlite en CPython**: transporte API→RAG por service binding, endpoints admin contra bindings reales, pasos chunk/embed/index del Workflow en workerd.
- **No verificado**: Vectorize real (sin emulación local), gateway real (chat y embeddings), Firecrawl desde el Worker, recursos remotos, despliegue, render en navegador, límites de producción (1 MiB por paso, CPU, memoria, arranque).

### Corpus
- 7 PDF reales en `documents/documentos_bienestar/` (ignorado por Git), 216 páginas.
- `evaluations/ocr/.artifacts/extraction-corpus-v2/`: 201 páginas con texto, todo `requires_review=true`, `publication_blocked=true`.
- **15 páginas de la Convocatoria 2026-1 sin OCR.** El usuario confirmó en el Dashboard de Firecrawl un consumo total de **22 créditos** (= 20 de Res. 00137 + 2 del piloto 02): el lote de 15 páginas y el primer piloto **no se cobraron**. No borrar `execution/` (evidencia histórica). Un nuevo OCR de esas páginas queda autorizado por el usuario cuando haga falta ("si necesitas el OCR, úsalo"); se hará por el pipeline de ingesta o lo ejecutará el usuario (la clave está en `.env`, que los agentes no leen).
- Defecto OCR conocido: Res. 00137 p18 dice "igual a 3.0"; el original dice "igual o superior a 3.0". Se corrige en la revisión con una corrección literal (ver §4) antes de publicar.
- Contradicciones reales detectadas (útiles para evaluación): Res. CBU 002-2025 (25 días hábiles vs calendario); Jóvenes en Paz (Res. 00137 vs Documento Técnico); grupos Sisbén (Documento Técnico vs Res. CBU 002-2025).
- No existen FAQs institucionales verificadas.

## 2. Decisiones vigentes

### Arquitectura y stack
- Monorepo de proyectos autónomos; sin pyproject/workspace/package.json raíz; sin contratos generados; tipos manuales por app.
- Python 3.14, uv (0.13.7 en CI y locks; `pywrangler` requiere ≥ 0.13.4 → `uvx --from uv==0.13.9` aislado), FastAPI (API y RAG; la ingesta no usa FastAPI), Pydantic, Ruff, pytest. API, RAG e ingesta son Cloudflare Python Workers (`uv.lock` host + `pylock.toml` WASM).
- Settings solo en la composición (`main.py`/`worker.py`); logger única excepción transversal; puertos con Protocol.
- Web: Vue 3 + Vite + Nuxt UI 4.12.0 standalone + TS 6.0.3 + pnpm. Sin Router, Pinia, cliente HTTP externo ni framework de tests adicional (node:test).
- Sin despliegues automáticos desde CI.

### Decisiones del usuario (2026-11-18)
- Generador `openai/gpt-6-luna` vía Vercel AI Gateway (ID verificado en catálogo; inferencia NO probada).
- Embeddings `openai/text-embedding-3-small`, 1536 dims, vía Vercel AI Gateway.
- Respuestas en **texto plano**; layout de chat inspirado en Nuxt UI con sidebar de **preguntas frecuentes**.
- Auth admin: **token secreto en la API** (Bearer, comparación en tiempo constante).
- Web **mobile first** (no solo responsive) e **instalable** (PWA sin dependencias: manifest por app, service worker propio que solo cachea el shell y nunca `/api`). Panel admin con el mismo lenguaje visual del chat (referencia Nuxt UI).
- Ingesta: **Python Worker + Cloudflare Workflow**.
- LangChain: **opción B** de `docs/research/langchain-evaluation.md`: `langchain-core` 1.8.3 como columna vertebral (BaseRetriever, Embeddings, BaseChatModel, ChatPromptTemplate, LCEL async) sobre adaptadores propios; validaciones propias se conservan; sin langgraph, langchain-openai, classic, community ni text-splitters por ahora.

### Decisiones de datos (orquestador)
- Vectorize: id = `chunks.id` (`{sha16(version_id‖chunker_version)}:{ordinal:04d}:{sha12(text)}`, 35 bytes); upsert por el Workflow al indexar; publicar/despublicar solo cambia D1 (el RAG revalida siempre); `deleteByIds` al reprocesar. Embeddings y OCR persistidos en R2 para no pagarlos dos veces.
- Publicación siempre manual (`ready`/`unpublished` → `published` con `requires_review=0`).
- Convocatorias vencidas: se recuperan con su vigencia y el modelo debe advertirlo.
- `supersedes_version_id` 1:1 en el MVP.
- Unicidad del original: `source_r2_key = raw/{sha256}.pdf` es UNIQUE global → el mismo PDF no puede registrarse en dos documentos (409 `source_registered_elsewhere`).
- Publicar no exige embeddings en todos los chunks.
- OCR del corpus (usuario, 2026-11-19): reutilizar el texto ya extraído (corpus v2) solo en las páginas que sirvan al 100 %; el resto se reprocesa con Firecrawl. Auditoría visual completa (`evaluations/ocr/reports/extraction-quality-audit.{md,json}`): 66 ok, 44 minor, 91 defect, 15 missing; ningún documento sirve al 100 % tal cual; de las 22 páginas ya procesadas por Firecrawl, 14 tienen defectos sustantivos. **El usuario autorizó 106 créditos** (86 defect/missing + 20 con errores puntuales). OCR selectivo fuera del Worker (fragmentos con Poppler, envío ejecutado por el usuario con su clave, auditoría visual de las páginas nuevas, ensamblado de la caché en formato del paso `ocr` y precarga en R2) para no pagar los documentos completos. Preparado (`evaluations/ocr/selective_*`, `assemble_cache.py`; 97 tests): 7 lotes / 106 páginas en `.artifacts/selective-ocr-v1/` verificados contra los originales; Envío 2026-11-19: lotes 01–03 completos (45 págs., 45 créditos declarados); lote 04 incierto (network_error, consumo desconocido, pendiente de conciliar en el Dashboard); 05–07 sin enviar. Auditoría de las 45 páginas nuevas (`reports/selective-ocr-v1-audit.*`): 10 usables tal cual, 32 con 118 correcciones literales, 3 a revisión manual; Firecrawl inventa y altera contenido → todo OCR requiere revisión antes de publicar. Hará falta una vía para sembrar la caché de extracción en R2 con el texto reutilizado (formato compatible con el paso `ocr`) para que el Workflow no pague esas páginas.
- En D1, `meta.changes` incluye filas tocadas por triggers: no comparar con `== 1`.

## 3. Contrato `/answer` (RAG) y `/chat` (API) — v1

Petición (idéntica en ambos):
```json
{"question": "1..500 code points tras strip", "history": [{"role": "user|assistant", "content": "1..2000"}]}
```
`history` opcional (no `null`), máx. 6 elementos, extras prohibidos. No se persiste.

Respuesta 200:
```json
{
  "status": "answered | abstained",
  "answer": "texto plano en español | null",
  "reason": "null | no_evidence | model_abstained | invalid_output | invalid_citation | stale_source",
  "citations": [{"chunk_id": "", "document_id": "", "document_title": "", "version_id": "",
                 "version_label": "str|null", "section": "str|null", "page_start": 1, "page_end": 1, "quote": ""}],
  "referral": "texto de derivación | null"
}
```
- `answered` ⇒ `answer` no nulo, ≥1 cita, `reason` null. `abstained` ⇒ `answer` null, sin citas, `reason` y `referral` no nulos.
- 503 `{"status":"unavailable","referral":"..."}`; 502 en API si el RAG responde inválido; 422 validación. En la API además: 429 `{"error":{"code":"rate_limited","message":...}}` con `Retry-After`, y 413/411 tempranos.
- Metadatos de cita solo desde D1; quote literal (espacios normalizados, ≥12 caracteres visibles).
- `usage` (tokens) no forma parte de v1: para medir costo desde el harness haría falta v2.

## 4. Contrato admin (API) — v1

Rutas `/admin/*` con `Authorization: Bearer <ADMIN_TOKEN>`. Sin token configurado: 503 `admin_disabled`; token inválido: 401; sin bindings: 503 `storage_unavailable`. Errores `{"error":{"code","message"}}`.
- `POST /admin/uploads` — cuerpo `application/pdf` ≤ 25 MB, magic `%PDF-`; query `title`, `document_type`, `version_label`, `document_id?`. R2 `raw/{sha}.pdf` → D1 `uploaded` → Workflow `ver_{version_id}`. 201 nuevo / 200 existente / 409 `source_registered_elsewhere`; si falla el lanzamiento, 201 con `ingestion.outcome: "failed"` (recuperable con reprocess).
- `GET /admin/documents`, `GET /admin/versions/{id}` (con `processing_events` y estado de la instancia).
- `PATCH /admin/versions/{id}` — `title`, `document_type`, `version_label`, `issued_at`, `effective_from`, `effective_until`, `supersedes_version_id`, `requires_review` (false: `review_required`→`ready`; true: `ready`/`unpublished`→`review_required`). 409 si la versión está publicada.
- `POST .../publish` (desde `ready`/`unpublished`, revisada), `.../unpublish`, `.../reprocess` (pone `requires_review=1`; el Workflow pasa a `processing`; 409 `ingestion_in_progress`).
- Payload Workflow: `{version_id, document_id, source_r2_key, source_sha256}`.
- Revisión y correcciones OCR: `GET /admin/versions/{id}/chunks?offset&limit` (≤100); `GET|POST /admin/versions/{id}/corrections` (`{page_number, find_text 1..500, replace_text 0..500, reason 1..300}`); `DELETE /admin/versions/{id}/corrections/{cid}` (borrado lógico). Crear/borrar devuelve la versión a `review_required`; 409 si está publicada. Se aplican al reprocesar (paso `chunk`, sin volver a pagar OCR): `find_text` debe aparecer exactamente una vez en la página (espacios equivalentes) o la versión falla con `correction_not_applicable`. Eventos con paso `correct`; `correction_ids` en el JSONL de chunks.
- `corrections_dirty: bool` en las versiones (listado, detalle y respuestas de correcciones): lo ponen a 1 triggers de D1 en cada alta/baja de corrección (`corrections_rev` +1) y solo lo limpia el paso `chunk` si `corrections_rev` no cambió desde que leyó las correcciones. Mientras sea true: `PATCH requires_review=false` y `publish` → 409 `corrections_pending` (además, trigger de D1 impide publicar). Las correcciones se aplican sobre el texto preparado que ve el administrador (tablas convertidas, cabeceras repetidas eliminadas). `GET .../corrections` incluye `match_rule`. Unicidad activa (versión, página, find_text) e inserción con tope de 100 en una sola sentencia; caracteres Unicode `Cf` → 422.

## 5. Revisión crítica (2026-11-18) — corregida
Revisión independiente de auth admin, upload, Workflow y panel: sin hallazgos críticos. Corregidos: rechazo temprano de `/admin/uploads` en `Default.fetch` antes de leer el cuerpo (503/401/411/413); reprocesar desde `processing`/`extracted`; reprocess que da 409 no modifica la base; `START_PROCESSING` fuerza `requires_review=1`; OCR truncado no se cachea y la clave incluye `max_pages` (`ocr/{sha}/{ocr_version}-p{max_pages}.json`); PATCH de título bloqueado si otra versión está publicada; `supersedes` mismo documento y sin ciclos; límite 50 MB coherente; `processing_events` append-only también ante DELETE; refresco del panel acotado. Pendiente: comprobar en workerd real que el puente ASGI no lee el cuerpo cuando se responde antes.

## 6. Siguientes tareas recomendadas
1. ✔ CI de integración: proyecto `tests/integration/` (22 tests, `integration.yml`); `apps/rag` ya no importa `apps/api`.
2. ✔ Rate limiting: bindings `CHAT_LIMITER` (20/60 s), `RETRIEVAL_LIMITER` (30/60 s) y `ADMIN_AUTH_LIMITER` (10/60 s, solo 401) con clave HMAC de `CF-Connecting-IP` normalizada (IPv4 completa, IPv6 /64; `RATE_LIMIT_SALT`); rechazo temprano con Bearer en todo `/admin/*`, 429 `rate_limited` con `Retry-After: 60`, fail-open salvo `RATE_LIMIT_REQUIRED=true`; 413/411 tempranos para cuerpos > 64 KiB. Binding real NO verificado; confirmar `namespace_id` 1001/1002/1003 libres antes de desplegar.
3. ✔ Despliegue same-origin de la web: Worker `unitropico-web` con Static Assets (`dist/`) y `run_worker_first: ["/api/*"]`; `worker/index.js` reenvía por service binding `API` solo `POST /api/chat`, `GET|HEAD /api/health` y `GET|POST|PATCH /api/admin/*` (sin `/api/retrieval/query`), conservando cabeceras (`CF-Connecting-IP`, `Authorization`) y cuerpo; CSP y cabeceras en `public/_headers`; 429 tratado en el chat. Verificado con dos `wrangler dev --local` enlazados; despliegue y CSP en navegador NO verificados.
   ✔ Workers renombrados: `unitropico-web`, `unitropico-api`, `unitropico-rag`, `unitropico-ingestion`; bindings `apps/web`→`unitropico-api`, `apps/api`→`unitropico-rag`.
4. ✔ Runbook de despliegue `docs/DEPLOYMENT.md`; `deploy --dry-run` OK en los 4 Workers (web 2,6 KiB; api 9,6 MiB; rag 21,8 MiB; ingesta 224 KiB).
5. Con autorización de gasto/recursos: crear D1/R2/Vectorize, aplicar migración, cargar los 7 PDF por el panel, revisar OCR (corregir Res. 00137 p18), publicar, completar `evaluations/rag/datasets/document_map.json` y ejecutar la evaluación en dev (léxico vs semántico vs híbrido; estructural vs fijo).
6. Validación humana del dataset y congelación del split test; FAQs verificadas.

## 7. Bloqueos y pendientes de aprobación del usuario
- Gasto: inferencia y embeddings reales, creación de recursos Cloudflare, despliegue. Plan Workers Paid: el usuario lo comprará.
- Política de publicación del OCR revisado (¿basta revisión del investigador?).
- Validación humana del dataset de evaluación y de las FAQs.
- Colección local de iconos (hoy Nuxt UI los descarga de api.iconify.design).
- Commits: autorizados por componente (inglés, `-s -S`, sin Co-Authored-By); requieren desbloquear el gpg-agent del usuario.

## 8. Validaciones ejecutadas (2026-11-18, local, por el orquestador)
- api: Ruff OK, 420 passed. rag: Ruff OK, 428 passed (13 DeprecationWarning de LangSmith). ingestion: Ruff OK, 170 passed. integration: Ruff OK, 22 passed. evaluations/rag: Ruff OK, 58 passed. evaluations/ocr: Ruff OK, 62 passed. infra/d1: 83 unittest OK. web: lint, typecheck, 71 tests, build (genera `dist/admin/index.html`) OK.
- YAML de workflows validado localmente.

## 9. Referencias
- `docs/research/python-workflows-bindings.md` — Workflows Python, bindings D1/R2/Vectorize, fetch y multipart.
- `docs/research/langchain-evaluation.md` — compatibilidad de LangChain en Workers y recomendación.
- `infra/README.md`, `infra/d1/queries.md`, `infra/vectorize/README.md`.
- README de cada proyecto.
