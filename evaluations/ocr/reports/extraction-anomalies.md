# Revisión visual: p54 requiere recuperación gráfica; p62 no contiene cuerpo útil

Se abrieron efectivamente **cinco imágenes**: las nuevas páginas físicas 54, 62 y 22 del DOCUMENTO TECNICO RENTA JOVEN, y los renders existentes 13–14 del piloto. No se dedujo contenido visual desde el texto extraído. Esto es revisión local/manual, **no OCR nuevo, aprobación institucional ni validación del corpus**.

## Hallazgos y ruta necesaria

Rutas de evidencia relativas a `evaluations/ocr/`:

| Física | Evidencia visual | Contraste directo y ruta propuesta |
|---|---|---|
| Técnica 54 | [Render](../.artifacts/review-v1/technical-p54.png): diagramas con bloques de Insumos, Productos y Resultados; título «5.5.3. Teoría del cambio». Contenido útil gráfico, no blanco ni formulario. | Extracción directa: **72 caracteres**, insuficientes para los bloques. Requiere recuperación de texto/layout de las ilustraciones y verificación visual; cualquier OCR adicional exige autorización separada. No se transcriben aquí los bloques pequeños ni rótulos desvanecidos. |
| Técnica 62 | [Render](../.artifacts/review-v1/technical-p62.png): blanco salvo folio «62» inferior. No hay tabla, formulario ni cuerpo visible. | **2 caracteres**, correspondientes al folio. Registrar como página sin cuerpo útil en este render, preservando mapping físico; no enviarla a OCR por el umbral solo. No se afirma ausencia de objetos invisibles del PDF. |
| Técnica 22, control | [Render](../.artifacts/review-v1/technical-p22.png): prosa y «Tabla No 1. Desempleo de jóvenes según sexo», con tres filas etarias y dos columnas de porcentajes representadas gráficamente. | **2501 caracteres** y título de tabla presente, pero **0/6 coincidencias literales** para las seis celdas numéricas inspeccionadas; consultas exactas en `direct-checks.json`. La capa directa no basta para esta tabla. Ruta: recuperación supervisada de celdas y estructura desde imagen, no propagar la suficiencia de la prosa a los valores. Sin OCR externo ni tabla reconstruida aquí. |

Los conteos se recalcularon solo para estas tres páginas, con la misma regla Unicode `len(page.strip())`; no son métricas de precisión. La ausencia de seis cadenas exactas es una comprobación limitada, no un análisis de toda posible representación numérica. La imagen sí muestra las celdas omitidas.

## Overlay manual del piloto 13–14

[Overlay propuesto](../.artifacts/review-v1/pilot-02-corrections.json), con tipo `derived_review_manual_overlay` y estado `proposed_not_applied`. Cada propuesta contiene antes/después, página física/fragmento, celda y ruta de render existente. Mapping: fragmento 1→física 13; 2→14.

- Física 13, Inscripción × Fecha Inicial: vacío extraído → **04 de agosto de 2026**, restituido manualmente desde imagen, no recuperado por OCR.
- Física 14, Reclamaciones × Actividad: «realizar realizaciones» → **«realizar reclamaciones»**.
- Verificación de Requisitos cruza 13–14: **dos segmentos, una actividad**. Fechas visibles en la continuación: 17 y 26 de agosto de 2026; no rellenar las celdas localmente vacías de la 13.
- Primera fila HTML de la 14: `thead/th` → propuesta de fila de datos continuada, sin eliminar ninguna celda.
- Tres propuestas de preservación de `colspan=2`: reuniones (26 y 31 de agosto de 2026) y publicación preliminar (26 de agosto de 2026). Una fecha por celda combinada, **no rangos inicial/final inventados**.

Cobertura: **7 propuestas**, ocho segmentos físicos y siete actividades lógicas. No se normalizó íntegramente la tabla: evita certificar cada celda no transcrita. No se completó el enlace vacío ni se reconstruyeron secciones o vigencia no vistas. El raw, `bbox`, `readingOrder` y `markdownSpan` permanecen intactos; el overlay conserva metadatos de `p1.b9` y `p2.b3` solo para procedencia, sin reinterpretar ni recalcular offsets. El problema de orden de lectura fuera de la tabla descrito en [pilot-02.md](pilot-02.md) no se corrige aquí.

## Verificación e integridad

Todos los comandos desde raíz: `PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --no-project --python 3.14 python evaluations/ocr/.artifacts/review-v1/` seguido del script indicado. Sin instalaciones; Python 3.14. Render y extracción: tres subprocess cada uno, timeout **8 s** individual, sin retries; bash acotado a 30 s. No hay herramienta background disponible. Comandos Docker completos y hashes de PNG: [render-checks.json](../.artifacts/review-v1/render-checks.json); consultas directas: [direct-checks.json](../.artifacts/review-v1/direct-checks.json).

| Script | Evidencia |
|---|---|
| `render-review.py` | Exit 0: tres PNG, imagen local `unitropico-pdf-audit:poppler-25.03.0-deb13u4`; `--pull never --network none --cap-drop ALL --security-opt no-new-privileges --read-only --user`; PDF readonly y única salida montada en el nuevo `review-v1`. |
| `check-direct.py` | Exit 0: conteos 72/2/2501; caption presente, seis valores ausentes por coincidencia literal. Texto completo procesado solo en memoria, no guardado. |
| `build-overlay.py` | Exit 0: siete propuestas manuales, sin cambiar payload ni offsets. |
| `validate-review.py` | Contrato estructural JSON v1 (campos/tipos/estado), cobertura, procedencia, valores originales y hashes; resultado observado en [validation.json](../.artifacts/review-v1/validation.json). No sustituye comparación visual. |

SHA-256 **antes = después** (valores completos también en `render-checks.json`, `direct-checks.json` y validación):

| Entrada preservada | SHA-256 antes y después |
|---|---|
| Técnico original | `46af91fb3a4efc942070444631141442e0343b75ff5584c5a77f9598f302fc0a` |
| Convocatoria original | `3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26` |
| Fragmento `pages-13-14.pdf` | `836713cefe6bf36f69832e3c88ac56288e4e238e61343f300ba6fe34814ff71a` |
| Segundo intento `response.raw` | `1ee79691cba239ed3847bfad5469c37c06c9a91d7105b07d3b2f6f46cfce9186` |

También se comprobaron sin cambios ambos PNG existentes de `validation-02`. Las escrituras JSON reutilizan `pilot.write_json` (creación exclusiva, flush/fsync); no ejecutar otra vez los generadores sobre estas salidas. Hashes no prueban ausencia de cambios transitorios concurrentes. No se tocaron fuentes, ledgers, raw, código extractor ni pruebas ajenas; no se hicieron pagos, prepare/send/polling, despliegues o aprobación automática.

**RED/GREEN no aplica:** documentación y overlay derivados, sin cambio de comportamiento ejecutable. Se verifica estructura, conteos/cobertura, procedencia y comparación visual efectiva, no tests ficticios ni precisión estimada. Próximo paso sujeto a autorización: recuperación gráfica supervisada de p54 y tabla p22; no requiere OCR la p62 según esta inspección.
