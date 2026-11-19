<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UBadge from '@nuxt/ui/components/Badge.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import USkeleton from '@nuxt/ui/components/Skeleton.vue'
import PageOverrideEditor from './PageOverrideEditor.vue'
import {
  OVERRIDE_EDIT_HINT, OVERRIDE_PUBLISHED_MESSAGE, PAGE_TEXT_UNAVAILABLE_MESSAGE, SOURCE_LABELS,
  activeOverrideFor, canEditOverrides, parsePageInput, pageNeighbours,
} from '../lib/pages'
import { formatTimestamp } from '../lib/versions'
import type { UsePageReview } from '../composables/usePageReview'
import type { Correction, PageOverrideInput, VersionSummary } from '../types'

const props = defineProps<{
  review: UsePageReview
  version: VersionSummary
  corrections: readonly Correction[]
  reprocessable: boolean
  busy: boolean
}>()
const emit = defineEmits<{ changed: []; reprocess: [] }>()

const pageCount = computed(() => props.version.page_count)
const current = computed(() => props.review.pageNumber.value)
const neighbours = computed(() => pageNeighbours(current.value, pageCount.value ?? 1))
const editable = computed(() => canEditOverrides(props.version.status))
const active = computed(() => activeOverrideFor(props.review.overrides.value, current.value))
const origin = computed(() => {
  const id = props.review.text.value?.override_id ?? null
  return id === null ? null : props.review.overrides.value.find(item => item.id === id) ?? null
})
const applied = computed(() => {
  const ids = props.review.text.value?.corrections_applied ?? []
  return ids.map(id => props.corrections.find(item => item.id === id) ?? null).map((item, i) => ({ id: ids[i]!, item }))
})

const pageInput = ref(String(current.value))
watch(current, value => { pageInput.value = String(value) })
const inputError = ref<string | null>(null)

function go(page: number | null): void {
  if (page !== null) void props.review.loadPage(page)
}
function submitPage(): void {
  const page = parsePageInput(pageInput.value, pageCount.value ?? 0)
  if (page === null) {
    inputError.value = `Indica una página entre 1 y ${pageCount.value ?? '—'}.`
    return
  }
  inputError.value = null
  go(page)
}

const editorOpen = ref(false)
const confirmingRemove = ref(false)
const disabled = computed(() => !editable.value || props.review.submitting.value)

async function save(input: PageOverrideInput): Promise<void> {
  if (await props.review.create(input)) {
    editorOpen.value = false
    emit('changed')
  }
}
async function remove(): Promise<void> {
  if (active.value && await props.review.remove(active.value.id)) emit('changed')
  confirmingRemove.value = false
}
watch(current, () => { confirmingRemove.value = false })
</script>

<template>
  <section
    aria-labelledby="pages-title"
    class="space-y-4"
  >
    <h3
      id="pages-title"
      class="sr-only"
    >
      Páginas
    </h3>

    <UAlert
      v-if="pageCount === null"
      color="neutral"
      variant="subtle"
      icon="i-lucide-file-question"
      description="Aún no se conoce el número de páginas: la versión no ha terminado de procesarse."
    />
    <template v-else>
      <form
        class="space-y-1"
        novalidate
        @submit.prevent="submitPage"
      >
        <div class="flex items-end gap-2">
          <UButton
            type="button"
            color="neutral"
            variant="outline"
            icon="i-lucide-chevron-left"
            aria-label="Página anterior"
            class="min-h-11 min-w-11 justify-center sm:min-h-9 sm:min-w-9"
            :disabled="neighbours.prev === null || review.loading.value"
            @click="go(neighbours.prev)"
          />
          <label class="min-w-0 flex-1 text-sm">
            <span class="mb-1 block font-medium text-default">Página (de {{ pageCount }})</span>
            <input
              v-model="pageInput"
              type="text"
              inputmode="numeric"
              class="min-h-11 w-full rounded-md bg-default px-2.5 text-center text-base ring ring-accented focus-visible:outline-2 focus-visible:outline-primary sm:min-h-9 sm:text-sm"
              :aria-invalid="inputError !== null"
              aria-describedby="page-input-error"
              @blur="submitPage"
            >
          </label>
          <UButton
            type="button"
            color="neutral"
            variant="outline"
            icon="i-lucide-chevron-right"
            aria-label="Página siguiente"
            class="min-h-11 min-w-11 justify-center sm:min-h-9 sm:min-w-9"
            :disabled="neighbours.next === null || review.loading.value"
            @click="go(neighbours.next)"
          />
        </div>
        <p
          id="page-input-error"
          class="min-h-4 text-xs text-error"
          role="alert"
        >
          {{ inputError }}
        </p>
      </form>

      <UAlert
        v-if="review.notice.value"
        color="success"
        variant="subtle"
        icon="i-lucide-circle-check"
        close
        @update:open="review.dismissNotice()"
      >
        <template #description>
          <p>{{ review.notice.value.message }}</p>
          <p
            v-if="review.notice.value.corrections_dirty"
            class="mt-1 font-medium"
          >
            Correcciones pendientes: reprocesa antes de revisar o publicar. El texto de abajo se actualizará al reprocesar.
          </p>
        </template>
        <template
          v-if="review.notice.value.reprocess_required && reprocessable"
          #actions
        >
          <UButton
            size="xs"
            color="neutral"
            variant="outline"
            label="Reprocesar ahora"
            class="min-h-11 sm:min-h-0"
            :disabled="busy"
            @click="emit('reprocess')"
          />
        </template>
      </UAlert>

      <div
        v-if="review.loading.value && !review.text.value"
        class="space-y-2"
        aria-busy="true"
      >
        <USkeleton class="h-4 w-full" />
        <USkeleton class="h-4 w-5/6" />
        <USkeleton class="h-4 w-2/3" />
      </div>
      <UAlert
        v-else-if="review.unavailable.value"
        color="warning"
        variant="subtle"
        icon="i-lucide-file-clock"
        title="Texto por página no disponible"
        :description="PAGE_TEXT_UNAVAILABLE_MESSAGE"
      >
        <template
          v-if="reprocessable"
          #actions
        >
          <UButton
            size="xs"
            color="neutral"
            variant="outline"
            label="Reprocesar"
            class="min-h-11 sm:min-h-0"
            :disabled="busy"
            @click="emit('reprocess')"
          />
        </template>
      </UAlert>
      <UAlert
        v-else-if="review.error.value"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        :description="review.error.value"
      />
      <template v-else-if="review.text.value">
        <div
          class="space-y-2"
          :aria-busy="review.loading.value"
        >
          <p class="flex flex-wrap items-center gap-2 text-sm">
            <UBadge
              :color="review.text.value.source === 'override' ? 'warning' : 'neutral'"
              variant="soft"
              :label="SOURCE_LABELS[review.text.value.source]"
            />
            <span class="text-xs text-muted">Página {{ review.text.value.page_number }} de {{ review.text.value.page_count }}</span>
          </p>
          <p
            v-if="origin"
            class="wrap-break-word text-xs text-muted"
          >
            Sustituido el {{ formatTimestamp(origin.created_at) }} · Motivo: {{ origin.reason }}
          </p>
          <pre
            tabindex="0"
            aria-label="Texto preparado de la página"
            class="max-h-[60dvh] overflow-auto rounded-lg bg-elevated/40 p-3 font-sans text-sm leading-6 wrap-break-word whitespace-pre-wrap ring ring-default focus-visible:outline-2 focus-visible:outline-primary"
          >{{ review.text.value.text }}</pre>
        </div>

        <div
          v-if="applied.length > 0"
          class="space-y-2"
        >
          <h4 class="text-sm font-medium text-highlighted">
            Correcciones literales aplicadas en esta página
          </h4>
          <ul class="divide-y divide-default rounded-lg text-sm ring ring-default">
            <li
              v-for="entry in applied"
              :key="entry.id"
              class="space-y-1 px-3 py-2.5"
            >
              <template v-if="entry.item">
                <p class="flex flex-wrap items-center gap-2 wrap-break-word">
                  <span class="rounded bg-error/10 px-1.5 py-0.5 font-mono text-[0.8125rem] line-through decoration-error/60">{{ entry.item.find_text }}</span>
                  <span class="sr-only">se reemplaza por</span>
                  <span aria-hidden="true">→</span>
                  <span
                    v-if="entry.item.replace_text === ''"
                    class="italic text-muted"
                  >(eliminar)</span>
                  <span
                    v-else
                    class="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[0.8125rem]"
                  >{{ entry.item.replace_text }}</span>
                </p>
                <p class="wrap-break-word text-xs text-muted">
                  Motivo: {{ entry.item.reason }}
                </p>
              </template>
              <p
                v-else
                class="font-mono text-xs text-muted"
              >
                {{ entry.id }}
              </p>
            </li>
          </ul>
        </div>
      </template>

      <UAlert
        v-if="review.overridesError.value"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        :description="review.overridesError.value"
      />

      <section
        class="space-y-3 rounded-lg bg-elevated/40 p-4 ring ring-default"
        aria-labelledby="override-title"
      >
        <h4
          id="override-title"
          class="text-sm font-medium text-highlighted"
        >
          Sustitución de la página {{ current }}
        </h4>
        <UAlert
          v-if="!editable"
          color="warning"
          variant="subtle"
          icon="i-lucide-lock"
          :description="OVERRIDE_PUBLISHED_MESSAGE"
        />
        <UAlert
          v-if="review.formError.value && !editorOpen"
          color="error"
          variant="subtle"
          icon="i-lucide-circle-alert"
          :description="review.formError.value"
        />

        <template v-if="active">
          <p class="text-sm text-default">
            Esta página tiene una sustitución activa
            <span class="text-muted">· creada {{ formatTimestamp(active.created_at) }}</span>
          </p>
          <p class="wrap-break-word text-sm text-muted">
            Motivo: {{ active.reason }}
          </p>
          <p class="text-xs text-muted">
            {{ OVERRIDE_EDIT_HINT }}
          </p>
          <UButton
            v-if="!confirmingRemove"
            color="error"
            variant="outline"
            icon="i-lucide-trash-2"
            label="Quitar sustitución"
            class="min-h-11 sm:min-h-0"
            :disabled="disabled"
            @click="confirmingRemove = true"
          />
          <div
            v-else
            class="flex flex-wrap items-center gap-2 rounded-md bg-error/5 p-2"
          >
            <span class="text-xs text-default">¿Quitar la sustitución? Volverá el texto OCR y la versión pasará a revisión.</span>
            <UButton
              size="xs"
              color="error"
              label="Sí, quitar"
              class="min-h-11 sm:min-h-0"
              :loading="review.submitting.value"
              :disabled="disabled"
              @click="remove"
            />
            <UButton
              size="xs"
              color="neutral"
              variant="ghost"
              label="Cancelar"
              class="min-h-11 sm:min-h-0"
              @click="confirmingRemove = false"
            />
          </div>
        </template>
        <template v-else>
          <p class="text-xs text-muted">
            Úsala cuando las correcciones literales no bastan (diagramas, tablas como imagen, texto inventado por el OCR).
          </p>
          <UButton
            color="neutral"
            icon="i-lucide-file-pen-line"
            label="Sustituir texto de la página"
            class="min-h-11 sm:min-h-0"
            :disabled="disabled"
            @click="review.clearFormError(); editorOpen = true"
          />
        </template>
      </section>

      <PageOverrideEditor
        v-model:open="editorOpen"
        :page-number="current"
        :page-count="pageCount"
        :initial-text="review.text.value?.text ?? ''"
        :submitting="review.submitting.value"
        :error="review.formError.value"
        @save="save"
      />
    </template>
  </section>
</template>
