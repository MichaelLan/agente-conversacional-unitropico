# Corpus v2: ensamblado parcial offline, publicación bloqueada

7 documentos, 216 páginas físicas únicas: 181 con contexto previo + 20 Res137 = 201;
15 convocatoria siguen SIN texto. No es éxito 35/35 ni web/RAG completo.
Todos los documentos/páginas conservan requires_review y publication_blocked.
Se mantienen evidencia directa vacía, textos, raw, spans y los cuatro issues piloto 13–14.
No se infieren secciones, páginas impresas, vigencia, grounding ni calidad nativa/institucional.

## Evidencia y límites
Rutas relativas a `evaluations/ocr/`; entradas fijas, sin rutas CLI ni symlinks.
SHA independiente v1 documents.json: `104160c01f34dff520fb67a28d0dd004f6866ccc3a1184704f59861df9dbbc1f`.
SHA independiente Res137 response.raw: `f75e036f39f8c4f76dc595032df702d746d3a7c3499f13777db2aa411c89d612`, 241580 bytes.
Fuente/payload: `c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362`, 19579889 bytes, identidad física 1..20; pins reutilizados de remaining_send.SPECS/entry, sin Docker.
HTTP 200, success booleano true, cobertura 20 páginas/20 grupos, numPages/totalPages/creditsUsed enteros 20.
392 items status ok; páginas 6 y 14 tienen todos sus content vacíos, aunque markdown no vacío.
Se conserva markdown por página, bloques/metadata completos y SHA/ruta del raw; no se reconstruye desde bloques.
Cualquier contenido de bloque vacío se marca block_content_incomplete sin corregir el proveedor.
Batch conserva stopped_no_replay; Conv2026: pendiente_evidencia_consumo_remoto_desconocido.
response.raw de Conv2026 está AUSENTE, no tiene cero bytes; no demuestra fallo remoto. Sin retry.
20 créditos DECLARADOS del lote actual, piloto previo 2 declarados por separado; primer piloto y consumo remoto Conv2026 desconocidos.
35 era estimación, NO gasto ni total de factura. No consulta de saldo/red/credenciales ni envío.

## Ejecución y comprobación
Runner: `PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.14 python`.
Focused: runner + `-m unittest discover -s evaluations/ocr/tests -p test_assemble_partial.py`: RED exit 1, módulo ausente; GREEN exit 0, 6 pruebas con subcasos adversariales.
Suite: runner + `-m unittest discover -s evaluations/ocr/tests`: exit 0, 62 pruebas (56 anteriores + 6); repetida tras reforzar aserciones, exit 0.
Real: runner + `-` (heredoc de inventario SHA/tamaño, app.main() UNA vez, comparación y write_json exclusivo de preservation.json): exit 0.
No se invocó de nuevo el ensamblado real. Directorio exclusivo: fallos parciales también impiden reutilizarlo; no borrar para reintentar.
Verificación posterior: runner + `-` (heredoc de comparación v1/v2, hashes y asserts): exit 0; 82 archivos previos/fuentes preservados, 4 issues piloto, 392 items, 6/14 vacías solo en bloques.
`.artifacts/extraction-corpus-v2/preservation.json` registra inventario antes/después; manifest registra entradas y ausencia Conv2026.
Manifest final enlazado exclusivamente después de documents y manifest.record; fallo de escritura no publica marcador de éxito.
SHA documents v2: `d0057b4292c2c94d3963aea6c2e33a3e0db368abf3ede936a3c5ce657563f0eb`.
Helpers v1 y sender sin modificar; sin framework duplicado. Pruebas usan fixtures sintéticas y temporales del sistema, eliminados al terminar.
