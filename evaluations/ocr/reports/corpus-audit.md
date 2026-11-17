# Corpus local: 37 páginas candidatas a OCR, no una evaluación de calidad

La auditoría recalculó **216 páginas físicas: 37 sin texto, 2 con 1–79 caracteres y 177 con ≥80**. Las 179 con texto no equivalen a 179 páginas correctas. Coincide con el histórico, sin usarlo como entrada. Los siete hashes SHA-256 y tamaños coinciden antes/después.

## Resultados por PDF

Fuente: `documents/documentos_bienestar/`. Conteos y hashes completos: `evaluations/ocr/.artifacts/corpus-audit.json`; `page_charcounts[i-1]` corresponde a la página física `i`.

| PDF | Bytes | Páginas | 0: candidato OCR | 1–79: revisión | ≥80: extracción directa candidata |
|---|---:|---:|---:|---:|---:|
| 2. Resolución CBU No. 002-2025.pdf | 594584 | 16 | 0 | 0 | 16 |
| Boletin Renta Joven 01 VA.pdf | 164768 | 2 | 0 | 0 | 2 |
| DOCUMENTO TECNICO RENTA JOVEN.pdf | 1660586 | 62 | 0 | 2 | 60 |
| M-IP-36 MANUAL PROGRAMA RENTA JOVEN V1.pdf | 848782 | 75 | 0 | 0 | 75 |
| Res00137ReglamentaRentaJoven.pdf | 19579889 | 20 | 20 | 0 | 0 |
| Resolución CBU No. 001 de 2026 - Convocatoria Pública 2026-1 (1).pdf | 7700226 | 17 | 17 | 0 | 0 |
| politica de bienestar.pdf | 8714503 | 24 | 0 | 0 | 24 |
| **Total** | | **216** | **37** | **2** | **177** |

Revisión escasa: DOCUMENTO TECNICO, páginas físicas **54 (72 caracteres)** y **62 (2)**. No se inspeccionó aquí su contenido ni se diagnosticó la causa.

## Método y reproducción

Herramienta stdlib: `evaluations/ocr/audit.py`, sin argumentos salvo `--help`. Usa exclusivamente la imagen local `unitropico-pdf-audit:poppler-25.03.0-deb13u4` y las guardas existentes de `pilot.py`; no llama a prepare/send ni a proveedores. Cada PDF se monta en `/source.pdf` readonly. Docker: `--pull never --network none --cap-drop ALL --security-opt no-new-privileges --read-only --user <uid>:<gid> --rm`.

Por PDF se ejecutó `pdfinfo /source.pdf` y `pdftotext -enc UTF-8 /source.pdf -`: **14 subprocess, todos exit 0**, timeout individual 60 s, sin retries. stdout de texto se procesa solo en memoria; no se imprime ni persiste. Se exige UTF-8 y un formfeed por página física, incluido el último; solo se elimina el segmento sentinel posterior al último formfeed. Se preservan páginas vacías internas y finales. La métrica es `len(page.strip())` en puntos de código Unicode, no bytes, palabras ni grafemas.

Inventario de exactamente siete PDF regulares no symlink, ordenado por nombre; hashes y tamaños tomados antes de cualquier Poppler y comprobados nuevamente al finalizar. El resultado se escribe exclusivamente con modo `x`, flush y fsync, reutilizando el escritor existente. Un artefacto previo bloquea la ejecución; no borrarlo para repetir. Mismatch, timeout, errores Poppler, UTF-8 o integridad devuelven exit 1 con categoría segura, sin éxito parcial. Un fallo de escritura puede dejar un archivo incompleto reservado: no interpretarlo como éxito ni sobreescribirlo.

## Comandos y evidencia observada

Ejecutados desde la raíz confirmada del repositorio, con `uv` y CPython **3.14.6**; sin instalaciones ni acceso de red solicitado:

| Comando | Exit / resultado |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests -p test_audit.py` | RED: 1, `ModuleNotFoundError: No module named 'audit'`, antes de crear implementación; GREEN: 0, 6 pruebas |
| `PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests` | 0, 25 pruebas offline; mensajes prepare/send son mocks y temporales, no operaciones reales |
| `PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.14 python evaluations/ocr/audit.py` | 0, `Audit complete: 7 PDFs.`; JSON exclusivo completo |

Las pruebas cubren Unicode, límites 0/1/79/80, páginas vacías internas/finales, formfeeds ausentes/excesivos, mismatch, UTF-8 inválido, errores seguros, timeout sin retry, cambio de hash, siete PDF exactos y no-overwrite. No se cambió código compartido; la suite existente preserva el comportamiento del piloto. Ejecución real foreground acotada: no había herramienta background disponible.

## Límites y próximos pasos

- **≥80 no garantiza calidad**, orden de lectura, tablas, fechas ni fidelidad. Cero caracteres puede ser una página genuinamente vacía; el umbral es solo triaje.
- Las páginas 13–14 de convocatoria 2026 ya tuvieron OCR externo, pero su PDF original sigue sin capa de texto. **No reenviar**: [pilot-02.md](pilot-02.md) registra fecha omitida, error textual y estructura de tabla incorrecta; ese piloto no es publicable automáticamente.
- Próximo paso propuesto: revisión visual supervisada de las dos páginas escasas y muestreo de extracción directa; cualquier OCR adicional exige autorización separada. No se seleccionó proveedor definitivo ni se tocó runtime productivo.
- Solo se añadieron herramienta, pruebas, este informe y el JSON nuevo; fuentes, ledgers, fragmentos y `response.raw` no fueron destinos de escritura. No se leyó credencial alguna ni se hicieron pagos, polling o envíos.
- Integridad por hashes no demuestra ausencia de cambios transitorios concurrentes ni reproducibilidad de un tag Docker mutable. La imagen debe seguir existiendo localmente; la salida existente impide repetir esta misma ejecución.
