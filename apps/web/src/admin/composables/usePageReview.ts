import { shallowRef } from 'vue'
import { AdminApiError, type AdminApi } from '../services/adminApi'
import { PAGE_TEXT_UNAVAILABLE_CODE, explainOverrideError } from '../lib/pages'
import type { PageOverride, PageOverrideInput, PageOverrideResult, PageText } from '../types'

export interface UsePageReviewOptions {
  api: AdminApi
  versionId: () => string
  describe: (error: unknown, fallback: string) => string
}

/** Texto preparado por página y sustituciones de una versión. */
export function usePageReview({ api, versionId, describe }: UsePageReviewOptions) {
  const pageNumber = shallowRef(1)
  const text = shallowRef<PageText | null>(null)
  const loading = shallowRef(false)
  const error = shallowRef<string | null>(null)
  const unavailable = shallowRef(false)
  const overrides = shallowRef<PageOverride[]>([])
  const overridesError = shallowRef<string | null>(null)
  const submitting = shallowRef(false)
  const formError = shallowRef<string | null>(null)
  const notice = shallowRef<PageOverrideResult | null>(null)
  let seq = 0
  let overridesSeq = 0

  async function loadPage(page = pageNumber.value): Promise<void> {
    const mine = ++seq
    pageNumber.value = page
    loading.value = true
    error.value = null
    unavailable.value = false
    try {
      const result = await api.getPage(versionId(), page)
      if (mine === seq) text.value = result
    } catch (cause) {
      if (mine !== seq) return
      text.value = null
      if (cause instanceof AdminApiError && cause.status === 404 && cause.code === PAGE_TEXT_UNAVAILABLE_CODE) {
        unavailable.value = true
      } else {
        error.value = describe(cause, 'No se pudo cargar el texto de la página.')
      }
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  async function loadOverrides(): Promise<void> {
    const mine = ++overridesSeq
    overridesError.value = null
    try {
      const result = await api.listPageOverrides(versionId())
      if (mine === overridesSeq) overrides.value = result.overrides
    } catch (cause) {
      if (mine === overridesSeq) overridesError.value = describe(cause, 'No se pudieron cargar las sustituciones.')
    }
  }

  async function mutate(run: () => Promise<PageOverrideResult>, fallback: string): Promise<boolean> {
    if (submitting.value) return false
    submitting.value = true
    formError.value = null
    try {
      notice.value = await run()
      await loadOverrides()
      return true
    } catch (cause) {
      if (cause instanceof AdminApiError) {
        formError.value = explainOverrideError(cause.code) ?? describe(cause, fallback)
      } else formError.value = fallback
      return false
    } finally {
      submitting.value = false
    }
  }

  const create = (input: PageOverrideInput): Promise<boolean> =>
    mutate(() => api.createPageOverride(versionId(), input), 'No se pudo guardar la sustitución.')
  const remove = (overrideId: string): Promise<boolean> =>
    mutate(() => api.deletePageOverride(versionId(), overrideId), 'No se pudo quitar la sustitución.')

  const dismissNotice = (): void => { notice.value = null }
  const clearFormError = (): void => { formError.value = null }

  return {
    pageNumber, text, loading, error, unavailable, overrides, overridesError,
    submitting, formError, notice,
    loadPage, loadOverrides, create, remove, dismissNotice, clearFormError,
  }
}

export type UsePageReview = ReturnType<typeof usePageReview>
