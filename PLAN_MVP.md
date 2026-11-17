# Plan incremental: MVP en un mes y tesis en dos

**Estado: implementación EN CURSO, no finalizada.** Existe una [arquitectura lógica acordada](ARQUITECTURA.md); este plan organiza su ejecución, no la sustituye ni cierra librerías pendientes. El [README](README.md) identifica el proyecto.

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

Las semanas se solapan solo cuando las dependencias lo permitan. Cada criterio exige evidencia registrada, no una declaración de avance. Ningún hito se declara completo en este plan.

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
- **Estado observado:** esqueletos API/RAG/web y CI preparados; controles locales verdes aportados por el coordinador. Workflows inspeccionados, no ejecutados en GitHub; no se declara el hito completo.

### Hito 2 — Viabilidad: runtime y benchmark documental · semana 1

- **Owners:** API y RAG prueban su runtime; Ingesta prueba extracción; Evaluación registra comparación; Integración coordina límites entre componentes.
- **Dependencias:** inventario del hito 0 y esqueleto mínimo del hito 1; muestras reales para pruebas representativas. Herramientas locales aprobadas no autorizan servicios remotos ni llamadas pagadas.
- **Entrega:** matriz de compatibilidad con dependencias concretas y benchmark OCR/paginación, tablas, errores, reintentos, latencia y costo.
- **Aceptación:** evidencia ejecutable del camino mínimo FastAPI/RAG y sus límites; comparación de salida contra páginas originales, incluidas tablas. Si se dispone de él, incluir el PDF cercano a 19 MB/30 páginas descrito en arquitectura; su ausencia queda explícita.
- **Puerta:** documentar qué funciona y qué requiere decisión. PoC local con `workers-py`/`workers-runtime-sdk` y Wrangler/workerd ejecutada: API y RAG respondieron en workerd/Pyodide; solo cubre salud HTTP y transformación determinista, no retrieval ni LLM. Sin login, deploy, recursos remotos ni llamadas pagadas. Una prueba local no demuestra operación remota ni compatibilidad de rutas futuras.
- **Entorno local:** Docker CLI y servidor 29.8.2 disponibles tras habilitación del usuario. Web pasó instalación congelada limpia, lint, tipos y build en contenedor. El benchmark OCR/tablas sigue pendiente; el hito no está completo.

### Hito 3 — Ingesta, RAG y vertical slice · semana 2

- **Owners:** Ingesta, RAG y API en sus superficies; Integración verifica interoperabilidad; Evaluación prepara casos de desarrollo.
- **Dependencias:** evidencia del hito 2 y decisiones de runtime, OCR y embeddings para la ruta afectada.
- **Entrega:** extracción estructurada fuera del índice, referencias fuente/versión/sección/página/chunk, recuperación semántica base y respuesta trazable.
- **Aceptación:** documento real recorre el slice; reintentar no duplica registros; un fallo permite reproceso por etapa; consulta sin evidencia se abstiene. Despublicar impide recuperar sus chunks y usarlos como evidencia, aunque sigan físicamente indexados.
- **Experimento:** comparar baseline semántico con híbrido/FTS5/RRF cuando sea viable; no añadir reranking sin mejora medida.

### Hito 4 — Aplicación y administración · semanas 3–4

- **Owners:** Frontend y API; Ingesta/RAG solo sus adaptaciones propias; Integración comprueba flujos.
- **Dependencias:** slice funcional; mecanismo de autenticación decidido antes de habilitar administración real.
- **Entrega:** chat público con fuentes, FAQs confirmadas y memoria de sesión; panel para carga, metadatos, publicación/despublicación, errores y reproceso.
- **Aceptación:** flujos conectados, no solo mocks; fuentes abren la ubicación disponible sin fabricar páginas; cambiar de sesión no recupera historial previo; operaciones administrativas sin autorización se rechazan.

- **Semana 4 — seguridad e integración:** cada owner endurece su superficie; Integración coordina pruebas extremo a extremo, con decisiones de antiabuso y controles de gasto previas a cualquier exposición pública.
- **Entrega adicional:** límites de solicitudes/contexto/tokens, protección administrativa, CI cruzada y métricas operativas.
- **Aceptación adicional:** pruebas de rechazo de roles privilegiados del cliente, acceso admin, entradas excesivas, despublicación, versiones vencidas, contradicciones y abstención. Sin conversaciones completas en logs ni persistencia de historial; secretos fuera del repo; ninguna acción de deploy en CI.
- **Cierre MVP:** demostrar el recorrido completo y publicar internamente evidencia, fallos y restricciones. Si falta prueba P0, no declarar viabilidad ni cierre; replanificar el alcance afectado.

### Hito 5 — Evaluación inicial, formal y tesis · semana 4 y mes 2

- **Owners:** Evaluación/documentación; demás roles aportan evidencia y correcciones en sus superficies.
- **Dependencias:** slice del hito 3 para evaluación inicial; aplicación integrada del hito 4 para evaluación completa; corpus disponible y protocolo fijado antes de medir el conjunto final.
- **Entrega:** reporte inicial en semana 4; comparación formal de recuperación y generación, análisis de errores, costo/latencia y límites de validez para la tesis en el mes 2.
- **Aceptación:** separación desarrollo/final sin ajustar con el final; casos directos, paráfrasis, excepciones, fechas, sustituciones, contradicciones y fuera de alcance. Registrar tamaño/procedencia y faltantes; repetir resultados con versiones de corpus, extracción, chunks, índice, embeddings, modelo, prompts, parámetros y costo.

## Decisiones cerradas, propuestas y pendientes

| Estado | Decisión / tratamiento |
|---|---|
| Cerradas | Monorepo modular; frontend Vue/Vite/TypeScript/Nuxt UI sin Nuxt; API Python 3.14/uv/FastAPI/Pydantic; separación API/RAG/ingesta; R2 y Vectorize; tipos manuales; sin deploy en CI actual. Detalles en arquitectura. |
| Aprobada; PoC local ejecutada | `langchain-core` 1.7.6 sin LangGraph: cadena asíncrona determinista en Workers/Pyodide. No valida retrieval, LLM ni todas sus dependencias/rutas; no se reabre la comparación con el paquete completo. |
| Pendiente: OCR/parser | Firecrawl Parse sigue siendo candidato; AnyDoc bajo evaluación. Según evidencia documental aportada por el coordinador, AnyDoc no resuelve OCR local; no asumirlo por usar WASM. Comparar antes de elegir. |
| Pendiente: embeddings | Modelo, dimensiones, compatibilidad Vectorize y calidad en español; selección basada en benchmark, no en disponibilidad nominal. |
| Pendiente: auth | Cloudflare Access o alternativa; debe proteger operaciones y recursos administrativos. |
| Aprobada solo local: herramientas Workers | `workers-py` 1.17.9 y `workers-runtime-sdk` 1.9.6, con Wrangler/workerd requeridos. PoC mínima local API/RAG superada, sin certificar producción ni rutas futuras; sin login, deploy, recursos remotos ni llamadas pagadas. |
| Pendiente: integración/gateway | Bindings, APIs, cliente del gateway y compatibilidad real por validar. El coordinador verificó el ID de catálogo Vercel `openai/gpt-6-luna`; no ejecutó inferencia: disponibilidad operativa y costo siguen sin demostrarse. |
| Propuesta: ingesta | Worker separado, previsiblemente TypeScript, y Workflows; cerrar runtime/librerías según PoC y límites, no convertir cada bloque lógico en Worker por defecto. |
| Aprobada: herramientas frontend | Vue 3.5.45, Vite 8.3.6, plugin-vue 6.0.10, Nuxt UI 4.12.0, Tailwind 4.3.5, vue-tsc 3.3.14, ESLint 10.14.0, eslint-plugin-vue 10.12.0, typescript-eslint 8.72.1 y TypeScript 6.0.3, más dependencias técnicas necesarias. Instalación limpia y controles locales verificados en Docker, sin integración backend; sin Router, Pinia, cliente HTTP externo ni framework de tests. Formato adicional no seleccionado. |

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

Cada hito deja estado, responsable, pruebas, resultados observados, costo y limitaciones en su superficie; no almacenar conversaciones completas como evidencia. La [evidencia técnica](INVESTIGACION.md) registra las pruebas locales y sus límites. El siguiente insumo es un PDF institucional escaneado con tablas y FAQs/respuestas verificadas para inventario y benchmark P0; no presupone proveedor ni presupuesto nuevo. Este cierre es documental: no crea infraestructura, contratos compartidos ni modifica el API.
