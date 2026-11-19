/** Tipos del contrato admin v1 (apps/api/src/unitropico_api/domain/admin.py). */

export const VERSION_STATUSES = [
  'uploaded', 'processing', 'extracted', 'review_required',
  'ready', 'published', 'unpublished', 'failed',
] as const
export type VersionStatus = (typeof VERSION_STATUSES)[number]

export const DOCUMENT_TYPES = [
  'resolucion', 'reglamento', 'manual', 'convocatoria',
  'politica', 'documento_tecnico', 'boletin', 'otro',
] as const
export type DocumentType = (typeof DOCUMENT_TYPES)[number]

export interface DocumentSummary {
  id: string
  title: string
  document_type: string
  created_at: string
}

export interface VersionSummary {
  id: string
  document_id: string
  version_label: string | null
  status: VersionStatus
  requires_review: boolean
  error_code: string | null
  processing_attempts: number
  source_sha256: string
  source_bytes: number
  page_count: number | null
  issued_at: string | null
  effective_from: string | null
  effective_until: string | null
  supersedes_version_id: string | null
  created_at: string
  updated_at: string
  published_at: string | null
}

export interface VersionView {
  document: DocumentSummary
  version: VersionSummary
}

export interface DocumentWithVersions extends DocumentSummary {
  versions: VersionSummary[]
}

export interface DocumentList {
  documents: DocumentWithVersions[]
}

export interface ProcessingEvent {
  id: number
  step: string
  status: string
  error_code: string | null
  detail: string | null
  created_at: string
}

export interface WorkflowState {
  instance_id: string
  status: string
  error_name: string | null
  error_message: string | null
}

export type IngestionOutcomeKind = 'created' | 'existing' | 'restarted' | 'failed'

export interface IngestionOutcome {
  instance_id: string
  outcome: IngestionOutcomeKind
  status: string | null
}

export interface VersionDetail extends VersionView {
  error_detail: string | null
  processing_events: ProcessingEvent[]
  workflow: WorkflowState | null
}

export interface UploadResult extends VersionView {
  created: boolean
  ingestion: IngestionOutcome | null
}

export interface ReprocessResult extends VersionView {
  ingestion: IngestionOutcome
}

/** Metadatos de subida (query de POST /admin/uploads). */
export interface UploadInput {
  title: string
  document_type: DocumentType
  version_label: string
  document_id?: string | undefined
}

/** Parche de PATCH /admin/versions/{id}; null borra fechas y sustitución. */
export interface VersionPatch {
  title?: string
  document_type?: DocumentType
  version_label?: string
  issued_at?: string | null
  effective_from?: string | null
  effective_until?: string | null
  supersedes_version_id?: string | null
  requires_review?: boolean
}

/** Fila plana de la tabla: una versión con su documento. */
export interface VersionRow {
  document: DocumentSummary
  version: VersionSummary
}

export type VersionAction = 'publish' | 'unpublish' | 'reprocess'

export interface ActionResult {
  ok: boolean
  message: string
}

/** Fragmento de texto extraído (GET /admin/versions/{id}/chunks). */
export interface Chunk {
  ordinal: number
  id: string
  section: string | null
  page_start: number
  page_end: number
  text: string
  chunker_version: string | null
}

export interface ChunkPage {
  version_id: string
  status: VersionStatus
  total: number
  offset: number
  limit: number
  chunks: Chunk[]
}

export interface Correction {
  id: string
  version_id: string
  page_number: number
  find_text: string
  replace_text: string
  reason: string
  created_at: string
  deleted_at: string | null
  active: boolean
}

export interface CorrectionList {
  version_id: string
  corrections: Correction[]
}

/** Cuerpo de POST /admin/versions/{id}/corrections. */
export interface CorrectionInput {
  page_number: number
  find_text: string
  replace_text: string
  reason: string
}

/** Respuesta de crear/borrar una corrección. */
export interface CorrectionResult {
  correction: Correction
  version_status: VersionStatus
  requires_review: boolean
  reprocess_required: boolean
  message: string
}
