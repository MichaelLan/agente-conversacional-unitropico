import type {
  AbstainReason,
  AssistantMessage,
  ChatHistoryItem,
  ChatMessage,
  ChatResult,
  ChatState,
} from '../types/chat'

/** Lógica pura de la conversación: sin Vue, red ni almacenamiento. */

export const QUESTION_MAX_CHARS = 500
export const HISTORY_MAX_MESSAGES = 6
export const HISTORY_CONTENT_MAX_CHARS = 2000
/** El contador del prompt aparece a partir de este número de caracteres. */
export const QUESTION_COUNTER_FROM = 400

/** Texto que representa una abstención en el historial enviado al backend. */
export const ABSTENTION_HISTORY_TEXT =
  'No encontré información suficiente en los documentos institucionales para responder.'

/** Texto genérico de abstención (sin evidencia, abstención del modelo o motivo desconocido). */
export const ABSTENTION_GENERIC_TEXT =
  'No encontré información suficiente en los documentos institucionales para responder esta pregunta. '
  + 'Intenta reformularla con más detalle, por ejemplo indicando el programa o la convocatoria.'

/** Hubo información relacionada, pero la respuesta no superó la verificación de citas. */
export const ABSTENTION_UNVERIFIED_TEXT =
  'Encontré información relacionada, pero no pude verificar la respuesta con citas exactas de los documentos. '
  + 'Intenta reformular la pregunta o hacerla más específica.'

/** Las fuentes citadas cambiaron durante la respuesta. */
export const ABSTENTION_STALE_TEXT =
  'Los documentos consultados se actualizaron mientras respondía. Vuelve a enviar la pregunta.'

/** Texto visible de una abstención según su motivo; `null` o desconocido → genérico. */
export function abstentionText(reason: AbstainReason | null): string {
  switch (reason) {
    case 'invalid_citation':
    case 'invalid_output':
      return ABSTENTION_UNVERIFIED_TEXT
    case 'stale_source':
      return ABSTENTION_STALE_TEXT
    default:
      return ABSTENTION_GENERIC_TEXT
  }
}

/** Anuncio breve (aria-live) de una abstención según su motivo. */
export function abstentionAnnouncement(reason: AbstainReason | null): string {
  switch (reason) {
    case 'invalid_citation':
    case 'invalid_output':
      return 'El asistente no pudo verificar la respuesta con citas.'
    case 'stale_source':
      return 'Las fuentes cambiaron; vuelve a enviar la pregunta.'
    default:
      return 'El asistente no encontró información suficiente.'
  }
}

/** Cuenta code points (no unidades UTF-16), como el backend. */
export function countChars(text: string): number {
  return Array.from(text).length
}

function truncateChars(text: string, max: number): string {
  const chars = Array.from(text)
  return chars.length > max ? chars.slice(0, max).join('') : text
}

/** Pregunta recortada si cumple 1..500 code points; si no, `null`. */
export function normalizeQuestion(text: string): string | null {
  const trimmed = text.trim()
  const length = countChars(trimmed)
  return length > 0 && length <= QUESTION_MAX_CHARS ? trimmed : null
}

function historyContent(message: AssistantMessage): string | null {
  if (message.kind === 'answer') return message.text
  if (message.kind === 'abstention') return ABSTENTION_HISTORY_TEXT
  return null
}

/**
 * Historial acotado: solo turnos completados (pregunta seguida de respuesta o
 * abstención), últimos 6 mensajes, contenido recortado a 2000 code points.
 */
export function buildHistory(messages: readonly ChatMessage[]): ChatHistoryItem[] {
  const turns: ChatHistoryItem[] = []
  for (let index = 0; index < messages.length - 1; index += 1) {
    const question = messages[index]
    const reply = messages[index + 1]
    if (question?.role !== 'user' || reply?.role !== 'assistant') continue
    const answer = historyContent(reply)
    const content = question.text.trim()
    if (answer === null || !content || !answer.trim()) continue
    turns.push(
      { role: 'user', content: truncateChars(content, HISTORY_CONTENT_MAX_CHARS) },
      { role: 'assistant', content: truncateChars(answer.trim(), HISTORY_CONTENT_MAX_CHARS) },
    )
  }
  return turns.slice(-HISTORY_MAX_MESSAGES)
}

/** Mapea una respuesta validada del contrato a un mensaje del asistente. */
export function resultToMessage(id: string, result: ChatResult): AssistantMessage {
  switch (result.status) {
    case 'answered':
      return {
        id, role: 'assistant', kind: 'answer', text: result.answer,
        citations: result.citations, referral: result.referral,
      }
    case 'abstained':
      return {
        id, role: 'assistant', kind: 'abstention', reason: result.reason, referral: result.referral,
      }
    case 'unavailable':
      return { id, role: 'assistant', kind: 'unavailable', referral: result.referral }
  }
}

export const initialChatState = (): ChatState => ({
  messages: [], status: 'ready', pending: null, failed: null,
})

const isRetryable = (message: ChatMessage | undefined): boolean =>
  message?.role === 'assistant'
  && (message.kind === 'error' || message.kind === 'unavailable' || message.kind === 'cancelled')

/** Añade la pregunta del estudiante y pasa a `submitted`. */
export function startTurn(state: ChatState, id: string, question: string): ChatState {
  return {
    messages: [...state.messages, { id, role: 'user', text: question }],
    status: 'submitted', pending: question, failed: null,
  }
}

/** Reintento: retira el aviso de fallo y reenvía la misma pregunta sin duplicarla. */
export function startRetry(state: ChatState): ChatState | null {
  if (state.status === 'submitted' || state.failed === null) return null
  const messages = isRetryable(state.messages.at(-1)) ? state.messages.slice(0, -1) : state.messages
  if (messages.at(-1)?.role !== 'user') return null
  return { messages, status: 'submitted', pending: state.failed, failed: null }
}

/** Resultado del backend: `unavailable` deja la pregunta disponible para reintentar. */
export function resolveTurn(state: ChatState, id: string, result: ChatResult): ChatState {
  const message = resultToMessage(id, result)
  const failed = message.kind === 'unavailable' ? state.pending : null
  return {
    messages: [...state.messages, message],
    status: failed === null ? 'ready' : 'error',
    pending: null, failed,
  }
}

/** Error de red, timeout o contrato inválido (mensaje ya saneado). */
export function failTurn(state: ChatState, id: string, text: string): ChatState {
  return {
    messages: [...state.messages, { id, role: 'assistant', kind: 'error', text }],
    status: 'error', pending: null, failed: state.pending,
  }
}

/** Cancelación del estudiante: no es un error, pero puede reintentarse. */
export function cancelTurn(state: ChatState, id: string): ChatState {
  return {
    messages: [...state.messages, { id, role: 'assistant', kind: 'cancelled' }],
    status: 'ready', pending: null, failed: state.pending,
  }
}

/** Id del último mensaje que admite «Reintentar», si existe. */
export function retryableMessageId(state: ChatState): string | null {
  const last = state.messages.at(-1)
  return state.failed !== null && state.status !== 'submitted' && isRetryable(last) && last
    ? last.id
    : null
}

/** Texto breve para la región aria-live. */
export function announcement(state: ChatState): string {
  if (state.status === 'submitted') return 'Consultando los documentos institucionales…'
  const last = state.messages.at(-1)
  if (last?.role !== 'assistant') return ''
  switch (last.kind) {
    case 'answer': return 'El asistente respondió.'
    case 'abstention': return abstentionAnnouncement(last.reason)
    case 'unavailable': return 'El asistente no está disponible en este momento.'
    case 'error': return 'No se pudo completar la consulta.'
    case 'cancelled': return 'Consulta cancelada.'
  }
}
