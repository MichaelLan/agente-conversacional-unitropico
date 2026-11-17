import type { Evidence, RetrievalResult } from '../types/retrieval'

const failure = 'No se pudo completar la consulta. Inténtalo de nuevo.'
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const nonblank = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0

function isEvidence(value: unknown): value is Evidence {
  return record(value)
    && ['document_id', 'version_id', 'chunk_id', 'text'].every(key => nonblank(value[key]))
    && typeof value.source_sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.source_sha256)
    && typeof value.page === 'number' && Number.isSafeInteger(value.page) && value.page > 0
    && (value.section === null || typeof value.section === 'string')
}

function isResult(value: unknown, status: number): value is RetrievalResult {
  if (!record(value) || !Array.isArray(value.evidence)) return false
  if (status === 502 || status === 503) {
    return value.status === 'unavailable' && value.evidence.length === 0
  }
  if (status !== 200) return false
  return (value.status === 'abstained' && value.evidence.length === 0)
    || (value.status === 'evidence_found' && value.evidence.length > 0
      && value.evidence.every(isEvidence))
}

/** Retrieve source fragments only; never expose dependency diagnostics. */
export async function queryRetrieval(
  question: string,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<RetrievalResult> {
  const trimmed = question.trim()
  if (!trimmed || Array.from(trimmed).length > 500) {
    throw new Error('Escribe una pregunta de 1 a 500 caracteres.')
  }
  try {
    const response = await fetcher('/api/retrieval/query', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: trimmed, limit: 5 }), signal: signal ?? null,
    })
    const body: unknown = await response.json()
    if (!isResult(body, response.status)) throw new Error(failure)
    return body
  } catch {
    if (signal?.aborted) throw new DOMException('Consulta cancelada.', 'AbortError')
    throw new Error(failure)
  }
}
