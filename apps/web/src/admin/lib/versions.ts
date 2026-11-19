import type {
  DocumentType,
  DocumentWithVersions,
  VersionAction,
  VersionRow,
  VersionStatus,
  VersionSummary,
} from '../types'

export const STATUS_LABELS: Readonly<Record<VersionStatus, string>> = {
  uploaded: 'Subido',
  processing: 'Procesando',
  extracted: 'Extraído',
  review_required: 'Requiere revisión',
  ready: 'Listo',
  published: 'Publicado',
  unpublished: 'Despublicado',
  failed: 'Fallido',
}

export type BadgeColor = 'info' | 'warning' | 'primary' | 'success' | 'neutral' | 'error'

export const STATUS_COLORS: Readonly<Record<VersionStatus, BadgeColor>> = {
  uploaded: 'neutral',
  processing: 'neutral',
  extracted: 'neutral',
  review_required: 'warning',
  ready: 'info',
  published: 'success',
  unpublished: 'neutral',
  failed: 'error',
}

export const DOCUMENT_TYPE_LABELS: Readonly<Record<DocumentType, string>> = {
  resolucion: 'Resolución',
  reglamento: 'Reglamento',
  manual: 'Manual',
  convocatoria: 'Convocatoria',
  politica: 'Política',
  documento_tecnico: 'Documento técnico',
  boletin: 'Boletín',
  otro: 'Otro',
}

export const documentTypeLabel = (value: string): string =>
  (DOCUMENT_TYPE_LABELS as Readonly<Record<string, string>>)[value] ?? value

/** Estados en los que el servidor trabaja de forma activa. */
export const IN_PROGRESS: ReadonlySet<VersionStatus> = new Set(['processing', 'extracted'])

/** Una versión `uploaded` más antigua que esto probablemente no llegó a iniciar el Workflow. */
export const UPLOADED_GRACE_MS = 5 * 60 * 1000

export const REPROCESSABLE: ReadonlySet<VersionStatus> =
  new Set(['uploaded', 'processing', 'extracted', 'failed', 'review_required', 'ready', 'unpublished'])

/** Reprocesar desde estos estados solo sirve si el procesamiento quedó detenido. */
export const STUCK_RECOVERY: ReadonlySet<VersionStatus> = new Set(['processing', 'extracted'])

export const SUSPECTED_START_FAILURE =
  'Posible fallo al iniciar el procesamiento: usa Reprocesar.'

const ageMs = (updatedAt: string, now: number): number => now - Date.parse(updatedAt)

/** `uploaded` con más de 5 minutos sin cambios (o fecha ilegible). */
export function isStaleUploaded(
  version: Pick<VersionSummary, 'status' | 'updated_at'>,
  now: number = Date.now(),
): boolean {
  if (version.status !== 'uploaded') return false
  const age = ageMs(version.updated_at, now)
  return !Number.isFinite(age) || age >= UPLOADED_GRACE_MS
}

/** Refresco automático: processing/extracted, o `uploaded` reciente. Reloj inyectable. */
export function isInProgress(
  version: Pick<VersionSummary, 'status' | 'updated_at'>,
  now: number = Date.now(),
): boolean {
  if (IN_PROGRESS.has(version.status)) return true
  return version.status === 'uploaded' && !isStaleUploaded(version, now)
}

/**
 * Acciones ofrecidas por estado (misma regla que el backend):
 * publicar solo ready/unpublished ya revisadas; despublicar solo published.
 */
export function allowedActions(version: Pick<VersionSummary, 'status' | 'requires_review'>): VersionAction[] {
  const actions: VersionAction[] = []
  if ((version.status === 'ready' || version.status === 'unpublished') && !version.requires_review) {
    actions.push('publish')
  }
  if (version.status === 'published') actions.push('unpublish')
  if (REPROCESSABLE.has(version.status)) actions.push('reprocess')
  return actions
}

export const ACTION_LABELS: Readonly<Record<VersionAction, string>> = {
  publish: 'Publicar',
  unpublish: 'Despublicar',
  reprocess: 'Reprocesar',
}

export const ACTION_CONSEQUENCES: Readonly<Record<VersionAction, string>> = {
  publish: 'La versión pasará a estar disponible para el asistente y podrá citarse en las respuestas del chat público.',
  unpublish: 'La versión dejará de ser consultable por el asistente. Podrás volver a publicarla después.',
  reprocess: 'Se reiniciará la ingesta del documento (extracción, fragmentos e índices). La versión quedará pendiente de revisión.',
}

export function flattenRows(documents: readonly DocumentWithVersions[]): VersionRow[] {
  return documents.flatMap(document => {
    const { versions, ...summary } = document
    return versions.map(version => ({ document: summary, version }))
  })
}

export const hasInProgress = (rows: readonly VersionRow[], now: number = Date.now()): boolean =>
  rows.some(row => isInProgress(row.version, now))

/** «2026-11-18T10:00:00Z» → «2026-11-18 10:00»; texto no ISO se devuelve tal cual. */
export function formatTimestamp(value: string | null): string {
  if (value === null) return '—'
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value)
  return match ? `${match[1]} ${match[2]} UTC` : value
}

export function formatValidity(from: string | null, until: string | null): string {
  if (from === null && until === null) return 'Sin vigencia'
  return `${from ?? '…'} → ${until ?? '…'}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Filtros de la barra lateral: agrupan estados para una lectura rápida. */
export type StatusFilter = 'all' | 'in_progress' | 'review' | 'ready' | 'published' | 'failed'

export const STATUS_FILTERS: readonly { id: StatusFilter; label: string; icon: string; statuses: readonly VersionStatus[] | null }[] = [
  { id: 'all', label: 'Todos', icon: 'i-lucide-files', statuses: null },
  { id: 'in_progress', label: 'En proceso', icon: 'i-lucide-loader', statuses: ['uploaded', 'processing', 'extracted'] },
  { id: 'review', label: 'En revisión', icon: 'i-lucide-eye', statuses: ['review_required'] },
  { id: 'ready', label: 'Listos', icon: 'i-lucide-circle-check', statuses: ['ready'] },
  { id: 'published', label: 'Publicados', icon: 'i-lucide-globe', statuses: ['published'] },
  { id: 'failed', label: 'Con error', icon: 'i-lucide-circle-alert', statuses: ['failed'] },
]

export function matchesFilter(status: VersionStatus, filter: StatusFilter): boolean {
  const statuses = STATUS_FILTERS.find(item => item.id === filter)?.statuses ?? null
  return statuses === null || statuses.includes(status)
}

export function countByFilter(rows: readonly VersionRow[]): Record<StatusFilter, number> {
  const counts = Object.fromEntries(STATUS_FILTERS.map(item => [item.id, 0])) as Record<StatusFilter, number>
  for (const row of rows) {
    for (const item of STATUS_FILTERS) if (matchesFilter(row.version.status, item.id)) counts[item.id] += 1
  }
  return counts
}

/** Agrupa filas planas por documento conservando el orden de llegada. */
export function groupByDocument(rows: readonly VersionRow[]): { document: VersionRow['document']; rows: VersionRow[] }[] {
  const groups = new Map<string, { document: VersionRow['document']; rows: VersionRow[] }>()
  for (const row of rows) {
    const group = groups.get(row.document.id)
    if (group) group.rows.push(row)
    else groups.set(row.document.id, { document: row.document, rows: [row] })
  }
  return [...groups.values()]
}
