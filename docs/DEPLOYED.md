# Despliegue en Cloudflare (estado real)

Desplegado el 2026-11-20 siguiendo `docs/DEPLOYMENT.md`. Cuenta con plan Workers Paid.

URL pública principal: https://unitropico.michaellan.dev (`/` chat, `/admin/` panel), Custom Domain del Worker `unitropico-web` (ruta `custom_domain: true` en `apps/web/wrangler.jsonc`; zona `michaellan.dev` en la misma cuenta; DNS y certificado creados por Cloudflare; verificado 2026-11-20: `/`, `/admin/`, `/api/health`, manifest y `sw.js` 200, `POST /api/chat` sin cuerpo válido 422). URL de respaldo, sigue activa: https://unitropico-web.michaellan-eng.workers.dev.

## Recursos
- D1 `unitropico`: `af5162b0-ce3d-4669-97dc-a82dad02c4d8` (ENAM). Migración `0001_initial.sql` aplicada en remoto.
- R2 `unitropico-documents` (Standard, ENAM). Caché OCR v2: 7 objetos `ocr/{sha256}/firecrawl-parse-v2-ocr-v1-p1000.json` (verificados byte a byte con `evaluations/ocr/.artifacts/ingestion-cache-v2/`), más los 7 PDF subidos (`raw/{sha256}.pdf`) y los artefactos que persiste la ingesta (embeddings).
- Vectorize `unitropico-chunks-1536` (1536, cosine, 436 vectores tras la carga del corpus) con índices de metadatos `version_id` y `document_id` (string).
- Workers: `unitropico-ingestion` (Workflow `ingestion`), `unitropico-rag`, `unitropico-api`: sin `workers.dev` ni rutas (404 en sus subdominios). `unitropico-web`: Custom Domain `unitropico.michaellan.dev` + `workers_dev: true`, binding `API`.
- Secrets en `unitropico-api`: `ADMIN_TOKEN`, `RATE_LIMIT_SALT` (generados con CSPRNG). `AI_GATEWAY_API_KEY` configurado en `unitropico-rag` y `unitropico-ingestion` (embeddings verificados en la carga; respuestas del chat aún sin probar).

## ADMIN_TOKEN
Solo en `.secrets/admin_token` (raíz del repo, permisos 600, ignorado por git). Copiarlo a un gestor de contraseñas.

## Carga del corpus (2026-11-20/21)
Subidos por `POST /api/admin/uploads` los 7 PDF originales (sha256 verificado contra `assembly.json`; los 7 coinciden). Todas las versiones reutilizaron la caché OCR de R2 (`extract skipped reused=1`, `failed_pages=0`): **sin Firecrawl ni OCR de pago**. Después: `seed_admin.py --apply --no-overrides` (128 correcciones; sin sustitución de página) y `reprocess` de las 6 versiones con correcciones. Ninguna versión publicada.

| Documento (título en D1) | `document_type` | `version_label` | document_id | version_id | Estado | Págs. | Chunks | Correcciones aplicadas |
|---|---|---|---|---|---|---|---|---|
| Resolución CBU No. 002 de 2025 – Convocatoria Pública 2025-2 del Apoyo Socioeconómico de Alimentación | convocatoria | 27 oct. 2025 | `a56d1d7bf21f4ea2ac976babc49a6ae3` | `8dfac01610a34addad5afaf9459d48e2` | review_required | 16 | 41 | 42 |
| Boletín de prensa: cómo operará Renta Joven de Prosperidad Social durante 2025 | boletin | 3 feb. 2025 | `e0dc4e3f2a834a5e87e4743f8c03da33` | `3d315c607ec04fe3b7a7f5982c70c4c9` | review_required | 2 | 2 | 0 |
| Documento Técnico de Diseño del Programa Renta Joven (Prosperidad Social) | documento_tecnico | 2024 | `02d92407cfd64b2ca01041e3e8fad9bd` | `0f69c4e667da4d35a77035557e8254f7` | review_required | 62 | 94 | 29 |
| Manual del Programa Renta Joven M-IP-36 (Prosperidad Social) | manual | V1 (dic. 2024) | `7fe4c308212446a8aecc1db0ff74be43` | `dc085efac6fe44938eaacd83ad24ebbd` | review_required | 75 | 131 | 20 |
| Resolución N.º 00137 de 2024 – Reglamento del programa Renta Joven (Prosperidad Social) | resolucion | 25 ene. 2024 | `6d610cf099f24bba91a0dc0efc73b942` | `e5f79e278f284b8f8a76eb414eca8fc6` | review_required | 20 | 68 | 11 |
| Resolución CBU No. 001 de 2026 – Convocatoria Pública 2026-1 del Apoyo Socioeconómico de Alimentación | convocatoria | 4 ago. 2026 | `584716a6e8f740368b8fef325e083dea` | `0df50275ac24471191a67cf4c72c68c7` | review_required | 17 | 42 | 15 |
| Política Integral de Bienestar Universitario (Acuerdo CS No. 044 de 2023) | politica | 31 ago. 2023 | `b06c663f6b2a43cdb2bbf58dbbaad06c` | `5c9e98e9a666463fb4969df71ff5837e` | review_required | 24 | 58 | 11 |

Total: 216 páginas, 436 chunks (idénticos al informe de la caché), 436 vectores en Vectorize, 128 correcciones activas y aplicadas (`corrections_dirty=false` en todas, `correction_not_applicable` 0, `error_code` null). `version_label` = fecha de expedición del documento (o versión/año cuando el documento no da fecha exacta).

Coste observado: la API no expone uso del gateway; consultar el panel de Vercel AI Gateway. Estimado < 0,01 USD (embeddings `openai/text-embedding-3-small` de 436 chunks en la carga inicial + chunks re-embebidos en el reproceso). Sin coste OCR. No se hizo ninguna llamada al LLM generador ni a `/api/chat`.

## Ajustes del RAG (2026-11-21)
Las 7 versiones están **publicadas** (2026-11-21, desde `/admin/`). Redesplegados `unitropico-rag` (versión `4ba4db22-945a-4bde-b072-d58e0a1005df`) y `unitropico-web` (`3ff75947-8a76-4445-8d2b-56a29df8c237`): citas inválidas descartadas individualmente (abstención `invalid_citation` solo si no queda ninguna), comparación de citas tolerante a tildes/comillas/guiones/Markdown, `ANSWER_EVIDENCE_LIMIT=8`, `GENERATION_MAX_INPUT_CHARS=32000`, sinónimos FTS (beca→apoyo/ayuda, inscripción/postulación), `FOLLOWUP_STRATEGY=rewrite`, regla 5 del prompt para preguntas amplias, y la web muestra un mensaje distinto según el motivo de abstención. Prueba en producción: becas generales, seguimiento "como me inscribo?" con historial, requisitos y Bien-Estar Contigo responden con citas; fuera de alcance se abstiene. Coste de las ~15 consultas < 0,02 USD.

## Smoke tests (2026-11-20, todos OK)
`/` y `/admin/` 200 con CSP; `/api/health` 200; `POST /api/chat` 503 `unavailable` (esperado: sin clave del gateway, no llama a ningún LLM); `/api/admin/documents` 401 sin token y 200 `{"documents":[]}` con token; `/api/retrieval/query` 404.

## Pendiente
1. Primera consulta real al chat (gasto en el LLM vía Vercel): pendiente de autorización del usuario; la clave ya está en `unitropico-rag`.
2. Verificación humana de la transcripción de DT p54 (`evaluations/ocr/.artifacts/corrections-seed-v1/page_overrides.json`, ver `evaluations/ocr/README.md`) y, si es correcta, registrar la sustitución con `seed_admin.py --base-url https://unitropico-web.michaellan-eng.workers.dev/api --apply --override-verified --document documento-tecnico` y reprocesar `0f69c4e667da4d35a77035557e8254f7`.
3. Revisión humana en `/admin/` de las 7 versiones (texto final por página, páginas con correcciones, comparación con los originales; releer Res. CBU 002 p3) y publicación manual (runbook §7). Nada publicado.
4. Completar `evaluations/rag/datasets/document_map.json` con los document_id/version_id de la tabla anterior.
5. Cloudflare Access delante de `/admin/*` (hoy solo protege el `ADMIN_TOKEN`).

## Avisos
- Rate limiting: `namespace_id` 1001 (chat, 20/60 s), 1002 (retrieval, 30/60 s) y 1003 (auth admin, 10/60 s). **No se pudo comprobar** que no los use otro proyecto de la cuenta (no hay comando para listarlos); si coinciden, comparten contadores.
- pywrangler y la web desplegaron con wrangler 4.166.0 (pywrangler no fija versión); los comandos de recursos usaron 4.165.0.
- `unitropico-api` reportó un tiempo de arranque de 3 014 ms en el deploy; desplegó y responde, pero vigilar errores de arranque.

## Coste estimado
Cuota fija Workers Paid 5 USD/mes. Con 7 PDF (~216 págs.) y tráfico de tesis, el uso estimado queda dentro de lo incluido (Vectorize ~7 % almacenado y ~11 % consultado; Workers ~0,1 %; D1, R2 y Workflows marginales), así que el coste esperado es 5 USD/mes. El gasto del gateway de IA (Vercel) y Firecrawl es aparte.
Precios: https://developers.cloudflare.com/workers/platform/pricing/ · https://developers.cloudflare.com/d1/platform/pricing/ · https://developers.cloudflare.com/r2/pricing/ · https://developers.cloudflare.com/vectorize/platform/pricing/ · https://developers.cloudflare.com/workflows/reference/pricing/
