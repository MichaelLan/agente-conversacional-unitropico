import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import ts from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

export default ts.config(
  { ignores: ['dist/**', 'node_modules/**', '.wrangler/**'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: { parser: ts.parser },
    },
    // TypeScript already reports undefined identifiers in SFCs.
    rules: { 'no-undef': 'off' },
  },
  {
    // Worker de Cloudflare (JS plano) y su test: globals Web estándar (workerd y Node).
    files: ['worker/**/*.js', 'tests/worker-*.test.mjs'],
    languageOptions: {
      globals: { Request: 'readonly', Response: 'readonly', URL: 'readonly', Headers: 'readonly' },
    },
  },
)
