# Auditoría visual del OCR selectivo v1 (lotes 01–03)

**El OCR nuevo de Firecrawl mejora mucho el texto anterior, pero no sirve al 100 % tal cual.** De las 45 páginas de los tres lotes completos, solo **10 se pueden usar sin tocar** (`use_new`). Otras **32 quedan fieles tras aplicar correcciones literales puntuales** (`use_new_with_corrections`, 118 correcciones verificadas). Las **3 restantes necesitan revisión manual**: Res. CBU 002-2025 p3 y Documento Técnico p16 y p54.

Datos por página y lista de correcciones en formato máquina: [`selective-ocr-v1-audit.json`](selective-ocr-v1-audit.json).

> **Nota de corrección (2026-11-20):** la corrección propuesta para el BPIN de Documento Técnico p54 decía «2023000000000204» (16 dígitos), con un cero de más. El valor real, verificado sobre la imagen en la comparación ciega (`alt-ocr-pilot-v1-comparison`), es «202300000000204» (15 dígitos: 2023 + 8 ceros + 204). Firecrawl lo leyó como «20230000000204» (14 dígitos). Se corrigió solo esa entrada (resumen, tabla de la página y lista de correcciones); el resto del informe no cambia.

## Alcance y método

- Lotes auditados (estado `complete` en `selective_send.py status`): 01-res-cbu-002-2025 (16 págs.), 02-documento-tecnico (17) y 03-manual-m-ip-36-1of2 (12). **No se auditan** el lote 04 (estado incierto) ni los lotes 05–07 (sin enviar).
- Texto auditado: `data.pages[].markdown` de cada `execution/<lote>/response.raw`. La página del fragmento se asigna a su página física con `manifest.json` (`lots[].mapping`).
- Cada página física del PDF original se renderizó en local a 150 dpi con PyMuPDF y se comparó visualmente con el markdown completo. En tablas, gráficos, cifras, fechas, URL y zonas desvaídas se amplió a 200–400 dpi.
- Se usó la rúbrica de la auditoría previa (`ok` / `minor` / `defect` / `missing`), con un criterio adicional: **toda «autocorrección» de una errata del original cuenta como `defect`**. El texto debe ser literal.
- La revisión se hizo en 4 bloques paralelos con un brief común. El coordinador verificó de forma independiente 01 p5, 01 p12, 02 p18, 02 p20, 02 p21 y 02 p46, y confirmó los hallazgos.
- Se comprobó por script que cada `find_text` aparece **exactamente una vez** en el markdown de su página.
- No se modificó nada en `documents/` ni en `.artifacts/`. No hubo red, llamadas a Firecrawl ni lectura de `.env`.

## Resultado

| Lote | Págs. | ok | minor | defect | use_new | con correcciones | manual_review |
|---|---:|---:|---:|---:|---:|---:|---:|
| Res. CBU 002-2025 | 16 | 1 | 0 | 15 | 1 | 14 | 1 |
| Documento Técnico | 17 | 1 | 4 | 12 | 3 | 12 | 2 |
| Manual M-IP-36 (1/2) | 12 | 3 | 5 | 4 | 6 | 6 | 0 |
| **Total** | **45** | **5** | **9** | **31** | **10** | **32** | **3** |

Defectos de la auditoría previa: **corregidos en 27 páginas**, parcialmente en 15 y no corregidos en 3. Las 3 no corregidas son las URL de la bibliografía del Documento Técnico: p58, p59 y p61.

### Qué mejora

- **Res. CBU 002-2025.** Desaparecen las sustituciones del OCR anterior: «ComitO», «International», «Pectoral», «2324».
  - Los montos vuelven a usar punto ($569.400, $142.350, $284.700.000).
  - Las tablas de puntos quedan bien emparejadas.
  - En los cronogramas de p12–14 cada fecha queda en la fila de su actividad.
  - Todas las cifras, leyes y fechas verificadas son correctas.
- **Manual M-IP-36.** Todas las tablas que antes salían columna por columna se reconstruyen bien fila a fila (p14, 19, 35, 36, 38).
- **Documento Técnico.**
  - Ahora se extraen valores que antes solo existían como imagen: Ilustraciones 5, 6, 7, 10 y la Tabla 1 (p22).
  - La p45 recupera el orden de lectura.

### Qué falla (patrones)

1. **Tildes perdidas** en palabras sueltas: suspension, lineas, Politica, inversion, Inscripcion. Es el error más frecuente.
2. **Palabras sustituidas por otras parecidas:**
   - «Unitrófico» por «Unitrópico» (01 p3, p12, p14).
   - «falsidad», «verifiable», «prioritizados», «correspondencias» por «corresponsabilidades» (03 p29).
   - «posgados», «Junfrica».
   - Artículos cambiados: «de estudiante» por «del estudiante».
3. **Autocorrección de erratas del original** (pérdida de literalidad):
   - «periodo» pasa a «período» (01 p5, p7).
   - «los estudiante.» pasa a «los estudiantes.» (01 p2).
   - El dominio «www.unitropico.eduo.co» pasa a «www.unitrópico.edu.co» (01 p13). El OCR anterior de la Convocatoria ya había hecho lo mismo.
   - En sentido contrario, «financiera» pasa a «financieras» (03 p19).
4. **Gráficos convertidos en tabla con errores:**
   - Un valor inventado en la fila 2010 de la Ilustración 6: «17.9», que no existe en la imagen (02 p20).
   - Los porcentajes del gráfico de anillo emparejados con la categoría equivocada (02 p18).
   - Etiquetas y una cifra omitidas (02 p17).
   - Los gráficos usan punto decimal donde el original usa coma. Se registra como `minor` porque no cambia el valor.
5. **Diagramas densos:**
   - El árbol de problemas (02 p16) tiene texto inventado y en inglés («intra-maternal/hospital», «Limited atención»).
   - La cadena de valor (02 p44, p46) tiene palabras corruptas.
   - La Ilustración 14 (02 p54) está casi ausente.
6. **URL bibliográficas: el OCR no aporta nada.** Pierde los mismos guiones de corte de línea que el texto anterior (02 p57–p61).
7. **Una fecha de cronograma omitida:** falta la fecha inicial de Inscripción, «28 de octubre de 2025» (01 p12).
8. **Una cifra con dígitos perdidos:** el BPIN aparece como «20230000000204» y debe ser «202300000000204» (02 p54).

Las erratas del original se conservan bien en general: «pueblo Room», «se llevas», «por el cuál», «SOCIECONOMICO», «Apcyo».

Hay un error en la auditoría previa: en 01 p8 la imagen dice «SOCIECONOMICO», no «SOCIOECONOMICO». El OCR nuevo lo transcribe bien.

## Páginas que requieren revisión manual

- **Res. CBU 002-2025 p3.** El escaneo está desvaído. Las cifras son correctas, pero quedan 17 errores de palabra («acte», «descrrito», «discosiciones», «naran», «perico», «Unitrófico» ×3).
  - El JSON trae las 17 correcciones verificadas con zoom. Aplicándolas, la página quedaría fiel.
  - Se deja como `manual_review` porque con tantos errores no se puede descartar alguno no detectado.
  - «por a Resolución» → «por la Resolución» tiene confianza media: la «l» casi no se ve.
- **Documento Técnico p16 (Ilustración 1, árbol de problemas).** Los efectos y el problema central son fieles. El bloque de CAUSAS está corrupto: texto inventado, mezcla con inglés, una caja omitida y otras duplicadas. Hay que transcribirlo a mano.
- **Documento Técnico p54.** La tabla de Insumos queda bien con 10 correcciones, incluido el BPIN. La Ilustración 14 (≈21 ítems en 3 columnas) sigue casi ausente. Buena parte de su contenido se repite en la p55.

## Páginas «de corrección puntual»: OCR nuevo frente a texto reutilizable

De las 20 páginas de corrección puntual, 7 están en los lotes auditados.

| Página | Veredicto | Motivo |
|---|---|---|
| Documento Técnico p5 | new_better | new conserva el guion de la URL (old: atencion-alciudadano); resto equivalente. |
| Documento Técnico p45 | new_better | new corrige el orden de lectura del ítem 2 que old.txt desplazaba; resto equivalente. |
| Documento Técnico p57 | equivalent | new conserva el guion de 11102- pero arrastra los mismos guiones perdidos que old en las otras dos URL. |
| Documento Técnico p58 | old_better | Ambos pierden los mismos guiones de URL, pero new elimina dos entradas DNP (2012) repetidas que old conserva. |
| Documento Técnico p59 | equivalent | new y old tienen exactamente los mismos defectos de guiones en URL; new solo añade formato markdown. |
| Documento Técnico p61 | equivalent | new y old pierden los mismos tres guiones de URL; new solo añade formato markdown. |
| Manual M-IP-36 (1/2) p18 | new_better | new.md corrige el orden de 'hay' y conserva literal el resto (incl. typo 'se llevas'). |

En las páginas de bibliografía, enviar a OCR no mejora nada. En la p58 empeora: el original repite tres veces la entrada «DNP. (2012)» y el OCR la deja en una. Para estas páginas habría bastado la corrección literal sobre el texto reutilizable.

## Detalle por página

| Lote | Pág. física | Tipo | Clase previa → nueva | Corrige previos | Recomendación | Nº corr. | Defectos nuevos / notas |
|---|---:|---|---|---|---|---:|---|
| Res. CBU 002-2025 | 1 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: Acuerdo CS No. 069 / new: Açuerdo CS No. 069 (mota de escaneo leída como cedilla) |
| Res. CBU 002-2025 | 2 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: graduación de los estudiante. / new: graduación de los estudiantes.; img: beneficios, estímulos, incentivos / new: beneficios, estimulos, incentivos |
| Res. CBU 002-2025 | 3 | firecrawl_ocr | defect → **defect** | partial | `manual_review` | 17 | img: regulado por la Resolución (l desvaída) / new: regulado por a Resolución; img: entendiéndose como aquellos / new: entendírcose como acuellos; img: carácter general o particular / new: carácter genera o particular … |
| Res. CBU 002-2025 | 4 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 3 | img: busca reconocer la identidad / new: busca reconcer la identidad; img: reconociéndolos como titulares / new: reconociendolos como titulares; img: posibilitando así, la adecuada / new: posibilito así, la adecuada |
| Res. CBU 002-2025 | 5 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: el periodo académico cursado / new: el período académico cursado; img: deberá adjuntarse (j dañada) / new: deberá aduntarse |
| Res. CBU 002-2025 | 6 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: dentro de las líneas de acción / new: dentro de las lineas de acción |
| Res. CBU 002-2025 | 7 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 4 | img: discrepancias, falsedad o adulteración (x2) / new: falsidad (x2); img: completa y verificable / new: completa y verifiable; img: durante el periodo académico / new: durante el período académico |
| Res. CBU 002-2025 | 8 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: orientan la Política Integral / new: orientan la Politica Integral; img: Hayan incurrido en / new: Hayan incurido en |
| Res. CBU 002-2025 | 9 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: La decisión de suspensión será / new: La decisión de suspension será; img: La suspensión no implicará / new: La suspENSION no implicará |
| Res. CBU 002-2025 | 10 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: grupos priorizados determinados / new: grupos prioritizados determinados |
| Res. CBU 002-2025 | 11 | firecrawl_ocr | defect → **ok** | yes | `use_new` | 0 | Sin encabezado de logo; negritas perdidas; comillas curvas conservadas. Se preservan literales del original ('dar conocer', 'los Apoyo'). |
| Res. CBU 002-2025 | 12 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 4 | img: Inscripción Fecha Inicial 28 de octubre de 2025 / new: celda vacía; img: página web de Unitrópico / new: página web de Unitrófico; img: Contigo” de Unitrópico, Convocatoria / new: de Unitrófico, Convocatoria … |
| Res. CBU 002-2025 | 13 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 3 | img: en el artículo 5 del presente acto / new: en el articulo 5; img: la información del estudiante / new: la información de estudiante; img: www.unitropico.eduo.co / new: www.unitrópico.edu.co |
| Res. CBU 002-2025 | 14 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 4 | img: página web de Unitrópico (x2) / new: Unitrófico; img: Publicación Listado Final de Beneficiarios / new: Publicación Listado de Final Beneficiarios; img: a nombre del estudiante beneficiario / new: a nombre de estudiante beneficiario |
| Res. CBU 002-2025 | 15 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: su condición de beneficiario / new: su condicion de beneficiario |
| Res. CBU 002-2025 | 16 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: Oficina Asesora Jurídica y de Contratación / new: Asesora Junfrica y de Contratación |
| Documento Técnico | 5 | literal_patch | defect → **minor** | yes | `use_new_with_corrections` | 1 | URL partida en dos líneas con etiquetas <u> (atencion-<u>al-</u> / <u>ciudadano...</u>); negritas markdown. Resto del glosario y nota 1 fiel |
| Documento Técnico | 16 | firecrawl_ocr | defect → **defect** | partial | `manual_review` | 0 | img: Bajas competencias académicas / new: Bajs competenci de academica a; img: Bajos ingresos para el sostenimiento / new: Bajs ingreso para la sostenimiento; img: limitadas competencias transversales de los jóvenes / new: Limitada oferta de capacitaciones transversales de los (duplicada) … |
| Documento Técnico | 17 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 3 | img: 6.241.974 (49,4%) son mujeres / new: (ausente); img: etiquetas "Zona Rural" y "Zona Urbana" de Ilustración 3 / new: (ausentes) |
| Documento Técnico | 18 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 2 | img (por color de leyenda): A 33%, B 35%, C 25%, D 7% / new: GRUPO SIS BEN 33, A 35, B 25, C 7, D vacío |
| Documento Técnico | 20 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 1 | img: 2010 Técnico profesional 22,46%, Tecnológico 18,15% / new: 18.15 \| 17.9 (22,46 ausente, 17.9 inventado) |
| Documento Técnico | 21 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: Técnico o tecnólogo / new: Técnico o tecnologo (sin tilde); img: Fuente: Cálculos de Prosperidad Social basado en la Gran Encuesta Integrada de Hogares 2019 / new: (ausente) |
| Documento Técnico | 22 | firecrawl_ocr | defect → **minor** | partial | `use_new_with_corrections` | 1 | Encabezados de columna de sexo vacíos (en la imagen son íconos: hombre turquesa, mujer rosa); "14 – 19" con guion simple. Prosa, cifras (46, |
| Documento Técnico | 30 | firecrawl_ocr | defect → **ok** | yes | `use_new` | 0 | Encabezado "Nivel 1..." como ## markdown. Literales del original preservados ("ilustración No 90", "ordenes", comillas de cierre sueltas). |
| Documento Técnico | 32 | firecrawl_ocr | defect → **minor** | yes | `use_new` | 0 | Encabezados markdown (####/###), viñetas "•" con cortes de línea internos ("Comunicación \nasertiva"). Literales "simultanea", "No10" preser |
| Documento Técnico | 44 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 10 | img: Contribuir a la integración de los jóvenes en procesos de acción social, política... / new: (ausente); img: educación post gradual de los jóvenes / new: educación postgradual de los jóvenes (2 veces post gradual); img: habilidades (x2) / new: habilitidades … |
| Documento Técnico | 45 | literal_patch | defect → **minor** | yes | `use_new` | 0 | Ítem d. convertido en encabezado "##" (formato). Cifras (decreto 1780 de 2024, A01 a B18, grupos A, B o C) y literales ("y o posgrado") fiel |
| Documento Técnico | 46 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 14 | img: exclusión soci al y productiva / new: exclusión social al y productiva; img: deserción enlos niveles Técnico, Tecnólogo / new: deserción ellos niveles Técnico, Tecnológico; img: Cursos de formación / new: Curaos de formación … |
| Documento Técnico | 54 | firecrawl_ocr | defect → **defect** | partial | `manual_review` | 10 | img: BPIN 202300000000204 / new: BPIN 20230000000204 (falta un cero); img: pobreza o vulnerabilidad a nivel Nacional / new: pobreza vulnerable a nivel Nacional; img: $4.1 billones COP / new: $4.1 billiones COP … |
| Documento Técnico | 57 | literal_patch | defect → **defect** | partial | `use_new_with_corrections` | 3 | img: ...inclusion-social-preguntas-respuestas-entenderlo / new: preguntasrespuestas; img: ...mercado-laboral/empleo-informal-y-seguridad-social#:~ / new: empleoinformal-y-seguridadsocial |
| Documento Técnico | 58 | literal_patch | defect → **defect** | no | `use_new_with_corrections` | 5 | img: liderazgo-aspectos-relevantes/ (2 veces) / new: aspectosrelevantes/; img: fao.org/joint-programme-... gender-transformative-approaches/en / new: jointprogramme ... transformativeapproaches; img: entrada "DNP. (2012). Guía para la Incorporación..." 3 veces / new: 1 vez (duplicados eliminados) |
| Documento Técnico | 59 | literal_patch | defect → **defect** | no | `use_new_with_corrections` | 3 | img: caracteristicas-y-ejemplos/ (URL en cursiva) / new: caracteristicas-yejemplos/; img: responsabilidadsocial.net/emprendimiento-que-es / new: emprendimientoque-es |
| Documento Técnico | 61 | literal_patch | defect → **defect** | no | `use_new_with_corrections` | 4 | img: tipos-caracteristicas-y-ejemplos/ / new: caracteristicasy-ejemplos/; img: /es/que-es-el-voluntariado/#:~ / new: que-es-elvoluntariado/; img: /articles/voluntariado-ambiental#:~ / new: voluntariadoambiental# |
| Manual M-IP-36 (1/2) | 14 | firecrawl_ocr | defect → **minor** | yes | `use_new_with_corrections` | 1 | 'Prosperidad Social' partido en dos filas (fila 'Social' con celda vacía); pie 'Código F-DE-3 / Página 14 de 75' omitido. |
| Manual M-IP-36 (1/2) | 18 | literal_patch | defect → **ok** | yes | `use_new` | 0 | Pie de página (F-DE-3, Página 18 de 75) omitido. |
| Manual M-IP-36 (1/2) | 19 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 1 | img: verificación administrativa operativa y financiera de los programas / new: ... y financieras de los programas |
| Manual M-IP-36 (1/2) | 20 | firecrawl_ocr | defect → **ok** | yes | `use_new` | 0 | Pie de página omitido. |
| Manual M-IP-36 (1/2) | 23 | firecrawl_ocr | defect → **minor** | yes | `use_new` | 0 | Título convertido en encabezado markdown '## ➢'; viñetas '▪' separadas por líneas en blanco; pie de página omitido. |
| Manual M-IP-36 (1/2) | 27 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 5 | img: Líneas / new: Lineas (tilde perdida); img: Inscripción / new: Inscripcion; img: Verificación / new: Verificacion … |
| Manual M-IP-36 (1/2) | 28 | firecrawl_ocr | defect → **minor** | yes | `use_new_with_corrections` | 1 | Título 9.4 fragmentado en cuatro '##'; pie de página omitido. |
| Manual M-IP-36 (1/2) | 29 | firecrawl_ocr | defect → **defect** | partial | `use_new_with_corrections` | 1 | img: 2. Verificación de condicionalidades y corresponsabilidades / new: ... y correspondencias |
| Manual M-IP-36 (1/2) | 35 | firecrawl_ocr | defect → **defect** | yes | `use_new_with_corrections` | 2 | img: programas universitarios y/o posgrados, de acuerdo / new: y/o posgados, de acuerdo; img: Específicas / new: Especificas (tilde perdida) |
| Manual M-IP-36 (1/2) | 36 | firecrawl_ocr | defect → **minor** | yes | `use_new` | 0 | Salto de párrafo perdido en 'del programa Cada participante' (el original tampoco tiene punto); pie de página omitido. |
| Manual M-IP-36 (1/2) | 38 | firecrawl_ocr | defect → **minor** | yes | `use_new` | 0 | En el original Permanencia y Excelencia comparten una misma celda (y la celda de frecuencia); new.md pone Excelencia en fila propia con frec |
| Manual M-IP-36 (1/2) | 40 | firecrawl_ocr | defect → **ok** | yes | `use_new` | 0 | Raya '–' de 'Superiores – ENS' como guion; pie de página omitido. |

## Correcciones propuestas (consolidado)

Formato para el mecanismo `text_corrections` del panel (`page_number`, `find_text`, `replace_text`). `page_number` es la página física del PDF original. Cada `find_text` aparece exactamente una vez en el markdown nuevo de esa página. «⏎» representa un salto de línea; el JSON trae el texto exacto.

Valores de la columna «Tipo»:
- `substantive`: restituye una palabra o cifra.
- `cosmetic`: solo formato (une una URL partida o un título fragmentado, quita etiquetas `<u>`).
- `editorial`: añade un rótulo que no está como texto en esa posición. **No es literal**; aplicarlo es decisión del revisor.

La «Conf.» es `medium` cuando la lectura es inferida o de baja resolución.

| Documento | Pág. | Buscar | Reemplazar | Tipo | Conf. |
|---|---:|---|---|---|---|
| Res. CBU 002-2025 | 1 | `Açuerdo CS No. 069` | `Acuerdo CS No. 069` | substantive | high |
| Res. CBU 002-2025 | 2 | `graduación de los estudiantes.` | `graduación de los estudiante.` | substantive | high |
| Res. CBU 002-2025 | 2 | `beneficios, estimulos, incentivos` | `beneficios, estímulos, incentivos` | substantive | high |
| Res. CBU 002-2025 | 3 | `regulado por a Resolución` | `regulado por la Resolución` | substantive | medium |
| Res. CBU 002-2025 | 3 | `entendírcose como acuellos` | `entendiéndose como aquellos` | substantive | high |
| Res. CBU 002-2025 | 3 | `carácter genera o particular` | `carácter general o particular` | substantive | high |
| Res. CBU 002-2025 | 3 | `alineaco con los` | `alineado con los` | substantive | high |
| Res. CBU 002-2025 | 3 | `2024–2028, er el eje` | `2024–2028, en el eje` | substantive | high |
| Res. CBU 002-2025 | 3 | `Bienestary Permanencia` | `Bienestar y Permanencia` | substantive | high |
| Res. CBU 002-2025 | 3 | `el bienestar estudiante,` | `el bienestar estudiantil,` | substantive | high |
| Res. CBU 002-2025 | 3 | `al perico académico` | `al periodo académico` | substantive | high |
| Res. CBU 002-2025 | 3 | `Alimentación de Programa` | `Alimentación del Programa` | substantive | high |
| Res. CBU 002-2025 | 3 | `para el concimiento` | `para el conocimiento` | substantive | high |
| Res. CBU 002-2025 | 3 | `estudiantes de Unitrófico.` | `estudiantes de Unitrópico.` | substantive | high |
| Res. CBU 002-2025 | 3 | `Americano, Unitrófico, en mérito` | `Americano, Unitrópico, en mérito` | substantive | high |
| Res. CBU 002-2025 | 3 | `Americano Unitrófico de conformidad` | `Americano Unitrópico de conformidad` | substantive | high |
| Res. CBU 002-2025 | 3 | `El presente acte tiene` | `El presente acto tiene` | substantive | high |
| Res. CBU 002-2025 | 3 | `Alimentación descrrito en` | `Alimentación descrito en` | substantive | high |
| Res. CBU 002-2025 | 3 | `con las discosiciones del` | `con las disposiciones del` | substantive | high |
| Res. CBU 002-2025 | 3 | `se naran efectivos` | `se harán efectivos` | substantive | high |
| Res. CBU 002-2025 | 4 | `busca reconcer la` | `busca reconocer la` | substantive | high |
| Res. CBU 002-2025 | 4 | `reconociendolos como` | `reconociéndolos como` | substantive | high |
| Res. CBU 002-2025 | 4 | `posibilito así,` | `posibilitando así,` | substantive | high |
| Res. CBU 002-2025 | 5 | `el período académico cursado` | `el periodo académico cursado` | substantive | high |
| Res. CBU 002-2025 | 5 | `deberá aduntarse` | `deberá adjuntarse` | substantive | high |
| Res. CBU 002-2025 | 6 | `dentro de las lineas de acción` | `dentro de las líneas de acción` | substantive | high |
| Res. CBU 002-2025 | 7 | `discrepancias, falsidad o adulteración entre` | `discrepancias, falsedad o adulteración entre` | substantive | high |
| Res. CBU 002-2025 | 7 | `discrepancias, falsidad o adulteración en la` | `discrepancias, falsedad o adulteración en la` | substantive | high |
| Res. CBU 002-2025 | 7 | `completa y verifiable` | `completa y verificable` | substantive | high |
| Res. CBU 002-2025 | 7 | `durante el período académico` | `durante el periodo académico` | substantive | high |
| Res. CBU 002-2025 | 8 | `orientan la Politica Integral` | `orientan la Política Integral` | substantive | high |
| Res. CBU 002-2025 | 8 | `Hayan incurido en` | `Hayan incurrido en` | substantive | high |
| Res. CBU 002-2025 | 9 | `decisión de suspension será` | `decisión de suspensión será` | substantive | high |
| Res. CBU 002-2025 | 9 | `La suspENSION no implicará` | `La suspensión no implicará` | substantive | high |
| Res. CBU 002-2025 | 10 | `grupos prioritizados determinados` | `grupos priorizados determinados` | substantive | high |
| Res. CBU 002-2025 | 12 | `\|  \| 10 de noviembre de 2025 6:00 pm \|` | `\| 28 de octubre de 2025 \| 10 de noviembre de 2025 6:00 pm \|` | substantive | high |
| Res. CBU 002-2025 | 12 | `web de Unitrófico www` | `web de Unitrópico www` | substantive | high |
| Res. CBU 002-2025 | 12 | `de Unitrófico, Convocatoria` | `de Unitrópico, Convocatoria` | substantive | high |
| Res. CBU 002-2025 | 12 | `convocatoria unicamente los` | `convocatoria únicamente los` | substantive | high |
| Res. CBU 002-2025 | 13 | `establecidos en el articulo 5` | `establecidos en el artículo 5` | substantive | high |
| Res. CBU 002-2025 | 13 | `información de estudiante no coincide` | `información del estudiante no coincide` | substantive | high |
| Res. CBU 002-2025 | 13 | `Unitrópico www.unitrópico.edu.co` | `Unitrópico www.unitropico.eduo.co` | substantive | high |
| Res. CBU 002-2025 | 14 | `web de Unitrófico www.unitropico.edu.co,` | `web de Unitrópico www.unitropico.edu.co,` | substantive | high |
| Res. CBU 002-2025 | 14 | `web de Unitrófico www.unitropico.edu.co micrositio` | `web de Unitrópico www.unitropico.edu.co micrositio` | substantive | high |
| Res. CBU 002-2025 | 14 | `Publicación Listado de Final Beneficiarios.` | `Publicación Listado Final de Beneficiarios.` | substantive | high |
| Res. CBU 002-2025 | 14 | `a nombre de estudiante beneficiario` | `a nombre del estudiante beneficiario` | substantive | high |
| Res. CBU 002-2025 | 15 | `su condicion de beneficiario` | `su condición de beneficiario` | substantive | high |
| Res. CBU 002-2025 | 16 | `Asesora Junfrica y de Contratación` | `Asesora Jurídica y de Contratación` | substantive | high |
| Documento Técnico | 5 | `atencion-<u>al-</u>⏎<u>ciudadano/glosario/</u>)` | `atencion-al-ciudadano/glosario/)` | cosmetic | high |
| Documento Técnico | 17 | `6.389.448 (50,6%) son hombres` | `6.241.974 (49,4%) son mujeres⏎⏎6.389.448 (50,6%) son hombres` | substantive | high |
| Documento Técnico | 17 | `2.983.689⏎(23,6 %)` | `Zona Rural⏎2.983.689⏎(23,6 %)` | substantive | high |
| Documento Técnico | 17 | `9.647.733⏎(76,4 %)` | `Zona Urbana⏎9.647.733⏎(76,4 %)` | substantive | high |
| Documento Técnico | 18 | `\| GRUPO SIS BEN \| 33 \|⏎\| A \| 35 \|⏎\| B \| 25 \|⏎\| C \| 7 \|⏎\| D \|  \|` | `\| A \| 33% \|⏎\| B \| 35% \|⏎\| C \| 25% \|⏎\| D \| 7% \|` | substantive | medium |
| Documento Técnico | 18 | `\| Category \| Share (%) \|` | `\| GRUPO SIS BEN \| % \|` | substantive | medium |
| Documento Técnico | 20 | `\| 2010 \| 9.89 \| 18.15 \| 17.9 \|` | `\| 2010 \| 9.89 \| 22.46 \| 18.15 \|` | substantive | high |
| Documento Técnico | 21 | `Técnico o tecnologo` | `Técnico o tecnólogo` | substantive | high |
| Documento Técnico | 21 | `1,8%⏎Son pobres` | `1,8%⏎Son pobres⏎⏎Fuente: Cálculos de Prosperidad Social basado en la Gran Encuesta Integrada de Hogares 2019` | substantive | medium |
| Documento Técnico | 22 | `<th>Edad</th><th></th><th></th>` | `<th>Edad</th><th>Hombres</th><th>Mujeres</th>` | editorial | high |
| Documento Técnico | 44 | `Técnico, Tecnologo y Profesional` | `Técnico, Tecnólogo y Profesional` | substantive | high |
| Documento Técnico | 44 | `las habilitidades y competencias` | `las habilidades y competencias` | substantive | high |
| Documento Técnico | 44 | `las habilitidades para la vida` | `las habilidades para la vida` | substantive | high |
| Documento Técnico | 44 | `emprendimiento y educación postgradual de los jóvenes` | `emprendimiento y educación post gradual de los jóvenes⏎Contribuir a la integración de los jóvenes en procesos de acción social, política, ambiental y económica de su territorio` | substantive | high |
| Documento Técnico | 44 | `promoción de derches` | `promoción de derechos` | substantive | high |
| Documento Técnico | 44 | `ofertas de formación me de capacidades s` | `ofertas de fortalecimiento de capacidades` | substantive | high |
| Documento Técnico | 44 | `empleabilidad, si emprendimiento y la educación postgradual` | `empleabilidad, el emprendimiento y la educación post gradual` | substantive | high |
| Documento Técnico | 44 | `experienclales` | `experienciales` | substantive | high |
| Documento Técnico | 44 | `construccion de semiliteros` | `construcción de semilleros` | substantive | high |
| Documento Técnico | 44 | `certificacion de participacion` | `certificación de participación` | substantive | high |
| Documento Técnico | 46 | `exclusión social al y productiva` | `exclusión social y productiva` | substantive | high |
| Documento Técnico | 46 | `deserción ellos niveles Técnico, Tecnológico` | `deserción enlos niveles Técnico, Tecnólogo` | substantive | high |
| Documento Técnico | 46 | `Curaos de formación` | `Cursos de formación` | substantive | high |
| Documento Técnico | 46 | `Acciones que facilitan` | `Acciones que faciliten` | substantive | high |
| Documento Técnico | 46 | `oferta de fortalecimiento to de capacidades` | `ofertas de fortalecimiento de capacidades` | substantive | high |
| Documento Técnico | 46 | `construcción de hues de accompañamiento o hacia` | `construcción de Rutas de acompañamiento hacia` | substantive | high |
| Documento Técnico | 46 | `la educación pool prodicional` | `la educación post gradual` | substantive | high |
| Documento Técnico | 46 | `Encuentros experientiales (aciciones de solidaridad en el marco del voluntariat 0)` | `Encuentros experienciales (acciones de solidaridad en el marco del voluntariado)` | substantive | high |
| Documento Técnico | 46 | `construcción de semiliteros` | `construcción de semilleros` | substantive | high |
| Documento Técnico | 46 | `Transfore noja monetaria y constancié de participación en el voluntaría do` | `Transferencia monetaria y constancia de participación en el voluntariado` | substantive | high |
| Documento Técnico | 46 | `Linea de promoción` | `Línea de promoción` | substantive | high |
| Documento Técnico | 46 | `Linea de expansión` | `Línea de expansión` | substantive | high |
| Documento Técnico | 46 | `Linea compromiso` | `Línea compromiso` | substantive | high |
| Documento Técnico | 46 | `ACTIVIDADES` | `ACTIVIDADES⏎INSUMOS` | substantive | high |
| Documento Técnico | 54 | `BPIN 20230000000204` | `BPIN 202300000000204` | substantive | high |
| Documento Técnico | 54 | `pobreza vulnerable a nivel` | `pobreza o vulnerabilidad a nivel` | substantive | high |
| Documento Técnico | 54 | `billiones` | `billones` | substantive | high |
| Documento Técnico | 54 | `Proyecto de inversion` | `Proyecto de inversión` | substantive | high |
| Documento Técnico | 54 | `Ley 2294 de 2023, Plan` | `Ley 2294 de 2023. Plan` | substantive | high |
| Documento Técnico | 54 | `√ Alcaldas:` | `√ Alcaldías:` | substantive | high |
| Documento Técnico | 54 | `Gestores indigenas` | `Gestores indígenas` | substantive | high |
| Documento Técnico | 54 | `√ DMP:` | `√ DNP:` | substantive | high |
| Documento Técnico | 54 | `• Modulos de` | `• Módulos de` | substantive | high |
| Documento Técnico | 54 | `• Registraduria` | `• Registraduría` | substantive | high |
| Documento Técnico | 57 | `11102-⏎aproximacion` | `11102-aproximacion` | cosmetic | high |
| Documento Técnico | 57 | `preguntasrespuestas` | `preguntas-respuestas` | substantive | high |
| Documento Técnico | 57 | `empleoinformal-y-seguridadsocial` | `empleo-informal-y-seguridad-social` | substantive | high |
| Documento Técnico | 58 | `DNP. (2012). *Guía para la Incorporación de la Variable Étnica.*` | `DNP. (2012). *Guía para la Incorporación de la Variable Étnica.*⏎⏎DNP. (2012). *Guía para la Incorporación de la Variable Étnica.*⏎⏎DNP. (2012). *Guía para la Incorporación de la Variable Étnica.*` | substantive | high |
| Documento Técnico | 58 | `aspectosrelevantes/⏎⏎Escuela` | `aspectos-relevantes/⏎⏎Escuela` | substantive | high |
| Documento Técnico | 58 | `aspectosrelevantes/⏎⏎EUROSociAL` | `aspectos-relevantes/⏎⏎EUROSociAL` | substantive | high |
| Documento Técnico | 58 | `fao.org/jointprogramme` | `fao.org/joint-programme` | substantive | high |
| Documento Técnico | 58 | `gender-transformativeapproaches/en` | `gender-transformative-approaches/en` | substantive | high |
| Documento Técnico | 59 | `caracteristicas-yejemplos/` | `caracteristicas-y-ejemplos/` | substantive | high |
| Documento Técnico | 59 | `net/emprendimientoque-es` | `net/emprendimiento-que-es` | substantive | high |
| Documento Técnico | 59 | `En%20el%⏎20caso` | `En%20el%20caso` | cosmetic | high |
| Documento Técnico | 61 | `caracteristicasy-ejemplos/` | `caracteristicas-y-ejemplos/` | substantive | high |
| Documento Técnico | 61 | `que-es-elvoluntariado/` | `que-es-el-voluntariado/` | substantive | high |
| Documento Técnico | 61 | `articles/voluntariadoambiental#` | `articles/voluntariado-ambiental#` | substantive | high |
| Documento Técnico | 61 | `TMC/Jo⏎venes/` | `TMC/Jovenes/` | cosmetic | high |
| Manual M-IP-36 (1/2) | 14 | `\| Prosperidad \| Departamento Administrativo para la Prosperidad Social \|⏎\| Social \|  \|` | `\| Prosperidad Social \| Departamento Administrativo para la Prosperidad Social \|` | cosmetic | high |
| Manual M-IP-36 (1/2) | 19 | `operativa y financieras de los programas` | `operativa y financiera de los programas` | substantive | high |
| Manual M-IP-36 (1/2) | 27 | `Lineas⏎□ Focalización` | `Líneas⏎□ Focalización` | substantive | high |
| Manual M-IP-36 (1/2) | 27 | `Lineas⏎□ Focalización` | `Líneas⏎[Ciclo Operativo]⏎□ Focalización` | editorial (opcional) | high |
| Manual M-IP-36 (1/2) | 27 | `□ Inscripcion` | `□ Inscripción` | substantive | high |
| Manual M-IP-36 (1/2) | 27 | `□ Verificacion` | `□ Verificación` | substantive | high |
| Manual M-IP-36 (1/2) | 27 | `□ Compromiso social para` | `[Líneas]⏎□ Compromiso social para` | editorial (opcional) | high |
| Manual M-IP-36 (1/2) | 28 | `## 9.4. COMPONENTE DE⏎⏎## TRANSFERENCIAS⏎⏎## MONETARIAS⏎⏎## CONDICIONADAS` | `## 9.4. COMPONENTE DE TRANSFERENCIAS MONETARIAS CONDICIONADAS` | cosmetic | high |
| Manual M-IP-36 (1/2) | 29 | `condicionalidades y correspondencias` | `condicionalidades y corresponsabilidades` | substantive | high |
| Manual M-IP-36 (1/2) | 35 | `y/o posgados, de acuerdo` | `y/o posgrados, de acuerdo` | substantive | high |
| Manual M-IP-36 (1/2) | 35 | `<td rowspan="2">Especificas</td>` | `<td rowspan="2">Específicas</td>` | substantive | high |

Notas sobre correcciones concretas:
- **03 p27:** hay dos correcciones con el mismo `find_text`; aplicar solo una. La literal pone la tilde. La editorial opcional además rotula el grupo «Ciclo Operativo». La inserción de «[Líneas]» también es editorial.
- **02 p22:** los rótulos «Hombres» y «Mujeres» proceden de íconos, no de texto. Sin ellos no se sabe a qué sexo corresponde cada columna.
- **02 p18:** el emparejamiento de los porcentajes se infirió midiendo el color de las porciones frente a la leyenda. La leyenda del original incluye «GRUPO SISBEN» como si fuera una categoría más.
- **02 p21:** el año «2019» de la línea Fuente está en una imagen de muy baja resolución. Coincide con la fuente de la p22.

## ¿Continuar con los lotes restantes?

**Sí, pero solo resolviendo antes el lote 04 y con auditoría y corrección obligatorias después.**

- **La calidad justifica continuar donde el texto actual falta o es muy malo.**
  - El OCR nuevo corrigió del todo o en parte los defectos previos en 42 de 45 páginas.
  - En el escaneo de la Res. CBU 002-2025 pasó de un texto inutilizable a uno fiel tras correcciones menores.
  - Esto es especialmente relevante para el lote 06 (Convocatoria: 15 páginas sin texto) y el 07 (Política de Bienestar, otro escaneo con capa OCR mala).
- **No justifica usar el OCR sin revisar.**
  - Solo el 22 % de las páginas sirve tal cual.
  - Firecrawl sigue autocorrigiendo erratas del original, pierde tildes e inventa texto en diagramas.
  - En gráficos llegó a inventar una cifra.
- **Lote 05 (Res. 00137): expectativa baja.** Ese texto ya era salida de Firecrawl, y los fallos observados aquí (palabras sustituidas, autocorrección) son los mismos que tenía.
- **Páginas de errata puntual: poco valor.** En las de errata única (URL, una palabra), el OCR aporta poco o empeora el resultado.
  - Esto afecta a las páginas de corrección puntual de los lotes 05 y 07, y a las p62 y p64 del lote 04.
  - Como los lotes ya están cortados y el manifest no debe regenerarse, se pueden enviar igualmente, pero conviene contrastarlas con el texto reutilizable antes de elegir.
- **Lote 04: no reenviar.** Según el README, el estado incierto se confirma primero en el Dashboard de Firecrawl; no se reintenta.

## Limitaciones

- La revisión visual la hicieron 4 revisores (subagentes) con un brief común, con verificación cruzada por muestreo del coordinador. Puede haber diferencias de criterio en la frontera entre `minor` y `defect`.
- A 150 dpi la letra muy pequeña puede ocultar errores de un carácter. El zoom se aplicó a tablas, cifras, URL y zonas dudosas, no a todo el texto.
- Los encabezados, pies de página, logos y folios omitidos se trataron como `minor` (igual que en la auditoría previa).
- Las correcciones solo garantizan la fidelidad de lo que se detectó. `ok` no es una certificación carácter por carácter.
