# Preparación OCR restante — offline, unidad 1 de 2

Preparación local ejecutada **una sola vez**, exit code 0; ningún POST,
credencial, instalación ni gasto realizado. Autorización acotada: **35 páginas
adicionales, estimadas 35 créditos**. El techo histórico de 100 no autoriza
más páginas, llamadas ni reintentos. El consumo histórico sigue sin alterarse.

## Identidades verificadas (SHA-256 literal, independientes del manifest)

Fuentes fijas bajo `documents/documentos_bienestar/`:
- `Res00137ReglamentaRentaJoven.pdf`: 20 páginas, 19579889 bytes;
  `c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362`.
- `Resolución CBU No. 001 de 2026 - Convocatoria Pública 2026-1 (1).pdf`:
  17 páginas, 7700226 bytes;
  `3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26`.

Payloads en `evaluations/ocr/.artifacts/remaining-ocr-v1/`:
- `res137.pdf`: **20 páginas reales**, **19579889 bytes**, copia íntegra exacta;
  `c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362`.
  Mapping: payload 1..20 → fuente 1..20.
- `conv2026.pdf`: **15 páginas reales**, **7048730 bytes**;
  `2bc87c7392c170bd3546e5ed7bd8754aeaaecd2138f6d9ca6b33bef9e055dc79`.
  Mapping: payload 1..12 → fuente 1..12; 13→15, 14→16, 15→17.
  Las físicas originales 13–14 NO se generaron ni incluyeron en este lote.

Ambos payloads son menores de 50000000 bytes. Los cuatro hashes anteriores
se comprobaron también con `sha256sum` sobre archivos reales, no solo leyendo
el plan. La unidad 2 deberá fijarlos fuera de cualquier manifest mutable.

## Ejecución y controles

Comando único de preparación desde la raíz (YA EJECUTADO; **no repetir**):
`uv run --offline --no-project --python 3.14 python evaluations/ocr/remaining.py prepare`

Docker local `unitropico-pdf-audit:poppler-25.03.0-deb13u4`, sin pull/red,
sin capacidades, no-new-privileges y raíz read-only; fuente readonly.
Directorio nuevo exclusivo; cualquier existencia, incluso parcial, bloquea.
`pdfseparate` solo rangos 1..12 y 15..17; `pdfunite` lista numérica explícita.
Timeout extracción 15 s; `pdfinfo` reutilizado de pilot, 60 s por llamada;
ejecución real limitada externamente a 30 s, sin timeout ni retry observado.
`preparation.json` exclusivo final registra hashes antes/después de fuentes
y toda evidencia previa, sin cambios, mapping, tamaños y opciones propuestas:
markdown, OCR, maxPages 20/15, pages/blocks/pageMarkers true, timeout 300000.
Estado `prepared_offline_not_sent`; estimación 35, gasto de preparación 0.
Los PDFs intermedios permanecen únicamente en el directorio nuevo.

## Pruebas observadas

- RED: `PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests -p test_remaining.py` → exit 1, módulo remaining aún inexistente.
- GREEN: mismo comando → exit 0, 7 pruebas con mocks y temporales aislados.
- Suite: `PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests` → exit 0, 48 pruebas (41 previas + 7 nuevas).
- Sin cambios en helpers compartidos, fuentes, ledgers, extracción o transcripciones.
- No se evaluó calidad OCR: esta unidad solo prepara PDFs y no envía nada.
