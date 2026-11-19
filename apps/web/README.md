# Web · Unitrópico

Chat del **Asistente de Fomento Socioeconómico** de Bienestar Universitario.
Vue 3.5 + Vite 8 + TypeScript 6 + Nuxt UI 4.12 en modo standalone (sin Nuxt).
Envía preguntas a `POST /api/chat` (contrato v1, `docs/IMPLEMENTATION_STATUS.md`
sección 4) y muestra respuestas en texto plano con citas, abstenciones con la
derivación del backend y errores con «Reintentar». No incluyas datos personales.

## Estado

- Hecho: chat en memoria, barra lateral de preguntas frecuentes, estado vacío
  con sugerencias, citas expandibles, abstención, 503/errores con reintento,
  cancelación, accesibilidad básica, responsive (sidebar deslizante en móvil).
- **Las preguntas frecuentes son de ejemplo** (`src/data/faq.ts`,
  `FAQ_IS_PLACEHOLDER = true`): solo preguntas genéricas, sin respuestas ni datos
  institucionales. La interfaz muestra «Preguntas de ejemplo en revisión» hasta
  que Bienestar Universitario valide un listado y la constante pase a `false`.
- Sin persistencia: ni `localStorage`, ni `sessionStorage`, ni cookies
  (`UDashboardGroup` con `:persistent="false"`). Recargar borra la conversación.
- Sin Router, Pinia, cliente HTTP, Markdown, framework de tests ni paquete de iconos.

## Ejecutar con la API local

Node 24.15.0 y pnpm 12.18.0. Desde `apps/web/`:

```sh
pnpm install --frozen-lockfile
pnpm dev          # http://localhost:5173
```

Vite reenvía `/api/*` a `http://127.0.0.1:8787/*` (quita `/api`) **solo en
desarrollo**; levanta la API en ese puerto (ver `apps/api/README.md`). Sin API, el
proxy responde 502 sin cuerpo JSON y la UI muestra «No se pudo completar la
consulta» con «Reintentar». En producción el mismo papel lo cumple el Worker de
la web (ver «Despliegue»).

Controles (los mismos que ejecuta `.github/workflows/web.yml`):

```sh
pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

## Comportamiento

- **Envío único**: prompt, sugerencias del estado vacío y preguntas frecuentes
  pasan por la misma función (`ask` en `App.vue` → `send` de `useChat`).
- **Prompt**: Enter envía, Shift+Enter inserta salto; 1–500 code points tras
  recortar; contador desde 400; mientras se consulta, el botón pasa a «detener»
  (cancela con `AbortController`) y no se aceptan nuevas preguntas.
- **Historial enviado**: solo turnos completados (pregunta + respuesta o
  abstención; los fallos se excluyen), últimos 6 mensajes, cada uno recortado a
  2000 code points. Las abstenciones se envían con un texto fijo, no con la derivación.
- **Respuesta**: texto plano interpolado por Vue con `white-space: pre-line`
  (nunca `v-html`). Citas: título del documento, versión y sección solo si
  existen, páginas («p. 3» / «pp. 13–14») y cita literal expandible.
- **Abstención**: aviso neutral + `referral` del backend.
- **429 `rate_limited`** (limitador de la API para `POST /chat`): muestra el
  `error.message` del servidor (recortado, ≤ 300 caracteres; si falta o no es
  válido, un texto fijo) con «Reintentar». Un 429 con cuerpo no JSON (p. ej. de
  WAF) se trata como error genérico. 411/413 de la API también son error genérico.
- **503/502 `unavailable`, red, timeout (60 s), contrato inválido, cancelación**:
  aviso con «Reintentar», que reenvía la misma pregunta sin duplicarla.
  Los mensajes de error son fijos en español y nunca exponen detalles internos.
- **Validación estricta** de la respuesta: claves exactas, invariantes
  `answered`/`abstained`, `reason` del conjunto controlado, páginas enteras ≥ 1 con
  `page_end >= page_start`. Cualquier desviación se trata como error.

## Estructura

```
src/
  App.vue                     UApp (locale es) + UDashboardGroup, skip link
  components/
    FaqSidebar.vue            UDashboardSidebar: título, colapsar, nueva conversación, FAQs
    ChatPanel.vue             UDashboardPanel: encabezado, mensajes, prompt, aria-live
    ChatEmptyState.vue        saludo, alcance, sugerencias
    AssistantMessage.vue      respuesta / abstención / error / cancelada
    CitationList.vue          fuentes discretas y expandibles (UCollapsible)
  composables/useChat.ts      estado reactivo en memoria: send, retry, abort, reset
  lib/chat-state.ts           lógica pura: historial, transiciones, mapeo, anuncios
  lib/format.ts               páginas y detalles visibles de una cita
  services/http.ts            fetch nativo inyectable, timeout, errores saneados
  services/chat.ts            POST /api/chat y validación del contrato v1
  types/chat.ts               tipos manuales del contrato y de los mensajes
  data/faq.ts                 preguntas de ejemplo (placeholder)
  assets/main.css             Tailwind + Nuxt UI, ajustes de tono para contraste
worker/index.js               Worker de despliegue: proxy /api/* -> service binding API
public/_headers               cabeceras de seguridad de los Static Assets
wrangler.jsonc                Worker unitropico-web (assets dist/, run_worker_first /api/*)
tests/                        node:test; support/load.mjs transpila src/*.ts con `typescript`
```

Tema: claro único; `primary: green`, `neutral: slate` en `vite.config.ts`
(`ui.colors`); `main.css` sube `--ui-primary` al tono 700 y `--ui-text-muted` al
600 para contraste AA. Tipografía del sistema, sin fuentes externas.

## Pruebas

60 pruebas `node:test` (chat, admin y Worker): validación del contrato (válidos, invariantes rotas,
429 rate_limited, 502/503, basura, claves extra), petición enviada, saneado de errores, timeout vs
cancelación, historial acotado y recortado, transiciones
send→answered/abstained/error/cancelada y reintento, una sola consulta en curso,
`reset`, FAQ por el mismo `send`, compilación de todos los SFC, y comprobación
estática de ausencia de `v-html`, `innerHTML`, web storage, `console` y `any`; proxy del Worker (rutas permitidas/bloqueadas, 405, conservación de cabeceras y cuerpo, `CF-Connecting-IP`, 429 + `Retry-After`, 502 saneado) y contenido de `_headers` y `wrangler.jsonc`.

## Limitaciones y pendientes

- **Iconos (pendiente de decisión)**: los `i-lucide-*` se resuelven en runtime
  desde `api.iconify.design`; no hay colección local (`@iconify-json/lucide`) para
  no añadir dependencias. Sin red a ese dominio los iconos no se ven (los botones
  conservan etiqueta o `aria-label`). Opciones: añadir la colección y
  `icon.clientBundle`, o servir SVG propios.
- No verificado: render visual en navegador, lectores de pantalla, contraste
  medido con herramienta, 360 px reales, integración real con la API/RAG.
- Las preguntas frecuentes no están validadas por la institución.
- Despliegue preparado (`wrangler.jsonc`, `worker/`) pero **no ejecutado**.

## Despliegue (Cloudflare Workers + Static Assets, same-origin)

Topología: un único Worker `unitropico-web` (`wrangler.jsonc`) sirve `dist/` como
Static Assets y, en el mismo origen, reenvía `/api/*` al Worker de la API por el
service binding `API`. La API no tiene `workers_dev` ni rutas públicas: solo es
alcanzable a través de este binding. No hay CORS ni orígenes cruzados.

```
navegador ─► unitropico-web ─┬─ /, /admin/, /assets/*  → Static Assets (dist/ + _headers)
                             └─ /api/*  (run_worker_first) → worker/index.js
                                   └─ env.API.fetch(...) → unitropico-api (Python)
```

- `worker/index.js` (JS plano, sin dependencias; tipado con JSDoc y comprobado por
  `tsconfig.worker.json` en `pnpm typecheck`) solo expone:
  `POST /api/chat`, `GET|HEAD /api/health` y `GET|POST|PATCH|DELETE /api/admin/<segmentos
  [A-Za-z0-9_-]>`. Quita `/api` y conserva método, query, cabeceras
  (`Authorization`, `Content-Length`, `CF-Connecting-IP`, que la API usa como clave
  del rate limiting) y cuerpo en streaming. Otras rutas `/api` → 404 (incluidas
  `/api/retrieval/query`, diagnóstico interno que la UI no usa, y `/api/docs`);
  método no previsto → 405 con `Allow`; rutas ambiguas (`//`, `%2F`, `%5C`, barra
  final) → 404; fallo del binding → 502 sin detalles. Rutas sin asset fuera de
  `/api` → 404 del Worker.
- En producción Cloudflare fija `CF-Connecting-IP` en la petición entrante, así que
  el valor reenviado es el del cliente. En `wrangler dev` local no se sobrescribe:
  un cliente puede enviar el suyo (solo afecta a pruebas locales).
- **Cabeceras**: `public/_headers` (Vite lo copia a `dist/`) se aplica a los assets:
  CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`,
  `X-Frame-Options`, COOP, HSTS; `/assets/*` inmutable (nombres con hash);
  `/admin` y `/admin/*` con `X-Robots-Tag: noindex, nofollow` y
  `Cache-Control: no-store`. `_headers` **no** se aplica a respuestas del Worker,
  por eso `worker/index.js` añade a `/api/*` y a sus 404/405/502 `no-store`,
  `nosniff`, `noindex` y una CSP `default-src 'none'`.
- **CSP** (verificada contra `dist/`): sin scripts inline ni `eval` →
  `script-src 'self'`. `style-src 'unsafe-inline'` es necesario porque Nuxt UI/vaul
  inyectan `<style>` en runtime. `connect-src` permite `api.iconify.design` y
  `api2.iconify.design` (iconos lucide en runtime) e `img-src data:` (SVG de
  Iconify como data URI). Al añadir una colección local de iconos, quitar esos
  orígenes. Sin fuentes ni recursos externos más.
- `/admin` y `/admin/index.html` redirigen (307) a `/admin/` (`html_handling:
  auto-trailing-slash`). El panel sigue protegido solo por `ADMIN_TOKEN` en la API;
  Cloudflare Access sobre `/admin/*` y `/api/admin/*` sigue pendiente (ARQUITECTURA).
- El `service` del binding debe coincidir con el `name` real del Worker de la API
  (hoy `unitropico-api` en `apps/api/wrangler.jsonc`); si la API se
  despliega con otro nombre, cambiarlo aquí.

Comandos (documentados, **no ejecutados**; requieren cuenta y desplegar antes la
API). Wrangler no está en `package.json`: se usa con `npx` (el Worker no necesita
build propio ni dependencias, wrangler lo empaqueta).

```sh
pnpm build
npx wrangler@4 deploy --dry-run            # valida config y bundle, sin subir nada
npx wrangler@4 deploy                      # despliega unitropico-web (+ assets)
```

Prueba local sin cuenta (dev registry de wrangler):

```sh
# Terminal 1 (apps/api): la API registra `unitropico-api` localmente
uvx --from uv==0.13.7 uv run pywrangler dev --local --port 8787
# Terminal 2 (apps/web)
pnpm build && npx wrangler@4 dev --local --port 8788
curl -i http://127.0.0.1:8788/api/health    # {"status":"ok"} con cabeceras de seguridad
curl -i http://127.0.0.1:8788/admin/        # HTML + noindex + no-store
```

La CI (`.github/workflows/web.yml`) no despliega (ARQUITECTURA §8); los tests del
proxy y de `_headers` corren con `pnpm test` (`tests/worker-proxy.test.mjs`).

### Política local de scripts de dependencias

`pnpm-workspace.yaml` autoriza solo los postinstall de `esbuild@0.27.7`
(prepara el binario de plataforma) y `vue-demi@0.14.10` (copia adaptadores de
Vue 3), revisados manualmente. No define un workspace ni afecta otras apps.

## Panel de administración (`/admin/`)

App Vite multipágina sin router: `admin/index.html` → `src/admin/main.ts` →
`AdminApp.vue`. El chat público (`/`) **no enlaza** al panel. Dev:
`http://localhost:5173/admin/`; el build genera `dist/admin/index.html`
(`build.rollupOptions.input` en `vite.config.ts`). Producción necesita servir
`/admin/` y el proxy de mismo origen `/api` → API (`/api/admin/*`).

- **Acceso**: pantalla donde el administrador pega su token (`ADMIN_TOKEN` de la
  API). Decisión de seguridad: el token vive **solo en memoria**
  (`useAdminSession`); recargar o «Cerrar sesión» lo borra. Nunca se escribe en
  almacenamiento del navegador, cookies, URL ni logs (hay un test estático). Se envía como
  `Authorization: Bearer` en cada petición. 401 vuelve al acceso con aviso; 503
  `admin_disabled` muestra «El panel no está habilitado en este entorno». La
  protección real es del servidor; la interfaz solo oculta controles.
- **Capa de datos**: `src/admin/services/adminApi.ts` sobre `services/http.ts`
  (`requestJson`: método, headers y cuerpo binario; subida con timeout de 120 s,
  resto 30 s). Valida en runtime las respuestas según los DTO de
  `apps/api/.../domain/admin.py` y muestra el `error.message` del servidor.
- **Listado** (UTable) con estado en UBadge, revisión pendiente, intentos,
  actualización y vigencia; refresco manual y automático cada 10 s solo mientras
  haya versiones `uploaded/processing/extracted` (se detiene al desmontar).
- **Subir**: PDF ≤ 25 MB (el backend no expone el límite; constante
  `MAX_UPLOAD_BYTES`), cuerpo `application/pdf` con `title`, `document_type`,
  `version_label` y `document_id` opcional en query. Distingue 201/200 y
  `ingestion.outcome = failed` (indica usar Reprocesar).
- **Detalle** (USlideover): metadatos, `error_code`/`error_detail`, Workflow y
  eventos cronológicos; desde ahí se edita (fechas AAAA-MM-DD,
  `effective_until ≥ effective_from`, sustitución, «Marcar como revisado»). Los 409
  se muestran con el mensaje del servidor.
- **Acciones** por estado (`lib/versions.ts › allowedActions`): Publicar
  (`ready`/`unpublished` con revisión resuelta), Despublicar (`published`),
  Reprocesar (`uploaded`, `failed`, `review_required`, `ready`, `unpublished`),
  siempre con confirmación y toast.
- Sin dependencias nuevas (lockfile intacto). Tests: `tests/admin-*.test.mjs`.

### Revisión de texto y correcciones OCR

El detalle de versión tiene tres pestañas: **Resumen**, **Texto extraído** y **Correcciones**.

- **Texto extraído**: chunks paginados (`GET /admin/versions/{id}/chunks?offset&limit`, 50 por página) con ordinal, sección y páginas («p. 3», «pp. 13–14»). El texto se muestra con `white-space: pre-line` e interpolación (nunca `v-html`). El filtro es local (sin tildes ni mayúsculas) sobre la página cargada y no hace peticiones.
- **Correcciones**: lista (activas y borradas), alta (`POST .../corrections`) con validación de longitudes (buscar 1–500, reemplazo 0–500, motivo 1–300) y borrado con confirmación (`DELETE .../corrections/{id}`). Regla: el texto a buscar debe aparecer exactamente una vez en la página. Desde un chunk, «Corregir texto de este fragmento» precarga la página (select si abarca varias) y el texto seleccionado. Con la versión `published` crear y borrar quedan deshabilitados (despublicar primero). Tras cada cambio se muestra el `message` del servidor y «Reprocesar ahora» (las correcciones se aplican al reprocesar, reutilizando el OCR en caché).
- Eventos: el paso `correct` se muestra como «Corrección»; el error `correction_not_applicable` se explica en español a partir de `step=chunk correction=<id> page=<n> matches=<k>` (con texto genérico si el detalle no se puede interpretar).
- Código: `src/admin/lib/corrections.ts` (lógica pura), `composables/useVersionReview.ts`, componentes `ChunksList`, `CorrectionsPanel`, `VersionReviewPanel`; servicios y validadores en `services/adminApi.ts`. El Worker proxy permite `DELETE` en `/api/admin/*`.
