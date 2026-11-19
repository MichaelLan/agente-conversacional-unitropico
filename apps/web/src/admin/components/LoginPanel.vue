<script setup lang="ts">
import { ref } from 'vue'
import UCard from '@nuxt/ui/components/Card.vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UForm from '@nuxt/ui/components/Form.vue'
import UFormField from '@nuxt/ui/components/FormField.vue'
import UInput from '@nuxt/ui/components/Input.vue'

defineProps<{ notice: string | null; busy: boolean }>()
const emit = defineEmits<{ submit: [token: string] }>()

const state = ref({ token: '' })
const showToken = ref(false)

function submit(): void {
  emit('submit', state.value.token)
  state.value.token = ''
}

const validate = (value: { token: string }) =>
  value.token.trim() === '' ? [{ name: 'token', message: 'Pega el token de administración.' }] : []
</script>

<template>
  <main class="flex min-h-dvh items-center justify-center px-4 py-8">
    <UCard class="w-full max-w-md">
      <template #header>
        <h1 class="text-lg font-semibold text-highlighted">
          Administración de documentos
        </h1>
        <p class="mt-1 text-sm text-muted">
          Asistente de Fomento Socioeconómico · Unitrópico
        </p>
      </template>
      <div aria-live="polite">
        <UAlert
          v-if="notice"
          class="mb-4"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :description="notice"
        />
      </div>
      <UForm
        :state="state"
        :validate="validate"
        class="space-y-4"
        @submit="submit"
      >
        <UFormField
          name="token"
          label="Token de administración"
          required
          help="Se mantiene solo en la memoria de esta pestaña y se pierde al recargar."
        >
          <UInput
            v-model="state.token"
            :type="showToken ? 'text' : 'password'"
            autocomplete="off"
            spellcheck="false"
            autofocus
            class="w-full"
            :ui="{ trailing: 'pe-1' }"
          >
            <template #trailing>
              <UButton
                color="neutral"
                variant="link"
                size="sm"
                :icon="showToken ? 'i-lucide-eye-off' : 'i-lucide-eye'"
                :aria-label="showToken ? 'Ocultar token' : 'Mostrar token'"
                :aria-pressed="showToken"
                @click="showToken = !showToken"
              />
            </template>
          </UInput>
        </UFormField>
        <UButton
          type="submit"
          label="Entrar"
          block
          :loading="busy"
        />
      </UForm>
      <p class="mt-4 text-xs text-muted">
        Esta pantalla no protege nada por sí sola: el acceso lo decide el servidor
        al validar el token en cada petición.
      </p>
    </UCard>
  </main>
</template>
