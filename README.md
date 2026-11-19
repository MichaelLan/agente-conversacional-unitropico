# Agente conversacional Unitrópico

Proyecto de tesis de la Maestría en Inteligencia Artificial de la Universidad de
La Salle: asistente que orienta sobre Fomento Socioeconómico de Bienestar
Universitario de Unitrópico, respondiendo solo con evidencia institucional citada
y absteniéndose (con derivación a Bienestar) cuando no la hay.
**En desarrollo: no está desplegado ni es un asistente operativo.**

- [Arquitectura](ARQUITECTURA.md): fuente de intención.
- [Estado de implementación](docs/IMPLEMENTATION_STATUS.md): estado operativo, contratos v1 y decisiones. Prevalece sobre cualquier "estado observado" de este README, de `PLAN_MVP.md` o de `INVESTIGACION.md`.
- [Plan incremental](PLAN_MVP.md) e [investigación y evidencia técnica](INVESTIGACION.md).
- [Investigaciones](docs/research/): [LangChain en Workers](docs/research/langchain-evaluation.md) y [Python Workflows y bindings](docs/research/python-workflows-bindings.md).

## Mapa del monorepo

Proyectos autónomos: cada uno tiene su manifiesto y lockfile; no hay workspace,
`pyproject.toml` ni `package.json` raíz, ni contratos generados (tipos manuales por app).

| Proyecto | Qué es | Estado real |
| --- | --- | --- |
| [`apps/web`](apps/web/README.md) | Vue 3 + Vite + Nuxt UI (standalone): chat en texto plano con citas, FAQ de ejemplo y panel `/admin/` | Implementado contra el contrato v1 con tests node y build locales. FAQ **de ejemplo** (no validadas). Render en navegador e integración real con la API **no verificados** |
| [`apps/api`](apps/api/README.md) | FastAPI en Python Worker: `/health`, `/retrieval/query`, `/chat` (proxy al RAG por service binding) y `/admin/*` con Bearer `ADMIN_TOKEN` | Implementado con tests; sin rate limit. Bindings reales (D1, R2, Workflow) y transporte por service binding **no verificados** |
| [`apps/rag`](apps/rag/README.md) | FastAPI en Python Worker: `/retrieval/query` y `/answer` (recuperación híbrida FTS5 + Vectorize con RRF, generación con citas validadas y abstención) | Implementado con tests; en `pywrangler dev --local` con D1 local devuelve evidencia real. **Vectorize real y gateway real no verificados** |
| [`workers/ingestion`](workers/ingestion/README.md) | Python Worker + Cloudflare Workflow: OCR (Firecrawl), chunking estructural, embeddings, indexado; nunca publica | Implementado; probado con fakes en CPython y parcialmente en workerd local. Firecrawl, gateway y Vectorize reales **no verificados** |
| [`infra`](infra/README.md) | Esquema D1 (migración, FTS5, máquina de estados), consultas canónicas y diseño de Vectorize | Esquema verificado con SQLite y D1 local. **Nada creado en remoto** |
| [`evaluations/ocr`](evaluations/ocr/README.md) | Herramientas y evidencia del OCR con Firecrawl Parse | Hubo envíos autorizados de pago; corpus v2 parcial y **bloqueado para publicación** (ver su README) |
| [`evaluations/rag`](evaluations/rag/README.md) | Harness de evaluación de recuperación y respuesta, con dataset borrador | **Sin resultados de evaluación**: dataset de 55 ítems sin validar |

Los conteos de tests y el detalle por componente están en
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md); no se repiten aquí.

### No verificado (explícito)

Vectorize real, gateway de IA real (inferencia y embeddings), D1/R2/Workflows
remotos, transporte API→RAG por service binding, llamadas reales a Firecrawl desde
el Worker de ingesta, render visual en navegador, y cualquier despliegue. No hay
recursos de Cloudflare creados ni evaluación con resultados. El corpus OCR
(`documents/` y `evaluations/ocr/.artifacts/`, ignorados por Git) está bloqueado
para publicación y le faltan 15 páginas de la Convocatoria 2026-1; no existen
FAQs institucionales verificadas.

## Ejecutar localmente

Requisitos: Python 3.14 con uv; Node 24.15.0 y pnpm 12.18.0 para la web. El uv
global del entorno es 0.13.0 y `pywrangler` exige uv ≥ 0.13.4: usar
`uvx --from uv==0.13.7 uv …` (el README de ingesta usa 0.13.9). Los comandos de
calidad no requieren credenciales ni servicios externos; no guardes secretos en
archivos versionados. Cada README de proyecto detalla contratos y límites.

```sh
# apps/api, apps/rag, workers/ingestion, evaluations/rag (desde su carpeta)
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

```sh
# evaluations/ocr (mismo bloque; pytest descubre tests unittest)
cd evaluations/ocr && uv sync --locked --group dev && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```
**No ejecutes** `pilot.py send` ni `remaining_send.py`: hacen llamadas de pago.

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

Servidores locales (sin login ni recursos remotos; `pywrangler` invoca `npx` sin fijar Wrangler):

```sh
# API (puerto 8787) y RAG (usa --port 8790 en su README), desde su carpeta
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.7 uv run pywrangler dev --local --ip 127.0.0.1 --port 8787
# RAG con D1 local: aplicar antes la migración
CI=1 uvx --from uv==0.13.7 uv run pywrangler d1 migrations apply DB --local
```

## CI por componente (sin despliegue)

Workflows en [`.github/workflows/`](.github/workflows/), todos con `push`,
`pull_request` filtrados por rutas y `workflow_dispatch`. Ninguno despliega,
inicia sesión ni aprovisiona infraestructura; no usan secrets.

| Workflow | Componente | Se dispara también por |
| --- | --- | --- |
| `api.yml` | `apps/api` | `infra/d1/**` |
| `rag.yml` | `apps/rag` | `infra/d1/**` |
| `web.yml` | `apps/web` | su propio workflow |
| `ingestion.yml` | `workers/ingestion` | `infra/d1/**` |
| `evaluations.yml` | `evaluations/ocr` | su propio workflow |
| `evaluations-rag.yml` | `evaluations/rag` | su propio workflow |

Estado remoto: según `docs/IMPLEMENTATION_STATUS.md`, `api`, `rag` y `web`
estuvieron verdes en GitHub sobre `fa23faf`. Los demás workflows no se han
ejecutado en GitHub. No existe CI de integración (el test API→RAG del RAG importa
`apps/api/src` por `sys.path`) ni pywrangler/workerd en CI. No marques estos
workflows filtrados como checks obligatorios sin un gate agregador que se ejecute siempre.
