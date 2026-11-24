/** Contrato /chat v1 (docs/IMPLEMENTATION_STATUS.md, sección 4). Tipos manuales. */

export type ChatRole = 'user' | 'assistant'

export interface ChatHistoryItem {
  role: ChatRole
  content: string
}

export interface ChatRequest {
  question: string
  history: ChatHistoryItem[]
}

export const ABSTAIN_REASONS = [
  'no_evidence',
  'model_abstained',
  'invalid_output',
  'invalid_citation',
  'stale_source',
] as const

export type AbstainReason = (typeof ABSTAIN_REASONS)[number]

export interface Citation {
  chunk_id: string
  document_id: string
  document_title: string
  version_id: string
  version_label: string | null
  section: string | null
  page_start: number
  page_end: number
  quote: string
}

export interface AnsweredResponse {
  status: 'answered'
  answer: string
  reason: null
  citations: Citation[]
  referral: string | null
}

export interface AbstainedResponse {
  status: 'abstained'
  answer: null
  /** `null` si el backend omite el motivo o envía uno desconocido (compatibilidad futura). */
  reason: AbstainReason | null
  citations: []
  referral: string
}

/** Cuerpo de 503 (y 502 cuando el RAG devuelve un payload inválido). */
export interface UnavailableResponse {
  status: 'unavailable'
  referral: string
}

export type ChatResult = AnsweredResponse | AbstainedResponse | UnavailableResponse

/** Estado de la conversación, compatible con `ChatStatus` de Nuxt UI. */
export type ChatStatus = 'ready' | 'submitted' | 'error'

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface AnswerMessage {
  id: string
  role: 'assistant'
  kind: 'answer'
  text: string
  citations: Citation[]
  referral: string | null
}

export interface AbstentionMessage {
  id: string
  role: 'assistant'
  kind: 'abstention'
  reason: AbstainReason | null
  referral: string
}

export interface UnavailableMessage {
  id: string
  role: 'assistant'
  kind: 'unavailable'
  referral: string
}

export interface ErrorMessage {
  id: string
  role: 'assistant'
  kind: 'error'
  text: string
}

export interface CancelledMessage {
  id: string
  role: 'assistant'
  kind: 'cancelled'
}

export type AssistantMessage =
  | AnswerMessage
  | AbstentionMessage
  | UnavailableMessage
  | ErrorMessage
  | CancelledMessage

export type ChatMessage = UserMessage | AssistantMessage

export interface ChatState {
  messages: ChatMessage[]
  status: ChatStatus
  /** Pregunta en curso (status `submitted`). */
  pending: string | null
  /** Última pregunta que falló o se canceló; la que `retry` reenvía. */
  failed: string | null
}
