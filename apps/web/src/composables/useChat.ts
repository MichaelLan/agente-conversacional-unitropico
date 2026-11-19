import { computed, getCurrentScope, onScopeDispose, shallowRef } from 'vue'
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
import {
  COOLDOWN_DONE_ANNOUNCEMENT,
  cooldownLabel,
  secondsLeft,
  systemClock,
  type Clock,
} from '../lib/cooldown'
import { sendChat } from '../services/chat'
import { HTTP_MESSAGES, HttpError, isAbortError } from '../services/http'

export type ChatRequester = (request: ChatRequest, signal: AbortSignal) => Promise<ChatResult>

export interface UseChatOptions {
  request?: ChatRequester
  createId?: () => string
  /** Reloj inyectable para la espera tras un 429. */
  clock?: Clock
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
export function useChat(
  { request = defaultRequester, createId = counterIds(), clock = systemClock }: UseChatOptions = {},
) {
  const state = shallowRef<ChatState>(initialChatState())
  let controller: AbortController | null = null
  /** Segundos de espera restantes tras un 429 (0 = sin espera). */
  const cooldown = shallowRef(0)
  /** Texto para una región aria-live: solo al iniciar y al terminar la espera. */
  const cooldownAnnouncement = shallowRef('')
  let cooldownUntil = 0
  let cooldownTimer: unknown = null

  function stopCooldownTimer(): void {
    if (cooldownTimer !== null) clock.clearInterval(cooldownTimer)
    cooldownTimer = null
  }

  function tickCooldown(): void {
    const left = secondsLeft(cooldownUntil, clock.now())
    cooldown.value = left
    if (left === 0) {
      stopCooldownTimer()
      cooldownAnnouncement.value = COOLDOWN_DONE_ANNOUNCEMENT
    }
  }

  function startCooldown(seconds: number): void {
    stopCooldownTimer()
    cooldownUntil = clock.now() + seconds * 1000
    cooldown.value = secondsLeft(cooldownUntil, clock.now())
    if (cooldown.value === 0) return
    cooldownAnnouncement.value = `${cooldownLabel(cooldown.value)}.`
    cooldownTimer = clock.setInterval(tickCooldown, 1000)
  }

  if (getCurrentScope()) onScopeDispose(stopCooldownTimer)

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
      if (error instanceof HttpError && error.kind === 'rate_limited' && error.retryAfterSeconds !== null) {
        startCooldown(error.retryAfterSeconds)
      }
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
    if (question === null || state.value.status === 'submitted' || cooldown.value > 0) return Promise.resolve(false)
    return run(startTurn(state.value, createId(), question)).then(() => true)
  }

  function retry(): Promise<boolean> {
    if (cooldown.value > 0) return Promise.resolve(false)
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
    cooldown: computed(() => cooldown.value),
    cooldownAnnouncement: computed(() => cooldownAnnouncement.value),
    retryableId: computed(() => retryableMessageId(state.value)),
    announcement: computed(() => announcement(state.value)),
    send,
    retry,
    abort,
    reset,
  }
}

export type UseChat = ReturnType<typeof useChat>
