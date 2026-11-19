import { shallowRef } from 'vue'
import { AdminApiError, type AdminApi } from '../services/adminApi'
import { CHUNKS_PAGE_SIZE } from '../lib/corrections'
import type { ChunkPage, Correction, CorrectionInput, CorrectionResult } from '../types'

export interface UseVersionReviewOptions {
  api: AdminApi
  versionId: () => string
  /** Convierte el error en mensaje (y cierra sesión si es 401). */
  describe: (error: unknown, fallback: string) => string
  pageSize?: number
}

/** Texto extraído (paginado) y correcciones de una versión. */
export function useVersionReview({ api, versionId, describe, pageSize = CHUNKS_PAGE_SIZE }: UseVersionReviewOptions) {
  const page = shallowRef<ChunkPage | null>(null)
  const chunksLoading = shallowRef(false)
  const chunksError = shallowRef<string | null>(null)
  const corrections = shallowRef<Correction[]>([])
  const correctionsLoading = shallowRef(false)
  const correctionsError = shallowRef<string | null>(null)
  const submitting = shallowRef(false)
  const formError = shallowRef<string | null>(null)
  const notice = shallowRef<CorrectionResult | null>(null)
  let chunksSeq = 0
  let correctionsSeq = 0

  async function loadChunks(offset = page.value?.offset ?? 0): Promise<void> {
    const seq = ++chunksSeq
    chunksLoading.value = true
    chunksError.value = null
    try {
      const result = await api.listChunks(versionId(), Math.max(0, offset), pageSize)
      if (seq === chunksSeq) page.value = result
    } catch (error) {
      if (seq === chunksSeq) chunksError.value = describe(error, 'No se pudo cargar el texto extraído.')
    } finally {
      if (seq === chunksSeq) chunksLoading.value = false
    }
  }

  async function loadCorrections(): Promise<void> {
    const seq = ++correctionsSeq
    correctionsLoading.value = true
    correctionsError.value = null
    try {
      const result = await api.listCorrections(versionId())
      if (seq === correctionsSeq) corrections.value = result.corrections
    } catch (error) {
      if (seq === correctionsSeq) correctionsError.value = describe(error, 'No se pudieron cargar las correcciones.')
    } finally {
      if (seq === correctionsSeq) correctionsLoading.value = false
    }
  }

  async function mutate(run: () => Promise<CorrectionResult>, fallback: string): Promise<boolean> {
    if (submitting.value) return false
    submitting.value = true
    formError.value = null
    try {
      notice.value = await run()
      await loadCorrections()
      return true
    } catch (error) {
      formError.value = error instanceof AdminApiError ? describe(error, fallback) : fallback
      return false
    } finally {
      submitting.value = false
    }
  }

  const create = (input: CorrectionInput): Promise<boolean> =>
    mutate(() => api.createCorrection(versionId(), input), 'No se pudo crear la corrección.')
  const remove = (correctionId: string): Promise<boolean> =>
    mutate(() => api.deleteCorrection(versionId(), correctionId), 'No se pudo borrar la corrección.')

  const dismissNotice = (): void => { notice.value = null }

  return {
    page, chunksLoading, chunksError, corrections, correctionsLoading, correctionsError,
    submitting, formError, notice,
    loadChunks, loadCorrections, create, remove, dismissNotice,
  }
}

export type UseVersionReview = ReturnType<typeof useVersionReview>
