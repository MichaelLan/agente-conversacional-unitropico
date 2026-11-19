# Plan incremental: MVP en un mes y tesis en dos

**Estado: implementación EN CURSO, no finalizada.** Existe una [arquitectura lógica acordada](ARQUITECTURA.md); este plan organiza su ejecución, no la sustituye ni cierra librerías pendientes. El [README](README.md) identifica el proyecto y el estado vigente está en [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md), que prevalece sobre los «estado observado» de este plan. *Actualización 2026-11-18: las tablas y estados de abajo se actualizaron contra ese documento.*

Un mes para el MVP y un segundo mes para la tesis son **objetivos de planificación, no un calendario de viabilidad demostrada**. La ruta crítica es comprobar runtime y extracción antes de ampliar funcionalidades. Este documento no autoriza despliegues ni provisión.

## Prioridad y secuencia

- **P0 — runtime:** PoC de Python 3.14, FastAPI y dependencias reales del RAG en Python Workers/Pyodide. Registrar incompatibilidades, no asumir que soporte de Python implica soporte de paquetes.
- **P0 — documentos:** benchmark de OCR/parser con escaneos y tablas reales; verificar texto, estructura y paginación. Sin fuentes representativas, la viabilidad documental queda sin demostrar.
- **P0 — vertical slice:** un documento → extracción persistida → índice → recuperación → respuesta con cita verificable; incluir abstención y despublicación. Anteponerlo a completar pantallas.
- **Después:** ampliar ingesta, chat y administración; endurecer seguridad e integración; evaluar formalmente en el mes 2. Reranking y LangGraph no entran por defecto.

## Responsables y superficies exclusivas

Roles de trabajo, no personas ni equipos confirmados: una persona puede asumir varios, pero cada superficie mantiene un único owner. La tabla delimita trabajo futuro; no ordena crear carpetas vacías.

| Owner por rol | Superficie exclusiva | Responsabilidad |
|---|---|---|
| API | `apps/api/`, `.github/workflows/api.yml` | HTTP, validación, autorización y coordinación |
| RAG | `apps/rag/`, `.github/workflows/rag.yml` | Recuperación, generación, citas y abstención |
| Ingesta | `workers/ingestion/`, `.github/workflows/ingestion.yml` | Extracción, etapas, chunking, embeddings e indexación |
| Frontend | `apps/web/`, `.github/workflows/web.yml` | Chat, sesión y panel administrativo |
| Integración | `infra/`, `.github/workflows/integration.yml` | Esquemas de persistencia y pruebas entre componentes |
| Evaluación/documentación | `evaluations/`, `PLAN_MVP.md` | Corpus de prueba, métricas, evidencia y seguimiento |

Los cambios de interfaz se coordinan entre owners sin editar la superficie ajena. Modelos Pydantic y tipos TypeScript permanecen manuales en su componente; integración comprueba JSON interoperable. Sin `contracts/`, generación de clientes ni `pyproject.toml`/`package.json` raíz. Cada componente incorpora dependencias, lockfile y controles de calidad cuando tenga implementación y herramientas definidas.

## Hitos 0–5

Las semanas se solapan solo cuando las dependencias lo permitan. Cada criterio exige evidencia registrada, no una declaración de avance. Ningún hito se declara completo en este plan (estado al 2026-11-18 en cada hito).

### Hito 0 — Auditoría y preguntas · inicio de semana 1

- **Owners:** Evaluación coordina inventario; cada rol audita su superficie.
- **Dependencias:** arquitectura y estado real del repositorio; acceso a documentos y FAQs por comprobar.
- **Entrega:** inventario de fuentes, permisos, código existente, faltantes y decisiones necesarias, sin convertir preguntas abiertas en elecciones aprobadas.
- **Aceptación:** se distinguen implementado/propuesto y disponible/no recibido; cada decisión necesaria tiene rama afectada, owner y evidencia para aprobación. No inventar corpus ni detener ramas independientes.

### Hito 1 — Esqueleto modular y CI · semana 1

- **Owners:** cada rol en su superficie; Integración coordina comprobaciones cruzadas.
- **Dependencias:** auditoría del hito 0 y selección aprobada de herramientas para cada componente.
- **Entrega:** esqueleto mínimo con código útil, dependencias y lockfiles locales, controles de calidad y CI por componente, sin carpetas vacías.
- **Aceptación:** controles disponibles pasan en componentes existentes; configuración independiente, sin contratos compartidos ni manifiestos raíz; CI sin login, deploy ni provisión.
- **Estado observado (2026-11-18): mayormente hecho.** Esqueletos API/RAG/web con controles locales verdes y CI por componente (`api.yml`, `rag.yml`, `web.yml`, más `ingestion.yml`, `evaluations.yml`, `evaluations-rag.yml`). `api`, `rag` y `web` verdes en GitHub sobre `fa23faf`; los otros tres workflows no se han ejecutado en remoto. Sin CI de integración (el test API→RAG del RAG importa `apps/api/src`) ni gate agregador. Sin despliegues.

### Hito 2 — Viabilidad: runtime y benchmark documental · semana 1

- **Owners:** API y RAG prueban su runtime; Ingesta prueba extracción; Evaluación registra comparación; Integración coordina límites entre componentes.
- **Dependencias:** inventario del hito 0 y esqueleto mínimo del hito 1; muestras reales para pruebas representativas. Herramientas locales aprobadas no autorizan servicios remotos ni llamadas pagadas.
- **Entrega:** matriz de compatibilidad con dependencias concretas y benchmark OCR/paginación, tablas, errores, reintentos, latencia y costo.
- **Aceptación:** evidencia ejecutable del camino mínimo FastAPI/RAG y sus límites; comparación de salida contra páginas originales, incluidas tablas. Si se dispone de él, incluir el PDF cercano a 19 MB/30 páginas descrito en arquitectura; su ausencia queda explícita.
- **Puerta:** documentar qué funciona y qué requiere decisión. PoC local con `workers-py`/`workers-runtime-sdk` y Wrangler/workerd ejecutada: API y RAG respondieron en workerd/Pyodide; solo cubre salud HTTP y transformación determinista, no retrieval ni LLM. Sin login, deploy, recursos remotos ni llamadas pagadas. Una prueba local no demuestra operación remota ni compatibilidad de rutas futuras.
- **Estado observado (2026-11-18): parcial.** Runtime: PoC local con workerd/Pyodide hecha para API y RAG; la ingesta (Python Worker + Workflow) se probó en workerd local hasta el paso `ocr` con `config_missing` (sin secrets); Firecrawl real desde el Worker, gateway y Vectorize **no verificados**. Benchmark documental: hubo envíos autorizados a Firecrawl Parse v2 (pilotos de 2 páginas, Res. 00137 con 20 páginas, lote de 15 páginas de la convocatoria con `network_error`); corpus v2 con 201 de 216 páginas con texto, todo `requires_review` y bloqueado para publicación. No hay benchmark comparativo de parsers ni medición de costo/latencia cerrada; el consumo de dos intentos es desconocido.
- **Entorno local:** Docker CLI y servidor 29.8.2 disponibles tras habilitación del usuario. Web pasó instalación congelada limpia, lint, tipos y build en contenedor. El benchmark OCR/tablas sigue pendiente; el hito no está completo.

### Hito 3 — Ingesta, RAG y vertical slice · semana 2

- **Owners:** Ingesta, RAG y API en sus superficies; Integración verifica interoperabilidad; Evaluación prepara casos de desarrollo.
- **Dependencias:** evidencia del hito 2 y decisiones de runtime, OCR y embeddings para la ruta afectada.
- **Entrega:** extracción estructurada fuera del índice, referencias fuente/versión/sección/página/chunk, recuperación semántica base y respuesta trazable.
- **Estado observado (2026-11-18): parcial, slice sin demostrar.** Hecho en código y tests: esquema D1 con FTS5 y máquina de estados, chunker estructural `structural-v1` (376 fragmentos sobre corpus v2, baseline `fixed-v1`), Workflow de ingesta, RAG con recuperación híbrida (RRF) y `/answer` con citas validadas, abstención con causa y revalidación de catálogo (despublicar impide usar chunks). Pendiente: recorrer un documento real de extremo a extremo (no hay recursos D1/R2/Vectorize remotos ni inferencia real), idempotencia ante reintentos solo con fakes y D1 local, y el experimento híbrido vs semántico vs léxico (sin medición).
- **Aceptación:** documento real recorre el slice; reintentar no duplica registros; un fallo permite reproceso por etapa; consulta sin evidencia se abstiene. Despublicar impide recuperar sus chunks y usarlos como evidencia, aunque sigan físicamente indexados.
- **Experimento:** comparar baseline semántico con híbrido/FTS5/RRF cuando sea viable; no añadir reranking sin mejora medida.

### Hito 4 — Aplicación y administración · semanas 3–4

- **Owners:** Frontend y API; Ingesta/RAG solo sus adaptaciones propias; Integración comprueba flujos.
- **Dependencias:** slice funcional; mecanismo de autenticación decidido antes de habilitar administración real.
- **Entrega:** chat público con fuentes, FAQs confirmadas y memoria de sesión; panel para carga, metadatos, publicación/despublicación, errores y reproceso.
- **Estado observado (2026-11-18): parcial.** Hecho en código y tests: chat web en texto plano con citas (contrato `/chat` v1), memoria solo en la sesión (sin persistencia), sidebar de FAQ **de ejemplo** (sin FAQs institucionales confirmadas), endpoints `/admin/*` con Bearer `ADMIN_TOKEN`, panel `/admin/`. Pendiente: flujos conectados contra recursos reales, render en navegador, reverse proxy `/api` de producción, rate limiting y controles de gasto, y evaluación de reglas de rechazo en integración.
- **Aceptación:** flujos conectados, no solo mocks; fuentes abren la ubicación disponible sin fabricar páginas; cambiar de sesión no recupera historial previo; operaciones administrativas sin autorización se rechazan.

- **Semana 4 — seguridad e integración:** cada owner endurece su superficie; Integración coordina pruebas extremo a extremo, con decisiones de antiabuso y controles de gasto previas a cualquier exposición pública.
- **Entrega adicional:** límites de solicitudes/contexto/tokens, protección administrativa, CI cruzada y métricas operativas.
- **Aceptación adicional:** pruebas de rechazo de roles privilegiados del cliente, acceso admin, entradas excesivas, despublicación, versiones vencidas, contradicciones y abstención. Sin conversaciones completas en logs ni persistencia de historial; secretos fuera del repo; ninguna acción de deploy en CI.
- **Cierre MVP:** demostrar el recorrido completo y publicar internamente evidencia, fallos y restricciones. Si falta prueba P0, no declarar viabilidad ni cierre; replanificar el alcance afectado.

### Hito 5 — Evaluación inicial, formal y tesis · semana 4 y mes 2

- **Owners:** Evaluación/documentación; demás roles aportan evidencia y correcciones en sus superficies.
- **Dependencias:** slice del hito 3 para evaluación inicial; aplicación integrada del hito 4 para evaluación completa; corpus disponible y protocolo fijado antes de medir el conjunto final.
- **Entrega:** reporte inicial en semana 4; comparación formal de recuperación y generación, análisis de errores, costo/latencia y límites de validez para la tesis en el mes 2.
- **Estado observado (2026-11-18): iniciado, sin resultados.** `evaluations/rag` tiene harness y dataset borrador de 55 ítems (37 dev / 18 test, `validated: false`); los informes `smoke-…-NOT-A-RESULT` solo prueban que el harness funciona. Falta validación humana del dataset, corpus indexado y corridas reales; no se reporta ninguna métrica.
- **Aceptación:** separación desarrollo/final sin ajustar con el final; casos directos, paráfrasis, excepciones, fechas, sustituciones, contradicciones y fuera de alcance. Registrar tamaño/procedencia y faltantes; repetir resultados con versiones de corpus, extracción, chunks, índice, embeddings, modelo, prompts, parámetros y costo.

## Decisiones cerradas, propuestas y pendientes

Estado actualizado el 2026-11-18 con `docs/IMPLEMENTATION_STATUS.md` §3 y §6 (decisiones del usuario del 2026-11-18 y de datos). «Hecho» significa implementado y probado localmente, no desplegado.

| Estado | Decisión / tratamiento |
|---|---|
| Cerrada | Monorepo de proyectos autónomos; frontend Vue/Vite/TypeScript/Nuxt UI sin Nuxt; Python 3.14/uv/FastAPI/Pydantic/Ruff/pytest; API y RAG como Python Workers; tipos manuales; sin deploy en CI. Web: Vue 3.5.45, Vite 8.3.6, Nuxt UI 4.12.0, TypeScript 6.0.3, pnpm; sin Router, Pinia, cliente HTTP ni framework de tests adicional (`node:test`). |
| Cerrada (usuario, 2026-11-18): embeddings | `openai/text-embedding-3-small`, 1536 dims, vía Vercel AI Gateway. Hecho en código (ingesta y RAG); **calidad en español y llamada real sin verificar**. |
| Cerrada (usuario, 2026-11-18): respuestas | Texto plano, sin Markdown ni librería de renderizado; chat con sidebar de FAQ en lugar de historial. Hecho en web; FAQ de ejemplo, pendientes de validar por Bienestar. |
| Cerrada (usuario, 2026-11-18): auth admin | Token secreto en la API (Bearer `ADMIN_TOKEN`, secret de Wrangler, comparación en tiempo constante); sustituye la opción Cloudflare Access. Hecho en API y panel web; sin verificar con bindings reales. |
| Cerrada (usuario, 2026-11-18): ingesta | Python Worker + Cloudflare Workflow (`workers/ingestion`); sustituye la propuesta de Worker TypeScript. Hecho; probado con fakes y parcialmente en workerd local. Workflows en Python está en open beta. |
| Cerrada: OCR | Firecrawl Parse v2 (modo `ocr`, `pages`, `blocks`) como parser de la ingesta. Hubo envíos reales autorizados; consumo de varios intentos desconocido; corpus v2 parcial y bloqueado para publicación. AnyDoc descartado como OCR local (no lo hace). No hay benchmark comparativo entre proveedores. |
| Cerrada: generador | `openai/gpt-6-luna` vía Vercel AI Gateway; ID verificado en catálogo público el 2026-11-18, **inferencia no probada**. `GatewayClient` propio con `reasoning_effort=low`, `json_schema` estricto, `max_tokens` 2048. |
| Decidida (opción B, pendiente de confirmación formal en §7 del estado) | LangChain: `langchain-core` como interfaces con adaptadores propios; sin LangGraph ni `langchain` 1.x (no instalan en WASM). La PoC LangChain fue retirada del código; `langchain-core` permanece en pyproject/locks de `apps/rag`. Ver [langchain-evaluation.md](docs/research/langchain-evaluation.md). |
| Cerradas (datos, orquestador) | Índice Vectorize 1536 dims, cosine, id de vector = `chunks.id`; convocatorias vencidas no se excluyen: se recuperan con su vigencia y el modelo debe advertirlo; `supersedes_version_id` 1:1 suficiente para el MVP; sha256 único por documento (no global); el Workflow hace upsert a Vectorize, publicar/despublicar solo cambia D1 y el RAG revalida en cada consulta; publicación siempre manual (`ready` → `published`); publicar no exige embeddings en todos los chunks (permite experimento solo léxico). Esquema D1 y FTS5 hechos y probados en D1 local; Vectorize sin emulación local y sin probar. |
| Aprobada solo local: herramientas Workers | `workers-py` 1.17.9 y `workers-runtime-sdk` 1.9.6 con Wrangler/workerd; `pywrangler` exige uv ≥ 0.13.4 (usar `uvx --from uv==0.13.7`). Pruebas locales sin login ni recursos remotos; no certifican producción. |
| Pendiente | Recursos Cloudflare (D1, R2, Vectorize, Workflows) y despliegue; plan Paid; gasto en inferencia y embeddings; política de publicación del OCR revisado y las 15 páginas sin OCR de la Convocatoria 2026-1; FAQs institucionales validadas; colección local de iconos (Nuxt UI descarga de `api.iconify.design`); rate limiting y antiabuso; reverse proxy `/api` de producción; CI de integración que sustituya el test cruzado API→RAG. |

Las decisiones necesarias detienen **solo la rama afectada**; inventario, protocolo y trabajo independiente continúan. El owner presenta evidencia y alternativas para aprobación antes de incorporar una elección pendiente; esta lista no convierte propuestas en tareas técnicas ya aprobadas.

## Datos, evaluación y límites de interpretación

- La arquitectura declara unas diez fuentes y FAQs disponibles; falta confirmar acceso efectivo, versiones, respuestas institucionales y representatividad. No suponer PDFs ni etiquetas de evaluación que aún no se hayan recibido. Si faltan, registrar la limitación y no sustituirlos silenciosamente por datos sintéticos.
- Una extracción automática satisfactoria **no demuestra fidelidad**: contrastar originales, tablas, excepciones y páginas. Las citas correctas **no prueban grounding**: evaluar también cada afirmación contra la evidencia recuperada.
- La vigencia se obtiene de disposiciones o metadatos administrativos explícitos, **no de la fecha de carga**. Probar sustituciones y contradicciones; exponer ambas fuentes cuando no haya prevalencia respaldada.
- Mantener desarrollo y evaluación final separados. Registrar controles de fuga, método de anotación y errores; no usar calificación automática como equivalente a revisión humana independiente.
- No hay validación institucional dedicada. Las conclusiones deben expresar ese límite y no presentar el MVP como contenido institucional validado.
- Recall@5 ≥90 %, citas correctas ≥95 %, afirmaciones sin respaldo ≤2 % y abstención fuera de alcance ≥95 % son **objetivos exploratorios**, no resultados ni requisitos impuestos. Medir P95 antes de fijar meta; documentar MRR/nDCG, grounding, errores y costo por consulta.
- No existe presupuesto mensual firme; la estimación >USD 50/mes no está validada. Separar costos de OCR, embeddings, inferencia y plataforma; registrar consumo, alertas y supuestos sin prometer sostenibilidad.

## Evidencia y siguiente paso

Cada hito deja estado, responsable, pruebas, resultados observados, costo y limitaciones en su superficie; no almacenar conversaciones completas como evidencia. La [evidencia técnica](INVESTIGACION.md) registra las pruebas locales y sus límites. *Actualización 2026-11-18:* ya hay 7 PDF reales (216 páginas, no versionados en Git) y OCR parcial. Los siguientes insumos son: FAQs/respuestas verificadas por Bienestar, validación humana del dataset de evaluación, autorización de gasto y de recursos Cloudflare para el primer recorrido real del slice, y decisión sobre la publicación del OCR. Este documento no crea infraestructura ni autoriza gasto.
