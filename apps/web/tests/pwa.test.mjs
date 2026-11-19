import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import vm from 'node:vm'
import { srcRoot, webRoot } from './support/load.mjs'

const read = path => readFile(join(webRoot, path), 'utf8')

/** Ancho y alto de un PNG (cabecera IHDR). */
async function pngSize(path) {
  const buffer = await readFile(join(webRoot, 'public', path))
  assert.equal(buffer.subarray(1, 4).toString('ascii'), 'PNG', path)
  return `${buffer.readUInt32BE(16)}x${buffer.readUInt32BE(20)}`
}

const APPS = [
  { manifest: 'public/manifest.webmanifest', html: 'index.html', scope: '/', id: '/' },
  { manifest: 'public/admin/manifest.webmanifest', html: 'admin/index.html', scope: '/admin/', id: '/admin/' },
]

test('each app has a valid installable manifest with existing PNG icons (192, 512, maskable)', async () => {
  for (const app of APPS) {
    const manifest = JSON.parse(await read(app.manifest))
    for (const key of ['name', 'short_name', 'start_url', 'scope', 'display', 'theme_color', 'background_color', 'icons']) {
      assert.ok(manifest[key], `${app.manifest}: ${key}`)
    }
    assert.ok(manifest.short_name.length <= 15, 'short_name corto para la pantalla de inicio')
    assert.equal(manifest.scope, app.scope)
    assert.equal(manifest.start_url, app.scope)
    assert.equal(manifest.id, app.id)
    assert.equal(manifest.display, 'standalone')
    assert.match(manifest.theme_color, /^#[0-9a-f]{6}$/i)
    const sizes = new Set()
    let maskable = false
    for (const icon of manifest.icons) {
      assert.equal(icon.type, 'image/png')
      assert.ok(icon.src.startsWith(app.scope), `${icon.src} dentro del scope`)
      assert.equal(await pngSize(icon.src), icon.sizes, icon.src)
      sizes.add(icon.sizes)
      if (icon.purpose === 'maskable') maskable = true
    }
    assert.ok(sizes.has('192x192') && sizes.has('512x512'), app.manifest)
    assert.ok(maskable, `${app.manifest}: icono maskable`)
    await stat(join(webRoot, 'public', app.scope, 'offline.html'))
  }
})

test('HTML entries link manifest, theme-color, apple-touch-icon and safe-area viewport', async () => {
  for (const app of APPS) {
    const html = await read(app.html)
    assert.match(html, new RegExp(`<link rel="manifest" href="${app.scope}manifest\\.webmanifest"`))
    assert.match(html, /<meta name="theme-color" content="#[0-9a-f]{6}"/i)
    assert.match(html, /viewport-fit=cover/)
    assert.match(html, /apple-mobile-web-app-capable/)
    const touch = /<link rel="apple-touch-icon"[^>]*href="([^"]+)"/.exec(html)
    assert.ok(touch, app.html)
    assert.equal(await pngSize(touch[1]), '180x180')
  }
  const offline = await read('public/offline.html')
  assert.match(offline, /Sin conexión: el asistente necesita internet/)
  assert.doesNotMatch(offline, /<script/)
})

/** Carga la plantilla del SW en un contexto aislado con una configuración dada. */
async function loadSw(config) {
  let source = await read('pwa/sw.js')
  if (config) source = source.replace(/\/\* __SW_CONFIG__ \*\/ \{[\s\S]*?\n\}/, `/* __SW_CONFIG__ */ ${JSON.stringify(config)}`)
  const listeners = {}
  const context = {
    URL, Request, Response, Promise,
    self: { location: { origin: 'https://app.test' }, addEventListener: (type, fn) => { listeners[type] = fn }, clients: { claim: async () => {} } },
    caches: { open: async () => ({ match: async () => undefined, put: async () => {}, addAll: async () => {} }), keys: async () => [], match: async () => undefined, delete: async () => true },
    fetch: async () => new Response('red'),
  }
  vm.createContext(context)
  vm.runInContext(source, context)
  return { routeFor: context.routeFor, listeners }
}

test('service worker never handles /api/* nor non-GET requests; only shell assets and navigations', async () => {
  const chat = await loadSw({ version: 't', scope: '/', offline: '/offline.html', precache: ['/offline.html', '/icons/icon-192.png'], exclude: ['/api/', '/admin/'] })
  const o = 'https://app.test'
  const r = (url, extra = {}) => chat.routeFor({ method: 'GET', url: o + url, mode: 'no-cors', ...extra }, o)
  assert.equal(r('/api/chat', { method: 'POST' }), 'network')
  assert.equal(r('/api/chat'), 'network')
  assert.equal(r('/api'), 'network')
  assert.equal(r('/api/admin/documents'), 'network')
  assert.equal(r('/api/health', { mode: 'navigate' }), 'network')
  assert.equal(r('/admin/', { mode: 'navigate' }), 'network', 'el SW del chat no controla el admin')
  assert.equal(r('/', { mode: 'navigate' }), 'navigate')
  assert.equal(r('/assets/main-abc123.js'), 'asset')
  assert.equal(r('/icons/icon-192.png'), 'asset')
  assert.equal(r('/assets/x.js', { method: 'HEAD' }), 'network')
  assert.equal(chat.routeFor({ method: 'GET', url: 'https://api.iconify.design/lucide.json' }, o), 'network')

  const admin = await loadSw({ version: 't', scope: '/admin/', offline: '/admin/offline.html', precache: ['/admin/offline.html'], exclude: ['/api/'] })
  const a = (url, extra = {}) => admin.routeFor({ method: 'GET', url: o + url, ...extra }, o)
  assert.equal(a('/admin/', { mode: 'navigate' }), 'navigate')
  assert.equal(a('/api/admin/versions/v1/chunks'), 'network')
  assert.equal(a('/api/admin/uploads', { method: 'POST' }), 'network')
  assert.equal(a('/assets/admin-abc.js'), 'asset')

  // El listener de fetch no llama a respondWith para /api (va directo a la red).
  let responded = false
  chat.listeners.fetch({ request: { method: 'GET', url: `${o}/api/chat`, mode: 'cors' }, respondWith: () => { responded = true } })
  assert.equal(responded, false)
})

test('service worker template stays minimal: no tokens, storage or skipWaiting; build excludes /api/', async () => {
  const source = await read('pwa/sw.js')
  assert.doesNotMatch(source, /Authorization|localStorage|sessionStorage|indexedDB|token\b|skipWaiting\(/)
  assert.match(source, /caches\.delete/, 'limpia cachés de versiones anteriores')
  const config = await read('vite.config.ts')
  assert.match(config, /sw: 'sw\.js'[^\n]*exclude: \['\/api\/', '\/admin\/'\]/)
  assert.match(config, /sw: 'admin\/sw\.js'[^\n]*exclude: \['\/api\/'\]/)
  assert.match(config, /apply: 'build'/, 'el SW solo se genera en build')
  const register = await readFile(join(srcRoot, 'lib/pwa.ts'), 'utf8')
  assert.match(register, /import\.meta\.env\.PROD/, 'registro solo en producción')
  assert.match(await readFile(join(srcRoot, 'main.ts'), 'utf8'), /registerServiceWorker\('\/sw\.js', '\/'\)/)
  assert.match(await readFile(join(srcRoot, 'admin/main.ts'), 'utf8'), /registerServiceWorker\('\/admin\/sw\.js', '\/admin\/'\)/)
})

test('CSP allows same-origin service workers and manifests; sw.js is revalidated', async () => {
  const headers = await read('public/_headers')
  const csp = /Content-Security-Policy: (.*)/.exec(headers)[1]
  assert.match(csp, /worker-src 'self'/)
  assert.match(csp, /manifest-src 'self'/)
  assert.match(headers, /\/sw\.js\n\s+Cache-Control: no-cache/)
})
