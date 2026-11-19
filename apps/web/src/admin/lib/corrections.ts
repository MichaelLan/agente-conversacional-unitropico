import type { Chunk, Correction, CorrectionInput, ProcessingEvent, VersionStatus } from '../types'

export const FIND_MAX = 500
export const REPLACE_MAX = 500
export const REASON_MAX = 300
export const CHUNKS_PAGE_SIZE = 50

export const MATCH_RULE_HELP =
  'El texto a buscar debe aparecer exactamente una vez en esa página. Los espacios y saltos de línea se consideran equivalentes; mayúsculas, tildes y puntuación deben ser idénticas.'

export const PUBLISHED_BLOCK_MESSAGE =
  'La versión está publicada y no admite cambios en las correcciones. Despublícala primero.'

/** Las correcciones solo se editan fuera del estado `published`. */
export const canEditCorrections = (status: VersionStatus): boolean => status !== 'published'

/** «p. 3» o «pp. 13–14». */
export const formatPages = (start: number, end: number): string =>
  start === end ? `p. ${start}` : `pp. ${start}–${end}`

export function chunkPages(chunk: Pick<Chunk, 'page_start' | 'page_end'>): number[] {
  const pages: number[] = []
  for (let page = chunk.page_start; page <= chunk.page_end && pages.length < 200; page += 1) pages.push(page)
  return pages
}

export interface CorrectionDraft {
  page_number: string
  find_text: string
  replace_text: string
  reason: string
}

export const emptyDraft = (): CorrectionDraft => ({ page_number: '', find_text: '', replace_text: '', reason: '' })

export const PAGE_CHOICE_HELP = 'Elige la página exacta donde aparece el texto'

/**
 * Precarga el formulario desde un chunk. Página preseleccionada solo si el chunk
 * es de una única página; si abarca varias queda vacía y el usuario debe elegirla
 * entre `pages`. Con selección razonable se precarga también el texto a buscar.
 */
export function prefillFromChunk(
  chunk: Pick<Chunk, 'page_start' | 'page_end'>,
  selection = '',
): { draft: CorrectionDraft; pages: number[] } {
  const picked = selection.trim()
  return {
    draft: {
      ...emptyDraft(),
      page_number: chunk.page_start === chunk.page_end ? String(chunk.page_start) : '',
      find_text: picked.length > 0 && picked.length <= FIND_MAX ? picked : '',
    },
    pages: chunkPages(chunk),
  }
}

export type DraftErrors = Partial<Record<keyof CorrectionDraft, string>>

export function validateDraft(
  draft: CorrectionDraft,
  pageCount: number | null = null,
  pageChoiceRequired = false,
): DraftErrors {
  const errors: DraftErrors = {}
  const page = Number(draft.page_number)
  if (draft.page_number.trim() === '' && pageChoiceRequired) {
    errors.page_number = PAGE_CHOICE_HELP + '.'
  } else if (draft.page_number.trim() === '' || !Number.isInteger(page) || page < 1) {
    errors.page_number = 'Indica un número de página entero (1 o más).'
  } else if (pageCount !== null && page > pageCount) {
    errors.page_number = `La versión solo tiene ${pageCount} páginas.`
  }
  if (draft.find_text.trim() === '') errors.find_text = 'Indica el texto a buscar.'
  else if (draft.find_text.length > FIND_MAX) errors.find_text = `Máximo ${FIND_MAX} caracteres.`
  if (draft.replace_text.length > REPLACE_MAX) errors.replace_text = `Máximo ${REPLACE_MAX} caracteres.`
  if (draft.reason.trim() === '') errors.reason = 'Indica el motivo.'
  else if (draft.reason.length > REASON_MAX) errors.reason = `Máximo ${REASON_MAX} caracteres.`
  return errors
}

/** Solo válido si `validateDraft` no devolvió errores. */
export const draftToInput = (draft: CorrectionDraft): CorrectionInput => ({
  page_number: Number(draft.page_number),
  find_text: draft.find_text,
  replace_text: draft.replace_text,
  reason: draft.reason.trim(),
})

const normalize = (value: string): string =>
  value.normalize('NFD').replace(/\p{M}+/gu, '').toLowerCase()

/** Filtro local (sin tildes ni mayúsculas) sobre texto y sección de la página cargada. */
export function filterChunks(chunks: readonly Chunk[], query: string): Chunk[] {
  const needle = normalize(query.trim())
  if (needle === '') return [...chunks]
  return chunks.filter(chunk => normalize(`${chunk.section ?? ''}\n${chunk.text}`).includes(needle))
}

export const sortCorrections = (items: readonly Correction[]): Correction[] =>
  [...items].sort((a, b) => Number(b.active) - Number(a.active))

export const STEP_LABELS: Readonly<Record<string, string>> = { correct: 'Corrección' }

export const stepLabel = (step: string): string => STEP_LABELS[step] ?? step

export interface CorrectionNotApplicable {
  correction: string
  page: number
  matches: number
}

/** `step=chunk correction=<cid> page=<n> matches=<k>`; null si no cumple el formato. */
export function parseCorrectionDetail(detail: string | null | undefined): CorrectionNotApplicable | null {
  if (typeof detail !== 'string') return null
  const match = /correction=(\S+)\s+page=(\d+)\s+matches=(\d+)/.exec(detail)
  if (!match) return null
  const [, correction, page, matches] = match
  if (correction === undefined || page === undefined || matches === undefined) return null
  return { correction, page: Number(page), matches: Number(matches) }
}

export const NOT_APPLICABLE_CODE = 'correction_not_applicable'

/** Explicación en español de `correction_not_applicable`, o null para otros errores. */
export function explainCorrectionError(code: string | null, detail: string | null): string | null {
  if (code !== NOT_APPLICABLE_CODE) return null
  const parsed = parseCorrectionDetail(detail)
  if (!parsed) {
    return 'Una corrección no coincide exactamente una vez en su página. Edítala o bórrala y reprocesa.'
  }
  return `La corrección ${parsed.correction} no coincide exactamente una vez en la página ${parsed.page} `
    + `(coincidencias: ${parsed.matches}). Edítala o bórrala y reprocesa.`
}

const CORRECT_DETAILS: Readonly<Record<string, string>> = {
  correction_created: 'Corrección creada',
  correction_deleted: 'Corrección borrada',
  correction_applied: 'Corrección aplicada',
}

/** Texto legible del evento: traduce detalles `correct`; el resto se muestra tal cual. */
export function eventDetailText(event: Pick<ProcessingEvent, 'step' | 'detail' | 'error_code'>): string | null {
  const explained = explainCorrectionError(event.error_code, event.detail)
  if (explained) return explained
  if (event.detail === null) return null
  if (event.step !== 'correct') return event.detail
  const match = /^(\w+)(?:\s+id=(\S+))?(?:\s+page=(\d+))?/.exec(event.detail)
  const label = match?.[1] ? CORRECT_DETAILS[match[1]] : undefined
  if (!match || label === undefined) return event.detail
  const parts = [match[2] ? `id ${match[2]}` : null, match[3] ? `página ${match[3]}` : null].filter(Boolean)
  return parts.length > 0 ? `${label} (${parts.join(', ')})` : label
}

/** Subconjunto de `Node` que necesita `selectionWithin`. */
export interface ContainsNode<T> {
  contains(other: T | null): boolean
}

/**
 * Texto seleccionado solo si ambos extremos (anchor y focus) están dentro del
 * contenedor; si no (u otra selección vacía) devuelve ''.
 */
export function selectionWithin<T>(
  container: ContainsNode<T> | null | undefined,
  selection: { anchorNode: T | null; focusNode: T | null; toString(): string } | null | undefined,
): string {
  if (!container || !selection) return ''
  const { anchorNode, focusNode } = selection
  if (anchorNode === null || focusNode === null) return ''
  return container.contains(anchorNode) && container.contains(focusNode) ? selection.toString() : ''
}
