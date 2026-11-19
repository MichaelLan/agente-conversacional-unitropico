<script setup lang="ts">
import UDashboardSidebar from '@nuxt/ui/components/DashboardSidebar.vue'
import UDashboardSidebarCollapse from '@nuxt/ui/components/DashboardSidebarCollapse.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
import { FAQ_CATEGORIES, FAQ_IS_PLACEHOLDER } from '../data/faq'

defineProps<{
  /** Pregunta frecuente seleccionada en la conversación actual. */
  active: string | null
}>()

const emit = defineEmits<{
  select: [question: string]
  reset: []
}>()

const open = defineModel<boolean>('open', { default: false })
const collapsed = defineModel<boolean>('collapsed', { default: false })
</script>

<template>
  <UDashboardSidebar
    id="preguntas-frecuentes"
    v-model:open="open"
    v-model:collapsed="collapsed"
    collapsible
    :default-size="17"
    :min-size="15"
    :max-size="20"
    :collapsed-size="4"
    :ui="{ root: 'border-e-0', body: 'gap-5 pb-4', header: 'gap-2' }"
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
      >Fomento Socioeconómico</span>
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
        icon="i-lucide-plus"
        aria-label="Nueva conversación"
        color="neutral"
        variant="ghost"
        square
        class="self-center"
        @click="emit('reset')"
      />
      <UButton
        v-else
        icon="i-lucide-plus"
        label="Nueva conversación"
        color="neutral"
        variant="ghost"
        class="-mx-2.5 justify-start"
        @click="emit('reset')"
      />

      <nav
        v-if="!isCollapsed"
        aria-label="Preguntas frecuentes"
        class="flex flex-col gap-5"
      >
        <section
          v-for="category in FAQ_CATEGORIES"
          :key="category.id"
          :aria-labelledby="`faq-${category.id}`"
        >
          <h2
            :id="`faq-${category.id}`"
            class="mb-1 text-xs font-semibold text-highlighted"
          >
            {{ category.label }}
          </h2>
          <ul>
            <li
              v-for="question in category.questions"
              :key="question"
            >
              <button
                type="button"
                :title="question"
                :aria-current="question === active ? 'true' : undefined"
                class="-mx-2 block w-[calc(100%+1rem)] truncate rounded-md px-2 py-1.5 text-start text-sm text-muted hover:bg-elevated hover:text-highlighted focus-visible:outline-2 focus-visible:outline-primary aria-[current=true]:bg-primary/10 aria-[current=true]:text-primary"
                @click="emit('select', question)"
              >
                {{ question }}
              </button>
            </li>
          </ul>
        </section>

        <p
          v-if="FAQ_IS_PLACEHOLDER"
          class="flex items-center gap-1.5 text-xs text-muted"
        >
          <UIcon
            name="i-lucide-info"
            class="size-3.5 shrink-0"
            aria-hidden="true"
          />
          Preguntas de ejemplo en revisión
        </p>
      </nav>
    </template>
  </UDashboardSidebar>
</template>
