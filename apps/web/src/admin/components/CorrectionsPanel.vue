<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import UTextarea from '@nuxt/ui/components/Textarea.vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
import UBadge from '@nuxt/ui/components/Badge.vue'
import {
  FIND_MAX, MATCH_RULE_HELP, PUBLISHED_BLOCK_MESSAGE, REASON_MAX, REPLACE_MAX,
  draftToInput, emptyDraft, sortCorrections, validateDraft,
  type CorrectionDraft,
} from '../lib/corrections'
import { formatTimestamp } from '../lib/versions'
import type { Correction, CorrectionInput, CorrectionResult } from '../types'

const props = defineProps<{
  corrections: readonly Correction[]
  loading: boolean
  error: string | null
  editable: boolean
  submitting: boolean
  formError: string | null
  notice: CorrectionResult | null
  pageCount: number | null
  /** Precarga pedida desde un chunk; `nonce` cambia en cada petición. */
  prefill: { draft: CorrectionDraft; pages: number[]; nonce: number } | null
  reprocessable: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  create: [input: CorrectionInput]
  remove: [id: string]
  reprocess: []
  dismiss: []
}>()

const draft = ref<CorrectionDraft>(emptyDraft())
const pages = ref<number[]>([])
const submitted = ref(false)
const confirming = ref<string | null>(null)
const errors = computed(() => validateDraft(draft.value, props.pageCount))
const sorted = computed(() => sortCorrections(props.corrections))
const disabled = computed(() => !props.editable || props.submitting)

watch(() => props.prefill?.nonce, () => {
  if (!props.prefill) return
  draft.value = { ...props.prefill.draft }
  pages.value = props.prefill.pages.length > 1 ? props.prefill.pages : []
  submitted.value = false
})

function submit(): void {
  submitted.value = true
  if (disabled.value || Object.keys(errors.value).length > 0) return
  emit('create', draftToInput(draft.value))
}

// Tras crear con éxito (aviso nuevo) se limpia el formulario.
watch(() => props.notice, (now, before) => {
  if (now && now !== before && now.correction.active) {
    draft.value = emptyDraft()
    pages.value = []
    submitted.value = false
  }
  confirming.value = null
})

const shown = (key: keyof CorrectionDraft): string | undefined => (submitted.value ? errors.value[key] : undefined)
</script>

<template>
  <section
    aria-labelledby="corrections-title"
    class="space-y-4"
  >
    <h3
      id="corrections-title"
      class="sr-only"
    >
      Correcciones
    </h3>

    <UAlert
      v-if="!editable"
      color="warning"
      variant="subtle"
      icon="i-lucide-lock"
      :description="PUBLISHED_BLOCK_MESSAGE"
    />

    <UAlert
      v-if="notice"
      color="success"
      variant="subtle"
      icon="i-lucide-circle-check"
      :description="notice.message"
      close
      @update:open="emit('dismiss')"
    >
      <template
        v-if="notice.reprocess_required && reprocessable"
        #actions
      >
        <UButton
          size="xs"
          color="neutral"
          variant="outline"
          label="Reprocesar ahora"
          :disabled="busy"
          @click="emit('reprocess')"
        />
      </template>
    </UAlert>

    <UAlert
      v-if="error"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-alert"
      :description="error"
    />
    <p
      v-else-if="!loading && corrections.length === 0"
      class="text-sm text-muted"
    >
      Aún no hay correcciones.
    </p>
    <ul
      v-else
      class="divide-y divide-default rounded-lg ring ring-default"
    >
      <li
        v-for="item in sorted"
        :key="item.id"
        class="space-y-2 px-4 py-3.5 text-sm"
      >
        <p class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span class="font-medium text-default">p. {{ item.page_number }}</span>
          <UBadge
            :color="item.active ? 'primary' : 'neutral'"
            variant="soft"
            size="sm"
            :label="item.active ? 'Activa' : 'Borrada'"
          />
          <span>Creada {{ formatTimestamp(item.created_at) }}</span>
          <span v-if="item.deleted_at">· Borrada {{ formatTimestamp(item.deleted_at) }}</span>
          <span class="font-mono text-[0.7rem] text-muted">{{ item.id }}</span>
        </p>
        <p
          class="flex flex-wrap items-center gap-2 wrap-break-word"
          :class="item.active ? '' : 'opacity-70'"
        >
          <span class="rounded bg-error/10 px-1.5 py-0.5 font-mono text-[0.8125rem] text-highlighted line-through decoration-error/60">{{ item.find_text }}</span>
          <UIcon
            name="i-lucide-arrow-right"
            class="size-3.5 shrink-0 text-muted"
            aria-hidden="true"
          />
          <span class="sr-only">se reemplaza por</span>
          <span
            v-if="item.replace_text === ''"
            class="italic text-muted"
          >(eliminar)</span>
          <span
            v-else
            class="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[0.8125rem] text-highlighted"
          >{{ item.replace_text }}</span>
        </p>
        <p class="wrap-break-word text-muted">
          Motivo: {{ item.reason }}
        </p>
        <div v-if="item.active">
          <UButton
            v-if="confirming !== item.id"
            size="xs"
            color="error"
            variant="ghost"
            icon="i-lucide-trash-2"
            label="Borrar"
            class="-ms-2 min-h-11 sm:min-h-0"
            :disabled="disabled"
            @click="confirming = item.id"
          />
          <div
            v-else
            class="flex flex-wrap items-center gap-2 rounded-md bg-error/5 p-2"
          >
            <span class="text-xs text-default">¿Borrar esta corrección? La versión volverá a revisión.</span>
            <UButton
              size="xs"
              color="error"
              label="Sí, borrar"
              class="min-h-11 sm:min-h-0"
              :disabled="disabled"
              @click="emit('remove', item.id)"
            />
            <UButton
              size="xs"
              color="neutral"
              variant="ghost"
              label="Cancelar"
              class="min-h-11 sm:min-h-0"
              @click="confirming = null"
            />
          </div>
        </div>
      </li>
    </ul>

    <form
      class="space-y-4 rounded-lg bg-elevated/40 p-4 ring ring-default"
      novalidate
      @submit.prevent="submit"
    >
      <h4 class="text-sm font-medium text-highlighted">
        Nueva corrección
      </h4>
      <p class="text-xs text-muted">
        {{ MATCH_RULE_HELP }}
      </p>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-default">Página</span>
        <select
          v-if="pages.length > 1"
          v-model="draft.page_number"
          class="min-h-11 w-full rounded-md bg-default px-2.5 text-base ring ring-accented focus-visible:outline-2 focus-visible:outline-primary sm:min-h-9 sm:text-sm"
          :disabled="disabled"
        >
          <option
            v-for="p in pages"
            :key="p"
            :value="String(p)"
          >
            {{ p }}
          </option>
        </select>
        <UInput
          v-else
          v-model="draft.page_number"
          class="w-full"
          type="number"
          inputmode="numeric"
          min="1"
          :disabled="disabled"
        />
        <span
          v-if="shown('page_number')"
          class="mt-1 block text-xs text-error"
        >{{ shown('page_number') }}</span>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-default">Texto a buscar ({{ draft.find_text.length }}/{{ FIND_MAX }})</span>
        <UTextarea
          v-model="draft.find_text"
          class="w-full"
          :rows="2"
          :disabled="disabled"
          placeholder="Pega aquí el fragmento literal con el error"
        />
        <span
          v-if="shown('find_text')"
          class="mt-1 block text-xs text-error"
        >{{ shown('find_text') }}</span>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-default">Reemplazo ({{ draft.replace_text.length }}/{{ REPLACE_MAX }}; vacío elimina el texto)</span>
        <UTextarea
          v-model="draft.replace_text"
          class="w-full"
          :rows="2"
          :disabled="disabled"
        />
        <span
          v-if="shown('replace_text')"
          class="mt-1 block text-xs text-error"
        >{{ shown('replace_text') }}</span>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-default">Motivo ({{ draft.reason.length }}/{{ REASON_MAX }})</span>
        <UInput
          v-model="draft.reason"
          class="w-full"
          :disabled="disabled"
          placeholder="Ej. Error de OCR frente al original"
        />
        <span
          v-if="shown('reason')"
          class="mt-1 block text-xs text-error"
        >{{ shown('reason') }}</span>
      </label>
      <UAlert
        v-if="formError"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        :description="formError"
      />
      <UButton
        type="submit"
        color="neutral"
        class="min-h-11 sm:min-h-0"
        label="Crear corrección"
        :loading="submitting"
        :disabled="disabled"
      />
    </form>
  </section>
</template>
