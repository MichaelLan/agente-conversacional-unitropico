# Arquitectura del agente conversacional de Unitrópico

**Estado:** arquitectura lógica acordada; decisiones técnicas pendientes de validación.

**Fecha:** 8 de noviembre de 2026.

**Propósito:** documento de referencia para la implementación y evaluación del proyecto de maestría.

## 1. Objetivo y alcance

Desarrollar un agente conversacional público que oriente a estudiantes de Unitrópico acerca de los procesos y servicios del área de **Fomento Socioeconómico de Bienestar Universitario**, respondiendo con evidencia institucional y derivando al área correspondiente cuando no disponga de respaldo suficiente.

No es un asistente generalista, no requiere autenticación estudiantil, no consulta datos personales o académicos y no crea tickets de atención. La derivación consiste en indicar dónde debe acudir el estudiante.

### Principios del sistema

- **Trazabilidad:** las respuestas fundamentadas deben identificar documento, sección y página cuando la fuente lo permita.
- **Fidelidad:** no inventar fechas, requisitos, convocatorias, beneficios ni procedimientos.
- **Vigencia:** distinguir versiones, documentos sustituidos y convocatorias vencidas.
- **Abstención:** cuando la evidencia es insuficiente o la consulta no está dentro del alcance, no improvisar una respuesta.
- **Evaluabilidad:** mantener datos y procesos reproducibles para comparar recuperación y generación.
- **Simplicidad operativa:** evitar microservicios o complejidad no justificada por la escala.

## 2. Condiciones de partida

| Dimensión | Decisión o dato |
|---|---|
| Documentos iniciales | Aproximadamente 10 |
| Formatos predominantes | PDF escaneados, algunas tablas |
| Documento de mayor tamaño | Aproximadamente 19 MB y hasta 30 páginas |
| Versionado | Existen documentos que sustituyen otros, convocatorias vencidas y posibles contradicciones |
| Revisión institucional del contenido extraído | No disponible en el MVP |
| Chat | Público y anónimo |
| Información personal estudiantil | No requerida |
| Memoria conversacional | Solo durante una sesión |
| Derivación | Indicar dónde acudir, sin crear solicitudes |
| Tráfico estimado | 100 consultas/día, 10 usuarios simultáneos |
| Preguntas frecuentes y respuestas institucionales | Disponibles |
| Funcionario evaluador de respuestas | No disponible |
| Citas | Documento, sección y página en respuestas basadas en documentos |
| Documentos contradictorios | Exponer lo que establece cada uno y citarlo; no decidir sin respaldo |
| Requisitos de precisión o latencia impuestos por la maestría | No se han definido |
| Modelos | Preferencia por OpenAI y Anthropic; acceso previsto mediante Vercel AI Gateway |
| LLM previsto | GPT-6 Luna, pendiente de verificar el identificador y disponibilidad concretos en el gateway |
| Procesamiento externo | Permitido; Firecrawl + AnyDoc son candidatos |
| Presupuesto | Prima funcionamiento y evaluación; no hay techo mensual firme. La estimación de >USD 50/mes no está validada |
| Administradores | Uno |
| Panel administrativo | Cargar, editar metadatos, despublicar, revisar errores y reprocesar |
| MVP | 1 mes |
| Tesis | 2 meses |
| Operación posterior | Se espera que Unitrópico pueda mantener el sistema |
| Dominio y cuentas institucionales durante la tesis | No disponibles |

## 3. Decisiones arquitectónicas acordadas

### 3.1 Repositorio y organización

- **Monorepo modular en GitHub.**
- Cada proyecto se administra con sus propias dependencias, lockfile, pruebas, lint, formato y CI.
- Sin `pyproject.toml` global y sin workspace JavaScript obligatorio.
- Sin paquete global `contracts/`.
- Modelos Pydantic y tipos TypeScript **mantenidos manualmente**. No se generarán tipos/clientes desde OpenAPI; FastAPI puede seguir exponiendo su esquema nativo.
- Compartir código únicamente cuando exista una necesidad concreta y probada.
- CI independiente por componente, con integración cruzada cuando haya contratos entre aplicaciones.
- **No se desplegará a Cloudflare desde GitHub Actions en la etapa actual.**

### 3.2 Frontend

- Vue 3, Vite, TypeScript y Nuxt UI utilizado en Vue sin Nuxt.
- Separación por vistas, componentes, composables, servicios HTTP y tipos; componentes reutilizables cuando existe reutilización real.
- Chat público con preguntas frecuentes, respuestas con fuentes y estado limitado a la sesión.
- Panel de un administrador para gestionar documentos, metadatos, publicación, errores y reprocesamientos.
- Interfaz desacoplada de detalles internos del RAG.

### 3.3 API

- Python **3.14** gestionado con `uv` y `uv.lock`, FastAPI, Pydantic y estructura modular.
- Organización deseada en `core/`, `infra/`, `domain/`, `services/` y `api/`.
- SOLID, especialmente **inversión de dependencias**: el dominio y los servicios dependen de abstracciones, no de SDK de Cloudflare, cliente del gateway o bibliotecas concretas.
- Composición e inyección de dependencias en `main.py` (punto de composición). `core/settings.py` concentra la lectura y validación de configuración, instanciándose **solo desde `main.py`**. Las dependencias reciben únicamente sus parámetros explícitos por constructor o firma, no un objeto global de configuración.
- `core/logger.py` contiene el logger transversal, importable donde corresponda.
- Todas las clases, funciones y métodos propios cuentan con docstrings concisos y útiles; docstrings de módulo únicamente si aportan contexto necesario; comentarios solo para explicar decisiones no evidentes.
- El API gestiona las operaciones HTTP y orquesta el acceso al servicio RAG y a la ingesta, pero no implementa el OCR dentro del request HTTP.

### 3.4 RAG

- Servicio Python independiente con la misma disciplina de código y configuración que el API.
- LangChain **como tecnología seleccionada, pendiente de prueba con versiones actuales en Python 3.14 y Cloudflare Workers**.
- LangGraph **no obligatorio**: solo si un flujo con estado/ciclos/reintentos se justifica.
- GPT-6 Luna es el modelo generador solicitado, pero debe verificarse el nombre/model ID exacto y la disponibilidad a través de Vercel AI Gateway. No inventar nombres de modelo ni endpoints.
- Embeddings especializados y compatibles con los límites de Vectorize; modelo todavía por seleccionar.
- Recuperación híbrida propuesta: similitud vectorial en Vectorize y búsqueda lexical en D1 FTS5; fusión RRF; reranking solo si las pruebas muestran una mejora.
- Chunking preferentemente estructural (secciones, artículos, numerales, requisitos, excepciones y tablas), con referencias al documento y ubicación exacta.
- Evaluación de suficiencia y vigencia de evidencia antes de generar, validación de citas y abstención controlada.

### 3.5 Cloudflare y servicios

| Componente | Propuesta | Estado |
|---|---|---|
| Frontend | Web estática en Cloudflare | Acordado como destino, despliegue posterior |
| API | Python Worker con FastAPI | Acordado; probar runtime y dependencias |
| RAG | Python Worker | Propuesto; condicionado a compatibilidad |
| Ingesta | Worker independiente, previsiblemente TypeScript | Propuesto; lenguaje y librerías por cerrar |
| Orquestación de ingesta | Cloudflare Workflows | Propuesto |
| Archivos y extracciones | R2 | Acordado |
| Metadatos y búsqueda lexical | D1, con FTS5 como candidato | Propuesto |
| Embeddings | Vectorize | Acordado |
| OCR/parse | Firecrawl Parse; AnyDoc bajo evaluación | Sin decisión definitiva |
| Inferencia | Vercel AI Gateway hacia OpenAI/Anthropic | Dirección seleccionada; modelos/SDK y credenciales pendientes |
| Auth admin | Cloudflare Access o alternativa | Pendiente |
| Protección de chat público | Rate limiting y mecanismo antiabuso adecuado | Necesario; implementación por definir |

**Regla:** un bloque lógico no equivale automáticamente a un Worker desplegado. Separar solo por requisitos de runtime, mantenimiento o aislamiento.

## 4. Arquitectura lógica

Hay dos rutas operativas independientes.

### 4.1 Ingesta documental, asíncrona

1. El administrador solicita la carga de un documento desde la web.
2. El API autoriza la operación, registra el documento y habilita su almacenamiento en **R2**; para archivos grandes, contemplar carga directa con URL firmada y validaciones de seguridad.
3. Se inicia un **Workflow** pasando identificadores, no el contenido completo del PDF.
4. El procesador recupera el original de R2, identifica formato y ejecuta el parser/OCR elegido.
5. Guarda en R2 la extracción estructurada (p. ej., Markdown y JSON con páginas, tablas y metadatos), sin depender de que el índice vectorial sea la única copia.
6. Valida presencia y calidad mínima de la extracción, y registra errores para reprocesar sin rehacer etapas completadas.
7. Divide en chunks que preservan la estructura y ubicación, genera embeddings y actualiza **Vectorize** y el catálogo textual/metadatos en **D1**.
8. Publica solo las versiones que hayan superado las validaciones; archiva o despublica las versiones correspondientes conforme a reglas explícitas.

El flujo es **idempotente**, admite reintentos por etapa y evita pasar resultados voluminosos a través de estados de Workflows o cadenas de HTTP entre Workers.

### 4.2 Consulta conversacional, síncrona

1. El estudiante escribe una pregunta; el cliente conserva un contexto limitado a la sesión.
2. El API valida la solicitud, aplica límites y llama a RAG.
3. RAG interpreta la consulta y recupera candidatos mediante búsqueda semántica + lexical.
4. Fusiona, filtra por vigencia y, si procede, reordena candidatos; recupera contexto suficiente.
5. Evalúa si existe evidencia adecuada para el dominio solicitado.
6. Si la hay, solicita al LLM una respuesta basada en el contexto, con referencias reales.
7. Verifica que las citas correspondan al material recuperado y que no haya afirmaciones no sustentadas.
8. Si no existe evidencia suficiente, el sistema se abstiene y orienta al estudiante al área competente.
9. Entrega respuesta y referencias al frontend; registra métricas operativas mínimas sin recopilar innecesariamente datos personales.

### 4.3 Contradicciones y vigencia

Los documentos tienen metadatos como `document_id`, `version_id`, `status`, `document_type`, `issued_at`, `effective_from`, `effective_until`, `supersedes_id`.

- No equiparar fecha de carga con vigencia.
- Cuando un documento sustituye formalmente otro, no recuperar indiscriminadamente versiones obsoletas para responder como si siguieran vigentes.
- Cuando dos documentos publicados presentan aparentes contradicciones, **exponer ambas disposiciones y sus citas** sin declarar una prevalencia no sustentada.
- No inventar números de página, secciones o referencias; la extracción debe preservar y proporcionar estos metadatos.

## 5. Estrategia RAG y evaluaciones

### 5.1 Recuperación

- Baseline semántico con Vectorize.
- Experimento de recuperación híbrida Vectorize + D1 FTS5 con RRF.
- Evaluar chunking estructural frente a alternativa simple y reranking opcional.
- Conservar vínculos fuente → versión → sección → página → chunk.
- Los contenidos completos de los chunks y los archivos deben conservarse fuera de los metadatos limitados del vector store.

### 5.2 Evaluación

Datos de entrada: preguntas frecuentes reales y respuestas institucionales verificadas. Diseñar casos directos, paráfrasis, excepciones, versiones, fechas, preguntas fuera de alcance y evidencia contradictoria. Separar conjunto de desarrollo del de evaluación final.

| Indicador | Objetivo de ingeniería **propuesto, no impuesto ni alcanzado** |
|---|---|
| Recall@5 | ≥ 90 % |
| Citas correctas | ≥ 95 % |
| Afirmaciones sin respaldo | ≤ 2 % |
| Abstención correcta fuera de alcance | ≥ 95 % |
| Latencia P95 | Medir antes de fijar objetivo |

Otras métricas: MRR/nDCG, calidad del grounding, costo por consulta y tasas de error. Los criterios y procedimientos deben documentarse; como no hay un evaluador institucional dedicado, las conclusiones de calidad deben reflejar esta limitación y evitar confundir la calificación automática con validación humana independiente.

## 6. Gestión de sesión y seguridad

- Chat público y anónimo. La web conserva el historial durante la sesión (por ejemplo, `sessionStorage`) y envía solo la porción necesaria; el backend limita su tamaño y no acepta roles privilegiados remitidos por clientes.
- No persistir conversaciones completas en D1 inicialmente. Los logs operativos deben minimizar contenido sensible.
- Un administrador autenticado; proteger rutas y archivos del panel. Evaluar Cloudflare Access.
- Limitar abuso y costos: rate limiting, límites de tokens y tamaño del contexto, presupuesto/alertas y protección del formulario público.
- Secretos fuera del repositorio; configuraciones por componente y plantillas sin credenciales.
- Los documentos son públicos según el alcance del proyecto, pero las credenciales y las operaciones administrativas no lo son.

## 7. Estructura propuesta del monorepo

```text
unitropico-agent/
├── apps/
│   ├── web/
│   │   ├── src/
│   │   ├── package.json
│   │   └── pnpm-lock.yaml
│   ├── api/
│   │   ├── src/
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   └── wrangler.jsonc
│   └── rag/
│       ├── src/
│       ├── tests/
│       ├── pyproject.toml
│       ├── uv.lock
│       └── wrangler.jsonc
├── workers/
│   └── ingestion/
│       ├── src/
│       ├── tests/
│       ├── package.json
│       ├── pnpm-lock.yaml
│       └── wrangler.jsonc
├── evaluations/
│   ├── datasets/
│   ├── retrieval/
│   ├── generation/
│   └── pyproject.toml
├── infra/
│   └── migrations/
├── .github/
│   └── workflows/
│       ├── web.yml
│       ├── api.yml
│       ├── rag.yml
│       ├── ingestion.yml
│       └── integration.yml
└── README.md
```

Estructura objetivo, **no mandato de crear carpetas vacías**. Los archivos de configuración se crean cuando haya un componente implementado y una herramienta escogida. No crear `contracts/` ni generación automática de OpenAPI.

### Responsabilidades de los tres backends

- `apps/api`: endpoints públicos y de administración, validación y coordinación.
- `apps/rag`: recuperación, contexto, generación, citas y abstenciones.
- `workers/ingestion`: extracción asíncrona, chunking, embeddings e indexación.

Las interfaces entre servicios son contratos explícitos, aunque sus modelos Pydantic/TypeScript se mantengan manualmente. La CI de integración valida los formatos JSON relevantes.

## 8. CI/CD de la etapa actual

- Workflows por componente, activados por rutas y dependencias relevantes.
- Web: lint y comprobación de TypeScript (y formato si se aprueba la herramienta).
- API y RAG: `uv sync --locked --group dev`, `uv run ruff check .`, `uv run ruff format --check .`, y pruebas si existen.
- Ingesta: configurar lint cuando se confirme lenguaje y herramienta.
- Integración: comprobaciones cruzadas de contratos cuando existan endpoints interoperables.
- **No incluir comandos de deploy, tokens de Cloudflare ni provisión automática de infraestructura todavía.**
- Si GitHub exige checks obligatorios, evitar que un workflow filtrado deje un check permanentemente pendiente.

## 9. Plan de ejecución

| Periodo | Entregable buscado |
|---|---|
| Semana 1 | Monorepo modular, prueba Python Worker/RAG y benchmark OCR con PDF escaneado y tablas |
| Semana 2 | Ingesta estructurada, índices, RAG base y primeras evaluaciones |
| Semana 3 | Chat Vue y panel básico de administración |
| Semana 4 | Estados, reproceso, seguridad, CI de calidad, integración y MVP |
| Segundo mes | Evaluación formal, comparaciones, análisis de errores y documentación de tesis |

Calendario objetivo sujeto a resultados de la prueba de runtime y OCR. Priorizar un **vertical slice** (documento → extracción → recuperación → respuesta con cita) antes de construir todas las pantallas.

## 10. Incertidumbres y pruebas pendientes

1. **OCR y parser:** comparar Firecrawl Parse, AnyDoc + OCR externo y alternativa nativa si vale la pena, con PDF escaneado de tablas. Verificar extracción por página, precisión, costo y recuperación de errores. El hecho de contar con WASM no garantiza que AnyDoc ejecute toda la ruta OCR dentro de Workers.
2. **Compatibilidad Python 3.14 / Cloudflare:** desplegar una prueba mínima de FastAPI y otra de LangChain actual con las dependencias reales. Cloudflare habilita Python 3.14 en Workers, pero eso no asegura que cualquier paquete funcione en Pyodide/WASM.
3. **Modelo GPT-6 Luna:** confirmar identificador real, disponibilidad y uso de Vercel AI Gateway. No inventar nombres ni parámetros.
4. **Embeddings:** seleccionar modelo y dimensiones compatibles con Vectorize, evaluando desempeño en español.
5. **Tablas y citas:** confirmar referencias fiables a página y sección después de OCR y chunking.
6. **Vigencia:** comprobar si las fechas y relaciones de sustitución constan en los documentos; de lo contrario, capturarlas como metadatos administrativos.
7. **Autenticación:** seleccionar Cloudflare Access u otro mecanismo simple y seguro para el administrador.
8. **Integración Workers:** concretar bindings, APIs y gestión de estados; mantener los costos de llamadas bajo control.
9. **Reranking y LangGraph:** no añadir sin beneficio medido o necesidad de flujos complejos.

## 11. Criterio de cierre

La arquitectura **lógica** y el enfoque de monorepo quedan acordados. La elección concreta de OCR, embeddings, modelos, dependencias y protocolos debe pasar por pruebas y aprobación. El proyecto no debe confundir sofisticación de infraestructura con calidad RAG: primero lograr una extracción fiel, luego una recuperación evaluable y finalmente una generación respaldada por fuentes.

## 12. Referencias oficiales útiles para implementación

- Cloudflare Python Workers: https://developers.cloudflare.com/workers/languages/python/
- Cloudflare FastAPI: https://developers.cloudflare.com/workers/languages/python/packages/fastapi/
- Cloudflare paquetes Python / pywrangler: https://developers.cloudflare.com/workers/languages/python/packages/
- Cloudflare flags y Python 3.14: https://developers.cloudflare.com/workers/configuration/compatibility-flags/
- LangChain y LangGraph: https://docs.langchain.com/
- AnyDoc: https://github.com/firecrawl/anydoc
- Firecrawl: https://www.firecrawl.dev/

> Este documento distingue decisiones acordadas de propuestas aún por validar; no constituye una especificación de producción cerrada.
