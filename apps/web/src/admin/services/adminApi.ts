import {
  VERSION_STATUSES,
  type Chunk,
  type ChunkPage,
  type Correction,
  type CorrectionInput,
  type CorrectionList,
  type CorrectionResult,
  type DocumentList,
  type DocumentSummary,
  type DocumentWithVersions,
  type IngestionOutcome,
  type ProcessingEvent,
  type ReprocessResult,
  type UploadInput,
  type UploadResult,
  type VersionDetail,
  type VersionPatch,
  type VersionStatus,
  type VersionSummary,
  type VersionView,
  type WorkflowState,
} from '../types'
import { HttpError, requestJson, type Fetcher } from '../../services/http'

export const ADMIN_BASE = '/api/admin'
export const REQUEST_TIMEOUT_MS = 30_000
export const UPLOAD_TIMEOUT_MS = 120_000

type Json = Record<string, unknown>

const isRecord = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isStr = (value: unknown): value is string => typeof value === 'string'
const isNullableStr = (value: unknown): value is string | null => value === null || isStr(value)
const isInt = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value)
const isNullableInt = (value: unknown): value is number | null => value === null || isInt(value)
const isStatus = (value: unknown): value is VersionStatus =>
  typeof value === 'string' && (VERSION_STATUSES as readonly string[]).includes(value)

export function isDocumentSummary(value: unknown): value is DocumentSummary {
  return isRecord(value) && isStr(value.id) && isStr(value.title)
    && isStr(value.document_type) && isStr(value.created_at)
}

export function isVersionSummary(value: unknown): value is VersionSummary {
  return isRecord(value)
    && isStr(value.id) && isStr(value.document_id) && isNullableStr(value.version_label)
    && isStatus(value.status) && typeof value.requires_review === 'boolean'
    && (value.corrections_dirty === undefined || typeof value.corrections_dirty === 'boolean')
    && isNullableStr(value.error_code) && isInt(value.processing_attempts)
    && isStr(value.source_sha256) && isInt(value.source_bytes) && isNullableInt(value.page_count)
    && isNullableStr(value.issued_at) && isNullableStr(value.effective_from)
    && isNullableStr(value.effective_until) && isNullableStr(value.supersedes_version_id)
    && isStr(value.created_at) && isStr(value.updated_at) && isNullableStr(value.published_at)
}

function isVersionView(value: unknown): value is VersionView {
  return isRecord(value) && isDocumentSummary(value.document) && isVersionSummary(value.version)
}

function isDocumentWithVersions(value: unknown): value is DocumentWithVersions {
  return isDocumentSummary(value) && Array.isArray((value as unknown as Json).versions)
    && ((value as unknown as Json).versions as unknown[]).every(isVersionSummary)
}

export function isDocumentList(value: unknown): value is DocumentList {
  return isRecord(value) && Array.isArray(value.documents) && value.documents.every(isDocumentWithVersions)
}

const isEvent = (value: unknown): value is ProcessingEvent =>
  isRecord(value) && isInt(value.id) && isStr(value.step) && isStr(value.status)
  && isNullableStr(value.error_code) && isNullableStr(value.detail) && isStr(value.created_at)

const isWorkflow = (value: unknown): value is WorkflowState =>
  isRecord(value) && isStr(value.instance_id) && isStr(value.status)
  && (value.error_name === undefined || isNullableStr(value.error_name))
  && (value.error_message === undefined || isNullableStr(value.error_message))

const OUTCOMES = ['created', 'existing', 'restarted', 'failed'] as const

const isIngestion = (value: unknown): value is IngestionOutcome =>
  isRecord(value) && isStr(value.instance_id)
  && (OUTCOMES as readonly unknown[]).includes(value.outcome)
  && (value.status === undefined || isNullableStr(value.status))

export function isVersionDetail(value: unknown): value is VersionDetail {
  return isVersionView(value) && isRecord(value)
    && isNullableStr(value.error_detail)
    && Array.isArray(value.processing_events) && value.processing_events.every(isEvent)
    && (value.workflow === null || isWorkflow(value.workflow))
}

export function isUploadResult(value: unknown): value is UploadResult {
  return isVersionView(value) && isRecord(value) && typeof value.created === 'boolean'
    && (value.ingestion === null || isIngestion(value.ingestion))
}

export function isReprocessResult(value: unknown): value is ReprocessResult {
  return isVersionView(value) && isRecord(value) && isIngestion(value.ingestion)
}

export function isChunk(value: unknown): value is Chunk {
  return isRecord(value) && isInt(value.ordinal) && isStr(value.id) && isNullableStr(value.section)
    && isInt(value.page_start) && isInt(value.page_end) && isStr(value.text)
    && isNullableStr(value.chunker_version)
}

export function isChunkPage(value: unknown): value is ChunkPage {
  return isRecord(value) && isStr(value.version_id) && isStatus(value.status)
    && isInt(value.total) && isInt(value.offset) && isInt(value.limit)
    && Array.isArray(value.chunks) && value.chunks.every(isChunk)
}

export function isCorrection(value: unknown): value is Correction {
  return isRecord(value) && isStr(value.id) && isStr(value.version_id) && isInt(value.page_number)
    && isStr(value.find_text) && isStr(value.replace_text) && isStr(value.reason)
    && isStr(value.created_at) && isNullableStr(value.deleted_at) && typeof value.active === 'boolean'
}

export function isCorrectionList(value: unknown): value is CorrectionList {
  return isRecord(value) && isStr(value.version_id)
    && Array.isArray(value.corrections) && value.corrections.every(isCorrection)
}

export function isCorrectionResult(value: unknown): value is CorrectionResult {
  return isRecord(value) && isCorrection(value.correction) && isStatus(value.version_status)
    && typeof value.requires_review === 'boolean' && typeof value.reprocess_required === 'boolean'
    && isStr(value.message)
}

export type AdminErrorKind = 'unauthorized' | 'disabled' | 'server' | 'network' | 'timeout' | 'invalid_response'

/** Error de la API admin: `message` es apto para mostrarse (del servidor o fijo). */
export class AdminApiError extends Error {
  readonly kind: AdminErrorKind
  readonly status: number | null
  readonly code: string | null

  constructor(kind: AdminErrorKind, message: string, status: number | null = null, code: string | null = null) {
    super(message)
    this.name = 'AdminApiError'
    this.kind = kind
    this.status = status
    this.code = code
  }
}

const FIXED_MESSAGES = {
  network: 'No se pudo conectar con la API. Revisa tu conexión e inténtalo de nuevo.',
  timeout: 'La API tardó demasiado en responder. Inténtalo de nuevo.',
  invalid_response: 'La API respondió algo inesperado. Inténtalo de nuevo.',
  server: 'La API no pudo completar la operación.',
  unauthorized: 'Credenciales de administración inválidas. Vuelve a ingresar el token.',
  disabled: 'El panel no está habilitado en este entorno.',
} as const

function errorFrom(status: number, body: unknown): AdminApiError {
  const envelope = isRecord(body) && isRecord(body.error) ? body.error : null
  const code = envelope && isStr(envelope.code) ? envelope.code : null
  const message = envelope && isStr(envelope.message) && envelope.message.trim() ? envelope.message : null
  if (status === 401) return new AdminApiError('unauthorized', message ?? FIXED_MESSAGES.unauthorized, status, code)
  if (status === 503 && code === 'admin_disabled') {
    return new AdminApiError('disabled', FIXED_MESSAGES.disabled, status, code)
  }
  return new AdminApiError('server', message ?? FIXED_MESSAGES.server, status, code)
}

export interface AdminApiOptions {
  /** Lee el token en el momento de cada petición; nunca se copia ni se registra. */
  getToken: () => string | null
  fetcher?: Fetcher | undefined
  signal?: AbortSignal | undefined
}

export type UploadBody = Blob | ArrayBuffer | Uint8Array<ArrayBuffer>

export interface AdminApi {
  listDocuments(): Promise<DocumentList>
  getVersion(id: string): Promise<VersionDetail>
  upload(meta: UploadInput, body: UploadBody): Promise<UploadResult>
  patchVersion(id: string, patch: VersionPatch): Promise<VersionView>
  publish(id: string): Promise<VersionView>
  unpublish(id: string): Promise<VersionView>
  reprocess(id: string): Promise<ReprocessResult>
  listChunks(id: string, offset: number, limit: number): Promise<ChunkPage>
  listCorrections(id: string): Promise<CorrectionList>
  createCorrection(id: string, input: CorrectionInput): Promise<CorrectionResult>
  deleteCorrection(id: string, correctionId: string): Promise<CorrectionResult>
}

/** Construye la URL de subida con los metadatos en query (sin token). */
export function uploadUrl(meta: UploadInput): string {
  const query = new URLSearchParams({
    title: meta.title,
    document_type: meta.document_type,
    version_label: meta.version_label,
  })
  if (meta.document_id) query.set('document_id', meta.document_id)
  return `${ADMIN_BASE}/uploads?${query.toString()}`
}

export function createAdminApi({ getToken, fetcher, signal }: AdminApiOptions): AdminApi {
  async function call<T>(
    path: string,
    init: {
      method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
      headers?: Record<string, string>
      body?: BodyInit
      timeoutMs?: number
      ok?: readonly number[]
    },
    guard: (value: unknown) => value is T,
  ): Promise<T> {
    const token = getToken()
    if (token === null || token === '') {
      throw new AdminApiError('unauthorized', 'Ingresa el token de administración.', 401)
    }
    let response
    try {
      response = await requestJson(path, {
        method: init.method ?? 'GET',
        headers: { ...init.headers, Authorization: `Bearer ${token}` },
        body: init.body,
        timeoutMs: init.timeoutMs ?? REQUEST_TIMEOUT_MS,
        fetcher,
        signal,
      })
    } catch (error) {
      if (error instanceof HttpError) {
        if (error.kind === 'timeout') throw new AdminApiError('timeout', FIXED_MESSAGES.timeout)
        if (error.kind === 'network') throw new AdminApiError('network', FIXED_MESSAGES.network)
        throw new AdminApiError('invalid_response', FIXED_MESSAGES.invalid_response)
      }
      throw error
    }
    if (!(init.ok ?? [200]).includes(response.status)) throw errorFrom(response.status, response.body)
    if (!guard(response.body)) throw new AdminApiError('invalid_response', FIXED_MESSAGES.invalid_response, response.status)
    return response.body
  }

  const versionPath = (id: string, action = ''): string =>
    `${ADMIN_BASE}/versions/${encodeURIComponent(id)}${action}`

  return {
    listDocuments: () => call(`${ADMIN_BASE}/documents`, {}, isDocumentList),
    getVersion: id => call(versionPath(id), {}, isVersionDetail),
    upload: (meta, body) => call(uploadUrl(meta), {
      method: 'POST',
      headers: { 'Content-Type': 'application/pdf' },
      body: body as BodyInit,
      timeoutMs: UPLOAD_TIMEOUT_MS,
      ok: [200, 201],
    }, isUploadResult),
    patchVersion: (id, patch) => call(versionPath(id), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }, isVersionView),
    publish: id => call(versionPath(id, '/publish'), { method: 'POST' }, isVersionView),
    unpublish: id => call(versionPath(id, '/unpublish'), { method: 'POST' }, isVersionView),
    reprocess: id => call(versionPath(id, '/reprocess'), { method: 'POST' }, isReprocessResult),
    listChunks: (id, offset, limit) => call(
      versionPath(id, `/chunks?${new URLSearchParams({ offset: String(offset), limit: String(limit) }).toString()}`),
      {}, isChunkPage),
    listCorrections: id => call(versionPath(id, '/corrections'), {}, isCorrectionList),
    createCorrection: (id, input) => call(versionPath(id, '/corrections'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
      ok: [201],
    }, isCorrectionResult),
    deleteCorrection: (id, correctionId) => call(
      versionPath(id, `/corrections/${encodeURIComponent(correctionId)}`),
      { method: 'DELETE' }, isCorrectionResult),
  }
}
