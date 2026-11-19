<script setup lang="ts">
import { computed } from 'vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UDropdownMenu from '@nuxt/ui/components/DropdownMenu.vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
import type { DropdownMenuItem } from '@nuxt/ui'
import StatusBadge from './StatusBadge.vue'
import {
  ACTION_LABELS, SUSPECTED_START_FAILURE, allowedActions, documentTypeLabel, formatTimestamp, formatValidity,
  groupByDocument, isStaleUploaded,
} from '../lib/versions'
import type { VersionAction, VersionRow } from '../types'

const props = defineProps<{
  rows: readonly VersionRow[]
  busy: Readonly<Record<string, VersionAction | 'update'>>
}>()
const emit = defineEmits<{ detail: [row: VersionRow]; action: [row: VersionRow, action: VersionAction] }>()

const groups = computed(() => groupByDocument(props.rows))

const ACTION_ICONS: Readonly<Record<VersionAction, string>> = {
  publish: 'i-lucide-globe',
  unpublish: 'i-lucide-eye-off',
  reprocess: 'i-lucide-rotate-ccw',
}

const label = (row: VersionRow): string => row.version.version_label ?? 'Sin etiqueta'
const target = (row: VersionRow): string => `${row.document.title}, versión ${row.version.version_label ?? 'sin etiqueta'}`

function menu(row: VersionRow): DropdownMenuItem[][] {
  const locked = props.busy[row.version.id] !== undefined
  const actions = allowedActions(row.version).map(action => ({
    label: ACTION_LABELS[action],
    icon: ACTION_ICONS[action],
    disabled: locked,
    onSelect: () => emit('action', row, action),
  }))
  const detail = [{ label: 'Ver detalle', icon: 'i-lucide-panel-right-open', onSelect: () => emit('detail', row) }]
  return actions.length > 0 ? [detail, actions] : [detail]
}
</script>

<template>
  <div class="min-w-0">
    <!-- Móvil (base): tarjetas compactas apiladas por documento. -->
    <div class="space-y-4 md:hidden">
      <section
        v-for="group in groups"
        :key="group.document.id"
        :aria-label="group.document.title"
        class="overflow-hidden rounded-lg ring ring-default"
      >
        <header class="bg-elevated/40 px-3 py-2.5">
          <h3 class="text-sm font-medium text-highlighted wrap-break-word">
            {{ group.document.title }}
          </h3>
          <p class="text-xs text-muted">
            {{ documentTypeLabel(group.document.document_type) }} · {{ group.rows.length }} {{ group.rows.length === 1 ? 'versión' : 'versiones' }}
          </p>
        </header>
        <ul class="divide-y divide-default">
          <li
            v-for="row in group.rows"
            :key="row.version.id"
            class="flex items-start gap-2 px-3 py-2"
          >
            <button
              type="button"
              class="min-h-11 min-w-0 flex-1 rounded-md py-1 text-start focus-visible:outline-2 focus-visible:outline-primary"
              :aria-label="`Ver detalle de ${target(row)}`"
              @click="emit('detail', row)"
            >
              <span class="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span class="text-sm font-medium text-default wrap-break-word">{{ label(row) }}</span>
                <StatusBadge :status="row.version.status" />
              </span>
              <span class="mt-1 block text-xs text-muted">
                {{ formatTimestamp(row.version.updated_at) }}
                <template v-if="row.version.requires_review"> · Revisión pendiente</template>
              </span>
              <span
                v-if="isStaleUploaded(row.version)"
                class="mt-1 block text-xs text-warning"
              >{{ SUSPECTED_START_FAILURE }}</span>
            </button>
            <UDropdownMenu
              :items="menu(row)"
              :content="{ align: 'end' }"
            >
              <UButton
                icon="i-lucide-ellipsis-vertical"
                color="neutral"
                variant="ghost"
                square
                class="size-11 justify-center"
                :loading="busy[row.version.id] !== undefined"
                :aria-label="`Acciones para ${target(row)}`"
              />
            </UDropdownMenu>
          </li>
        </ul>
      </section>
    </div>

    <!-- md+: tabla con versiones agrupadas bajo su documento; scroll horizontal contenido. -->
    <div class="hidden min-w-0 overflow-x-auto md:block">
      <table class="w-full min-w-3xl table-fixed border-separate border-spacing-0 text-sm">
        <caption class="sr-only">
          Documentos y sus versiones
        </caption>
        <colgroup>
          <col>
          <col class="w-48">
          <col class="w-24">
          <col class="w-48">
          <col class="w-56">
          <col class="w-16">
        </colgroup>
        <thead>
          <tr class="text-start text-xs text-muted">
            <th
              scope="col"
              class="border-b border-default py-2.5 ps-4 pe-3 text-start font-medium"
            >
              Versión
            </th>
            <th
              scope="col"
              class="border-b border-default px-3 py-2.5 text-start font-medium"
            >
              Estado
            </th>
            <th
              scope="col"
              class="border-b border-default px-3 py-2.5 text-end font-medium"
            >
              Intentos
            </th>
            <th
              scope="col"
              class="border-b border-default px-3 py-2.5 text-start font-medium"
            >
              Actualizada
            </th>
            <th
              scope="col"
              class="border-b border-default px-3 py-2.5 text-start font-medium"
            >
              Vigencia
            </th>
            <th
              scope="col"
              class="border-b border-default py-2.5 ps-3 pe-4 text-end font-medium"
            >
              <span class="sr-only">Acciones</span>
            </th>
          </tr>
        </thead>
        <tbody
          v-for="group in groups"
          :key="group.document.id"
        >
          <tr>
            <th
              scope="colgroup"
              colspan="6"
              class="border-b border-default bg-elevated/40 py-2.5 ps-4 pe-4 text-start font-normal"
            >
              <span class="flex items-center gap-2">
                <UIcon
                  name="i-lucide-file-text"
                  class="size-4 shrink-0 text-muted"
                  aria-hidden="true"
                />
                <span class="font-medium text-highlighted">{{ group.document.title }}</span>
                <span class="text-xs text-muted">· {{ documentTypeLabel(group.document.document_type) }}</span>
              </span>
            </th>
          </tr>
          <tr
            v-for="row in group.rows"
            :key="row.version.id"
            class="group/row hover:bg-elevated/30"
          >
            <td class="border-b border-default py-3 ps-10 pe-3 align-top wrap-break-word">
              <button
                type="button"
                class="rounded-sm text-start font-medium text-default hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-primary"
                :aria-label="`Ver detalle de ${target(row)}`"
                @click="emit('detail', row)"
              >
                {{ label(row) }}
              </button>
              <p
                v-if="row.version.requires_review"
                class="mt-0.5 text-xs text-muted"
              >
                Revisión pendiente
              </p>
            </td>
            <td class="border-b border-default px-3 py-3 align-top">
              <StatusBadge :status="row.version.status" />
              <p
                v-if="isStaleUploaded(row.version)"
                class="mt-1 max-w-56 text-xs text-warning"
              >
                {{ SUSPECTED_START_FAILURE }}
              </p>
            </td>
            <td class="border-b border-default px-3 py-3 text-end align-top tabular-nums text-muted">
              {{ row.version.processing_attempts }}
            </td>
            <td class="border-b border-default px-3 py-3 align-top whitespace-nowrap text-muted">
              {{ formatTimestamp(row.version.updated_at) }}
            </td>
            <td class="border-b border-default px-3 py-3 align-top whitespace-nowrap text-muted">
              {{ formatValidity(row.version.effective_from, row.version.effective_until) }}
            </td>
            <td class="border-b border-default py-2 ps-3 pe-4 text-end align-top">
              <UDropdownMenu
                :items="menu(row)"
                :content="{ align: 'end' }"
              >
                <UButton
                  icon="i-lucide-ellipsis"
                  color="neutral"
                  variant="ghost"
                  size="sm"
                  square
                  :loading="busy[row.version.id] !== undefined"
                  :aria-label="`Acciones para ${target(row)}`"
                />
              </UDropdownMenu>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
