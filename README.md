# Agente conversacional Unitrópico

Proyecto de tesis de la Maestría en Inteligencia Artificial de la Universidad de
La Salle: orientar sobre Fomento Socioeconómico de Bienestar Universitario de
Unitrópico con evidencia institucional. **En desarrollo: no es un asistente operativo.**

Consulta la [arquitectura](ARQUITECTURA.md), el [plan incremental](PLAN_MVP.md)
y la [investigación y evidencia técnica](INVESTIGACION.md).

## Componentes actuales

| Componente | Estado y alcance real |
| --- | --- |
| [Web](apps/web/README.md) | Vue/Vite/Nuxt UI: aviso de servicio en preparación; sin chat ni conexión al API. |
| [API](apps/api/README.md) | FastAPI: `GET /health` local; no comprueba disponibilidad del RAG ni servicios externos. |
| [RAG](apps/rag/README.md) | PoC determinista con LangChain Core: normaliza una pregunta; sin retrieval ni LLM. |

OCR, embeddings, autenticación administrativa e ingesta siguen pendientes.
Los SDK Workers están destinados solo a pruebas locales por ahora: los checks
CPython no demuestran compatibilidad con Pyodide/WASM ni operación en Cloudflare.
Verificación local recibida: API en Workers respondió `/health` con 200 y
`{"status":"ok"}`, y una ruta inexistente con 404. Web pasó instalación congelada
limpia, lint, tipos y build en Docker con Node 24.15.0 y pnpm 12.18.0.
RAG también pasó la PoC Workers local: transformación asíncrona determinista y
rechazo 422 de preguntas vacías; sin retrieval ni LLM. No se ha ejecutado CI remota.

## Preparación y calidad local

Ejecuta estos bloques desde la raíz del repositorio. Cada aplicación tiene su
propio manifiesto y lockfile; no hay workspace ni instalación de dependencias raíz.
Requisitos: Python 3.14 con uv 0.13.7; para web, Node 24.15.0 y pnpm 12.18.0.
El uv global sigue en 0.13.0: las pruebas usaron uv 0.13.7 aislado mediante
`uvx --from uv==0.13.7 uv …`; los bloques siguientes presuponen esa versión.
TypeScript es una dependencia **local** de web, no requiere instalación global.

```sh
(cd apps/api && \
  uv sync --locked --group dev && \
  uv run ruff check . && \
  uv run ruff format --check . && \
  uv run pytest)
```

```sh
(cd apps/rag && \
  uv sync --locked --group dev && \
  uv run ruff check . && \
  uv run ruff format --check . && \
  uv run pytest)
```

```sh
(cd apps/web && \
  pnpm install --frozen-lockfile && \
  pnpm lint && \
  pnpm typecheck && \
  pnpm build)
```

Los README de cada componente detallan sus contratos y límites. Estas
comprobaciones no necesitan credenciales ni servicios externos, aunque la
instalación puede descargar dependencias. No guardes secretos reales en archivos
de entorno, plantillas ni en Git; cualquier ejemplo debe usar valores ficticios.
No se requiere login, token de Cloudflare ni GitHub secrets.

## CI de calidad, sin despliegue

Los workflows [API](.github/workflows/api.yml), [RAG](.github/workflows/rag.yml)
y [web](.github/workflows/web.yml) ejecutan los controles anteriores. Se activan
en `push` y `pull_request` por cambios en su componente, su propio workflow o
`ARQUITECTURA.md`; también admiten ejecución manual (`workflow_dispatch`). Las
cachés dependen de los lockfiles de cada componente, no de un lockfile global.
No se inicia Pywrangler en CI ni se despliega o aprovisiona infraestructura.
No hay pipelines vacíos de ingesta o integración.

**Checks requeridos:** no marcar estos workflows filtrados como obligatorios
incondicionalmente: al omitirse por rutas pueden dejar un check pendiente.
Antes se necesita un agregador/gate que se ejecute siempre y refleje los controles
aplicables; aquí no se configura ese gate ni la protección de ramas.
La configuración aún debe ejecutarse en GitHub Actions: no se afirma CI remota verde.
