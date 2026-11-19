import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadSrc } from './support/load.mjs'
import { Blob } from 'node:buffer'
import { URL } from 'node:url'

const [api, types] = await loadSrc('admin/services/adminApi', 'admin/types')
const { createAdminApi, AdminApiError, isDocumentList, isVersionDetail, isUploadResult, uploadUrl } = api

const TOKEN = 'secreto-de-prueba'
const document = { id: 'doc1', title: 'Reglamento', document_type: 'reglamento', created_at: '2026-01-01T00:00:00Z' }
const version = (over = {}) => ({
  id: 'ver1', document_id: 'doc1', version_label: '2026-1', status: 'ready', requires_review: false,
  error_code: null, processing_attempts: 1, source_sha256: 'a'.repeat(64), source_bytes: 10, page_count: 3,
  issued_at: null, effective_from: null, effective_until: null, supersedes_version_id: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', published_at: null, ...over,
})
const view = (over) => ({ document, version: version(over) })

function fake(status, body) {
  const calls = []
  const fetcher = async (url, init) => {
    calls.push({ url, init })
    return { status, json: async () => (body instanceof Error ? Promise.reject(body) : body) }
  }
  return { calls, fetcher }
}
const client = (fetcher, token = TOKEN) => createAdminApi({ getToken: () => token, fetcher })

test('document list validation: valid and malformed shapes', () => {
  assert.ok(isDocumentList({ documents: [{ ...document, versions: [version()] }] }))
  assert.ok(isDocumentList({ documents: [] }))
  assert.ok(!isDocumentList({ documents: [{ ...document, versions: [version({ status: 'raro' })] }] }))
  assert.ok(!isDocumentList({ documents: [{ ...document, versions: [version({ processing_attempts: '1' })] }] }))
  assert.ok(!isDocumentList({ documents: [{ ...document }] }))
  assert.ok(!isDocumentList(null))
  assert.ok(!isDocumentList({ documents: 'x' }))
})

test('detail and upload result validation', () => {
  const detail = { ...view(), error_detail: null, processing_events: [
    { id: 1, step: 'ocr', status: 'ok', error_code: null, detail: null, created_at: 't' }], workflow: null }
  assert.ok(isVersionDetail(detail))
  assert.ok(isVersionDetail({ ...detail, workflow: { instance_id: 'ver_ver1', status: 'running', error_name: null, error_message: null } }))
  assert.ok(!isVersionDetail({ ...detail, processing_events: [{ id: 'x' }] }))
  assert.ok(!isVersionDetail({ ...detail, workflow: { status: 'running' } }))
  assert.ok(isUploadResult({ ...view(), created: true, ingestion: { instance_id: 'i', outcome: 'created', status: null } }))
  assert.ok(isUploadResult({ ...view(), created: false, ingestion: null }))
  assert.ok(!isUploadResult({ ...view(), created: true, ingestion: { instance_id: 'i', outcome: 'otro' } }))
  assert.ok(!isUploadResult({ ...view() }))
})

test('upload: binary PDF body, metadata in query, Bearer header, long timeout path', async () => {
  const f = fake(201, { ...view({ status: 'uploaded' }), created: true, ingestion: { instance_id: 'i', outcome: 'created', status: 'queued' } })
  const body = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], { type: 'application/pdf' })
  const result = await client(f.fetcher).upload(
    { title: 'Título & más', document_type: 'manual', version_label: 'v 1', document_id: 'doc1' }, body)
  assert.equal(result.created, true)
  const [{ url, init }] = f.calls
  const parsed = new URL(url, 'http://x')
  assert.equal(parsed.pathname, '/api/admin/uploads')
  assert.equal(parsed.searchParams.get('title'), 'Título & más')
  assert.equal(parsed.searchParams.get('document_type'), 'manual')
  assert.equal(parsed.searchParams.get('version_label'), 'v 1')
  assert.equal(parsed.searchParams.get('document_id'), 'doc1')
  assert.equal(init.method, 'POST')
  assert.equal(init.headers['Content-Type'], 'application/pdf')
  assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`)
  assert.equal(init.body, body)
  assert.ok(!url.includes(TOKEN), 'el token nunca va en la URL')
  assert.equal(api.UPLOAD_TIMEOUT_MS, 120_000)
  assert.ok(!uploadUrl({ title: 't', document_type: 'otro', version_label: 'l' }).includes('document_id'))
})

test('upload accepts 200 (already existed)', async () => {
  const f = fake(200, { ...view(), created: false, ingestion: null })
  assert.equal((await client(f.fetcher).upload({ title: 't', document_type: 'otro', version_label: 'l' }, new Blob([]))).created, false)
})

test('PATCH sends JSON with Bearer; actions are POST to the right paths', async () => {
  const f = fake(200, view())
  const c = client(f.fetcher)
  await c.patchVersion('ver 1', { requires_review: false })
  assert.equal(f.calls[0].url, '/api/admin/versions/ver%201')
  assert.equal(f.calls[0].init.method, 'PATCH')
  assert.equal(f.calls[0].init.headers['Content-Type'], 'application/json')
  assert.equal(f.calls[0].init.body, '{"requires_review":false}')
  await c.publish('ver1')
  await c.unpublish('ver1')
  assert.deepEqual(f.calls.slice(1).map(call => [call.init.method, call.url]), [
    ['POST', '/api/admin/versions/ver1/publish'], ['POST', '/api/admin/versions/ver1/unpublish']])
  for (const call of f.calls) assert.equal(call.init.headers.Authorization, `Bearer ${TOKEN}`)
})

test('401 → unauthorized; 503 admin_disabled → disabled; 409 shows the server message', async () => {
  await assert.rejects(client(fake(401, { error: { code: 'unauthorized', message: 'Credenciales de administración inválidas.' } }).fetcher).listDocuments(),
    e => e instanceof AdminApiError && e.kind === 'unauthorized' && e.status === 401)
  await assert.rejects(client(fake(503, { error: { code: 'admin_disabled', message: 'La administración no está habilitada.' } }).fetcher).listDocuments(),
    e => e.kind === 'disabled' && e.message === 'El panel no está habilitado en este entorno.')
  await assert.rejects(client(fake(409, { error: { code: 'conflict', message: 'Despublique la versión antes de editarla.' } }).fetcher).patchVersion('v', { title: 'x' }),
    e => e.kind === 'server' && e.status === 409 && e.message === 'Despublique la versión antes de editarla.' && e.code === 'conflict')
  await assert.rejects(client(fake(503, { error: { code: 'storage_unavailable', message: 'Almacenamiento no disponible.' } }).fetcher).listDocuments(),
    e => e.kind === 'server' && e.message === 'Almacenamiento no disponible.')
})

test('malformed success or error bodies yield sanitized errors', async () => {
  await assert.rejects(client(fake(200, { documents: 'x' }).fetcher).listDocuments(), e => e.kind === 'invalid_response')
  await assert.rejects(client(fake(502, new Error('no json')).fetcher).listDocuments(), e => e.kind === 'invalid_response')
  await assert.rejects(client(fake(500, { raro: true }).fetcher).listDocuments(),
    e => e.kind === 'server' && e.message === 'La API no pudo completar la operación.')
  await assert.rejects(client(async () => { throw new Error(`boom ${TOKEN}`) }).listDocuments(),
    e => e.kind === 'network' && !e.message.includes(TOKEN))
})

test('without a token no request is made', async () => {
  const f = fake(200, { documents: [] })
  await assert.rejects(client(f.fetcher, null).listDocuments(), e => e.kind === 'unauthorized')
  assert.equal(f.calls.length, 0)
  assert.ok(types.DOCUMENT_TYPES.includes('convocatoria'))
})

test('upload request has a known-size body (fetch sends Content-Length) and no manual length header', async () => {
  const f = fake(201, { ...view({ status: 'uploaded' }), created: true, ingestion: null })
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 1, 2, 3])
  await client(f.fetcher).upload({ title: 't', document_type: 'otro', version_label: 'l' }, new Blob([bytes]))
  const [{ url, init }] = f.calls
  assert.ok(init.body instanceof Blob)
  assert.equal(init.body.size, bytes.length)
  assert.ok(!Object.keys(init.headers).some(h => h.toLowerCase() === 'content-length'))
  // Un body Blob construye una Request sin streaming (sin duplex): fetch calcula Content-Length.
  const request = new globalThis.Request(new URL(url, 'http://x'), { method: 'POST', headers: init.headers, body: init.body })
  assert.equal((await request.arrayBuffer()).byteLength, bytes.length)
})

test('411, 413 and 409 ingestion_in_progress surface the server message', async () => {
  const cases = [
    [411, 'content_length_required', 'Falta Content-Length.'],
    [413, 'payload_too_large', 'El PDF supera el límite.'],
    [409, 'ingestion_in_progress', 'La ingesta sigue en curso.'],
  ]
  for (const [status, code, message] of cases) {
    const f = fake(status, { error: { code, message } })
    const body = new Blob([])
    for (const run of [
      () => client(f.fetcher).upload({ title: 't', document_type: 'otro', version_label: 'l' }, body),
      () => client(f.fetcher).reprocess('ver1'),
    ]) {
      await assert.rejects(run, error => error instanceof AdminApiError
        && error.kind === 'server' && error.status === status && error.code === code && error.message === message)
    }
  }
})
