import { computed, shallowRef } from 'vue'
import type { ChatRequest, ChatResult, ChatState } from '../types/chat'
import {
  announcement,
  buildHistory,
  cancelTurn,
  failTurn,
  initialChatState,
  normalizeQuestion,
  resolveTurn,
  retryableMessageId,
  startRetry,
  startTurn,
} from '../lib/chat-state'
import { sendChat } from '../services/chat'
import { HTTP_MESSAGES, HttpError, isAbortError } from '../services/http'

export type ChatRequester = (request: ChatRequest, signal: AbortSignal) => Promise<ChatResult>

export interface UseChatOptions {
  request?: ChatRequester
  createId?: () => string
}

const defaultRequester: ChatRequester = (request, signal) => sendChat(request, { signal })

function counterIds(): () => string {
  let next = 0
  return () => `m${(next += 1)}`
}

/**
 * Estado de conversación solo en memoria (sin persistencia). Una única
 * consulta en curso; `send` ignora nuevas preguntas mientras status=submitted.
 */
export function useChat({ request = defaultRequester, createId = counterIds() }: UseChatOptions = {}) {
  const state = shallowRef<ChatState>(initialChatState())
  let controller: AbortController | null = null

  async function run(next: ChatState): Promise<void> {
    const question = next.pending
    if (question === null) return
    const own = new AbortController()
    controller = own
    state.value = next
    // El historial excluye la pregunta en curso porque aún no tiene respuesta.
    const history = buildHistory(next.messages)
    try {
      const result = await request({ question, history }, own.signal)
      if (controller !== own) return
      state.value = resolveTurn(state.value, createId(), result)
    } catch (error) {
      if (controller !== own) return
      state.value = isAbortError(error)
        ? cancelTurn(state.value, createId())
        : failTurn(state.value, createId(),
          error instanceof HttpError ? error.message : HTTP_MESSAGES.invalid_response)
    } finally {
      if (controller === own) controller = null
    }
  }

  /** Punto de entrada único: prompt manual, sugerencias y preguntas frecuentes. */
  function send(text: string): Promise<boolean> {
    const question = normalizeQuestion(text)
    if (question === null || state.value.status === 'submitted') return Promise.resolve(false)
    return run(startTurn(state.value, createId(), question)).then(() => true)
  }

  function retry(): Promise<boolean> {
    const next = startRetry(state.value)
    if (next === null) return Promise.resolve(false)
    return run(next).then(() => true)
  }

  function abort(): void {
    controller?.abort()
  }

  /** Nueva conversación: descarta la consulta en curso y los mensajes. */
  function reset(): void {
    const current = controller
    controller = null
    current?.abort()
    state.value = initialChatState()
  }

  return {
    messages: computed(() => state.value.messages),
    status: computed(() => state.value.status),
    retryableId: computed(() => retryableMessageId(state.value)),
    announcement: computed(() => announcement(state.value)),
    send,
    retry,
    abort,
    reset,
  }
}

export type UseChat = ReturnType<typeof useChat>
