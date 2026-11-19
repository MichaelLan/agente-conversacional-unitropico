<script setup lang="ts">
import { computed, ref } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import UEmpty from '@nuxt/ui/components/Empty.vue'
import UDashboardGroup from '@nuxt/ui/components/DashboardGroup.vue'
import UDashboardPanel from '@nuxt/ui/components/DashboardPanel.vue'
import UDashboardNavbar from '@nuxt/ui/components/DashboardNavbar.vue'
import { useToast } from '@nuxt/ui/composables/useToast'
import AdminSidebar from './AdminSidebar.vue'
import DocumentsTable from './DocumentsTable.vue'
import UploadModal from './UploadModal.vue'
import ConfirmActionModal from './ConfirmActionModal.vue'
import VersionDetailSlideover from './VersionDetailSlideover.vue'
import { useAdminDocuments } from '../composables/useAdminDocuments'
import { STATUS_FILTERS, countByFilter, matchesFilter, type StatusFilter } from '../lib/versions'
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
const sidebarOpen = ref(false)
const filter = ref<StatusFilter>('all')
const counts = computed(() => countByFilter(docs.rows.value))
const visibleRows = computed(() => docs.rows.value.filter(row => matchesFilter(row.version.status, filter.value)))
const filterLabel = computed(() => (filter.value === 'all'
  ? 'Documentos'
  : STATUS_FILTERS.find(item => item.id === filter.value)?.label ?? 'Documentos'))

const detailBusy = computed(() => docs.detail.value !== null && docs.busy.value[docs.detail.value.version.id] !== undefined)
</script>

<template>
  <UDashboardGroup
    unit="rem"
    :persistent="false"
    class="h-dvh bg-muted ps-[env(safe-area-inset-left)] pe-[env(safe-area-inset-right)]"
  >
    <AdminSidebar
      v-model:open="sidebarOpen"
      v-model:filter="filter"
      :counts="counts"
      @upload="openUpload"
      @sign-out="session.signOut()"
    />

    <UDashboardPanel
      id="documentos"
      :ui="{
        root: 'bg-default lg:my-2 lg:me-2 lg:min-h-0 lg:rounded-xl lg:ring lg:ring-default',
        body: 'p-0 sm:p-0 gap-0 sm:gap-0',
      }"
    >
      <template #header>
        <UDashboardNavbar
          as="header"
          :ui="{ root: 'border-b-0 gap-2', title: 'text-sm font-medium text-highlighted', toggle: '-ms-1.5 size-11 justify-center lg:size-auto' }"
        >
          <template #title>
            <h1>{{ filterLabel }}</h1>
            <span
              v-if="docs.loaded.value"
              class="text-xs font-normal tabular-nums text-muted"
            >{{ visibleRows.length }}</span>
          </template>
          <template #right>
            <span
              v-if="docs.autoRefreshing.value && !docs.loadError.value"
              class="hidden items-center gap-1.5 text-xs text-muted sm:flex"
            >
              <span
                class="size-1.5 animate-pulse rounded-full bg-primary"
                aria-hidden="true"
              />
              Actualización automática
            </span>
            <UButton
              color="neutral"
              variant="ghost"
              icon="i-lucide-refresh-cw"
              aria-label="Actualizar listado"
              size="sm"
              class="size-11 justify-center lg:size-auto lg:px-2.5"
              :loading="docs.loading.value"
              @click="docs.load()"
            >
              <span class="sr-only lg:not-sr-only">Actualizar</span>
            </UButton>
            <UButton
              color="neutral"
              icon="i-lucide-upload"
              aria-label="Subir documento"
              class="min-h-11 lg:hidden"
              square
              @click="openUpload"
            />
          </template>
        </UDashboardNavbar>
      </template>

      <template #body>
        <p
          class="sr-only"
          aria-live="polite"
        >
          {{ docs.announcement.value }}
        </p>

        <main
          id="contenido"
          class="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-3 pb-[max(1rem,env(safe-area-inset-bottom))] pt-1 sm:px-6"
        >
          <div
            v-if="!docs.loaded.value && docs.loading.value"
            class="space-y-3"
            aria-busy="true"
            aria-label="Cargando documentos"
          >
            <USkeleton class="h-10 w-full" />
            <USkeleton class="h-14 w-full" />
            <USkeleton class="h-14 w-full" />
            <USkeleton class="h-14 w-5/6" />
          </div>
          <UAlert
            v-else-if="!docs.loaded.value"
            color="error"
            variant="subtle"
            icon="i-lucide-circle-alert"
            title="No se pudo cargar el listado"
            :description="docs.loadError.value ?? ''"
            :actions="[{ label: 'Reintentar', color: 'neutral', variant: 'outline', onClick: () => docs.load() }]"
          />
          <template v-else>
            <div
              aria-live="polite"
              class="empty:hidden"
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
                class="text-xs text-muted sm:sr-only"
              >
                Hay versiones en procesamiento: el listado se actualiza cada 10 segundos.
              </p>
            </div>
            <UEmpty
              v-if="docs.rows.value.length === 0"
              icon="i-lucide-file-up"
              title="Aún no hay documentos"
              description="Sube un PDF para registrar el primer documento y comenzar su procesamiento."
              variant="naked"
              class="flex-1 justify-center"
              :actions="[{ label: 'Subir documento', icon: 'i-lucide-upload', color: 'neutral', onClick: openUpload }]"
            />
            <UEmpty
              v-else-if="visibleRows.length === 0"
              icon="i-lucide-filter"
              :title="`No hay versiones en «${filterLabel}»`"
              description="Prueba con otro filtro o vuelve al listado completo."
              variant="naked"
              class="flex-1 justify-center"
              :actions="[{ label: 'Ver todos', color: 'neutral', variant: 'outline', onClick: () => { filter = 'all' } }]"
            />
            <DocumentsTable
              v-else
              :rows="visibleRows"
              :busy="docs.busy.value"
              class="md:overflow-hidden md:rounded-lg md:ring md:ring-default"
              @detail="openDetail"
              @action="askAction"
            />
          </template>

          <p class="mt-auto pt-2 text-xs text-muted">
            El token solo existe en la memoria de esta pestaña. La protección real
            de estas operaciones la aplica el servidor, no esta interfaz.
          </p>
        </main>
      </template>
    </UDashboardPanel>

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
  </UDashboardGroup>
</template>
