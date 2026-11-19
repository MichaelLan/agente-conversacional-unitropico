import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import ui from '@nuxt/ui/vite'

export default defineConfig({
  build: {
    rollupOptions: {
      // Multipágina sin router: el chat público (/) y el panel (/admin/).
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        admin: resolve(import.meta.dirname, 'admin/index.html'),
      },
    },
  },
  // Development only: production needs a same-origin /api reverse proxy.
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        rewrite: path => path.replace(/^\/api/, ''),
      },
    },
  },
  plugins: [
    vue(),
    ui({
      colorMode: false,
      router: false,
      autoImport: false,
      components: false,
      ui: {
        // Verde institucional sobrio + neutros slate. Ajustes finos en main.css.
        colors: { primary: 'green', neutral: 'slate' },
      },
    }),
  ],
})
