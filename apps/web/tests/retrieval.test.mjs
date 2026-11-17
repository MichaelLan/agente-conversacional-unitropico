import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, URL } from 'node:url'
const { AbortController, DOMException } = globalThis
import ts from 'typescript'
import { parse, compileTemplate } from 'vue/compiler-sfc'

const source = await readFile(new URL('../src/services/retrieval.ts', import.meta.url), 'utf8')
const scratch = await mkdtemp(join(tmpdir(), 'unitropico-web-'))
let queryRetrieval
try {
  const file = join(scratch, 'retrieval.mjs')
  await writeFile(file, ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText)
  ;({ queryRetrieval } = await import(pathToFileURL(file).href))
} finally {
  await rm(scratch, { recursive: true, force: true })
}
const evidence = {
  document_id: 'doc', version_id: 'v1', chunk_id: 'c1', source_sha256: 'a'.repeat(64),
  page: 1, section: null, text: '<script>alert(1)</script>',
}
const response = (status, body) => ({ status, json: async () => body })
const run = (status, body) => queryRetrieval('pregunta', undefined, async () => response(status, body))

test('POST same-origin, trimmed question, fixed limit and abort signal', async () => {
  const signal = new AbortController().signal
  const result = await queryRetrieval('  becas  ', signal, async (url, options) => {
    assert.equal(url, '/api/retrieval/query')
    assert.deepEqual(options, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: 'becas', limit: 5 }), signal,
    })
    return response(200, { status: 'abstained', evidence: [] })
  })
  assert.deepEqual(result, { status: 'abstained', evidence: [] })
})
test('blank/oversize rejected without fetch; trimmed 500 character boundary accepted', async () => {
  for (const value of ['', ' \n ', 'a'.repeat(501)]) {
    await assert.rejects(queryRetrieval(value, undefined, () => assert.fail('unexpected fetch')),
      { message: 'Escribe una pregunta de 1 a 500 caracteres.' })
  }
  assert.equal((await queryRetrieval(` ${'a'.repeat(500)} `, undefined,
    async () => response(200, { status: 'abstained', evidence: [] }))).status, 'abstained')
})
test('all valid HTTP/body combinations preserve source evidence', async () => {
  for (const status of [502, 503]) {
    assert.deepEqual(await run(status, { status: 'unavailable', evidence: [] }),
      { status: 'unavailable', evidence: [] })
  }
  assert.deepEqual(await run(200, { status: 'evidence_found', evidence: [evidence] }),
    { status: 'evidence_found', evidence: [evidence] })
  assert.equal((await run(200, { status: 'evidence_found', evidence: [{ ...evidence, section: 'Becas' }] })).status,
    'evidence_found')
})
test('rejects wrong HTTP, incoherent states, malformed bodies and invalid evidence', async () => {
  const invalid = [null, '<html>exception</html>', {}, { status: 'answer', evidence: [] },
    { status: 'evidence_found', evidence: [] }, { status: 'abstained', evidence: [evidence] },
    ...[{ page: 0 }, { page: 1.5 }, { page: '1' }, { source_sha256: 'x' },
      { document_id: ' ' }, { version_id: null }, { chunk_id: '' }, { text: '' },
      { section: 1 }, { section: undefined }].map(change => ({
      status: 'evidence_found', evidence: [{ ...evidence, ...change }],
    }))]
  for (const body of invalid) await assert.rejects(run(200, body), { message: 'No se pudo completar la consulta. Inténtalo de nuevo.' })
  for (const status of [201, 400, 422, 500, 503]) {
    await assert.rejects(run(status, { status: 'abstained', evidence: [] }))
  }
  await assert.rejects(run(200, { status: 'unavailable', evidence: [] }))
  await assert.rejects(run(502, { status: 'evidence_found', evidence: [evidence] }))
})
test('network/JSON failures are sanitized; abort remains identifiable', async () => {
  for (const fetcher of [async () => { throw new Error('private exception') },
    async () => ({ status: 200, json: async () => { throw new Error('<html>') } })]) {
    await assert.rejects(queryRetrieval('becas', undefined, fetcher),
      { message: 'No se pudo completar la consulta. Inténtalo de nuevo.' })
  }
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(queryRetrieval('becas', controller.signal, async () => {
    throw new DOMException('Aborted', 'AbortError')
  }), { name: 'AbortError' })
})
test('Vue compiles escaped evidence and accessible states without HTML injection', async () => {
  const source = await readFile(new URL('../src/components/DocumentQuery.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const compiled = compileTemplate({ source: descriptor.template.content, filename: 'DocumentQuery.vue', id: 'query' })
  assert.deepEqual(compiled.errors, [])
  assert.match(compiled.code, /toDisplayString\(fragment.text\)/)
  assert.doesNotMatch(source, /v-html|localStorage|console\./)
  for (const pattern of [/aria-live="polite"/, /for="pregunta"/, /maxlength="500"/,
    /onBeforeUnmount/, /if \(loading.value/, /Fragmentos recuperados; no respuesta generada/]) {
    assert.match(source, pattern)
  }
})
