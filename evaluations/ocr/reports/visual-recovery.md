# Recuperación visual local: seis valores y diagramas, con pendientes explícitos

**Se recuperaron visualmente las seis celdas numéricas de la tabla física 22 y texto por regiones de los diagramas físicos 54.** Son transcripciones manuales propuestas: `artifacttype: derived_visual_transcription`, `requires_review: true`, sin aprobación pública/institucional. No son OCR de proveedor ni sustituyen el texto directo, raw o sus offsets.

## P22: datos legibles recuperados

Evidencia abierta efectivamente: [tabla a 450 dpi](../.artifacts/visual-recovery-v1/p22-table.png) y [contexto a 300 dpi](../.artifacts/visual-recovery-v1/p22-context.png). Título visible: «Tabla No 1. Desempleo de jóvenes según sexo.»; banda: «Desocupados».

| Edad, rótulo visible | Columna central, pictograma turquesa | Columna derecha, pictograma magenta |
|---|---|---|
| 14 – 19 años | 11,3% | 11,7% |
| 20-24 años | 21% | 27,6% |
| 25-28 años | 11,8% | 16,6% |

Las dos columnas de datos **no tienen título textual**, sino figuras humanas; se preservan como pictogramas, sin inventar etiquetas institucionales. Se conservan porcentajes como cadenas con coma, y `21%` sin decimal añadido. Los guiones etarios son representación visual, no garantía del código Unicode original del PDF. La nota de fuente también se transcribe desde el recorte contextual, sin convertirla en verificación independiente de esa fuente.

[Tabla JSON propuesta](../.artifacts/visual-recovery-v1/p22-table-transcription.json): tres filas, seis valores; cada celda y rótulo referencia página física, posición, render/crop, resolución, SHA del render y SHA de fuente. Esta recuperación queda separada del texto directo incompleto constatado en la unidad anterior.

## P54: regiones, no reconstrucción semántica

Se abrieron efectivamente [vista completa a 300 dpi](../.artifacts/visual-recovery-v1/p54-overview.png), [región superior a 450 dpi](../.artifacts/visual-recovery-v1/p54-inputs.png), [región inferior a 450 dpi](../.artifacts/visual-recovery-v1/p54-results.png) y [presupuesto a 600 dpi](../.artifacts/visual-recovery-v1/p54-budget.png).

[Transcripción JSON propuesta](../.artifacts/visual-recovery-v1/p54-diagrams-transcription.json): **11 regiones**, con título/leyenda, rótulos y bloques Normatividad, Presupuesto, Recursos, Documentos, Institucional, Productos y Resultados por plazo. Cada región conserva referencia de evidencia. Se representan ítems y saltos de línea como cadenas separadas; no se declara transcripción tipográfica facsimilar.

- **Dos regiones parciales:** código BPIN con secuencia de ceros no comprobada con seguridad incluso ampliado a 600 dpi; rótulos superiores de muy bajo contraste pendientes de segunda revisión. El hueco central superior no se completa con una etiqueta supuesta.
- **Tres flechas claramente visibles:** naranja hacia la derecha junto a Insumos (sin certificar destino), y las dos flechas entre las cajas inferiores Productos → Resultados de corto plazo → Resultados de mediano y largo plazo. Se registran solo relaciones gráficas, **no causalidad ni interpretación institucional**.
- No se reconstruyen secciones, vigencia, relaciones no visibles o texto faltante. P62 no se renderizó: se mantiene el hallazgo anterior de folio sin cuerpo útil.

## Comandos, controles y límites

Desde raíz, todos con prefijo exacto:

`PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1 uv run --no-project --python 3.14 python evaluations/ocr/.artifacts/visual-recovery-v1/`

| Sufijo del comando | Resultado observado |
|---|---|
| `render.py p22-table` | exit 0 |
| `render.py p54-overview` | exit 0 |
| `render.py p54-inputs` | exit 0 |
| `render.py p54-results` | exit 0 |
| `render.py p22-context` | exit 0 |
| `render.py p54-budget` | exit 0 |
| `transcribe.py` | exit 0: dos JSON exclusivos; 6 valores, 11 regiones, 3 flechas |
| `validate.py` | exit 0: esquema, estructura específica, 29 referencias, conteos y procedencia; 2 regiones parciales preservadas |

Cada render tiene su `*-checks.json` con comando Docker completo, crop en píxeles desde esquina superior izquierda a la resolución indicada, hash PNG y hashes de fuente antes/después. Imagen local aprobada `unitropico-pdf-audit:poppler-25.03.0-deb13u4`; `--pull never --network none --cap-drop ALL --security-opt no-new-privileges --read-only --user`; fuente readonly y único directorio de salida nuevo. Timeout subprocess **20 s** y tool **30 s**, sin retries ni timeouts observados.

SHA-256 fuente técnica **antes = después** en los seis renders, transcripciones y validación: `46af91fb3a4efc942070444631141442e0343b75ff5584c5a77f9598f302fc0a`.

[Validación](../.artifacts/visual-recovery-v1/validation.json): valida todas las palabras clave de aserción usadas en el [esquema de envelope](../.artifacts/visual-recovery-v1/transcription.schema.json) mediante comprobador stdlib local, más estructura específica de celdas/regiones/flechas y hashes. No es un validador general JSON Schema ni una prueba automática de fidelidad visual. Escritura JSON exclusiva reutilizando `pilot.write_json`; no sobrescribir ni reejecutar generadores sobre las salidas existentes.

**RED/GREEN no aplica** a evidencia/transcripción sin cambio del extractor. No se ejecutaron tests ficticios ni se estimó precisión. No se editaron artefactos/informe anteriores, fuentes, raw, ledgers o código ajeno; no se leyó el ensamblado paralelo `extraction-corpus-v1`. Sin OCR externo, pagos, prepare/send/polling, red o instalaciones. Los datos legibles quedan recuperados en artefactos derivados; su adopción sigue siendo propuesta sujeta a revisión, no validación institucional.
