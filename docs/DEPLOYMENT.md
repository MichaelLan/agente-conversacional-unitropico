# Runbook de despliegue (Cloudflare)

Estado: **preparado, no ejecutado**. Nada de lo que sigue se ha corrido contra una cuenta. Redactado el 2026-11-19. Prevalece `docs/IMPLEMENTATION_STATUS.md` si discrepan.

Política: nada se crea en remoto sin autorización explícita del usuario (`infra/README.md`). Cada comando con `--remote`, `create`, `secret put` o `deploy` de este documento es **remoto**.

Convenciones: todos los comandos se ejecutan desde la raíz del repo salvo que se indique `cd`. Wrangler se invoca con `npx wrangler@4.165.0` (la versión usada y verificada en el repo; ver "Prerrequisitos").

Referencias oficiales (consultar antes de ejecutar; la sintaxis de abajo se contrastó con `wrangler <cmd> --help` de 4.165.0, no con las páginas en línea):
- Comandos de Wrangler: https://developers.cloudflare.com/workers/wrangler/commands/
- D1: https://developers.cloudflare.com/d1/wrangler-commands/ y migraciones https://developers.cloudflare.com/d1/reference/migrations/
- R2: https://developers.cloudflare.com/r2/reference/wrangler-commands/
- Vectorize: https://developers.cloudflare.com/vectorize/reference/wrangler-commands/ y https://developers.cloudflare.com/vectorize/reference/metadata-filtering/
- Secrets: https://developers.cloudflare.com/workers/configuration/secrets/
- Rate Limiting: https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/
- Workflows (precios y límites): https://developers.cloudflare.com/workflows/reference/limits/
- Rollbacks: https://developers.cloudflare.com/workers/configuration/versions-and-deployments/rollbacks/
- Python Workers: https://developers.cloudflare.com/workers/languages/python/

## 0. Verificación previa sin cuenta (hecha el 2026-11-19)

`deploy --dry-run` de los cuatro Workers, sin login ni red de pago (solo se escribió en `dist/`, `.wrangler/`, `python_modules/`, ignorados):

| Worker | Comando | Resultado | Tamaño |
| --- | --- | --- | --- |
| `unitropico-web` | `pnpm build` y `npx --no-install wrangler deploy --dry-run` (4.165.0) | OK. 35 archivos de assets | Worker 2,60 KiB (gzip 1,12 KiB). Binding `API` -> `unitropico-api` |
| `unitropico-api` | `uvx --from uv==0.13.9 uv run pywrangler deploy --dry-run` | OK. 451 módulos | 9 592 KiB (gzip 2 400 KiB) |
| `unitropico-rag` | idem | OK. 1 513 módulos | 21 818 KiB (gzip 5 229 KiB); límite 64 MiB sin comprimir |
| `unitropico-ingestion` | idem | OK. 52 módulos | 224 KiB (gzip 62 KiB) |

Los dry-run listan los bindings esperados (API: INGESTION, DB, DOCUMENTS, RAG, CHAT_LIMITER, RETRIEVAL_LIMITER; RAG: DB, VECTORIZE y vars; ingesta: INGESTION, DB, VECTORIZE, DOCUMENTS y vars). Solo se revisó el final de la salida; no se vio ninguna advertencia en esa parte.

Un dry-run **no** valida que existan los recursos remotos (ids, bucket, índice) ni los secrets.

## 1. Prerrequisitos

- [ ] Cuenta de Cloudflare y plan **Workers Paid** (necesario para Workflows en la práctica: límites Free de 10 ms de CPU y 50 subrequests por instancia; ver `docs/research/python-workflows-bindings.md` §1).
- [ ] Node >= 22 (el repo usa 24.15.0) y pnpm 12.18.0 para la web.
- [ ] `uv` aislado: `uvx --from uv==0.13.9 uv ...` (pywrangler exige uv >= 0.13.4; el uv global del equipo es 0.13.0).
- [ ] Wrangler 4.165.0. Ojo: `pywrangler` invoca `npx --yes wrangler` **sin fijar versión** (README de `apps/api`), así que resolverá la última 4.x. Para los pasos no-Python usa `npx wrangler@4.165.0`; si quieres reproducibilidad total, comprueba qué versión usó pywrangler (`npx wrangler --version`) antes de desplegar.
- [ ] Dependencias instaladas en cada worker Python: `uvx --from uv==0.13.9 uv sync --locked --group dev`.
- [ ] Controles de calidad en verde (ver README raíz) antes de desplegar.
- [ ] Login (abre el navegador): `npx wrangler@4.165.0 login`; comprobar con `npx wrangler@4.165.0 whoami`.
- [ ] Claves a la mano, **fuera del repo**: `AI_GATEWAY_API_KEY` (Vercel AI Gateway), `FIRECRAWL_API_KEY` (solo si falta caché OCR), y un `ADMIN_TOKEN` largo y aleatorio (p. ej. `openssl rand -base64 48`).

## 2. Crear recursos (una sola vez)

### 2.1 D1

```sh
npx wrangler@4.165.0 d1 create unitropico
```

Imprime el `database_id` (UUID). Opciones útiles: `--location` (weur, eeur, apac, oc, wnam, enam) o `--jurisdiction` (eu, fedramp, us); no se pueden cambiar después.

- [ ] Pegar el `database_id` en los **tres** `wrangler.jsonc` que declaran `DB`:
  - `apps/api/wrangler.jsonc` (hoy `REPLACE_WITH_D1_DATABASE_ID`)
  - `apps/rag/wrangler.jsonc` (hoy ceros)
  - `workers/ingestion/wrangler.jsonc` (hoy ceros)
- [ ] Los tres deben tener el **mismo** id y `database_name: "unitropico"`. Cambios locales sin commitear o commit aparte: este paso lo hace el operador, no el CI.

### 2.2 R2

```sh
npx wrangler@4.165.0 r2 bucket create unitropico-documents
```

Lo usan `apps/api` y `workers/ingestion` (binding `DOCUMENTS`).

### 2.3 Vectorize (índices de metadatos ANTES de insertar)

```sh
npx wrangler@4.165.0 vectorize create unitropico-chunks-1536 --dimensions=1536 --metric=cosine \
  --description="Chunks Bienestar Unitropico, openai/text-embedding-3-small"
npx wrangler@4.165.0 vectorize create-metadata-index unitropico-chunks-1536 --propertyName=version_id --type=string
npx wrangler@4.165.0 vectorize create-metadata-index unitropico-chunks-1536 --propertyName=document_id --type=string
npx wrangler@4.165.0 vectorize list-metadata-index unitropico-chunks-1536
npx wrangler@4.165.0 vectorize info unitropico-chunks-1536
```

- [ ] `list-metadata-index` muestra `version_id` y `document_id` antes de subir ningún PDF (los vectores insertados antes de crear el índice de metadatos no aparecen en él).
- [ ] Dimensión 1536 coincide con `EMBEDDING_DIMENSIONS` de rag e ingesta. La dimensión no se puede cambiar después.

### 2.4 Rate limiting (`namespace_id` 1001 y 1002)

`apps/api/wrangler.jsonc` usa `CHAT_LIMITER` (1001, 10/60 s) y `RETRIEVAL_LIMITER` (1002, 30/60 s). El id debe ser **único en la cuenta**: dos bindings con el mismo id comparten contadores.

- [ ] Si la cuenta es nueva y no tiene otros Workers con `ratelimits`, no chocan. Si tiene otros, revisa sus `wrangler.jsonc` (no hay comando de Wrangler para listar namespaces; no verificado que exista otro modo). Si hay colisión, cambia los ids en `apps/api/wrangler.jsonc`.

## 3. Migraciones D1 remotas

La migración se aplica **desde un solo worker** (`infra/README.md`). Se usa `apps/api` porque su config declara `DB` con `migrations_dir: ../../infra/d1/migrations`.

```sh
cd apps/api
npx wrangler@4.165.0 d1 migrations list unitropico --remote
npx wrangler@4.165.0 d1 migrations apply unitropico --remote
npx wrangler@4.165.0 d1 execute unitropico --remote --command "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
```

- [ ] `0001_initial.sql` aplicada (pide confirmación).
- [ ] La consulta lista `documents`, `document_versions`, `chunks`, `chunks_fts`, `processing_events`, `text_corrections`, `v_chunk_registry`.
- Wrangler resuelve `unitropico` por `database_name` del `wrangler.jsonc` del directorio actual; por eso se ejecuta desde `apps/api` con el `database_id` ya sustituido.

## 4. Secrets (nunca en archivos ni en el historial del shell)

`wrangler secret put` pide el valor por stdin interactivo. Si el Worker aún no existe, Wrangler ofrece crearlo; es más limpio ponerlos **después del primer deploy** de cada worker (sección 5) o crear el worker y repetir. Se ejecuta desde la carpeta de cada worker (usa el `name` de su config) o con `--name`.

| Worker | Secret | Obligatorio |
| --- | --- | --- |
| `unitropico-rag` | `AI_GATEWAY_API_KEY` | sí (sin él `/answer` da 503) |
| `unitropico-ingestion` | `AI_GATEWAY_API_KEY` | sí (embeddings) |
| `unitropico-ingestion` | `FIRECRAWL_API_KEY` | solo si algún PDF no tiene caché OCR en R2 |
| `unitropico-api` | `ADMIN_TOKEN` | sí (sin él `/admin/*` da 503) |
| `unitropico-api` | `RATE_LIMIT_SALT` | opcional (valor por defecto público en el repo; recomendable cambiarlo) |

```sh
(cd apps/rag && npx wrangler@4.165.0 secret put AI_GATEWAY_API_KEY)
(cd workers/ingestion && npx wrangler@4.165.0 secret put AI_GATEWAY_API_KEY)
(cd workers/ingestion && npx wrangler@4.165.0 secret put FIRECRAWL_API_KEY)   # omitir si no hará falta OCR de pago
(cd apps/api && npx wrangler@4.165.0 secret put ADMIN_TOKEN)
(cd apps/api && npx wrangler@4.165.0 secret put RATE_LIMIT_SALT)              # opcional
```

- [ ] `npx wrangler@4.165.0 secret list --name <worker>` muestra los nombres esperados (no los valores).
- [ ] Guardar `ADMIN_TOKEN` en un gestor de contraseñas; el panel lo pide al entrar y solo lo guarda en memoria.

## 5. Orden de despliegue (por dependencias de bindings)

Un service binding o un `script_name` de Workflow exige que el Worker destino exista. Orden:

1. `unitropico-ingestion` (necesita D1, R2, Vectorize ya creados)
2. `unitropico-rag` (D1, Vectorize)
3. `unitropico-api` (service binding `RAG`, Workflow `INGESTION` -> `unitropico-ingestion`, D1, R2)
4. `unitropico-web` (service binding `API`)

```sh
# 1. ingestion
cd workers/ingestion && uvx --from uv==0.13.9 uv run pywrangler deploy
# 2. rag
cd ../../apps/rag && uvx --from uv==0.13.9 uv run pywrangler deploy
# 3. api
cd ../api && uvx --from uv==0.13.9 uv run pywrangler deploy
# 4. web
cd ../web && pnpm install --frozen-lockfile && pnpm build && npx wrangler@4.165.0 deploy
```

- [ ] Cada `--dry-run` previo pasa (ya verificado el 2026-11-19; repetir tras pegar `database_id`).
- [ ] Si los secrets aún no están puestos, hacerlos ahora (sección 4) y no hace falta redesplegar: los secrets aplican a una nueva versión automáticamente.
- [ ] La URL pública queda en `https://unitropico-web.<subdominio>.workers.dev` (solo la web tiene `workers_dev: true`).
- Nota: los Python Workers y Workflows están en beta; el primer deploy puede tardar y fallar en arranque (límite de 1 s de inicio). Si falla, revisa el mensaje y no reintentes a ciegas.

## 6. Precarga de la caché OCR en R2 (ANTES de subir PDF)

Objetivo: que el Workflow encuentre el OCR ya hecho en `ocr/{sha256}/firecrawl-parse-v2-ocr-v1-p1000.json` y **no pague Firecrawl otra vez** (`OCR_VERSION` y `OCR_MAX_PAGES` forman parte de la clave).

- [ ] Ejecutar (offline) `evaluations/ocr/assemble_cache.py` para generar `evaluations/ocr/.artifacts/ingestion-cache-v1/` y su `upload-plan.md`. **Hoy ese directorio no existe**: depende de que el usuario ejecute antes el OCR selectivo de 106 créditos (`selective_send.py send --confirm-credits 106 --env-file .env`) y de la auditoría de las páginas nuevas. Ver `evaluations/ocr/README.md`.
- [ ] Revisar `upload-plan.md` y ejecutar los comandos que lista (no se reproducen aquí para no divergir). Su forma esperada, con `--remote` explícito porque `r2 object put` acepta `--local` y `--remote` y no se verificó cuál es el valor por defecto:

```sh
npx wrangler@4.165.0 r2 object put "unitropico-documents/ocr/<sha256>/firecrawl-parse-v2-ocr-v1-p1000.json" \
  --file=<ruta>.json --content-type=application/json --remote
```

- [ ] Verificar cada objeto: `npx wrangler@4.165.0 r2 object get "unitropico-documents/ocr/<sha256>/..." --remote --file=/dev/null` (o desde el Dashboard).
- [ ] Las claves usan el sha256 del PDF original: el PDF que se suba por el panel debe ser byte a byte el mismo que se usó al ensamblar.
- [ ] Alternativa si no se precarga: configurar `FIRECRAWL_API_KEY` y aceptar el costo (OCR completo de los documentos).

## 7. Carga y revisión de documentos (panel admin)

1. [ ] Abrir `https://<web>/admin/` y entrar con `ADMIN_TOKEN`.
2. [ ] Subir los **7 PDF** (216 páginas) con título, tipo (`resolucion`, `reglamento`, `convocatoria`, `documento_tecnico`, ...) y `version_label`. Máximo 25 MB por PDF (`ADMIN_MAX_UPLOAD_BYTES`). El mismo PDF no puede registrarse en dos documentos (409 `source_registered_elsewhere`).
3. [ ] Esperar a que cada versión pase por `uploaded -> processing -> review_required` (el Workflow nunca publica). Si falla, ver detalle y `processing_events`; `reprocess` la recupera.
4. [ ] Revisar el texto extraído página a página. Corrección conocida: Res. 00137 p. 18 dice "igual a 3.0" y debe decir "igual o superior a 3.0": añadir corrección literal (la versión pasa a reproceso necesario).
5. [ ] Documentos con páginas faltantes (Convocatoria 2026-1, 15 páginas) no se publican hasta tener su OCR.
6. [ ] Rellenar vigencia (`effective_from`, `effective_until`) y sustitución donde corresponda.
7. [ ] Publicar solo las versiones revisadas. Las FAQ de la web siguen siendo de ejemplo (`FAQ_IS_PLACEHOLDER=true`); no hay FAQs institucionales validadas.

## 8. Smoke tests

```sh
WEB=https://unitropico-web.<subdominio>.workers.dev

curl -i  $WEB/api/health                                   # 200 {"status":"ok"}
curl -i  $WEB/api/admin/documents                          # 401 sin token
curl -i  -H "Authorization: Bearer $ADMIN_TOKEN" $WEB/api/admin/documents   # 200 (no pegues el token en historial compartido)
curl -sS -X POST $WEB/api/chat -H 'content-type: application/json' \
  -d '{"question":"¿Qué requisitos exige la convocatoria de apoyos socioeconómicos?"}'
curl -i  $WEB/api/retrieval/query                          # 404: la web no expone esa ruta
```

- [ ] `/api/health` 200.
- [ ] `/api/admin/*` sin token: 401 (con `ADMIN_TOKEN` sin configurar: 503).
- [ ] `/api/chat` devuelve `status: answered` con `citations` (si ninguna versión está publicada, esperable `abstained` con `reason: no_evidence`).
- [ ] 11 peticiones seguidas a `/api/chat` en 60 s producen 429 `rate_limited` (cuota 10/60 s).
- [ ] Probar el chat desde el navegador y en móvil (la PWA no se ha verificado en dispositivos reales).

## 9. Logs, observabilidad y rollback

```sh
npx wrangler@4.165.0 tail unitropico-rag --format pretty
npx wrangler@4.165.0 tail unitropico-api --status error
npx wrangler@4.165.0 tail unitropico-ingestion
```

- `RAG_LOG_LEVEL=INFO` (var) activa `answer_metrics` (tokens y latencias por fase); por defecto `WARNING`. Los logs nunca contienen preguntas ni texto de evidencia.
- [ ] Estado de un Workflow: Dashboard > Workers > Workflows > `ingestion`, o `npx wrangler@4.165.0 workflows instances list ingestion` (no verificado en este repo).

Rollback de código (por worker):

```sh
npx wrangler@4.165.0 versions list --name unitropico-api
npx wrangler@4.165.0 rollback <version-id> --name unitropico-api -m "motivo"
```

- El rollback **no** revierte D1, R2, Vectorize ni secrets. Las migraciones D1 son solo hacia adelante: no apliques migraciones destructivas sin copia (`wrangler d1 export unitropico --remote --output=backup.sql`).
- Para retirar contenido sin tocar código: despublicar la versión en el panel (solo cambia D1; el RAG revalida en cada consulta).

## 10. Costos a vigilar

- **Firecrawl**: solo se paga si no hay caché OCR en R2. Con la caché de la sección 6 precargada y los PDF idénticos, el Workflow reutiliza el JSON y no llama a Firecrawl. Cambiar `OCR_VERSION` o `OCR_MAX_PAGES` invalida la caché y fuerza un OCR de pago. El OCR selectivo ya autorizado es de 106 créditos.
- **Gateway de IA (Vercel)**: una llamada de generación (`openai/gpt-6-luna`) y una de embeddings por cada consulta del chat. La cuota 10/60 s por cliente limita el abuso, pero revisa el gasto en el panel de Vercel.
- **Embeddings al indexar**: se pagan una vez por lote y se guardan en R2 (`embeddings/...`); reprocesar con el mismo chunker no los repite. Cambiar el chunker cambia los ids y recalcula.
- **Cloudflare**: Workers Paid (cuota base), solicitudes, D1 lecturas/escrituras, R2 almacenamiento, Vectorize dimensiones consultadas, Workflows. El corpus (7 PDF) es muy pequeño; no se estimó el costo total.
- **No pagar el OCR dos veces**: (1) no borrar `evaluations/ocr/.artifacts/`; (2) precargar la caché antes de subir; (3) no subir versiones modificadas de los PDF (otro sha256 = otra clave = OCR nuevo); (4) no reprocesar sin necesidad.

## 11. Incoherencias detectadas entre configs (no corregidas)

- `database_id`: `apps/api` usa el texto `REPLACE_WITH_D1_DATABASE_ID`; `apps/rag` e ingesta, ceros. No son iguales ni el de api es un UUID válido (el dry-run lo acepta). Sustituir los tres por el mismo id.
- `EMBEDDING_DIMENSIONS`: número (`1536`) en `apps/rag` y cadena (`"1536"`) en ingesta. Coherentes en valor; solo estilo.
- Nombres de binding/recursos coherentes: `DB` -> `unitropico`, `DOCUMENTS` -> `unitropico-documents`, `VECTORIZE` -> `unitropico-chunks-1536`, `API` -> `unitropico-api`, `RAG` -> `unitropico-rag`, Workflow `ingestion`/`IngestionWorkflow`/`unitropico-ingestion`.
- Versión de uv: README de ingesta y de investigación usan 0.13.9; README raíz y de api/rag, 0.13.7. Este runbook usa 0.13.9 (la usada en el dry-run).
- Wrangler no está fijado en ningún manifiesto; pywrangler usa la última 4.x.
- `infra/vectorize/README.md` usa `--propertyName`; la página de metadata filtering usa `--property-name`. Aquí se usa `--propertyName` (el de `--help` 4.165.0).
- `evaluations/ocr/.artifacts/ingestion-cache-v1/` y `upload-plan.md` no existen todavía.

## 12. No verificado

- Ningún comando remoto se ha ejecutado: login, creación de D1/R2/Vectorize, migraciones remotas, secrets, deploy, rollback y `tail`.
- La sintaxis se contrastó con `wrangler --help` 4.165.0 local, no con una lectura de las páginas oficiales actuales (las URLs de arriba son las referencias a confirmar).
- Vectorize real (forma del retorno de `query`, filtros), gateway real (chat y embeddings), Firecrawl desde el Worker, transporte API -> RAG por service binding, bindings reales de D1/R2/Workflow desde Python, límites reales de producción (1 MiB por paso de Workflow, CPU, memoria, arranque de 1 s), tope de sentencias D1 por `batch`.
- Que `namespace_id` 1001/1002 estén libres en la cuenta.
- Que `r2 object put` por defecto escriba en remoto: por eso se pasa `--remote`.
- Que pywrangler deploy ejecute migraciones o cree recursos (no lo hace según el dry-run; no se probó un deploy real).
- Comportamiento de `wrangler secret put` sobre un Worker inexistente y de `workflows instances list`.
- Cloudflare Access sobre `/admin/*` sigue pendiente: el panel está protegido solo por `ADMIN_TOKEN`.
- PWA y render en dispositivos reales; evaluación RAG sin resultados (dataset de 55 preguntas sin validar).
