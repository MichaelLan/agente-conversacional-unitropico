# Auditoría de calidad de extracción página a página (corpus v2)

**Ninguno de los 7 PDF sirve al 100 % tal como está.** Con el criterio del usuario («usamos lo que ya tenemos siempre y cuando sirva al 100 %»), solo pueden reutilizarse tal cual **110 de 216 páginas** (66 `ok` + 44 `minor`). Además, **86 páginas** necesitan OCR (≈ **86 créditos** de Firecrawl a 1 crédito/página) y **20** se pueden resolver con una corrección literal puntual. Si esas 20 también se mandan a OCR, el total sube a **106 créditos**.

Detalle por página (clase, tipo de defecto, ejemplo breve, acción): [`extraction-quality-audit.json`](extraction-quality-audit.json).

## Alcance y método

- Entrada: `.artifacts/extraction-corpus-v2/documents.json`. Para cada página se toma el markdown de Firecrawl si existe (Res. 00137 p1–20 y Convocatoria p13–14) y, si no, el texto de `pdftotext`. Nada en `documents/` ni en `.artifacts/` se modificó.
- Cada página se renderizó en local con PyMuPDF a 150 dpi y se comparó visualmente con su texto completo. En tablas, cifras y letra pequeña se amplió a 220 dpi. **Se revisaron visualmente completas las 201 páginas con texto**; no hay páginas `unreviewed` ni muestreo. Las 15 páginas sin texto de la Convocatoria se confirmaron con una miniatura: todas tienen contenido denso, con tablas en p10, 11, 15 y 16.
- Clases: `ok` (contenido sustantivo completo y fiel), `minor` (solo diferencias no sustantivas: viñetas sueltas, cortes de guion, encabezados, marcas de nota pegadas sin alterar palabra ni cifra), `defect` (falta o cambia contenido sustantivo) y `missing` (sin texto).
- Ajuste de coherencia del coordinador: los URL de bibliografía del Documento Técnico que perdieron el guion en el corte de línea (p57, 58, 59, 61) pasaron de `minor` a `defect` con parche literal, igual que la p5. Un URL mal citado no es literal.
- No hubo llamadas de pago, red, `.env` ni LLM externos.

## Resultado por documento

| PDF | Págs. | ok | minor | defect | missing | Reutilizar | Parche literal | OCR Firecrawl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2. Resolución CBU No. 002-2025 | 16 | 0 | 0 | 16 | 0 | 0 | 0 | **16** |
| Boletín Renta Joven 01 VA | 2 | 0 | 2 | 0 | 0 | 2 | 0 | 0 |
| Documento Técnico Renta Joven | 62 | 36 | 9 | 17 | 0 | 45 | 6 | **11** |
| M-IP-36 Manual Programa Renta Joven | 75 | 29 | 22 | 24 | 0 | 51 | 3 | **21** |
| Res. 00137 (ya es OCR Firecrawl) | 20 | 1 | 7 | 12 | 0 | 8 | 5 | **7** |
| Convocatoria Pública 2026-1 (Res. CBU 001-2026) | 17 | 0 | 0 | 2 | 15 | 0 | 0 | **17** |
| Política de Bienestar | 24 | 0 | 4 | 20 | 0 | 4 | 6 | **14** |
| **Total** | **216** | **66** | **44** | **91** | **15** | **110** | **20** | **86** |

### Páginas físicas a reprocesar con OCR

- **Res. CBU 002-2025 (16):** p1–16, el documento completo.
  - Es un escaneo con una capa OCR propia de mala calidad. En todas las páginas hay letras mal reconocidas y palabras sustituidas por otras en inglés o por palabras reales distintas: «ComitO», «International», «Resolución Pectoral» por «Rectoral».
  - En p3 hay años erróneos: «de 2324» por 2024 y «2024-2C2S».
  - En p10 los montos llevan coma en vez de punto: «$569,400» por «$569.400».
  - En p9 la «o» se lee como «0».
  - Los cronogramas de p12–14 salen columna por columna, así que la fecha y la actividad quedan sin emparejar.
- **Documento Técnico (11):** p16, 17, 18, 20, 21, 22, 30, 32, 44, 46, 54.
  - Los valores de figuras y gráficos solo existen como imagen: Ilustraciones 1–7, 9, 11, 12 y 14, y la Tabla 1 de p22 (seis porcentajes).
  - En p18 los porcentajes del gráfico de anillo salen sin el grupo al que corresponden.
  - En p32 se mezclan las dos listas de la Ilustración 10 y la Tabla 2.
  - En p54 la tabla de insumos es una imagen: ley, BPIN, presupuesto y resolución no aparecen en el texto.
- **Manual M-IP-36 (21):** p14, 19, 20, 23, 27, 28, 29, 35, 36, 38, 40, 41, 47, 52, 61, 63, 65, 67, 68, 74, 75.
  - Lo dominante son tablas aplanadas columna por columna, de modo que el emparejamiento entre fila y valor queda ambiguo.
  - **p47 es crítica.** En el texto, la Tabla 12 asigna $250.000 a «28 a 38 horas», cuando la imagen dice $200.000. El coordinador lo comprobó de forma independiente.
  - p27, 29 y 52: texto de ilustraciones no extraído.
  - p23 y p28: palabras de encabezados desordenadas.
  - p61 y p63: año fusionado con el número de nota («de 20249»), además de tabla aplanada.
- **Res. 00137 (7):** p1, 4, 5, 14, 17, 18, 19.
  - **p18:** se omite «o superior» dos veces («igual a 3.0» / «igual a 4.0»), lo que cambia el requisito.
  - **p1, 5, 17, 19:** el texto queda cortado en una línea vertical del escaneo; salen fragmentos reordenados y se pierden 1–3 caracteres.
  - **p14:** «conditionada» (×6) y «dispersion» (×4).
  - **p4:** varias palabras mal escritas y tildes perdidas.
- **Convocatoria 2026-1 (17):** p1–12 y p15–17 están sin texto. Las p13–14 tienen defectos ya conocidos:
  - p13: falta la fecha inicial de Inscripción y el orden de lectura está alterado.
  - p14: «realizaciones» en lugar de «reclamaciones», y una fila de datos aparece promovida a encabezado.
  - Hallazgo nuevo en p14: el texto corrige en silencio una errata del original, «eduo.co», que pasa a «edu.co». La transcripción ya no es literal.
- **Política de Bienestar (14):** p3, 4, 8, 9, 12, 13, 14, 15, 16, 17, 18, 20, 22, 23.
  - Es un escaneo con capa OCR. En p13–20 se pierde la numeración de las listas de principios, objetivos y líneas.
  - Hay errores de OCR que cambian el sentido: «caridad de vida» por «calidad».
  - p3: firmantes ilegibles en el texto.
  - p16: rótulos del diagrama dañados.
  - p22–23: celdas de la Tabla 1 de indicadores mezcladas entre filas.

### Páginas que admiten corrección literal puntual (20)

Una o dos palabras aisladas por página. Cada corrección debe registrarse como corrección manual contra la imagen, no como texto extraído.

| PDF | Páginas | Ejemplo |
|---|---|---|
| Documento Técnico | 5, 45, 57, 58, 59, 61 | URL «atencion-alciudadano» → «atencion-al-ciudadano»; en p45, «de población indígena» desplazado a otro ítem |
| Manual M-IP-36 | 18, 62, 64 | «hay» desplazado; «de 20249» → «2024» + nota 9 |
| Res. 00137 | 2, 3, 9, 11, 13 | «diseño» → «diseñar»; «vulnerability» → «vulnerabilidad»; «juvenile» → «joven» |
| Política de Bienestar | 1, 2, 7, 10, 11, 24 | «Poda cual» → «Por la cual»; «caridad» → «calidad»; «199413» → «1994» + nota 13 |

Si se aplica estrictamente el «al 100 %» y no se quieren correcciones manuales, estas 20 páginas se suman al OCR (106 créditos).

## Recomendación

1. **Reutilizar tal cual las 110 páginas `ok` y `minor`.** Esto incluye todo el Boletín y la mayor parte del Documento Técnico y del Manual, cuyo texto nativo es fiel en prosa, cifras, fechas y normas.
2. **OCR obligatorio, sin alternativa razonable (33 créditos):**
   - Convocatoria 2026-1: 15 páginas sin texto.
   - Res. CBU 002-2025: 16 páginas.
   - Documento Técnico: p22 y p54, ya documentadas.
3. **OCR recomendado para el resto de los defectos (53 créditos).**
   - En el Documento Técnico y el Manual (30 páginas), el problema está en tablas e ilustraciones, no en la prosa. Conviene conservar el texto nativo como control cruzado de la prosa y usar el OCR para tablas y figuras.
   - Política: 14 páginas.
4. **Res. 00137 y Convocatoria p13–14: precaución antes de volver a pagar.** Ese texto **ya es salida de Firecrawl**, y sus defectos son propios del proveedor sobre este escaneo: línea de corte vertical, «o superior» omitido, palabras en inglés y erratas del original corregidas en silencio. Reprocesar con la misma configuración podría repetir los fallos.
   - Opción A, según la regla del usuario: reprocesar p1, 4, 5, 14, 17, 18, 19 de Res. 00137 y p13–14 de la Convocatoria (9 créditos dentro de los 86) y volver a auditar el resultado.
   - Opción B: corrección manual contra la imagen. El overlay de 7 propuestas para p13–14 ya existe en `review-v1`.
   - En ambos casos el resultado debe revisarse visualmente antes de publicar.
5. **Ningún OCR nuevo debe darse por bueno sin auditoría.** Las únicas salidas de Firecrawl evaluadas (22 páginas) tienen 14 con defectos sustantivos y solo 1 `ok`. El texto nuevo debe pasar por esta misma comparación visual página a página, con atención a requisitos, montos y tablas.

## Limitaciones

- La comparación visual la hicieron siete revisores en paralelo (subagentes) con una rúbrica común. Puede haber diferencias de criterio en la frontera entre `minor` y `defect`; el coordinador solo armonizó el caso de los URL. Los conteos de `minor` y `ok` no constituyen una medida estadística de precisión.
- A 150 dpi, la letra muy pequeña (notas al pie, sellos, rótulos de baja densidad) puede tener errores de un carácter no detectados. El zoom a 220 dpi se aplicó selectivamente, no a todas las páginas.
- `ok` significa que no se detectó ningún defecto sustantivo tras comparar el texto completo con la imagen. No es una certificación carácter por carácter.
- Erratas presentes en el propio original («Ley 1118 de 2008», «agenda 2020-2023», «cconvocatorias», «trabado») se consideraron fieles cuando el texto las reproduce. Firecrawl corrigió algunas en silencio, y eso se registró como pérdida de literalidad.
- La estimación de créditos usa la tarifa documentada de 1 crédito/página. No incluye reintentos, no se consultó saldo y no incluye el consumo incierto de envíos anteriores.
- En el Documento Técnico, la p62 está en blanco salvo el folio y se clasificó `ok`. La p53 contiene el título de la ilustración cuyo diagrama está en la p54.
