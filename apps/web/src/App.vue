<script setup lang="ts">
import { ref, useTemplateRef } from 'vue'
import UApp from '@nuxt/ui/components/App.vue'
import UDashboardGroup from '@nuxt/ui/components/DashboardGroup.vue'
import { es } from '@nuxt/ui/locale'
import FaqSidebar from './components/FaqSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import { useChat } from './composables/useChat'
import { FAQ_CATEGORIES } from './data/faq'

const { messages, status, retryableId, announcement, send, retry, abort, reset } = useChat()

const sidebarOpen = ref(false)
const activeFaq = ref<string | null>(null)
const panel = useTemplateRef('panel')

const isFaq = (question: string): boolean =>
  FAQ_CATEGORIES.some(category => category.questions.includes(question))

/** Única vía de envío: prompt, sugerencias del estado vacío y preguntas frecuentes. */
function ask(question: string): void {
  sidebarOpen.value = false
  activeFaq.value = isFaq(question) ? question : null
  void send(question)
  panel.value?.focusPrompt()
}

function startOver(): void {
  reset()
  sidebarOpen.value = false
  activeFaq.value = null
  panel.value?.focusPrompt()
}
</script>

<template>
  <UApp :locale="es">
    <a
      href="#conversacion"
      class="sr-only rounded-md bg-default px-3 py-2 text-sm font-medium text-highlighted ring-2 ring-primary focus:not-sr-only focus:fixed focus:start-3 focus:top-3 focus:z-50"
    >Saltar a la conversación</a>
    <UDashboardGroup
      unit="rem"
      :persistent="false"
      class="bg-muted"
    >
      <FaqSidebar
        v-model:open="sidebarOpen"
        :active="activeFaq"
        @select="ask"
        @reset="startOver"
      />
      <ChatPanel
        ref="panel"
        :messages="messages"
        :status="status"
        :retryable-id="retryableId"
        :announcement="announcement"
        @send="ask"
        @retry="retry"
        @abort="abort"
      />
    </UDashboardGroup>
  </UApp>
</template>
