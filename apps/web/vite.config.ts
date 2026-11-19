import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import ui from '@nuxt/ui/vite'

/** Una app instalable: su HTML, scope, página sin conexión y prefijos que su SW ignora. */
interface PwaApp { html: string; sw: string; scope: string; offline: string; manifest: string; icons: string; exclude: string[] }

const PWA_APPS: PwaApp[] = [
  { html: 'index.html', sw: 'sw.js', scope: '/', offline: '/offline.html', manifest: '/manifest.webmanifest', icons: '/icons/', exclude: ['/api/', '/admin/'] },
  { html: 'admin/index.html', sw: 'admin/sw.js', scope: '/admin/', offline: '/admin/offline.html', manifest: '/admin/manifest.webmanifest', icons: '/admin/icons/', exclude: ['/api/'] },
]
const ICON_FILES = ['icon.svg', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png', 'apple-touch-icon.png']

/**
 * Genera los service workers a partir de `pwa/sw.js` (sin dependencias): precache
 * del shell (HTML sin conexión, JS/CSS con hash referenciados por cada HTML,
 * iconos, manifest) y versión de caché derivada de ese contenido. Solo en build.
 */
function pwaPlugin(): Plugin {
  const template = readFileSync(resolve(import.meta.dirname, 'pwa/sw.js'), 'utf8')
  return {
    name: 'unitropico-pwa',
    apply: 'build',
    writeBundle(options) {
      const dir = options.dir ?? resolve(import.meta.dirname, 'dist')
      for (const app of PWA_APPS) {
        const html = readFileSync(join(dir, app.html), 'utf8')
        const assets = [...new Set(html.match(/\/assets\/[^"'\s)]+/g) ?? [])].sort()
        const precache = [app.offline, app.manifest, ...ICON_FILES.map(name => app.icons + name), ...assets]
        const version = createHash('sha256').update(template).update(JSON.stringify(precache)).digest('hex').slice(0, 12)
        const config = JSON.stringify({ version, scope: app.scope, offline: app.offline, precache, exclude: app.exclude })
        const source = template.replace(/\/\* __SW_CONFIG__ \*\/ \{[\s\S]*?\n\}/, `/* __SW_CONFIG__ */ ${config}`)
        if (source === template) throw new Error('pwa/sw.js: marcador __SW_CONFIG__ no encontrado')
        writeFileSync(join(dir, app.sw), source)
      }
    },
  }
}

export default defineConfig({
  build: {
    rollupOptions: {
      // Multipágina sin router: el chat público (/) y el panel (/admin/).
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        admin: resolve(import.meta.dirname, 'admin/index.html'),
      },
    },
  },
  // Development only: production needs a same-origin /api reverse proxy.
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        rewrite: path => path.replace(/^\/api/, ''),
      },
    },
  },
  plugins: [
    vue(),
    pwaPlugin(),
    ui({
      colorMode: false,
      router: false,
      autoImport: false,
      components: false,
      ui: {
        // Verde institucional sobrio + neutros slate. Ajustes finos en main.css.
        colors: { primary: 'green', neutral: 'slate' },
      },
    }),
  ],
})
