<script setup lang="ts">
import UTable from '@nuxt/ui/components/Table.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import type { TableColumn } from '@nuxt/ui'
import StatusBadge from './StatusBadge.vue'
import {
  ACTION_LABELS, SUSPECTED_START_FAILURE, allowedActions, documentTypeLabel, formatTimestamp, formatValidity, isStaleUploaded,
} from '../lib/versions'
import type { VersionAction, VersionRow } from '../types'

defineProps<{
  rows: readonly VersionRow[]
  busy: Readonly<Record<string, VersionAction | 'update'>>
}>()
const emit = defineEmits<{ detail: [row: VersionRow]; action: [row: VersionRow, action: VersionAction] }>()

const columns: TableColumn<VersionRow>[] = [
  { id: 'title', header: 'Título' },
  { id: 'type', header: 'Tipo' },
  { id: 'version', header: 'Versión' },
  { id: 'status', header: 'Estado' },
  { id: 'review', header: 'Revisión pendiente' },
  { id: 'attempts', header: 'Intentos' },
  { id: 'updated', header: 'Actualizada' },
  { id: 'validity', header: 'Vigencia' },
  { id: 'actions', header: 'Acciones' },
]
</script>

<template>
  <UTable
    :data="[...rows]"
    :columns="columns"
    class="max-h-[70vh] min-w-0"
  >
    <template #title-cell="{ row }">
      <span class="block max-w-xs font-medium text-highlighted">{{ row.original.document.title }}</span>
    </template>
    <template #type-cell="{ row }">
      {{ documentTypeLabel(row.original.document.document_type) }}
    </template>
    <template #version-cell="{ row }">
      {{ row.original.version.version_label ?? '—' }}
    </template>
    <template #status-cell="{ row }">
      <StatusBadge :status="row.original.version.status" />
      <p
        v-if="isStaleUploaded(row.original.version)"
        class="mt-1 max-w-48 text-xs text-warning"
      >
        {{ SUSPECTED_START_FAILURE }}
      </p>
    </template>
    <template #review-cell="{ row }">
      {{ row.original.version.requires_review ? 'Sí' : 'No' }}
    </template>
    <template #attempts-cell="{ row }">
      {{ row.original.version.processing_attempts }}
    </template>
    <template #updated-cell="{ row }">
      <span class="whitespace-nowrap">{{ formatTimestamp(row.original.version.updated_at) }}</span>
    </template>
    <template #validity-cell="{ row }">
      <span class="whitespace-nowrap">{{ formatValidity(row.original.version.effective_from, row.original.version.effective_until) }}</span>
    </template>
    <template #actions-cell="{ row }">
      <div class="flex flex-wrap gap-1">
        <UButton
          size="xs"
          color="neutral"
          variant="outline"
          label="Detalle"
          :aria-label="`Ver detalle de ${row.original.document.title}, versión ${row.original.version.version_label ?? 'sin etiqueta'}`"
          @click="emit('detail', row.original)"
        />
        <UButton
          v-for="action in allowedActions(row.original.version)"
          :key="action"
          size="xs"
          color="neutral"
          variant="outline"
          :label="ACTION_LABELS[action]"
          :aria-label="`${ACTION_LABELS[action]} ${row.original.document.title}, versión ${row.original.version.version_label ?? 'sin etiqueta'}`"
          :loading="busy[row.original.version.id] === action"
          :disabled="busy[row.original.version.id] !== undefined"
          @click="emit('action', row.original, action)"
        />
      </div>
    </template>
  </UTable>
</template>
