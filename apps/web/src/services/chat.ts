import {
  ABSTAIN_REASONS,
  type AbstainReason,
  type ChatRequest,
  type ChatResult,
  type Citation,
} from '../types/chat'
import {
  HISTORY_CONTENT_MAX_CHARS,
  HISTORY_MAX_MESSAGES,
  countChars,
  normalizeQuestion,
} from '../lib/chat-state'
import { parseRetryAfter } from '../lib/cooldown'
import { HTTP_MESSAGES, HttpError, postJson, type RequestOptions } from './http'

export const CHAT_ENDPOINT = '/api/chat'

type Json = Record<string, unknown>

const isRecord = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const nonblank = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0

const nullableString = (value: unknown): value is string | null =>
  value === null || typeof value === 'string'

const page = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 1

/** Exactamente estas claves: el backend prohíbe campos extra y aquí también. */
function hasExactKeys(value: Json, keys: readonly string[]): boolean {
  const own = Object.keys(value)
  return own.length === keys.length && keys.every(key => Object.hasOwn(value, key))
}

const CITATION_KEYS = [
  'chunk_id', 'document_id', 'document_title', 'version_id', 'version_label',
  'section', 'page_start', 'page_end', 'quote',
] as const

function isCitation(value: unknown): value is Citation {
  return isRecord(value)
    && hasExactKeys(value, CITATION_KEYS)
    && nonblank(value.chunk_id) && nonblank(value.document_id)
    && nonblank(value.document_title) && nonblank(value.version_id)
    && nonblank(value.quote)
    && nullableString(value.version_label) && nullableString(value.section)
    && page(value.page_start) && page(value.page_end)
    && value.page_end >= value.page_start
}

const isReason = (value: unknown): value is AbstainReason =>
  typeof value === 'string' && (ABSTAIN_REASONS as readonly string[]).includes(value)

const RESULT_KEYS = ['status', 'answer', 'reason', 'citations', 'referral'] as const

/**
 * Valida estrictamente el cuerpo según el status HTTP (contrato v1).
 * Devuelve `null` si cualquier invariante no se cumple.
 */
export function parseChatResponse(status: number, body: unknown): ChatResult | null {
  if (!isRecord(body)) return null
  if (status === 502 || status === 503) {
    return hasExactKeys(body, ['status', 'referral'])
      && body.status === 'unavailable' && nonblank(body.referral)
      ? { status: 'unavailable', referral: body.referral }
      : null
  }
  if (status !== 200 || !hasExactKeys(body, RESULT_KEYS) || !Array.isArray(body.citations)) {
    return null
  }
  const { answer, reason, citations, referral } = body
  if (body.status === 'answered') {
    return nonblank(answer) && reason === null && nullableString(referral)
      && citations.length > 0 && citations.every(isCitation)
      ? { status: 'answered', answer, reason: null, citations, referral }
      : null
  }
  if (body.status === 'abstained') {
    return answer === null && citations.length === 0 && isReason(reason) && nonblank(referral)
      ? { status: 'abstained', answer: null, reason, citations: [], referral }
      : null
  }
  return null
}

/** Longitud máxima del mensaje del servidor que se muestra en un 429. */
export const RATE_LIMIT_MESSAGE_MAX_CHARS = 300

/**
 * 429 de la API (`{"error":{"code":"rate_limited","message":...}}`): usa el
 * mensaje del servidor si es texto razonable; si no, el texto fijo.
 */
export function rateLimitMessage(body: unknown): string {
  const error = isRecord(body) ? body.error : undefined
  const message = isRecord(error) && error.code === 'rate_limited' ? error.message : undefined
  return nonblank(message) && countChars(message.trim()) <= RATE_LIMIT_MESSAGE_MAX_CHARS
    ? message.trim()
    : HTTP_MESSAGES.rate_limited
}

function isValidRequest(request: ChatRequest): boolean {
  return normalizeQuestion(request.question) === request.question
    && request.history.length <= HISTORY_MAX_MESSAGES
    && request.history.every(item =>
      (item.role === 'user' || item.role === 'assistant')
      && nonblank(item.content) && countChars(item.content) <= HISTORY_CONTENT_MAX_CHARS)
}

/**
 * POST /api/chat. Resuelve con `answered`, `abstained` o `unavailable`;
 * lanza `HttpError` (mensaje saneado; `rate_limited` en 429, reintentable) o
 * `RequestAbortedError`.
 */
export async function sendChat(request: ChatRequest, options: RequestOptions = {}): Promise<ChatResult> {
  if (!isValidRequest(request)) throw new HttpError('invalid_request')
  const { status, body, retryAfter } = await postJson(CHAT_ENDPOINT, request, options)
  if (status === 429) throw new HttpError('rate_limited', rateLimitMessage(body), parseRetryAfter(retryAfter))
  const result = parseChatResponse(status, body)
  if (result === null) throw new HttpError('invalid_response')
  return result
}
