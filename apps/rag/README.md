# RAG: PoC local determinista, no producto terminado

Este componente ofrece recuperación documental experimental con dependencias
inyectadas y una PoC determinista separada con LangChain Core real
(`RunnableLambda` async y `ainvoke`). **No implementa chat ni genera respuestas**.
Sin retriever y catálogo configurados, retrieval devuelve `unavailable`: no hay
índice, embeddings, inferencia, persistencia ni corpus publicado por esta prueba.
No requiere credenciales ni hace llamadas pagadas.

## Contrato HTTP local

- `GET /health` → `200 {"status":"ok"}`: vida local, no readiness de servicios.
- `POST /poc/query` recibe exclusivamente `{"question":"  ¿Apoyos?  "}` y devuelve
  `200 {"mode":"deterministic-poc","normalized_question":"¿Apoyos?"}`.
- `question` debe ser string de 1–500 caracteres **antes** de `strip`; se rechazan
  cadenas vacías, solo whitespace, tipos distintos y campos extra con `422`.
  Error propio de whitespace: `question must not be whitespace-only`.
- `/chat` no existe (`404`). El resultado PoC no es una respuesta a la pregunta.

### Recuperación interna y puente local

`POST /retrieval/query` recibe `{"question":"  prueba  ","limit":5}`:
pregunta normalizada de 1–500 caracteres, límite entero 1–20 (por defecto 5),
sin campos extra ni roles privilegiados (`422` antes de acceder a dependencias).
No confundir este contrato con `/poc/query`.

| HTTP / estado | Significado |
| --- | --- |
| `200 evidence_found` | Fragmentos elegibles con texto exacto, IDs de documento/versión/chunk, SHA de fuente, página física y sección nullable; nunca `answer`. |
| `200 abstained` | Recuperación operativa sin evidencia elegible; `evidence: []`. |
| `503 unavailable` | Falta configuración o falla una dependencia; `evidence: []`, sin diagnósticos ni resultados parciales. |

Cada consulta comprueba publicación/revisión y correspondencia de fuente, hash
de texto y ubicación contra el catálogo actual. Despublicar excluye el fragmento
en la consulta siguiente. Recuperar evidencia no prueba suficiencia para responder.

`tests/test_api_integration.py` atraviesa FastAPI API → `RagServiceBindingClient`
real → FastAPI RAG, mediante un transporte ASGI async exclusivo del test. Solo
retriever/catálogo usan fixtures claramente sintéticas; no se publica contenido.
El cliente usa `https://rag.internal/retrieval/query` como dirección del binding,
no como llamada a Internet. La API expone `/retrieval/query`; la web usa
`/api/retrieval/query` vía proxy de desarrollo (véase [Web](../web/README.md)).
La API también clasifica upstream malformado como `502 unavailable`.

No registrar preguntas. La composición fuerza `LANGSMITH_TRACING`,
`LANGCHAIN_TRACING_V2` y `LANGCHAIN_TRACING` a `false`, incluso si el proceso los
hereda habilitados. No se configura cliente remoto ni callback de trazas.
`RAG_LOG_LEVEL` admite DEBUG/INFO/WARNING/ERROR, por defecto WARNING; es la única
opción local. No se necesita archivo de entorno.

## Instalación y controles host

Desde `apps/rag`, con uv disponible:

```sh
uv sync --locked --group dev
uv run python --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pywrangler --help
uv run pywrangler --version
```

Para actualizar el lock deliberadamente: `uv lock`; para aplicar formato:
`uv run ruff format .`. No hay workspace ni empaquetado (`tool.uv.package=false`).
Pytest añade `src` al import path; las pruebas llaman ASGI directamente mediante
`asyncio.run`, sin HTTPX/TestClient. La integración añade temporalmente
`apps/api/src` al path solo en su fixture, sin paquete compartido ni workspace.
Controles host offline desde `apps/rag`, sin sincronizar dependencias:

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync --python 3.14 pytest -p no:cacheprovider
uv run --offline --no-sync --python 3.14 ruff check --no-cache .
uv run --offline --no-sync --python 3.14 ruff format --check --no-cache .
```

Este puente prueba contratos host, **no el SDK ni la ejecución en Workers**.
La integración actual no se probó con Worker local ni deploy; la evidencia
histórica siguiente corresponde únicamente a `/health` y `/poc/query`.

## Organización

- `main.py`: único importador/instanciador de settings; compone servicio y rutas.
- `core/settings.py`: lectura/validación de entorno y bloqueo de tracing remoto.
- `core/logger.py`: logger central, sin contenido de solicitudes.
- `api/routes.py`: contratos Pydantic manuales y transporte HTTP.
- `services/query.py`: orquestación contra callable async inyectado, sin LangChain.
- `infra/langchain.py`: encapsula el SDK; importación al invocar, después de configurar
  el proceso. Los errores de importación nativa no se capturan ni se ocultan.
- `src/worker.py`: adaptación oficial `from workers import asgi` y
  `Default = asgi.entrypoint(app)`; no lee configuración.

## Versiones y evidencia

Pines directos: Python 3.14 (`.python-version`), FastAPI 0.162.2, Pydantic 2.14.3,
LangChain Core 1.7.6, workers-runtime-sdk 1.9.6. Desarrollo local: pytest 9.2.0,
Ruff 0.17.4 y workers-py/pywrangler 1.17.9. El lock registra todas las transitivas;
no se usa `--no-deps` ni se sustituye una dependencia incompatible.

Resolución CPython observada: LangSmith 0.14.17, uuid-utils 0.19.0 (<1),
orjson 3.13.0, zstandard 0.25.0, xxhash 4.0.2, pydantic-core 2.50.4,
PyYAML 6.0.3. También se resuelven HTTPX 0.28.1 y httpx2 2.16.0
**transitivos**, no clientes directos. Sin paquete LangChain completo, LangGraph,
SDK OpenAI, pydantic-settings ni tokenizadores. Tests estructurales comprueban
el lock sin LangGraph y la frontera de imports del servicio.

Host observado: CPython 3.14.6. LangSmith emite avisos de obsolescencia de
`asyncio.iscoroutinefunction`; no se silencian. Una prueba en proceso limpio
comprueba que importar `main` no carga Core/LangSmith, que la composición apaga
los tres switches heredados habilitados y que la cadena funciona con conexiones
socket bloqueadas. El adaptador se importa antes de configurar, pero el SDK
solo se importa dentro de su función async: no fue necesario reestructurarlo.
Esta prueba acotada no certifica todas las posibles rutas de red de terceros.

## Workers/WASM: PoC local observada, no garantía de deploy

`wrangler.jsonc` declara `src/worker.py`, fecha `2026-11-08` y flag
`python_workers`, sin bindings remotos. El SDK ASGI importa `js` de Pyodide:
los tests host no importan el bridge.

workers-py 1.17.9 necesita **uv >=0.13.4 para preparar Pyodide**. El coordinador
usó uv 0.13.7 aislado, sin actualizar el uv global 0.13.0. Comando ejecutado por
el coordinador desde `apps/rag` (servidor ya detenido; no es un check host):

```sh
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.7 uv run pywrangler dev --local --ip 127.0.0.1 --port 8788
```

Evidencia comunicada por el coordinador: job 10 terminó con exit 0 en 22 s;
workerd/Pyodide arrancó con 21 MB / 1495 módulos reportados. `GET /health` dio
200; `POST /poc/query` con `{"question":"  Prueba local  "}` dio
`200 {"mode":"deterministic-poc","normalized_question":"Prueba local"}`;
whitespace-only dio 422. No hubo deploy, inferencia ni retrieval. Los 21 MB
reportados **no son una medición de tamaño comprimido** ni una validación de
límites de despliegue. Wrangler 4.158.0 y workerd 1.20261104.1 están reportados
en caché npx; falta un pin reproducible Node/runtime en esta PoC.

### Dos locks, dos destinos

- `uv.lock`: resolución host CPython y herramientas de desarrollo.
- `pylock.toml`: lock WASM generado por pywrangler durante la prueba; se conserva
  sin regenerar ni editar. Declara Python >=3.14.2 y wheels Pyodide v314.0.7 /
  `pyemscripten_2026_0_wasm32`, además de paquetes Python puros.
- Diferencia nativa real: orjson **3.11.8 WASM frente a 3.13.0 host**.
  Ambos mantienen workers-runtime-sdk 1.9.6, pydantic-core 2.50.4,
  uuid-utils 0.19.0, xxhash 4.0.2, PyYAML 6.0.3 y zstandard 0.25.0;
  el lock WASM incluye `httpx2-jsfetch` 1.0 con marker emscripten.
  No se cambiaron los pines directos aprobados para forzar compatibilidad.
- `python_modules/`, `.venv-workers/` y `.wrangler/` son salida local ignorada,
  no código fuente ni sustitutos de los locks. No se borra el vendor generado.

La PoC demuestra esta ruta determinista local con esas resoluciones, no soporte
general de paquetes nativos, runtime remoto ni producto institucional terminado.
No se ejecutó deploy/dry-run, login o provisión. No ejecutar rutas remotas.
Si una actualización falla, conservar el error y detener solo esa PoC de runtime;
no cambiar versiones ni ocultar ImportError sin una nueva decisión. No se declara
completado el MVP. Documentación oficial:
https://developers.cloudflare.com/workers/languages/python/packages/fastapi/
