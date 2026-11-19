# Ingesta con Python Worker + Cloudflare Workflow, y bindings desde Python

Fecha: 2026-11-18. Versiones: workers-runtime-sdk 1.9.6, pywrangler 1.17.9, wrangler 4.165.0, `compatibility_date` 2026-11-08, `pywrangler dev --local` sin cuenta remota.
Evidencia: documentación oficial (URLs), código del SDK instalado y una prueba local efímera (Worker de prueba en scratchpad, ya no disponible).

> `pywrangler dev` exige uv ≥ 0.13.4 (el host tiene 0.13.0). Usar uv más reciente en un venv aislado (p. ej. `uvx --from uv==0.13.9 ...`), sin actualizar el uv global.

## 1. Workflows en Python

- Estado: **open beta** desde 2025-08-22, sin anuncio de GA (https://developers.cloudflare.com/workflows/reference/changelog/). En workerd el flag figura como "still in development" (https://github.com/cloudflare/workerd/blob/main/src/workerd/io/compatibility-date.capnp).
- Flags: `python_workflows` implícito con `python_workers` desde 2025-09-20; `python_workflows_implicit_dependencies` desde 2026-02-25. Con la fecha del repo basta `["python_workers"]` (verificado en local).
- API de pasos (https://developers.cloudflare.com/workflows/python/python-workers-api/; confirmado en `workers/entrypoints.py`):
  - `step.do(name=None, *, concurrent=False, config=None)` como decorador; el callable resultante se invoca con `await`.
  - Dependencias por nombre de parámetro (un parámetro con el nombre de un paso anterior recibe su resultado). Parámetro `ctx` → `{"step": {name, count}, "attempt", "config"}`.
  - `config={"retries": {"limit", "delay", "backoff"}, "timeout": ...}`.
  - `step.sleep(name, "10 seconds")`, `step.sleep_until`, `step.wait_for_event(name, type, timeout=...)`.
  - `from workers.workflows import NonRetryableError` corta reintentos; el estado queda `errored` con `error.message` (verificado).
  - Solo `ValueError` y `TypeError` cruzan RPC como tales.
- wrangler.jsonc (https://developers.cloudflare.com/workers/wrangler/configuration/#workflows):
  - Worker de ingesta: `"workflows":[{"name":"ingestion","binding":"INGESTION","class_name":"IngestionWorkflow"}]`.
  - API: el mismo bloque + `"script_name":"unitropico-ingestion"`. `concurrency`/`default_retention` no se combinan con `script_name`.
- Desde otro Worker (https://developers.cloudflare.com/workflows/python/bindings/, https://developers.cloudflare.com/workflows/build/workers-api/); el SDK envuelve el binding (`_WorkflowBindingWrapper` en `rpc.py`):
  ```python
  inst = await self.env.INGESTION.create(id="doc_123_v1", params={"document_id": "123", "r2_key": "raw/123.pdf"})
  inst = await self.env.INGESTION.get("doc_123_v1")
  st = await inst.status()   # {"status": "queued|running|errored|complete|waiting|...", "output", "error": {"name","message"}}
  await inst.restart()       # también pause / resume / terminate / send_event
  ```
- Idempotencia:
  - `create()` con id existente lanza error (`WorkflowError: (instance.already_exists)` en local). Patrón: `create` → si `already_exists`, `get` + `status`; si `errored`, `restart()`.
  - `create_batch` no lanza con ids duplicados (código 10405).
  - Id: ≤ 100 caracteres `[A-Za-z0-9_-]`, empieza por letra, dígito o `_`.
  - Cada paso debe ser idempotente: claves R2 deterministas, UPSERT en D1, `upsert` en Vectorize con ids estables.
- Límites (https://developers.cloudflare.com/workflows/reference/limits/):

  | Límite | Free | Paid |
  |---|---|---|
  | Retorno de un paso | 1 MiB | 1 MiB |
  | Payload del evento | 1 MiB | 1 MiB |
  | CPU por paso | 10 ms | 30 s (hasta 5 min) |
  | Wall time por paso | ilimitado | ilimitado |
  | Subrequests por instancia | 50 | 10 000 |
  | Pasos | 1 024 | 10 000 |
  | Estado persistido | 100 MB | 1 GB |
  | Retención | 3 días | 7 días por defecto |

  Consecuencias: el plan **Workers Paid** es prácticamente necesario. Ningún paso devuelve el texto OCR: se escribe en R2 y se devuelven claves y conteos. **El límite de 1 MiB no se aplica en local** (un paso devolvió 19,5 MB sin error).

- Ejemplo mínimo (ejecutado en local hasta `complete`):
  ```python
  from workers import Response, WorkerEntrypoint, WorkflowEntrypoint
  from workers.workflows import NonRetryableError

  class IngestionWorkflow(WorkflowEntrypoint):
      async def run(self, event, step):
          p = event["payload"]

          @step.do(config={"retries": {"limit": 2, "delay": "1 second", "backoff": "constant"}})
          async def read_pdf():
              obj = await self.env.DOCS.get(p["key"])
              if obj is None:
                  raise NonRetryableError("missing object")
              return {"size": obj.size}

          @step.do()
          async def store(read_pdf, ctx):
              await self.env.DB.prepare(
                  "INSERT INTO docs(id,size) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET size=excluded.size"
              ).bind(p["key"], read_pdf["size"]).run()
              return {"attempt": ctx["attempt"]}

          r = await store()
          await step.sleep("pause", "1 second")
          return r

  class Default(WorkerEntrypoint):
      async def fetch(self, request):
          return Response("not found", status=404)
  ```

## 2. D1, Vectorize, R2 y fetch desde Python

- No hay wrappers Python dedicados, pero el SDK 1.9.6 aplica `_EnvWrapper`/`_BindingWrapper` (`workers/rpc.py`) a `self.env` en `WorkerEntrypoint` y `WorkflowEntrypoint`: argumentos con `to_js(..., dict_converter=Object.fromEntries)` (`None` → `null`), resultados con `to_py()` (dict con acceso por atributo o lista), kwargs → último argumento objeto. No hace falta `to_js`/`to_py` manual (la página de FFI https://developers.cloudflare.com/workers/languages/python/ffi/ es anterior a este cambio).
- **D1** (verificado en local): `await env.DB.prepare(sql).bind(a, b).all()` → `{"results": [...], "meta": {changes, last_row_id, rows_read, ...}}`; `.run()` igual; `.first()` → dict o `None`; `await env.DB.batch([s1, s2.bind(2)])` → lista; `await env.DB.exec(sql)` para DDL. FTS5 con `MATCH ?` y `bm25()` funciona en D1 local.
- **Vectorize** (https://developers.cloudflare.com/vectorize/reference/client-api/, https://developers.cloudflare.com/vectorize/reference/metadata-filtering/):
  ```python
  r = await env.VECTORIZE.query(vec, topK=8, returnMetadata="all", filter={"doc_id": {"$in": ids}})
  await env.VECTORIZE.upsert([{"id": cid, "values": vec, "metadata": {...}}])   # máx. 1000 por lote
  await env.VECTORIZE.deleteByIds([...])
  ```
  topK máx. 50 con metadata/valores (100 sin ellos); 1536 dims es el máximo (https://developers.cloudflare.com/vectorize/platform/limits/); mutaciones asíncronas y eventualmente consistentes; índices de metadata antes de insertar (`wrangler vectorize create-metadata-index <idx> --property-name=doc_id --type=string`), 64 B indexados por string, máx. 10. **No verificado**: forma real del retorno y conversión de `filter` (Vectorize exige remoto).
- **R2** (https://developers.cloudflare.com/r2/api/workers/workers-api-reference/; verificado con 19,5 MB): `await env.DOCS.put(key, data, httpMetadata={"contentType": "application/pdf"})` → objeto con `.size`; `get` → `None` si no existe; `await obj.arrayBuffer()` → `memoryview`. `put` con stream exige longitud conocida. Una sola parte hasta ~5 GiB: no hace falta multipart.
- **fetch saliente**:
  - JSON: `workers.fetch(url, method="POST", headers={...}, body=json.dumps(...))` funciona (mismo patrón que `apps/rag/.../infra/workers_fetch.py`). Embeddings: `POST https://ai-gateway.vercel.sh/v1/embeddings`, `{"model":"openai/text-embedding-3-small","input":[...]}` → `data[i].embedding` (https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/embeddings).
  - **Bug en SDK 1.9.6 con multipart**: `workers.fetch(body=FormData())` envía `str()` del objeto; `body=fd.js_object` envía una boundary distinta. Lo que funciona (verificado con 19,5 MB):
    ```python
    import js
    from js import Object
    from pyodide.ffi import to_js
    from workers import Blob, FormData

    fd = FormData()
    fd.append("file", Blob([data], content_type="application/pdf"), filename="doc.pdf")
    fd["options"] = json.dumps(opts)
    resp = await js.fetch(url, to_js({"method": "POST", "body": fd.js_object,
                                      "headers": {"Authorization": f"Bearer {key}"}},
                                     dict_converter=Object.fromEntries))
    ```
    Alternativa: multipart construido a mano como `bytes` con boundary explícita vía `workers.fetch`. Encapsular en un adaptador `infra/` con puerto.

## 3. Subida de PDF de ~19 MB

- Body máximo: 100 MB (Free/Pro), 200 MB (Business) (https://developers.cloudflare.com/workers/platform/limits/). 19,5 MB llegó al Worker Python y a R2 en local.
- URLs prefirmadas R2 requieren credenciales S3 API y SigV4 (https://developers.cloudflare.com/r2/api/s3/presigned-urls/), CORS en el bucket y no admiten POST de formularios: más secretos sin beneficio para un único administrador.
- Recomendación: subida autenticada a la API Worker con cuerpo binario `application/pdf` (≤ 25 MB), validación (`%PDF`, tamaño, sha256), `put` en R2 `raw/{sha256}.pdf`, fila D1 `uploaded`, `INGESTION.create(...)` solo con ids.
- Memoria del isolate: 128 MB. Pasar `request.js_object.body` directo a `put` evitaría la copia (no verificado en Python).

## 4. Bundle y arranque

- Se ejecuta el módulo principal y sus imports al desplegar y se guarda un snapshot (https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/). Límites: 64 MiB sin comprimir, 1 s de arranque.
- Tamaños observados: `apps/rag/python_modules` 26 MB (fastapi, pydantic, langchain-core), `apps/api` 11 MB, Worker solo con SDK 112 KiB.
- Recomendación: el Worker de ingesta **sin FastAPI** (`WorkflowEntrypoint` + `Default` 404 + funciones puras, dataclasses del stdlib).

## 5. Pruebas locales

- `uv run pywrangler dev --local --port 8789` desde `workers/ingestion`: Workflow, D1 y R2 locales funcionan (verificado).
- **Vectorize no tiene emulación local** ("needs to be run remotely"; https://developers.cloudflare.com/workers/development-testing/). Usar puerto `VectorIndex` con fake en memoria, o `"remote": true` con cuenta.
- Dos `pywrangler dev` (ingesta y API) se enlazan solos por dev registry (verificado). Rareza: `get(id)` solo funciona desde el binding que creó la instancia (cada directorio tiene su `.wrangler/state`).
- No ejecutados (documentados en https://developers.cloudflare.com/workflows/build/local-development/): `npx wrangler workflows instances describe ingestion <ID> --local --port 8789`, `npx wrangler workflows trigger ingestion --local`, explorer en `http://localhost:8789/cdn-cgi/local/explorer`, `npx wrangler d1 migrations apply <db> --local`, `npx wrangler r2 object put <bucket>/raw/x.pdf --file=x.pdf --local`.
- pytest en CPython con fakes; no importar `js` ni `workers` fuera de `worker.py` e `infra/`.

## 6. Estructura propuesta para `workers/ingestion`

```
workers/ingestion/
  pyproject.toml / uv.lock / pylock.toml   # workers-runtime-sdk==1.9.6; dev: pytest, ruff, workers-py
  wrangler.jsonc        # python_workers; workflows; d1; r2; vectorize; vars; secrets FIRECRAWL_API_KEY, AI_GATEWAY_API_KEY
  src/
    worker.py           # IngestionWorkflow(WorkflowEntrypoint) + Default 404; Settings solo aquí
    unitropico_ingestion/
      core/settings.py, core/logger.py
      domain/           # Document, OcrPage, Chunk, EmbeddingBatch, IngestionStatus; errores
      services/         # run_ocr, chunk_document, embed_chunks, index_chunks
      ports.py          # BlobStore, OcrClient, Embedder, ChunkRepository, VectorIndex, Clock
      infra/            # r2_store, firecrawl (js.fetch multipart), gateway_embeddings, d1_repository, vectorize_index
  tests/
```

Pasos del Workflow (cada uno devuelve < 1 MiB): `mark_processing` → `ocr` (R2 → Firecrawl → `ocr/{sha}.json`; timeout amplio, 2 reintentos, 4xx `NonRetryableError`) → `chunk` (`chunks/{sha}.jsonl` + D1/FTS5 por `batch`) → `embed_N` (lotes de ~100) → `index` (upsert Vectorize) → estado final `ready`/`review_required` (la publicación es manual, ver `docs/IMPLEMENTATION_STATUS.md`).

## 7. Grado de verificación

- **Docs**: open beta y flags; `step.do`, `sleep`, `wait_for_event`, `ctx`, `NonRetryableError`; `create/get/status/restart/create_batch`; `script_name`; `already_exists`; límites de Workflows, Workers, R2 y Vectorize; Vectorize sin emulación local; presigned con credenciales S3; endpoint de embeddings.
- **Código del SDK 1.9.6**: `_EnvWrapper`/`_BindingWrapper`, `_WorkflowBindingWrapper`, dependencias implícitas, `NonRetryableError`, `workers.fetch` delega en `pyfetch`.
- **Ejecución local**: Workflow completo con dependencias, `ctx`, retries y `sleep`; `already_exists`, `restart`, `errored`; llamada entre Workers; D1 `all/run/first/batch/exec`/FTS5; R2 con 19,5 MB y body entrante de 19,5 MB; multipart con `js.fetch` y bug de `workers.fetch`; fetch JSON; error de Vectorize en local.
- **No verificado**: retorno de `query`/`upsert` de Vectorize y conversión de `filter`; límite de 1 MiB y CPU en producción; memoria y arranque desplegados; streaming de `request.body` a R2; `obj.blob()` directo a FormData; `restart()` frente a pasos cacheados en producción; contrato concreto de Firecrawl Parse; comandos `wrangler workflows ... --local`.
