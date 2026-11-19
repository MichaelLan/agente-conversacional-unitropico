import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { loadSrc, srcRoot } from './support/load.mjs'

const [api, lib, comp, corr] = await loadSrc('admin/services/adminApi', 'admin/lib/pages',
  'admin/composables/usePageReview', 'admin/lib/corrections')
const TOKEN = 'secreto-de-prueba'

const page = (over = {}) => ({
  version_id: 'ver1', page_number: 2, page_count: 5, text: 'Texto\ncon salto', source: 'ocr',
  override_id: null, corrections_applied: ['cor_1'], chunker_version: '3', ...over,
})
const override = (over = {}) => ({
  id: 'pov_1', version_id: 'ver1', page_number: 2, text: 'nuevo', reason: 'Tabla imagen',
  created_at: '2026-11-19T10:00:00Z', deleted_at: null, active: true, ...over,
})
const result = (over = {}) => ({
  override: override(), version_status: 'review_required', requires_review: true,
  corrections_dirty: true, reprocess_required: true, message: 'Reprocesa para aplicar.', ...over,
})
function fake(status, body) {
  const calls = []
  return { calls, fetcher: async (url, init) => { calls.push({ url, init }); return { status, json: async () => body } } }
}
const client = fetcher => api.createAdminApi({ getToken: () => TOKEN, fetcher })

test('page validators: valid and malformed', () => {
  assert.ok(api.isPageText(page()))
  assert.ok(api.isPageText(page({ source: 'override', override_id: 'pov_1' })))
  assert.ok(!api.isPageText(page({ source: 'override' })))
  assert.ok(!api.isPageText(page({ override_id: 'pov_1' })))
  assert.ok(!api.isPageText(page({ source: 'otro' })))
  assert.ok(!api.isPageText(page({ page_number: 6 })))
  assert.ok(!api.isPageText(page({ corrections_applied: [1] })))
  assert.ok(!api.isPageText(page({ text: undefined })))
  assert.ok(api.isPageOverrideList({ version_id: 'v', overrides: [override(), override({ active: false, deleted_at: 't' })] }))
  assert.ok(!api.isPageOverrideList({ version_id: 'v', overrides: [override({ active: 'si' })] }))
  assert.ok(api.isPageOverrideResult(result()))
  assert.ok(!api.isPageOverrideResult(result({ corrections_dirty: 'x' })))
  assert.ok(!api.isPageOverrideResult(result({ version_status: 'raro' })))
  assert.ok(!api.isPageOverrideResult(null))
})

test('requests: GET page, POST and DELETE overrides carry Authorization', async () => {
  const g = fake(200, page())
  assert.equal((await client(g.fetcher).getPage('ver 1', 2)).page_number, 2)
  assert.equal(g.calls[0].url, '/api/admin/versions/ver%201/pages?page=2')
  assert.equal(g.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`)

  const l = fake(200, { version_id: 'v', overrides: [] })
  assert.deepEqual((await client(l.fetcher).listPageOverrides('v')).overrides, [])
  assert.equal(l.calls[0].url, '/api/admin/versions/v/page-overrides')

  const p = fake(201, result())
  const input = { page_number: 2, text: 'nuevo', reason: 'Tabla imagen' }
  await client(p.fetcher).createPageOverride('ver1', input)
  assert.equal(p.calls[0].url, '/api/admin/versions/ver1/page-overrides')
  assert.equal(p.calls[0].init.method, 'POST')
  assert.equal(p.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`)
  assert.equal(p.calls[0].init.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(p.calls[0].init.body), input)

  const d = fake(200, result({ override: override({ active: false, deleted_at: 't' }) }))
  assert.equal((await client(d.fetcher).deletePageOverride('ver1', 'pov/1')).override.active, false)
  assert.equal(d.calls[0].url, '/api/admin/versions/ver1/page-overrides/pov%2F1')
  assert.equal(d.calls[0].init.method, 'DELETE')
  assert.equal(d.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`)

  await assert.rejects(client(fake(201, { override: 1 }).fetcher).createPageOverride('v', input), { kind: 'invalid_response' })
  await assert.rejects(client(fake(200, result()).fetcher).createPageOverride('v', input), { status: 200 })
})

test('server error codes map to Spanish messages', async () => {
  const body = { error: { code: 'override_exists', message: 'raw' } }
  await assert.rejects(client(fake(409, body).fetcher).createPageOverride('v', { page_number: 1, text: 'a', reason: 'b' }),
    { code: 'override_exists', status: 409 })
  assert.match(lib.explainOverrideError('override_exists'), /Quítala primero/)
  assert.match(lib.explainOverrideError('version_published'), /Despublícala/)
  assert.equal(lib.explainOverrideError('otro'), null)
  assert.equal(lib.explainOverrideError(null), null)
})

test('page navigation logic', () => {
  assert.deepEqual(lib.pageNeighbours(1, 5), { prev: null, next: 2 })
  assert.deepEqual(lib.pageNeighbours(5, 5), { prev: 4, next: null })
  assert.deepEqual(lib.pageNeighbours(1, 1), { prev: null, next: null })
  assert.equal(lib.clampPage(0, 5), 1)
  assert.equal(lib.clampPage(9, 5), 5)
  assert.equal(lib.clampPage(3.7, 5), 3)
  assert.equal(lib.parsePageInput(' 3 ', 5), 3)
  for (const bad of ['', '0', '6', '-1', '1.5', 'a', '1e1']) assert.equal(lib.parsePageInput(bad, 5), null, bad)
})

test('override form validation: limits, reason, control characters', () => {
  const ok = { text: 'Texto', reason: 'Motivo' }
  assert.deepEqual(lib.validateOverride(ok, 2, 5), {})
  assert.ok(lib.validateOverride({ ...ok, text: '   ' }, 2, 5).text)
  assert.deepEqual(lib.validateOverride({ ...ok, text: 'a'.repeat(20000) }, 2, 5), {})
  assert.ok(lib.validateOverride({ ...ok, text: 'a'.repeat(20001) }, 2, 5).text)
  assert.ok(lib.validateOverride({ ...ok, reason: ' ' }, 2, 5).reason)
  assert.deepEqual(lib.validateOverride({ ...ok, reason: 'm'.repeat(300) }, 2, 5), {})
  assert.ok(lib.validateOverride({ ...ok, reason: 'm'.repeat(301) }, 2, 5).reason)
  assert.ok(lib.validateOverride(ok, 6, 5).page)
  assert.ok(lib.validateOverride(ok, 0, null).page)
  assert.ok(lib.validateOverride({ ...ok, text: 'a​b' }, 2, 5).text)
  assert.ok(lib.validateOverride({ ...ok, text: 'a\u0007b' }, 2, 5).text)
  assert.deepEqual(lib.validateOverride({ ...ok, text: 'a\tb\nc\r\nd' }, 2, 5), {})
  // puntos de código: 10000 emojis son 20000 caracteres para el servidor
  assert.equal(lib.countChars('😀'.repeat(3)), 3)
  assert.deepEqual(lib.validateOverride({ ...ok, text: '😀'.repeat(20000) }, 2, 5), {})
  assert.deepEqual(lib.overrideToInput({ text: 'a\r\nb', reason: ' m ' }, 2), { page_number: 2, text: 'a\nb', reason: 'm' })
})

test('permissions by status: published is not editable', () => {
  assert.equal(lib.canEditOverrides('published'), false)
  for (const s of ['uploaded', 'processing', 'extracted', 'review_required', 'ready', 'unpublished', 'failed']) {
    assert.equal(lib.canEditOverrides(s), true, s)
  }
  assert.equal(lib.activeOverrideFor([override({ active: false }), override({ id: 'pov_2' })], 2).id, 'pov_2')
  assert.equal(lib.activeOverrideFor([override()], 3), null)
  assert.equal(lib.SOURCE_LABELS.ocr, 'Texto OCR')
  assert.equal(lib.SOURCE_LABELS.override, 'Texto sustituido')
})

test('override_applied event is shown in Spanish', () => {
  assert.equal(corr.eventDetailText({ step: 'correct', error_code: null, detail: 'override_applied id=pov_1 page=2' }),
    'Sustitución de página aplicada (id pov_1, página 2)')
})

const describe = (e, fallback) => (e && e.message) || fallback
const review = a => comp.usePageReview({ api: a, versionId: () => 'v1', describe })

test('usePageReview: 404 page_text_unavailable becomes the unavailable state', async () => {
  const err = new api.AdminApiError('server', 'x', 404, 'page_text_unavailable')
  const r = review({ getPage: async () => { throw err } })
  await r.loadPage(1)
  assert.equal(r.unavailable.value, true)
  assert.equal(r.error.value, null)
  assert.equal(r.text.value, null)
  const other = review({ getPage: async () => { throw new api.AdminApiError('server', 'boom', 500, null) } })
  await other.loadPage(3)
  assert.equal(other.unavailable.value, false)
  assert.equal(other.error.value, 'boom')
  assert.equal(other.pageNumber.value, 3)
})

test('usePageReview: create/remove set notice and map server codes', async () => {
  let listed = 0
  const a = {
    listPageOverrides: async () => { listed += 1; return { version_id: 'v1', overrides: [override()] } },
    createPageOverride: async () => result(),
    deletePageOverride: async () => { throw new api.AdminApiError('server', 'raw', 409, 'version_published') },
  }
  const r = review(a)
  assert.equal(await r.create({ page_number: 2, text: 't', reason: 'r' }), true)
  assert.equal(r.notice.value.corrections_dirty, true)
  assert.equal(listed, 1)
  assert.equal(await r.remove('pov_1'), false)
  assert.match(r.formError.value, /Despublícala/)
})

test('page components never use v-html and keep text pre-wrapped', async () => {
  for (const name of ['PagesPanel', 'PageOverrideEditor']) {
    assert.doesNotMatch(await readFile(join(srcRoot, 'admin/components', `${name}.vue`), 'utf8'), /v-html|innerHTML/, name)
  }
  assert.match(await readFile(join(srcRoot, 'admin/components/PagesPanel.vue'), 'utf8'), /whitespace-pre-wrap/)
})
