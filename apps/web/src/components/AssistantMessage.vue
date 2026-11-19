<script setup lang="ts">
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import CitationList from './CitationList.vue'
import type { AssistantMessage } from '../types/chat'

defineProps<{
  message: AssistantMessage
  /** Solo el último fallo admite «Reintentar». */
  retryable: boolean
  /** Espera tras un 429: el botón queda deshabilitado. */
  retryDisabled?: boolean
}>()

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div
    v-if="message.kind === 'answer'"
    class="text-base/7 text-default"
  >
    <p class="whitespace-pre-line wrap-break-word">
      {{ message.text }}
    </p>
    <p
      v-if="message.referral"
      class="mt-3 whitespace-pre-line text-sm text-muted"
    >
      {{ message.referral }}
    </p>
    <CitationList :citations="message.citations" />
  </div>

  <UAlert
    v-else-if="message.kind === 'abstention'"
    color="neutral"
    variant="subtle"
    icon="i-lucide-search-x"
    title="No encontré información suficiente en los documentos institucionales para responder esta pregunta."
    :ui="{ description: 'whitespace-pre-line' }"
    :description="message.referral"
  />

  <UAlert
    v-else
    color="neutral"
    variant="subtle"
    :icon="message.kind === 'cancelled' ? 'i-lucide-circle-slash' : 'i-lucide-circle-alert'"
    :ui="{ description: 'whitespace-pre-line' }"
  >
    <template #title>
      <template v-if="message.kind === 'unavailable'">
        El asistente no está disponible en este momento.
      </template>
      <template v-else-if="message.kind === 'error'">
        {{ message.text }}
      </template>
      <template v-else>
        Cancelaste la consulta.
      </template>
    </template>
    <template
      v-if="message.kind === 'unavailable'"
      #description
    >
      {{ message.referral }}
    </template>
    <template
      v-if="retryable"
      #actions
    >
      <UButton
        label="Reintentar"
        icon="i-lucide-rotate-ccw"
        color="neutral"
        variant="outline"
        size="sm"
        :disabled="retryDisabled"
        @click="emit('retry')"
      />
    </template>
  </UAlert>
</template>
