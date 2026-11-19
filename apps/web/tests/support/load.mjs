// Transpila módulos TS de src/ a ESM temporal (sin framework de tests).
// El directorio temporal vive bajo node_modules/.tmp para que `vue` resuelva.
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath, pathToFileURL, URL } from 'node:url'
import ts from 'typescript'

export const webRoot = fileURLToPath(new URL('../../', import.meta.url))
export const srcRoot = join(webRoot, 'src')

async function listTs(dir) {
  const entries = await readdir(dir, { withFileTypes: true })
  const nested = await Promise.all(entries.map(entry => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return listTs(path)
    return entry.name.endsWith('.ts') ? [path] : []
  }))
  return nested.flat()
}

/** Importa `src/<path>.ts` (y sus dependencias relativas) ya transpilado. */
export async function loadSrc(...paths) {
  const tmpRoot = join(webRoot, 'node_modules', '.tmp')
  await mkdir(tmpRoot, { recursive: true })
  const out = await mkdtemp(join(tmpRoot, 'web-test-'))
  try {
    for (const file of await listTs(srcRoot)) {
      const source = await readFile(file, 'utf8')
      const code = ts.transpileModule(source, {
        compilerOptions: {
          module: ts.ModuleKind.ESNext,
          target: ts.ScriptTarget.ES2022,
          verbatimModuleSyntax: true,
        },
      }).outputText.replace(/(from\s+['"])(\.{1,2}\/[^'"]+)(['"])/g, '$1$2.mjs$3')
      const target = join(out, relative(srcRoot, file)).replace(/\.ts$/, '.mjs')
      await mkdir(dirname(target), { recursive: true })
      await writeFile(target, code)
    }
    return await Promise.all(paths.map(path =>
      import(pathToFileURL(join(out, `${path}.mjs`)).href)))
  } finally {
    await rm(out, { recursive: true, force: true })
  }
}

export { listTs }
