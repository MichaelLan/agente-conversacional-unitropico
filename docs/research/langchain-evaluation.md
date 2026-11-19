# Evaluación de LangChain para el RAG en Cloudflare Python Workers

**Fecha:** 2026-11-18. **Alcance:** `apps/rag` (Python Worker, contrato `/answer` v1) y la ingesta (`workers/ingestion`, Python Worker + Workflow). Este documento no modifica código. Las pruebas se hicieron en un directorio temporal fuera del repo, sin inferencia, embeddings ni despliegue de pago. Solo se usó un servidor local falso compatible con OpenAI.

Niveles de evidencia usados en todo el documento:

- **[E] verificado empíricamente en workerd local.** Se ejecutó con `pywrangler dev --local` y la resolución WASM real de pywrangler.
- **[D] verificado en documentación o código fuente.** Hay documentación oficial, metadatos de PyPI o código leído del paquete instalado, pero no se ejecutó.
- **[N] no verificado.**

---

## 0. Resumen ejecutivo

1. **LangChain sí funciona hoy en Python Workers, pero no todo y no "tal cual".**
   - Funcionan **[E]** `langchain-core` 1.8.3, `langchain-openai` 1.8.3 (con un ajuste obligatorio), `langchain-text-splitters` 1.1.3, `langchain-classic` 1.0.8, `langchain-community` 0.4.2 con `rank-bm25` y `langchain-cloudflare` 0.4.3 con bindings.
   - **No se instalan** **[E]** `langgraph` 1.2.22 ni `langchain` 1.5.2. Ambos dependen de `ormsgpack`, que no tiene wheel WASM.
2. **Trampas reales encontradas al ejecutar:**
   - Toda llamada **síncrona** que LangChain paraleliza con hilos falla con `RuntimeError: can't start new thread`. Ocurre con `RunnableParallel.invoke` y con el auto-batch de LangSmith. Las variantes `ainvoke`, `abatch` y `astream` funcionan.
   - `ChatOpenAI()` y `OpenAIEmbeddings()` **fallan al construirse** con su cliente HTTP por defecto (`ModuleNotFoundError: httpcore2`). Funcionan si se les inyecta `http_client=httpx2.Client()` y `http_async_client=httpx2.AsyncClient()`.
   - `OpenAIEmbeddings` descarga en tiempo de ejecución el BPE de tiktoken desde internet salvo que se use `check_embedding_ctx_length=False`.
3. **Qué aporta y qué no.** Lo que LangChain hace claramente mejor aquí es estandarizar:
   - interfaces (`Document`, `BaseRetriever`, `Embeddings`, `BaseChatModel`, `ChatPromptTemplate`, LCEL asíncrono);
   - generación de `response_format` `json_schema` estricto desde Pydantic (`with_structured_output`);
   - el ecosistema de trazas y evaluación (LangSmith).

   Lo que es requisito del proyecto **no lo ofrece**: citas solo desde metadatos verificados, quotes literales, revalidación de publicación y vigencia, y abstención controlada. Tampoco ofrece la búsqueda léxica FTS5 en D1, ni RRF con trazabilidad de rangos, ni chunking normativo con páginas. Todo eso se escribe igual. Los componentes "listos" (`MarkdownHeaderTextSplitter`, `EnsembleRetriever`, `BM25Retriever`, `CloudflareVectorize`, `create_retrieval_chain`) estorban o son insuficientes en puntos concretos (§3).
4. **Recomendación: opción B.** `langchain-core` como columna vertebral de interfaces, con adaptadores propios sobre bindings y `workers.fetch`. Se conservan intactas las validaciones de citas y catálogo. `langchain-openai` queda como alternativa medible detrás de la misma interfaz, no como dependencia inicial. `langchain-text-splitters` solo en la ingesta y como divisor secundario. Se descartan LangGraph (no instala y no hay necesidad), `langchain` 1.x, `langchain-classic`, `langchain-community` y el vector store de `langchain-cloudflare`. Plan incremental en §6.4.

---

## 1. Método y entorno

| Elemento | Valor |
|---|---|
| Host | Linux (WSL2), CPython 3.14.6 vía uv, Node 24.15.0 |
| uv | 0.13.9 aislado (`uvx --from uv==0.13.9 uv …`). El uv global 0.13.0 no se tocó. |
| Herramientas | workers-py (pywrangler) 1.17.9, workers-runtime-sdk 1.9.6, wrangler 4.165.0 |
| Runtime | workerd local de wrangler; Pyodide 314.0.7 (CPython 3.14.2 emscripten); índice `https://index.pyodide.org/314.0.7` |
| `compatibility_date` / flags | `2026-11-08`, `["python_workers"]` (igual que `apps/rag/wrangler.jsonc`) |
| Red | Solo un servidor `http.server` local falso en `127.0.0.1:9999` que imita `/v1/chat/completions` y `/v1/embeddings` y registra cada petición. Hubo una descarga pública gratuita del BPE de tiktoken (§2.2). |
| Sin | `.env`, credenciales, inferencia real, embeddings reales, `deploy` (solo `deploy --dry-run` para medir el bundle) |

Workers de prueba creados (fuera del repo):

| Worker | Dependencias |
|---|---|
| w1 | core + openai + text-splitters |
| w2 | core + classic + community + rank-bm25 (con langgraph primero, que falló) |
| w3 | langchain-cloudflare + D1 local |
| w4 | `langchain` 1.5.2 |
| w5 | fastapi + pydantic + core + openai, para medir tamaño e import |

Cada prueba captura excepciones y avisos (`warnings`) dentro del Worker y devuelve JSON.

---

## 2. Compatibilidad real en Python Workers (noviembre de 2026)

### 2.1 Tabla de compatibilidad

| Paquete / API (versión) | Resultado | Evidencia |
|---|---|---|
| `langchain-core` 1.8.3: `ChatPromptTemplate` + `MessagesPlaceholder`, LCEL (`RunnableParallel`, `.assign`, `RunnableLambda`), `Document`, `BaseRetriever` propio, `BaseChatModel` propio, `Embeddings` propio, `PydanticOutputParser`, `GenericFakeChatModel.astream` | Funciona con `ainvoke`, `abatch` y `astream` | **[E]** |
| `langchain-core`: `RunnableParallel.invoke` (síncrono) | **Falla**: `RuntimeError: can't start new thread` (usa `ContextThreadPoolExecutor`). Una secuencia síncrona sin paralelo sí funciona. | **[E]** |
| `langchain-core`: `with_structured_output` en un `BaseChatModel` propio | `NotImplementedError`: hay que implementar `bind_tools` o hacer el parseo uno mismo | **[E]** |
| `langchain-core`: `InMemoryVectorStore` | Falla sin numpy (`cosine_similarity requires numpy`). Irrelevante en producción (se usa Vectorize). | **[E]** |
| `langchain-openai` 1.8.3: `ChatOpenAI(base_url=…)` con el cliente por defecto | **Falla al construir**: `httpx2.create_ssl_context` importa `httpcore2`, que no existe en emscripten | **[E]** |
| `ChatOpenAI` con `http_client=httpx2.Client()`, `http_async_client=httpx2.AsyncClient()` | Funciona: `ainvoke`, `invoke` síncrono (JSPI disponible en workerd), `with_structured_output(method="json_schema", strict=True, include_raw=True)` y `method="function_calling"`, `usage_metadata` | **[E]** |
| Petición que envía `ChatOpenAI` | `model`, `messages`, `max_completion_tokens` (no `max_tokens`), `reasoning_effort: "low"`, `response_format.json_schema.strict: true` con `additionalProperties: false` | **[E]** (registro del servidor falso) |
| `OpenAIEmbeddings(dimensions=1536, check_embedding_ctx_length=False, http_*_client=httpx2…)` | Funciona (`aembed_query`, `aembed_documents`). Envía `encoding_format: "base64"`. | **[E]** |
| `OpenAIEmbeddings` con `check_embedding_ctx_length=True` (por defecto) / `tiktoken` 0.12.0 (wheel de Pyodide) | Funciona, pero **descarga el BPE cl100k en tiempo de ejecución** (≈2 s, `requests` síncrono) | **[E]** |
| Aceptación de `max_completion_tokens` y `encoding_format=base64` por Vercel AI Gateway | Vercel declara implementar "la misma especificación" que OpenAI Chat Completions | **[D]** / **[N]** contra el gateway real |
| `httpx` 0.28.1 (Pyodide) / `httpx2` 2.16.2 + `httpx2-jsfetch` 1.0 | `httpx.AsyncClient().post` funciona sobre fetch. `httpx2-jsfetch` aborta el fetch JS con `AbortController` cuando vence el timeout. | **[E]** / **[D]** (código) |
| `langchain-text-splitters` 1.1.3: `RecursiveCharacterTextSplitter(add_start_index=True)`, `MarkdownHeaderTextSplitter` | Funciona (puro Python). Limitaciones de fidelidad en §3. | **[E]** |
| `langchain-classic` 1.0.8: `EnsembleRetriever` (RRF ponderado, `c=60`, `id_key`) | Funciona (async y sync) | **[E]** |
| `langchain-classic`: `create_history_aware_retriever`, `create_retrieval_chain`, `create_stuff_documents_chain` | Funciona con modelo falso | **[E]** |
| `langchain-community` 0.4.2 + `rank-bm25` 0.2.2 (+ numpy 2.4.6 de Pyodide): `BM25Retriever` | Funciona. Emite `DeprecationWarning: langchain-community is being sunset`. | **[E]** |
| `langchain-cloudflare` 0.4.3: `CloudflareVectorize(binding=…, d1_binding=env.DB)` | Funciona con un binding Vectorize simulado (Vectorize no corre en local) y D1 **local real**, vía SQLAlchemy + `run_sync`. Envía `{"topK", "returnMetadata"}` al binding. | **[E]** (D1) / **[N]** (Vectorize real) |
| D1 local: FTS5 `tokenize='unicode61 remove_diacritics 2'` | "matricula" encuentra "matrícula" | **[E]** |
| `langgraph` 1.2.22 | **No instala**: `langgraph-checkpoint>=4.3.0` exige `ormsgpack>=1.12.0`, sin wheel PyEmscripten en PyPI ni en el índice de Pyodide | **[E]** (resolución de pywrangler) |
| `langchain` 1.5.2 | **No instala** (depende de `langgraph>=1.2.12,<1.3.0`, mismo motivo) | **[E]** |
| LangSmith 0.15.0: `@traceable` con trazas desactivadas | Funciona | **[E]** |
| LangSmith con trazas activas, `Client(auto_batch_tracing=False)` | Funciona: `POST/PATCH /runs` **síncronos**, uno por evento, en el camino de la petición | **[E]** (contra el endpoint falso) |
| LangSmith con el auto-batch por defecto | **Falla**: `RuntimeError: can't start new thread` | **[E]** |
| Arranque en producción, tamaño del snapshot y memoria de 128 MB | No medibles sin desplegar | **[N]** |

Fuentes de versión y dependencias: `https://pypi.org/pypi/<paquete>/json` para cada paquete citado, consultadas el 2026-11-18. Ejemplos: [langchain-core](https://pypi.org/pypi/langchain-core/json), [langchain-openai](https://pypi.org/pypi/langchain-openai/json), [langgraph](https://pypi.org/pypi/langgraph/json), [langgraph-checkpoint](https://pypi.org/pypi/langgraph-checkpoint/json), [ormsgpack](https://pypi.org/pypi/ormsgpack/json), [openai](https://pypi.org/pypi/openai/json) (depende de `httpx2`), [httpx2](https://pypi.org/pypi/httpx2/json) (usa `httpx2-jsfetch` en emscripten).

Sobre la página "LangChain en Workers" de 2024 (versiones 0.1.x): hoy la [página de Python Workers](https://developers.cloudflare.com/workers/languages/python/) solo dice "Packages, including FastAPI, Langchain, Pydantic and more". La [guía de paquetes](https://developers.cloudflare.com/workers/languages/python/packages/) admite paquetes puros, wheels PyEmscripten y los incluidos en Pyodide, y advierte que el soporte WASM es inmaduro. **Ninguna certifica versiones concretas.** La tabla anterior es la evidencia actual. Una nota secundaria ([InfoQ, 2026-09-29](https://infoq.com/news/2026/09/cloudflare-python-workers-ga)) atribuye a que `requests` y `httpx` usen fetch el hecho de que "OpenAI, LangChain y MCP" funcionen en Workers. Coincide con lo observado.

### 2.2 Hallazgos que condicionan el diseño

1. **Solo API asíncrona.** En Workers no hay hilos ([stdlib de Python Workers](https://developers.cloudflare.com/workers/languages/python/stdlib/)). LangChain usa `ThreadPoolExecutor` en rutas síncronas (paralelos, `batch`, auto-batch de LangSmith).
   - El event loop de Pyodide resolvió sin error los `run_in_executor` internos de `ainvoke`; las lambdas síncronas dentro de una cadena asíncrona funcionaron.
   - Regla: **nunca `invoke`/`batch`/`stream` en código del Worker**. Conviene un test estructural que lo prohíba.
2. **`langchain-openai` necesita clientes `httpx2` explícitos.** Funciona, pero depende de un detalle interno: el constructor de cliente por defecto llama a `create_ssl_context`. Una versión futura podría arreglarlo o romper la solución.
   - Peso añadido: `openai` ocupa 15,6 MB y `langchain_openai` 0,75 MB descomprimidos.
3. **tiktoken en tiempo de ejecución.** Con `check_embedding_ctx_length=True`, cada isolate frío descarga ≈1,7 MB desde `openaipublic.blob.core.windows.net` con `requests` síncrono. Debe desactivarse: el presupuesto de entrada se controla en el propio chunking.
4. **`langchain-community` está en retirada.** El anuncio oficial ([issue #674](https://github.com/langchain-ai/langchain-community/issues/674), 2026-05-22) es de efecto inmediato y el repositorio está archivado desde el 2026-06-19. No debe entrar como dependencia nueva de una tesis.
5. **`langchain-cloudflare` (mantenido por Cloudflare, [PyPI](https://pypi.org/project/langchain-cloudflare/), [GitHub](https://github.com/cloudflare/langchain-cloudflare)) sí admite bindings.** Es así para `CloudflareVectorize(binding=env.VECTORIZE, d1_binding=env.DB)`, el reranker de Workers AI y AI Search; sin binding usa la REST API con token de cuenta. Del código leído salen tres problemas para nuestro caso:
   - el D1 tiene un esquema fijo (`id, text, namespace, metadata`);
   - los metadatos del `Document` salen **solo de Vectorize** (máx. 10 KiB, posiblemente desactualizados) y la columna `metadata` de D1 se ignora;
   - si falta la fila en D1, devuelve `page_content=""` **en silencio** (`_combine_vectorize_and_d1_data`).

   Lo segundo y lo tercero chocan con "fallar cerrado" y con la revalidación de catálogo. Además no hay FTS5 ni híbrido.
6. **LangSmith dentro del Worker** solo funciona en modo síncrono por evento, lo que añade latencia a cada petición. Lo correcto es mantener las trazas apagadas en el Worker (como hoy fuerza la composición) y trazar en el **arnés de evaluación** en CPython, donde el auto-batch funciona.
7. **Aviso persistente** `DeprecationWarning: 'asyncio.iscoroutinefunction' …` en Python 3.14. No proviene de `langchain_core`: en el código instalado solo aparece en `langsmith/evaluation/_arunner.py` y `_beta_decorator.py`, sin origen confirmado. No es bloqueante.

### 2.3 Tamaño y arranque

Medido con `pywrangler deploy --dry-run` (no despliega):

| Conjunto | Módulos vendorizados (sin comprimir) | gzip |
|---|---|---|
| core + openai + text-splitters (w1) | 33,9 MiB (3.570 módulos) | 7,2 MiB |
| fastapi + pydantic + core + openai (w5, "opción C" sin splitters) | 35,1 MiB | 7,4 MiB |
| core + classic + community + numpy + rank-bm25 (w2) | 52,7 MiB (4.886 módulos) | — |
| langchain-cloudflare (w3) | 29,7 MiB | — |
| Referencia: `apps/rag/python_modules` actual (fastapi + pydantic + core 1.7.6) | ≈26 MB en disco | — |

Límites oficiales ([límites de Workers](https://developers.cloudflare.com/workers/platform/limits/)):

- **64 MiB sin comprimir**; "There is no compressed size limit".
- **arranque de 1 s**: "A Worker must parse and execute its global scope … within 1 second";
- **128 MB de memoria** por isolate.

En Python, el código de nivel superior y sus imports se ejecutan **al desplegar** y se guarda un snapshot de memoria que se restaura en frío ([cómo funcionan los Python Workers](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)). Cloudflare publica 1.027 ms de arranque en frío medio importando httpx+fastapi+pydantic con snapshot y ≈10 s sin él ([changelog 2025-12-08](https://developers.cloudflare.com/changelog/post/2025-12-08-python-cold-start-improvements/)). No documenta límite de tamaño del snapshot.

Tiempos de import observados:

| Módulo | workerd local, sin snapshot (3 isolates fríos) | CPython host |
|---|---|---|
| pydantic | 0,31–0,35 s | — |
| fastapi | 1,5–1,6 s | — |
| `langchain_core` (prompts/runnables/models/retrievers) | 3,5–5,6 s (dominado por la cadena langsmith/zstandard/orjson) | 0,44 s |
| openai | 1,1–1,6 s | 0,35 s |
| `langchain_openai` | 0,7–0,9 s | 1,26 s (incluye core + openai) |
| Total local de w5 | 7,6–9,4 s | — |

Un cuarto intento devolvió todo a 0 ms por reutilización del isolate; se descartó.

Conclusión: el **tamaño cabe con holgura** en las opciones B y C. El **arranque en producción no se puede afirmar** sin desplegar **[N]**. El core ya está en el lock actual, así que la opción B no añade coste de import respecto al estado actual. La opción C suma ≈2–2,5 s de import local (unos 16 MB de openai) que acabarían en el snapshot. Antes del primer despliegue hay que medir el arranque real del Worker y la memoria.

---

## 3. Mapa componente por componente

Leyenda: **Mejor** = LangChain aporta claramente; **Igual** = equivalente a lo propio; **Estorba** = choca con requisitos o añade riesgo.

| Pieza | Qué ofrece LangChain exactamente | Encaje con nuestras restricciones | Qué hay que escribir igualmente | Veredicto |
|---|---|---|---|---|
| **Chunking estructural** de normativa (artículos, parágrafos, tablas, `page_start`/`page_end`) | `MarkdownHeaderTextSplitter` (metadatos por encabezado), `RecursiveCharacterTextSplitter` (`add_start_index`, separadores configurables) | **[E]** El divisor por encabezados **reescribe el texto**: une encabezados con `"  \n"`, por lo que el chunk deja de ser subcadena exacta de la extracción. No conoce páginas. El recursivo **partió una tabla** separando la cabecera de sus filas. `start_index` es relativo al sub-documento, no al original. | Parser de Artículo/Capítulo/Parágrafo en español, mapa de offsets a página (pageMarkers/blocks de Firecrawl), tablas como unidad atómica, ids estables y hash | **Estorba** como divisor principal. **Igual / útil** el recursivo como *fallback* para artículos demasiado largos, en la ingesta. |
| **Embeddings** `text-embedding-3-small` 1536 vía gateway | Interfaz `Embeddings`. `OpenAIEmbeddings` (con `httpx2` y `check_embedding_ctx_length=False`) | **[E]** con servidor falso. Añade openai SDK (16 MB). Usa base64 por defecto **[N]** en el gateway. | Adaptador sobre `workers.fetch` (ya previsto en la ingesta), lotes, reintentos del Workflow | **Mejor la interfaz** `Embeddings`. **Igual** la implementación: un `GatewayEmbeddings(Embeddings)` propio de unas 40 líneas basta. |
| **Vector store Vectorize** | `CloudflareVectorize` (binding o REST) | Metadatos solo de Vectorize, D1 con esquema fijo, `page_content=""` silencioso, SQLAlchemy síncrono vía JSPI ([código, §2.2](#22-hallazgos-que-condicionan-el-diseño)) | Adaptador propio `env.VECTORIZE.query` → ids → catálogo D1 propio | **Estorba**. Usar la interfaz `BaseRetriever` con adaptador propio. |
| **Búsqueda léxica FTS5** (D1) | Nada para D1/FTS5. `BM25Retriever` (community) es en memoria. | **[E]** `BM25Retriever` exige cargar todo el corpus en cada isolate y tokeniza con `str.split()` (sin minúsculas, tildes ni puntuación). Con 4 documentos, `rank_bm25` dio IDF≈0 a "matricula" y devolvió un documento irrelevante. Paquete en retirada. | SQL FTS5 con `unicode61 remove_diacritics 2` ([D1 FTS5](https://developers.cloudflare.com/d1/sql-api/sql-statements/)), saneado de la consulta MATCH | **Estorba**. Lo propio es mejor. |
| **Fusión RRF** | `EnsembleRetriever` (classic): RRF ponderado ([Cormack et al., 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)), `c=60`, deduplicación por `id_key` | **[E]** Funciona, pero **descarta las puntuaciones y los rangos por fuente** (no quedan en metadatos), algo que la tesis necesita para el análisis de ablación. Arrastra `langchain-classic` (7 MB) + SQLAlchemy (8,9 MB) + requests. | RRF en unas 20 líneas, conservando `rank_vector`, `rank_fts` y `rrf_score` en metadatos | **Igual** en algoritmo; **estorba** por dependencias y opacidad |
| **Reranking** | `CloudflareWorkersAIReranker` (bge-reranker-base, binding `env.AI`). En el núcleo solo existe `BaseDocumentCompressor`. | Workers AI añade otro proveedor fuera del stack decidido; no se probó | Si se evalúa: adaptador al binding o al gateway | **Igual** (no prioritario; experimento posterior) |
| **Reformulación con historial** | `create_history_aware_retriever` (classic), o LCEL `prompt \| model \| StrOutputParser` | **[E]** La función classic funciona; es una composición trivial (sin historial pasa la pregunta tal cual). Con LCEL de core se hace en 5 líneas sin classic. | Prompt de reformulación en español, límites, evaluación del efecto (se activa solo si mejora) | **Igual**. LCEL de core basta. |
| **Construcción de contexto** | `create_stuff_documents_chain` (classic) / `format_document` | Nuestro contexto es JSON con vigencia, versión, páginas y `fecha_consulta`, con presupuesto (`_fit`) que descarta historial y luego fuentes de menor rango. Classic no lo hace. | Se conserva `_content`/`_fit` | **Estorba** classic; se mantiene lo propio |
| **Prompt** | `ChatPromptTemplate`, `MessagesPlaceholder`, plantillas versionables | Encaja: el historial puede seguir siendo **dato** en el JSON de usuario (decisión anti-inyección actual) | El texto del prompt (ya existe en `services/prompt.py`) | **Mejor (moderado)**: estandariza, versiona y traza |
| **LLM con json_schema estricto + reasoning_effort** | `ChatOpenAI(reasoning_effort=…)` + `with_structured_output(Schema, method="json_schema", strict=True, include_raw=True)` | **[E]** El esquema estricto se genera bien desde Pydantic (`additionalProperties:false`, todos `required`). **Pierde controles del `GatewayClient` actual**: `redirect="manual"`, rechazo de claves duplicadas, presupuesto de caracteres de entrada, clasificación cerrada de errores sin registrar payloads. Envía `max_completion_tokens` **[N]** en el gateway. **Gana** aborto real del fetch al vencer el timeout (`AbortController`), algo que el adaptador actual no hace (README de `apps/rag`, "No verificado"). | Mapeo de excepciones de openai a `GenerationError`; seguir validando `finish_reason`, `refusal` y `tool_calls` sobre el `raw` | **Igual o mejor** según se valore. Hay que medirlo con el gateway real antes de sustituir. |
| **Citas verificables** contra metadatos | Nada equivalente. Los ejemplos oficiales de "citations" devuelven ids o fragmentos que el modelo elige, sin verificación literal ni catálogo. | Requisito del proyecto | `quote_matches`, unicidad de `chunk_id`, mínimo de 12 caracteres, construcción de la cita **solo** desde el catálogo | **Propio** (se conserva) |
| **Abstención** (`no_evidence`, `model_abstained`, `invalid_output`, `invalid_citation`, `stale_source`) | Nada | Requisito | Se conserva `AnswerService` | **Propio** |
| **Vigencia / contradicciones** | Nada específico | Requisito | Metadatos de vigencia en el prompt, revalidación `is_admissible` antes y después de generar | **Propio** |
| **Tracing / observabilidad** | LangSmith (callbacks automáticos en LCEL, `@traceable`) | **[E]** En el Worker solo funciona síncrono y sin batch, con latencia por evento. Exporta pregunta y evidencia a un tercero, en contra de la política actual de no registrar preguntas ni evidencia. | Logging propio en el Worker (ya existe). LangSmith en el arnés offline. | **Mejor fuera del Worker**; **estorba** dentro |
| **Evaluación** | LangSmith: datasets, experimentos y evaluadores *correctness*, *relevance*, *groundedness* y *retrieval relevance* con un LLM juez estructurado ([tutorial RAG de LangSmith](https://docs.langchain.com/langsmith/evaluate-rag-tutorial)). RAGAS: *faithfulness*, *context precision/recall*, *response relevancy*, *noise sensitivity* ([métricas RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)). | Corren en CPython (host/CI), **no dependen** de que el runtime use LangChain: `evaluate(target, …)` acepta cualquier función, por ejemplo una que llame a `/answer`. RAGAS no es instalable en Workers (numpy, datasets, diskcache…) y no lo necesita. | Métricas deterministas propias: Recall@k/MRR por recuperador, tasa de citas válidas, exactitud de abstención, tasa `stale_source` | **Mejor** (estándar), independiente de la opción de runtime |

**Balance honesto:**

- **LangChain es claramente mejor en:** interfaces estándar, generación del esquema estricto desde Pydantic, ergonomía de prompts y cadenas asíncronas, integración de trazas y ecosistema de evaluación.
- **Es equivalente en:** RRF, reformulación, embeddings y cliente LLM. Este último tiene ventajas (abort, tipado, `usage_metadata`) y desventajas (controles perdidos, +16 MB, solución con `httpx2`).
- **Estorba en:** chunking normativo, Vectorize/D1 de `langchain-cloudflare`, BM25 de community, cadenas classic de contexto y respuesta, y trazas dentro del Worker.

Las piezas que distinguen este RAG (citas literales verificadas, catálogo vigente, abstención) **no existen en LangChain**. La afirmación "LangChain lo hace muchísimo mejor" no se sostiene para este proyecto. Sí se sostiene "LangChain da un vocabulario y un ecosistema estándar que conviene adoptar en las fronteras".

---

## 4. LangGraph

- **¿Funciona en Pyodide?** No **[E]**. `langgraph` 1.2.22 → `langgraph-checkpoint` ≥4.3.0 → `ormsgpack` ≥1.12.0, sin wheel PyEmscripten ([PyPI ormsgpack](https://pypi.org/pypi/ormsgpack/json) solo publica wheels manylinux/macOS/Windows). Por lo mismo tampoco instala `langchain` 1.x, cuya API de agentes (`create_agent`) vive sobre LangGraph ([guía de migración v1](https://docs.langchain.com/oss/python/migrate/langchain-v1)). El tutorial RAG oficial actual es agéntico y usa `deepagents` ([docs](https://docs.langchain.com/oss/python/langchain/rag)), también inviable aquí.
- **¿Aporta algo?** El flujo propuesto (fuera de alcance → reformular → recuperar → generar → validar citas → reintentar o abstenerse) es un **DAG corto con una sola rama de reintento acotada**. No hay estado persistente entre turnos (el historial llega en la petición y no se persiste), ni *human-in-the-loop*, ni herramientas elegidas por el modelo, ni bucles abiertos. Esas son las capacidades que justifican LangGraph (checkpoints, interrupciones, ciclos). Con 30–50 líneas de `async` explícito, o LCEL con `RunnableBranch`, se expresa el mismo flujo de forma más auditable, y cada transición termina en un motivo de abstención controlado.
- **Conclusión:** no hay necesidad demostrable y además no instala. Si en el futuro se quisiera un agente con herramientas, tendría que vivir fuera de Python Workers (otro runtime) o esperar wheels WASM de `ormsgpack`.

---

## 5. Valor académico

| Criterio | A: núcleo propio | B: core como columna vertebral | C: B + openai/splitters/ensemble |
|---|---|---|---|
| Reproducibilidad | Alta: pocas dependencias, todo fijado (`uv.lock` + `pylock.toml`) | Alta: core fijado; interfaces estables desde 1.0 | Media: más superficie y solución con `httpx2` sujeta a cambios internos |
| Reconocimiento y estándar de industria | Hay que explicar cada interfaz | Vocabulario reconocible (`Document`, `BaseRetriever`, `Embeddings`, LCEL) | Igual que B, y además clientes estándar |
| Comparabilidad con la literatura | Se compara por métricas, no por herramienta | Igual, y facilita reemplazar componentes (otro retriever, otro modelo) en ablaciones | Igual que B |
| Evaluadores estándar (LangSmith, RAGAS) | Utilizables **igual** (caja negra sobre `/answer`) | Igual, y trazas de componentes en el arnés offline (mismas clases ejecutadas en CPython) | Igual que B |
| Contribución propia visible | Total, pero puede leerse como "reinventar" | Clara: la aportación (verificación de citas, catálogo vigente, híbrido FTS5+Vectorize con trazabilidad) queda separada de la infraestructura estándar | Se diluye algo entre integraciones |
| Riesgo de obsolescencia durante la tesis | Bajo | Bajo-medio (core publica con frecuencia: 1.7.6 el 2026-11-05, 1.8.3 hoy) | Medio |

Para la memoria, la narrativa más defendible es la B: "se adoptan las abstracciones estándar de LangChain Core para que los componentes sean intercambiables y comparables. Se documenta empíricamente qué partes del ecosistema no son viables en el runtime WASM (LangGraph, community, cliente por defecto de openai, trazas síncronas). Se implementa de forma propia lo que el ecosistema no cubre: citas literales verificadas contra un catálogo vigente, abstención tipificada y recuperación híbrida FTS5+Vectorize con RRF trazable". La evaluación con LangSmith y RAGAS es válida en cualquier opción y conviene complementarla con métricas deterministas propias.

---

## 6. Opciones y recomendación

### 6.1 Opción A: núcleo propio sin LangChain

Se retira `langchain-core` del lock y se mantienen los `Protocol` actuales.

- **Pro:** mínimo bundle y arranque; control total; ya está implementado y probado.
- **Contra:** se pierde el vocabulario estándar y la integración natural con trazas y evaluación de componentes. Además contradice la decisión aprobada en `INVESTIGACION.md` ("RAG lineal: langchain-core").

### 6.2 Opción B (recomendada): langchain-core como columna vertebral, con adaptadores propios

Paquetes del Worker RAG (versiones **verificadas como instalables y ejecutables en Pyodide 314.0.7 [E]**):

- `langchain-core==1.8.3`, que sustituye a 1.7.6. Las pruebas se hicieron con 1.8.3; si se prefiere seguir en 1.7.6, hay que repetir esta batería con esa versión.
- Transitivas resueltas por pywrangler: `langsmith==0.15.0`, `langchain-protocol==0.0.21`, `uuid-utils==0.19.0`, `xxhash==4.1.0`, `orjson==3.11.8` (Pyodide), `zstandard==0.25.0` (Pyodide), `pydantic==2.14.3`, `pydantic-core==2.50.4`.
- Sin `langchain-openai`, `classic`, `community`, `cloudflare` ni `langgraph`.

Ingesta (`workers/ingestion`): opcionalmente `langchain-text-splitters==1.1.3`, solo para dividir artículos largos que excedan el tamaño objetivo, después del parser normativo propio y nunca reescribiendo el texto. Su interfaz `Document` permite compartir el tipo con el RAG.

**Qué se reemplaza y qué se conserva:**

| Código actual | Destino |
|---|---|
| `AsyncRetriever` (Protocol) y adaptadores Vectorize/FTS5 que escribe el otro agente | Se **envuelven** como `BaseRetriever` (`_aget_relevant_documents`) devolviendo `Document(id=chunk_id, page_content=text, metadata={…ChunkCandidate})`. El RRF propio se expone como `HybridRetriever(BaseRetriever)` que conserva `rank_vector`, `rank_fts` y `rrf_score`. No se reescriben los adaptadores. |
| `RetrievalService` (revalidación de catálogo, sobremuestreo ×3) | **Se conserva** íntegro; consume cualquier `BaseRetriever` vía `ainvoke`. |
| Cliente de embeddings (en curso, vía gateway) | Se envuelve como `GatewayEmbeddings(Embeddings)` (solo `aembed_*`), sobre `WorkersFetch`. |
| `services/prompt.py` | `SYSTEM_PROMPT` pasa a `ChatPromptTemplate`. El contenido de usuario sigue siendo el JSON de `_content` (historial como dato). |
| `GatewayClient` (192 líneas, validación estricta) | **Se conserva** y se expone como `GatewayChatModel(BaseChatModel)` con `_agenerate`, que delega en `complete()` y reutiliza `StructuredOutput`. Así puede sustituirse por `ChatOpenAI` o `GenericFakeChatModel` sin tocar el servicio. |
| `AnswerService` (`_fit`, `quote_matches`, catálogo, motivos de abstención) | **Se conserva**. El orden de pasos puede expresarse en LCEL asíncrono, pero la validación sigue siendo código propio y explícito. |
| `_retrieval_question` | Punto de extensión para una cadena LCEL `prompt | model | StrOutputParser` de reformulación, desactivada por defecto hasta que la evaluación muestre mejora. |
| Logging y desactivación de trazas en la composición | **Se conserva** (trazas apagadas en el Worker). |

**Riesgos:**

1. Uso accidental de API síncrona (hilos). Mitigación: test estructural que prohíba `.invoke(`, `.batch(` y `.stream(` en `src/`, y un smoke test en workerd.
2. Arranque y memoria en producción no medidos.
3. Ritmo de versiones de core: fijar versión y repetir la batería de §2 al actualizar.
4. LangSmith siempre importado por core (≈5 MB y la mayor parte del tiempo de import local).
5. La envoltura añade una capa; hay que mantenerla delgada para no duplicar validaciones.

### 6.3 Opción C: B + langchain-openai / text-splitters / EnsembleRetriever

Funciona técnicamente **[E]**: 35,1 MiB con fastapi y openai; 52,7 MiB si se añade classic y community. No se recomienda adoptarla en bloque:

- `ChatOpenAI` y `OpenAIEmbeddings` requieren la solución `httpx2` y pierden controles del cliente actual.
- `EnsembleRetriever` oculta las puntuaciones y arrastra classic, SQLAlchemy y requests.
- `BM25Retriever` y community están en retirada.

**`langchain-openai==1.8.3` sí merece un experimento controlado** detrás de la interfaz `BaseChatModel` de la opción B, comparado contra `GatewayChatModel` con el gateway real. Se mediría validez del JSON, `finish_reason`, latencia, aborto por timeout y aceptación de `max_completion_tokens`, `reasoning_effort` y base64. Requiere autorización de gasto. Si gana, se cambia la composición, no el servicio.

### 6.4 Plan de integración incremental (sin romper contratos HTTP ni tests)

Cada paso termina con `uv run pytest -q`, `ruff check` y `ruff format --check` en verde, y no toca `/answer` ni `/retrieval/query`.

1. **Coordinación.** No interferir con la oleada 2 en curso (D1 FTS5, Vectorize, embeddings, RRF). Este plan **envuelve** sus adaptadores después, no los reescribe.
2. **Dependencia.** Subir `langchain-core` a 1.8.3 en `pyproject.toml`, `uv sync --locked`, regenerar `pylock.toml` con `pywrangler sync` y repetir el smoke test en workerd. Alternativa: mantener 1.7.6 y repetir la batería de §2.
3. **Frontera de recuperación.** Añadir `infra/lc_retrievers.py`: `BaseRetriever` sobre los adaptadores de la oleada 2 y `HybridRetriever` (RRF trazable). Adaptar `RetrievalService` para aceptar `BaseRetriever` y mantener compatibilidad con el `Protocol` durante la transición. Tests con dobles: mismos resultados que antes y metadatos completos.
4. **Embeddings.** `GatewayEmbeddings(Embeddings)` asíncrono sobre `WorkersFetch`, con tests de contrato con fetch falso.
5. **Prompt y modelo.** `ChatPromptTemplate` para el sistema; `GatewayChatModel(BaseChatModel)` delegando en `GatewayClient`. `AnswerService` pasa a depender de `BaseChatModel` (o se mantiene `TextGenerator` con un adaptador). Los tests de `test_answer.py` y `test_gateway.py` no cambian de expectativas.
6. **Guardas.** Test estructural contra API síncrona y contra imports de `langchain_classic`, `langchain_community` y `langgraph` en `src/`. El test de trazas desactivadas se mantiene.
7. **Smoke test en workerd** (patrón de esta investigación): `pywrangler dev --local` con servidor OpenAI falso, `/answer` de extremo a extremo con retriever simulado, `answered` y `abstained`.
8. **Medición previa al primer despliegue:** `deploy --dry-run` (tamaño) y, cuando se autorice desplegar, arranque en frío y memoria.
9. **Arnés de evaluación offline** (CPython, fuera del Worker): dataset dev/final; LangSmith `evaluate` o RAGAS más métricas deterministas propias. Ablaciones: solo vector, solo FTS5, híbrido RRF, con y sin reformulación.
10. **Experimento opcional C:** `ChatOpenAI` frente a `GatewayChatModel` con el gateway real, con autorización de gasto.

---

## 7. Reproducción

Directorio de pruebas (temporal, fuera del repo; lo borra un reinicio):
`/tmp/claude-1000/-home-michaellan-Development-master-ia-tesis-agente-conversacional-unitropico/97009bcd-4ba2-44a7-a216-2be572d05fdf/scratchpad/langchain-research/`

- Workers `w1`…`w5`: `pyproject.toml`, `wrangler.jsonc`, `src/worker.py` y el `pylock.toml` generado.
- `tools/fake_openai.py`: servidor falso.
- `fake.log`: peticiones capturadas.

Comandos por Worker:

```sh
uvx --from uv==0.13.9 uv sync --group dev
uvx --from uv==0.13.9 uv run pywrangler sync
uvx --from uv==0.13.9 uv run pywrangler dev --local --ip 127.0.0.1 --port 879X
uvx --from uv==0.13.9 uv run pywrangler deploy --dry-run --outdir dist   # solo tamaño
python3 tools/fake_openai.py 9999 fake.log                               # servidor falso
```

Para conservar la evidencia, copiar ese directorio a una ubicación durable antes de reiniciar.
