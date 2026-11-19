<script setup lang="ts">
import { shallowRef } from 'vue'
import UApp from '@nuxt/ui/components/App.vue'
import { es } from '@nuxt/ui/locale'
import AdminPanel from './components/AdminPanel.vue'
import LoginPanel from './components/LoginPanel.vue'
import { useAdminSession } from './composables/useAdminSession'

const session = useAdminSession()
const signingIn = shallowRef(false)

function login(token: string): void {
  signingIn.value = true
  // El panel valida el token al cargar el listado: 401/503 vuelven aquí con un aviso.
  session.signIn(token)
  signingIn.value = false
}
</script>

<template>
  <UApp :locale="es">
    <AdminPanel
      v-if="session.authenticated.value"
      :session="session"
    />
    <LoginPanel
      v-else
      :notice="session.notice.value"
      :busy="signingIn"
      @submit="login"
    />
  </UApp>
</template>
