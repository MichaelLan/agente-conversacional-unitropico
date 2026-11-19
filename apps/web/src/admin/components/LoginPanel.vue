<script setup lang="ts">
import { ref } from 'vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
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
  <main class="flex min-h-dvh items-center justify-center bg-muted px-4 pt-[max(2rem,env(safe-area-inset-top))] pb-[max(2rem,env(safe-area-inset-bottom))]">
    <div class="w-full max-w-sm">
      <div class="mb-6 flex flex-col items-center text-center">
        <span class="mb-3 flex size-11 items-center justify-center rounded-xl bg-default ring ring-default">
          <UIcon
            name="i-lucide-message-circle"
            class="size-6 text-primary"
            aria-hidden="true"
          />
        </span>
        <h1 class="text-lg font-semibold text-highlighted">
          Administración de documentos
        </h1>
        <p class="mt-1 text-sm text-muted">
          Asistente de Fomento Socioeconómico
        </p>
      </div>

      <div class="rounded-xl bg-default p-5 ring ring-default sm:p-6">
        <div
          aria-live="polite"
          class="empty:hidden"
        >
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
            help="Solo vive en la memoria de esta pestaña; se borra al recargar."
          >
            <UInput
              v-model="state.token"
              :type="showToken ? 'text' : 'password'"
              autocomplete="off"
              spellcheck="false"
              autofocus
              size="lg"
              class="w-full"
              :ui="{ trailing: 'pe-1' }"
            >
              <template #trailing>
                <UButton
                  color="neutral"
                  variant="link"
                  :icon="showToken ? 'i-lucide-eye-off' : 'i-lucide-eye'"
                  :aria-label="showToken ? 'Ocultar token' : 'Mostrar token'"
                  :aria-pressed="showToken"
                  class="size-10 justify-center"
                  @click="showToken = !showToken"
                />
              </template>
            </UInput>
          </UFormField>
          <UButton
            type="submit"
            label="Entrar"
            color="neutral"
            size="lg"
            block
            class="min-h-11"
            :loading="busy"
          />
        </UForm>
      </div>

      <p class="mt-4 flex items-start gap-1.5 px-1 text-xs text-muted">
        <UIcon
          name="i-lucide-shield"
          class="mt-px size-3.5 shrink-0"
          aria-hidden="true"
        />
        <span>Esta pantalla no protege nada por sí sola: el acceso lo decide el servidor
          al validar el token en cada petición.</span>
      </p>
    </div>
  </main>
</template>
