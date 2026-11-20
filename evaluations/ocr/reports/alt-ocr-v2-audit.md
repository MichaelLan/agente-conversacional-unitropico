# Auditoría visual del OCR de Azure, tanda v2 (69 páginas)

**Azure v2 es la mejor fuente disponible para casi todas las páginas, pero tampoco sirve al 100 % tal cual.** De 69 páginas, **31 sirven sin tocar** (`use_azure`), **34 quedan fieles con correcciones literales** (`use_azure_with_corrections`) y en **4 conviene la fuente previa** (`prefer_previous`, Firecrawl lotes 01–03). **Ninguna requiere transcripción manual.**

- Clasificación: 8 `ok`, 23 `minor`, 38 `defect`, 0 `missing`.
- Correcciones: 75 en total, de las cuales **61 son necesarias** (sustantivas o de estructura) y 14 son opcionales (cosméticas). Todas se comprobaron por script: cada `find_text` aparece exactamente una vez en su página.
- Datos críticos: 284/309 exactos en el texto de Azure; los fallos son el sello de fecha de la Res. 00137, rótulos de diagramas y firmas.
- Alucinaciones: 9, todas ruido de iconos o rúbricas leído como caracteres sueltos. Omisiones: 3 (dos medias líneas en la Política y un cero del sello). Autocorrecciones de erratas: **0**.
- Regla de confianza: **no funcionó como filtro de páginas** (solo marcó DT p32). La confianza por palabra < 0,9 captó 23 de 34 errores de palabra (68 %) y ninguno de estructura.

Datos por página, correcciones exactas y plan de fuente final para las 216 páginas: [`alt-ocr-v2-audit.json`](alt-ocr-v2-audit.json).

## Método

- **Entrada:** `.artifacts/alt-ocr-v2/export/pages.json` (markdown, confianza por palabra y `needs_full_review`). La página física se toma del manifiesto.
- **Render:** cada página física del PDF original con PyMuPDF (`uvx --with pymupdf python -I`, desde el scratchpad) a 150 dpi completa y en tercios a 300 dpi. Tablas, cifras, fechas, URL, sellos y zonas dudosas se ampliaron a 400 dpi.
- **Revisión:** 7 revisores (subagentes) con un brief común. Cada uno leyó el markdown completo contra la imagen, fijó desde la imagen los datos críticos y comparó con la fuente previa de la página:
  - pdftotext del corpus v2;
  - Firecrawl previo (Res. 00137 y Convocatoria p13–14);
  - Firecrawl lotes 01–03 con sus correcciones, en las 8 páginas de gráficos.
- **Rúbrica:** la de las auditorías previas (`ok`/`minor`/`defect`/`missing`). Toda autocorrección y toda estructura de tabla que cambie el emparejamiento fila-valor cuenta como `defect`.
- **Verificación del coordinador:**
  - Res. 00137: p18 y el sello de fecha en p4.
  - Manual p47: Tabla 12.
  - Convocatoria p14: «eduo.co».
  - Política p17: la omisión, a 300 dpi.
  - Búsqueda de «caridad» en la Política.
  - Recuento por script de los 79 `find_text`: los 75 de este informe y los 4 del piloto.
- **Ajustes de armonización del coordinador:**
  - El sello «2 5 ENE 2024» de la Res. 00137 pasa de cosmético a sustantivo, igual que «2.5», porque es la fecha de la resolución. Así, 6 páginas pasan de `minor` a `defect`.
  - En Manual p75, la tabla de firmas separa un nombre de su cargo. Pasa a `defect`, con el mismo criterio que Política p23.
- **Restricciones:** sin red, sin llamadas de pago, sin `.env` y sin modificar `documents/` ni `.artifacts/`.

## Resultado por documento

| Documento | Págs. | ok | minor | defect | use_azure | con correcciones | prefer_previous | Corr. necesarias |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Doc. Técnico | 6 | 0 | 1 | 5 | 1 | 2 | 3 | 15 |
| Manual M-IP-36 | 14 | 4 | 0 | 10 | 4 | 9 | 1 | 14 |
| Res. 00137 | 12 | 1 | 0 | 11 | 1 | 11 | 0 | 14 |
| Convocatoria 2026-1 | 17 | 2 | 10 | 5 | 12 | 5 | 0 | 10 |
| Política Bienestar | 20 | 1 | 12 | 7 | 13 | 7 | 0 | 8 |
| **Total** | **69** | **8** | **23** | **38** | **31** | **34** | **4** | **61** |

## Comprobaciones pedidas

- **Res. 00137 p18:**
  - «igual o superior a 3.0» e «igual o superior a 4.0» son **correctos**. Era el fallo de los OCR previos.
  - $400.000 (matrícula y permanencia) y $200.000 (excelencia) están bien emparejados.
  - Solo falla el sello: «2.5 ENE».
- **Res. 00137 p1, 5, 17, 19 (línea vertical):**
  - En la imagen del PDF no hay línea interior: el perfil de oscuridad por columnas solo muestra el marco.
  - Los fragmentos que el Firecrawl previo cortaba salen completos y en orden: el título de p1, «Jefatura monoparental» y «Joven» en p5, el primer párrafo de p17 y el Parágrafo 3 de p19.
  - Los defectos previos de p14 («conditionada», «dispersion») y p4 (tildes) también desaparecen.
- **Sello de la Res. 00137:**
  - «25 ENE 2024», con los dígitos espaciados, sale «2.5» en p1, 13, 17 y 18, «2 5» en 6 páginas y bien en 1.
  - «00137» sale «0137» en p3, porque el primer cero del sello está desvaído.
  - Hay 1 corrección por página, 11 en total.
- **Manual p47:** la Tabla 12 es **correcta**: 28–38 h $200.000, 39–48 h $250.000, 49–59 h $300.000 y ≥ 60 h $350.000. El $250.000 erróneo del texto previo no se repite. Solo falta la tilde de «línea» en el título.
- **Convocatoria, «eduo.co»:** la errata del original «www.unitropico.eduo.co» se **conserva literal** en p14, y «reclamaciones» está bien escrito. En las 69 páginas Azure no autocorrige ninguna errata del original, por ejemplo «Trecientos», «pueblo Room», «Ley 1118 de 2008», «Estratégias» o «enlos».
- **Cronograma de la Convocatoria (p13–14):**
  - Todas las fechas quedan en su fila y columna: Inscripción del 04 al 14 de agosto de 2026 a las 6:00 pm, con la fecha inicial presente (Firecrawl la perdía); Verificación del 17 al 26 de agosto; Reclamaciones del 26 al 28 de agosto; reunión el 31 de agosto.
  - Los defectos son solo de estructura: filas partidas y una fila de continuación promovida a `<th>`. Se resuelven con 4 correcciones de estructura.
- **Política p12:**
  - Dice «calidad» en todas las apariciones.
  - La página **no tiene tabla de indicadores**. «caridad» solo existía en el pdftotext previo de p4 y p7, y Azure lee «calidad» en ambas.
  - La Tabla 1 de indicadores ocupa p22–24, con 5 columnas: Denominación, Nombre del Indicador, Estrategia, Área de Acción y Responsable.
    - p22: fiel celda por celda.
    - p23: una fila partida que parte también el responsable.
    - p24: la celda de área está partida en dos columnas y desordenada.
  - Hay 3 correcciones de estructura.
- **Tablas de la Res. 00137:**
  - p18 y p19 están bien emparejadas. En p19 sobra una columna vacía, que es cosmético.
  - En p13, el art. 1.5.4 entra como `<caption>` de la primera tabla y la fila Permanencia sale partida. Se resuelve con 3 correcciones de estructura.

## Patrones de error de Azure

1. **Estructura de tablas:** es el defecto más frecuente (12 páginas con corrección de estructura): filas partidas, filas de continuación como encabezado y celdas combinadas perdidas. No cambia palabras ni cifras, pero sí el emparejamiento.
2. **Diagramas multicolumna leídos por filas:** DT p17, p32, Manual p27 y p52. En los tres primeros conviene Firecrawl v1. En p52 basta reagrupar las viñetas.
3. **Llamadas de nota fusionadas con el año:** «20249», «202410», «IES4», «199413». Es el mismo fallo que pdftotext, y Azure les da confianza alta.
4. **Omisión de media línea limpia sustituida por fragmentos:** Política p16 («que max») y p17 («que foment los by»). Es un patrón nuevo y peligroso porque la página tiene confianza media alta.
5. **Letras leídas como dígitos** («capacidade 8», «voluntariad 0)») y **tildes perdidas** sueltas («jovenes», «Linea», «asi», «linea»).
6. **Ruido:** iconos, logos y rúbricas leídos como caracteres sueltos («91», «Ba», «Kb», «A6»). Fuera de `<figure>` cuentan como alucinación, pero son visibles y nunca contenido verosímil. No aparece ningún «%» leído como dígitos.

## Utilidad de la confianza

| Medida | Resultado |
|---|---|
| `needs_full_review` (media < 0,93 o > 10 % < 0,8) | 1 página marcada (DT p32), con 0 errores de palabra; 37 de las 38 páginas `defect` sin marcar |
| Errores de palabra con confianza < 0,9 | 23 de 34 (68 %) |
| Palabras < 0,9 que son error | ≈ 41 de 629 (≈ 6–7 %); el resto son viñetas, logos, iconos del pie y sellos |
| Errores de estructura detectables por confianza | 0 |

**Conclusión:**
- La regla de página **no funcionó** en esta tanda. Los escaneos son buenos (media 0,956–0,992), y los errores reales (estructura, años con nota, omisiones de media línea, tildes) no bajan la media.
- Revisar las palabras < 0,9 ayuda a cribar: detecta el sello, las omisiones de la Política y el bloque de firmas.
- Pero deja pasar un tercio de los errores de palabra y todos los de estructura. Las tablas, notas al pie y cifras siempre hay que contrastarlas con la imagen.

## Detalle por página

Columnas: clasificación · recomendación · correcciones necesarias/opcionales · confianza media.

| Página | Clase | Recomendación | Corr. | Conf. | Nota |
|---|---|---|---|---:|---|
| Doc. Técnico p17 | defect | `prefer_previous (firecrawl_v1_lots01_03)` | 3/0 | 0.981 | Alternativa equivalente: Azure con sustitución de la <table> por bloque literal (Zona Rural 2.983.689 (23,6 %) … |
| Doc. Técnico p21 | defect | `prefer_previous (firecrawl_v1_lots01_03)` | 2/0 | 0.966 | La corrección Fuente de Firecrawl (confianza medium) queda confirmada: Azure lee '2019' y coincide con la … |
| Doc. Técnico p30 | minor | `use_azure` | 0/0 | 0.990 | Corte 'Socioemocion/ales' es literal del círculo. |
| Doc. Técnico p32 | defect | `prefer_previous (firecrawl_v1_lots01_03)` | 0/0 | 0.939 | Revisión completa: texto de Azure sin errores de palabra; defecto solo estructural (diagrama 2 columnas leído … |
| Doc. Técnico p44 | defect | `use_azure_with_corrections` | 2/0 | 0.986 | Cadena del árbol: cada recuadro sale íntegro y por niveles (Impactos, Resultados, Objetivo general, específicos, Medios); … |
| Doc. Técnico p46 | defect | `use_azure_with_corrections` | 8/0 | 0.974 | Cadena de valor: niveles en orden correcto (Impactos, Resultados mediano/corto, Productos, Procesos, Actividades, Insumos); solo un … |
| Manual M-IP-36 p27 | defect | `prefer_previous (firecrawl_v1_lots01_03)` | 3/0 | 0.983 | Se omiten las correcciones editoriales opcionales ([Ciclo Operativo]/[Líneas]) de firecrawl_v1_audit por no ser literales. |
| Manual M-IP-36 p29 | ok | `use_azure` | 0/0 | 0.992 | Escapes '5\.' son formato markdown. |
| Manual M-IP-36 p41 | defect | `use_azure_with_corrections` | 2/0 | 0.983 | Prosa y Tabla 10 fieles; solo la nota fusionada IES4 y el rowspan perdido. |
| Manual M-IP-36 p47 | defect | `use_azure_with_corrections` | 2/0 | 0.985 | Punto crítico resuelto: Azure da $200.000 para 28-38 h (correcto). |
| Manual M-IP-36 p52 | defect | `use_azure_with_corrections` | 1/0 | 0.973 | Prosa fiel (comillas rectas = formato). Logo 'Impulso VOLUNTARIO' dentro de <figure>. |
| Manual M-IP-36 p61 | defect | `use_azure_with_corrections` | 1/0 | 0.981 | Azure trata el superíndice como dígito igual que pdftotext. |
| Manual M-IP-36 p62 | defect | `use_azure_with_corrections` | 1/0 | 0.981 | Erratas del original conservadas por Azure. |
| Manual M-IP-36 p63 | defect | `use_azure_with_corrections` | 1/0 | 0.975 | Erratas 'transito' sin tilde conservadas. |
| Manual M-IP-36 p64 | defect | `use_azure_with_corrections` | 1/0 | 0.984 | Título de Tabla 15 como encabezado '#' (formato). |
| Manual M-IP-36 p65 | ok | `use_azure` | 0/0 | 0.981 | Fiel. |
| Manual M-IP-36 p67 | defect | `use_azure_with_corrections` | 1/0 | 0.979 | Solo falta la tilde en el caption. |
| Manual M-IP-36 p68 | ok | `use_azure` | 0/0 | 0.980 | Fiel. |
| Manual M-IP-36 p74 | ok | `use_azure` | 0/0 | 0.983 | Fiel. |
| Manual M-IP-36 p75 | defect | `use_azure_with_corrections` | 1/0 | 0.991 | Texto 100 % fiel; solo estructura de la tabla de firmas. |
| Res. 00137 p1 | defect | `use_azure_with_corrections` | 1/0 | 0.988 | Cuerpo fiel. En este PDF no aparece ninguna línea vertical interior (perfil de columnas a 100 … |
| Res. 00137 p2 | defect | `use_azure_with_corrections` | 1/1 | 0.987 | Fiel. Solo formato: subrayado leído como '_', '2 5' en el sello, puntos suspensivos espaciados. |
| Res. 00137 p3 | defect | `use_azure_with_corrections` | 1/0 | 0.990 | Cuerpo fiel. Azure omite un cero del número de resolución en el encabezado (primer dígito del … |
| Res. 00137 p4 | defect | `use_azure_with_corrections` | 1/0 | 0.990 | Fiel; tildes correctas en toda la página. |
| Res. 00137 p5 | defect | `use_azure_with_corrections` | 1/0 | 0.989 | Fiel. No se ve línea vertical interior en este PDF; las líneas centrales están completas. |
| Res. 00137 p9 | defect | `use_azure_with_corrections` | 1/0 | 0.990 | Fiel. |
| Res. 00137 p11 | ok | `use_azure` | 0/0 | 0.988 | Fiel, sin errores. '1\.' es escape de markdown. |
| Res. 00137 p13 | defect | `use_azure_with_corrections` | 4/0 | 0.990 | Cifras correctas. Defectos: fecha '2.5', art. 1.5.4 metido como caption de la 1.ª tabla, fila Permanencia … |
| Res. 00137 p14 | defect | `use_azure_with_corrections` | 1/0 | 0.990 | Fiel. |
| Res. 00137 p17 | defect | `use_azure_with_corrections` | 1/0 | 0.990 | Cuerpo fiel; no hay línea vertical interior visible. Único error: el sello '2.5'. |
| Res. 00137 p18 | defect | `use_azure_with_corrections` | 1/0 | 0.989 | Tabla fiel (Permanencia+Excelencia en una celda, como el original). Único error: el sello '2.5'. |
| Res. 00137 p19 | defect | `use_azure_with_corrections` | 1/1 | 0.990 | Fiel; solo una columna vacía espuria en la tabla (no altera el emparejamiento por el rowspan). |
| Convocatoria 2026-1 p1 | minor | `use_azure` | 0/0 | 0.985 | Fiel. Ruido solo en <figure> y en iconos del pie. |
| Convocatoria 2026-1 p2 | minor | `use_azure` | 0/0 | 0.988 | Fiel; guion en lugar de raya (cosmético). |
| Convocatoria 2026-1 p3 | minor | `use_azure` | 0/0 | 0.988 | Fiel. '04464' dentro de <figure> es ruido del reverso (cosmético). |
| Convocatoria 2026-1 p4 | ok | `use_azure` | 0/0 | 0.988 | Fiel. |
| Convocatoria 2026-1 p5 | minor | `use_azure` | 0/0 | 0.983 | Fiel; ruido de reverso dentro de <figure>. |
| Convocatoria 2026-1 p6 | minor | `use_azure` | 0/0 | 0.982 | Fiel; viñetas ➢ leídas como '\>' y subrayado del req. 11 perdido (formato). |
| Convocatoria 2026-1 p7 | minor | `use_azure` | 0/0 | 0.989 | Fiel; erratas conservadas (verificado a 400 dpi). |
| Convocatoria 2026-1 p8 | minor | `use_azure` | 0/0 | 0.987 | Fiel. |
| Convocatoria 2026-1 p9 | minor | `use_azure` | 0/0 | 0.987 | Fiel. |
| Convocatoria 2026-1 p10 | minor | `use_azure` | 0/0 | 0.987 | Fiel. Ruido '4' en <figure> del logo y 'Q' por icono del pie: cosméticos. |
| Convocatoria 2026-1 p11 | defect | `use_azure_with_corrections` | 1/0 | 0.986 | Texto fiel (erratas 'Trecientos' y 'Room' conservadas). Solo defecto estructural en la tabla del tercer nivel. |
| Convocatoria 2026-1 p12 | ok | `use_azure` | 0/0 | 0.983 | Fiel. Comillas tipográficas como rectas (formato). |
| Convocatoria 2026-1 p13 | defect | `use_azure_with_corrections` | 2/0 | 0.984 | Texto y fechas fieles; solo estructura de tabla (filas partidas). |
| Convocatoria 2026-1 p14 | defect | `use_azure_with_corrections` | 2/0 | 0.986 | Errata 'eduo.co' conservada literal; 'reclamaciones' correcto; sin errores de texto ni fechas. |
| Convocatoria 2026-1 p15 | minor | `use_azure` | 0/0 | 0.983 | Fiel; solo viñetas en línea y celda vacía extra en la fila de continuación. |
| Convocatoria 2026-1 p16 | defect | `use_azure_with_corrections` | 1/0 | 0.988 | Prosa (Art. 15–17) fiel. Ojo: p15 dice 'días hábiles' y p16 'días calendario' — así en … |
| Convocatoria 2026-1 p17 | defect | `use_azure_with_corrections` | 4/4 | 0.956 | Cuerpo y fechas fieles. Errores concentrados en el bloque de firmas/visto bueno (tildes y rúbricas leídas … |
| Política Bienestar p1 | minor | `use_azure` | 0/1 | 0.984 | Texto fiel; solo ruido de rúbrica fuera de figura. Glifos sueltos de rúbricas/firmas al margen ('&', … |
| Política Bienestar p2 | minor | `use_azure` | 0/1 | 0.978 | Fiel; guiones '–' leídos como '-' (minor) y ruido de rúbrica. Glifos sueltos de rúbricas/firmas al … |
| Política Bienestar p3 | minor | `use_azure` | 0/2 | 0.978 | Firmantes correctos. 'Frutas' dentro de <figure> = trazo de firma (cosmético); PageNumber '6' espurio y 'of' … |
| Política Bienestar p4 | minor | `use_azure` | 0/1 | 0.983 | Llamadas de nota en superíndice aplanadas (Unitrópico1, jubilados2, deberes3): minor. Línea suelta 'compuesta por…' existe así … |
| Política Bienestar p7 | minor | `use_azure` | 0/1 | 0.978 | Fiel; viñetas '-' escapadas ('\-'), formato. Glifos sueltos de rúbricas/firmas al margen ('&', '☒', '$', 'gps', … |
| Política Bienestar p8 | defect | `use_azure_with_corrections` | 1/0 | 0.983 | Un único defecto: apóstrofo espurio en 'el'CESU'. Glifos sueltos de rúbricas/firmas al margen ('&', '☒', '$', … |
| Política Bienestar p9 | minor | `use_azure` | 0/0 | 0.987 | Superíndices 11/12 aplanados (comunidad11, evaluación12): minor. Glifos sueltos de rúbricas/firmas al margen ('&', '☒', '$', 'gps', … |
| Política Bienestar p10 | minor | `use_azure` | 0/1 | 0.985 | 'N°' leído como 'Nº' (no sustantivo); superíndices 11/12/13 aplanados. Glifos sueltos de rúbricas/firmas al margen ('&', … |
| Política Bienestar p11 | defect | `use_azure_with_corrections` | 1/0 | 0.984 | Única corrección: año 1994 + nota 13. Glifos sueltos de rúbricas/firmas al margen ('&', '☒', '$', … |
| Política Bienestar p12 | minor | `use_azure` | 0/1 | 0.982 | No hay tabla ni lista de indicadores en esta página (solo ejes con viñeta y notas … |
| Política Bienestar p13 | minor | `use_azure` | 0/0 | 0.979 | Fiel. Numeración 1-5 conservada; errata 'respecto' preservada. Ruido de firma/escudo ('\*','☒','K','\#') y pies en comentarios: cosmético. |
| Política Bienestar p14 | minor | `use_azure` | 0/0 | 0.983 | Fiel; numeración completa. Ruido de firma/escudo ('\*','☒','K','\#') y pies en comentarios: cosmético. |
| Política Bienestar p15 | minor | `use_azure` | 0/0 | 0.975 | Fiel; 'Vicerrectoría de Investigaciones' sin guion también en el original. Ruido de firma/escudo ('\*','☒','K','\#') y pies … |
| Política Bienestar p16 | defect | `use_azure_with_corrections` | 1/0 | 0.975 | Una omisión en '1- Arte y Cultura'; diagrama correcto. Ruido de firma/escudo ('\*','☒','K','\#') y pies en … |
| Política Bienestar p17 | defect | `use_azure_with_corrections` | 1/0 | 0.962 | Revisada completa: único fallo la línea de Actividad Física. Ruido de firma/escudo ('\*','☒','K','\#') y pies en … |
| Política Bienestar p18 | ok | `use_azure` | 0/0 | 0.984 | Fiel; cursivas no marcadas (formato). |
| Política Bienestar p20 | defect | `use_azure_with_corrections` | 1/0 | 0.982 | Un único error (tilde). 'no–maleficencia' con raya → guion: cosmético. Ruido de firma/escudo ('\*','☒','K','\#') y pies … |
| Política Bienestar p22 | minor | `use_azure` | 0/0 | 0.985 | Tabla fiel celda por celda; la tabla cruza p22→p23→p24. |
| Política Bienestar p23 | defect | `use_azure_with_corrections` | 1/0 | 0.985 | Una corrección estructural. Ruido de firma/escudo ('\*','☒','K','\#') y pies en comentarios: cosmético. |
| Política Bienestar p24 | defect | `use_azure_with_corrections` | 2/0 | 0.963 | Elegido Azure frente a pdftotext con parche (menos correcciones). Ruido de firma/escudo ('\*','☒','K','\#') y pies en … |

## Correcciones necesarias

Formato `text_corrections` (`page_number` = página física, `find_text`, `replace_text`). Aquí se muestran recortadas a 15 palabras y con «⏎» como salto de línea. El texto exacto, incluidas las correcciones de estructura sobre HTML de tablas, está en `corrections` del JSON. «Destino» indica sobre qué texto se aplica.

| Documento | Pág. | Destino | Tipo | Buscar | Reemplazar |
|---|---:|---|---|---|---|
| Doc. Técnico | 17 | firecrawl_v1 | substantive | `6.389.448 (50,6%) son hombres` | `6.241.974 (49,4%) son mujeres⏎⏎6.389.448 (50,6%) son hombres` |
| Doc. Técnico | 17 | firecrawl_v1 | substantive | `2.983.689⏎(23,6 %)` | `Zona Rural⏎2.983.689⏎(23,6 %)` |
| Doc. Técnico | 17 | firecrawl_v1 | substantive | `9.647.733⏎(76,4 %)` | `Zona Urbana⏎9.647.733⏎(76,4 %)` |
| Doc. Técnico | 21 | firecrawl_v1 | substantive | `Técnico o tecnologo` | `Técnico o tecnólogo` |
| Doc. Técnico | 21 | firecrawl_v1 | substantive | `1,8%⏎Son pobres` | `1,8%⏎Son pobres⏎⏎Fuente: Cálculos de Prosperidad Social basado en la Gran Encuesta Integrada de Hogares 2019` |
| Doc. Técnico | 44 | azure | substantive | `capacidade⏎8` | `capacidade⏎s` |
| Doc. Técnico | 44 | azure | substantive | `semillero⏎8` | `semillero⏎s` |
| Doc. Técnico | 46 | azure | substantive | `los jovenes pertenecientes` | `los jóvenes pertenecientes` |
| Doc. Técnico | 46 | azure | substantive | `productivamente⏎⏎: :⏎RESULTADOS` | `productivamente⏎⏎RESULTADOS` |
| Doc. Técnico | 46 | azure | substantive | `jóvenes⏎⏎I⏎⏎Fortalecimiento` | `jóvenes⏎⏎Fortalecimiento` |
| Doc. Técnico | 46 | azure | substantive | `voluntariad⏎0)` | `voluntariad⏎o)` |
| Doc. Técnico | 46 | azure | structure | `o hacia la⏎empleabilidad,` | `o hacia la⏎empleabilidad,⏎el⏎emprendimiento⏎y la educación⏎post gradual` |
| Doc. Técnico | 46 | azure | structure | `educación superior⏎⏎el⏎emprendimiento⏎y la educación⏎post gradual` | `educación superior` |
| Doc. Técnico | 46 | azure | substantive | `Linea compromiso` | `Línea compromiso` |
| Doc. Técnico | 46 | azure | substantive | `PROCESOS⏎⏎000000⏎⏎` | `PROCESOS⏎⏎` |
| Manual M-IP-36 | 27 | firecrawl_v1 | substantive | `Lineas⏎□ Focalización` | `Líneas⏎□ Focalización` |
| Manual M-IP-36 | 27 | firecrawl_v1 | substantive | `□ Inscripcion` | `□ Inscripción` |
| Manual M-IP-36 | 27 | firecrawl_v1 | substantive | `□ Verificacion` | `□ Verificación` |
| Manual M-IP-36 | 41 | azure | substantive | `<td>IES4</td>` | `<td rowspan="2">IES⁴</td>` |
| Manual M-IP-36 | 41 | azure | structure | `recibió la inscripción.</td>⏎<td></td>` | `recibió la inscripción.</td>` |
| Manual M-IP-36 | 47 | azure | structure | `<caption>Tabla 11. Montos de las TMC por linea de intervención y condicionalidad académica</caption>⏎` | `(vacío)` |
| Manual M-IP-36 | 47 | azure | substantive | `⏎⏎<table>⏎<tr>⏎<th>Línea de intervención</th>` | `⏎⏎<table>⏎<caption>Tabla 11. Montos de las TMC por línea de intervención y condicionalidad académica</caption>⏎<tr>⏎<th>Línea de intervención</th>` |
| Manual M-IP-36 | 52 | azure | structure | `COMPROMISO⏎SOCIAL PARA LA⏎TRANSFORMACIÓN⏎⏎PROMOCIÓN DE⏎DERECHOS Y⏎FORTALECIMIENTO⏎DE CAPACIDADES⏎⏎OPORTUNIDADES⏎PARA LA⏎MOVILIDAD SOCIAL⏎⏎· Encuentros⏎temáticos⏎⏎· Curso virtual de HpV⏎⏎· Empleo⏎⏎· Salud …` | `COMPROMISO⏎SOCIAL PARA LA⏎TRANSFORMACIÓN⏎⏎· Encuentros⏎temáticos⏎⏎· Encuentros⏎experienciales⏎⏎· Acción voluntaria⏎⏎Impulso⏎VOLUNTARIO⏎⏎PROMOCIÓN DE⏎DERECHOS Y⏎FORTALECIMIENTO⏎DE CAPACIDADES⏎⏎· Curso virtual de HpV⏎⏎· Salud …` |
| Manual M-IP-36 | 61 | azure | substantive | `de 20249)` | `de 2024⁹)` |
| Manual M-IP-36 | 62 | azure | substantive | `de 20249)` | `de 2024⁹)` |
| Manual M-IP-36 | 63 | azure | substantive | `de 202410)` | `de 2024¹⁰)` |
| Manual M-IP-36 | 64 | azure | substantive | `de 202410)` | `de 2024¹⁰)` |
| Manual M-IP-36 | 67 | azure | substantive | `TMC por linea de` | `TMC por línea de` |
| Manual M-IP-36 | 75 | azure | structure | `<tr>⏎<td>Deisy Astrid Morales Corba Profesional Especializada GIT Renta Joven</td>⏎<td rowspan="2">Jairo Fernando Contreras Subdirector de Transferencias …` | `<tr>⏎<td>Deisy Astrid Morales Corba Profesional Especializada GIT Renta Joven<br>Alexander González Coordinador del GIT Renta Joven<br>Diego …` |
| Res. 00137 | 1 | azure | substantive | `00137 DEL 2.5 ENE 2024` | `00137 DEL 25 ENE 2024` |
| Res. 00137 | 2 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 3 | azure | substantive | `0137⏎⏎DEL 2 5 ENE 2024` | `00137⏎⏎DEL 25 ENE 2024` |
| Res. 00137 | 4 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 5 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 9 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 13 | azure | substantive | `DEL 2.5 ENE 2024` | `DEL 25 ENE 2024` |
| Res. 00137 | 13 | azure | structure | `<table>⏎<caption>Artículo 1.5.4. Transferencia monetaria condicionada (TMC) por corresponsabilidad. Para los participantes que opten por incorporarse …` | `<table>⏎` |
| Res. 00137 | 13 | azure | structure | `</table>⏎⏎⏎<table>⏎<tr>⏎<th>RANGOS` | `</table>⏎⏎Artículo 1.5.4. Transferencia monetaria condicionada (TMC) por corresponsabilidad. Para los participantes que opten por incorporarse …` |
| Res. 00137 | 13 | azure | structure | `<td>Permanencia</td>⏎<td>$400.000</td>⏎<td rowspan="2">Se asigna una TMC por cada periodo académico, máximo dos veces al año</td>⏎</tr>⏎<tr>⏎<td>Estar registrado …` | `<td>Permanencia Estar registrado como matriculado en la base del SIMAT en grado 12 o 13, …` |
| Res. 00137 | 14 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 17 | azure | substantive | `2.5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 18 | azure | substantive | `2.5 ENE 2024` | `25 ENE 2024` |
| Res. 00137 | 19 | azure | substantive | `2 5 ENE 2024` | `25 ENE 2024` |
| Convocatoria 2026-1 | 11 | azure | structure | `<th>Grupos representativos de Deporte y Arte y</th>⏎<th>3 puntos</th>⏎</tr>⏎<tr>⏎<td>Cultura.</td>⏎<td></td>` | `<td>Grupos representativos de Deporte y Arte y Cultura.</td>⏎<td>3 puntos</td>` |
| Convocatoria 2026-1 | 13 | azure | structure | `<td rowspan="2">Inscripción. Término en el cual los estudiantes podrán inscribirse a los Apoyo Socioeconómico de …` | `<td>Inscripción. Término en el cual los estudiantes podrán inscribirse a los Apoyo Socioeconómico de Alimentación …` |
| Convocatoria 2026-1 | 13 | azure | structure | `<td>Verificación de Requisitos:</td>⏎<td>El Comité de Bienestar Universitario será la instancia responsable de</td>⏎<td rowspan="2"></td>⏎<td rowspan="2"></td>⏎</tr>⏎<tr>⏎<td>Nota. Serán …` | `<td>Verificación de Requisitos: Nota. Serán admitidos dentro de la convocatoria</td>⏎<td>El Comité de Bienestar Universitario será …` |
| Convocatoria 2026-1 | 14 | azure | structure | `<th>únicamente los aspirantes que cumplan con los requisitos establecidos en el artículo 5 del presente …` | `<td>únicamente los aspirantes que cumplan con los requisitos establecidos en el artículo 5 del presente …` |
| Convocatoria 2026-1 | 14 | azure | structure | `<td>Publicación de Listado Preliminar de Potenciales Beneficiarios.</td>⏎<td>La publicación de la lista de admitidos se publicará …` | `<td>Publicación de Listado Preliminar de Potenciales Beneficiarios. El Comité de Bienestar Universitario dará a conocer …` |
| Convocatoria 2026-1 | 16 | azure | structure | `alimentación a la cuenta⏎bancaria aportada durante el⏎proceso de inscripción.⏎⏎mes, de acuerdo con el periodo académico⏎establecido …` | `<table>⏎<tr>⏎<td></td>⏎<td>alimentación a la cuenta bancaria aportada durante el proceso de inscripción.</td>⏎<td colspan="2">mes, de acuerdo con …` |
| Convocatoria 2026-1 | 17 | azure | substantive | `Proyecto:` | `Proyectó:` |
| Convocatoria 2026-1 | 17 | azure | substantive | `Reviso: Karol` | `Revisó: Karol` |
| Convocatoria 2026-1 | 17 | azure | substantive | `Universitario Kb⏎A6⏎Reviso: Alieth del Rocio Avila` | `Universitario⏎Revisó: Alieth del Rocío Ávila` |
| Convocatoria 2026-1 | 17 | azure | substantive | `Contratación⏎⏎A⏎⏎` | `Contratación⏎⏎` |
| Política Bienestar | 8 | azure | substantive | `el'CESU` | `el CESU` |
| Política Bienestar | 11 | azure | substantive | `de 199413` | `de 1994¹³` |
| Política Bienestar | 16 | azure | substantive | `que max⏎niveles` | `que fomenta las expresiones culturales y artísticas en los⏎niveles` |
| Política Bienestar | 17 | azure | substantive | `que foment los by⏎través` | `que fomenta los hábitos y estilos de vida saludable a⏎través` |
| Política Bienestar | 20 | azure | substantive | `promoviendo asi,` | `promoviendo así,` |
| Política Bienestar | 23 | azure | structure | `<tr>⏎<td>Existencia de planes, programas y proyectos orientados a la</td>⏎<td>- Tasa de deserción anual.</td>⏎<td>-Acciones y actividades …` | `<tr>⏎<td>Existencia de planes, programas y proyectos orientados a la prevención de la deserción y a …` |
| Política Bienestar | 24 | azure | structure | `<th>Ambiente Saludables.</th>⏎<th>Sano y Entornos</th>⏎<th></th>` | `<th>- Ambiente Sano y Entornos Saludables.</th>⏎<th></th>` |
| Política Bienestar | 24 | azure | structure | `<td colspan="2"></td>` | `<td></td>` |

Correcciones opcionales (cosméticas: ruido de rúbricas, guion bajo de subrayado, columna vacía): 14, solo en el JSON.

Correcciones para las páginas del piloto en que el plan elige Azure (`pilot_corrections`, verificadas una vez en el markdown del piloto):

| Documento | Pág. | Buscar | Reemplazar |
|---|---:|---|---|
| Res. CBU 002-2025 | 12 | `cronograma aqui dispuesto` | `cronograma aquí dispuesto` |
| Res. CBU 002-2025 | 12 | `web de Unitropic www` | `web de Unitrópico www` |
| Doc. Técnico | 16 | `académica⏎8⏎` | `académica⏎s⏎` |
| Doc. Técnico | 54 | `argo plaza` | `largo plazo` |

## Plan de fuente final para el corpus completo (216 páginas)

Combina cuatro fuentes:
- el texto reutilizable `ok`/`minor` (110 páginas);
- Firecrawl lotes 01–03 con las correcciones de `selective-ocr-v1-audit`;
- el piloto Azure, según la comparación ciega;
- Azure v2, según esta auditoría.

La lista por documento y página física está en `final_source_plan` del JSON. Cada entrada indica la fuente, las correcciones necesarias y opcionales, su referencia y si hace falta transcripción manual.

| Documento | Págs. | Reutilizable | Firecrawl v1 | Azure piloto | Azure v2 | Corr. necesarias | Transcr. manual |
|---|---:|---:|---:|---:|---:|---:|---:|
| Res. CBU 002-2025 | 16 | 0 | 14 | 2 | 0 | 42 | 0 |
| Boletín | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| Doc. Técnico | 62 | 45 | 10 | 4 | 3 | 29 | 1 |
| Manual M-IP-36 | 75 | 51 | 10 | 1 | 13 | 17 | 0 |
| Res. 00137 | 20 | 8 | 0 | 0 | 12 | 14 | 0 |
| Convocatoria 2026-1 | 17 | 0 | 0 | 0 | 17 | 10 | 0 |
| Política Bienestar | 24 | 4 | 0 | 0 | 20 | 8 | 0 |
| **Total** | **216** | **110** | **34** | **7** | **65** | **120** | **1** |

**Para llegar al 100 %:**
- **120 correcciones literales** en 59 páginas: 61 de esta auditoría, 55 de la auditoría de los lotes 01–03 y 4 del piloto.
- **1 sustitución manual parcial:** el diagrama de Insumos de DT p54. Se transcribe a mano el bloque intercalado; el resto de la página de Azure piloto es fiel tras 1 corrección.
- Las otras 157 páginas se usan tal cual. Hay 21 correcciones opcionales (cosméticas o editoriales).

Decisiones de fuente:
- **Reutilizable:** las 110 páginas `ok`/`minor` de la auditoría de extracción (102 pdftotext y 8 Firecrawl previo de la Res. 00137), sin cambios.
- **Firecrawl v1, 34 páginas:**
  - las de los lotes 01–03 que no repasaron el piloto ni la v2;
  - Res. CBU p3 y p13 y DT p22 del piloto, donde Azure era peor;
  - DT p17, p21, p32 y Manual p27 de esta tanda.
  - En Res. CBU p3 hay que releer la página tras sus 17 correcciones (estaba en `manual_review` en v1).
- **Azure piloto, 7 páginas:** Res. CBU p12 y p14, DT p16, p18, p20 y p54, Manual p38.
- **Azure v2, 65 páginas:** las restantes de esta tanda, entre ellas las 12 de la Res. 00137, las 17 de la Convocatoria y las 20 de la Política.

## Limitaciones

- La revisión no fue ciega: los revisores veían la fuente previa, porque había que elegir la fuente final. Fueron 7 revisores con un brief común, y el coordinador verificó una muestra. Puede haber diferencias de criterio entre `minor` y `defect`; el coordinador armonizó el sello de la Res. 00137 y la tabla de firmas del Manual p75.
- `ok` no es una certificación carácter por carácter. A 300 dpi pueden quedar errores de un carácter en notas al pie o sellos no ampliados a 400 dpi.
- Las correcciones de estructura reescriben fragmentos de HTML de tabla. Son literales en las palabras, pero su `find_text` es largo. En el informe se muestran recortadas.
- Los recuentos de errores frente a confianza son aproximados, porque algunos errores abarcan varios tokens o son omisiones. El plan para las páginas del piloto usa los recuentos de la comparación ciega; sus 4 correcciones Azure se formularon en este informe.
- Los gráficos se clasificaron `minor` cuando Azure lista los valores sin emparejarlos con categoría o año, como en DT p18 y p20 del piloto. Si el RAG necesita esos emparejamientos, hay que añadirlos como transcripción editorial.
