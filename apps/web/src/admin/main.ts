import { createApp } from 'vue'
import ui from '@nuxt/ui/vue-plugin'
import AdminApp from './AdminApp.vue'
import '../assets/main.css'

createApp(AdminApp).use(ui).mount('#app')
