# Evaluaciones OCR (Firecrawl Parse)

## Estado actual (honesto y resumido)

Este README conserva abajo la descripción del primer piloto; el estado vigente es:

- Primer piloto (convocatoria 2026, págs. 13-14): resultado `uncertain_no_replay`,
  consumo desconocido. No reenviar.
- Res137: 20 páginas procesadas con HTTP 200 (`reports/remaining-execution.md`,
  `reports/corpus-partial-assembly.md`). La cobertura no certifica calidad OCR;
  las páginas 6 y 14 tienen bloques con `content` vacío.
- Lote Conv2026 (15 páginas): `network_error`, sin `response.raw`, consumo remoto
  desconocido. No se reintenta; el lote permanece `stopped_no_replay`.
- Corpus v2: ensamblado parcial offline (216 páginas únicas; 15 de convocatoria SIN
  texto). Todas las páginas conservan `requires_review` y `publication_blocked`:
  **bloqueado para publicación**, no es un corpus completo para web/RAG.
- OCR selectivo v1: 106 páginas preparadas offline en 7 lotes, **no enviadas**
  (ver «OCR selectivo v1» abajo; 106 créditos autorizados por el usuario).
- Los scripts `pilot.py`, `remaining.py`, `remaining_send.py` y `selective_send.py`
  hacen o preparan llamadas de pago; no ejecutar `send` sin autorización humana explícita.

## OCR selectivo v1 (106 páginas autorizadas)

Origen: auditoría visual página a página (`reports/extraction-quality-audit.json`).
De 216 páginas, **110 se reutilizan** (`ok`/`minor`) y **106 se reprocesan** con
Firecrawl (86 `defect`/`missing` + 20 con errores puntuales). El usuario autorizó
expresamente **106 créditos** (1 crédito/página) para esas 106 páginas exactas.
Referencia de saldo: el Dashboard de Firecrawl mostraba 22 créditos consumidos en
total antes de este envío (el lote incierto de 15 páginas de la Convocatoria no se cobró).

### 1. Preparación (ya ejecutada; offline y gratuita)

```sh
uv run --no-project --python 3.14 python evaluations/ocr/prepare_selective.py
```

Selecciona `firecrawl_ocr_pages + literal_patch_pages` de la auditoría (SHA-256
fijado) y falla si no son exactamente 106. Corta un PDF fragmento por lote con
la imagen Poppler local (red `none`, original montado read-only, timeout 60 s)
y verifica cada página del fragmento contra su página física (hash del texto
de `pdftotext`, hash de un render a 40 dpi, tamaño y rotación). Escribe
`.artifacts/selective-ocr-v1/manifest.json` (exclusivo; el directorio existente,
incluso de un fallo, bloquea otra preparación: **no regenerar**).

| Lote | Págs. | Páginas físicas | Bytes | SHA-256 del fragmento |
|---|---:|---|---:|---|
| 01-res-cbu-002-2025 | 16 | 1–16 | 816479 | `ecc6bb47f87707248d19fa8ccd26e27c16c54714c86b99e5beddc1ff64ec536a` |
| 02-documento-tecnico | 17 | 5, 16–18, 20–22, 30, 32, 44–46, 54, 57–59, 61 | 4457243 | `9207bc79a00908a3d8ad676517478236fb7fbab627ca85c3601739140effc4ae` |
| 03-manual-m-ip-36-1of2 | 12 | 14, 18–20, 23, 27–29, 35, 36, 38, 40 | 942768 | `35e307ab592a411736d84c51420544ef5beee000ff058e639468bd3b3ae0d872` |
| 04-manual-m-ip-36-2of2 | 12 | 41, 47, 52, 61–65, 67, 68, 74, 75 | 927282 | `4fb4cda7a733779845de35fefb35fe1fe8f04e1d76a737c703297bdbd264b43b` |
| 05-res-00137 | 12 | 1–5, 9, 11, 13, 14, 17–19 | 12052896 | `4fc6c946c62ce5886059ed94f665d974b6fcd7ad7e081aef76bdc81b93a6ec87` |
| 06-convocatoria-2026-1 | 17 | 1–17 | 7709394 | `9498437d14958d079f654962c45d7e4e93f20edf249e4a9710299e1d595b7d30` |
| 07-politica-bienestar | 20 | 1–4, 7–18, 20, 22–24 | 7222973 | `4271c2b8745856e48a5bc5ab3d9078aa41494dcdc5d0c75189fdd08ca8f534fd` |

Total 106 páginas, 7 lotes (≤ 20 páginas y ≤ 50 MB cada uno). Manifest SHA-256
`5868748c1b2ec6e5bb40959ca8e6ee0aeec41762db580426923f8843fe433161`; ese hash y
los de cada fragmento están fijados como constantes en `selective_send.py`.

### 2. Envío (lo ejecuta el usuario, a mano, desde la raíz del repo)

Comprobación offline previa (sin clave, sin ledger, sin red):

```sh
uv run --no-project --python 3.14 python evaluations/ocr/selective_send.py check
```

Envío pagado (**106 créditos como máximo**, un POST por lote, sin retries):

```sh
uv run --no-project --python 3.14 python evaluations/ocr/selective_send.py send --confirm-credits 106 --env-file .env
```

- Sin `--confirm-credits 106` exacto no se carga la clave ni se envía nada.
- `--env-file` lee solo `FIRECRAWL_API_KEY` (mismas reglas que `pilot.py`); sin él,
  usa la variable de entorno. La clave nunca se registra.
- Antes de cada POST se verifica el manifest (hash fijado), el fragmento (hash,
  tamaño, `%PDF-`, ≤ 50 MB) y el original. Endpoint fijo
  `https://api.firecrawl.dev/v2/parse`, sin proxies heredados ni redirects.
- Por lote: `execution/<lote>/attempt.json` (exclusivo + fsync **antes** del HTTP),
  `response.raw` (cuerpo recibido) y `result.json`. Opciones: `mode: "ocr"`,
  `maxPages` = páginas del lote, `pages`, `blocks`, `pageMarkers`, timeout 300 s
  (cliente 330 s).
- Procesa los lotes en orden y **se detiene en el primero que no quede completo**
  (timeout, error de red, HTTP ≠ 200, cobertura de páginas incompleta). Un lote
  completo exige HTTP 200, `success: true` y todas sus páginas/bloques.
- Volver a ejecutar `send` salta los lotes completos y continúa con los no
  iniciados, pero **se niega a enviar nada** si existe cualquier lote incierto.

### 3. Estado (offline)

```sh
uv run --no-project --python 3.14 python evaluations/ocr/selective_send.py status
```

Lista cada lote (`not_started`, `complete`, `uncertain`), su código HTTP, los
créditos declarados por `metadata.creditsUsed` y las páginas físicas con markdown
vacío. Sin respuesta, el consumo se muestra como **«consumo desconocido»**.

### Si un lote queda incierto

**No reintentar**, no borrar su carpeta ni su `attempt.json`. Ejecutar `status`,
consultar el consumo real en el Dashboard de Firecrawl y avisar antes de cualquier
otro envío: un timeout puede haber consumido créditos sin respuesta local.
Cualquier reenvío necesita una nueva autorización humana y un plan que preserve
la evidencia.

### 4. Siguiente paso: auditoría visual, luego ensamblado

Antes de ensamblar, revisar visualmente las 106 páginas nuevas contra el PDF
(mismo criterio que `reports/extraction-quality-audit.md`); un HTTP 200 no prueba
calidad OCR. Después, offline:

```sh
uv run --no-project --python 3.14 python evaluations/ocr/assemble_cache.py
```

Exige los 7 lotes completos y combina, por documento, las páginas reutilizables de
`extraction-corpus-v2` (SHA-256 fijado) con las nuevas (fragmento → página física).
Falla si falta cualquier página física o si una página queda sin texto. Escribe en
`.artifacts/ingestion-cache-v1/` un JSON por documento (los 7, incluido el Boletín,
100 % reutilizado) bajo la clave R2 del paso `ocr` del Workflow,
`ocr/{sha256}/firecrawl-parse-v2-ocr-v1-p1000.json` (valores por defecto de
`OCR_VERSION`/`OCR_MAX_PAGES`). Formato Firecrawl Parse v2 (`data.pages`,
`data.metadata.numPages/totalPages`); cada página lleva `source`
(`reused_pdftotext`, `reused_firecrawl` o `firecrawl_selective_v1`) y
`data.provenance` resume lotes y hashes: el parser real de la ingesta ignora ambos
campos (comprobado en `tests/test_assemble_cache.py` y en el propio ensamblado).
También genera `upload-plan.md` con los comandos `npx wrangler r2 object put … --remote`
(**no ejecutados**). El PDF original debe estar además en `raw/{sha256}.pdf`: lo sube
la API al cargarlo por el panel. Subir la caché antes de lanzar la ingesta.

## Piloto OCR alternativo v1 (10 páginas, Azure y Mistral)

Compara motores alternativos a Firecrawl sobre 10 páginas ya auditadas (01 p3, p12,
p13, p14 · 02 p16, p18, p20, p22, p54 · 03 p38), según `docs/research/ocr-alternatives.md`.
`prepare_alt_pilot.py` (offline, ya ejecutado) cortó **un PDF de una página por
página** con la imagen Poppler local (red `none`, original de solo lectura) y verificó
texto, render y geometría de cada fragmento contra su original. Manifiesto inmutable:
`.artifacts/alt-ocr-pilot-v1/manifest.json` (sha256
`0fc698abb01be2fe81df33f5cecb4a06193066770f4de7264c662b702b2e6327`, fijado en
`alt_ocr.py` junto a los hashes de cada fragmento; el mayor pesa 556 KB).

### Qué debe crear el usuario

- **Azure (principal):** en el portal de Azure, crear un recurso **Document
  Intelligence** (nivel **F0** gratuito: 500 págs./mes, 2 págs. por petición, 4 MB;
  o **S0**) en cualquier región. En «Keys and Endpoint» copiar el endpoint
  (`https://<nombre>.cognitiveservices.azure.com`) y la clave 1. No hace falta nada más.
- **Mistral (opcional):** cuenta en `console.mistral.ai` con facturación activada y una
  API key. Los datos se retienen 30 días salvo *zero data retention*.

### Variables de entorno

`AZURE_DOCINT_ENDPOINT`, `AZURE_DOCINT_KEY`, `MISTRAL_API_KEY`. Se leen del entorno o
de un `--env-file` explícito (solo esas líneas `NOMBRE=valor`; sin shell, expansión ni
`eval`). Nunca se registran: el ledger no guarda ni la clave ni el nombre del recurso.
El endpoint de Azure debe ser exactamente `https://<nombre>.cognitiveservices.azure.com`
(sin puerto, ruta ni credenciales); cualquier otro se rechaza antes de abrir el ledger.

### Comandos exactos (desde la raíz de `agente-conversacional-unitropico/`)

```sh
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py check
export AZURE_DOCINT_ENDPOINT='https://<nombre>.cognitiveservices.azure.com'
export AZURE_DOCINT_KEY='<clave>'
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py send --provider azure --confirm-pages 10
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py status
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py export --provider azure
# Opcional (0,04 USD):
export MISTRAL_API_KEY='<clave>'
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py send --provider mistral --confirm-pages 10
uv run --no-project --python 3.14 python evaluations/ocr/alt_ocr.py export --provider mistral
```

Con S0 añadir `--azure-tier s0` (con F0 el script rechaza localmente fragmentos de más
de 3 MB). `--confirm-pages` es obligatorio y debe valer exactamente 10.

### Comportamiento y garantías

- **Ledger exclusivo por (proveedor, página):** `execution/<proveedor>/<page_id>/` se crea
  con `mkdir` exclusivo y `attempt.json` se escribe con `fsync` **antes** de cada HTTP.
  Una carpeta sin `result.json` completo (timeout, 4xx/5xx, respuesta truncada,
  operación fallida, sondeo agotado) detiene todo envío posterior: **no hay reintentos ni
  replay**; hay que decidir a mano (`status`, portal del proveedor).
- Sin redirects, sin proxies heredados, transporte `urllib` con un solo intento. Las
  respuestas crudas se guardan intactas (`post.raw`, `response.raw`, `operation.json`).
- Azure es asíncrono: `POST` → `202` + `Operation-Location` (solo se acepta el mismo host,
  la ruta `prebuilt-layout/analyzeResults/<id>` y `api-version=2024-11-30`; la clave
  nunca se envía a otra URL) → `GET` con `Retry-After` (2–30 s) hasta `succeeded`,
  con un tope de 300 s tras el cual la página queda **incierta**.
- Observabilidad por fases a stderr (`phase=… category=… status=…`), sin texto de
  respuesta ni cabeceras.

### Salida (`export`, offline, exige las 10 páginas completas)

`.artifacts/alt-ocr-pilot-v1/export/<proveedor>/`:

- `<slug-documento>.json`: forma Firecrawl Parse v2 (`{"success": true, "data": {"pages":
  [{"pageNumber": <página física>, "markdown", "source", "confidence"}], "metadata":
  {"numPages", "totalPages"}, "provenance"}}`), **aceptada por `parse_ocr_response`** del
  worker de ingesta (comprobado en `tests/test_alt_ocr.py`). Se exporta por documento
  porque el parser rechaza números de página duplicados y las 10 páginas pertenecen a
  3 documentos. `assemble_cache.py` **no** se usa: requiere el documento completo
  (reutiliza páginas ok/minor y mapea lotes Firecrawl); si el piloto resulta ganador se
  adaptará en su momento (otro `source` y otro `OCR_VERSION`).
- `pages.json` (`alt-ocr-pilot-export/v1`): las 10 páginas con `page_id`, `document`,
  `pageNumber`, `markdown` y `confidence` para la comparación.
- Azure: Markdown de `analyzeResult.content` cortado por `pages[].spans`
  (`outputContentFormat=markdown`, tablas en HTML tal como las devuelve el servicio,
  `stringIndexType=unicodeCodePoint`); confianza por palabra (`words[].confidence`,
  mínimo y media) y por línea solo si el servicio la incluye.
- Mistral: `pages[0].markdown` con los marcadores `[tbl-N.html](tbl-N.html)` sustituidos
  por el HTML de `tables` (`table_format=html`); confianza por palabra
  (`confidence_scores_granularity=word`) y medias/mínimos de página.

### Coste esperado

| Proveedor | 10 páginas |
|---|---|
| Azure `prebuilt-layout` F0 | 0 USD (1 página por petición; cuota 500 págs./mes) |
| Azure `prebuilt-layout` S0 | 0,10 USD (10 USD/1000 págs.) |
| Mistral OCR (`mistral-ocr-latest`) | 0,04 USD (4 USD/1000 págs.) |

`status` (offline) muestra el estado por página y proveedor y el coste máximo estimado
de las páginas intentadas; el consumo real solo se confirma en el portal del proveedor.

### Fuentes oficiales (consultadas el 2026-11-19)

- Azure Analyze Document 2024-11-30: https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30)
- Azure Get Analyze Result: https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30)
- Azure límites F0/S0: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0
- Mistral endpoint OCR: https://docs.mistral.ai/api/endpoint/ocr
- Mistral OCR (tablas, confianza): https://docs.mistral.ai/capabilities/document_ai/basic_ocr/

**No verificado en la documentación oficial** (el script lo trata de forma defensiva):
valores exactos del enum `status` de Azure (se asumen `notStarted`/`running`/`succeeded`/
`failed`; cualquier otro valor deja la página incierta); que `contentFormat` valga
`markdown` con `outputContentFormat=markdown` (`export` lo exige); el formato exacto del
data URI base64 de Mistral para PDF (se envía `data:application/pdf;base64,…` en
`document_url`) y si acepta ese tamaño; los campos internos de `pages[].tables` y de
`word_confidence_scores` (se usan `id`, `content`, `text`, `confidence`); la base del
índice de página de Mistral (no se usa: cada PDF tiene una página); si Azure devuelve
confianza por línea.

### Siguiente paso

Auditoría **ciega** de las 10 páginas por proveedor con la misma rúbrica del OCR
selectivo v1 (`ok`/`minor`/`defect`/`missing`; alucinaciones como criterio eliminatorio;
hechos críticos exactos; estructura de tablas; utilidad de la confianza), siguiendo
`docs/research/ocr-alternatives.md` §7. Solo si un proveedor cumple la regla de decisión
se autorizan las 61 páginas restantes.

## Herramientas de calidad

Proyecto Python propio (`pyproject.toml`, `uv.lock`; Python 3.14, sin dependencias
de runtime; grupo `dev`: `ruff==0.17.4`, `pytest==9.2.0`). Desde `evaluations/ocr/`:

```sh
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

pytest descubre las pruebas `unittest` de `tests/` (también valen
`python -m unittest discover -s tests`). Todas las pruebas son offline, con
fakes y credenciales falsas; no necesitan `.artifacts/`. Ruff excluye `.artifacts/`.
CI: `.github/workflows/evaluations.yml` (sin secrets ni red a Firecrawl).

## Historial: primer piloto

**Existe un intento registrado con resultado incierto; no reenviar.**
El resultado local es `uncertain_no_replay`, con `http_status: null` y sin
`response.raw`. No confirma recepción, OCR completado ni créditos cobrados.
El consumo real debe comprobarse en el Dashboard de Firecrawl.
No instala dependencias ni usa MCP. Python stdlib + imagen Poppler local existente.

## Contrato oficial comprobado mediante GET público

- https://docs.firecrawl.dev/features/parse (también su versión `.md`).
- https://docs.firecrawl.dev/api-reference/endpoint/parse (también `.md`, con OpenAPI).
- Endpoint: `POST https://api.firecrawl.dev/v2/parse`, autenticación Bearer.
- Multipart: `file` binario y `options` JSON (`application/json`). Máximo 50 MB.
- PDF: `mode: "ocr"`, `maxPages: 2`, `pages: true`, `blocks: true`,
  `pageMarkers: true`; salida `markdown`; timeout API 300000 ms, cliente 330 s.
- Tarifa documentada: **1 crédito/página: 2 páginas = 2 créditos**.
  `pages`, `blocks` y `pageMarkers` no añaden coste. No se solicitan formatos
  LLM, resumen, JSON estructurado ni otros extras facturables. No se consultó
  el saldo ni la cuenta; la tarifa se verificó en documentación pública.
- `pages` contiene páginas físicas; `blocks` contiene bloques por página.
  Los marcadores pueden omitir fronteras de tablas continuadas: no sustituir
  `pages` por marcadores para atribuir una cita.
- `https://mcp.firecrawl.dev/v2/mcp-oauth` **no** es el endpoint Parse.

## Uso desde la raíz de `agente-conversacional-unitropico/`

Pruebas sin red, con credencial falsa y temporales eliminados por unittest:

```sh
export PYTHONDONTWRITEBYTECODE=1
uv run --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests
```

Preparación offline (ya ejecutada; **no regenerar** el fragmento aprobado;
`pdfunite` puede cambiar su hash por metadatos y se niega a sobrescribirlo):

```sh
uv run --no-project --python 3.14 python evaluations/ocr/pilot.py prepare
```

Usa `unitropico-pdf-audit:poppler-25.03.0-deb13u4` sin pull, red ni capacidades,
con `no-new-privileges`, raíz de contenedor read-only y original montado read-only.
`pdfseparate -f 13 -l 14` y `pdfunite` crean el fragmento; `pdfinfo` comprueba
17 páginas originales y **2 páginas reales** del fragmento. Se compara SHA-256
original antes/después. Solo `.artifacts/` recibe PDFs y manifest.

El techo de 100 créditos no es un objetivo ni autoriza más páginas o llamadas:
este piloto sigue limitado a **2 páginas, 2 créditos y un solo POST**.

**STOP: el único intento ya fue registrado. No ejecutar `send` ni `prepare`,
no borrar el ledger ni regenerar el fragmento para reintentar.**
Cualquier nuevo envío pagado requiere autorización humana explícita y un plan
que preserve la evidencia anterior. La excepción autorizada para un **segundo**
piloto se describe a continuación; no permite repetir ningún ledger existente.

### Segundo piloto autorizado: un solo envío adicional

El usuario autorizó expresamente otro piloto de las mismas páginas físicas
13–14, estimado en **2 créditos adicionales**. El consumo del primer intento
sigue desconocido. No se autoriza procesar todo el documento ni el corpus.

Desde la raíz del proyecto, ejecutar **una sola vez**, manualmente:

```sh
uv run --no-project --python 3.14 python evaluations/ocr/pilot.py send --confirm-two-credits --second-authorized-attempt --env-file .env
```

Esta opción exige el ledger previo y su resultado histórico incierto. Reserva
exclusivamente `.artifacts/second-attempt/`, copia el fragmento y manifest ya
aprobados y aplica todas las validaciones originales antes del POST. No borra,
sobrescribe ni regenera evidencia anterior, ni habilita rutas arbitrarias.
La carpeta existente bloquea otra ejecución, incluso si falló la preparación
del segundo intento. No borrarla para repetir y no volver a ejecutar `prepare`.

Los resultados nuevos quedan en `.artifacts/second-attempt/attempt.json`,
`result.json` y, si se recibió y guardó una respuesta, `response.raw`.
Un exit code 0 solo indica HTTP 200 con `success: true`, **no calidad OCR**;
contrastar después la tabla y sus referencias de página con el original.
Ante incertidumbre, detenerse y consultar el Dashboard, sin retries.

La ruta `.env` se localizó por metadatos Git; no se leyó ni validó su contenido
ni se usó una clave durante la preparación. En ejecución, `--env-file` lee
únicamente la asignación `FIRECRAWL_API_KEY` (sin evaluar shell ni expandir
variables). Acepta un único valor alfanumérico con guiones/guiones bajos,
con o sin comillas simples/dobles y prefijo `export` opcional. Duplicados,
comentarios inline, interpolaciones o sintaxis ambigua detienen el envío.
Sin `--env-file`, utiliza solo `FIRECRAWL_API_KEY` del entorno.
No copiar la clave al comando, al repositorio ni al informe.

Antes del POST, constantes del código fijan la identidad aprobada, sin derivarla
del manifest mutable y sin bypass CLI:

- Fragmento `pages-13-14.pdf`: 660929 bytes, SHA-256
  `836713cefe6bf36f69832e3c88ac56288e4e238e61343f300ba6fe34814ff71a`.
- Original: ruta fija de convocatoria 2026 en `documents/documentos_bienestar/`,
  17 páginas, SHA-256
  `3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26`.

Se exige ese hash del fragmento tanto al archivo leído para el payload como al
manifest, con tamaño exacto, 2 páginas reales comprobadas por Poppler y mapping
1→13/2→14. La procedencia declarada debe indicar ruta original exacta, 17 páginas
y ambos hashes originales (antes/después) iguales al aprobado. `send` verifica
esa evidencia registrada; no vuelve a extraer ni relee el PDF original.
Un fragmento sustituto con manifest autoconsistente también se rechaza antes de
crear el ledger. Las pruebas parchean únicamente las constantes para sus fixtures.

El único archivo enviado es `.artifacts/pages-13-14.pdf`, nunca el original.
`maxPages` no selecciona páginas originales: estas ya se extrajeron.

`attempt.json` se crea con modo exclusivo `x` y fsync **antes** de acceder a la
red. Cualquier intento existente impide otra llamada, incluso tras timeout,
error HTTP, interrupción o resultado incierto. No borrar ese ledger para
reintentar. No hay retries, redirects ni proxies heredados del entorno.

`.artifacts/response.raw` conserva el cuerpo recibido sin imprimirlo;
`result.json` contiene estado local, código HTTP y, cuando hay una excepción,
una categoría controlada de error; nunca headers, clave, texto del documento
ni mensajes de excepción. Un timeout puede haber consumido
créditos aunque no exista respuesta. Una respuesta exitosa debe revisarse localmente
para comprobar integridad de tabla, páginas y bloques; no demuestra calidad OCR.
Los artefactos están ignorados por Git y pueden contener datos del documento.

## Observabilidad segura (sin repetir el intento)

Las nuevas ejecuciones emiten eventos a **stderr con flush inmediato**:
`phase`, timestamp UTC, `elapsed_s` monotónico, `category` y `status` HTTP
(si se conoce). No se vuelcan objetos de excepción, credenciales, rutas de
credencial, headers ni cuerpos de respuesta.

Fases: carga/validación de credencial, validación PDF, reserva durable del
ledger, inicio HTTP, código HTTP recibido, raw guardado, resultado guardado y
fin. Los fallos indican su fase y categoría: `timeout`, `network_error`,
`poppler_timeout`, `poppler_error`, `invalid_input`, `existing_artifact`,
`local_io_error`, `interrupted` o `unexpected_error`.
El mensaje final es **“Intento registrado; consumo desconocido”**, no una
confirmación de cobro ni de calidad OCR. Una interrupción durante HTTP intenta
guardar el resultado antes de propagar `KeyboardInterrupt`; el ledger permanece.

Cada subprocess Poppler tiene timeout de 60 s. Los 330 s del cliente HTTP no
son un deadline global ni garantizan progreso del servidor; durante la espera
HTTP puede no haber eventos nuevos. Los logs marcan fases locales, no porcentajes
de OCR. No hay polling, retries ni llamadas adicionales para mostrar progreso.
Los nuevos logs no reconstruyen la causa del intento histórico ni modifican
sus artefactos. Las pruebas usan temporales, mocks y credenciales falsas.

Mapping del manifest: fragmento **1 → original 13**, **2 → original 14**.
No modificar el documento fuente ni incorporar este experimento a la aplicación.
