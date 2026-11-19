import type { PageOverride, PageOverrideInput, PageSource } from '../types'
import { canEditCorrections } from './corrections'

export const OVERRIDE_TEXT_MAX = 20000
export const OVERRIDE_REASON_MAX = 300

export const PAGE_TEXT_UNAVAILABLE_CODE = 'page_text_unavailable'
export const PAGE_TEXT_UNAVAILABLE_MESSAGE =
  'Reprocesa la versión para ver el texto por página. Esta versión se procesó antes de que se guardara el texto preparado de cada página.'

export const OVERRIDE_PUBLISHED_MESSAGE =
  'La versión está publicada y no admite sustituir páginas. Despublícala primero.'

export const OVERRIDE_WARNING =
  'Reemplaza TODO el texto de la página. Transcríbelo fielmente desde el PDF original, incluidas sus erratas: no corrijas ni resumas.'

export const OVERRIDE_EDIT_HINT = 'Para editar una sustitución, quítala y crea otra.'

export const SOURCE_LABELS: Readonly<Record<PageSource, string>> = {
  ocr: 'Texto OCR',
  override: 'Texto sustituido',
}

/** Las sustituciones se editan en las mismas condiciones que las correcciones. */
export const canEditOverrides = canEditCorrections

const codePoints = (value: string): number => [...value].length

/** Cuenta caracteres como el servidor (puntos de código, no unidades UTF-16). */
export const countChars = codePoints

export const clampPage = (value: number, pageCount: number): number =>
  Math.min(Math.max(1, Math.trunc(value)), Math.max(1, pageCount))

/** Número de página desde texto libre: entero 1..pageCount o null. */
export function parsePageInput(raw: string, pageCount: number): number | null {
  if (!/^\s*\d+\s*$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= 1 && value <= pageCount ? value : null
}

/** Páginas vecinas (null en los extremos). */
export function pageNeighbours(current: number, pageCount: number): { prev: number | null; next: number | null } {
  return { prev: current > 1 ? current - 1 : null, next: current < pageCount ? current + 1 : null }
}

export interface OverrideDraft {
  text: string
  reason: string
}

export type OverrideErrors = Partial<Record<keyof OverrideDraft | 'page', string>>

// Cc (salvo \n y \t; \r se normaliza) y Cf: el servidor responde 422.
const FORBIDDEN = /[\p{Cc}\p{Cf}]/u

export const normalizeNewlines = (value: string): string => value.replace(/\r\n?/g, '\n')

export function validateOverride(draft: OverrideDraft, pageNumber: number, pageCount: number | null): OverrideErrors {
  const errors: OverrideErrors = {}
  if (!Number.isInteger(pageNumber) || pageNumber < 1 || (pageCount !== null && pageNumber > pageCount)) {
    errors.page = 'La página no es válida para esta versión.'
  }
  const text = normalizeNewlines(draft.text)
  if (text.trim() === '') errors.text = 'Escribe el texto completo de la página.'
  else if (codePoints(text) > OVERRIDE_TEXT_MAX) errors.text = `Máximo ${OVERRIDE_TEXT_MAX} caracteres.`
  else if (FORBIDDEN.test(text.replace(/[\n\t]/g, ''))) {
    errors.text = 'El texto contiene caracteres de control o invisibles no permitidos.'
  }
  const reason = draft.reason.trim()
  if (reason === '') errors.reason = 'Indica el motivo.'
  else if (codePoints(reason) > OVERRIDE_REASON_MAX) errors.reason = `Máximo ${OVERRIDE_REASON_MAX} caracteres.`
  else if (FORBIDDEN.test(reason)) errors.reason = 'El motivo contiene caracteres no permitidos.'
  return errors
}

/** Solo válido si `validateOverride` no devolvió errores. */
export const overrideToInput = (draft: OverrideDraft, pageNumber: number): PageOverrideInput => ({
  page_number: pageNumber,
  text: normalizeNewlines(draft.text),
  reason: draft.reason.trim(),
})

/** Sustitución activa de una página, si la hay. */
export const activeOverrideFor = (overrides: readonly PageOverride[], page: number): PageOverride | null =>
  overrides.find(item => item.active && item.page_number === page) ?? null

const SERVER_CODES: Readonly<Record<string, string>> = {
  override_exists: 'Esa página ya tiene una sustitución activa. Quítala primero.',
  version_published: OVERRIDE_PUBLISHED_MESSAGE,
  page_out_of_range: 'La página no existe en esta versión.',
}

/** Mensaje en español para códigos conocidos; null deja el mensaje del servidor. */
export const explainOverrideError = (code: string | null): string | null =>
  code === null ? null : (SERVER_CODES[code] ?? null)
