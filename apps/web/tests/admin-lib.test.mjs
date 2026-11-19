import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadSrc } from './support/load.mjs'

const [versions, forms, types] = await loadSrc('admin/lib/versions', 'admin/lib/forms', 'admin/types')
const { allowedActions, STATUS_COLORS, hasInProgress, flattenRows, isInProgress, isStaleUploaded } = versions
const { validateMetadata, validateUpload, buildPatch, metadataFormFrom, isCalendarDate, NONE, MAX_UPLOAD_BYTES } = forms

test('allowed actions follow the backend state machine', () => {
  const a = (status, requires_review) => allowedActions({ status, requires_review })
  assert.deepEqual(a('ready', false), ['publish', 'reprocess'])
  assert.deepEqual(a('ready', true), ['reprocess'])
  assert.deepEqual(a('unpublished', false), ['publish', 'reprocess'])
  assert.deepEqual(a('published', false), ['unpublish'])
  assert.deepEqual(a('failed', true), ['reprocess'])
  assert.deepEqual(a('review_required', true), ['reprocess'])
  assert.deepEqual(a('uploaded', true), ['reprocess'])
  assert.deepEqual(a('processing', true), ['reprocess'])
  assert.deepEqual(a('extracted', true), ['reprocess'])
})

test('every status has a semantic color; in-progress detection', () => {
  for (const status of types.VERSION_STATUSES) assert.ok(STATUS_COLORS[status], status)
  assert.equal(STATUS_COLORS.failed, 'error')
  assert.equal(STATUS_COLORS.published, 'success')
  assert.equal(STATUS_COLORS.review_required, 'warning')
  const rows = flattenRows([{ id: 'd', title: 't', document_type: 'otro', created_at: 'x',
    versions: [{ id: 'v1', status: 'ready' }, { id: 'v2', status: 'processing' }] }])
  assert.equal(rows.length, 2)
  assert.equal(rows[0].document.id, 'd')
  assert.ok(hasInProgress(rows))
  assert.ok(!hasInProgress([rows[0]]))
})

const base = { title: 'Reglamento', document_type: 'reglamento', version_label: '2026-1',
  issued_at: '', effective_from: '', effective_until: '', supersedes_version_id: NONE, requires_review: true }

test('metadata validation: required, dates, effective_until >= effective_from', () => {
  assert.deepEqual(validateMetadata(base), [])
  const names = form => validateMetadata({ ...base, ...form }).map(e => e.name)
  assert.deepEqual(names({ title: '  ' }), ['title'])
  assert.deepEqual(names({ version_label: '' }), ['version_label'])
  assert.deepEqual(names({ document_type: 'x' }), ['document_type'])
  assert.deepEqual(names({ issued_at: '2026-02-30' }), ['issued_at'])
  assert.deepEqual(names({ effective_from: '18/11/2026' }), ['effective_from'])
  assert.deepEqual(names({ effective_from: '2026-05-01', effective_until: '2026-04-30' }), ['effective_until'])
  assert.deepEqual(names({ effective_from: '2026-05-01', effective_until: '2026-05-01' }), [])
  assert.ok(isCalendarDate('2024-02-29') && !isCalendarDate('2025-02-29'))
})

test('upload validation: PDF, size limit, required fields', () => {
  const form = { title: 't', document_type: 'manual', version_label: 'v', document_id: NONE }
  const pdf = { name: 'a.pdf', size: 100, type: 'application/pdf' }
  assert.deepEqual(validateUpload(form, pdf), [])
  assert.deepEqual(validateUpload(form, null).map(e => e.name), ['file'])
  assert.match(validateUpload(form, { ...pdf, name: 'a.docx', type: 'application/msword' })[0].message, /PDF/)
  assert.match(validateUpload(form, { ...pdf, size: MAX_UPLOAD_BYTES + 1 })[0].message, /25 MB/)
  assert.equal(validateUpload(form, { ...pdf, size: MAX_UPLOAD_BYTES }).length, 0)
  assert.deepEqual(validateUpload({ ...form, title: '', version_label: ' ', document_type: '' }, pdf).map(e => e.name),
    ['title', 'version_label', 'document_type'])
})

test('buildPatch sends only changed fields; null clears; no changes → null', () => {
  const version = { version_label: '2026-1', issued_at: null, effective_from: null, effective_until: null,
    supersedes_version_id: null, requires_review: true }
  const original = metadataFormFrom('Reglamento', version, 'reglamento')
  assert.equal(buildPatch({ ...original }, original), null)
  assert.deepEqual(buildPatch({ ...original, requires_review: false, effective_from: '2026-01-01' }, original),
    { effective_from: '2026-01-01', requires_review: false })
  const withDates = { ...original, effective_until: '2026-12-31', supersedes_version_id: 'v0' }
  assert.deepEqual(buildPatch({ ...withDates, effective_until: '', supersedes_version_id: NONE }, withDates),
    { effective_until: null, supersedes_version_id: null })
})

test('auto-refresh window: processing/extracted, uploaded only if recent (injected clock)', () => {
  const now = Date.parse('2026-11-18T10:10:00Z')
  const v = (status, updated_at) => ({ status, updated_at })
  assert.ok(isInProgress(v('processing', '2020-01-01T00:00:00Z'), now))
  assert.ok(isInProgress(v('extracted', '2020-01-01T00:00:00Z'), now))
  assert.ok(isInProgress(v('uploaded', '2026-11-18T10:06:00Z'), now))
  assert.ok(!isInProgress(v('uploaded', '2026-11-18T10:05:00Z'), now))
  assert.ok(!isInProgress(v('uploaded', 'basura'), now))
  assert.ok(!isInProgress(v('ready', '2026-11-18T10:09:59Z'), now))
  assert.ok(isStaleUploaded(v('uploaded', '2026-11-18T10:00:00Z'), now))
  assert.ok(!isStaleUploaded(v('uploaded', '2026-11-18T10:08:00Z'), now))
  assert.ok(!isStaleUploaded(v('processing', '2020-01-01T00:00:00Z'), now))
  const rows = [{ document: {}, version: v('uploaded', '2026-11-18T09:00:00Z') }]
  assert.ok(!hasInProgress(rows, now))
})
