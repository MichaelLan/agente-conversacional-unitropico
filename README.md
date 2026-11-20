# Agente conversacional Unitrópico

Proyecto de tesis de la Maestría en Inteligencia Artificial de la Universidad de
La Salle: asistente que orienta sobre Fomento Socioeconómico de Bienestar
Universitario de Unitrópico, respondiendo solo con evidencia institucional citada
y absteniéndose (con derivación a Bienestar) cuando no la hay.
**En desarrollo: no está desplegado ni es un asistente operativo.**

El estado vigente, los conteos de tests, decisiones, contratos y pendientes viven
en [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md); prevalece sobre
este README.

## Mapa del monorepo

Proyectos autónomos: cada uno tiene su manifiesto y lockfile; no hay workspace,
`pyproject.toml` ni `package.json` raíz, ni contratos generados.

| Proyecto | Estado |
| --- | --- |
| [`apps/web`](apps/web/README.md) | Chat público Vue 3 + Nuxt UI (FAQ de ejemplo, citas, PWA) y panel `/admin/`; implementado, probado con API falsa |
| [`apps/api`](apps/api/README.md) | FastAPI en Python Worker: `/chat`, `/retrieval/query`, `/admin/*` y rate limiting; implementado, bindings reales no verificados |
| [`apps/rag`](apps/rag/README.md) | FastAPI en Python Worker: recuperación híbrida FTS5 + Vectorize, respuesta con citas validadas y abstención; implementado, Vectorize y gateway reales no verificados |
| [`workers/ingestion`](workers/ingestion/README.md) | Python Worker + Workflow (OCR, chunking, embeddings, indexado; nunca publica); implementado con fakes y workerd local parcial |
| [`infra`](infra/README.md) | Esquema D1 (migraciones, FTS5, estados) verificado con SQLite/D1 local; Vectorize solo diseñado; nada creado en remoto |
| [`evaluations/ocr`](evaluations/ocr/README.md) | Herramientas y evidencia del OCR (Firecrawl y Azure); corpus bloqueado para publicación hasta revisión humana |
| [`evaluations/rag`](evaluations/rag/README.md) | Harness de evaluación implementado; sin resultados (dataset borrador sin validar) |
| [`tests/integration`](tests/integration/README.md) | Pruebas API→RAG en CPython (ASGI en memoria, D1 como SQLite) |

## Ejecutar localmente

Requisitos: Python 3.14 con uv; Node 24.15.0 y pnpm 12.18.0 para la web.
`pywrangler` exige uv ≥ 0.13.4, por eso se usa `uvx --from uv==0.13.7 uv …`
(el README de ingesta usa 0.13.9 para `pywrangler dev`). Los comandos de calidad no
requieren credenciales ni servicios externos; no guardes secretos en archivos
versionados.

```sh
# apps/api, apps/rag, workers/ingestion, evaluations/rag, tests/integration (desde su carpeta)
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
# evaluations/rag además: uv run python -m unitropico_eval validate
```

```sh
# evaluations/ocr (mismo bloque de calidad)
cd evaluations/ocr && uv sync --locked --group dev && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```
**No ejecutes** scripts de envío de OCR (`pilot.py`, `remaining_send.py`,
`selective_send.py`, `alt_ocr.py send`): hacen llamadas de pago.

```sh
# apps/web
cd apps/web && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build
pnpm dev   # http://localhost:5173; /api/* se reenvía a 127.0.0.1:8787 solo en desarrollo
```

```sh
# infra (desde la raíz): esquema D1 con SQLite real
uv run --no-project --python 3.14 python -m unittest discover -s infra/d1/tests -v
uvx ruff check infra && uvx ruff format --check infra
```

Servidores locales (sin login ni recursos remotos; `pywrangler` invoca `npx`):

```sh
# API (puerto 8787), desde apps/api
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.7 uv run pywrangler dev --local --ip 127.0.0.1 --port 8787
# RAG (puerto 8790), desde apps/rag: migración D1 local y servidor
CI=1 uvx --from uv==0.13.7 uv run pywrangler d1 migrations apply DB --local
uvx --from uv==0.13.7 uv run pywrangler dev --local --port 8790
```

Cada README de proyecto detalla contratos, configuración y límites.

## CI

Siete workflows en [`.github/workflows/`](.github/workflows/), todos con `push`,
`pull_request` filtrados por rutas y `workflow_dispatch`. Ninguno despliega ni usa
secrets.

| Workflow | Componente |
| --- | --- |
| `web.yml` | `apps/web` |
| `api.yml` | `apps/api` (también `infra/d1/**`) |
| `rag.yml` | `apps/rag` (también `infra/d1/**`) |
| `ingestion.yml` | `workers/ingestion` (también `infra/d1/**`) |
| `evaluations.yml` | `evaluations/ocr` (también `workers/ingestion/src/**`) |
| `evaluations-rag.yml` | `evaluations/rag` |
| `integration.yml` | `tests/integration` (también `apps/{api,rag}/src/**`, `infra/d1/**`) |

Solo se ha registrado ejecución en GitHub de `api`, `rag` y `web` (según el estado
de implementación); los demás no se han ejecutado allí. No hay pywrangler/workerd
en CI. No marques estos workflows filtrados como checks obligatorios sin un gate
agregador que se ejecute siempre.

## No verificado

Despliegue real y recursos de Cloudflare (D1/R2/Workflows/Vectorize remotos),
Vectorize real, gateway de IA real (inferencia y embeddings), service binding y
rate limit reales, Firecrawl desde el Worker de ingesta, dispositivos iOS/Android
reales y CSP en navegador. No hay evaluación con resultados ni FAQs
institucionales verificadas. El corpus OCR (`documents/` y
`evaluations/ocr/.artifacts/`, ignorados por Git) requiere revisión humana antes
de publicarse.

## Documentación

| Documento | Contenido |
| --- | --- |
| [`ARQUITECTURA.md`](ARQUITECTURA.md) | Fuente de intención |
| [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) | Estado actual, traspaso, decisiones, contratos y pendientes |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Runbook de despliegue |
| [`docs/research/langchain-evaluation.md`](docs/research/langchain-evaluation.md) | LangChain en Workers |
| [`docs/research/python-workflows-bindings.md`](docs/research/python-workflows-bindings.md) | Python Workflows y bindings |
| [`docs/research/ocr-alternatives.md`](docs/research/ocr-alternatives.md) | Alternativas OCR |
| [`evaluations/ocr/reports/`](evaluations/ocr/reports/) | Evidencia del OCR |
| [`infra/d1/queries.md`](infra/d1/queries.md), [`infra/vectorize/README.md`](infra/vectorize/README.md) | Consultas canónicas D1 y diseño de Vectorize |
