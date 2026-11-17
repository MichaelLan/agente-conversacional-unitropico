# Piloto 02: útil para diagnóstico, no para publicar el cronograma sin revisión

**La extracción recupera las actividades, pero omite una fecha inicial y altera texto y estructura.** Sirve como insumo trazable para una futura normalización supervisada; no como cronograma fiable automático. Este contraste cubre únicamente las páginas físicas 13–14 de la convocatoria 2026: no valida el documento completo, el corpus ni una interpretación institucional.

## Evidencia y alcance

Rutas relativas a `evaluations/ocr/`:

- Respuesta conservada: `.artifacts/second-attempt/response.raw`; estado, procedencia y reserva: `result.json`, `manifest.json` y `attempt.json` del mismo directorio.
- Fuente visual: fragmento aprobado `.artifacts/pages-13-14.pdf`, páginas 1→13 y 2→14. SHA-256 comprobado: `836713cefe6bf36f69832e3c88ac56288e4e238e61343f300ba6fe34814ff71a`. El original identificado en `pilot.py` también coincide con su hash aprobado.
- Imágenes inspeccionadas directamente: `.artifacts/validation-02/page-1.png` (física 13) y `page-2.png` (física 14), renderizadas a 150 dpi. Comando, hashes y resultado: `.artifacts/validation-02/checks.json`.

El payload declara `success: true`, dos entradas en `data.pages` (`pageNumber` 1 y 2) y **13 + 5 bloques** en `data.blocks[].items`. El estado local registra HTTP 200 y `response_saved_no_replay`. Son hechos del resultado, no métricas de precisión.

## Contraste de las ocho filas aparentes

Fechas de agosto de 2026. Se revisaron actividad, descripción y celdas de fecha en ambas imágenes; «coincide» se limita a lo indicado, no implica equivalencia carácter por carácter.

| Fila / fuente física | Imagen original | Extracción y evaluación |
|---|---|---|
| 1. Divulgación / 13 | Inicial 04; final 14. Descripción de publicación y difusión. | Actividad, descripción y ambas fechas coinciden en contenido. |
| 2. Inscripción / 13 | Inicial **04**; final **14, 6:00 pm**. Formulario, rótulo «Enlace:» sin destino visible y notas 1–2. | Conserva formulario, notas y cierre; **omite la fecha inicial**, deja la celda vacía. No inventar un enlace a partir del rótulo vacío. |
| 3. Verificación de Requisitos / 13 | Comienzo de actividad y descripción; ambas celdas de fecha vacías en esta página. | Recupera ese comienzo y los vacíos locales; no son prueba de ausencia de fechas para la actividad completa. |
| 4. Continuación de Verificación / 14 | Continúa requisitos del artículo 5 y exclusión por soportes; descripción «Solo serán admitidos…». Inicial 17; final 26. | Texto y fechas corresponden a la continuación, pero quedan en `thead/th`, **no son encabezados ni otra actividad**. |
| 5. Reunión de Comité / 14 | Socialización interna del listado preliminar; **26**, celda combinada sobre las dos columnas de fecha. | Contenido y fecha coinciden; HTML conserva `colspan="2"`. |
| 6. Publicación del listado preliminar / 14 | Actividad y descripción de publicación; **26**, celda combinada. | Contenido y fecha coinciden; conserva `colspan="2"`. |
| 7. Reclamaciones / 14 | «Plazo establecido para realizar reclamaciones…»; descripción con canal y evidencias. Inicial 26; final **28, 6:00 p.m.** | Fechas y descripción coinciden en contenido; cambia **«reclamaciones» por «realizaciones»** en la actividad. |
| 8. Reunión Comité / 14 | Evaluación de reclamaciones; **31**, celda combinada. | Actividad, descripción y fecha coinciden; conserva `colspan="2"`. |

**Estructura:** son ocho segmentos de fila entre páginas, pero **siete actividades lógicas**: las filas 3–4 son una sola actividad continuada. La página 13 muestra los encabezados «Actividad», «Descripción», «Fecha Inicial», «Fecha Final», recuperados en Markdown. La 14 no repite esos encabezados: el HTML promueve indebidamente su primera fila de datos a encabezado. No se aprecia intercambio de columnas de fechas en las demás filas.

**Otro límite textual:** antes del artículo 14, la fuente dice «…la documentación requerida de conformidad con el cronograma aquí dispuesto». El Markdown separa «documentación requerida de conformidad» detrás de «con el cronograma…» e introduce una «d» aislada: hay alteración del orden de lectura fuera de la tabla. El título del artículo 14 y el apartado 11 son reconocibles; esto no certifica transcripción literal de toda la página.

## Condiciones para uso posterior

1. Preservar `response.raw` intacto y conservar tanto `pages[].markdown` como `blocks[].items`, incluidos identificadores, `bbox`, `readingOrder` y `markdownSpan`. Ejemplos: tabla física 13 `p1.b9`, span `[711, 1896]`; física 14 `p2.b3`, span `[1920, 3671]`. No recalcular ni reinterpretar offsets sobre texto corregido; comprobar su sistema de referencia antes de usarlos. Algunos bloques de cabecera/pie tienen span nulo.
2. Mantener por separado texto extraído, corrección propuesta y evidencia página/celda. Una eventual restitución del inicio de inscripción debe citar **física 13, Inscripción × Fecha Inicial**, no presentarse como dato recuperado por OCR. No se ha corregido el payload ni construido una tabla normalizada en este trabajo.
3. Unir la continuación de Verificación conservando ambas páginas y sus bloques; no descartar `thead` como si fuera cabecera repetida. Para las tres fechas con `colspan="2"`, conservar celda combinada y fecha única: **no inventar fecha inicial y final independientes**. No completar fechas vacías por propagación entre filas.

## Coste declarado y comprobaciones

- Segundo piloto: `data.metadata.creditsUsed = 2`; reserva local estimada: 2 créditos. Es declaración del proveedor, no conciliación de facturación.
- Primer intento: gasto incierto según el contexto y registro histórico descrito en `README.md`; no sumar como si el total confirmado fuera 2 o 4. No se consultó cuenta ni saldo.
- Render local: imagen `unitropico-pdf-audit:poppler-25.03.0-deb13u4`, `--pull never`, `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only`; PDF montado readonly y única salida en el nuevo directorio `validation-02`. Solo páginas 1–2 del fragmento, subprocess con timeout de 60 s: exit 0, dos PNG inspeccionados mediante herramienta de imagen.
- Hashes antes/después: original, ambos fragmentos y los cuatro archivos de evidencia del segundo intento sin cambios. No se regeneró el fragmento ni se efectuaron envíos, polling o llamadas de red.
- RED/GREEN no aplica: informe de evidencia, sin cambio de comportamiento ejecutable. Verificación realizada: lectura visual de ambas páginas, inspección local selectiva del JSON, conteos de páginas/bloques y comprobación de integridad. Sin suite de aplicación ni estimación estadística de precisión OCR.
