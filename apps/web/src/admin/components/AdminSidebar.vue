<script setup lang="ts">
import UDashboardSidebar from '@nuxt/ui/components/DashboardSidebar.vue'
import UDashboardSidebarCollapse from '@nuxt/ui/components/DashboardSidebarCollapse.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
import { STATUS_FILTERS, type StatusFilter } from '../lib/versions'

defineProps<{
  filter: StatusFilter
  counts: Readonly<Record<StatusFilter, number>>
}>()

const emit = defineEmits<{
  'update:filter': [filter: StatusFilter]
  upload: []
  signOut: []
}>()

const open = defineModel<boolean>('open', { default: false })
const collapsed = defineModel<boolean>('collapsed', { default: false })

/** Mismo patrón que la barra del chat: lista de texto simple, objetivo táctil ≥ 44 px en móvil. */
const ITEM = 'flex min-h-11 w-full items-center gap-2.5 rounded-md px-2 text-start text-sm text-muted hover:bg-elevated hover:text-highlighted focus-visible:outline-2 focus-visible:outline-primary aria-[current=page]:bg-primary/10 aria-[current=page]:font-medium aria-[current=page]:text-primary lg:min-h-9'

function upload(): void {
  emit('upload')
  open.value = false
}

function choose(id: StatusFilter): void {
  emit('update:filter', id)
  open.value = false
}
</script>

<template>
  <UDashboardSidebar
    id="admin-sidebar"
    v-model:open="open"
    v-model:collapsed="collapsed"
    collapsible
    :default-size="16"
    :min-size="14"
    :max-size="20"
    :collapsed-size="4"
    :ui="{ root: 'border-e-0', body: 'gap-5 pb-4', header: 'gap-2', toggle: '-ms-1.5 size-11 justify-center', footer: 'pb-[max(0.5rem,env(safe-area-inset-bottom))]' }"
  >
    <template #header="{ collapsed: isCollapsed }">
      <UIcon
        name="i-lucide-message-circle"
        class="size-5 shrink-0 text-primary"
        aria-hidden="true"
      />
      <span
        v-if="!isCollapsed"
        class="min-w-0 truncate text-sm font-semibold text-highlighted"
      >Administración</span>
      <UDashboardSidebarCollapse
        v-if="!isCollapsed"
        class="ms-auto"
      />
    </template>

    <template #default="{ collapsed: isCollapsed }">
      <UDashboardSidebarCollapse
        v-if="isCollapsed"
        class="self-center"
      />
      <UButton
        v-if="isCollapsed"
        icon="i-lucide-upload"
        aria-label="Subir documento"
        color="neutral"
        variant="ghost"
        square
        class="self-center"
        @click="upload"
      />
      <UButton
        v-else
        icon="i-lucide-upload"
        label="Subir documento"
        color="neutral"
        variant="ghost"
        class="-mx-2 min-h-11 justify-start lg:min-h-9"
        @click="upload"
      />

      <nav
        aria-label="Filtrar por estado"
        :class="isCollapsed ? 'flex flex-col items-center gap-1' : ''"
      >
        <h2
          v-if="!isCollapsed"
          class="mb-1 text-xs font-semibold text-highlighted"
        >
          Documentos
        </h2>
        <ul :class="isCollapsed ? 'flex flex-col items-center gap-1' : '-mx-2'">
          <li
            v-for="item in STATUS_FILTERS"
            :key="item.id"
          >
            <button
              type="button"
              :class="isCollapsed ? `${ITEM} size-9 min-h-9 justify-center px-0` : ITEM"
              :aria-current="item.id === filter ? 'page' : undefined"
              :aria-label="isCollapsed ? `${item.label} (${counts[item.id]})` : undefined"
              :title="isCollapsed ? item.label : undefined"
              @click="choose(item.id)"
            >
              <UIcon
                :name="item.icon"
                class="size-4 shrink-0"
                aria-hidden="true"
              />
              <template v-if="!isCollapsed">
                <span class="min-w-0 flex-1 truncate">{{ item.label }}</span>
                <span class="text-xs tabular-nums">{{ counts[item.id] }}</span>
              </template>
            </button>
          </li>
        </ul>
      </nav>
    </template>

    <template #footer="{ collapsed: isCollapsed }">
      <UButton
        v-if="isCollapsed"
        icon="i-lucide-log-out"
        aria-label="Cerrar sesión"
        color="neutral"
        variant="ghost"
        square
        class="mx-auto"
        @click="emit('signOut')"
      />
      <UButton
        v-else
        icon="i-lucide-log-out"
        label="Cerrar sesión"
        color="neutral"
        variant="ghost"
        class="-mx-2 min-h-11 w-[calc(100%+1rem)] justify-start lg:min-h-9"
        @click="emit('signOut')"
      />
    </template>
  </UDashboardSidebar>
</template>
