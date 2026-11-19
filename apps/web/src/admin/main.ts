import { createApp } from 'vue'
import { registerServiceWorker } from '../lib/pwa'
import ui from '@nuxt/ui/vue-plugin'
import AdminApp from './AdminApp.vue'
import '../assets/main.css'

createApp(AdminApp).use(ui).mount('#app')
registerServiceWorker('/admin/sw.js', '/admin/')
