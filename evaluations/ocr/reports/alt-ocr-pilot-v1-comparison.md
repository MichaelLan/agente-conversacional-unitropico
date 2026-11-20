# Comparativa ciega Azure Document Intelligence vs Firecrawl (piloto OCR alternativo v1, 10 páginas)

**Azure `prebuilt-layout` es mejor que Firecrawl en 7 de las 10 páginas.**

- **Errores y datos:** comete menos errores sustantivos en las 9 páginas que no son escaneo desvaído (13 frente a 45) y acierta más datos críticos (129/141 = 91 % frente a 103/141 = 73 %).
- **Alucinaciones:** no inventa contenido semántico. Sus 4 «alucinaciones» son ruido de escaneo leído como caracteres sueltos. Firecrawl tiene 10, entre ellas texto inventado o en inglés y cifras de gráficos inventadas o mal emparejadas.
- **Dónde pierde Azure:**
  - En el escaneo desvaído (Res. CBU p3) deja 41 errores de palabra, frente a 14 de Firecrawl.
  - En la Tabla 1 del Documento Técnico (p22) lee el signo «%» como dígitos.
  - En la tabla de p13 parte una fila.
- **No cumple la regla de decisión estricta de §7** («0 alucinaciones»), aunque sus 4 casos son de naturaleza distinta (ver más abajo).
- **Muestra pequeña:** con 10 páginas la diferencia en páginas de texto y tablas no es concluyente. En gráficos y diagramas sí es clara.

Datos completos por página, datos críticos, correcciones y análisis de confianza: [`alt-ocr-pilot-v1-comparison.json`](alt-ocr-pilot-v1-comparison.json).

## Método

- **Fuentes:**
  - Azure: `.artifacts/alt-ocr-pilot-v1/export/azure/pages.json`.
  - Firecrawl: `data.pages[].markdown` de `.artifacts/selective-ocr-v1/execution/{01,02,03}/response.raw`, con el fragmento asignado a su página física mediante `manifest.json` (`lots[].mapping`).
  - Ambos estados verificados offline con `alt_ocr.py status` (azure 10/10 complete) y `selective_send.py status` (lotes 01–03 complete).
- **Ciego:**
  - Un script (`python -I`, PyMuPDF vía `uvx`) guardó cada par como A/B con `random.Random(20261120)`.
  - La clave (`key.json`, sha256 `004c7f52…2e24f`) no se abrió hasta tener las 10 evaluaciones.
  - Se neutralizaron las marcas que delataban al proveedor: `<!-- PageFooter="…" -->` se sustituyó por su texto y se eliminaron `<figure>`/`<figcaption>`.
- **Evaluación:**
  - 4 subagentes recibieron solo A/B, el render a 150 dpi y tercios a 300 dpi. Podían renderizar el PDF original a 400–600 dpi.
  - No tuvieron acceso a la clave ni a la auditoría previa.
  - Cada evaluador fijó la lista cerrada de datos críticos desde la imagen antes de leer A/B.
  - Rúbrica `ok/minor/defect/missing` de la auditoría v1. Toda autocorrección de una errata cuenta como `defect`.
- **Restricciones:** sin red, sin `.env` y sin modificar `documents/` ni `.artifacts/`.

## Resultado por página

Columnas: clasificación · correcciones sustantivas · alucinaciones · datos críticos exactos.

| Página | Azure | Firecrawl | Comentario |
|---|---|---|---|
| Res. CBU p3 (escaneo desvaído) | defect · 41 · 0 · 17/20 | defect · 14 · 0 · 15/20 | Ver nota 1 |
| Res. CBU p12 | defect · 2 · 0 · 10/11 | defect · 4 · 0 · 8/11 | Ver nota 2 |
| Res. CBU p13 | defect · 4 · 3 · 7/10 | defect · 3 · 0 · 8/10 | Ver nota 3 |
| Res. CBU p14 | **minor** · 0 · 0 · 10/11 | defect · 4 · 0 · 10/11 | Firecrawl: «Unitrófico» ×2, «Listado de Final Beneficiarios», «de estudiante» |
| Doc. Técnico p16 (árbol de problemas) | defect · 1 · 1 · 10/11 | defect · 15 · 7 · 1/11 | Ver nota 4 |
| Doc. Técnico p18 (anillo + pirámide) | **minor** · 0 · 0 · 16/16 | defect · 6 · 2 · 10/16 | Ver nota 5 |
| Doc. Técnico p20 (gráfico de líneas) | **minor** · 0 · 0 · 21/21 | defect · 2 · 1 · 18/21 | Firecrawl, fila 2010: 18.15 en la serie equivocada y «17.9» inexistente. Azure lista las 36 etiquetas sin año ni serie |
| Doc. Técnico p22 (Tabla 1) | defect · 3 · 0 · 12/15 | **minor** · 0 · 0 · 15/15 | Azure lee «%» como dígitos: «11,39%», «11,796», «11,89%» por 11,3 %, 11,7 %, 11,8 % |
| Doc. Técnico p54 (insumos + Ilustr. 14) | defect · 3 · 0 · 14/14 | defect · 10 · 0 · 9/14 | Ver nota 6 |
| Manual M-IP-36 p38 (tabla) | **ok** · 0 · 0 · 12/12 | defect · 1 · 0 · 9/12 | Firecrawl separa en dos filas la celda compartida Permanencia/Excelencia y deja vacía la frecuencia de Excelencia |
| **Total** | 1 ok · 3 minor · 6 defect · **54** · **4** · **129/141** | 0 ok · 1 minor · 9 defect · **59** · **10** · **103/141** | Sin p3: Azure 13 corr. y 112/121 datos; Firecrawl 45 corr. y 88/121 datos |

Notas:

1. **Res. CBU p3.** Azure transcribe glifo a glifo y deja palabras visiblemente rotas («acjudicacior ce Ics», «Socicecoromeo»). Firecrawl da un texto más limpio, pero con sustituciones plausibles («Unitrófico» ×3, «estudiante» por «estudiantil») y omite el pie, con 5 datos críticos.
2. **Res. CBU p12.** Firecrawl omite la fecha inicial de Inscripción («28 de octubre de 2025») y escribe «Unitrófico» ×2. Azure escribe «Unitropic».
3. **Res. CBU p13.**
   - Firecrawl **autocorrige «www.unitropico.eduo.co»** a «www.unitrópico.edu.co».
   - Azure conserva la errata, pero parte en dos filas la fila «Publicación de Listado Preliminar» (la fecha queda dividida en dos columnas) y añade ruido: «kopi», «| MI», «: 3ª».
4. **Doc. Técnico p16.**
   - Firecrawl inventa texto, parte de él en inglés: «intra-maternal/hospital», «Limited atención…», «inclusión pedagogica», «vita eductiva». También duplica las causas indirectas, omite cuadros y autocorrige «partipación».
   - Azure es fiel y solo añade «académica 8», donde lee como «8» una «s» cortada.
5. **Doc. Técnico p18.** Firecrawl inventa la cabecera «Category | Share (%)» y desplaza los 5 porcentajes una categoría. Los valores reales, verificados por color de píxel, son A 33, B 35, C 25 y D 7. Azure lista las cifras sin emparejarlas con categorías, así que no se equivoca.
6. **Doc. Técnico p54.**
   - Firecrawl: BPIN con un cero de menos, «DMP» por «DNP», «billiones», «pobreza vulnerable», tildes perdidas, e Ilustración 14 casi ausente.
   - Azure: todas las palabras y cifras exactas, incluido el BPIN, y la Ilustración 14 completa. Pero lee el diagrama de 5 columnas por filas e intercala texto de otras columnas (2 errores de estructura), y escribe «argo plaza» por «largo plazo».

### Hallazgos transversales

- **Las alucinaciones son de distinta naturaleza:**
  - Las de Firecrawl son contenido verosímil inventado: frases, palabras en inglés, cabeceras, cifras de gráficos y emparejamientos cifra-categoría. Pasan inadvertidas en una lectura rápida y en un RAG producirían respuestas falsas.
  - Las de Azure son fragmentos de ruido («kopi», «MI», «8») con confianza 0,07–0,65, salvo «: 3ª».
  - Formalmente ninguno de los dos cumple «0 alucinaciones».
- **Gráficos:** Azure no reconstruye tablas a partir de los gráficos. Lista las etiquetas en orden de lectura, sin asociar año, serie ni categoría. Por eso no inventa nada, aunque tampoco estructura los datos. Firecrawl estructura y se equivoca en 2 de 3 gráficos.
- **Tablas:** empate.
  - Azure mejor en p38 y p14. Firecrawl mejor en p13 (fila partida en Azure) y en p22 (signo %).
  - En el cronograma p12→p13→p14 ninguno inventa cabeceras de continuación. Las fechas de p14 son correctas en ambos.
  - Firecrawl pierde una fecha en p12. Azure parte una fecha en p13.
- **Autocorrecciones:** Azure 0. Firecrawl 2: «eduo.co» y «partipación».
- **Erratas de la auditoría previa** (detectadas por el evaluador ciego y verificadas por el coordinador sobre la imagen incrustada, 1227×758, por perfil de píxeles):
  - El BPIN de p54 es **`202300000000204`** (15 dígitos: 2023 + 8 ceros + 204).
  - La corrección propuesta en `selective-ocr-v1-audit.json` (`BPIN 2023000000000204`, 16 dígitos) y el ejemplo de `docs/research/ocr-alternatives.md` §7 tienen un cero de más.
  - La salida de Firecrawl (`20230000000204`) tiene un cero de menos.
  - **Hay que corregir esa entrada antes de aplicar las correcciones de la auditoría v1.** Este informe no modifica el archivo.
- **Coherencia con la auditoría v1 de Firecrawl:** el recuento ciego de correcciones coincide en p12, p13, p14 y p54, y difiere poco en el resto (p3 14 frente a 17; p18 6 frente a 2; p20 2 frente a 1). La rúbrica es reproducible.

## Utilidad de la confianza por palabra de Azure

Se tomaron los tokens erróneos de las correcciones sustantivas de lectura, sin omisiones ni errores de estructura (la confianza por palabra no puede detectarlos). Cada token se emparejó con su palabra en `words[]`.

| Conjunto | Errores | Umbral | Palabras bajo umbral | Sensibilidad | Precisión |
|---|---:|---:|---:|---:|---:|
| 10 páginas | 98 | 0,8 | 272 de 3545 (7,7 %) | 51 % | 18 % |
| Solo p3 (desvaído) | 88 | 0,8 | 167 de 516 | 48 % | 25 % |
| 9 páginas restantes | 10 | 0,8 | 105 de 3029 | 80 % | 8 % |
| 9 páginas restantes | 10 | 0,9 | 164 de 3029 | 100 % | 6 % |

- **En el escaneo desvaído la confianza no basta:** la mitad de los errores tiene confianza ≥ 0,8 (p. ej. «publicas» 0,951, «mediarte» 0,992).
- **En las páginas limpias es útil para cribar:** con umbral **0,9** se revisa ~5 % de las palabras y aparecieron los 10 errores. Pero n = 10 es una muestra mínima, y la precisión es baja (6–8 %), así que no sustituye a la revisión.
- **La confianza media por página sí separa bien el escaneo problemático:** p3 0,83 (32 % de palabras < 0,8); el resto 0,95–0,99 (p54 0,946). Propuesta de regla: **media < 0,93 o > 10 % de palabras < 0,8 ⇒ página de revisión completa**.
- **Lo que la confianza no capta:**
  - los errores de estructura (fila partida en p13, columnas intercaladas en p54);
  - el «%» leído como dígitos en p22 («11,796» con 0,899).

## Recomendación

### 61 páginas restantes (lotes 04–07)

**Usar Azure por defecto, con revisión dirigida, y una segunda lectura con Firecrawl solo en los escaneos de baja calidad.**

- **Coste:** Azure cuesta 0,61 USD en S0 (0 en F0), frente a 61 créditos de Firecrawl.
- **Texto y tablas** (Manual 2/2, Política de Bienestar, Res. 00137, Convocatoria):
  - Azure, con revisión visual completa de las tablas.
  - Comprobar en particular las celdas con «%», las filas partidas y los diagramas multicolumna.
  - Revisar en el texto corrido las palabras con confianza < 0,9.
- **Escaneos desvaídos** (detectados por la regla de confianza media):
  - Procesar también con Firecrawl y conciliar a mano.
  - Ninguno de los dos motores es fiable solo: Azure deja palabras rotas fáciles de ver, Firecrawl deja sustituciones verosímiles difíciles de ver.
  - La Convocatoria (sin capa de texto) y Res. 00137 son los candidatos.
- **Gráficos y diagramas:** Azure, porque no inventa. Las relaciones cifra-categoría y cifra-año se transcriben a mano, como prevé §7.
- **Alucinaciones:** la regla «0 alucinaciones» de §7 no la cumple ningún proveedor en sentido estricto. Mi recomendación se basa en que las alucinaciones de Azure son ruido visible y de baja confianza, y las de Firecrawl son contenido verosímil. **Si se aplica la regla al pie de la letra, el resultado es la rama «mixto con transcripción manual de figuras» de §7.3**, que coincide en la práctica con lo recomendado.

### Las 45 páginas ya procesadas con Firecrawl

**No reprocesarlas en bloque. Pasar por Azure solo las de gráficos y diagramas, y las que quedaron en `manual_review`** (≈8–10 páginas, ~0,10 USD).

- Las 118 correcciones de la auditoría v1 ya se verificaron con zoom, y el recuento ciego confirma que esa auditoría capta casi todos los errores de Firecrawl en texto y tablas.
- El riesgo residual de Firecrawl está en gráficos y diagramas (p16, p17, p18, p20, p44, p46, p54), donde inventa o desplaza valores. Ahí Azure aporta una lectura sin invenciones contra la que contrastar.
- Antes de aplicar las correcciones v1, **corregir la del BPIN (p54) a `202300000000204`**.
- Hipótesis, no medida: ejecutar Azure sobre las 45 cuesta ≤ 0,45 USD y permitiría usar las discrepancias entre motores para detectar errores. Valdría la pena medirlo en una segunda fase.

**Límites:** son 10 páginas de 3 documentos, y la mitad de los errores de Azure están en una sola página. Las diferencias en tablas (2 frente a 2) y en texto limpio (13 frente a 45 correcciones, sobre todo por p16) no son estadísticamente concluyentes. La ventaja de Azure en gráficos y diagramas (3 páginas, 0 frente a 10 alucinaciones) es la evidencia más sólida.
