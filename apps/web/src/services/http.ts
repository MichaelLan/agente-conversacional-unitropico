/** Transporte HTTP mínimo sobre fetch nativo: JSON, timeout y errores saneados. */

export type HttpFailure = 'network' | 'timeout' | 'invalid_response' | 'invalid_request' | 'rate_limited'

export const HTTP_MESSAGES: Readonly<Record<HttpFailure, string>> = {
  network: 'No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.',
  timeout: 'El asistente tardó demasiado en responder. Inténtalo de nuevo.',
  invalid_response: 'No se pudo completar la consulta. Inténtalo de nuevo.',
  invalid_request: 'Escribe una pregunta de 1 a 500 caracteres.',
  rate_limited: 'Has realizado muchas consultas seguidas. Espera un momento e inténtalo de nuevo.',
}

/**
 * Error con mensaje en español apto para mostrar; nunca expone detalles internos.
 * `message` solo se sustituye por textos de usuario ya validados (p. ej. el 429).
 */
export class HttpError extends Error {
  readonly kind: HttpFailure

  constructor(kind: HttpFailure, message: string = HTTP_MESSAGES[kind]) {
    super(message)
    this.name = 'HttpError'
    this.kind = kind
  }
}

/** Cancelación pedida por el usuario (distinta del timeout). */
export class RequestAbortedError extends Error {
  constructor() {
    super('Consulta cancelada.')
    this.name = 'AbortError'
  }
}

export const isAbortError = (error: unknown): error is RequestAbortedError =>
  error instanceof RequestAbortedError

/** Subconjunto de `fetch` que usamos; permite inyectar dobles en pruebas. */
export type Fetcher = (
  input: string,
  init: RequestInit,
) => Promise<Pick<Response, 'status' | 'json'>>

export interface RequestOptions {
  signal?: AbortSignal | undefined
  fetcher?: Fetcher | undefined
  timeoutMs?: number | undefined
}

export interface JsonResponse {
  status: number
  body: unknown
}

export const DEFAULT_TIMEOUT_MS = 60_000

export interface SendOptions extends RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE' | undefined
  headers?: Record<string, string> | undefined
  /** Cuerpo ya serializado (JSON como texto, o binario). */
  body?: BodyInit | undefined
}

/**
 * Petición genérica: devuelve status y cuerpo JSON sin interpretar el contrato.
 * Los errores de transporte se sanean en `HttpError`; nunca se registra nada.
 */
export async function requestJson(
  url: string,
  { signal, fetcher = fetch, timeoutMs = DEFAULT_TIMEOUT_MS, method = 'GET', headers = {}, body }: SendOptions = {},
): Promise<JsonResponse> {
  if (signal?.aborted) throw new RequestAbortedError()
  const timeout = AbortSignal.timeout(timeoutMs)
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
  let response: Pick<Response, 'status' | 'json'>
  try {
    const init: RequestInit = { method, headers: { Accept: 'application/json', ...headers }, signal: combined }
    if (body !== undefined) init.body = body
    response = await fetcher(url, init)
  } catch {
    throw classify(signal, timeout, 'network')
  }
  try {
    return { status: response.status, body: (await response.json()) as unknown }
  } catch {
    throw classify(signal, timeout, 'invalid_response')
  }
}

/** POST JSON; devuelve status y cuerpo sin interpretar el contrato. */
export function postJson(
  url: string,
  payload: unknown,
  options: RequestOptions = {},
): Promise<JsonResponse> {
  return requestJson(url, {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

function classify(
  signal: AbortSignal | undefined,
  timeout: AbortSignal,
  fallback: HttpFailure,
): Error {
  if (signal?.aborted) return new RequestAbortedError()
  if (timeout.aborted) return new HttpError('timeout')
  return new HttpError(fallback)
}
