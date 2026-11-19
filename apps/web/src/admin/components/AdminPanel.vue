<script setup lang="ts">
import { computed, ref } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import UEmpty from '@nuxt/ui/components/Empty.vue'
import { useToast } from '@nuxt/ui/composables/useToast'
import DocumentsTable from './DocumentsTable.vue'
import UploadModal from './UploadModal.vue'
import ConfirmActionModal from './ConfirmActionModal.vue'
import VersionDetailSlideover from './VersionDetailSlideover.vue'
import { useAdminDocuments } from '../composables/useAdminDocuments'
import type { AdminSession } from '../composables/useAdminSession'
import type { ActionResult, UploadInput, VersionAction, VersionPatch, VersionRow } from '../types'

const props = defineProps<{ session: AdminSession }>()

const docs = useAdminDocuments({ session: props.session })
const toast = useToast()

const uploadOpen = ref(false)
const uploadError = ref<string | null>(null)
const confirmOpen = ref(false)
const pending = ref<{ row: VersionRow; action: VersionAction } | null>(null)
const detailOpen = ref(false)
const saveError = ref<string | null>(null)
const saving = computed(() => docs.detail.value !== null && docs.busy.value[docs.detail.value.version.id] === 'update')

void docs.load()

function notify(result: ActionResult): void {
  toast.add({
    title: result.message,
    color: result.ok ? 'success' : 'error',
    icon: result.ok ? 'i-lucide-circle-check' : 'i-lucide-circle-alert',
  })
}

async function submitUpload(meta: UploadInput, file: File): Promise<void> {
  uploadError.value = null
  const outcome = await docs.upload(meta, file)
  if (outcome.result !== null) {
    uploadOpen.value = false
    notify(outcome)
  } else {
    uploadError.value = outcome.message
  }
}

function openUpload(): void {
  uploadError.value = null
  uploadOpen.value = true
}

function askAction(row: VersionRow, action: VersionAction): void {
  pending.value = { row, action }
  confirmOpen.value = true
}

async function confirmAction(): Promise<void> {
  const current = pending.value
  confirmOpen.value = false
  if (current === null) return
  notify(await docs.act(current.row.version.id, current.action))
}

async function openDetail(row: VersionRow): Promise<void> {
  saveError.value = null
  detailOpen.value = true
  await docs.openDetail(row.version.id)
}

async function save(patch: VersionPatch): Promise<void> {
  const id = docs.detail.value?.version.id
  if (id === undefined) return
  saveError.value = null
  const result = await docs.update(id, patch)
  if (result.ok) notify(result)
  else saveError.value = result.message
}

function detailAction(action: VersionAction): void {
  const detail = docs.detail.value
  if (detail) askAction({ document: detail.document, version: detail.version }, action)
}

function onDetailOpen(value: boolean): void {
  detailOpen.value = value
  if (!value) docs.closeDetail()
}

const existingDocuments = computed(() => docs.documents.value.map(document => ({
  id: document.id,
  title: document.title,
  document_type: document.document_type,
  created_at: document.created_at,
})))
const detailBusy = computed(() => docs.detail.value !== null && docs.busy.value[docs.detail.value.version.id] !== undefined)
</script>

<template>
  <div class="mx-auto flex min-h-dvh max-w-7xl flex-col gap-4 px-4 py-6 sm:px-6">
    <header class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-highlighted">
          Administración de documentos
        </h1>
        <p class="text-sm text-muted">
          Asistente de Fomento Socioeconómico · Unitrópico
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <UButton
          color="neutral"
          variant="outline"
          icon="i-lucide-refresh-cw"
          label="Actualizar"
          :loading="docs.loading.value"
          @click="docs.load()"
        />
        <UButton
          icon="i-lucide-upload"
          label="Subir documento"
          @click="openUpload"
        />
        <UButton
          color="neutral"
          variant="ghost"
          icon="i-lucide-log-out"
          label="Cerrar sesión"
          @click="session.signOut()"
        />
      </div>
    </header>

    <p
      class="sr-only"
      aria-live="polite"
    >
      {{ docs.announcement.value }}
    </p>

    <main class="min-w-0 flex-1 rounded-lg border border-default bg-default">
      <div
        v-if="!docs.loaded.value && docs.loading.value"
        class="space-y-3 p-4"
        aria-busy="true"
        aria-label="Cargando documentos"
      >
        <USkeleton class="h-8 w-full" />
        <USkeleton class="h-8 w-full" />
        <USkeleton class="h-8 w-full" />
      </div>
      <div
        v-else-if="!docs.loaded.value"
        class="p-4"
      >
        <UAlert
          color="error"
          variant="subtle"
          icon="i-lucide-circle-alert"
          title="No se pudo cargar el listado"
          :description="docs.loadError.value ?? ''"
          :actions="[{ label: 'Reintentar', color: 'neutral', variant: 'outline', onClick: () => docs.load() }]"
        />
      </div>
      <template v-else>
        <div
          class="px-4 pt-3"
          aria-live="polite"
        >
          <UAlert
            v-if="docs.loadError.value"
            color="warning"
            variant="subtle"
            icon="i-lucide-triangle-alert"
            title="El listado puede estar desactualizado"
            :description="docs.loadError.value"
          />
          <p
            v-else-if="docs.autoRefreshing.value"
            class="text-xs text-muted"
          >
            Hay versiones en procesamiento: el listado se actualiza cada 10 segundos.
          </p>
        </div>
        <UEmpty
          v-if="docs.rows.value.length === 0"
          icon="i-lucide-file-text"
          title="Aún no hay documentos"
          description="Sube un PDF para registrar el primer documento y comenzar su procesamiento."
          :actions="[{ label: 'Subir documento', onClick: openUpload }]"
        />
        <div
          v-else
          class="min-w-0 overflow-x-auto"
        >
          <DocumentsTable
            :rows="docs.rows.value"
            :busy="docs.busy.value"
            @detail="openDetail"
            @action="askAction"
          />
        </div>
      </template>
    </main>

    <p class="text-xs text-muted">
      El token solo existe en la memoria de esta pestaña. La protección real
      de estas operaciones la aplica el servidor, no esta interfaz.
    </p>

    <UploadModal
      v-model:open="uploadOpen"
      :documents="existingDocuments"
      :uploading="docs.uploading.value"
      :error="uploadError"
      @submit="submitUpload"
    />
    <ConfirmActionModal
      v-model:open="confirmOpen"
      :action="pending?.action ?? null"
      :row="pending?.row ?? null"
      @confirm="confirmAction"
    />
    <VersionDetailSlideover
      :open="detailOpen"
      :detail="docs.detail.value"
      :loading="docs.detailLoading.value"
      :error="docs.detailError.value"
      :candidates="docs.rows.value"
      :saving="saving"
      :save-error="saveError"
      :busy="detailBusy"
      :api="docs.client"
      :describe="docs.describe"
      @update:open="onDetailOpen"
      @save="save"
      @action="detailAction"
      @changed="docs.refreshDetail($event)"
    />
  </div>
</template>
