# Evidencia de viabilidad y decisiones técnicas

Consulta inicial: **9 de noviembre de 2026**. Este registro complementa [ARQUITECTURA.md](ARQUITECTURA.md) y [PLAN_MVP.md](PLAN_MVP.md). Una versión publicada o una guía de compatibilidad **no equivale a una prueba ejecutada en el runtime**.

## Decisiones aprobadas en este ciclo

| Área | Selección aprobada | Evidencia oficial de versión |
|---|---|---|
| Web | Vue 3.5.45, Vite 8.3.6, plugin-vue 6.0.10, Nuxt UI 4.12.0 | [Vue](https://registry.npmjs.org/vue/latest), [Vite](https://registry.npmjs.org/vite/latest), [plugin](https://registry.npmjs.org/@vitejs/plugin-vue/latest), [UI](https://registry.npmjs.org/@nuxt/ui/latest) |
| Calidad web | TypeScript 6.0.3, vue-tsc 3.3.14, ESLint 10.14.0, eslint-plugin-vue 10.12.0, typescript-eslint 8.72.1 | [TypeScript](https://registry.npmjs.org/typescript), [vue-tsc](https://registry.npmjs.org/vue-tsc/latest), [ESLint](https://registry.npmjs.org/eslint/latest), [Vue lint](https://registry.npmjs.org/eslint-plugin-vue/latest), [TS lint](https://registry.npmjs.org/typescript-eslint/latest) |
| CSS | Tailwind CSS 4.3.5, requerido por la integración de Nuxt UI | [Registro](https://registry.npmjs.org/tailwindcss/latest), [integración Vue](https://ui.nuxt.com/docs/getting-started/installation/vue) |
| Python | FastAPI 0.162.2, Pydantic 2.14.3, Ruff 0.17.4, pytest 9.2.0 | [FastAPI](https://pypi.org/pypi/fastapi/json), [Pydantic](https://pypi.org/pypi/pydantic/json), [Ruff](https://pypi.org/pypi/ruff/json), [pytest](https://pypi.org/pypi/pytest/json) |
| RAG lineal | langchain-core 1.7.6 con transitivas, **sin LangGraph** | [Core](https://pypi.org/pypi/langchain-core/json) |
| PoC Workers local | workers-py 1.17.9, workers-runtime-sdk 1.9.6 y Wrangler/workerd requeridos | [CLI](https://pypi.org/pypi/workers-py/json), [SDK](https://pypi.org/pypi/workers-runtime-sdk/json) |

Se aprobaron las dependencias técnicas necesarias del conjunto web, no Router, Pinia, cliente HTTP externo, framework de pruebas frontend ni un SDK de proveedor generativo. Los lockfiles de cada proyecto fijan la resolución efectiva; los enlaces `latest`/JSON anteriores son catálogos cambiantes, no snapshots inmutables.

**Por qué no instalar simplemente `latest`:** TypeScript 7.0.2 era la versión estable más reciente consultada, pero typescript-eslint 8.72.1 declara `typescript >=4.8.4 <6.1.0`. Se eligió la estable compatible 6.0.3, no una prerelease. Asimismo, `langchain` 1.4.9 exige `langgraph >=1.2.12,<1.3.0`; se aprobó Core para evitar esa instalación, sin sustituir la tecnología RAG por otra.

## Resultados ejecutados y verificación de cierre

| Comprobación | Resultado observado | Alcance |
|---|---|---|
| API y RAG en CPython 3.14.6 | Cierre independiente: 18 + 18 pruebas (36 en total), Ruff lint y formato: exit 0; formato comprobado en 9 archivos API y 12 RAG | RAG conserva 13 avisos de obsolescencia de LangSmith, no silenciados |
| Web en Docker Node 24.15.0 / pnpm 12.18.0 | Instalación limpia congelada, lint, typecheck y build: exit 0 | Copia aislada, fuente montada en solo lectura; sin revisión visual |
| API en Workers local | `/health` 200 y ruta inexistente 404 | workerd/Pyodide, sin recursos remotos |
| Core en Workers local | `/health` 200; `/poc/query` 200 (`deterministic-poc`, trim) y pregunta de solo espacios 422 | `RunnableLambda` asíncrono real; **sin retrieval ni LLM** |

Ambas PoC usaron `uvx --from uv==0.13.7 uv run pywrangler dev --local`, con `--ip 127.0.0.1 --port 8787` para API y puerto 8788 para RAG. Los dos servidores se detuvieron después de las pruebas. El uv global permanece en 0.13.0; se usó 0.13.7 aislado. Host CPython 3.14.6; preparación Pyodide 3.14.2; Wrangler observado 4.158.0. Pywrangler invoca `npx --yes wrangler` sin fijar versión: es un límite de reproducibilidad, no resuelto instalando otra herramienta. Cada componente conserva `uv.lock` para host y `pylock.toml` generado para WASM. El conjunto runtime puede diferir: orjson 3.11.8 en RAG/Pyodide frente a 3.13.0 en host. No se midió tamaño comprimido de despliegue ni se probó producción.

La primera copia Docker falló por intentar restaurar propietarios con capacidades retiradas; se corrigió con `tar --no-same-owner`, conservando `--cap-drop=ALL` y `no-new-privileges`. Imagen `node:24.15.0-bookworm-slim`, digest `sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d`. La instalación pnpm requirió revisar y permitir únicamente los scripts `esbuild@0.27.7` y `vue-demi@0.14.10` en política **local** de `apps/web/pnpm-workspace.yaml`, sin agrupar otros proyectos ni cambiar configuración global.

## Python 3.14 y Cloudflare

- El entorno dispone de **CPython 3.14.6 mediante uv**; `python3` del sistema es 3.12.3. No confundir ambos.
- La [guía FastAPI](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/) documenta `from workers import asgi` y `Default = asgi.entrypoint(app)`. El Worker proporciona ASGI; no se necesita introducir Uvicorn para ese destino.
- El flag [`python_workers_314`](https://developers.cloudflare.com/workers/configuration/compatibility-flags/index.md) selecciona Python 3.14; está predeterminado desde `2026-09-08`. `requires-python` no selecciona por sí solo la versión del runtime.
- Cloudflare admite [paquetes puros, wheels PyEmscripten y paquetes incluidos en Pyodide](https://developers.cloudflare.com/workers/languages/python/packages/). El soporte general anunciado para LangChain no certifica cualquier versión de sus transitivas.
- [Pydantic Core 2.50.4](https://pypi.org/pypi/pydantic-core/2.50.4/json) publica `pydantic_core-2.50.4-cp314-cp314-pyemscripten_2026_0_wasm32.whl`. El catálogo por sí solo no prueba ejecución; la evidencia ejecutada es la PoC local descrita arriba.
- Core introduce LangSmith y paquetes nativos/transporte. El análisis de dependencias señala UUID Utils, orjson, xxhash y zstandard como puntos que deben verificarse contra el Pyodide concreto. No declarar incompatibilidad absoluta únicamente por ausencia de wheel WASM en PyPI.
- La [biblioteca estándar de Workers](https://developers.cloudflare.com/workers/languages/python/stdlib/) tiene restricciones de hilos/procesos. La PoC debe usar funciones asíncronas nativas; `ainvoke` por sí solo no garantiza ausencia de thread pools.

**Prueba mínima ejecutada:** arrancaron workerd/Pyodide local, HTTP y una cadena determinista con las versiones resueltas. Esto no valida todas las APIs de Core, transporte de proveedores, Vectorize ni integración remota. Cada nueva dependencia/ruta necesita su propia prueba; `uv sync` o un doble por sí solos no la sustituyen. Sin login, bindings remotos, provisión ni deploy.

## OCR, tablas y referencias

[Firecrawl Parse](https://docs.firecrawl.dev/features/parse) documenta:

- entrada multipart con archivo y opciones; máximo **50 MB**, por lo que 19 MB cabe nominalmente;
- **1 crédito por página**, sin convertirlo en una estimación mensual en dólares;
- `pages`, `pageMarkers` y `blocks` en opciones del parser PDF, sin costo adicional anunciado;
- bloques con geometría y `markdownSpan` para relacionar texto con página;
- timeout predeterminado 30 s y máximo 300 s. Ajustar tiempos del Workflow tras medir el documento real.

[AnyDoc](https://github.com/firecrawl/anydoc), versión npm/WASM consultada **0.2.4**, **no realiza OCR local**: los escaneos requieren OCR alojado y se envía el documento completo. WASM no convierte esa ruta en OCR local. No confundir sus benchmarks de documentos de oficina con calidad demostrada para nuestras tablas escaneadas.

**Selección todavía pendiente:** parser/proveedor definitivo, runtime y SDK de ingesta. No se ha ejecutado benchmark ni se ha enviado ningún PDF a esos servicios en este ciclo.

Benchmark propuesto, no ejecutado: conservar hash del original, registrar parser/opciones y probar un PDF real escaneado con tablas; comprobar cobertura de páginas, cifras/negaciones, estructura y orden de tablas, secciones, spans y reintentos. Una muestra contrastada por el investigador no equivale a validación institucional independiente.

Para publicar: rechazar extracción parcial, referencias fuera de rango y chunks sin procedencia verificable. Distinguir página física del PDF de numeración impresa; no inventar secciones ausentes. Mantener la extracción original inmutable si los spans dependen de sus offsets. Las validaciones automáticas detectan anomalías, **no garantizan fidelidad OCR ni respaldo semántico de cada afirmación**.

## Gateway, embeddings y búsqueda

- El [catálogo público Vercel](https://ai-gateway.vercel.sh/v1/models) devolvió **`openai/gpt-6-luna`**, tipo `language`. No confundirlo con el modelo del orquestador ni con rutas de modelos/clasificadores de Pi.
- La [API OpenAI-compatible](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-compat) documenta base `https://ai-gateway.vercel.sh/v1`, `POST /chat/completions` y `POST /embeddings`, autenticados. No se realizó inferencia ni se comprobó el acceso de una cuenta; no se eligió SDK ni modelo alternativo.
- [Vectorize](https://developers.cloudflare.com/vectorize/platform/limits/) limita a **1536 dimensiones** y **10 KiB de metadatos por vector**. El modelo y las dimensiones de embeddings siguen pendientes de aprobación; guardar chunks completos fuera de esos metadatos.
- [D1 soporta FTS5](https://developers.cloudflare.com/d1/sql-api/sql-statements/). Su calidad lexical en español y la mejora de RRF frente al baseline semántico siguen siendo experimentos, no resultados.

## Entorno y agentes

El entorno de la sesión declara `PI_MODEL=gpt-6.1-sol`, proveedor `openai-codex`. Se invocaron subagentes reales. Los investigadores `explore` fallaron antes de entregar resultados; los verificadores y workers sí produjeron entregas. La causa fatal de los fallos no quedó demostrada: el fragmento de stderr contiene una advertencia de empaquetado, no un diagnóstico suficiente. No se modificó la instalación ni el perfil de modelos de Pi.

Docker CLI y servidor **29.8.2** están disponibles: tras un primer fallo por socket ausente, el usuario habilitó Docker y se comprobó con `docker info`. **La instalación limpia y los controles del frontend ya pasaron en contenedor.** TypeScript es una dependencia local del frontend; no requiere instalación global. Cualquier validación local o de CI debe indicar el entorno realmente utilizado.

La estructura de los tres workflows de calidad se comprobó localmente (YAML, permisos, rutas, SHA, lockfiles y comandos), pero **GitHub Actions no se ejecutó** ni se instaló actionlint. La revisión nativa de Gentle se detuvo en inspect con STOP `managed_assets_outdated`, sin START ni lineage; no se sincronizó la instalación sin autorización ni se reconstruyó un documento nativo.

Según la entrega final comunicada por el coordinador, el verificador genérico independiente `mw8qgk4u-h-y1py` no encontró defectos funcionales bloqueantes: pasaron sus 16 pruebas API, 16 RAG y 14 probes adicionales de RAG en scratch. Estos probes no se suman al conteo de tests del repositorio ni equivalen a aprobación nativa.

**P3 resuelto:** los owners API/RAG incorporaron un `extend-exclude` específico para tres directorios locales y regresiones con Ruff real en copias sin `.git`. Según la nueva entrega independiente `mwgzds7n-1-o3qg`, pasaron las cuatro regresiones; API comprueba además que Ruff rechaza errores del código propio. La suite pasa de 32 a **36 tests: 18 API y 18 RAG**; los 14 probes scratch anteriores siguen fuera de ese conteo.

El nuevo verificador ejecutó separadamente los seis comandos de pytest, Ruff lint y formato, todos con exit 0, en copia aislada y modo offline/frozen con `UV_NO_SYNC=1`, sin instalar, descargar ni editar. API: 18 passed y formato de 9 archivos; RAG: 18 passed y formato de 12 archivos, con los 13 warnings de LangSmith sin silenciar. El código productivo permanece intacto; siguen vigentes los resultados anteriores de Docker web y las PoC Workers API/RAG. Este cierre no equivale a aprobación nativa: persiste `managed_assets_outdated`, sin sync. Los `.gitignore` propios de API/RAG ya ignoran `vendor`; las cachés locales no forman parte de Git.

## Pendientes que requieren insumos o decisiones posteriores

1. PDF institucional escaneado con tablas y FAQs/respuestas verificadas para benchmark y dataset dev/final, confirmando permisos de uso antes de enviarlo a servicios.
2. Parser OCR, embeddings/dimensiones, lenguaje de ingesta y prueba Workflows.
3. Autenticación administrativa y protección antiabuso antes de exposición pública.
4. Cliente/protocolo del gateway y una prueba de inferencia con autorización de gasto independiente.
5. Presupuesto y política de reintentos, con costos y latencia medidos; ningún objetivo exploratorio se presenta como alcanzado.
