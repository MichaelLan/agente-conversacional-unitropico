# Texto directo persistido; no ingesta ni OCR completado

Se guardaron **7 documentos, 216 registros de página física: 179 con texto
(177 candidatos directos, 2 escasos para revisión) y 37 sin texto pendientes de
OCR**. Todos llevan `requires_review: true`; nada está publicado. No se ejecutó
OCR, prepare/send, llamadas pagadas ni cambios de producción.

## Artefactos nuevos exclusivos

`evaluations/ocr/.artifacts/extraction-direct-v1/` (ignorado por Git):

- `documents.json`: **530883 bytes**, texto y procedencia de los siete PDF.
- `manifest.json`: **848 bytes**, cierre `complete_requires_review`, hash del
  JSON, imagen/comando y snapshot SHA-256 del único artefacto previo leído por
  el extractor: `corpus-audit.json`. Se comprobó su hash antes/después.

Cada documento identifica contenido mediante `sha256:<hash>`, nombre/ruta fuente,
SHA-256 y tamaños antes/después, número de páginas y páginas físicas 1-based.
Cada página conserva `text`, `method: pdftotext`, `charcount`, `status` y revisión
obligatoria. No se inventan sección, vigencia, documento sustituido ni página
impresa. Las páginas vacías no se filtran ni se rellenan con el piloto OCR.

Se decodifica UTF-8 sin traducción de saltos de línea. Solo se separa el terminador
U+000C: concatenar `text + '\f'` por página reconstruye el stdout original.
`strip()` se usa únicamente para la métrica Unicode de triaje 0/1–79/≥80.
Se validan delimitadores, páginas reales y métricas contra la auditoría existente,
sin volver a ejecutar `audit.py`. SHA-256/tamaño de las fuentes se verifican antes
y después; los siete coinciden. No se imprime texto documental.

## Ejecución y pruebas observadas

Desde la raíz, stdlib y CPython **3.14.6** vía uv offline, sin instalar dependencias:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests -p test_extract.py
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests
PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 timeout 28s uv run --no-project --python 3.14 python evaluations/ocr/extract.py
```

- RED: primer comando exit 1, `ModuleNotFoundError: No module named 'extract'`.
- GREEN: exit 0, inicialmente 4 pruebas; final 5 pruebas, exit 0.
- Suite OCR completa final: **30 pruebas, exit 0**. Mensajes prepare/send proceden
  exclusivamente de mocks/temporales, no operaciones reales.
- Extracción local única: **exit 0**, 14 subprocess Poppler exitosos; sin retries.
  No había herramienta background: foreground limitado a 28 s, no alcanzado.
- Verificación estructural posterior con Python 3.14: exit 0; comprobó hashes del
  JSON, auditoría y fuentes, tamaños, páginas consecutivas, métricas y revisión;
  confirmó 216 registros / 179 con texto / 37 pendientes. Sin imprimir contenido.

Las pruebas aíslan temporales y cubren Unicode, whitespace/CRLF intactos, vacías
internas/finales, límites 0/1/79/80, UTF-8/delimitadores/paginación inválidos,
mismatch contra auditoría, fallo/timeout del runner, mutación de fuente/auditoría,
procedencia, hash del JSON y rechazo de sobrescritura/reintento tras fallo.
No se modificaron helpers compartidos ni el piloto; la suite preserva sus contratos.

## Guardas y límites

Docker usa exclusivamente la imagen local
`unitropico-pdf-audit:poppler-25.03.0-deb13u4`, `--pull never --network none
--cap-drop ALL --security-opt no-new-privileges --read-only`, usuario local y
fuente montada readonly. Timeout individual 60 s; deadline externo 28 s en esta
invocación. La carpeta se reserva exclusivamente antes del trabajo. El manifiesto
se escribe al final con el escritor existente exclusivo, flush/fsync. Un fallo
puede dejar una reserva o archivo incompleto: no es éxito y no habilita retries;
exigir exit 0, manifiesto válido y hash concordante, nunca borrar para repetir.

Texto disponible **no equivale a fidelidad**, orden correcto, tablas completas ni
interpretación normativa válida. Las dos páginas escasas siguen en revisión;
las 37 vacías siguen pendientes. El OCR previo de páginas 13–14 tiene defectos y
no se reutilizó como verdad. No son 216 páginas completadas ni corpus listo para
producción. Los hashes detectan cambios finales, no alteraciones transitorias;
el tag de imagen no sustituye un digest inmutable.
