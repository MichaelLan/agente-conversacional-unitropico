import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'
import { listTs, srcRoot, webRoot } from './support/load.mjs'

const read = path => readFile(path, 'utf8')
const adminSfcs = ['AdminApp.vue', 'components/AdminPanel.vue', 'components/LoginPanel.vue', 'components/DocumentsTable.vue',
  'components/UploadModal.vue', 'components/ConfirmActionModal.vue', 'components/VersionDetailSlideover.vue',
  'components/MetadataForm.vue', 'components/StatusBadge.vue']

test('new admin SFCs compile', async () => {
  for (const name of adminSfcs) {
    const file = join(srcRoot, 'admin', name)
    const { descriptor, errors } = parse(await read(file), { filename: file })
    assert.deepEqual(errors, [], name)
    const script = compileScript(descriptor, { id: file, inlineTemplate: true })
    assert.ok(script.content.length > 0)
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: file, id: file,
      compilerOptions: { isTS: true } }).errors, [], name)
  }
})

test('no web storage, cookies, console or URL-embedded token anywhere in src', async () => {
  const files = [...await listTs(srcRoot)]
  for (const name of adminSfcs) files.push(join(srcRoot, 'admin', name))
  for (const file of files) {
    const source = await read(file)
    assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie|indexedDB|console\./, file)
  }
  const api = await read(join(srcRoot, 'admin/services/adminApi.ts'))
  assert.doesNotMatch(api, /token=|\?token|searchParams\.(set|append)\(\s*['"]token/)
})

test('admin entry exists, build input lists both pages, chat does not link to /admin', async () => {
  const html = await read(join(webRoot, 'admin/index.html'))
  assert.match(html, /<html lang="es">/)
  assert.match(html, /src="\/src\/admin\/main\.ts"/)
  assert.match(await read(join(webRoot, 'vite.config.ts')), /admin:[^\n]*admin\/index\.html/)
  for (const file of [join(webRoot, 'index.html'), join(srcRoot, 'App.vue')]) {
    assert.doesNotMatch(await read(file), /\/admin/, file)
  }
  for (const file of await listTs(srcRoot)) {
    if (file.includes('/admin/')) continue
    assert.doesNotMatch(await read(file), /\/admin/, file)
  }
})

test('login states the real protection is server-side; sign-out and confirmations exist', async () => {
  assert.match(await read(join(srcRoot, 'admin/components/LoginPanel.vue')), /lo decide el servidor/)
  const panel = await read(join(srcRoot, 'admin/components/AdminPanel.vue'))
  assert.match(panel, /Cerrar sesión/)
  assert.match(panel, /aria-live="polite"/)
  assert.match(panel, /<ConfirmActionModal/)
  assert.match(await read(join(srcRoot, 'admin/components/DocumentsTable.vue')), /overflow|min-w-0/)
})
