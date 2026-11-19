import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadSrc } from './support/load.mjs'

const { AbortController, AbortSignal } = globalThis

const [{ parseChatResponse, sendChat, CHAT_ENDPOINT }, { HttpError, isAbortError, postJson }] =
  await loadSrc('services/chat', 'services/http')

const citation = {
  chunk_id: 'c1', document_id: 'd1', document_title: 'Reglamento de bienestar',
  version_id: 'v1', version_label: '2024', section: 'Capítulo 2', page_start: 13, page_end: 14,
  quote: 'Texto literal <b>escapado</b>.',
}
const answered = {
  status: 'answered', answer: 'Respuesta.\nSegunda línea.', reason: null,
  citations: [citation], referral: null,
}
const abstained = {
  status: 'abstained', answer: null, reason: 'no_evidence', citations: [],
  referral: 'Consulta con Bienestar Universitario.',
}
const unavailable = { status: 'unavailable', referral: 'Servicio no disponible; acude a Bienestar.' }
const reply = (status, body) => async () => ({ status, json: async () => body })
const request = { question: 'becas', history: [] }

test('accepts valid answered, abstained and unavailable bodies', () => {
  assert.deepEqual(parseChatResponse(200, answered), answered)
  assert.deepEqual(parseChatResponse(200, { ...answered, referral: 'Más info.' }).referral, 'Más info.')
  assert.deepEqual(parseChatResponse(200, { ...answered, citations: [{ ...citation, version_label: null, section: null, page_end: 13 }] }).status, 'answered')
  assert.deepEqual(parseChatResponse(200, abstained), abstained)
  for (const reason of ['no_evidence', 'model_abstained', 'invalid_output', 'invalid_citation', 'stale_source']) {
    assert.equal(parseChatResponse(200, { ...abstained, reason }).reason, reason)
  }
  assert.deepEqual(parseChatResponse(503, unavailable), unavailable)
  assert.deepEqual(parseChatResponse(502, unavailable), unavailable)
})

test('rejects broken answered/abstained invariants', () => {
  const broken = [
    { ...answered, answer: null }, { ...answered, answer: '  ' }, { ...answered, citations: [] },
    { ...answered, reason: 'no_evidence' }, { ...answered, referral: 3 },
    { ...abstained, answer: 'algo' }, { ...abstained, citations: [citation] },
    { ...abstained, reason: null }, { ...abstained, reason: 'otra' },
    { ...abstained, referral: null }, { ...abstained, referral: ' ' },
    { ...answered, status: 'unavailable' }, { ...answered, extra: true },
    ...[{ page_start: 0 }, { page_end: 12 }, { page_start: 1.5 }, { page_end: '14' },
      { quote: '' }, { document_title: ' ' }, { chunk_id: null }, { version_id: 1 },
      { section: 2 }, { version_label: undefined }, { extra: 'x' }]
      .map(change => ({ ...answered, citations: [{ ...citation, ...change }] })),
  ]
  for (const body of broken) assert.equal(parseChatResponse(200, body), null, JSON.stringify(body))
})

test('rejects wrong HTTP status, bad 503 bodies and garbage payloads', () => {
  for (const status of [201, 400, 422, 500]) assert.equal(parseChatResponse(status, answered), null)
  assert.equal(parseChatResponse(503, answered), null)
  assert.equal(parseChatResponse(503, { status: 'unavailable' }), null)
  assert.equal(parseChatResponse(503, { status: 'unavailable', referral: '' }), null)
  assert.equal(parseChatResponse(200, unavailable), null)
  for (const body of [null, undefined, 'texto', 42, [], {}, '<html>error</html>']) {
    assert.equal(parseChatResponse(200, body), null)
  }
})

test('sendChat POSTs the v1 request to /api/chat and returns the validated result', async () => {
  const history = [{ role: 'user', content: 'hola' }, { role: 'assistant', content: 'respuesta' }]
  const result = await sendChat({ question: 'becas', history }, {
    fetcher: async (url, init) => {
      assert.equal(url, '/api/chat')
      assert.equal(CHAT_ENDPOINT, '/api/chat')
      assert.equal(init.method, 'POST')
      assert.equal(init.headers['Content-Type'], 'application/json')
      assert.deepEqual(JSON.parse(init.body), { question: 'becas', history })
      assert.ok(init.signal instanceof AbortSignal)
      return { status: 200, json: async () => answered }
    },
  })
  assert.deepEqual(result, answered)
  assert.deepEqual(await sendChat(request, { fetcher: reply(503, unavailable) }), unavailable)
})

test('sendChat rejects invalid requests without fetching', async () => {
  const never = () => assert.fail('unexpected fetch')
  for (const bad of [{ question: '', history: [] }, { question: ' x ', history: [] },
    { question: 'a'.repeat(501), history: [] },
    { question: 'x', history: Array.from({ length: 7 }, () => ({ role: 'user', content: 'a' })) },
    { question: 'x', history: [{ role: 'system', content: 'a' }] },
    { question: 'x', history: [{ role: 'user', content: 'a'.repeat(2001) }] }]) {
    await assert.rejects(sendChat(bad, { fetcher: never }), { kind: 'invalid_request' })
  }
  // 500 code points (emoji = 2 unidades UTF-16) sí es válido.
  assert.equal((await sendChat({ question: '😀'.repeat(500), history: [] },
    { fetcher: reply(200, abstained) })).status, 'abstained')
})

test('invalid contract, network and JSON failures are sanitized in Spanish', async () => {
  const generic = 'No se pudo completar la consulta. Inténtalo de nuevo.'
  await assert.rejects(sendChat(request, { fetcher: reply(200, { status: 'answered' }) }),
    error => error instanceof HttpError && error.message === generic)
  await assert.rejects(sendChat(request, { fetcher: reply(500, { detail: 'Traceback secret' }) }),
    error => error instanceof HttpError && !error.message.includes('secret'))
  await assert.rejects(sendChat(request, { fetcher: async () => { throw new TypeError('ECONNREFUSED 127.0.0.1') } }),
    error => error.kind === 'network' && !error.message.includes('127.0.0.1')
      && error.message === 'No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.')
  await assert.rejects(sendChat(request, { fetcher: async () => ({ status: 200, json: async () => { throw new SyntaxError('<html>') } }) }),
    { kind: 'invalid_response', message: generic })
})

const hangUntilAbort = (_url, init) => new Promise((_, reject) => {
  init.signal.addEventListener('abort', () => reject(init.signal.reason))
})

test('timeout is distinct from user abort', async () => {
  await assert.rejects(postJson('/api/chat', {}, { fetcher: hangUntilAbort, timeoutMs: 10 }),
    error => error instanceof HttpError && error.kind === 'timeout'
      && error.message === 'El asistente tardó demasiado en responder. Inténtalo de nuevo.')
  const controller = new AbortController()
  const pending = postJson('/api/chat', {}, { fetcher: hangUntilAbort, signal: controller.signal, timeoutMs: 60_000 })
  controller.abort()
  await assert.rejects(pending, error => isAbortError(error) && !(error instanceof HttpError))
  const already = new AbortController()
  already.abort()
  await assert.rejects(postJson('/api/chat', {}, { signal: already.signal, fetcher: () => assert.fail('no fetch') }),
    error => isAbortError(error))
})
