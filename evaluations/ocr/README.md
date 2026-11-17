# Piloto OCR: convocatoria 2026, páginas 13–14

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
