import type { Citation } from '../types/chat'

/** «p. 3» para una página; «pp. 13–14» para un rango. */
export function formatPages(start: number, end: number): string {
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`
}

const present = (value: string | null): value is string =>
  value !== null && value.trim().length > 0

/**
 * Datos visibles de una cita: solo los presentes en el contrato.
 * Nunca rellena versión ni sección ausentes.
 */
export function citationDetails(citation: Citation): string[] {
  return [
    present(citation.version_label) ? `Versión ${citation.version_label.trim()}` : null,
    present(citation.section) ? citation.section.trim() : null,
    formatPages(citation.page_start, citation.page_end),
  ].filter((detail): detail is string => detail !== null)
}
