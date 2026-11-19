import { test } from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { Blob } from 'node:buffer'
import { setTimeout } from 'node:timers'
import { loadSrc } from './support/load.mjs'

const [{ useAdminDocuments }, { useAdminSession }, { AdminApiError }] =
  await loadSrc('admin/composables/useAdminDocuments', 'admin/composables/useAdminSession', 'admin/services/adminApi')

const document = { id: 'd', title: 'T', document_type: 'otro', created_at: 'x' }
const version = (id, status) => ({ id, document_id: 'd', version_label: 'v', status, requires_review: false,
  error_code: null, processing_attempts: 0, source_sha256: 's', source_bytes: 1, page_count: null,
  issued_at: null, effective_from: null, effective_until: null, supersedes_version_id: null,
  created_at: 'x', updated_at: 'x', published_at: null })
const list = (...statuses) => ({ documents: [{ ...document, versions: statuses.map((s, i) => version(`v${i}`, s)) }] })

function setup(api, refreshMs = 10_000) {
  const session = useAdminSession()
  session.signIn('tok')
  const scope = effectScope()
  const docs = scope.run(() => useAdminDocuments({ session, api, refreshMs }))
  return { session, docs, scope }
}

test('session keeps the token in memory only and signOut clears it', () => {
  const s = useAdminSession()
  assert.equal(s.getToken(), null)
  assert.equal(s.signIn('   '), false)
  assert.equal(s.signIn('  abc '), true)
  assert.equal(s.getToken(), 'abc')
  s.signOut('Sesión cerrada')
  assert.equal(s.getToken(), null)
  assert.equal(s.authenticated.value, false)
  assert.equal(s.notice.value, 'Sesión cerrada')
})

test('401 on load returns to login with the message; 503 disabled likewise', async () => {
  const { session, docs, scope } = setup({ listDocuments: async () => { throw new AdminApiError('unauthorized', 'Token inválido', 401) } })
  assert.equal(await docs.load(), false)
  assert.equal(session.getToken(), null)
  assert.equal(session.notice.value, 'Token inválido')
  scope.stop()
  const second = setup({ listDocuments: async () => { throw new AdminApiError('disabled', 'El panel no está habilitado en este entorno.', 503, 'admin_disabled') } })
  await second.docs.load()
  assert.equal(second.session.notice.value, 'El panel no está habilitado en este entorno.')
  second.scope.stop()
})

test('auto-refresh runs only while versions are in progress and stops on dispose', async () => {
  let calls = 0
  const queue = [list('processing'), list('processing'), list('ready'), list('ready')]
  const api = { listDocuments: async () => { calls += 1; return queue.shift() ?? list('ready') } }
  const { docs, scope } = setup(api, 15)
  await docs.load()
  assert.equal(calls, 1)
  assert.equal(docs.autoRefreshing.value, true)
  await new Promise(resolve => setTimeout(resolve, 120))
  assert.ok(calls >= 3, `refrescó (${calls})`)
  assert.equal(docs.autoRefreshing.value, false)
  const settled = calls
  await new Promise(resolve => setTimeout(resolve, 80))
  assert.equal(calls, settled, 'sin versiones en curso no refresca')
  const again = setup({ listDocuments: async () => { calls += 1; return list('processing') } }, 10)
  await again.docs.load()
  again.scope.stop()
  const stopped = calls
  await new Promise(resolve => setTimeout(resolve, 60))
  assert.equal(calls, stopped, 'detenido al desmontar')
  scope.stop()
})

test('row actions: busy per row, 409 message surfaced, list refreshed after success', async () => {
  let listCalls = 0
  const api = {
    listDocuments: async () => { listCalls += 1; return list('ready') },
    publish: async () => ({ document, version: version('v0', 'published') }),
    unpublish: async () => { throw new AdminApiError('server', 'Despublique antes de editar.', 409, 'conflict') },
  }
  const { docs, scope } = setup(api)
  await docs.load()
  const pending = docs.act('v0', 'publish')
  assert.equal(docs.busy.value.v0, 'publish')
  assert.equal((await docs.act('v0', 'unpublish')).ok, false, 'segunda acción sobre la misma fila rechazada')
  assert.deepEqual(await pending, { ok: true, message: 'Publicar: operación completada.' })
  assert.equal(docs.busy.value.v0, undefined)
  assert.equal(listCalls, 2)
  const failed = await docs.act('v0', 'unpublish')
  assert.deepEqual(failed, { ok: false, message: 'Despublique antes de editar.' })
  scope.stop()
})

test('upload outcomes: created, existing, ingestion failed', async () => {
  const mk = (created, ingestion) => async () => ({ document, version: version('v9', 'uploaded'), created, ingestion })
  const meta = { title: 't', document_type: 'otro', version_label: 'l' }
  const outcome = async upload => {
    const { docs, scope } = setup({ listDocuments: async () => list('uploaded'), upload })
    const result = await docs.upload(meta, new Blob([]))
    scope.stop()
    return result
  }
  assert.match((await outcome(mk(true, { instance_id: 'i', outcome: 'created', status: null }))).message, /comenzó/)
  assert.match((await outcome(mk(false, null))).message, /ya estaba registrado/)
  const failed = await outcome(mk(true, { instance_id: 'i', outcome: 'failed', status: null }))
  assert.equal(failed.ok, false)
  assert.match(failed.message, /Reprocesar/)
  assert.ok(failed.result)
})
