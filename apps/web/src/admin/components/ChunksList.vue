<script setup lang="ts">
import { computed, ref } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import { filterChunks, formatPages } from '../lib/corrections'
import type { Chunk, ChunkPage } from '../types'

const props = defineProps<{
  page: ChunkPage | null
  loading: boolean
  error: string | null
  canCorrect: boolean
}>()
const emit = defineEmits<{ goto: [offset: number]; correct: [chunk: Chunk, selection: string] }>()

const query = ref('')
const visible = computed(() => filterChunks(props.page?.chunks ?? [], query.value))
const from = computed(() => (props.page && props.page.total > 0 ? props.page.offset + 1 : 0))
const to = computed(() => (props.page ? props.page.offset + props.page.chunks.length : 0))
const hasPrev = computed(() => (props.page?.offset ?? 0) > 0)
const hasNext = computed(() => props.page !== null && props.page.offset + props.page.limit < props.page.total)

function onCorrect(chunk: Chunk): void {
  const selection = typeof window === 'undefined' ? '' : (window.getSelection()?.toString() ?? '')
  emit('correct', chunk, selection)
}
</script>

<template>
  <section aria-labelledby="chunks-title">
    <h3
      id="chunks-title"
      class="mb-2 text-sm font-semibold text-highlighted"
    >
      Texto extraído
    </h3>
    <UAlert
      v-if="error"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-alert"
      :description="error"
    />
    <div
      v-else-if="loading && page === null"
      class="space-y-3"
      aria-busy="true"
    >
      <USkeleton class="h-16 w-full" />
      <USkeleton class="h-16 w-full" />
    </div>
    <template v-else-if="page">
      <p
        v-if="page.total === 0"
        class="text-sm text-muted"
      >
        Esta versión aún no tiene texto extraído.
      </p>
      <template v-else>
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <UInput
            v-model="query"
            class="min-w-0 flex-1"
            size="sm"
            icon="i-lucide-search"
            placeholder="Filtrar en esta página de resultados"
            aria-label="Filtrar fragmentos cargados"
          />
          <p
            class="text-xs text-muted"
            aria-live="polite"
          >
            {{ from }}–{{ to }} de {{ page.total }}
          </p>
        </div>
        <p
          v-if="visible.length === 0"
          class="text-sm text-muted"
        >
          Ningún fragmento cargado coincide con el filtro.
        </p>
        <ol
          class="space-y-3"
          :aria-busy="loading"
        >
          <li
            v-for="chunk in visible"
            :key="chunk.id"
            class="rounded-md border border-default p-3"
          >
            <p class="mb-1 flex flex-wrap items-center gap-x-2 text-xs text-muted">
              <span class="font-medium text-default">#{{ chunk.ordinal }}</span>
              <span>{{ formatPages(chunk.page_start, chunk.page_end) }}</span>
              <span
                v-if="chunk.section"
                class="break-words"
              >{{ chunk.section }}</span>
            </p>
            <p class="whitespace-pre-line break-words text-sm text-default">
              {{ chunk.text }}
            </p>
            <UButton
              v-if="canCorrect"
              class="mt-2"
              size="xs"
              color="neutral"
              variant="outline"
              icon="i-lucide-pencil-line"
              label="Corregir texto de este fragmento"
              @click="onCorrect(chunk)"
            />
          </li>
        </ol>
        <div class="mt-3 flex items-center justify-between gap-2">
          <UButton
            size="sm"
            color="neutral"
            variant="outline"
            icon="i-lucide-chevron-left"
            label="Anterior"
            :disabled="!hasPrev || loading"
            @click="emit('goto', Math.max(0, page.offset - page.limit))"
          />
          <UButton
            size="sm"
            color="neutral"
            variant="outline"
            trailing-icon="i-lucide-chevron-right"
            label="Siguiente"
            :disabled="!hasNext || loading"
            @click="emit('goto', page.offset + page.limit)"
          />
        </div>
      </template>
    </template>
  </section>
</template>
