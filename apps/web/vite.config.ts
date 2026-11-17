import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import ui from '@nuxt/ui/vite'

export default defineConfig({
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
      router: false,
      autoImport: false,
      components: false,
    }),
  ],
})
