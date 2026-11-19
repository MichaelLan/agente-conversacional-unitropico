import { DOCUMENT_TYPES, type DocumentType, type VersionPatch, type VersionSummary } from '../types'

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024
export const TITLE_MAX = 200
export const LABEL_MAX = 80
export const NONE = '__none__'

export interface FieldError {
  name: string
  message: string
}

export const isDocumentType = (value: unknown): value is DocumentType =>
  typeof value === 'string' && (DOCUMENT_TYPES as readonly string[]).includes(value)

/** Fecha de calendario real escrita como YYYY-MM-DD. */
export function isCalendarDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

function checkText(name: string, label: string, value: string, max: number): FieldError | null {
  const text = value.trim()
  if (text.length === 0) return { name, message: `${label} es obligatorio.` }
  if (text.length > max) return { name, message: `${label} admite hasta ${max} caracteres.` }
  return null
}

function checkDate(name: string, label: string, value: string): FieldError | null {
  const text = value.trim()
  if (text === '' || isCalendarDate(text)) return null
  return { name, message: `${label} debe ser una fecha válida con formato AAAA-MM-DD.` }
}

export interface UploadForm {
  title: string
  document_type: string
  version_label: string
  document_id: string
}

export interface FileLike {
  name: string
  size: number
  type: string
}

export function validateFile(file: FileLike | null): FieldError | null {
  if (file === null) return { name: 'file', message: 'Selecciona un archivo PDF.' }
  const looksPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
  if (!looksPdf) return { name: 'file', message: 'El archivo debe ser un PDF.' }
  if (file.size === 0) return { name: 'file', message: 'El archivo está vacío.' }
  if (file.size > MAX_UPLOAD_BYTES) {
    return { name: 'file', message: 'El archivo supera el límite de 25 MB.' }
  }
  return null
}

export function validateUpload(form: UploadForm, file: FileLike | null): FieldError[] {
  const errors = [
    checkText('title', 'El título', form.title, TITLE_MAX),
    checkText('version_label', 'La etiqueta de versión', form.version_label, LABEL_MAX),
    isDocumentType(form.document_type)
      ? null
      : { name: 'document_type', message: 'Selecciona un tipo de documento.' },
    validateFile(file),
  ]
  return errors.filter((error): error is FieldError => error !== null)
}

export interface MetadataForm {
  title: string
  document_type: string
  version_label: string
  issued_at: string
  effective_from: string
  effective_until: string
  /** Id de versión o `NONE`. */
  supersedes_version_id: string
  /** true = pendiente de revisión; false = «Marcar como revisado». */
  requires_review: boolean
}

export function validateMetadata(form: MetadataForm): FieldError[] {
  const errors: (FieldError | null)[] = [
    checkText('title', 'El título', form.title, TITLE_MAX),
    checkText('version_label', 'La etiqueta de versión', form.version_label, LABEL_MAX),
    isDocumentType(form.document_type)
      ? null
      : { name: 'document_type', message: 'Selecciona un tipo de documento.' },
    checkDate('issued_at', 'La fecha de emisión', form.issued_at),
    checkDate('effective_from', 'El inicio de vigencia', form.effective_from),
    checkDate('effective_until', 'El fin de vigencia', form.effective_until),
  ]
  const from = form.effective_from.trim()
  const until = form.effective_until.trim()
  if (isCalendarDate(from) && isCalendarDate(until) && until < from) {
    errors.push({ name: 'effective_until', message: 'El fin de vigencia debe ser igual o posterior al inicio.' })
  }
  return errors.filter((error): error is FieldError => error !== null)
}

export function metadataFormFrom(title: string, version: VersionSummary, documentType: string): MetadataForm {
  return {
    title,
    document_type: documentType,
    version_label: version.version_label ?? '',
    issued_at: version.issued_at ?? '',
    effective_from: version.effective_from ?? '',
    effective_until: version.effective_until ?? '',
    supersedes_version_id: version.supersedes_version_id ?? NONE,
    requires_review: version.requires_review,
  }
}

/**
 * Parche mínimo: solo los campos cambiados respecto al original. Devuelve
 * `null` si no hay cambios (el backend exige al menos un campo).
 */
export function buildPatch(form: MetadataForm, original: MetadataForm): VersionPatch | null {
  const patch: VersionPatch = {}
  if (form.title.trim() !== original.title.trim()) patch.title = form.title.trim()
  if (form.document_type !== original.document_type && isDocumentType(form.document_type)) {
    patch.document_type = form.document_type
  }
  if (form.version_label.trim() !== original.version_label.trim()) {
    patch.version_label = form.version_label.trim()
  }
  for (const key of ['issued_at', 'effective_from', 'effective_until'] as const) {
    if (form[key].trim() !== original[key].trim()) patch[key] = form[key].trim() || null
  }
  if (form.supersedes_version_id !== original.supersedes_version_id) {
    patch.supersedes_version_id = form.supersedes_version_id === NONE ? null : form.supersedes_version_id
  }
  if (form.requires_review !== original.requires_review) patch.requires_review = form.requires_review
  return Object.keys(patch).length === 0 ? null : patch
}
