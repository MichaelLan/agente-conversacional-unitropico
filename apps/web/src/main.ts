import { createApp } from 'vue'
import { registerServiceWorker } from './lib/pwa'
import ui from '@nuxt/ui/vue-plugin'
import App from './App.vue'
import './assets/main.css'

createApp(App).use(ui).mount('#app')
registerServiceWorker('/sw.js', '/')
