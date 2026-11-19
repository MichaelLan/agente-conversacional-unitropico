/** Espera tras un 429: lógica pura con reloj inyectable. */

export const RETRY_AFTER_DEFAULT_SECONDS = 60
export const RETRY_AFTER_MAX_SECONDS = 300

/**
 * `Retry-After` en segundos enteros. Ausente, no numérico, negativo o fecha HTTP
 * → 60 s; se acota a 300 s.
 */
export function parseRetryAfter(value: string | null | undefined): number {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!/^\d{1,9}$/.test(text)) return RETRY_AFTER_DEFAULT_SECONDS
  return Math.min(Number(text), RETRY_AFTER_MAX_SECONDS)
}

/** Segundos restantes (redondeo hacia arriba) hasta `until`; 0 si ya pasó. */
export function secondsLeft(until: number, now: number): number {
  return Math.max(0, Math.ceil((until - now) / 1000))
}

export const cooldownLabel = (seconds: number): string => `Puedes intentarlo de nuevo en ${seconds} s`
export const COOLDOWN_DONE_ANNOUNCEMENT = 'Ya puedes intentarlo de nuevo.'

export interface Clock {
  now: () => number
  setInterval: (callback: () => void, ms: number) => unknown
  clearInterval: (handle: unknown) => void
}

export const systemClock: Clock = {
  now: () => Date.now(),
  setInterval: (callback, ms) => setInterval(callback, ms),
  clearInterval: handle => clearInterval(handle as ReturnType<typeof setInterval>),
}
