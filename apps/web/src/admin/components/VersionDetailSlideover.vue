<script setup lang="ts">
import { ref, watch } from 'vue'
import USlideover from '@nuxt/ui/components/Slideover.vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import MetadataForm from './MetadataForm.vue'
import StatusBadge from './StatusBadge.vue'
import {
  ACTION_LABELS, allowedActions, documentTypeLabel, formatBytes, formatTimestamp, formatValidity,
} from '../lib/versions'
import type { VersionAction, VersionDetail, VersionPatch, VersionRow } from '../types'

const props = defineProps<{
  detail: VersionDetail | null
  loading: boolean
  error: string | null
  candidates: readonly VersionRow[]
  saving: boolean
  saveError: string | null
  busy: boolean
}>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ save: [patch: VersionPatch]; action: [action: VersionAction] }>()

const editing = ref(false)
watch(open, isOpen => {
  if (!isOpen) editing.value = false
})
// Tras guardar con éxito el padre limpia el error; cerramos el formulario.
watch(() => props.saving, (now, before) => {
  if (before && !now && props.saveError === null) editing.value = false
})

const row = (): VersionRow | null => props.detail
  ? { document: props.detail.document, version: props.detail.version }
  : null

function rows(): [string, string][] {
  const { document, version } = props.detail ?? {}
  if (!document || !version) return []
  return [
    ['Documento', document.title],
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
    description="Metadatos, errores y registro de procesamiento."
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
            <UButton
              size="sm"
              color="neutral"
              variant="outline"
              icon="i-lucide-pencil"
              label="Editar metadatos"
              :disabled="busy"
              @click="editing = true"
            />
            <UButton
              v-for="action in allowedActions(detail.version)"
              :key="action"
              size="sm"
              color="neutral"
              variant="outline"
              :label="ACTION_LABELS[action]"
              :disabled="busy"
              @click="emit('action', action)"
            />
          </div>

          <UAlert
            v-if="detail.version.error_code || detail.error_detail"
            color="error"
            variant="subtle"
            icon="i-lucide-circle-alert"
            :title="detail.version.error_code ? `Error: ${detail.version.error_code}` : 'Error de procesamiento'"
          >
            <template
              v-if="detail.error_detail"
              #description
            >
              <p class="whitespace-pre-line break-words">
                {{ detail.error_detail }}
              </p>
            </template>
          </UAlert>

          <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <template
              v-for="[label, value] in rows()"
              :key="label"
            >
              <dt class="text-muted">
                {{ label }}
              </dt>
              <dd class="break-words text-default">
                {{ value }}
              </dd>
            </template>
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
              class="space-y-2 border-s border-default ps-4"
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
                  <span class="font-medium">{{ event.step }}</span> · {{ event.status }}
                  <span
                    v-if="event.error_code"
                    class="text-error"
                  > · {{ event.error_code }}</span>
                </p>
                <p
                  v-if="event.detail"
                  class="whitespace-pre-line break-words text-muted"
                >
                  {{ event.detail }}
                </p>
              </li>
            </ol>
          </section>
        </div>
      </template>
    </template>
  </USlideover>
</template>
