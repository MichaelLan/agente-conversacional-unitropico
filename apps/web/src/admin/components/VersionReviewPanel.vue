<script setup lang="ts">
import { ref, watch } from 'vue'
import ChunksList from './ChunksList.vue'
import CorrectionsPanel from './CorrectionsPanel.vue'
import PagesPanel from './PagesPanel.vue'
import { usePageReview } from '../composables/usePageReview'
import { useVersionReview } from '../composables/useVersionReview'
import { canEditCorrections, prefillFromChunk, type CorrectionDraft } from '../lib/corrections'
import type { AdminApi } from '../services/adminApi'
import type { Chunk, VersionDetail } from '../types'

const props = defineProps<{
  api: AdminApi
  describe: (error: unknown, fallback: string) => string
  detail: VersionDetail
  section: 'text' | 'pages' | 'corrections'
  busy: boolean
  reprocessable: boolean
}>()
const emit = defineEmits<{ changed: []; reprocess: []; section: [section: 'text' | 'pages' | 'corrections'] }>()

const review = useVersionReview({
  api: props.api,
  versionId: () => props.detail.version.id,
  describe: props.describe,
})
const pagesReview = usePageReview({
  api: props.api,
  versionId: () => props.detail.version.id,
  describe: props.describe,
})
const prefill = ref<{ draft: CorrectionDraft; pages: number[]; nonce: number } | null>(null)
let nonce = 0

void review.loadChunks(0)
void review.loadCorrections()
void pagesReview.loadPage(1)
void pagesReview.loadOverrides()

// El reproceso cambia texto y estado: se recarga solo cuando la versión cambia.
watch(() => props.detail.version.updated_at, () => {
  void review.loadChunks()
  void review.loadCorrections()
  void pagesReview.loadPage()
  void pagesReview.loadOverrides()
})

function onCorrect(chunk: Chunk, selection: string): void {
  prefill.value = { ...prefillFromChunk(chunk, selection), nonce: (nonce += 1) }
  emit('section', 'corrections')
}

async function create(input: Parameters<typeof review.create>[0]): Promise<void> {
  if (await review.create(input)) emit('changed')
}
async function remove(id: string): Promise<void> {
  if (await review.remove(id)) emit('changed')
}
</script>

<template>
  <div>
    <div v-show="section === 'text'">
      <ChunksList
        :page="review.page.value"
        :loading="review.chunksLoading.value"
        :error="review.chunksError.value"
        :can-correct="canEditCorrections(detail.version.status)"
        @goto="review.loadChunks($event)"
        @correct="onCorrect"
      />
    </div>
    <div v-show="section === 'pages'">
      <PagesPanel
        :review="pagesReview"
        :version="detail.version"
        :corrections="review.corrections.value"
        :reprocessable="reprocessable"
        :busy="busy"
        @changed="emit('changed')"
        @reprocess="emit('reprocess')"
      />
    </div>
    <div v-show="section === 'corrections'">
      <CorrectionsPanel
        :corrections="review.corrections.value"
        :loading="review.correctionsLoading.value"
        :error="review.correctionsError.value"
        :editable="canEditCorrections(detail.version.status)"
        :submitting="review.submitting.value"
        :form-error="review.formError.value"
        :notice="review.notice.value"
        :page-count="detail.version.page_count"
        :prefill="prefill"
        :reprocessable="reprocessable"
        :busy="busy"
        @create="create"
        @remove="remove"
        @reprocess="emit('reprocess')"
        @dismiss="review.dismissNotice()"
      />
    </div>
  </div>
</template>
