# Web · Unitrópico

Interfaz autónoma en Vue, Vite, TypeScript y Nuxt UI (sin Nuxt), con formulario
experimental de recuperación documental. Envía preguntas al API y muestra
fragmentos con procedencia, **no respuestas generadas ni institucionales**.
No hay chat, historial, administración ni corpus publicado por esta prueba.
No incluyas datos personales. No representa un MVP completo ni valida contenido.

## Consulta local y estados

El formulario hace `fetch` a `/api/retrieval/query` (mismo origen), con pregunta
recortada de 1–500 caracteres y límite fijo 5. Vite elimina `/api` y reenvía a
`http://127.0.0.1:8787/retrieval/query` **solo en desarrollo**. Producción requiere
un reverse proxy de mismo origen: el build no lo configura.

- `evidence_found` (200): texto exacto escapado, IDs, SHA de fuente, página física
  y sección si existe; fragmentos recuperados no garantizan respuesta suficiente.
- `abstained` (200): sin evidencia elegible, no una respuesta negativa al usuario.
- `unavailable` (503, o 502 por upstream inválido): sin evidencia ni diagnósticos.
  Otros fallos de red/contrato muestran un error genérico; no se simulan resultados.

Sin dependencias configuradas RAG permanece no disponible. La prueba host en
[ RAG ](../rag/README.md#recuperación-interna-y-puente-local) cruza API y RAG reales
por ASGI, pero no incluye navegador, proxy Vite ni runtime/SDK Workers.
No se ejecutó Worker local para esta integración, deploy, LLM ni publicación.

## Requisitos y comandos

Node 24.15.0 y pnpm 12.18.0. TypeScript se instala **localmente**;
no hace falta instalarlo globalmente. Desde `apps/web/`:

```sh
pnpm install --frozen-lockfile
node --test tests/retrieval.test.mjs
pnpm lint
pnpm typecheck
pnpm build
```

El lockfile es exclusivo de esta aplicación. `pnpm install` se utiliza para
resolver cambios deliberados de dependencias; no se necesita un workspace
ni un manifiesto en la raíz del repositorio.

`pnpm dev` ofrece desarrollo local; `pnpm preview` sirve el build de `dist/`.
Estos dos servidores no forman parte de la verificación ejecutada.

### Política local de scripts de dependencias

Inicialmente, `pnpm install` y `pnpm install --frozen-lockfile` fallaron con
`ERR_PNPM_IGNORED_BUILDS`. Tras inspeccionar los manifiestos y scripts locales,
se autorizó exclusivamente `esbuild@0.27.7` y `vue-demi@0.14.10` mediante
`allowBuilds` en `pnpm-workspace.yaml`. Este archivo solo configura la política
de esta aplicación: no contiene globs `packages`, no incorpora otros componentes
ni establece un workspace raíz. No se modificó la configuración global de pnpm.
Otras versiones y scripts no quedan autorizados por estos matchers exactos.

Scripts revisados:

- esbuild: `postinstall: node install.js` prepara el binario correspondiente a
  la plataforma, optimiza su enlace local y comprueba que su versión sea 0.27.7.
  Si falta la dependencia opcional, contempla instalar el paquete de plataforma
  de esa misma versión mediante npm o descargarlo del registro oficial.
- vue-demi: su postinstall ejecuta `scripts/postinstall.js` con captura de errores;
  este carga Vue local y utiliza `scripts/utils.js` para copiar los tres adaptadores
  (`index.cjs`, `index.mjs`, `index.d.ts`) de la versión detectada a su propio `lib/`.
  Con Vue 3.5.45 selecciona los adaptadores de Vue 3, sin descarga ni instalación.

Después de aplicar esta política, `pnpm install --frozen-lockfile` pasó y ambos
postinstall informaron `Done`; no se actualizaron versiones ni el lockfile.
También pasaron nuevamente `pnpm lint`, `pnpm typecheck` y `pnpm build`.
La instalación se repitió sobre las dependencias locales existentes y el
coordinador confirmó después la instalación limpia y los controles en Docker
(véase [Límites de verificación](#límites-de-verificación)).
El primer typecheck ya había pasado **antes del primer build**, sin artefactos
previos ni declaraciones generadas.

## Estructura y decisiones

- `src/main.ts`: composición de Vue y plugin de Nuxt UI.
- `src/App.vue`: estructura semántica de la página y `UApp` raíz.
- `src/components/ServiceNotice.vue`: alcance y aviso de disponibilidad.
- `src/components/DocumentQuery.vue`: formulario y estados accesibles de consulta.
- `src/services/retrieval.ts`: fetch nativo y validación del contrato recibido.
- `src/assets/main.css`: Tailwind, Nuxt UI y tipografía del sistema, sin fuentes externas.
- `vite.config.ts`: Nuxt UI con `router: false`, sin autoimports de componentes
  ni composables. Los imports explícitos permiten comprobar tipos sin generar
  declaraciones durante un build previo.
- `tsconfig.json`: modo estricto; `skipLibCheck` omite la comprobación interna
  de declaraciones de terceros, no la del código propio.
- `eslint.config.js`: reglas recomendadas de JavaScript, TypeScript y Vue;
  cualquier warning hace fallar el comando de lint.

No hay Router, Pinia, cliente HTTP adicional, colección de iconos añadida,
framework de pruebas ni generación de contratos desde OpenAPI. Los tipos
del contrato de recuperación se mantienen manualmente en `src/types/retrieval.ts`.
No se crean carpetas vacías para capas todavía inexistentes.

## Versiones directas resueltas

| Paquete | Versión |
| --- | --- |
| vue | 3.5.45 |
| vite | 8.3.6 |
| @vitejs/plugin-vue | 6.0.10 |
| @nuxt/ui | 4.12.0 |
| tailwindcss | 4.3.5 |
| vue-tsc | 3.3.14 |
| typescript | 6.0.3 |
| eslint | 10.14.0 |
| eslint-plugin-vue | 10.12.0 |
| typescript-eslint | 8.72.1 |
| @eslint/js | 10.0.1 |
| vue-eslint-parser | 10.4.2 |
| @types/node | 24.19.2 |

Las versiones y peers de las tres dependencias técnicas adicionales se
consultaron en el registro oficial de npm. `pnpm exec vue-tsc --version`
informa la versión del compilador TypeScript (6.0.3), no la del paquete vue-tsc.

## Límites de verificación

Las seis pruebas con Node builtin verifican serialización, límites, estados,
validación, errores/abort y compilación del template con texto escapado; no añaden
un framework de tests. El coordinador observó tests, lint, tipos y build verdes.
La página usa landmarks, títulos jerárquicos y disposición responsive, pero
no se ejecutaron pruebas en navegador, revisión visual ni auditoría de
accesibilidad: el build no demuestra esas propiedades por sí solo.

El coordinador también verificó una instalación limpia en Docker con Node
24.15.0 y pnpm 12.18.0: instalación congelada, lint, typecheck y build pasaron.
No se montaron `node_modules` ni `dist` del host. Desde `apps/web/`:

```sh
docker run --rm --cap-drop=ALL --security-opt=no-new-privileges \
  --mount "type=bind,src=$PWD,dst=/source,readonly" \
  node:24.15.0-bookworm-slim sh -eu -c '
    mkdir /tmp/web
    tar -C /source --exclude=node_modules --exclude=dist --exclude=.env --exclude=.env.local -cf - . |
      tar --no-same-owner -C /tmp/web -xf -
    cd /tmp/web
    corepack pnpm install --frozen-lockfile
    corepack pnpm lint
    corepack pnpm typecheck
    corepack pnpm build
  '
```

La imagen observada tuvo digest
`sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d`.
No se necesita Dockerfile. La [CI de calidad](../../.github/workflows/web.yml)
se configura separadamente y aún no se ha ejecutado en GitHub. No hay despliegue,
servicios pagados ni prueba end-to-end con navegador y backend.
