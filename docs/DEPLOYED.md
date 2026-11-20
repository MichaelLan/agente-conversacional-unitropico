# Despliegue en Cloudflare (estado real)

Desplegado el 2026-11-20 siguiendo `docs/DEPLOYMENT.md`. Cuenta con plan Workers Paid.

URL pública (única): https://unitropico-web.michaellan-eng.workers.dev (`/` chat, `/admin/` panel).

## Recursos
- D1 `unitropico`: `af5162b0-ce3d-4669-97dc-a82dad02c4d8` (ENAM). Migración `0001_initial.sql` aplicada en remoto.
- R2 `unitropico-documents` (Standard, ENAM). Contiene solo la caché OCR v2: 7 objetos `ocr/{sha256}/firecrawl-parse-v2-ocr-v1-p1000.json` (verificados byte a byte con `evaluations/ocr/.artifacts/ingestion-cache-v2/`).
- Vectorize `unitropico-chunks-1536` (1536, cosine, 0 vectores) con índices de metadatos `version_id` y `document_id` (string).
- Workers: `unitropico-ingestion` (Workflow `ingestion`), `unitropico-rag`, `unitropico-api`: sin `workers.dev` ni rutas (404 en sus subdominios). `unitropico-web`: `workers_dev: true`, binding `API`.
- Secrets en `unitropico-api`: `ADMIN_TOKEN`, `RATE_LIMIT_SALT` (generados con CSPRNG). Ningún otro secret configurado.

## ADMIN_TOKEN
Solo en `.secrets/admin_token` (raíz del repo, permisos 600, ignorado por git). Copiarlo a un gestor de contraseñas.

## Smoke tests (2026-11-20, todos OK)
`/` y `/admin/` 200 con CSP; `/api/health` 200; `POST /api/chat` 503 `unavailable` (esperado: sin clave del gateway, no llama a ningún LLM); `/api/admin/documents` 401 sin token y 200 `{"documents":[]}` con token; `/api/retrieval/query` 404.

## Pendiente
1. `AI_GATEWAY_API_KEY` en `unitropico-rag` (respuestas) y `unitropico-ingestion` (embeddings). Sin ella el chat responde 503 y la ingesta no puede indexar.
2. Subir los 7 PDF por `/admin/` (byte a byte los originales para reutilizar la caché OCR y no pagar Firecrawl), revisar y publicar (runbook §7).
3. `evaluations/ocr/seed_admin.py` (correcciones y overrides de página vía API admin), cuando todas las versiones estén en `review_required`; usar `--base-url https://unitropico-web.michaellan-eng.workers.dev/api` (la API solo es alcanzable vía la web). No ejecutado.
4. Cloudflare Access delante de `/admin/*` (hoy solo protege el `ADMIN_TOKEN`).

## Avisos
- Rate limiting: `namespace_id` 1001 (chat, 20/60 s), 1002 (retrieval, 30/60 s) y 1003 (auth admin, 10/60 s). **No se pudo comprobar** que no los use otro proyecto de la cuenta (no hay comando para listarlos); si coinciden, comparten contadores.
- pywrangler y la web desplegaron con wrangler 4.166.0 (pywrangler no fija versión); los comandos de recursos usaron 4.165.0.
- `unitropico-api` reportó un tiempo de arranque de 3 014 ms en el deploy; desplegó y responde, pero vigilar errores de arranque.

## Coste estimado
Cuota fija Workers Paid 5 USD/mes. Con 7 PDF (~216 págs.) y tráfico de tesis, el uso estimado queda dentro de lo incluido (Vectorize ~7 % almacenado y ~11 % consultado; Workers ~0,1 %; D1, R2 y Workflows marginales), así que el coste esperado es 5 USD/mes. El gasto del gateway de IA (Vercel) y Firecrawl es aparte.
Precios: https://developers.cloudflare.com/workers/platform/pricing/ · https://developers.cloudflare.com/d1/platform/pricing/ · https://developers.cloudflare.com/r2/pricing/ · https://developers.cloudflare.com/vectorize/platform/pricing/ · https://developers.cloudflare.com/workflows/reference/pricing/
