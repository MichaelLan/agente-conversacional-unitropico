import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { loadSrc, srcRoot } from './support/load.mjs'

const [api, lib] = await loadSrc('admin/services/adminApi', 'admin/lib/corrections')
const TOKEN = 'secreto-de-prueba'

const correction = (over = {}) => ({
  id: 'cor_1', version_id: 'ver1', page_number: 18, find_text: 'promedio igual a 3.0',
  replace_text: 'promedio igual o superior a 3.0', reason: 'OCR', created_at: '2026-11-18T10:00:00Z',
  deleted_at: null, active: true, ...over,
})
const chunk = (over = {}) => ({
  ordinal: 0, id: 'c0', section: null, page_start: 3, page_end: 3, text: 'Hola', chunker_version: null, ...over,
})
const result = (over = {}) => ({
  correction: correction(), version_status: 'review_required', requires_review: true,
  reprocess_required: true, message: 'Reprocesa para aplicar.', ...over,
})

function fake(status, body) {
  const calls = []
  return { calls, fetcher: async (url, init) => { calls.push({ url, init }); return { status, json: async () => body } } }
}
const client = fetcher => api.createAdminApi({ getToken: () => TOKEN, fetcher })

test('chunk page validator: valid and malformed', () => {
  const page = { version_id: 'v', status: 'ready', total: 1, offset: 0, limit: 50, chunks: [chunk(), chunk({ section: 'Art. 1', chunker_version: '1' })] }
  assert.ok(api.isChunkPage(page))
  assert.ok(api.isChunkPage({ ...page, total: 0, chunks: [] }))
  assert.ok(!api.isChunkPage({ ...page, status: 'raro' }))
  assert.ok(!api.isChunkPage({ ...page, chunks: [chunk({ page_start: '3' })] }))
  assert.ok(!api.isChunkPage({ ...page, chunks: [{ ...chunk(), text: undefined }] }))
  assert.ok(!api.isChunkPage({ ...page, offset: 1.5 }))
  assert.ok(!api.isChunkPage(null))
})

test('correction validators: list and result', () => {
  assert.ok(api.isCorrectionList({ version_id: 'v', corrections: [correction(), correction({ deleted_at: 't', active: false })] }))
  assert.ok(!api.isCorrectionList({ version_id: 'v', corrections: [correction({ active: 'si' })] }))
  assert.ok(!api.isCorrectionList({ version_id: 'v' }))
  assert.ok(api.isCorrectionResult(result()))
  assert.ok(!api.isCorrectionResult(result({ message: 3 })))
  assert.ok(!api.isCorrectionResult(result({ version_status: 'x' })))
  assert.ok(!api.isCorrectionResult(result({ reprocess_required: 1 })))
  assert.ok(!api.isCorrectionResult({ ...result(), correction: { id: 'x' } }))
})

test('requests: GET chunks query, POST and DELETE corrections carry Authorization', async () => {
  const g = fake(200, { version_id: 'v1', status: 'ready', total: 0, offset: 50, limit: 50, chunks: [] })
  await client(g.fetcher).listChunks('v1', 50, 50)
  assert.equal(g.calls[0].url, '/api/admin/versions/v1/chunks?offset=50&limit=50')
  assert.equal(g.calls[0].init.method, 'GET')

  const input = { page_number: 18, find_text: 'a', replace_text: '', reason: 'r' }
  const p = fake(201, result())
  await client(p.fetcher).createCorrection('v1', input)
  assert.equal(p.calls[0].url, '/api/admin/versions/v1/corrections')
  assert.equal(p.calls[0].init.method, 'POST')
  assert.equal(p.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`)
  assert.equal(p.calls[0].init.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(p.calls[0].init.body), input)

  const d = fake(200, result({ correction: correction({ active: false, deleted_at: 't' }) }))
  await client(d.fetcher).deleteCorrection('v1', 'cor_1')
  assert.equal(d.calls[0].url, '/api/admin/versions/v1/corrections/cor_1')
  assert.equal(d.calls[0].init.method, 'DELETE')
  assert.equal(d.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`)

  const l = fake(200, { version_id: 'v1', corrections: [] })
  assert.deepEqual((await client(l.fetcher).listCorrections('v1')).corrections, [])
})

test('errors: server envelope surfaces; malformed 201 is invalid_response', async () => {
  const e = fake(409, { error: { code: 'version_published', message: 'Publicada.' } })
  await assert.rejects(client(e.fetcher).createCorrection('v1', { page_number: 1, find_text: 'a', replace_text: '', reason: 'r' }),
    err => err.code === 'version_published' && err.message === 'Publicada.')
  const bad = fake(201, { correction: 1 })
  await assert.rejects(client(bad.fetcher).createCorrection('v1', { page_number: 1, find_text: 'a', replace_text: '', reason: 'r' }),
    err => err.kind === 'invalid_response')
})

test('prefill from chunk: single page, multipage and selection', () => {
  const one = lib.prefillFromChunk(chunk({ page_start: 3, page_end: 3 }), '  texto  ')
  assert.equal(one.draft.page_number, '3')
  assert.equal(one.draft.find_text, 'texto')
  assert.deepEqual(one.pages, [3])
  const multi = lib.prefillFromChunk(chunk({ page_start: 13, page_end: 15 }))
  assert.equal(multi.draft.page_number, '')
  assert.equal(multi.draft.find_text, '')
  assert.deepEqual(multi.pages, [13, 14, 15])
  assert.equal(lib.prefillFromChunk(chunk(), 'x'.repeat(501)).draft.find_text, '')
  assert.equal(lib.formatPages(3, 3), 'p. 3')
  assert.equal(lib.formatPages(13, 14), 'pp. 13–14')
})

test('draft validation and conversion', () => {
  const ok = { page_number: '18', find_text: 'a', replace_text: '', reason: ' motivo ' }
  assert.deepEqual(lib.validateDraft(ok, 20), {})
  assert.deepEqual(lib.draftToInput(ok), { page_number: 18, find_text: 'a', replace_text: '', reason: 'motivo' })
  const bad = lib.validateDraft({ page_number: '0', find_text: '', replace_text: 'x'.repeat(501), reason: 'y'.repeat(301) })
  assert.deepEqual(Object.keys(bad).sort(), ['find_text', 'page_number', 'reason', 'replace_text'])
  assert.ok(lib.validateDraft({ ...ok, page_number: '21' }, 20).page_number)
  assert.ok(lib.validateDraft({ ...ok, page_number: '1.5' }).page_number)
  assert.ok(lib.validateDraft({ ...ok, find_text: 'x'.repeat(501) }).find_text)
  assert.ok(lib.validateDraft({ ...ok, reason: '  ' }).reason)
})

test('correction_not_applicable detail parsing is defensive', () => {
  const detail = 'step=chunk correction=cor_abc page=18 matches=2'
  assert.deepEqual(lib.parseCorrectionDetail(detail), { correction: 'cor_abc', page: 18, matches: 2 })
  assert.equal(lib.parseCorrectionDetail(null), null)
  assert.equal(lib.parseCorrectionDetail('basura'), null)
  assert.equal(lib.parseCorrectionDetail('correction=x page=a matches=1'), null)
  assert.equal(lib.explainCorrectionError('correction_not_applicable', detail),
    'La corrección cor_abc no coincide exactamente una vez en la página 18 (coincidencias: 2). Edítala o bórrala y reprocesa.')
  assert.match(lib.explainCorrectionError('correction_not_applicable', 'raro'), /Edítala o bórrala y reprocesa/)
  assert.equal(lib.explainCorrectionError('otro', detail), null)
})

test('event labels and details for the correct step', () => {
  assert.equal(lib.stepLabel('correct'), 'Corrección')
  assert.equal(lib.stepLabel('ocr'), 'ocr')
  assert.equal(lib.eventDetailText({ step: 'correct', error_code: null, detail: 'correction_applied id=cor_1 page=18' }),
    'Corrección aplicada (id cor_1, página 18)')
  assert.equal(lib.eventDetailText({ step: 'correct', error_code: null, detail: 'otra cosa' }), 'otra cosa')
  assert.equal(lib.eventDetailText({ step: 'ocr', error_code: null, detail: null }), null)
  assert.match(lib.eventDetailText({ step: 'chunk', error_code: 'correction_not_applicable', detail: 'step=chunk correction=c page=2 matches=0' }), /página 2 \(coincidencias: 0\)/)
})

test('permissions by status: published is not editable', () => {
  for (const s of ['uploaded', 'processing', 'extracted', 'review_required', 'ready', 'unpublished', 'failed']) {
    assert.equal(lib.canEditCorrections(s), true, s)
  }
  assert.equal(lib.canEditCorrections('published'), false)
  assert.match(lib.PUBLISHED_BLOCK_MESSAGE, /Despublícala/)
})

test('local filter ignores case and accents; sorting puts active first', () => {
  const list = [chunk({ text: 'Resolución 137' }), chunk({ id: 'c1', section: 'Promedio', text: 'otro' })]
  assert.equal(lib.filterChunks(list, 'resolucion').length, 1)
  assert.equal(lib.filterChunks(list, 'PROMEDIO')[0].id, 'c1')
  assert.equal(lib.filterChunks(list, '  ').length, 2)
  assert.deepEqual(lib.sortCorrections([correction({ id: 'a', active: false }), correction({ id: 'b' })]).map(c => c.id), ['b', 'a'])
})

test('review components never use v-html', async () => {
  for (const name of ['ChunksList', 'CorrectionsPanel', 'VersionReviewPanel', 'VersionDetailSlideover']) {
    const source = await readFile(join(srcRoot, 'admin/components', `${name}.vue`), 'utf8')
    assert.doesNotMatch(source, /v-html|innerHTML/, name)
  }
  assert.match(await readFile(join(srcRoot, 'admin/components/ChunksList.vue'), 'utf8'), /whitespace-pre-line/)
})

test('prefill: single-page chunk preselects the page; multipage forces an explicit choice', () => {
  const single = lib.prefillFromChunk(chunk({ page_start: 5, page_end: 5 }), 'texto')
  assert.equal(single.draft.page_number, '5')
  const multi = lib.prefillFromChunk(chunk({ page_start: 13, page_end: 14 }), 'texto')
  assert.equal(multi.draft.page_number, '')
  assert.deepEqual(multi.pages, [13, 14])
  const draft = { ...multi.draft, find_text: 'a', reason: 'r' }
  assert.equal(lib.validateDraft(draft, 20, true).page_number, 'Elige la página exacta donde aparece el texto.')
  assert.equal(lib.validateDraft({ ...draft, page_number: '14' }, 20, true).page_number, undefined)
  assert.equal(lib.PAGE_CHOICE_HELP, 'Elige la página exacta donde aparece el texto')
})

test('selectionWithin only returns text selected inside the chunk element', () => {
  const inside = {}, outside = {}
  const container = { contains: node => node === inside }
  const sel = (anchorNode, focusNode, text = 'sel') => ({ anchorNode, focusNode, toString: () => text })
  assert.equal(lib.selectionWithin(container, sel(inside, inside)), 'sel')
  assert.equal(lib.selectionWithin(container, sel(inside, outside)), '')
  assert.equal(lib.selectionWithin(container, sel(outside, inside)), '')
  assert.equal(lib.selectionWithin(container, sel(null, null)), '')
  assert.equal(lib.selectionWithin(undefined, sel(inside, inside)), '')
  assert.equal(lib.selectionWithin(container, null), '')
})

test('corrections_dirty is an optional boolean in version validators and blocks publish', async () => {
  const [v] = await loadSrc('admin/lib/versions')
  const base = { id: 'v', document_id: 'd', version_label: null, status: 'ready', requires_review: false,
    error_code: null, processing_attempts: 1, source_sha256: 'x', source_bytes: 1, page_count: 1, issued_at: null,
    effective_from: null, effective_until: null, supersedes_version_id: null, created_at: 'a', updated_at: 'b', published_at: null }
  assert.equal(api.isVersionSummary(base), true)
  assert.equal(api.isVersionSummary({ ...base, corrections_dirty: true }), true)
  assert.equal(api.isVersionSummary({ ...base, corrections_dirty: false }), true)
  assert.equal(api.isVersionSummary({ ...base, corrections_dirty: 'yes' }), false)
  assert.equal(v.actionBlockReason({ corrections_dirty: true }, 'publish'), v.CORRECTIONS_PENDING_NOTICE)
  assert.equal(v.actionBlockReason({ corrections_dirty: false }, 'publish'), null)
  assert.equal(v.actionBlockReason({}, 'publish'), null)
  assert.equal(v.actionBlockReason({ corrections_dirty: true }, 'reprocess'), null)
  assert.equal(v.CORRECTIONS_PENDING_NOTICE, 'Correcciones pendientes: reprocesa antes de revisar o publicar')
})

test('409 corrections_pending surfaces the server message', async () => {
  const fetcher = async () => ({ status: 409, json: async () => ({ error: { code: 'corrections_pending', message: 'Hay correcciones sin aplicar.' } }) })
  const client = api.createAdminApi({ getToken: () => TOKEN, fetcher })
  await assert.rejects(client.publish('ver1'), e => e.code === 'corrections_pending' && e.status === 409 && e.message === 'Hay correcciones sin aplicar.')
})
