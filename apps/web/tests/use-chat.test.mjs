import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadSrc } from './support/load.mjs'

const [{ useChat }, { HttpError, RequestAbortedError }, faq] =
  await loadSrc('composables/useChat', 'services/http', 'data/faq')

const answered = text => ({ status: 'answered', answer: text, reason: null, citations: [{
  chunk_id: 'c', document_id: 'd', document_title: 'Doc', version_id: 'v', version_label: null,
  section: null, page_start: 1, page_end: 1, quote: 'q' }], referral: null })

function fakeServer(...replies) {
  const calls = []
  const request = (body, signal) => {
    calls.push(body)
    const next = replies.shift()
    if (next === 'hang') {
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new RequestAbortedError())))
    }
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next)
  }
  return { calls, request }
}

test('send → answered sends trimmed question and bounded history', async () => {
  const server = fakeServer(answered('uno'), answered('dos'))
  const chat = useChat({ request: server.request })
  assert.equal(await chat.send('  primera  '), true)
  assert.equal(chat.status.value, 'ready')
  assert.deepEqual(server.calls[0], { question: 'primera', history: [] })
  await chat.send('segunda')
  assert.deepEqual(server.calls[1].history, [
    { role: 'user', content: 'primera' }, { role: 'assistant', content: 'uno' },
  ])
  assert.deepEqual(chat.messages.value.map(m => m.role), ['user', 'assistant', 'user', 'assistant'])
})

test('send → abstained renders the referral; invalid input is ignored', async () => {
  const server = fakeServer({ status: 'abstained', answer: null, reason: 'no_evidence', citations: [], referral: 'Acude a Bienestar.' })
  const chat = useChat({ request: server.request })
  assert.equal(await chat.send('   '), false)
  assert.equal(await chat.send('x'.repeat(501)), false)
  assert.equal(server.calls.length, 0)
  await chat.send('algo')
  assert.equal(chat.messages.value.at(-1).kind, 'abstention')
  assert.equal(chat.messages.value.at(-1).referral, 'Acude a Bienestar.')
  assert.equal(chat.retryableId.value, null)
})

test('network error → error status, then retry resends the same question', async () => {
  const server = fakeServer(new HttpError('network'), answered('ok'))
  const chat = useChat({ request: server.request })
  await chat.send('becas')
  assert.equal(chat.status.value, 'error')
  const failure = chat.messages.value.at(-1)
  assert.equal(failure.kind, 'error')
  assert.equal(failure.text, 'No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.')
  assert.equal(chat.retryableId.value, failure.id)
  assert.equal(await chat.retry(), true)
  assert.deepEqual(server.calls[1], { question: 'becas', history: [] })
  assert.deepEqual(chat.messages.value.map(m => m.kind ?? m.role), ['user', 'answer'])
  assert.equal(chat.status.value, 'ready')
  assert.equal(await chat.retry(), false)
})

test('429 rate limited shows the server message and allows retry', async () => {
  const server = fakeServer(new HttpError('rate_limited', 'Espera un momento.'), answered('ok'))
  const chat = useChat({ request: server.request })
  await chat.send('becas')
  assert.equal(chat.status.value, 'error')
  const failure = chat.messages.value.at(-1)
  assert.deepEqual([failure.kind, failure.text], ['error', 'Espera un momento.'])
  assert.equal(chat.retryableId.value, failure.id)
  assert.equal(await chat.retry(), true)
  assert.deepEqual(chat.messages.value.map(m => m.kind ?? m.role), ['user', 'answer'])
})

test('503 unavailable is retryable and unknown errors are sanitized', async () => {
  const server = fakeServer({ status: 'unavailable', referral: 'Servicio caído.' }, new Error('stack trace secreto'))
  const chat = useChat({ request: server.request })
  await chat.send('q')
  assert.equal(chat.status.value, 'error')
  assert.equal(chat.messages.value.at(-1).referral, 'Servicio caído.')
  await chat.retry()
  assert.equal(chat.messages.value.at(-1).text, 'No se pudo completar la consulta. Inténtalo de nuevo.')
})

test('only one request in flight; abort cancels and allows retry; reset clears', async () => {
  const server = fakeServer('hang', answered('tarde'), 'hang')
  const chat = useChat({ request: server.request })
  const pending = chat.send('primera')
  assert.equal(chat.status.value, 'submitted')
  assert.equal(await chat.send('otra'), false)
  chat.abort()
  await pending
  assert.equal(chat.status.value, 'ready')
  assert.equal(chat.messages.value.at(-1).kind, 'cancelled')
  await chat.retry()
  assert.equal(chat.messages.value.at(-1).text, 'tarde')

  const inFlight = chat.send('se descarta')
  chat.reset()
  await inFlight
  assert.deepEqual(chat.messages.value, [])
  assert.equal(chat.status.value, 'ready')
})

test('FAQ questions go through the same send as the manual prompt', async () => {
  assert.equal(faq.FAQ_IS_PLACEHOLDER, true)
  const question = faq.FAQ_CATEGORIES[0].questions[0]
  assert.ok(faq.FAQ_SUGGESTIONS.length >= 3 && faq.FAQ_SUGGESTIONS.length <= 4)
  for (const category of faq.FAQ_CATEGORIES) {
    for (const q of category.questions) assert.match(q, /^¿.+\?$/, 'solo preguntas, nunca respuestas')
  }
  const server = fakeServer(answered('r'))
  const chat = useChat({ request: server.request })
  await chat.send(question)
  assert.deepEqual(server.calls, [{ question, history: [] }])
})

function fakeClock() {
  let now = 0
  let cb = null
  return {
    now: () => now,
    setInterval: fn => { cb = fn; return 1 },
    clearInterval: () => { cb = null },
    advance(ms) { now += ms; cb?.() },
    get active() { return cb !== null },
  }
}

test('429 with Retry-After blocks send and retry until the countdown ends', async () => {
  const clock = fakeClock()
  const server = fakeServer(new HttpError('rate_limited', 'Espera.', 3), answered('ok'))
  const chat = useChat({ request: server.request, clock })
  await chat.send('hola')
  assert.equal(chat.cooldown.value, 3)
  assert.equal(chat.cooldownAnnouncement.value, 'Puedes intentarlo de nuevo en 3 s.')
  assert.equal(await chat.retry(), false)
  assert.equal(await chat.send('otra'), false)
  assert.equal(server.calls.length, 1)
  clock.advance(1000)
  assert.equal(chat.cooldown.value, 2)
  assert.equal(chat.cooldownAnnouncement.value, 'Puedes intentarlo de nuevo en 3 s.')
  clock.advance(2000)
  assert.equal(chat.cooldown.value, 0)
  assert.equal(chat.cooldownAnnouncement.value, 'Ya puedes intentarlo de nuevo.')
  assert.equal(clock.active, false)
  assert.equal(await chat.retry(), true)
  assert.equal(chat.status.value, 'ready')
})
