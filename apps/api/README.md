# API Unitrópico

Primer entregable autónomo sobre **CPython 3.14**, FastAPI y Pydantic.
Aplicación no empaquetada, gestionada con uv y lockfile propio; no requiere
workspace ni configuración en la raíz del monorepo.

## Preparación y calidad

Desde `apps/api`, con uv disponible:

```sh
uv sync --locked --group dev
uv run python --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Para aplicar formato: `uv run ruff format .`. Tras un cambio autorizado de
versiones, regenerar el lock con `uv lock` y sincronizar con
`uv sync --group dev`.

Ruff verifica E, F, I, UP, B y D con convención Google. Solo se desactivan
D100/D104: los docstrings de módulo/paquete no se exigen si no aportan contexto,
según la arquitectura. Toda función y clase propia lleva docstring.

## Contrato y composición

- `unitropico_api.main.create_app()` es la factory ASGI; importarla no lee
  configuración ni crea la aplicación. El proyecto usa `src/` vía el pythonpath
  de pytest, no instala un paquete.
- `GET /health` devuelve HTTP 200 con `{"status":"ok"}`. Solo prueba salud
  HTTP local: **no es readiness de RAG**, almacenamiento, OCR ni modelos.
- Las rutas inexistentes devuelven el 404 JSON de FastAPI.
- `core/settings.py` es el único lector del entorno. `main.py` instancia y
  valida settings en cada composición, antes de configurar logging.
- `LOG_LEVEL` acepta exactamente `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`;
  ausente equivale a `INFO`. Vacío, minúsculas, espacios y niveles desconocidos
  provocan `pydantic.ValidationError` al construir la aplicación.
- El logger `unitropico_api` recibe únicamente el nivel explícito, escribe en
  stderr y reutiliza su handler. No cambia el logger raíz ni registra preguntas,
  cuerpos de peticiones o secretos. El nivel es compartido por el proceso.

Las pruebas invocan ASGI directamente con `asyncio.run`, sin HTTPX ni red;
verifican salud, 404, validación fail-fast y la frontera de configuración.

## Workers: bootstrap verificado; transporte API→RAG pendiente de prueba local

Los SDK `workers-py==1.17.9` y `workers-runtime-sdk==1.9.6` están en el grupo
`dev`, no en las dependencias HTTP. Wrangler toma `src/` como raíz Python.
`src/worker.py` exporta `Default(WorkerEntrypoint)` y compone `create_app` por
petición con `self.env.RAG`, pasando app, request, env y ctx a `asgi.fetch`.
Solo este bridge importa el SDK; el núcleo sigue siendo importable en CPython.
Las pruebas usan un shim SDK exclusivamente de test y aplicaciones ASGI reales.

`wrangler.jsonc` conserva `2026-11-08`, `python_workers` y `workers_dev: false`.
Declara `services: [{"binding":"RAG","service":"unitropico-rag-poc"}]`, cuyo
nombre coincide con `apps/rag/wrangler.jsonc`; no crea un servicio remoto.
`workers_dev: false` no sustituye la prohibición operativa de desplegar.

El cliente nativo hace un único `await binding.fetch` con POST JSON a
`https://rag.internal/retrieval/query`. El destino es el **binding**, no una URL
pública ni una autoridad de capacidades. No usa HTTPX, threads, retries ni
interop JS manual. El timeout asyncio de 10 segundos cubre fetch y lectura JSON.
Ausencia del binding, fallo de transporte/timeout, HTTP no-2xx o respuesta sin
datos producen 503; JSON malformado o esquema/status incoherente producen 502.
Ambos errores devuelven `{"status":"unavailable","evidence":[]}` sin diagnósticos.
Un JSON válido de abstención/evidencia devuelve 200; `unavailable` devuelve 503.
`GET /health` siempre permanece local, sin subrequests, incluso sin binding.
La API no genera respuestas LLM ni decide publicación o suficiencia: RAG debe
excluir versiones despublicadas. `/chat` sigue devolviendo 404.

Herramientas observadas durante la preparación: Node **24.15.0**, npm **11.12.1**,
Wrangler **4.158.0**, workerd **1.20261104.1** (metadatos del paquete instalado).
Wrangler requiere Node >=22. Pywrangler invoca `npx --yes wrangler`, por lo que
su versión efectiva depende del entorno/cache npm y no queda fijada en uv.lock.
El código instalado de pywrangler 1.17.9 fija ese comando y no ofrece una opción
pequeña de configuración para seleccionar Wrangler 4.158.0. No se añadieron
package.json, parches del SDK ni herramientas alternativas; comprobar la versión
efectiva antes de repetir la PoC.

Workers-py 1.17.9 requiere uv >=**0.13.4** para preparar Pyodide y Wrangler
>=**4.127.1**. Se verificó usando uv **0.13.7** aislado mediante uvx, sin
actualizar el uv global **0.13.0**. Desde `apps/api`:

```sh
uvx --from uv==0.13.7 uv --version
uv sync --locked --group dev
uv run pywrangler --version
uv run pywrangler --help
WRANGLER_SEND_METRICS=false uvx --from uv==0.13.7 uv run pywrangler dev --local --ip 127.0.0.1 --port 8787
```

El último comando histórico mantiene un servidor: no se ejecutó para este
transporte. Para una futura prueba API→RAG, el operador deberá disponer de ambos
Workers en desarrollo local y enlazar el nombre exacto `unitropico-rag-poc`.
Usar únicamente Wrangler ya disponible en cache y modo local; el wrapper
pywrangler sincroniza dependencias e invoca `npx --yes`, así que no garantiza
una ejecución offline. No se verificó aquí un comando de arranque multiworker.
Sin login, tokens, `--remote` ni deploy. La prueba anterior consistió en probar
`GET http://127.0.0.1:8787/health` (200) y `/missing` (404), y detenerlo al terminar.
No requiere Uvicorn ni un cliente HTTP Python adicional.

La PoC fue ejecutada por el operador padre: `/health` devolvió **200** con
`{"status":"ok"}` y `/missing` **404** con `{"detail":"Not Found"}`. El job local
terminó con exit 0 en 14 segundos. El builder Pyodide utilizó Python **3.14.2**;
el host de pruebas CPython utilizó **3.14.6**. Son verificaciones distintas.

Pywrangler prepara `.venv-workers/`, `python_modules/` y estado `.wrangler/`
(ignorados por Git y excluidos explícitamente mediante `tool.ruff.extend-exclude`,
también en copias autónomas sin `.git`).
Se conserva **pylock.toml** como evidencia reproducible, sin ignorarlo:

- **uv.lock** fija dependencias de aplicación y desarrollo para el host CPython.
- **pylock.toml** fija la resolución para Pyodide/WASM, con URLs y hashes de
  wheels. Contiene FastAPI **0.162.2**, Pydantic **2.14.3**, pydantic-core
  **2.50.4** con wheel `cp314-cp314-pyemscripten_2026_0_wasm32`, y
  workers-runtime-sdk **1.9.6**.

No son locks intercambiables. Pywrangler resuelve automáticamente el SDK y
reutiliza pylock.toml como restricciones en siguientes resoluciones; revisar
sus cambios, especialmente tras regenerarlo o solicitar una actualización.
El pin del grupo dev por sí solo no fija el SDK del runtime.

## Límites de este entregable

**Probado únicamente en Workers local (workerd), no en Cloudflare remoto.**
La PoC anterior verifica el bootstrap y dos rutas HTTP, no el transporte
API→RAG nuevo ni la compatibilidad de dependencias futuras. El transporte solo
se verificó mediante pruebas deterministas ASGI con binding de test; falta la
prueba funcional con ambos Workers locales. No hubo login, deploy ni creación
de recursos remotos. Ingesta, OCR, autenticación, frontend, generación LLM y
publicación de corpus quedan fuera de este cambio.
