import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { parse, compileTemplate, compileScript } from 'vue/compiler-sfc'
import { srcRoot, webRoot } from './support/load.mjs'

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = await Promise.all(entries.map(e => e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]))
  return files.flat()
}
const files = await walk(srcRoot)
const sfcs = files.filter(f => f.endsWith('.vue'))
const read = path => readFile(path, 'utf8')

test('every SFC compiles (script setup + template) without errors', async () => {
  assert.ok(sfcs.length >= 6)
  for (const file of sfcs) {
    const { descriptor, errors } = parse(await read(file), { filename: file })
    assert.deepEqual(errors, [], file)
    assert.equal(descriptor.scriptSetup?.lang, 'ts', `${file} usa <script setup lang="ts">`)
    const script = compileScript(descriptor, { id: file, inlineTemplate: true })
    assert.ok(script.content.length > 0)
    const compiled = compileTemplate({ source: descriptor.template.content, filename: file, id: file,
      compilerOptions: { isTS: true } })
    assert.deepEqual(compiled.errors, [], file)
  }
})

test('no v-html, innerHTML, web storage, console or `any` in src', async () => {
  for (const file of files) {
    const source = await read(file)
    assert.doesNotMatch(source, /v-html|innerHTML|localStorage|sessionStorage|console\./, file)
    assert.doesNotMatch(source, /:\s*any\b|as any\b|<any>/, file)
  }
})

test('assistant text is interpolated (escaped) with pre-line, citations shown', async () => {
  const source = await read(join(srcRoot, 'components/AssistantMessage.vue'))
  const { descriptor } = parse(source)
  const compiled = compileTemplate({ source: descriptor.template.content, filename: 'AssistantMessage.vue', id: 'a' })
  assert.match(compiled.code, /toDisplayString\(\$props\.message\.text\)|toDisplayString\(_ctx\.message\.text\)/)
  assert.match(source, /whitespace-pre-line/)
  assert.match(source, /<CitationList/)
  assert.match(source, /Reintentar/)
  const citations = await read(join(srcRoot, 'components/CitationList.vue'))
  assert.match(citations, /UCollapsible/)
})

test('FAQ sidebar and prompt share the single `ask` → `send` path', async () => {
  const app = await read(join(srcRoot, 'App.vue'))
  assert.match(app, /<FaqSidebar[\s\S]*?@select="ask"/)
  assert.match(app, /<ChatPanel[\s\S]*?@send="ask"/)
  assert.match(app, /function ask\(question: string\)[\s\S]*?send\(question\)/)
  assert.match(app, /:persistent="false"/, 'DashboardGroup no persiste en cookies/storage')
  const panel = await read(join(srcRoot, 'components/ChatPanel.vue'))
  assert.match(panel, /<ChatEmptyState[\s\S]*?@select="emit\('send', \$event\)"/)
  assert.match(panel, /aria-live="polite"/)
  assert.match(panel, /id="conversacion"/)
  const sidebar = await read(join(srcRoot, 'components/FaqSidebar.vue'))
  assert.match(sidebar, /v-if="FAQ_IS_PLACEHOLDER"[\s\S]*?Preguntas de ejemplo en revisión/)
})

test('index.html declares Spanish and a page title; skip link exists', async () => {
  const html = await read(join(webRoot, 'index.html'))
  assert.match(html, /<html lang="es">/)
  assert.match(html, /<title>[^<]+<\/title>/)
  assert.match(await read(join(srcRoot, 'App.vue')), /href="#conversacion"/)
})
