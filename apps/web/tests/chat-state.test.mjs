import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadSrc } from './support/load.mjs'

const [state, { formatPages, citationDetails }] = await loadSrc('lib/chat-state', 'lib/format')
const {
  buildHistory, normalizeQuestion, countChars, initialChatState, startTurn, resolveTurn,
  failTurn, cancelTurn, startRetry, retryableMessageId, resultToMessage, announcement,
  ABSTENTION_HISTORY_TEXT,
} = state

const user = (id, text) => ({ id, role: 'user', text })
const answer = (id, text) => ({ id, role: 'assistant', kind: 'answer', text, citations: [], referral: null })

test('normalizeQuestion trims and counts code points (1..500)', () => {
  assert.equal(normalizeQuestion('  hola  '), 'hola')
  assert.equal(normalizeQuestion(' \n '), null)
  assert.equal(normalizeQuestion('a'.repeat(500)), 'a'.repeat(500))
  assert.equal(normalizeQuestion('a'.repeat(501)), null)
  assert.equal(countChars('😀😀'), 2)
  assert.equal(normalizeQuestion('😀'.repeat(500)), '😀'.repeat(500))
})

test('history keeps only completed turns, last 6 messages, trimmed to 2000', () => {
  const messages = []
  for (let i = 1; i <= 5; i += 1) messages.push(user(`u${i}`, `pregunta ${i}`), answer(`a${i}`, `respuesta ${i}`))
  const history = buildHistory(messages)
  assert.equal(history.length, 6)
  assert.deepEqual(history[0], { role: 'user', content: 'pregunta 3' })
  assert.deepEqual(history.at(-1), { role: 'assistant', content: 'respuesta 5' })

  const long = buildHistory([user('u', `  ${'é'.repeat(2500)}  `), answer('a', '😀'.repeat(2100))])
  assert.equal(countChars(long[0].content), 2000)
  assert.equal(countChars(long[1].content), 2000)

  const mixed = buildHistory([
    user('u1', 'falló'), { id: 'e', role: 'assistant', kind: 'error', text: 'x' },
    user('u2', 'cancelada'), { id: 'c', role: 'assistant', kind: 'cancelled' },
    user('u3', 'sin datos'), { id: 'b', role: 'assistant', kind: 'abstention', referral: 'derivación' },
    user('u4', 'caída'), { id: 'n', role: 'assistant', kind: 'unavailable', referral: 'r' },
    user('u5', 'en curso'),
  ])
  assert.deepEqual(mixed, [
    { role: 'user', content: 'sin datos' },
    { role: 'assistant', content: ABSTENTION_HISTORY_TEXT },
  ])
})

test('send → answered transition', () => {
  let s = startTurn(initialChatState(), 'u1', 'becas')
  assert.equal(s.status, 'submitted')
  assert.equal(s.pending, 'becas')
  assert.equal(announcement(s), 'Consultando los documentos institucionales…')
  s = resolveTurn(s, 'a1', { status: 'answered', answer: 'Sí.', reason: null, citations: [], referral: null })
  assert.equal(s.status, 'ready')
  assert.equal(s.failed, null)
  assert.equal(s.messages.at(-1).kind, 'answer')
  assert.equal(retryableMessageId(s), null)
  assert.equal(announcement(s), 'El asistente respondió.')
})

test('send → abstained is a normal (non-retryable) reply carrying the referral', () => {
  let s = startTurn(initialChatState(), 'u1', 'algo')
  s = resolveTurn(s, 'a1', { status: 'abstained', answer: null, reason: 'no_evidence', citations: [], referral: 'Acude a Bienestar.' })
  assert.equal(s.status, 'ready')
  assert.deepEqual(s.messages.at(-1), { id: 'a1', role: 'assistant', kind: 'abstention', referral: 'Acude a Bienestar.' })
  assert.equal(retryableMessageId(s), null)
})

test('unavailable / error / cancel allow retry of the same question without duplicating it', () => {
  for (const fail of [
    s => resolveTurn(s, 'x', { status: 'unavailable', referral: 'r' }),
    s => failTurn(s, 'x', 'No se pudo conectar'),
    s => cancelTurn(s, 'x'),
  ]) {
    let s = fail(startTurn(initialChatState(), 'u1', 'becas'))
    assert.equal(s.failed, 'becas')
    assert.equal(retryableMessageId(s), 'x')
    s = startRetry(s)
    assert.equal(s.status, 'submitted')
    assert.equal(s.pending, 'becas')
    assert.deepEqual(s.messages, [user('u1', 'becas')])
    assert.equal(startRetry(s), null, 'no retry while submitted')
  }
  const failed = failTurn(startTurn(initialChatState(), 'u1', 'q'), 'e', 'x')
  assert.equal(failed.status, 'error')
  assert.equal(cancelTurn(startTurn(initialChatState(), 'u1', 'q'), 'c').status, 'ready')
  assert.equal(startRetry(initialChatState()), null)
})

test('result mapping and citation formatting never invent data', () => {
  const citation = { chunk_id: 'c', document_id: 'd', document_title: 'Doc', version_id: 'v', version_label: null, section: null, page_start: 3, page_end: 3, quote: 'q' }
  assert.deepEqual(resultToMessage('a', { status: 'unavailable', referral: 'r' }), { id: 'a', role: 'assistant', kind: 'unavailable', referral: 'r' })
  assert.equal(formatPages(3, 3), 'p. 3')
  assert.equal(formatPages(13, 14), 'pp. 13–14')
  assert.deepEqual(citationDetails(citation), ['p. 3'])
  assert.deepEqual(citationDetails({ ...citation, version_label: ' ', section: '' }), ['p. 3'])
  assert.deepEqual(citationDetails({ ...citation, version_label: '2024', section: 'Art. 5', page_end: 4 }),
    ['Versión 2024', 'Art. 5', 'pp. 3–4'])
})
