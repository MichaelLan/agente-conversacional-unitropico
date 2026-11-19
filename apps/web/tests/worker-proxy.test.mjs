import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import worker, { API_SECURITY_HEADERS, buildUpstreamRequest, decideRoute } from '../worker/index.js'
import { webRoot } from './support/load.mjs'

const ORIGIN = 'https://web.example'
const decide = (method, path) => decideRoute(method, new URL(path, ORIGIN))

test('allowed routes are proxied without the /api prefix', () => {
  assert.deepEqual(decide('POST', '/api/chat'), { action: 'proxy', path: '/chat' })
  assert.deepEqual(decide('GET', '/api/health'), { action: 'proxy', path: '/health' })
  assert.deepEqual(decide('get', '/api/admin/documents'), { action: 'proxy', path: '/admin/documents' })
  assert.deepEqual(decide('POST', '/api/admin/uploads?title=a&version_label=v1'),
    { action: 'proxy', path: '/admin/uploads' })
  assert.deepEqual(decide('PATCH', '/api/admin/versions/abc123'), { action: 'proxy', path: '/admin/versions/abc123' })
  assert.deepEqual(decide('DELETE', '/api/admin/versions/abc123/corrections/cor_1'), { action: 'proxy', path: '/admin/versions/abc123/corrections/cor_1' })
  assert.deepEqual(decide('POST', '/api/admin/versions/abc_1-2/publish'),
    { action: 'proxy', path: '/admin/versions/abc_1-2/publish' })
})

test('unexpected or ambiguous /api routes are not found', () => {
  for (const path of [
    '/api', '/api/', '/api/retrieval/query', '/api/docs', '/api/openapi.json', '/api/admin', '/api/admin/',
    '/api/chat/', '/api/admin//documents', '/api/admin/versions%2Fx', '/api/admin/a%5Cb', '/api/admin/a.b',
    '/api/../chat', '/api/admin/../../retrieval/query', '/apichat', '/chat', '/health', '/admin/documents',
  ]) {
    const decision = decide('POST', path)
    // Dot segments are resolved by the URL parser: /api/../chat is /chat, outside /api.
    assert.equal(decision.action, 'not_found', path)
  }
})

test('wrong methods get 405 with Allow', () => {
  assert.deepEqual(decide('GET', '/api/chat'), { action: 'method_not_allowed', allow: ['POST'] })
  assert.equal(decide('PUT', '/api/admin/versions/x').action, 'method_not_allowed')
  assert.equal(decide('OPTIONS', '/api/health').action, 'method_not_allowed')
})

test('upstream request keeps method, query, headers and body', async () => {
  const original = new Request(`${ORIGIN}/api/admin/uploads?title=T&version_label=v1`, {
    method: 'POST',
    headers: {
      'CF-Connecting-IP': '203.0.113.7',
      Authorization: 'Bearer secret',
      'Content-Type': 'application/pdf',
    },
    body: '%PDF-1.7',
  })
  const upstream = buildUpstreamRequest(original, '/admin/uploads')
  const url = new URL(upstream.url)
  assert.equal(url.pathname, '/admin/uploads')
  assert.equal(url.search, '?title=T&version_label=v1')
  assert.equal(upstream.method, 'POST')
  assert.equal(upstream.headers.get('cf-connecting-ip'), '203.0.113.7')
  assert.equal(upstream.headers.get('authorization'), 'Bearer secret')
  assert.equal(upstream.headers.get('content-type'), 'application/pdf')
  assert.equal(await upstream.text(), '%PDF-1.7')
})

test('fetch forwards through the API binding and adds security headers', async () => {
  const seen = []
  const env = {
    API: {
      async fetch(request) {
        seen.push(request)
        return new Response('{"status":"ok"}', { status: 200, headers: { 'Content-Type': 'application/json' } })
      },
    },
  }
  const response = await worker.fetch(new Request(`${ORIGIN}/api/health`, {
    headers: { 'CF-Connecting-IP': '198.51.100.1' },
  }), env)
  assert.equal(response.status, 200)
  assert.equal(await response.text(), '{"status":"ok"}')
  assert.equal(response.headers.get('content-type'), 'application/json')
  for (const [name, value] of Object.entries(API_SECURITY_HEADERS)) assert.equal(response.headers.get(name), value)
  assert.equal(seen.length, 1)
  assert.equal(new URL(seen[0].url).pathname, '/health')
  assert.equal(seen[0].headers.get('cf-connecting-ip'), '198.51.100.1')
})

test('CF-Connecting-IP reaches the API intact and 429 + Retry-After pass through', async () => {
  const body = '{"error":{"code":"rate_limited","message":"Espera."}}'
  let forwarded
  const env = {
    API: {
      async fetch(request) {
        forwarded = request
        return new Response(body, { status: 429, headers: { 'Retry-After': '60', 'Content-Type': 'application/json' } })
      },
    },
  }
  const response = await worker.fetch(new Request(`${ORIGIN}/api/chat`, {
    method: 'POST',
    headers: { 'CF-Connecting-IP': '2001:db8::1', 'Content-Type': 'application/json', 'Content-Length': '2' },
    body: '{}',
  }), env)
  assert.equal(forwarded.headers.get('cf-connecting-ip'), '2001:db8::1')
  assert.equal(await forwarded.text(), '{}')
  assert.equal(response.status, 429)
  assert.equal(response.headers.get('retry-after'), '60')
  assert.equal(await response.text(), body)
})

test('fetch never calls the API for blocked routes and hides upstream failures', async () => {
  let calls = 0
  const env = { API: { async fetch() { calls += 1; throw new Error('internal detail') } } }
  const notFound = await worker.fetch(new Request(`${ORIGIN}/api/retrieval/query`, { method: 'POST' }), env)
  assert.equal(notFound.status, 404)
  assert.equal(notFound.headers.get('x-content-type-options'), 'nosniff')
  const notAllowed = await worker.fetch(new Request(`${ORIGIN}/api/chat`), env)
  assert.equal(notAllowed.status, 405)
  assert.equal(notAllowed.headers.get('allow'), 'POST')
  const outsideApi = await worker.fetch(new Request(`${ORIGIN}/missing`), env)
  assert.equal(outsideApi.status, 404)
  assert.equal(calls, 0)
  const failed = await worker.fetch(new Request(`${ORIGIN}/api/chat`, { method: 'POST', body: '{}' }), env)
  assert.equal(calls, 1)
  assert.equal(failed.status, 502)
  assert.equal(await failed.text(), '')
  const unbound = await worker.fetch(new Request(`${ORIGIN}/api/health`), {})
  assert.equal(unbound.status, 502)
})

test('_headers declares the security headers for assets and admin', async () => {
  const text = await readFile(join(webRoot, 'public/_headers'), 'utf8')
  const blocks = new Map()
  let current = null
  for (const line of text.split('\n')) {
    if (!line.trim() || line.startsWith('#')) continue
    if (!/^\s/.test(line)) { current = line.trim(); blocks.set(current, new Map()); continue }
    const index = line.indexOf(':')
    blocks.get(current).set(line.slice(0, index).trim().toLowerCase(), line.slice(index + 1).trim())
    assert.ok(line.length <= 2000, 'Cloudflare limits each _headers line to 2000 characters')
  }
  const all = blocks.get('/*')
  assert.ok(all, 'global /* rule')
  const csp = all.get('content-security-policy')
  for (const directive of [
    "default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'self'",
    "form-action 'self'", "frame-ancestors 'none'", 'https://api.iconify.design',
  ]) assert.ok(csp.includes(directive), directive)
  assert.ok(!/script-src[^;]*'unsafe-(inline|eval)'/.test(csp), 'no unsafe script sources')
  assert.equal(all.get('x-content-type-options'), 'nosniff')
  assert.equal(all.get('referrer-policy'), 'strict-origin-when-cross-origin')
  assert.match(all.get('permissions-policy'), /camera=\(\).*geolocation=\(\).*microphone=\(\)/)
  for (const path of ['/admin', '/admin/*']) {
    assert.match(blocks.get(path)?.get('x-robots-tag') ?? '', /noindex/, path)
    assert.equal(blocks.get(path)?.get('cache-control'), 'no-store', path)
  }
})

test('wrangler config serves dist/ and runs the Worker first only for /api/*', async () => {
  const raw = await readFile(join(webRoot, 'wrangler.jsonc'), 'utf8')
  const config = JSON.parse(raw.replace(/^\s*\/\/.*$/gm, ''))
  assert.equal(config.name, 'unitropico-web')
  assert.equal(config.main, 'worker/index.js')
  assert.equal(config.assets.directory, './dist')
  assert.deepEqual(config.assets.run_worker_first, ['/api/*'])
  assert.deepEqual(config.services.map(s => s.binding), ['API'])
})
