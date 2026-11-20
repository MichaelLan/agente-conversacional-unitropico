# Estado de implementación

Estado ACTUAL y compacto para retomar el proyecto. Fuente de intención: `ARQUITECTURA.md`. Este archivo prevalece sobre cualquier "estado observado" de otros documentos. El detalle de cada contrato y componente está en el README de su proyecto. Última actualización: 2026-11-20.

## 0. Cómo trabajar en este proyecto (OBLIGATORIO)

- **El agente principal NUNCA implementa.** Siempre delega en subagentes: Opus para investigación, implementación compleja y revisiones críticas; Sonnet para tareas sencillas o moderadas. Cada subagente recibe propiedad EXCLUSIVA de archivos y criterios de aceptación. El principal solo define tareas y contratos, revisa diffs, ejecuta verificaciones e integra. Informes breves; esperar informes antes de depender de ellos. Python siempre con `uv`.
- **Commits**: en inglés, cortos, `git commit -s -S`, sin Co-Authored-By. El usuario desbloquea GPG en su terminal: `export GPG_TTY=$(tty); echo test | gpg --clearsign -u DAC79F37ECE1197C >/dev/null`.
- **Memoria**: guardar decisiones y estado importantes en Engram: `engram save … --project agente-conversacional-unitropico`.
- **Límites**: no leer `.env`; no gastos, despliegues, migraciones remotas ni borrado de datos sin autorización expresa. No inventar IDs de modelo, métricas ni afirmar MVP terminado. Código y documentos locales son la fuente de verdad.
- **Ejecutar comandos en la terminal del usuario**: `hc send` NO sirve para shells (envuelve en `<hc …>`). Usar `hc keys t21 b a s h space a z p . s h enter` con un envoltorio `azp.sh` en la raíz (script con `exec bash <ruta real>`, no symlink) y leer solo logs, NUNCA la pantalla (puede contener claves). Borrar `azp.sh` al terminar.

## 1. Traspaso actual

- Último commit: `630c26e`; 31 commits por delante de `origin/main`, **sin push**.
- **Sin commitear** (ronda OCR alternativo/Azure): `evaluations/ocr/{alt_ocr.py, alt_ocr_v2.py, final_sources.py, final_corrections.py, prepare_alt_v2.py, pyproject.toml, README.md, tests/test_alt_ocr*.py}`, `evaluations/ocr/reports/{alt-ocr-pilot-v1-comparison.*, alt-ocr-v2-audit.*, selective-ocr-v1-audit.*}`, `docs/research/ocr-alternatives.md`. Commits propuestos: `feat(ocr): add Azure OCR v2 pipeline and audits` y `docs: consolidate status`. **No commitear `evaluations/rag/reports/`** (salidas de humo).
- **Caché final y semillas listas** (2026-11-20): `evaluations/ocr/.artifacts/ingestion-cache-v2/` (7 JSON con la clave de `ocr_key()`, `upload-plan.md`) y `.artifacts/corrections-seed-v1/` (128 correcciones validadas con el código real de la ingesta sobre texto preparado: 98 literales, 18 reformuladas en 30 por tablas HTML→Markdown, 4 omitidas por estar en cabeceras que la ingesta elimina; `page_overrides.json` con DT p54 marcado `requires_human_verification`). Registro tras desplegar con `evaluations/ocr/seed_admin.py` (dry-run por defecto; `--apply --override-verified` o `--no-overrides`). `.artifacts/` y `documents/` NO están en git: respaldarlos. Limitación conocida: `workers/ingestion/services/tables.py` descarta `<caption>` (títulos de tabla); M-IP-36 p47/p67 se resolvieron con una línea de título.
- **Siguiente para el usuario**: comprar Workers Paid y desplegar con `docs/DEPLOYMENT.md` (orden ingestion → rag → api → web); precargar caché en R2, subir los 7 PDF por el panel, `seed_admin.py --apply`, reprocesar, revisar, publicar y ejecutar la evaluación.

## 2. Estado por componente

| Componente | Estado | Qué hace | NO verificado |
|---|---|---|---|
| `apps/web` | Implementado | Chat público (Vue 3 + Nuxt UI) con FAQ de ejemplo, texto plano, citas, reintentar; panel `/admin/` (revisión de texto, correcciones y sustituciones); Worker `unitropico-web` (Static Assets + proxy `/api/*`, CSP); mobile first y PWA por app | Dispositivos iOS/Android reales, CSP en navegador, despliegue; probado con Playwright contra API falsa |
| `apps/api` | Implementado | `/health`, `/retrieval/query`, `/chat` (proxy al RAG por service binding), `/admin/*` (Bearer `ADMIN_TOKEN`, D1 + R2 + Workflow), rate limiting | Bindings reales (D1/R2/Workflow, rate limit), service binding real |
| `apps/rag` | Implementado | `/answer` y `/retrieval/query`: híbrido D1 FTS5 + Vectorize (RRF k=60), generación con citas literales validadas y abstención; `langchain-core` 1.8.3 (opción B); `FOLLOWUP_STRATEGY` none/concat/rewrite y `MIN_RELEVANCE` opcional | Vectorize real, gateway real (chat y embeddings) |
| `workers/ingestion` | Implementado | Workflow: mark_processing → ocr (Firecrawl; reutiliza R2) → chunk (correcciones y sustituciones) → embed → index → finalize `review_required`/`ready`; nunca publica | Firecrawl y gateway desde el Worker, pasos chunk/embed/index en workerd, límites de producción |
| `infra/d1` | Implementado | Migraciones, FTS5, máquina de estados por trigger, correcciones/sustituciones, `v_chunk_registry` | Nada creado en remoto |
| `infra/vectorize` | Diseñado | Índice `unitropico-chunks-1536`, cosine | Sin emulación local; no creado |
| `evaluations/ocr` | Activo | Pilotos, auditoría visual de 216 páginas, OCR selectivo Firecrawl, pilotos Azure y plan final de fuentes | Revisión humana de las páginas (ver §5) |
| `evaluations/rag` | Implementado, **sin resultados** | Harness (Recall@k, MRR, nDCG, abstención, citas, latencia); 55 preguntas candidatas SIN validar (37 dev / 18 test) | Dataset y `document_map.json` |
| CI | 7 workflows | `web`, `api`, `rag`, `ingestion`, `evaluations`, `evaluations-rag`, `integration`; sin despliegue ni secrets | Solo api/rag/web corrieron en GitHub (sobre `fa23faf`) |

Ejecutado en workerd local (`pywrangler dev --local`): RAG `/health`, `/retrieval/query` con D1 local, Workflow registrado con fallo `config_missing` persistido, migración D1. Todo lo demás: fakes/sqlite en CPython. `deploy --dry-run` OK en los 4 Workers. Última verificación completa de suites: 2026-11-19/20 en cada proyecto (Ruff + pytest/node:test/unittest verdes; conteos en cada README).

## 3. Decisiones vigentes

### Stack y arquitectura
- Monorepo de proyectos autónomos: sin pyproject/workspace/package.json raíz, sin contratos generados (tipos manuales por app). Sin despliegues automáticos desde CI.
- Python 3.14 con uv (el `python3` del sistema es 3.12; usar el CPython 3.14 de uv), FastAPI, Pydantic 2.14.3, Ruff 0.17.4, pytest 9.2.0. `pywrangler` exige uv ≥ 0.13.4: `uvx --from uv==0.13.7` (0.13.9 en el README de ingesta), aislado, sin actualizar el global. API, RAG e ingesta son Cloudflare Python Workers (`uv.lock` host + `pylock.toml` WASM); flag `python_workers_314`; workers-py 1.17.9 / runtime SDK 1.9.6. La ingesta no usa FastAPI; FastAPI 0.162.2 en API/RAG.
- Settings solo en la composición (`main.py`/`worker.py`); puertos con Protocol; docstrings en todo.
- Web: Vue 3.5.45, Vite 8.3.6, plugin-vue 6.0.10, Nuxt UI 4.12.0 standalone, Tailwind 4.3.5, TS 6.0.3 (typescript-eslint exige <6.1; NO TS 7), vue-tsc 3.3.14, ESLint 10.14.0, pnpm. Sin Router, Pinia, cliente HTTP externo ni framework de tests adicional (node:test). Node 24.15.0 / pnpm 12.18.0.
- LangChain opción B (`docs/research/langchain-evaluation.md`): `langchain-core` 1.8.3 con adaptadores propios; sin langgraph, langchain-openai, classic, community ni text-splitters.

### Decisiones del usuario
- Generador `openai/gpt-6-luna` y embeddings `openai/text-embedding-3-small` (1536 dims) vía Vercel AI Gateway (generador verificado en catálogo; inferencia NO probada).
- Respuestas en texto plano; chat con sidebar de FAQ; auth admin por token secreto en la API; web mobile first e instalable (PWA sin dependencias, SW propio que nunca cachea `/api`); ingesta con Python Worker + Cloudflare Workflow.
- Plan Workers Paid: lo comprará el usuario. Publicación siempre manual.

### Decisiones de datos
- Vectorize: id = `chunks.id` (`{sha16(version_id‖chunker_version)}:{ordinal:04d}:{sha12(text)}`); upsert por el Workflow; publicar/despublicar solo cambia D1 (el RAG revalida siempre); `deleteByIds` al reprocesar. OCR y embeddings persistidos en R2.
- Publicar: `ready`/`unpublished` → `published` con `requires_review=0`; no exige embeddings en todos los chunks. Convocatorias vencidas se recuperan con su vigencia y el modelo debe advertirlo. `supersedes_version_id` 1:1.
- `source_r2_key = raw/{sha256}.pdf` UNIQUE global (409 `source_registered_elsewhere`). En D1, `meta.changes` incluye filas de triggers: no comparar con `== 1`.
- Todo OCR requiere revisión humana antes de publicar (Firecrawl y Azure alteran o inventan contenido).

## 4. Contratos (detalle en el README de la app)

- **/answer (RAG) y /chat (API) v1**: `{question 1..500, history? ≤6}` → `{status answered|abstained, answer, reason, citations[], referral}`; `answered` ⇒ ≥1 cita y `reason` null; `abstained` ⇒ sin respuesta/citas, con `reason` y `referral`; 503 `unavailable`, 502 RAG inválido, 422; metadatos de cita solo desde D1, quote literal ≥12 caracteres. `usage` no es parte de v1 (medir costo exigiría v2). Detalle: `apps/rag/README.md`, `apps/api/README.md`.
- **Admin v1** (`/admin/*`, Bearer `ADMIN_TOKEN`; 503 `admin_disabled`, 401, 503 `storage_unavailable`): uploads (PDF ≤ 25 MB), documentos/versiones, PATCH, publish/unpublish/reprocess (409 `ingestion_in_progress`), chunks. Payload del Workflow `{version_id, document_id, source_r2_key, source_sha256}`. Detalle: `apps/api/README.md`.
- **Correcciones literales**: por versión y página, `find_text` debe aparecer exactamente una vez; se aplican al reprocesar (paso `chunk`) sin repetir OCR; `corrections_dirty` bloquea revisar/publicar (409 `corrections_pending`) hasta reprocesar; tope 100 activas por versión; `Cf` → 422. Detalle: `apps/api/README.md`, `workers/ingestion/README.md`.
- **Sustitución de página**: D1 `page_overrides` (una activa por versión/página, ≤ 20000 caracteres), aplicada en `chunk` antes de las correcciones; `GET /admin/versions/{id}/pages?page=N` y CRUD `page-overrides`; mismo bloqueo `corrections_dirty`. Detalle: `apps/api/README.md`, `workers/ingestion/README.md`.
- **Rate limiting** (API): bindings `CHAT_LIMITER` 20/60 s, `RETRIEVAL_LIMITER` 30/60 s, `ADMIN_AUTH_LIMITER` 10/60 s (solo 401), clave HMAC de IP (`RATE_LIMIT_SALT`), 429 `rate_limited` con `Retry-After: 60`, fail-open salvo `RATE_LIMIT_REQUIRED=true`; cuerpos > 64 KiB rechazados pronto. Binding real NO verificado; confirmar `namespace_id` 1001/1002/1003 libres antes de desplegar. Detalle: `apps/api/README.md`.
- **Pendiente de comprobar**: en workerd real que el puente ASGI no lee el cuerpo cuando se responde antes (rechazo temprano de uploads).

## 5. OCR y corpus

- 7 PDF en `documents/documentos_bienestar/` (ignorado por Git), 216 páginas. Hay contradicciones reales útiles para evaluación: Res. CBU 002-2025 (25 días hábiles vs calendario), Jóvenes en Paz (Res. 00137 vs Documento Técnico), grupos Sisbén (Documento Técnico vs Res. CBU 002-2025). No existen FAQs institucionales verificadas.
- **Plan final de fuentes (216 páginas)**: 110 reutilizables (102 pdftotext + 8 Firecrawl previo) + 34 Firecrawl lotes 01–03 + 7 Azure piloto + 65 Azure v2. **120 correcciones literales obligatorias en 59 páginas** (21 opcionales cosméticas) y **1 sustitución de página** (Documento Técnico p54, diagrama de Insumos; BPIN corregido a 202300000000204). Incluye Res. 00137 p18 ("igual o superior a 3.0") y releer Res. CBU p3 tras sus 17 correcciones. Evidencia: `evaluations/ocr/reports/alt-ocr-v2-audit.{md,json}` (`final_source_plan`), `alt-ocr-pilot-v1-comparison.*`, `selective-ocr-v1-audit.*`, `extraction-quality-audit.*`; rationale de proveedores en `docs/research/ocr-alternatives.md`.
- **Gasto OCR**: Firecrawl 67 créditos consumidos de 106+22 autorizados; no hace falta más (lotes 04–07 sin enviar y descartados). Azure Document Intelligence F0 (`unitropico-ocr`, westus2): 79 páginas de las 500/mes gratuitas.
- **Salvaguardas Firecrawl vigentes** (detalle en `evaluations/ocr/README.md`): NUNCA reenviar `send`, borrar ledger/`execution/` ni regenerar lotes; los scripts `pilot.py`, `remaining_send.py`, `selective_send.py` y `alt_ocr.py send` hacen llamadas de pago y solo los ejecuta el usuario con su clave.
- **Por verificar por humanos**: DT p54 (transcripción del diagrama de Insumos; verificación obligatoria), las páginas con correcciones, comparación final contra los originales antes de publicar, y política de publicación del OCR revisado.

## 6. Pendientes y bloqueos

**Usuario**: Workers Paid y despliegue; crear D1/R2/Vectorize y aplicar migración; ejecutar `seed_admin.py --apply` y las cargas con su clave; revisar OCR y publicar; validar el dataset de evaluación (y congelar split test) y las FAQs; decidir política de publicación del OCR; autorizar gasto de inferencia/embeddings reales; colección local de iconos (hoy Nuxt UI usa api.iconify.design); desbloquear GPG para commits.

**Agentes**: terminar caché final y semillas (§1); commitear la ronda OCR; revisión crítica final de la documentación y ejecución de suites antes de desplegar.

**Siguientes pasos**: (1) cerrar caché final + semillas y commitear; (2) despliegue y recursos (`docs/DEPLOYMENT.md`); (3) precarga en R2, subida de PDFs, semillas, reprocesar; (4) revisión humana y publicación; (5) completar `evaluations/rag/datasets/document_map.json` y evaluar (léxico vs semántico vs híbrido; estructural vs fijo); (6) validación humana del dataset, FAQs verificadas y pruebas reales de gateway/Vectorize.

## 7. Referencias

- `ARQUITECTURA.md` — intención del usuario (no modificar sin su orden).
- `docs/DEPLOYMENT.md` — runbook de despliegue (orden, recursos, secretos, dry-run).
- `docs/research/langchain-evaluation.md` — compatibilidad de LangChain en Workers y opción B.
- `docs/research/python-workflows-bindings.md` — Workflows Python, bindings D1/R2/Vectorize, límites.
- `docs/research/ocr-alternatives.md` — comparativa de OCR y justificación de Azure.
- `apps/{api,rag,web}/README.md`, `workers/ingestion/README.md` — contratos, configuración y pruebas de cada proyecto.
- `infra/README.md`, `infra/d1/queries.md`, `infra/vectorize/README.md` — esquema D1, consultas canónicas y diseño de Vectorize.
- `evaluations/ocr/README.md` y `evaluations/ocr/reports/` — herramientas y evidencia del OCR; `evaluations/rag/README.md` — harness y dataset.
