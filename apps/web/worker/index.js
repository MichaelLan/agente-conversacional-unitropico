// Worker de la web (unitropico-web). Los Static Assets (dist/) sirven `/` y
// `/admin/` sin pasar por aquí; este script solo corre para `/api/*`
// (`run_worker_first`) y para rutas sin asset. Reenvía a la API por el service
// binding `API` las rutas que la UI usa y responde 404/405 a todo lo demás.
// JavaScript plano sin dependencias: wrangler lo empaqueta tal cual y node:test
// lo importa sin runtime de Cloudflare.

/**
 * @typedef {{ fetch(request: Request): Promise<Response> }} Fetcher
 * @typedef {{ API?: Fetcher }} Env
 * @typedef {{ action: 'proxy', path: string }
 *   | { action: 'method_not_allowed', allow: readonly string[] }
 *   | { action: 'not_found' }} RouteDecision
 */

const ADMIN_PATH = /^\/api\/admin(\/[A-Za-z0-9_-]+)+$/

/**
 * Rutas expuestas: [patrón exacto o RegExp, métodos permitidos].
 * `/retrieval/query` no se expone: es diagnóstico interno y la UI no lo usa.
 * @type {ReadonlyArray<readonly [string | RegExp, readonly string[]]>}
 */
const ROUTES = [
  ['/api/chat', ['POST']],
  ['/api/health', ['GET', 'HEAD']],
  [ADMIN_PATH, ['GET', 'POST', 'PATCH', 'DELETE']],
]

/**
 * Cabeceras añadidas a toda respuesta generada o reenviada por el Worker
 * (`_headers` no se aplica a respuestas del Worker).
 * @type {Readonly<Record<string, string>>}
 */
export const API_SECURITY_HEADERS = {
  'Cache-Control': 'no-store',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'X-Robots-Tag': 'noindex, nofollow',
}

/**
 * Decide qué hacer con una petición. Pura: solo mira método y URL.
 * Rechaza rutas ambiguas (`//`, barras codificadas, barra final) para no
 * depender de cómo normalice la API.
 * @param {string} method
 * @param {URL} url
 * @returns {RouteDecision}
 */
export function decideRoute(method, url) {
  const pathname = url.pathname
  if (/\/\/|%2f|%5c|\\/i.test(pathname)) return { action: 'not_found' }
  for (const [pattern, methods] of ROUTES) {
    const matches = typeof pattern === 'string' ? pathname === pattern : pattern.test(pathname)
    if (!matches) continue
    if (!methods.includes(method.toUpperCase())) return { action: 'method_not_allowed', allow: methods }
    return { action: 'proxy', path: pathname.slice('/api'.length) }
  }
  return { action: 'not_found' }
}

/**
 * Construye la petición para la API: misma URL sin el prefijo `/api` (query
 * incluida), y mismo método, cabeceras (p. ej. `Authorization`,
 * `CF-Connecting-IP`) y cuerpo.
 * @param {Request} request
 * @param {string} path ruta ya sin `/api`
 * @returns {Request}
 */
export function buildUpstreamRequest(request, path) {
  const url = new URL(request.url)
  url.pathname = path
  return new Request(url, request)
}

/**
 * Copia la respuesta con las cabeceras de seguridad de la API.
 * @param {Response} response
 * @returns {Response}
 */
export function withSecurityHeaders(response) {
  const secured = new Response(response.body, response)
  for (const [name, value] of Object.entries(API_SECURITY_HEADERS)) secured.headers.set(name, value)
  return secured
}

/**
 * @param {number} status
 * @param {Record<string, string>} [extra]
 * @returns {Response}
 */
function emptyResponse(status, extra = {}) {
  return withSecurityHeaders(new Response(null, { status, headers: extra }))
}

export default {
  /**
   * @param {Request} request
   * @param {Env} env
   * @returns {Promise<Response>}
   */
  async fetch(request, env) {
    const decision = decideRoute(request.method, new URL(request.url))
    if (decision.action === 'not_found') return emptyResponse(404)
    if (decision.action === 'method_not_allowed') {
      return emptyResponse(405, { Allow: decision.allow.join(', ') })
    }
    if (!env.API) return emptyResponse(502)
    try {
      return withSecurityHeaders(await env.API.fetch(buildUpstreamRequest(request, decision.path)))
    } catch {
      // Sin detalles internos: la UI muestra su aviso genérico con «Reintentar».
      return emptyResponse(502)
    }
  },
}
