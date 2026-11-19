<script setup lang="ts">
import { computed, ref } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import { filterChunks, formatPages, selectionWithin } from '../lib/corrections'
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

const items = new Map<string, HTMLElement>()

function setItem(id: string, element: unknown): void {
  if (element instanceof HTMLElement) items.set(id, element)
  else items.delete(id)
}

function onCorrect(chunk: Chunk): void {
  const selection = typeof window === 'undefined' ? '' : selectionWithin(items.get(chunk.id), window.getSelection())
  emit('correct', chunk, selection)
}
</script>

<template>
  <section aria-labelledby="chunks-title">
    <h3
      id="chunks-title"
      class="sr-only"
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
            icon="i-lucide-search"
            placeholder="Filtrar fragmentos"
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
          class="divide-y divide-default rounded-lg ring ring-default"
          :aria-busy="loading"
        >
          <li
            v-for="chunk in visible"
            :key="chunk.id"
            :ref="(element: unknown) => setItem(chunk.id, element)"
            class="px-4 py-3.5"
          >
            <div class="mb-1.5 flex items-center gap-2">
              <p class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 text-xs text-muted">
                <span class="font-medium tabular-nums text-default">#{{ chunk.ordinal }}</span>
                <span>{{ formatPages(chunk.page_start, chunk.page_end) }}</span>
                <span
                  v-if="chunk.section"
                  class="wrap-break-word"
                >· {{ chunk.section }}</span>
              </p>
              <UButton
                v-if="canCorrect"
                size="xs"
                color="neutral"
                variant="ghost"
                icon="i-lucide-pencil-line"
                class="-me-2 min-h-11 shrink-0 sm:min-h-0"
                label="Corregir"
                aria-label="Corregir texto de este fragmento"
                @click="onCorrect(chunk)"
              />
            </div>
            <p class="max-w-prose whitespace-pre-line wrap-break-word text-[0.9375rem]/7 text-default">
              {{ chunk.text }}
            </p>
          </li>
        </ol>
        <div class="mt-3 flex items-center justify-between gap-2">
          <UButton
            size="sm"
            color="neutral"
            variant="outline"
            class="min-h-11 sm:min-h-0"
            icon="i-lucide-chevron-left"
            label="Anterior"
            :disabled="!hasPrev || loading"
            @click="emit('goto', Math.max(0, page.offset - page.limit))"
          />
          <UButton
            size="sm"
            color="neutral"
            variant="outline"
            class="min-h-11 sm:min-h-0"
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
