<script setup lang="ts">
import { ref, watch } from 'vue'
import USlideover from '@nuxt/ui/components/Slideover.vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import UTabs from '@nuxt/ui/components/Tabs.vue'
import { DETAIL_SLIDEOVER_UI } from '../lib/ui'
import MetadataForm from './MetadataForm.vue'
import VersionReviewPanel from './VersionReviewPanel.vue'
import StatusBadge from './StatusBadge.vue'
import {
  ACTION_LABELS, CORRECTIONS_PENDING_NOTICE, actionBlockReason, allowedActions, hasPendingCorrections, documentTypeLabel, formatBytes, formatTimestamp, formatValidity,
} from '../lib/versions'
import { eventDetailText, explainCorrectionError, stepLabel } from '../lib/corrections'
import type { AdminApi } from '../services/adminApi'
import type { VersionAction, VersionDetail, VersionPatch, VersionRow } from '../types'

const props = defineProps<{
  detail: VersionDetail | null
  loading: boolean
  error: string | null
  candidates: readonly VersionRow[]
  saving: boolean
  saveError: string | null
  busy: boolean
  api: AdminApi
  describe: (error: unknown, fallback: string) => string
}>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ save: [patch: VersionPatch]; action: [action: VersionAction]; changed: [id: string] }>()

type Tab = 'summary' | 'text' | 'pages' | 'corrections'
const TABS = [
  { value: 'summary', label: 'Resumen' },
  { value: 'text', label: 'Texto extraído' },
  { value: 'pages', label: 'Páginas' },
  { value: 'corrections', label: 'Correcciones' },
] satisfies { value: Tab; label: string }[]
const isTab = (value: unknown): value is Tab => TABS.some(item => item.value === value)
function selectTab(value: string | number): void {
  if (isTab(value)) tab.value = value
}
const tab = ref<Tab>('summary')
watch(() => props.detail?.version.id, () => { tab.value = 'summary' })
const reviewSection = (): 'text' | 'pages' | 'corrections' => (tab.value === 'text' || tab.value === 'pages' ? tab.value : 'corrections')

const editing = ref(false)
watch(open, isOpen => {
  if (!isOpen) {
    editing.value = false
    tab.value = 'summary'
  }
})
// Tras guardar con éxito el padre limpia el error; cerramos el formulario.
watch(() => props.saving, (now, before) => {
  if (before && !now && props.saveError === null) editing.value = false
})

const reviewVisited = ref(false)
watch(tab, value => { if (value !== 'summary') reviewVisited.value = true })
watch(() => props.detail?.version.id, () => { reviewVisited.value = false })

const row = (): VersionRow | null => props.detail
  ? { document: props.detail.document, version: props.detail.version }
  : null

function rows(): [string, string][] {
  const { document, version } = props.detail ?? {}
  if (!document || !version) return []
  return [
    ['Tipo', documentTypeLabel(document.document_type)],
    ['Versión', version.version_label ?? '—'],
    ['Revisión pendiente', version.requires_review ? 'Sí' : 'No'],
    ['Intentos de procesamiento', String(version.processing_attempts)],
    ['Emisión', version.issued_at ?? '—'],
    ['Vigencia', formatValidity(version.effective_from, version.effective_until)],
    ['Sustituye a', version.supersedes_version_id ?? '—'],
    ['Páginas', version.page_count === null ? '—' : String(version.page_count)],
    ['Tamaño', formatBytes(version.source_bytes)],
    ['SHA-256', version.source_sha256],
    ['Creada', formatTimestamp(version.created_at)],
    ['Actualizada', formatTimestamp(version.updated_at)],
    ['Publicada', formatTimestamp(version.published_at)],
  ]
}
</script>

<template>
  <USlideover
    v-model:open="open"
    :title="detail ? detail.document.title : 'Detalle de versión'"
    :description="detail ? `Versión ${detail.version.version_label ?? 'sin etiqueta'} · ${documentTypeLabel(detail.document.document_type)}` : 'Metadatos, errores y registro de procesamiento.'"
    :ui="DETAIL_SLIDEOVER_UI"
  >
    <template #body>
      <div
        v-if="loading"
        class="space-y-3"
        aria-busy="true"
      >
        <USkeleton class="h-6 w-1/2" />
        <USkeleton class="h-4 w-full" />
        <USkeleton class="h-4 w-5/6" />
        <USkeleton class="h-4 w-2/3" />
      </div>
      <UAlert
        v-else-if="error"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        :description="error"
      />
      <template v-else-if="detail">
        <MetadataForm
          v-if="editing"
          :row="row()!"
          :candidates="candidates"
          :saving="saving"
          :error="saveError"
          @save="emit('save', $event)"
          @cancel="editing = false"
        />
        <div
          v-else
          class="space-y-6"
        >
          <div class="flex flex-wrap items-center gap-2">
            <StatusBadge :status="detail.version.status" />
            <span
              v-if="detail.version.requires_review"
              class="text-xs text-muted"
            >Revisión pendiente</span>
            <div class="flex w-full flex-wrap gap-2 sm:ms-auto sm:w-auto">
              <UButton
                color="neutral"
                variant="outline"
                icon="i-lucide-pencil"
                label="Editar metadatos"
                class="min-h-11 sm:min-h-0"
                size="sm"
                :disabled="busy"
                @click="editing = true"
              />
              <UButton
                v-for="action in allowedActions(detail.version)"
                :key="action"
                size="sm"
                color="neutral"
                :variant="action === 'publish' ? 'solid' : 'outline'"
                class="min-h-11 sm:min-h-0"
                :label="ACTION_LABELS[action]"
                :disabled="busy || actionBlockReason(detail.version, action) !== null"
                :aria-describedby="actionBlockReason(detail.version, action) ? 'corrections-pending-notice' : undefined"
                @click="emit('action', action)"
              />
            </div>
          </div>

          <p
            v-if="hasPendingCorrections(detail.version)"
            id="corrections-pending-notice"
            class="text-sm text-warning"
          >
            {{ CORRECTIONS_PENDING_NOTICE }}. Publicar y «Marcar como revisado» están deshabilitados hasta entonces.
          </p>

          <UTabs
            :model-value="tab"
            :items="TABS"
            :content="false"
            variant="link"
            color="neutral"
            aria-label="Secciones del detalle"
            class="-mx-4 sm:-mx-6"
            :ui="{ list: 'px-4 sm:px-6 overflow-x-auto', trigger: 'min-h-11 sm:min-h-0 shrink-0' }"
            @update:model-value="selectTab"
          />

          <VersionReviewPanel
            v-if="tab !== 'summary' || reviewVisited"
            v-show="tab !== 'summary'"
            :api="api"
            :describe="describe"
            :detail="detail"
            :section="reviewSection()"
            :busy="busy"
            :reprocessable="allowedActions(detail.version).includes('reprocess')"
            @changed="emit('changed', detail.version.id)"
            @reprocess="emit('action', 'reprocess')"
            @section="tab = $event"
          />

          <div
            v-show="tab === 'summary'"
            class="space-y-6"
          >
            <UAlert
              v-if="detail.version.error_code || detail.error_detail"
              color="error"
              variant="subtle"
              icon="i-lucide-circle-alert"
              :title="detail.version.error_code ? `Error: ${detail.version.error_code}` : 'Error de procesamiento'"
            >
              <template #description>
                <p
                  v-if="explainCorrectionError(detail.version.error_code, detail.error_detail)"
                  class="break-words"
                >
                  {{ explainCorrectionError(detail.version.error_code, detail.error_detail) }}
                </p>
                <p
                  v-if="detail.error_detail"
                  class="whitespace-pre-line break-words"
                >
                  {{ detail.error_detail }}
                </p>
              </template>
            </UAlert>

            <dl class="divide-y divide-default rounded-lg text-sm ring ring-default">
              <div
                v-for="[label, value] in rows()"
                :key="label"
                class="grid gap-0.5 px-3 py-2.5 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4"
              >
                <dt class="text-muted">
                  {{ label }}
                </dt>
                <dd
                  class="text-default"
                  :class="label === 'SHA-256' || label === 'Sustituye a' ? 'break-all font-mono text-xs leading-5' : 'wrap-break-word'"
                >
                  {{ value }}
                </dd>
              </div>
            </dl>

            <section aria-labelledby="workflow-title">
              <h3
                id="workflow-title"
                class="mb-2 text-sm font-semibold text-highlighted"
              >
                Instancia de Workflow
              </h3>
              <p
                v-if="detail.workflow === null"
                class="text-sm text-muted"
              >
                Sin instancia registrada.
              </p>
              <dl
                v-else
                class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm"
              >
                <dt class="text-muted">
                  Instancia
                </dt>
                <dd class="break-all">
                  {{ detail.workflow.instance_id }}
                </dd>
                <dt class="text-muted">
                  Estado
                </dt>
                <dd>{{ detail.workflow.status }}</dd>
                <template v-if="detail.workflow.error_name || detail.workflow.error_message">
                  <dt class="text-muted">
                    Error
                  </dt>
                  <dd class="break-words text-error">
                    {{ [detail.workflow.error_name, detail.workflow.error_message].filter(Boolean).join(': ') }}
                  </dd>
                </template>
              </dl>
            </section>

            <section aria-labelledby="events-title">
              <h3
                id="events-title"
                class="mb-2 text-sm font-semibold text-highlighted"
              >
                Eventos de procesamiento
              </h3>
              <p
                v-if="detail.processing_events.length === 0"
                class="text-sm text-muted"
              >
                Aún no hay eventos.
              </p>
              <ol
                v-else
                class="space-y-3 border-s border-default ps-4"
              >
                <li
                  v-for="event in detail.processing_events"
                  :key="event.id"
                  class="text-sm"
                >
                  <p class="text-xs text-muted">
                    {{ formatTimestamp(event.created_at) }}
                  </p>
                  <p class="text-default">
                    <span class="font-medium">{{ stepLabel(event.step) }}</span> · {{ event.status }}
                    <span
                      v-if="event.error_code"
                      class="text-error"
                    > · {{ event.error_code }}</span>
                  </p>
                  <p
                    v-if="eventDetailText(event)"
                    class="whitespace-pre-line break-words text-muted"
                  >
                    {{ eventDetailText(event) }}
                  </p>
                </li>
              </ol>
            </section>
          </div>
        </div>
      </template>
    </template>
  </USlideover>
</template>
