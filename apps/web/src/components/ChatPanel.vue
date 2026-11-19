<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, type ComponentPublicInstance } from 'vue'
import UDashboardPanel from '@nuxt/ui/components/DashboardPanel.vue'
import UDashboardNavbar from '@nuxt/ui/components/DashboardNavbar.vue'
import UChatMessages from '@nuxt/ui/components/ChatMessages.vue'
import UChatMessage from '@nuxt/ui/components/ChatMessage.vue'
import UChatPrompt from '@nuxt/ui/components/ChatPrompt.vue'
import UChatPromptSubmit from '@nuxt/ui/components/ChatPromptSubmit.vue'
import UChatShimmer from '@nuxt/ui/components/ChatShimmer.vue'
import AssistantMessage from './AssistantMessage.vue'
import ChatEmptyState from './ChatEmptyState.vue'
import type { ChatMessage, ChatStatus } from '../types/chat'
import {
  QUESTION_COUNTER_FROM,
  QUESTION_MAX_CHARS,
  countChars,
  normalizeQuestion,
} from '../lib/chat-state'
import { FAQ_SUGGESTIONS } from '../data/faq'

const props = defineProps<{
  messages: readonly ChatMessage[]
  status: ChatStatus
  retryableId: string | null
  announcement: string
}>()

const emit = defineEmits<{
  send: [question: string]
  retry: []
  abort: []
}>()

const input = ref('')
const prompt = useTemplateRef('prompt')

const length = computed(() => countChars(input.value.trim()))
const busy = computed(() => props.status === 'submitted')
const canSubmit = computed(() => !busy.value && normalizeQuestion(input.value) !== null)
const submitStatus = computed(() => (busy.value ? 'submitted' : 'ready'))
const counter = computed(() => (length.value >= QUESTION_COUNTER_FROM
  ? `${length.value}/${QUESTION_MAX_CHARS}`
  : ''))
const tooLong = computed(() => length.value > QUESTION_MAX_CHARS)

/** Forma mínima que UChatMessages usa para el scroll al enviar. */
const scrollMessages = computed(() => props.messages.map(message => ({
  id: message.id,
  role: message.role,
  parts: [{ type: 'text' as const, text: message.role === 'user' ? message.text : '' }],
})))

const USER_PROPS = {
  side: 'right',
  variant: 'soft',
  ui: { content: 'whitespace-pre-line bg-elevated/60 text-default', container: 'max-w-[85%] sm:max-w-[75%]' },
} as const
const ASSISTANT_PROPS = {
  side: 'left',
  variant: 'naked',
  ui: { body: 'w-full', container: 'w-full' },
} as const

type RegisterRef = (id: string, element: ComponentPublicInstance | null) => void

function messageRef(register: RegisterRef, id: string) {
  return (element: Element | ComponentPublicInstance | null) =>
    register(id, element === null || element instanceof Element ? null : element)
}

function focusPrompt(): void {
  void nextTick(() => prompt.value?.textareaRef?.focus())
}

function submit(): void {
  if (!canSubmit.value) return
  emit('send', input.value)
  input.value = ''
}

function retry(): void {
  emit('retry')
  focusPrompt()
}

defineExpose({ focusPrompt })
</script>

<template>
  <UDashboardPanel
    id="chat"
    :ui="{
      root: 'bg-default lg:my-2 lg:me-2 lg:min-h-0 lg:rounded-xl lg:ring lg:ring-default',
      body: 'p-0 sm:p-0',
    }"
  >
    <template #header>
      <UDashboardNavbar
        as="header"
        :ui="{ root: 'border-b-0', title: 'text-sm font-medium text-highlighted' }"
      >
        <template #title>
          Asistente de Fomento Socioeconómico
          <span class="sr-only">— Bienestar Universitario, Unitrópico</span>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <main
        id="conversacion"
        tabindex="-1"
        aria-label="Conversación"
        class="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 focus:outline-none sm:px-6"
      >
        <p
          class="sr-only"
          role="status"
          aria-live="polite"
        >
          {{ announcement }}
        </p>

        <ChatEmptyState
          v-if="messages.length === 0"
          :suggestions="FAQ_SUGGESTIONS"
          @select="emit('send', $event)"
        />

        <UChatMessages
          v-else
          :messages="scrollMessages"
          :status="status"
          :spacing-offset="120"
          class="flex-1 gap-6 px-0 py-6"
        >
          <template #default="{ registerMessageRef }">
            <UChatMessage
              v-for="message in messages"
              :id="message.id"
              :key="message.id"
              :ref="messageRef(registerMessageRef, message.id)"
              :role="message.role"
              :parts="[]"
              v-bind="message.role === 'user' ? USER_PROPS : ASSISTANT_PROPS"
            >
              <template #content>
                <AssistantMessage
                  v-if="message.role === 'assistant'"
                  :message="message"
                  :retryable="message.id === retryableId"
                  @retry="retry"
                />
                <template v-else>
                  {{ message.text }}
                </template>
              </template>
            </UChatMessage>
          </template>
          <template #indicator>
            <UChatShimmer
              text="Consultando documentos institucionales…"
              class="text-sm"
            />
          </template>
        </UChatMessages>

        <div class="sticky bottom-0 z-10 bg-default pb-3">
          <UChatPrompt
            ref="prompt"
            v-model="input"
            placeholder="Pregunta sobre becas, apoyos, requisitos…"
            aria-label="Escribe tu pregunta"
            :rows="1"
            autoresize
            :maxrows="8"
            variant="outline"
            :ui="{ root: 'rounded-xl' }"
            @submit="submit"
          >
            <template #footer>
              <span
                class="text-xs"
                :class="tooLong ? 'text-error' : 'text-muted'"
                aria-live="polite"
              >{{ counter }}</span>
              <UChatPromptSubmit
                :status="submitStatus"
                :disabled="!canSubmit"
                color="neutral"
                variant="solid"
                @stop="emit('abort')"
              />
            </template>
          </UChatPrompt>
          <p class="mt-2 text-center text-xs text-muted">
            Respuestas generadas a partir de documentos institucionales.
            Verifica la información con Bienestar Universitario.
          </p>
        </div>
      </main>
    </template>
  </UDashboardPanel>
</template>
