import { computed, onScopeDispose, shallowRef, watch } from 'vue'
import { AdminApiError, createAdminApi, type AdminApi, type UploadBody } from '../services/adminApi'
import { ACTION_LABELS, flattenRows, hasInProgress } from '../lib/versions'
import type {
  ActionResult,
  DocumentList,
  UploadInput,
  UploadResult,
  VersionAction,
  VersionDetail,
  VersionPatch,
  VersionRow,
} from '../types'
import type { AdminSession } from './useAdminSession'

export const AUTO_REFRESH_MS = 10_000

export interface UseAdminDocumentsOptions {
  session: Pick<AdminSession, 'getToken' | 'signOut'>
  api?: AdminApi
  refreshMs?: number
}

export interface UploadOutcome extends ActionResult {
  result: UploadResult | null
}

const CANNOT_REFRESH = 'No se pudo actualizar el listado.'

/** Listado, refresco automático y acciones con estado por fila. */
export function useAdminDocuments({ session, api, refreshMs = AUTO_REFRESH_MS }: UseAdminDocumentsOptions) {
  const client = api ?? createAdminApi({ getToken: session.getToken })
  const documents = shallowRef<DocumentList['documents']>([])
  const loaded = shallowRef(false)
  const loading = shallowRef(false)
  const loadError = shallowRef<string | null>(null)
  /** Acción en curso por versión (deshabilita los botones de esa fila). */
  const busy = shallowRef<Readonly<Record<string, VersionAction | 'update'>>>({})
  const uploading = shallowRef(false)
  const detail = shallowRef<VersionDetail | null>(null)
  const detailLoading = shallowRef(false)
  const detailError = shallowRef<string | null>(null)
  const announcement = shallowRef('')

  const rows = computed<VersionRow[]>(() => flattenRows(documents.value))
  const autoRefreshing = computed(() => hasInProgress(rows.value))

  /** 401/503-deshabilitado devuelven al acceso; devuelve el mensaje a mostrar. */
  function describe(error: unknown, fallback: string): string {
    if (error instanceof AdminApiError) {
      if (error.kind === 'unauthorized' || error.kind === 'disabled') session.signOut(error.message)
      return error.message
    }
    return fallback
  }

  async function load(silent = false): Promise<boolean> {
    if (loading.value) return false
    loading.value = true
    if (!silent) loadError.value = null
    try {
      documents.value = (await client.listDocuments()).documents
      loaded.value = true
      loadError.value = null
      return true
    } catch (error) {
      const message = describe(error, CANNOT_REFRESH)
      // Un fallo del refresco silencioso conserva el listado previo y avisa.
      loadError.value = message
      return false
    } finally {
      loading.value = false
    }
  }

  function setBusy(id: string, value: VersionAction | 'update' | null): void {
    const { [id]: _removed, ...rest } = busy.value
    void _removed
    busy.value = value === null ? rest : { ...rest, [id]: value }
  }

  async function guarded<T>(id: string, kind: VersionAction | 'update', run: () => Promise<T>, onOk: (value: T) => string, fallback: string): Promise<ActionResult> {
    if (busy.value[id] !== undefined) return { ok: false, message: 'Ya hay una acción en curso para esta versión.' }
    setBusy(id, kind)
    try {
      const message = onOk(await run())
      announcement.value = message
      await load(true)
      if (detail.value?.version.id === id) await openDetail(id, true)
      return { ok: true, message }
    } catch (error) {
      const message = describe(error, fallback)
      announcement.value = message
      return { ok: false, message }
    } finally {
      setBusy(id, null)
    }
  }

  const act = (id: string, action: VersionAction): Promise<ActionResult> => {
    const label = ACTION_LABELS[action]
    return guarded(id, action, async () => {
      if (action === 'publish') return client.publish(id)
      if (action === 'unpublish') return client.unpublish(id)
      return client.reprocess(id)
    }, value => {
      if (action === 'reprocess' && (value as { ingestion?: { outcome: string } }).ingestion?.outcome === 'failed') {
        return 'No se pudo iniciar el procesamiento. Vuelve a pulsar Reprocesar.'
      }
      return `${label}: operación completada.`
    }, `No se pudo completar «${label}».`)
  }

  const update = (id: string, patch: VersionPatch): Promise<ActionResult> =>
    guarded(id, 'update', () => client.patchVersion(id, patch), () => 'Metadatos guardados.', 'No se pudieron guardar los metadatos.')

  async function upload(meta: UploadInput, body: UploadBody): Promise<UploadOutcome> {
    if (uploading.value) return { ok: false, message: 'Ya hay una subida en curso.', result: null }
    uploading.value = true
    try {
      const result = await client.upload(meta, body)
      const failed = result.ingestion?.outcome === 'failed'
      const message = failed
        ? 'El documento se registró, pero no se pudo iniciar el procesamiento. Usa Reprocesar en la fila.'
        : result.created
          ? 'Documento subido: la ingesta comenzó.'
          : 'Este PDF ya estaba registrado (mismo contenido); no se creó una versión nueva.'
      announcement.value = message
      await load(true)
      return { ok: !failed, message, result }
    } catch (error) {
      const message = describe(error, 'No se pudo subir el documento.')
      announcement.value = message
      return { ok: false, message, result: null }
    } finally {
      uploading.value = false
    }
  }

  async function openDetail(id: string, keepOpen = false): Promise<void> {
    if (!keepOpen) detail.value = null
    detailLoading.value = !keepOpen
    detailError.value = null
    try {
      detail.value = await client.getVersion(id)
    } catch (error) {
      detailError.value = describe(error, 'No se pudo cargar el detalle.')
    } finally {
      detailLoading.value = false
    }
  }

  function closeDetail(): void {
    detail.value = null
    detailError.value = null
  }

  let timer: ReturnType<typeof setInterval> | null = null
  const stop = (): void => {
    if (timer !== null) clearInterval(timer)
    timer = null
  }
  watch(autoRefreshing, active => {
    stop()
    if (active) timer = setInterval(() => { void load(true) }, refreshMs)
  }, { immediate: true })
  onScopeDispose(stop)

  return {
    rows, documents, loaded, loading, loadError, busy, uploading, announcement, autoRefreshing,
    detail, detailLoading, detailError,
    load, act, update, upload, openDetail, closeDetail,
  }
}

export type UseAdminDocuments = ReturnType<typeof useAdminDocuments>
