# Alternativas OCR a Firecrawl Parse v2 para el corpus de Unitrópico

**Fecha:** 2026-11-19 (fecha de consulta de todas las fuentes web). **Alcance:** elegir qué OCR pilotar sobre ~10 páginas difíciles **ya auditadas** (lotes 01–03 del OCR selectivo v1) antes de gastar 61 páginas más (lotes 04–07: 12 + 12 + 17 + 20) en Firecrawl. Este documento no modifica código. No se leyó `.env`, no se hizo ninguna llamada de pago y no se creó ninguna cuenta. Solo se consultaron documentación y páginas de precios públicas. Los precios de Azure salen de la API pública de precios minoristas (`prices.azure.com`), sin autenticación.

Niveles de evidencia:

- **[D]** Verificado en documentación oficial o página de precios del proveedor, en la fecha indicada.
- **[S]** Fuente secundaria (blog, agregador). No es oficial.
- **[N]** No verificado: estimación o conocimiento previo que hay que confirmar.

---

## 0. Resumen ejecutivo

- **Recomendación principal: Azure AI Document Intelligence `prebuilt-layout`** (API `2024-11-30`, v4.0 GA).
  - Es un motor OCR + layout **clásico, no generativo**. Cuando falla, falla de forma visible: caracteres mal leídos con baja confianza, una celda vacía. No redacta ni completa contenido.
  - Devuelve **confianza por palabra**, **tablas en HTML con `rowSpan`/`columnSpan`** (celdas combinadas), **número de página física** por elemento y salida Markdown con marcas de salto de página.
  - Soporta español impreso y manuscrito.
  - Se integra con una llamada REST y una clave en cabecera, con `urllib` de stdlib.
  - Coste: piloto de 10 páginas = **0,10 USD** en S0 (10 USD/1000 págs.); las 61 restantes = **0,61 USD**. Con el nivel gratuito F0 (500 págs./mes, 2 págs. por petición, 4 MB) el coste es **0 USD** si se envía una página por petición.
- **Segundo candidato, opcional: Mistral OCR 4.1** (`mistral-ocr-latest`; OCR 4.1 en la página de precios).
  - Es un **modelo generativo de visión y lenguaje (VLM)**, de la misma familia de riesgo que el modo OCR de Firecrawl: puede inventar contenido.
  - Merece pilotarse por tres motivos: es el mejor candidato para figuras y gráficos, devuelve **confianza por palabra** (útil para marcar zonas dudosas) y su precio es mínimo: 4 USD/1000 págs. → **0,04 USD** el piloto y **0,24 USD** el resto.
  - Compararlo con Azure en las mismas páginas mide si un VLM mejor que Firecrawl compensa su riesgo.
- **No recomendados para este piloto:**
  - **Google Document AI.** Enterprise Document OCR no reconstruye tablas. Layout Parser usa Gemini en sus versiones nuevas, así que también es generativo. Además, la autenticación es OAuth y no basta una clave simple.
  - **AWS Textract.** Las peticiones síncronas solo aceptan PDF de 1 página. No hay salida Markdown y hay que reconstruir las tablas desde bloques. Las peticiones requieren firma SigV4. Es el más caro: 15 USD/1000 págs. con tablas.
  - **Opciones locales** (Tesseract, Docling, PaddleOCR, Marker/Surya, docTR). Sirven como **línea base gratuita**, no como sustituto. En esta máquina (16 núcleos, 15 GB RAM, sin GPU, WSL2) se esperan peor calidad en el escaneo desvaído y nulo valor en gráficos.
- **Ningún motor resolverá bien los gráficos** (02 p18, p20) **ni el árbol de problemas** (02 p16).
  - Un motor clásico solo extrae las etiquetas visibles. No empareja porcentajes con categorías por color.
  - Un VLM puede «interpretarlos», pero también inventar, como ya hizo Firecrawl.
  - Para esas figuras la política correcta sigue siendo **transcripción manual o marca `requires_review`**. El piloto debe medir sobre todo el texto escaneado, las tablas y el cronograma.

---

## 1. Punto de partida: qué falló con Firecrawl

Datos de [`evaluations/ocr/reports/selective-ocr-v1-audit.md`](../../evaluations/ocr/reports/selective-ocr-v1-audit.md) (45 páginas, lotes 01–03):

| Resultado | Páginas |
|---|---:|
| `use_new` (usable tal cual) | 10 |
| `use_new_with_corrections` (118 correcciones literales) | 32 |
| `manual_review` (01 p3, 02 p16, 02 p54) | 3 |

Patrones de fallo relevantes para elegir motor:

1. **Contenido inventado** (alucinación): «17.9» en la fila 2010 de la Ilustración 6 (02 p20); texto en inglés en el árbol de problemas («intra-maternal/hospital», «Limited atención», 02 p16); cabecera inventada «Category | Share (%)» (02 p18).
2. **Emparejamiento erróneo:** porcentajes del gráfico de anillo asignados al grupo equivocado (02 p18).
3. **Omisiones:** fecha inicial «28 de octubre de 2025» del cronograma (01 p12); «6.241.974 (49,4%) son mujeres» y etiquetas de zona (02 p17); Ilustración 14 casi ausente (02 p54); dígitos del BPIN (02 p54).
4. **Autocorrección de erratas del original:** «periodo» → «período», «los estudiante.» → «los estudiantes.», «www.unitropico.eduo.co» → «www.unitrópico.edu.co».
5. **Errores clásicos de OCR:** tildes perdidas, «Unitrófico», «falsidad», «verifiable», «prioritizados», «Junfrica».

Los tipos 1, 2 y 4 son propios de un **modelo generativo**: produce texto plausible en lugar de transcribir. Los tipos 3 y 5 aparecen en cualquier motor. Por eso el criterio principal es el **riesgo de alucinación**: un error visible se corrige en revisión, pero una cifra inventada y verosímil puede pasar al RAG y citarse literalmente.

---

## 2. Formato que espera la ingesta

- `workers/ingestion/src/unitropico_ingestion/services/ocr_parser.py` acepta la forma de Firecrawl Parse v2:

  ```json
  {"success": true,
   "data": {"pages": [{"pageNumber": 1, "markdown": "..."}],
            "metadata": {"numPages": N, "totalPages": N}}}
  ```

  - `pageNumber` es la **página física**. Si falta `pages`, la ingesta divide `markdown` por `<!-- page N -->`.
  - Rechaza páginas duplicadas y respuestas truncadas (`totalPages > numPages`).
- `evaluations/ocr/assemble_cache.py` construye ese mismo JSON con una página por página física (`pages[].pageNumber/markdown/source`) y lo guarda con la clave `ocr/{sha256}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json`.
  - Hoy `OCR_VERSION = "firecrawl-parse-v2-ocr-v1"`.
  - Si se adopta otro proveedor, sus páginas necesitan un **`source` propio** (p. ej. `azure_layout_v1`) y, si cambia el contenido, un **`OCR_VERSION` nuevo** para no reutilizar la caché anterior.
- **Consecuencia:** cualquier proveedor sirve si un script lo convierte a `{pageNumber, markdown}` por página física. Las tablas pueden ir en HTML dentro del Markdown: Firecrawl ya lo hace con `colspan`, y el chunker lo trata como texto.

---

## 3. Evaluación por proveedor

### 3.1 Azure AI Document Intelligence (`prebuilt-read` / `prebuilt-layout`)

| Aspecto | Detalle |
|---|---|
| Motor | OCR de aprendizaje profundo + análisis de layout. **No generativo**: transcribe lo que ve. Riesgo de alucinación **bajo**. Los fallos son caracteres mal leídos, celdas vacías o divisiones de tabla [D]. |
| Confianza | `confidence` por **palabra** (`pages[].words[]`), por línea manuscrita y por marca de selección [D]. Permite marcar para revisión las palabras por debajo de un umbral. |
| Español | Soportado en impreso y manuscrito (`es`), con detección automática. La documentación recomienda **no** forzar el idioma [D]. |
| Tablas | `prebuilt-layout` devuelve `tables[].cells[]` con `rowIndex`, `columnIndex`, `rowSpan`, `columnSpan` y `boundingRegions` con `pageNumber`. En Markdown (`outputContentFormat=markdown`), las tablas salen en **HTML** para conservar celdas combinadas y cabeceras multifila [D]. |
| Tablas entre páginas | Cada página produce su propia tabla. La documentación recomienda unirlas en postproceso [D]. Encaja con nuestro formato por página física: no hay que unir nada para la ingesta. |
| Figuras | Detecta `figures` con su región y el texto OCR que contienen (etiquetas). **No interpreta** valores de gráficos. Esto es deseable: no inventará pares grupo–porcentaje. |
| Página física | `pages[].pageNumber` y `pages[].spans` (offset/longitud en `content`). Permiten cortar el Markdown por página de forma exacta [D]. |
| API | **Asíncrona:** `POST {endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze?api-version=2024-11-30&outputContentFormat=markdown` con `{"base64Source": ...}` → `202` + `Operation-Location` → `GET` con sondeo (respetar `Retry-After`). Parámetro `pages` para rangos [D]. |
| Límites | S0: 500 MB, 2000 págs. por documento, 15 TPS. **F0: 4 MB, solo analiza las 2 primeras páginas de cada petición**, 1 TPS [D]. |
| Autenticación | Clave del recurso en la cabecera `Ocp-Apim-Subscription-Key` y el endpoint del recurso [D]. |
| Privacidad | Procesa en la región del recurso. Guarda entrada y resultado **24 h** y luego los borra; `Delete Analyze Result` los borra antes [D]. No hay datos personales de estudiantes, así que no hay obstáculo. |
| Precio | Read: 1,50 USD/1000 págs. Layout ("Pre-built Pages"): **10 USD/1000 págs.** F0: 500 págs./mes gratis [D, `prices.azure.com`, `eastus`]. |
| Coste piloto / resto | S0: 10 págs. = **0,10 USD**; 61 págs. = **0,61 USD**. F0: **0 USD** con 1 página por petición (≤ 4 MB por página; los fragmentos actuales pesan ≤ ~1 MB/pág.). |
| Integración | Stdlib (`urllib`, `json`, `base64`, `time`). ~120 líneas: envío, sondeo y corte por `pages[].spans` → `{pageNumber, markdown}`. **Bajo esfuerzo.** |

`prebuilt-read` (1,50 USD/1000) no reconstruye tablas. Sirve solo si se quiere texto más confianza en páginas de prosa. Para el piloto conviene `prebuilt-layout`.

### 3.2 Mistral OCR (OCR 4.1; alias `mistral-ocr-latest`)

| Aspecto | Detalle |
|---|---|
| Versión actual | La tabla de modelos lista **OCR 4.1** y **OCR 3** (v25.12) como vigentes. OCR 4.0 (`mistral-ocr-4-0`) está retirado desde el 2026-09-30, igual que OCR 2 y OCR (25.03). La ficha de OCR 4 menciona `mistral-ocr-4-1` [D]. OCR 4.1 tuvo vista previa pública desde julio de 2026 [S]. **Fijar el `model` devuelto en la respuesta** para que el piloto sea reproducible. |
| Motor | **VLM generativo** ("document-understanding model"). Riesgo de alucinación **medio-alto**, de la misma clase que Firecrawl OCR. Mistral no publica tasas de alucinación. Avisa de que no debe usarse para decisiones legales o financieras de alto riesgo y de que sus benchmarks son "direccionales" [D]. |
| Confianza | `confidence_scores_granularity`: `page` \| `block` \| `word`. Con `word` devuelve `word_confidence_scores` por página y por tabla, más `average_page_confidence_score` y `minimum_page_confidence_score` [D]. Es una ventaja real frente a Firecrawl. Lo que no está documentado es si la confianza de un VLM detecta una invención fluida. Precisamente eso es lo que el piloto debe medir. |
| Español | 170 idiomas, sin desglose explícito del español [D]. Interno multilingüe 0,98; OlmOCRBench 85,20 [D]. |
| Tablas | `table_format`: `null` (Markdown en línea), `markdown` o `html`. Con valor, las tablas van en `pages[].tables` y aparecen como marcadores en el Markdown [D]. Usar `html` para conservar celdas combinadas y sustituir los marcadores en el script. |
| Página física | `pages[].index` (base 0) → `pageNumber = index + 1`. `pages` admite rangos [D]. |
| API | **Síncrona:** `POST https://api.mistral.ai/v1/ocr`, JSON con `document: {"type":"document_url","document_url":"data:application/pdf;base64,..."}` [D]. Existe también Batch con 50 % de descuento [D]. |
| Límites | La documentación consultada no indica tamaño ni páginas máximas (la FAQ no carga) [N]. Los fragmentos del piloto son pequeños, así que no debería haber problema. |
| Autenticación | `Authorization: Bearer <MISTRAL_API_KEY>` [D]. |
| Privacidad | Alojamiento UE por defecto. Retiene entradas y salidas de la API 30 días para vigilar abusos, salvo con *zero data retention*. La exclusión de entrenamiento se gestiona en el panel de administración [S]. El plan gratuito *Experiment* implicaría ceder datos para entrenamiento [S]. Aceptable para documentos públicos, pero conviene saberlo. |
| Precio | **OCR 4.1: 4 USD/1000 págs.**; OCR 3: 2 USD/1000 págs. [D, página de precios]. Batch −50 % (2 USD/1000 en OCR 4) [D]. No consta capa gratuita de OCR [N]. |
| Coste piloto / resto | 10 págs. = **0,04 USD**; 61 págs. = **0,24 USD** (OCR 4.1). |
| Integración | Stdlib, ~100 líneas, una sola petición. **Muy bajo esfuerzo.** |

### 3.3 Google Cloud Document AI (Enterprise Document OCR / Layout Parser)

| Aspecto | Detalle |
|---|---|
| Motor | **Enterprise Document OCR** (`pretrained-ocr-v2.x`): OCR clásico, no generativo. **Layout Parser:** la versión estable por defecto es `pretrained-layout-parser-v1.0-2024-06-03`. Las v1.5/v1.6/v3.1 combinan OCR con **Gemini** (2.5 Flash/Pro, 3.0 Flash/Pro, 3.1 Flash Lite), es decir, son **generativas** [D]. Google afirma «significativamente menos alucinaciones» que un parser LLM puro, pero no las descarta [D]. |
| Confianza | El objeto `Document` incluye `layout.confidence` en bloques, líneas y tokens [N: lo confirma la referencia del esquema, no la página del procesador]. Puntuación de calidad de imagen por página, con defectos como `faint` o `blurry` [D]. |
| Español | Pistas de idioma BCP-47 (el ejemplo usa `es`) y detección automática [D]. |
| Tablas | **Enterprise OCR no extrae tablas**: devuelve bloques, párrafos, líneas y tokens [D]. Las tablas requieren Form Parser (30 USD/1000) o Layout Parser. Layout Parser avisa de que **una tabla que cruza páginas puede partirse en dos** [D]. |
| Figuras | Layout Parser puede anotar figuras y gráficos con descripciones y datos (función en vista previa, generativa) [D]. Es justo el riesgo que queremos evitar. |
| API | `process` síncrono: 15 págs., 30 en modo *imageless*, 20 MB. Lote asíncrono vía Cloud Storage: 500 págs. [D/S]. |
| Autenticación | Proyecto GCP con facturación, API habilitada, **procesador creado** y **OAuth 2.0** con cuenta de servicio o `gcloud auth print-access-token`. Sin claves API simples [N]. Firmar el JWT de una cuenta de servicio con stdlib no es práctico. |
| Región | Procesador en `us` o `eu`. Las v1.6 de Layout Parser usan el endpoint global de Vertex y no cumplen residencia de datos [D]. |
| Precio | Enterprise OCR: **primeras 1000 págs./mes gratis**, luego 1,50 USD/1000; complementos OCR 6 USD/1000; Form Parser 30 USD/1000; **Layout Parser 10 USD/1000** [D]. Crédito de 300 USD para clientes nuevos [D]. |
| Coste piloto / resto | OCR: 0 USD. Layout Parser: 0,10 / 0,61 USD. |
| Integración | **Media-alta:** autenticación OAuth, creación del procesador y conversión del árbol `Document`/`DocumentLayout` a Markdown por página. |

### 3.4 AWS Textract (`AnalyzeDocument` con `TABLES` [+ `LAYOUT`])

| Aspecto | Detalle |
|---|---|
| Motor | OCR + análisis de tablas y layout **clásico, no generativo**. Riesgo de alucinación **bajo** [D/N]. |
| Confianza | `Confidence` por bloque: `WORD`, `LINE`, `CELL`, `TABLE` [N: conocido, no reverificado hoy]. |
| Español | Impreso soportado en `AnalyzeDocument` (Tables, Layout) y `DetectDocumentText`. **Manuscrito solo en inglés** [D]. |
| Tablas | Bloques `TABLE`/`CELL`/`MERGED_CELL` con `RowSpan`/`ColumnSpan` y relaciones [N]. **No hay salida Markdown**: hay que reconstruir texto, orden de lectura y tablas desde el grafo de bloques. |
| API | **Síncrona solo con imágenes o PDF de 1 página** (10 MB). Los PDF multipágina exigen la API asíncrona con **S3**: `StartDocumentAnalysis`, hasta 3000 págs. y 500 MB [D]. Para el piloto habría que enviar 10 PDF de una página. |
| Autenticación | Usuario o rol IAM con claves de acceso y **firma SigV4**. Implementable con `hmac`/`hashlib`, pero no trivial. Lo habitual es `boto3`. |
| Precio | DetectDocumentText 1,50 USD/1000; **Tables (o Tables+Layout) 15 USD/1000**. Capa gratuita de 3 meses para clientes nuevos: 100 págs./mes con Tables [D, tarifas de us-west-2; la página no mostraba us-east-1]. |
| Coste piloto / resto | 0,15 / 0,92 USD (o 0 USD dentro de la capa gratuita). |
| Integración | **Alta:** SigV4 + reconstrucción de Markdown/HTML desde bloques. |

### 3.5 Opciones locales sin coste por página

Máquina actual: 16 núcleos, 15 GB de RAM, **sin GPU NVIDIA** (WSL2), Docker disponible. Tesseract no está instalado; `unitropico-pdf-audit:poppler-25.03.0-deb13u4` sí existe para renderizar páginas.

| Opción | Tipo | Hardware | Tablas | Calidad esperada en este corpus |
|---|---|---|---|---|
| **Tesseract 5 + `spa`** (p. ej. `ocrmypdf`/`tesseract` en un contenedor Debian) | Clásico (LSTM), no generativo | CPU, segundos por página | **No reconstruye tablas.** Solo texto con orden de lectura por bloques; los cronogramas saldrían aplanados | Buena en prosa limpia; floja en el escaneo desvaído (01 p3). Confianza por palabra (TSV/hOCR). Línea base útil y honesta [N] |
| **Docling** (IBM) | Pipeline clásico: modelos de layout + **TableFormer** + OCR conectable (EasyOCR, Tesseract, RapidOCR). Pipeline VLM opcional (GraniteDocling) que es generativo | CPU viable (lento, ~segundos–decenas de segundos por página) [N] | Sí: estructura de celdas, exportación Markdown/HTML, procedencia por página | La estructura de tablas puede ser buena; el texto depende del motor OCR elegido (Tesseract `spa` o EasyOCR `es`) [N] |
| **PaddleOCR 3.x** (PP-OCRv5/v6 + PP-StructureV3) | Clásico (detección + reconocimiento); **PaddleOCR-VL** (0,9B) es un VLM | CPU posible, lento; v6 acelera con OpenVINO [D] | PP-StructureV3 exporta Markdown/JSON con tablas [D] | Reconocimiento latino (incl. español) [D/N]; instalación pesada [N] |
| **Marker / Surya** | Surya 2.5 es un **VLM** en modo balanced; el modo rápido usa ONNX en CPU; `--use_llm` añade un LLM externo (Gemini por defecto) [D] | GPU recomendada; CPU en modo rápido [D] | Tablas en HTML; las escaneadas recurren al VLM [D] | Riesgo generativo en modos VLM/LLM. Licencia de pesos OpenRAIL-M modificada (gratuita para investigación y entidades con < 5 M USD) [D] |
| **docTR** | Clásico (detección + reconocimiento) | CPU viable | **No** reconstruye tablas | Solo texto; no aporta frente a Tesseract para nuestras tablas [N] |

**Conclusión local:** no sustituye a un servicio para el escaneo desvaído ni para las tablas. Una ejecución de **Tesseract `spa`** (o Docling + Tesseract) sobre las mismas 10 páginas cuesta 0 USD y da un suelo de comparación con confianza por palabra. Es opcional y requiere construir una imagen Docker con `tesseract-ocr-spa`, lo que implica descarga de red.

---

## 4. Tabla comparativa

| Criterio | Azure Layout | Mistral OCR 4.1 | Google Enterprise OCR | Google Layout Parser | AWS Textract Tables | Tesseract `spa` (local) |
|---|---|---|---|---|---|---|
| Tipo de motor | Clásico + layout | **VLM generativo** | Clásico | v1.0 clásico; v1.5+ **Gemini** | Clásico + tablas | Clásico |
| Riesgo de alucinación | Bajo | **Medio-alto** | Bajo | Medio (v1.5+) | Bajo | Bajo |
| Confianza por palabra | Sí | Sí (`word`) | Sí (tokens) [N] | No documentada | Sí [N] | Sí |
| Tablas / celdas combinadas | HTML con span | HTML/MD (generadas) | No | Sí; partición entre páginas | Bloques con span [N] | No |
| Página física | `pageNumber` + spans | `index` + 1 | `pageNumber` | Árbol de layout | `Page` | Por página |
| Llamada | Asíncrona (sondeo) | Síncrona | Síncrona ≤ 15 págs. | Síncrona ≤ 15 págs. | Síncrona: 1 pág.; multipágina vía S3 | Local |
| Autenticación | Clave + endpoint | Clave Bearer | OAuth (cuenta de servicio) | OAuth | IAM + SigV4 | — |
| USD/1000 págs. | 10 (Read 1,5) | 4 (OCR 3: 2) | 1,5 (1000/mes gratis) | 10 | 15 | 0 |
| Piloto 10 págs. | 0,10 (F0: 0) | 0,04 | 0 | 0,10 | 0,15 | 0 |
| Resto 61 págs. | 0,61 (F0: 0) | 0,24 | 0 | 0,61 | 0,92 | 0 |
| Integración stdlib | Fácil | Muy fácil | Difícil (OAuth) | Difícil | Media-difícil | Fácil (subprocess) |

Referencia: Firecrawl Parse v2 cuesta 1 crédito por página. Las 61 páginas restantes = 61 créditos.

---

## 5. Recomendación

1. **Pilotar Azure Document Intelligence `prebuilt-layout`** (obligatorio).
   - Ataca el problema de fondo: necesitamos un motor que no invente. Sus errores serán visibles y vendrán con confianza baja.
   - Conserva la estructura de las tablas (cronograma, Tabla 1, celdas combinadas del Manual) sin «interpretarla».
   - Si en las páginas de texto y tablas iguala o mejora a Firecrawl con **0 alucinaciones**, puede procesar las 61 páginas restantes por 0,61 USD (o 0 USD en F0).
   - Firecrawl quedaría solo para comparación, o para nada.
2. **Pilotar también Mistral OCR 4.1** (opcional, recomendable por su coste marginal de 0,04 USD).
   - Sirve para medir si un VLM de última generación mejora de forma sustancial el texto desvaído y las figuras, y si su `word_confidence` señala las invenciones.
   - Si comete alucinaciones como Firecrawl, se descarta la vía generativa con datos y no con opiniones.
   - Si no las comete y mejora en figuras, cabría un esquema mixto: Azure por defecto y Mistral solo en páginas de figuras, siempre con `requires_review`.
3. **Descartar por ahora** Google (sin tablas en OCR; Layout Parser generativo; OAuth) y Textract (más caro, sin Markdown, SigV4 + S3). Ninguno aporta algo que Azure no cubra con menos esfuerzo.
4. **Línea base local opcional:** Tesseract `spa` sobre las mismas páginas, solo para documentar el suelo en la tesis.

---

## 6. Páginas propuestas para el piloto (10 páginas físicas, ya auditadas)

Todas tienen verdad de referencia en `selective-ocr-v1-audit.json`: el Markdown de Firecrawl más las correcciones `substantive` verificadas con zoom.

| # | Documento (lote) | Pág. física | Qué pone a prueba | Firecrawl (auditoría) |
|---:|---|---:|---|---|
| 1 | Res. CBU 002-2025 (01) | 3 | Escaneo **desvaído**: tildes, palabras dañadas, «l» casi invisible | `manual_review`, 17 correcciones |
| 2 | Res. CBU 002-2025 (01) | 12 | **Cronograma** tabular (inicio) y celda de fecha | Omitió «28 de octubre de 2025»; «Unitrófico»; 4 correcciones |
| 3 | Res. CBU 002-2025 (01) | 13 | Continuación del cronograma; **errata del original** `www.unitropico.eduo.co` (prueba de autocorrección) | La «corrigió»; 3 correcciones |
| 4 | Res. CBU 002-2025 (01) | 14 | Final del cronograma (tabla que **cruza páginas**); orden de palabras | «Listado de Final»; 4 correcciones |
| 5 | Documento Técnico (02) | 16 | **Diagrama denso** (árbol de problemas) | `manual_review`: texto inventado en inglés, cajas omitidas o duplicadas |
| 6 | Documento Técnico (02) | 18 | **Gráfico de anillo**: emparejar % con grupo SISBEN | Emparejamiento erróneo y cabecera inventada |
| 7 | Documento Técnico (02) | 20 | **Gráfico con cifras** (Ilustración 6) | Cifra **inventada** «17.9», «22,46» ausente |
| 8 | Documento Técnico (02) | 22 | **Tabla 1** como imagen, cabeceras con iconos | `minor`, 1 corrección |
| 9 | Documento Técnico (02) | 54 | Tabla de insumos (BPIN de 16 dígitos, montos) + **Ilustración 14** | `manual_review`: BPIN sin dos ceros; ilustración casi ausente; 10 correcciones |
| 10 | Manual M-IP-36 (03) | 38 | **Celdas combinadas** (Permanencia/Excelencia comparten celda y frecuencia) | `minor` (separó la celda combinada) |

**Ajustes respecto a la propuesta inicial:**

- Se añaden 01 p13 y p14 para tener el **cronograma completo**: es el único caso auditado de tabla que cruza páginas, y p13 contiene la mejor prueba de autocorrección (`eduo.co`).
- Se añade 03 p38 por las celdas combinadas.
- Si se quiere ampliar a 12 páginas (coste marginal ≤ 0,02 USD), sumar 01 p7 («periodo», «falsedad», «verificable»: autocorrecciones y sustituciones) y 02 p17 (cifras y etiquetas omitidas).
- No se incluye la bibliografía (02 p57–61). Ahí el OCR no aporta nada y basta la corrección literal.

**Preparación del fragmento (offline, gratis):** cortar un PDF con estas 10 páginas usando la imagen Poppler local, con el mismo procedimiento que `prepare_selective.py`:

- red `none` y original en solo lectura;
- verificación de hash de render por página;
- `manifest.json` con `mapping` de página del fragmento → `(source_path, página física)`.

Para Azure F0, además, un PDF por página.

---

## 7. Método de comparación

1. **Misma rúbrica** de la auditoría v1: `ok` / `minor` / `defect` / `missing`.
   - Toda autocorrección de una errata del original cuenta como `defect`.
   - Mismo render: PyMuPDF a 150 dpi y zoom de 200–400 dpi en cifras, tablas y zonas desvaídas.
   - Revisión **a ciegas**: el revisor no sabe qué proveedor produjo cada texto.
2. **Métricas por página y proveedor** (Firecrawl ya tiene sus valores):
   - Correcciones literales necesarias (`substantive`; `cosmetic` aparte). Firecrawl en estas 10 páginas: 3: 17 · 12: 4 · 13: 3 · 14: 4 · 16: manual · 18: 2 · 20: 1 · 22: 1 · 54: 10 + manual · 38: 0 (minor).
   - **Alucinaciones**: contenido que no está en la imagen (cifra, palabra, idioma, fila, cabecera). Es **criterio eliminatorio**. Se cuentan aparte de los errores de lectura.
   - **Omisiones** de contenido sustantivo.
   - **Autocorrecciones** de erratas del original.
   - **Hechos críticos exactos**: lista cerrada por página. Ejemplos: fechas del cronograma de p12–14; los cuatro % de p18; los valores 2010 de p20; los seis % de p22; BPIN `202300000000204` (15 dígitos; corregido el 2026-11-20, antes figuraba con un cero de más), ley, presupuesto y resolución de p54; montos y fechas de p3. Se mide el % de hechos transcritos carácter a carácter.
   - **Estructura de tablas**: cada fecha queda en la fila de su actividad; las celdas combinadas se conservan (`colspan`/`rowspan`) y no se duplican ni se inventan fechas.
   - **Utilidad de la confianza** (Azure, Mistral, Tesseract): de las palabras erróneas, qué % tiene confianza < 0,8 (sensibilidad) y qué % de las palabras con confianza baja es realmente erróneo (precisión). Si la confianza detecta los errores, la revisión humana puede concentrarse en ellos.
3. **Regla de decisión** para procesar las 61 páginas restantes:
   - El proveedor elegido debe tener **0 alucinaciones** en el piloto.
   - Debe tener ≤ correcciones totales que Firecrawl en las páginas de texto y tablas (1–4, 8–10), y ≥ hechos críticos exactos.
   - En las figuras (5–7, ilustración de 9) no se exige acierto: basta con que **no invente**. Se transcriben a mano.
   - Si ningún proveedor cumple, se mantiene Firecrawl con auditoría completa o se combina Azure en texto y tablas con transcripción manual de figuras.
4. **Artefactos:** respuestas crudas intactas (`response.raw`) más `pages.json` en formato `{pageNumber, markdown}` por página física, en `.artifacts/ocr-pilot-<proveedor>-v1/`. Informe en `evaluations/ocr/reports/ocr-pilot-alternatives.md`, más un JSON con el mismo esquema que `selective-ocr-v1-audit.json`.

### 7.1 Script de integración (esbozo; no implementado)

Un script `evaluations/ocr/alt_ocr.py` (stdlib, Python 3.14, sin `.env` en el repo; la clave se lee de una variable de entorno que exporta el usuario) con dos órdenes:

- **`prepare`**: valida el fragmento y el `manifest` del piloto (hashes fijados, como `selective_send.py`).
- **`send --provider azure|mistral`**: exige confirmación explícita y escribe de forma exclusiva (no sobrescribe).
  - **Azure:** `POST ...prebuilt-layout:analyze?api-version=2024-11-30&outputContentFormat=markdown` (o `&pages=N`), sondeo de `Operation-Location` con `Retry-After`. Se corta `analyzeResult.content` por `pages[i].spans` y se guarda `words[].confidence`.
  - **Mistral:** `POST /v1/ocr` con `model`, `document` en data-URI, `table_format="html"`, `confidence_scores_granularity="word"`, `include_image_base64=false`. Se sustituyen los marcadores de tabla por su HTML y se aplica `pageNumber = index + 1`.
  - **Salida:** `{"success": true, "data": {"pages": [...], "metadata": {"numPages": n, "totalPages": n}, "provenance": {...}}}`, que `parse_ocr_response` acepta sin cambios.

Esfuerzo estimado: ~200 líneas más pruebas unitarias con respuestas simuladas (fixtures). Ninguna dependencia nueva.

---

## 8. Qué necesita el usuario para autorizarlo

### Azure (recomendado)

1. Una cuenta Azure. El alta pide tarjeta para verificar identidad, aunque se use el nivel gratuito [N].
2. Crear un recurso **Document Intelligence** (Foundry Tools) en una región, p. ej. `East US` o `West Europe`.
   - Nivel **F0 (gratis)**: 500 págs./mes, 2 págs. por petición, 4 MB. Coste **0 USD** enviando una página por petición.
   - O nivel **S0**: coste exacto **0,10 USD** el piloto (10 págs. × 0,01 USD) y **0,61 USD** las 61 restantes.
3. Copiar el **endpoint** y una **clave** del recurso y exportarlas como variables de entorno solo en la sesión de envío (`AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`). No van al repo ni a `.env` versionado.
4. Autorizar explícitamente: «10 páginas físicas listadas en §6, `prebuilt-layout`, API `2024-11-30`, nivel F0/S0».

### Mistral (opcional)

1. Cuenta en `console.mistral.ai` con facturación activada (plan de pago; el plan gratuito *Experiment* implica ceder datos para entrenamiento y no consta que incluya OCR).
2. Crear una **API key** (`MISTRAL_API_KEY`).
3. Coste exacto: **0,04 USD** el piloto (10 págs. × 4 USD/1000, OCR 4.1). Si se aprobara para el resto, **0,24 USD**, o 0,12 USD con Batch.
   - Mistral puede exigir una recarga mínima de saldo; no verificado [N].

### Total del piloto doble

**0,14 USD** (Azure S0 + Mistral), o **0,04 USD** si Azure va en F0. Como referencia, procesar las 61 páginas restantes en Firecrawl cuesta 61 créditos.

---

## 9. Fuentes (consultadas el 2026-11-19)

**Mistral**

- OCR (capacidades, parámetros, confianza): https://docs.mistral.ai/capabilities/document_ai/basic_ocr/
- Endpoint OCR: https://docs.mistral.ai/api/endpoint/ocr
- Modelos y retiradas: https://docs.mistral.ai/getting-started/models/models_overview/
- Precios API (OCR 4.1 4 USD, OCR 3 2 USD por 1000 págs.): https://docs.mistral.ai/inference/pricing
- Anuncio OCR 4 (precio, Batch, benchmarks, alcance): https://mistral.ai/news/ocr-4/
- OCR 4.1 en vista previa, confianza por bloque [S]: https://runtimewire.com/article/mistral-ocr-4-1-block-confidence-document-ai
- Privacidad y retención [S/D]: https://docs.mistral.ai/admin/monitor-comply/zero-data-retention · https://help.mistral.ai/en/articles/455207-can-i-opt-out-of-my-input-or-output-data-being-used-for-training
- Plan *Experiment*: https://help.mistral.ai/en/articles/450104-how-can-i-try-the-api-for-free-with-the-experiment-plan

**Azure**

- Layout (Markdown, tablas HTML, figuras, confianza): https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout
- Límites F0/S0: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits
- Idiomas Read/Layout (español impreso y manuscrito): https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/language-support/ocr
- Privacidad (región, 24 h): https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/document-intelligence/data-privacy-security
- Precios: https://azure.microsoft.com/en-us/pricing/details/ai-document-intelligence/ (la tabla carga por JS; cifras obtenidas de la API pública https://prices.azure.com/api/retail/prices filtrando `productName eq 'Azure Document Intelligence'`, `eastus`: Read 1,5; Pre-built/Layout 10; Batch Layout 10 USD/1000)

**Google**

- Precios Document AI (OCR 0–1000 gratis, 1,50; complementos 6; Form Parser 30; Layout Parser 10): https://cloud.google.com/document-ai/pricing
- Enterprise Document OCR: https://docs.cloud.google.com/document-ai/docs/enterprise-document-ocr
- Layout Parser (versiones Gemini, límites 15/500 págs., tablas entre páginas): https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk
- Límites por procesador: https://docs.cloud.google.com/document-ai/limits

**AWS**

- Precios Textract: https://aws.amazon.com/textract/pricing/
- Límites e idiomas: https://docs.aws.amazon.com/textract/latest/dg/limits-document.html

**Locales**

- Docling: https://docling-project.github.io/docling/usage/ · https://github.com/docling-project/docling
- Marker/Surya (licencia, hardware, `--use_llm`): https://github.com/datalab-to/marker
- PaddleOCR 3.x / PaddleOCR-VL: https://www.paddleocr.ai/latest/en/index.html

**Internas**

- `evaluations/ocr/reports/selective-ocr-v1-audit.md` y `.json`
- `evaluations/ocr/reports/extraction-quality-audit.md`
- `evaluations/ocr/reports/pilot-02.md`
- `evaluations/ocr/README.md`
- `evaluations/ocr/assemble_cache.py`
- `workers/ingestion/src/unitropico_ingestion/services/ocr_parser.py`
