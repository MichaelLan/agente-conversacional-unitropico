# RAG: recuperación y respuesta con citas (parcial)

Servicio interno (solo accesible por service binding desde `apps/api`) que
recupera evidencia institucional, pide al LLM una respuesta en texto plano y
valida cada cita contra el texto recuperado y el catálogo vigente.

**Estado real (2026-11-19):** el contrato `/answer` v1 está implementado con
adaptadores reales de D1 (FTS5 + catálogo), Vectorize y embeddings, fusionados
con RRF, orquestados sobre las interfaces de **LangChain Core 1.8.3**
(`BaseRetriever`, `Embeddings`, `BaseChatModel`, `ChatPromptTemplate`, LCEL
async). Probado en CPython con el esquema D1 real sobre sqlite3 y dobles de
Vectorize/gateway, y en `pywrangler dev --local` contra D1 local (recuperación
léxica). **Nunca se ha ejecutado contra Vectorize, el gateway real ni D1
remoto**; los recursos remotos no existen todavía.

## Contrato HTTP

- `GET /health` → `200 {"status":"ok"}` (vida local, no readiness).
- `POST /answer` — contrato v1 (fuente: `docs/IMPLEMENTATION_STATUS.md` §4).
- `POST /retrieval/query` — recuperación interna, sin generación.
- `/poc/query` y `/chat` no existen en el RAG (`404`).

### `POST /answer`

Petición (campos extra prohibidos en todos los niveles, tipos estrictos):

```json
{"question": "1..500 code points tras strip",
 "history": [{"role": "user|assistant", "content": "1..2000, no solo espacios"}]}
```

`history` es opcional (máx. 6 elementos; `null` se rechaza). No se persiste.

| HTTP | Cuerpo |
| --- | --- |
| `200 answered` | `answer` (texto plano en español), `citations` ≥ 1, `reason: null`, `referral: null` |
| `200 abstained` | `answer: null`, `citations: []`, `reason` ∈ `no_evidence`, `model_abstained`, `invalid_output`, `invalid_citation`, `stale_source`; `referral` fijo |
| `503` | `{"status":"unavailable","referral":"..."}`: falta infraestructura (adaptadores, clave, fetch) o falla el proveedor/catálogo |
| `422` | validación de FastAPI |

Cada cita: `chunk_id, document_id, document_title, version_id, version_label,
section, page_start, page_end, quote`. Todo sale del catálogo salvo `quote`, que
se valida contra el chunk. Los motivos de abstención significan:

- `no_evidence`: la recuperación funcionó pero no quedó evidencia admisible, **o** la mejor similitud semántica quedó por debajo de `MIN_RELEVANCE` si está activado (`below_threshold=true` en `answer_metrics`). No se llama al LLM.
- `model_abstained`: el modelo devolvió una abstención bien formada.
- `invalid_output`: JSON inválido, claves duplicadas o extra, `answered` sin texto o sin citas, abstención con contenido.
- `invalid_citation`: **ninguna** cita es válida. Las citas inválidas (`chunk_id` desconocido o repetido, `quote` no literal o demasiado corta) se **descartan** y la respuesta se devuelve con las restantes; solo si no queda ninguna se abstiene. `answer_metrics` registra `dropped_citations=<n>`.
- `stale_source`: el catálogo cambió durante la generación (despublicado, revisión, hash, páginas…).

**Validación de quote** (`services/quotes.py`, `match_quote`): quote y chunk
se pliegan igual, carácter a carácter: NFKD + `casefold`, sin tildes (el OCR las
pierde y el modelo las restaura; como en FTS5, `ñ` ≈ `n`), comillas tipográficas
→ rectas, guiones/rayas/menos → `-`, se eliminan guion blando, espacios de ancho
cero, BOM y marcas Markdown `*` y `` ` ``, la barra `|` de tablas cuenta como
espacio y los espacios se colapsan. A la quote se le recortan espacios,
puntuación y puntos suspensivos (`…`/`...`) de los extremos. Debe quedar como
**subcadena contigua** del chunk plegado con **al menos 12 caracteres no
blancos** (así no pasan “de” o “2026”, y sí “30 de junio de 2026”). Sin
similitud difusa: unos puntos suspensivos en medio, un dato cambiado o palabras
omitidas no coinciden. La cita devuelta es el **tramo del propio chunk** que
coincide (espacios colapsados, invisibles eliminados), no la grafía del modelo:
se muestra como evidencia y, tras el plegado, la del modelo podría diferir del
texto fuente (tildes, mayúsculas, comillas).

**Historial:** se envía al LLM como dato (`historial` dentro del JSON de usuario),
no como mensajes con rol. La generación recibe siempre la pregunta original y el
historial; lo que se usa para **recuperar** depende de `FOLLOWUP_STRATEGY` (ver
"Conversación y alcance"). Si el contenido excede el presupuesto se descartan
primero los turnos más antiguos y después las fuentes de menor rango; si ni una
fuente cabe, `503`.

**Prompt** (`services/prompt.py`, español): solo evidencia dada, citar por
`chunk_id` con quote literal, ante preguntas amplias ("qué becas/apoyos hay")
responder con lo que sí contienen las fuentes y aclarar que puede haber más,
abstenerse si no basta o si la pregunta no trata de
servicios, apoyos, programas, convocatorias o trámites de Bienestar
Universitario/Fomento Socioeconómico (aunque las fuentes tengan palabras
parecidas o se pudiera responder con conocimiento general; un seguimiento de un
tema de Bienestar del historial sí está en alcance),
exponer disposiciones distintas con su cita sin elegir, considerar la vigencia
(`vigencia`, `vigente_desde`, `vigente_hasta` frente a `fecha_consulta`), no
inventar requisitos/fechas/beneficios, texto plano sin Markdown. Por fuente se
envían título, versión, vigencia, páginas, sección y texto.

**Derivación:** texto fijo configurable por constructor (`DEFAULT_REFERRAL`),
sin teléfonos, correos ni ubicaciones inventados.

### `POST /retrieval/query`

`{"question": "1..500 tras strip", "limit": 1..20 (5)}`. Respuesta
`evidence_found | abstained` (200) o `unavailable` (503). Cada evidencia:
`document_id, version_id, chunk_id, source_sha256, page_start, page_end,
section, document_title, version_label, text`. El servicio pide
`limit × 3` candidatos al retriever y corta a `limit` tras revalidar contra el
catálogo (publicado, sin revisión pendiente, no sustituido, hash y ubicación
idénticos, `page_end ≥ page_start`).

## Arquitectura con LangChain Core (opción B)

Decisión (2026-11-18, `docs/research/langchain-evaluation.md` §6.2/§6.4):
`langchain-core` es la columna vertebral de **interfaces**; los adaptadores y
todas las validaciones son propios. Sin `langchain-openai`, `langchain-classic`,
`langchain-community`, `langchain-cloudflare`, `langgraph` ni
`langchain-text-splitters` (test de frontera y de locks en
`tests/test_structure.py`).

| Pieza | LangChain (estándar) | Propio (debajo) |
| --- | --- | --- |
| Recuperación | `ChunkRetriever(BaseRetriever)` (`services/retriever.py`), `ainvoke(q, k=n)` → `Document(id=chunk_id, page_content=texto, metadata=…)` | `HybridRetriever` (RRF trazable), `D1LexicalRetriever` (FTS5), `VectorRetriever` (Vectorize) |
| Embeddings | `GatewayEmbeddings(Embeddings)` (`infra/embeddings.py`), solo `aembed_*` | `GatewayEmbedder` (fetch, validación de vectores) |
| Generación | `GeneratorChatModel(BaseChatModel)` (`services/chat_model.py`), `_agenerate` | `GatewayClient` (json_schema strict, `reasoning_effort`, `max_tokens`, presupuesto, redirect manual, rechazo de truncado/refusal/tool calls) |
| Prompt | `ChatPromptTemplate` (`build_answer_prompt`): `SystemMessage` literal + `("human", "{content}")` | texto del prompt y JSON de usuario (`_content`/`_fit`, historial como dato) |
| Orquestación | LCEL async en `AnswerService`: `RunnablePassthrough.assign(message=prompt \| model) \| parse_candidate \| verify_citations \| revalidate_catalog` | parser estricto (claves duplicadas, `AnswerCandidate`), citas literales, revalidación de catálogo, motivos de abstención |

Metadatos de cada `Document`: `document_id, version_id, chunk_id,
source_sha256, page_start, page_end, section`, `rrf_score` y, por canal
configurado, `rank_<canal>`/`score_<canal>` (None si el canal no devolvió el
chunk; las puntuaciones son nativas: bm25 menor es mejor, coseno mayor es
mejor). `RetrievalService` reconstruye el `ChunkCandidate` desde el `Document`
y **sigue** revalidando en D1; los metadatos de cita salen solo del catálogo.

**Por qué así:** LangChain aporta vocabulario reconocible e intercambiable
(otro retriever u otro modelo, p. ej. `ChatOpenAI`, cambian la composición y no
el servicio) y componentes ejecutables en el arnés de evaluación. No ofrece lo
que distingue este RAG (citas literales verificadas, catálogo vigente,
abstención tipificada, FTS5+Vectorize con RRF trazable), así que eso sigue
siendo código propio; los envoltorios son capas delgadas sin lógica duplicada.
Las interfaces async simples (`search`, `embed`, `retrieve`, `complete`)
siguen existiendo y `RetrievalService`/`AnswerService` envuelven
automáticamente un `AsyncRetriever` o un `TextGenerator` planos.

**Trampas de Workers (obligatorias):**

- Solo `ainvoke`/`abatch`/`astream`. Las rutas síncronas de LangChain pueden
  usar hilos (`RuntimeError: can't start new thread` en Pyodide). Los métodos
  síncronos de los tres envoltorios (`invoke` → `_generate`/
  `_get_relevant_documents`, `embed_query`/`embed_documents`) lanzan
  `NotImplementedError("…async-only…")`. `test_structure.py` falla si `src/`
  llama a `.invoke(`, `.batch(`, `.stream(`, `.embed_query(`… o nombra
  `RunnableParallel`; `test_tracing.py` recorre `/answer` y `/retrieval/query`
  con `Thread.start` y `run_in_executor` parcheados para fallar.
- Trazas de LangSmith apagadas: la composición pone `LANGSMITH_TRACING`,
  `LANGCHAIN_TRACING_V2` y `LANGCHAIN_TRACING` a `false` **y** llama a
  `langsmith.configure(enabled=False)` (prevalece sobre la caché de entorno de
  LangSmith). `test_tracing.py` hereda las trazas activadas con clave falsa y
  verifica que no se crea `LangChainTracer` ni `langsmith.Client` ni se abre un
  socket. Trazar con LangSmith solo en el arnés offline (CPython).
- `cache=False` en el chat model (nunca la caché global de LLM).

## Recuperación

```
pregunta ─┬─ D1LexicalRetriever (FTS5 bm25, solo publicadas) ──┐
          └─ VectorRetriever (GatewayEmbedder → VECTORIZE) ────┴─ RRF ─ D1 load ─ RetrievalService (revalida en D1)
```

- **Léxico** (`infra/d1.py`): `build_fts5_query` (copia de
  `infra/d1/tests/fts_query.py`, test de deriva por AST) + `search_chunks` de
  `infra/d1/queries.sql` (copia literal, test de deriva). Si la consulta FTS
  queda vacía no se consulta D1.
- **Sinónimos FTS** (`infra/fts_synonyms.py`, `WELFARE_SYNONYMS`): tabla
  pequeña y direccional para el dominio. `beca(s)` → apoyo(s), auxilio(s),
  estímulo(s), subsidio(s), beneficio(s); `ayuda(s)` → apoyo(s), auxilio(s);
  formas de inscribir/postular (inscribo, inscribirme, inscripción, postularme,
  postulación…) → inscripción(es), inscribirse, postulación(es), postularse;
  `requisito` ↔ `requisitos`. Las palabras ya específicas del corpus (`apoyo`)
  no se expanden. Claves plegadas (sin tildes): el tokenizador `unicode61
  remove_diacritics 2` iguala `estímulo`/`estimulo`; no hay stemming, por eso
  se listan plurales. Máximo 12 términos extra; son literales fijos entre
  comillas (`quote_term`), nunca texto del usuario. Como FTS5 no pondera
  términos, si la expansión añade algo se lanzan **dos** consultas en paralelo
  (la de referencia y la ampliada) y los aciertos de sinónimos solo
  **rellenan** detrás de los exactos (`synonyms=None` lo desactiva).
- **Semántico** (`infra/vectorize.py`, `infra/embeddings.py`): un embedding por
  pregunta (`POST https://ai-gateway.vercel.sh/v1/embeddings`, valida nº de
  vectores, índice, longitud = `EMBEDDING_DIMENSIONS` y floats finitos; sin
  reintentos) y `VECTORIZE.query(vector, topK≤50, returnValues=False,
  returnMetadata="none")`. **Sin filtro por `status`**: los vectores se suben en
  `ready`/`review_required` y publicar solo cambia D1, así que el filtro
  ocultaría todo. Los ids de Vectorize son `chunks.id`.
- **Fusión** (`services/fusion.py`): `HybridRetriever` ejecuta los canales con
  `asyncio.gather`, pide `max(limit, 50)` a cada uno, fusiona por `chunk_id` con
  RRF (`1/(k+rango)`, `k=60` por defecto; desempates: mejor rango, orden de
  canal, id) y carga desde D1 (`v_chunk_registry` por `json_each`) los `limit`
  primeros. Si un canal falla se registra `retrieval_channel_failed
  channel=... error=Tipo` y sigue con el otro; si fallan todos → `503`.
- **Revalidación** (`services/retrieval.py`): `RetrievalService` consulta el
  catálogo en **una** lectura por lote (`get_current_many`, opcional en el
  Protocol) y aplica `is_admissible`. `AnswerService` vuelve a leer cada cita
  (`registry_by_chunk_id`) tras generar → `stale_source` si cambió. D1 es la
  única fuente de verdad: los vectores de versiones no publicadas se descartan.
  Coste por `/answer`: 1 FTS (2 si hay sinónimos) + 1 carga + 1 lote de
  catálogo + 1 por cita. Con `ANSWER_EVIDENCE_LIMIT=8` el servicio pide
  `8 × 3 = 24` candidatos (sobremuestreo para descartes del catálogo) y cada
  canal devuelve `max(24, 50) = 50` (tope `topK` de Vectorize), de sobra para 8.
- Interfaces async simples y sin FastAPI (`search(question, limit) →
  [RankedChunk]`, `embed(texts) → vectores`, `load(ids)`), envueltas como
  `BaseRetriever`/`Embeddings` de LangChain (ver arriba). `VectorRetriever`
  consume `aembed_query` (superficie de `Embeddings`).

## Conversación y alcance

Requisitos del proyecto: mantener el contexto durante una sesión e identificar
si una consulta corresponde al alcance. El historial (máx. 6 turnos) lo envía
el cliente; el RAG no persiste nada.

### Pregunta de recuperación: `FOLLOWUP_STRATEGY`

Una pregunta de seguimiento ("¿y cuáles son los requisitos?") no nombra el tema,
así que recuperar solo con ella falla. `AnswerService._retrieval_question`
decide el texto que va al retriever; **la generación sigue recibiendo la
pregunta original + historial**.

| Valor | Regla | Coste extra por consulta |
| --- | --- | --- |
| `none` | Solo la pregunta actual (comportamiento anterior). | 0 |
| `concat` (defecto de `Settings`) | Si el historial tiene algún turno `user` **y** la pregunta actual tiene **≤ 10 palabras** (separadas por espacios, `CONCAT_MAX_WORDS`), se recupera con `"<última pregunta del usuario> <pregunta actual>"` (espacios colapsados). La anterior se recorta por el final para no pasar de 500 caracteres; la actual nunca se recorta. En otro caso, la pregunta actual. | 0 |
| `rewrite` (**desplegado** en `wrangler.jsonc`) | Con historial no vacío, una cadena LCEL async (`build_rewrite_prompt` → chat model → parser estricto) pide al modelo una pregunta autónoma en español **sin responderla y sin añadir hechos** (del historial solo puede tomar el tema). Salida `{"question": str}` con `response_format` `json_schema` `strict` (`STANDALONE_QUESTION_SCHEMA`), cliente propio con `FOLLOWUP_REWRITE_MAX_TOKENS` (256) y `FOLLOWUP_REWRITE_TIMEOUT_SECONDS` (8 s, también como `asyncio.timeout` en el servicio). JSON inválido, claves duplicadas o extra, texto vacío o > 500 caracteres, error del proveedor o timeout → **fallback a `concat`** con `answer_rewrite_fallback error=<código>`. | 1 llamada al LLM por seguimiento (entrada ≈ prompt + historial, hasta ~4 000 tokens con 6 turnos de 2 000 caracteres; salida ≤ 256, incluido razonamiento) |

Por qué la regla de `concat`: es explícita, gratuita e independiente del idioma.
Limitar a preguntas cortas evita contaminar la recuperación cuando el
estudiante cambia de tema con una pregunta completa. **Limitación honesta:**
los 5 ítems `follow_up` del dataset tienen ≤ 10 palabras y empiezan por "¿Y…",
así que el dataset no distingue esta regla de "concatenar siempre"; no hay ítems
de cambio de tema con historial. En producción `concat` falló en seguimientos
reales (p. ej. "¿cómo me inscribo?" tras preguntar por un programa concreto),
así que `wrangler.jsonc` despliega `rewrite` (mismo `GENERATION_MODEL`, cliente
propio; ante cualquier fallo vuelve a `concat`). `Settings` mantiene `concat`
como defecto sin coste para desarrollo y tests. Los tokens del rewrite **no** se suman a
`AnswerResult.usage` (que sigue siendo el de la generación); se registran
aparte en `rewrite_tokens`.

### Umbral de relevancia: `MIN_RELEVANCE`

Opcional y **desactivado por defecto** (vacío/ausente): no hay corpus indexado
ni consultas reales para calibrarlo. Si se activa (`0 < MIN_RELEVANCE ≤ 1`), tras
la fusión y la revalidación se toma la **mejor similitud coseno del canal
semántico (Vectorize, métrica `cosine`)** entre las evidencias admisibles
(`RetrievalResult.relevance`, interno; `/retrieval/query` no lo expone); si es
menor que el umbral, `/answer` se abstiene con `no_evidence` sin llamar al LLM.

Por qué coseno y no otra señal:

- **RRF** solo usa rangos: el primer candidato siempre puntúa `1/(k+1)` (o el
  doble si coincide en ambos canales) sea cual sea su relevancia real; un
  umbral sobre RRF no detecta preguntas fuera de alcance.
- **bm25** de FTS5 no está acotado y depende de las estadísticas del corpus y
  del número de términos de la consulta OR; "normalizarlo" exigiría calibración
  por corpus y aun así variaría con la longitud de la pregunta.
- **Coseno** está acotado, no depende de la longitud de la consulta ni del resto
  del corpus y es la señal estándar de "nada parecido en el índice".

Límite: sin puntuación semántica (`RETRIEVAL_MODE=lexical`, `hybrid` degradado
o canal semántico caído) el umbral **no se aplica** (`threshold=unscored`) y se
sigue como hasta ahora; para evaluarlo hay que usar `hybrid` o `semantic`. El
contrato v1 fija los motivos de abstención, por eso no hay un motivo nuevo:
`no_evidence` incluye "por debajo del umbral".

Además, el prompt de sistema (regla 5) pide abstenerse explícitamente si la
pregunta no trata de Bienestar Universitario/Fomento Socioeconómico; eso cubre
lo que el umbral no ve (fuentes léxicamente parecidas a una pregunta ajena).

### Métricas y cómo evaluarlas (`evaluations/rag`)

`answer_metrics` añade, sin contenido: `followup_strategy` (configurada),
`followup_used` (`none|concat|rewrite`: qué regla produjo el texto
recuperado), `followup_fallback` (`true` si el rewrite falló),
`rewrite_ms`, `rewrite_tokens`, `threshold` (`off|pass|below|unscored`),
`below_threshold` y `dropped_citations` (citas descartadas por inválidas o
repetidas). Con `RAG_LOG_LEVEL=INFO`.

1. **Seguimientos:** arrancar el RAG con `FOLLOWUP_STRATEGY=none`, correr
   `answer --label fu-none --category follow_up`; repetir con `concat` y
   `rewrite`; `compare`. Comparar conducta, exactitud y cobertura de citas en
   `follow_up` (y en `in_scope` para comprobar que no empeora). Para `rewrite`,
   contar en los logs `followup_fallback=true` y sumar `rewrite_tokens` (coste
   extra). `retrieval` no sirve aquí: `/retrieval/query` no recibe historial.
2. **Fuera de alcance:** con `RETRIEVAL_MODE=hybrid`, correr `answer` con
   `--category out_of_scope --category in_scope` para varios valores de
   `MIN_RELEVANCE` (vacío, 0.2, 0.3, …) **solo en `dev`**; elegir el valor que
   maximiza la abstención en `out_of_scope` sin bajar la tasa de respuesta en
   `in_scope`, y congelarlo antes de medir `test`. `threshold=below` en los logs
   distingue la abstención por umbral de un `no_evidence` sin candidatos.
   Anotar la estrategia y el umbral en `--note` (el contrato no los expone).

### `RETRIEVAL_MODE` (evaluación de la tesis)

| Modo | Canales | Si falta `VECTORIZE`, fetch o `AI_GATEWAY_API_KEY` |
| --- | --- | --- |
| `hybrid` (defecto) | léxico + semántico + RRF | degrada a léxico con `retrieval_degraded mode=hybrid effective=lexical missing=...` |
| `lexical` | solo FTS5 | no aplica |
| `semantic` | solo Vectorize | `503` (`retrieval_unconfigured mode=semantic`): una evaluación nunca mide otro modo en silencio |

Sin el binding `DB` todo responde `503` (`retrieval_unconfigured missing=DB`).

## Configuración (Workers `env`)

`Settings.from_mapping` se construye **solo** en la composición (`worker.py`)
a partir de `self.env`; no lee `os.environ`. Valores en blanco o ausentes usan
el valor por defecto. Un valor inválido lanza `SettingsError` con los nombres de
campo, nunca con valores.

| Nombre | Tipo | Defecto |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | secret (`wrangler secret put`) | ausente ⇒ `/answer` 503 |
| `GENERATION_MODEL` | var | `openai/gpt-6-luna` |
| `GENERATION_REASONING_EFFORT` | var | `low` (`none…max`) |
| `GENERATION_MAX_TOKENS` | var | `2048` (incluye tokens de razonamiento) |
| `GENERATION_MAX_INPUT_CHARS` | var | `32000` (sistema + usuario; única fuente para servicio y gateway). Prompt de sistema ≈ 2 600 + 8 fuentes × ≈ 2 200 (chunks ≤ 1 800 caracteres en JSON con metadatos) ≈ 20 000; el resto queda para historial, que se descarta primero. Peor caso ≈ 8–9 k tokens de entrada |
| `AI_GATEWAY_URL` | var | `https://ai-gateway.vercel.sh/v1/chat/completions` |
| `AI_GATEWAY_TIMEOUT_SECONDS` | var | `20` |
| `ANSWER_EVIDENCE_LIMIT` | var | `8` (1..20) |
| `RAG_LOG_LEVEL` | var | `WARNING` |
| `RETRIEVAL_MODE` | var | `hybrid` (`lexical`, `semantic`) |
| `RETRIEVAL_RRF_K` | var | `60` |
| `EMBEDDING_MODEL` | var | `openai/text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | var | `1536` (1..1536; debe coincidir con el índice) |
| `EMBEDDING_URL` | var | `https://ai-gateway.vercel.sh/v1/embeddings` |
| `EMBEDDING_TIMEOUT_SECONDS` | var | `10` |
| `FOLLOWUP_STRATEGY` | var | `concat` (`none`, `rewrite`); `wrangler.jsonc`: `rewrite` |
| `FOLLOWUP_REWRITE_MAX_TOKENS` | var | `256` (16..2048; solo `rewrite`) |
| `FOLLOWUP_REWRITE_TIMEOUT_SECONDS` | var | `8` (solo `rewrite`) |
| `MIN_RELEVANCE` | var | vacío = desactivado (`0 < x ≤ 1`, coseno) |
| `DB` | binding D1 | ausente ⇒ `503` |
| `VECTORIZE` | binding Vectorize | ausente ⇒ ver `RETRIEVAL_MODE` |

`wrangler.jsonc` declara `workers_dev: false`, `preview_urls: false`, las vars
de modelo/modo, recuperación (`ANSWER_EVIDENCE_LIMIT=8`, `GENERATION_MAX_INPUT_CHARS=32000`, `FOLLOWUP_STRATEGY=rewrite`), `d1_databases` (`DB`, `database_name: unitropico`,
`database_id` **placeholder de ceros** a sustituir al crear la base,
`migrations_dir: ../../infra/d1/migrations`) y `vectorize` (`VECTORIZE`,
`index_name: unitropico-chunks-1536`, índice aún no creado). La misma clave
`AI_GATEWAY_API_KEY` sirve para generación y embeddings.

El gateway envía `reasoning_effort` (alias estándar de `reasoning.effort` según
la documentación de Vercel AI Gateway) y `response_format` `json_schema` con
`strict: true` y el esquema de `AnswerCandidate`. `finish_reason: "length"` se
clasifica como `truncated`. Redirecciones: `redirect="manual"` y todo lo que no
sea 200 se rechaza.

## Logging

`configure_logger` (llamado en la composición) añade un `StreamHandler` a stderr
con el nivel de `RAG_LOG_LEVEL`. Se registran fase, código y tipo de excepción
(`retrieval_failed phase=catalog error=RuntimeError`, `generation_failed
code=timeout`, `answer_abstained reason=...`). Nunca pregunta, historial, texto
de evidencia, payloads del proveedor ni secretos. La composición desactiva las
trazas de LangSmith (ver "Trampas de Workers").

Métricas para la evaluación: cada `/answer` configurado emite en **INFO**
(`RAG_LOG_LEVEL=INFO`):

```
answer_metrics status=answered reason=- sources=3 retrieval_ms=41.0 generation_ms=1830.2 validation_ms=4.1 prompt_tokens=2510 completion_tokens=212 total_tokens=2722 followup_strategy=concat followup_used=concat followup_fallback=false rewrite_ms=- rewrite_tokens=- threshold=off below_threshold=false dropped_citations=0
```

`retrieval_ms` excluye el rewrite (`rewrite_ms`); `generation_ms` cubre prompt + modelo; `validation_ms`, parser + citas +
revalidación. Los tokens vienen del `usage` del proveedor (`-` si no los
reporta). En Workers el reloj solo avanza con E/S (mitigación de Spectre), así
que las latencias reflejan sobre todo la E/S. **El contrato `/answer` v1 no
lleva `usage` ni tiempos** (la API valida estrictamente y prohíbe campos
extra): para medir costo desde el harness por HTTP habría que versionar el
contrato a v2 (p. ej. un objeto `diagnostics` opcional en RAG y API a la vez);
mientras tanto, el harness debe leer estos logs o ejecutar `AnswerService` en
CPython, donde `AnswerResult.usage` está disponible.

## Organización

- `src/worker.py`: `Default(WorkerEntrypoint)`; construye `Settings` y
  `Bindings` (`DB`, `VECTORIZE`) desde `self.env`, inyecta
  `WorkersFetch(workers.fetch)` y cachea la app por isolate (`AppCache`, se
  reconstruye si cambian los settings o la **presencia** de bindings; los
  objetos de binding de la primera petición se reutilizan, porque no cambian
  dentro de un isolate).
- `main.py`: `build_app(settings, fetch=, bindings=, retriever=, catalog=)`
  compone logger, trazas apagadas, adaptadores (`compose_retrieval` según
  `RETRIEVAL_MODE` → `ChunkRetriever(HybridRetriever)`), `GeneratorChatModel`
  sobre `GatewayClient` y servicios; `retriever`/`catalog` explícitos (tests) tienen prioridad;
  `create_app` solo enlaza rutas.
- `core/`: `settings.py` (validación) y `logger.py` (única dependencia transversal).
- `domain/`: evidencia (`is_admissible`, `to_evidence`), respuesta, generación.
- `services/`: `RetrievalService`, `AnswerService` (depende del Protocol
  `EvidenceRetrieval`; cadena LCEL), `fusion.py` (`HybridRetriever`, RRF con
  `FusedCandidate`), `retriever.py` (`ChunkRetriever`), `chat_model.py`
  (`GeneratorChatModel`), `prompt.py` (`ChatPromptTemplate`), `quotes.py`
  (`match_quote`).
- `infra/`: `gateway.py` (`GatewayClient`), `embeddings.py`
  (`GatewayEmbedder`), `d1.py` (puerto `D1Database`, `D1LexicalRetriever`,
  `D1EvidenceCatalog`), `vectorize.py` (puerto `VectorIndex`, `VectorizeIndex`,
  `VectorRetriever`), `fts_query.py`, `fts_synonyms.py`, `workers_fetch.py` (adaptador del fetch de
  Pyodide; convierte excepciones JS en `OSError`/`ValueError`).
- `api/`: DTO Pydantic estrictos separados del dominio (`answer.py`,
  `retrieval.py`, `routes.py`).

Ningún módulo de `api/`, `domain/`, `services/` o `infra/` importa `Settings`;
`domain/` y `api/` no importan LangChain (tests de frontera en
`tests/test_structure.py`).

## Comandos

Desde `apps/rag` (el uv global 0.13.0 es antiguo para pywrangler):

```sh
uvx --from uv==0.13.7 uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Los tests llaman ASGI directamente, sin red. `tests/d1_support.py` aplica
`infra/d1/migrations/*.sql` a sqlite3 en memoria detrás de un binding `DB`
falso (forma `{"results": [...]}`) y siembra datos respetando la máquina de
estados; si falta `infra/d1` (checkout parcial) los tests fallan con un mensaje
explícito. `tests/test_d1_integration.py` recorre `/answer` completo por
`build_app` con D1 real, Vectorize falso y un transporte falso que atiende
embeddings y chat. `rag.yml` se dispara también con cambios en `infra/d1/**`,
porque estos tests aplican la migración real.

Arranque local (sin cuenta; Vectorize no tiene emulación local y wrangler
avisa):

```sh
CI=1 uvx --from uv==0.13.7 uv run pywrangler d1 migrations apply DB --local
uvx --from uv==0.13.7 uv run pywrangler dev --local --port 8790
```

Verificado el 2026-11-18 (de nuevo con LangChain Core 1.8.3 y
`ChunkRetriever`): `/health` 200; `/answer` 503 sin clave; con la migración
aplicada y un chunk publicado sembrado en D1 local, `/retrieval/query`
devuelve `evidence_found` con título, versión, páginas y sección de D1 (FTS5,
`json_each`, conversión de resultados del SDK y `BaseRetriever.ainvoke`
reales en Pyodide) y `abstained` sin coincidencias.

Las pruebas que cruzan API → binding → RAG viven en el proyecto autónomo
`tests/integration/` (workflow `integration.yml`); `apps/rag` no depende de
`apps/api`.

## Dependencias y locks

Pines directos: Python 3.14, FastAPI 0.162.2, Pydantic 2.14.3,
**LangChain Core 1.8.3** (versión probada en workerd por la investigación;
transitivas WASM sin cambios: langsmith 0.15.0, langchain-protocol 0.0.21,
uuid-utils, xxhash, orjson, zstandard), workers-runtime-sdk 1.9.6; dev: pytest
9.2.0, Ruff 0.17.4, workers-py 1.17.9. `uv.lock` es el lock host y
`pylock.toml` el lock WASM generado por pywrangler (no editar a mano).
Regenerar tras cambiar `pyproject.toml`:

```sh
uvx --from uv==0.13.7 uv lock
uvx --from uv==0.13.7 uv sync --locked --group dev
CI=1 uvx --from uv==0.13.7 uv run pywrangler sync   # reescribe pylock.toml
```

Al actualizar `langchain-core`, repetir la batería de la investigación (§2) y
el smoke test en workerd.

Tamaño (`pywrangler deploy --dry-run`, 2026-11-18, con core 1.8.3): 1513
módulos, 21 817 KiB sin comprimir (64 MiB de límite), 5 229 KiB gzip. `python_modules/`,
`.venv-workers/` y `.wrangler/` son salida local ignorada.

## No verificado

- La cadena LCEL de `/answer` (prompt → `GeneratorChatModel` → parser →
  validación) **en workerd**: sin clave ni gateway falso HTTPS no se ejecutó
  generación en `pywrangler dev`; solo en CPython. Pendiente el smoke test del
  paso 7 de §6.4 (servidor OpenAI falso; requiere permitir una URL de gateway
  no HTTPS solo en local o un túnel TLS).
- Arranque en frío y memoria en producción con core 1.8.3 (import de
  `langchain_core`/langsmith ≈3,5–5,6 s en workerd local sin snapshot según la
  investigación).

- Forma real del retorno de `VECTORIZE.query` desde Python (conversión del SDK)
  y que acepte `returnMetadata="none"` como kwargs; el parseo es defensivo
  (dict o atributos) y falla cerrado.
- Llamada real a `/v1/embeddings` de Vercel AI Gateway con
  `openai/text-embedding-3-small` (formato `data[i].embedding`, `index`).
- D1 remoto (tokenizador `unicode61 remove_diacritics 2`, `json_each`) y el
  `database_id` real.
- Calidad de la fusión (k=60, límite por canal 50): pendiente del harness de
  evaluación con `RETRIEVAL_MODE`.
- Efecto real de `FOLLOWUP_STRATEGY` (`concat` vs `rewrite`) y valor de
  `MIN_RELEVANCE`: sin corpus indexado ni gateway real no hay mediciones; la
  regla de `concat` (≤ 10 palabras) no está calibrada y el umbral está apagado.
- Efecto en el corpus real de `ANSWER_EVIDENCE_LIMIT=8`, la expansión de
  sinónimos FTS, la tolerancia parcial de citas y el plegado de quotes: solo
  hay tests offline; falta medirlo con el harness.

- `WorkersFetch` sobre `workers.fetch` real hacia el gateway (en workerd solo
  se verificó `self.env`, D1 local y las rutas sin generación).
- Llamada real a Vercel AI Gateway (`reasoning_effort`, `response_format` strict
  y `max_tokens` con `openai/gpt-6-luna`); suficiencia de 2048 tokens.
- Que la cancelación por timeout aborte el fetch JS subyacente (solo cancela la
  espera en Python).
