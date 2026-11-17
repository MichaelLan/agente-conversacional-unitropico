# Sender restante: preparado, NO ejecutado
No POST real, credenciales leídas, polling ni gasto nuevo en esta unidad.
Solo se ejecutaron pruebas con fakes y precheck real offline; `execution/` sigue ausente.
Autorización: 20 páginas Res137 + 15 convocatoria, estimadas 35 créditos, NO 100.
Convocatoria excluye originales 13–14: fragmento 1..12→1..12, 13→15, 14→16, 15→17.

## Ejecución manual única desde la raíz

`uv run --no-project --python 3.14 python evaluations/ocr/remaining_send.py --confirm-thirty-five-credits --env-file .env`

No ejecutado por el agente. Lee la clave con `pilot.load_key`, sin shell.
Toda existencia de `execution/`, incluso parcial, bloquea otra ejecución: no borrar/reutilizar.
Máximo dos POST, Res137 primero; segundo solo con primera respuesta completa válida.
Reserva exclusiva durable del batch y cada lote antes de red; sin proxies, redirects ni retry.
Endpoint fijo `/v2/parse`; OCR markdown/pages/blocks/pageMarkers, maxPages 20/15.
Timeout proveedor 300000 ms; cliente 330 s. No polling, resume ni reconciliación de factura.
Logs stderr UTC, elapsed y fases; resultados y raw exclusivos bajo execution/res137 y conv2026.
Cobertura/estructura/disponibilidad no certifican calidad OCR; créditos son declaración del payload. pageCount/numPages/totalPages deben concordar como enteros no booleanos.
Consumo desconocido permanece desconocido; una interrupción conserva reserva y CLI retorna 130. Grupos permiten ok/completed/success; error o success:false detienen. Global/metadata conservan completed/success, sin inventar compatibilidad con ok.

## Evidencia offline e identidad inmutable
Proveniencia independiente: `remaining-preparation.md` testimonia Docker/Poppler real: fuentes 20/17, payloads 20/15 y mapping anterior. SHA-256 literales en SPECS, fuera del plan mutable, fijan exactamente esos bytes; no hace falta recontarlos ni interpretar PDF. Se conservan tamaño exacto, %PDF-, ruta fija, no symlinks, JSON exacto/duplicate keys, autorización 35 y techo histórico 100 (no autorización). Un byte distinto, aun actualizando el manifest, se rechaza antes de reserva/HTTP. Preparación y pilot mantienen Poppler; el fix compartido `docker_unavailable` no se modifica.
Historia: RED inicial módulo ausente, GREEN 7; corrección RED exit 1 (diez subcasos), GREEN 6; suite anterior 54. Refactor actual: RED exit 1, 6 pruebas (2 fallos, 21 errores por Docker inaccesible); GREEN exit 0, 6 pruebas, suite completa exit 0, 56. Runner: `PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.14 python -m unittest discover -s evaluations/ocr/tests`, focused añade `-p test_remaining_send.py`. Todos los tests del sender bloquean subprocess; conservan transporte simulado, créditos, cobertura, durabilidad y no replay; mutaciones autoconsistentes de fuente/payload y duplicate keys dan cero HTTP.
Check independiente real: `PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --python 3.14 python evaluations/ocr/remaining_send.py --check` → exit 0, sin Docker, clave ni reserva. Investigación previa: el recuento redundante fallaba FileNotFoundError filename=docker (antes local_io_error); no invalida la paginación independiente ya atestiguada. No se instaló ni eludió nada; no requests reales.
Auditoría SHA-256/tamaños de fuentes, payloads, plan y evidencia/raw previos: sin cambios; execution ausente. Autoría total: 500 líneas en código, tests e informe; sin borrar casos de seguridad. Ejecución manual posterior: solo el comando explícito anterior, nunca envío automatizado del agente.
