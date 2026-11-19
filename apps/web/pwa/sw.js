// Service worker mínimo, escrito a mano (sin dependencias). Plantilla: el build
// (plugin `pwaPlugin` en vite.config.ts) sustituye SW_CONFIG y emite
// dist/sw.js (scope /) y dist/admin/sw.js (scope /admin/).
//
// Reglas:
// - Solo se cachea el shell estático: HTML de la app, JS/CSS con hash de
//   /assets/, iconos, manifest y la página sin conexión.
// - /api/* (chat y admin) NUNCA pasa por la caché: el SW no intercepta esas
//   peticiones y van siempre a la red. El SW no lee cabeceras ni guarda tokens.
// - Navegaciones: red primero; sin red se muestra la página sin conexión.
// - Caché versionada por build; al activar se borran las versiones anteriores.
// - Actualización segura: sin skipWaiting. Un SW nuevo espera a que se cierren
//   las pestañas que usan el anterior (que conservan su caché y sus assets) y,
//   al activarse, borra las cachés viejas.

/**
 * @typedef {{ version: string, scope: string, offline: string, precache: string[], exclude: string[] }} SwConfig
 */

/** @type {SwConfig} */
const SW_CONFIG = /* __SW_CONFIG__ */ {
  version: 'dev',
  scope: '/',
  offline: '/offline.html',
  precache: ['/offline.html'],
  exclude: ['/api/', '/admin/'],
}

const CACHE_PREFIX = `unitropico-shell:${SW_CONFIG.scope}:`
const CACHE_NAME = `${CACHE_PREFIX}${SW_CONFIG.version}`

/**
 * Decide cómo tratar una petición. Exportada de forma implícita para los tests.
 * @param {{ method: string, url: string, mode?: string }} request
 * @param {string} origin
 * @returns {'network' | 'navigate' | 'asset'}
 *   network: el SW no responde (el navegador va a la red sin caché del SW).
 */
function routeFor(request, origin) {
  if (request.method !== 'GET') return 'network'
  const url = new URL(request.url)
  if (url.origin !== origin) return 'network'
  const path = url.pathname
  if (path === '/api' || path.startsWith('/api/')) return 'network'
  if (SW_CONFIG.exclude.some(prefix => path.startsWith(prefix))) return 'network'
  if (request.mode === 'navigate') return path.startsWith(SW_CONFIG.scope) ? 'navigate' : 'network'
  // Los assets con hash son comunes a ambas apps y viven fuera del scope del admin.
  if (path.startsWith('/assets/') || SW_CONFIG.precache.includes(path)) return 'asset'
  return 'network'
}

/** Solo respuestas completas y del mismo origen entran en la caché. */
const cacheable = (/** @type {Response} */ response) =>
  response.ok && response.status === 200 && response.type === 'basic'

const sw = /** @type {any} */ (self)

sw.addEventListener('install', (/** @type {any} */ event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SW_CONFIG.precache.map(path => new Request(path, { cache: 'reload' })))),
  )
})

sw.addEventListener('activate', (/** @type {any} */ event) => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
        .map(key => caches.delete(key))))
      .then(() => sw.clients.claim()),
  )
})

sw.addEventListener('fetch', (/** @type {any} */ event) => {
  const request = event.request
  const route = routeFor(request, sw.location.origin)
  if (route === 'network') return

  if (route === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match(SW_CONFIG.offline, { cacheName: CACHE_NAME })
        .then(cached => cached ?? new Response('Sin conexión: el asistente necesita internet.', {
          status: 503,
          headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        }))),
    )
    return
  }

  // Assets con hash (inmutables) e iconos: caché primero, red como respaldo.
  event.respondWith(
    caches.open(CACHE_NAME).then(cache => cache.match(request).then(cached => {
      if (cached) return cached
      return fetch(request).then(response => {
        if (cacheable(response)) void cache.put(request, response.clone())
        return response
      })
    })),
  )
})
