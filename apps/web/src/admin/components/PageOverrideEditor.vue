<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import UModal from '@nuxt/ui/components/Modal.vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import UTextarea from '@nuxt/ui/components/Textarea.vue'
import { MODAL_FULLSCREEN_ON_MOBILE } from '../lib/ui'
import {
  OVERRIDE_REASON_MAX, OVERRIDE_TEXT_MAX, OVERRIDE_WARNING,
  countChars, overrideToInput, validateOverride,
} from '../lib/pages'
import type { PageOverrideInput } from '../types'

const props = defineProps<{
  pageNumber: number
  pageCount: number | null
  /** Texto actual de la página con el que se precarga el editor. */
  initialText: string
  submitting: boolean
  error: string | null
}>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ save: [input: PageOverrideInput] }>()

const text = ref('')
const reason = ref('')
const step = ref<'edit' | 'confirm'>('edit')
const attempted = ref(false)

watch(open, isOpen => {
  if (isOpen) {
    text.value = props.initialText
    reason.value = ''
    step.value = 'edit'
    attempted.value = false
  }
})

const errors = computed(() => validateOverride({ text: text.value, reason: reason.value }, props.pageNumber, props.pageCount))
const textCount = computed(() => countChars(text.value))
const shown = (key: 'text' | 'reason' | 'page'): string | undefined => (attempted.value ? errors.value[key] : undefined)

function review(): void {
  attempted.value = true
  if (Object.keys(errors.value).length === 0) step.value = 'confirm'
}
function confirm(): void {
  if (props.submitting || Object.keys(errors.value).length > 0) return
  emit('save', overrideToInput({ text: text.value, reason: reason.value }, props.pageNumber))
}
</script>

<template>
  <UModal
    v-model:open="open"
    :title="`Sustituir texto de la página ${pageNumber}`"
    description="Reemplaza por completo el texto preparado de esta página."
    :dismissible="!submitting"
    :ui="MODAL_FULLSCREEN_ON_MOBILE"
  >
    <template #body>
      <form
        v-if="step === 'edit'"
        id="override-form"
        class="space-y-4"
        novalidate
        @submit.prevent="review"
      >
        <UAlert
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :description="OVERRIDE_WARNING"
        />
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-default">
            Texto de la página
            <span
              class="font-normal"
              :class="textCount > OVERRIDE_TEXT_MAX ? 'text-error' : 'text-muted'"
            >({{ textCount }}/{{ OVERRIDE_TEXT_MAX }})</span>
          </span>
          <UTextarea
            v-model="text"
            class="w-full"
            :rows="14"
            :disabled="submitting"
            :ui="{ base: 'font-mono text-[0.8125rem] leading-5 whitespace-pre-wrap' }"
            aria-describedby="override-text-error"
          />
          <span
            v-if="shown('text')"
            id="override-text-error"
            class="mt-1 block text-xs text-error"
            role="alert"
          >{{ shown('text') }}</span>
        </label>
        <label class="block text-sm">
          <span class="mb-1 block font-medium text-default">
            Motivo
            <span class="font-normal text-muted">({{ countChars(reason) }}/{{ OVERRIDE_REASON_MAX }})</span>
          </span>
          <UInput
            v-model="reason"
            class="w-full"
            :disabled="submitting"
            placeholder="Ej. Tabla como imagen que el OCR no leyó"
          />
          <span
            v-if="shown('reason')"
            class="mt-1 block text-xs text-error"
            role="alert"
          >{{ shown('reason') }}</span>
        </label>
        <span
          v-if="shown('page')"
          class="block text-xs text-error"
          role="alert"
        >{{ shown('page') }}</span>
      </form>

      <div
        v-else
        class="space-y-4 text-sm"
      >
        <UAlert
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :title="`¿Sustituir el texto de la página ${pageNumber}?`"
          :description="OVERRIDE_WARNING"
        />
        <p class="text-default">
          Tras guardar, la versión volverá a revisión y deberás reprocesarla antes de revisarla o publicarla.
        </p>
        <dl class="space-y-1 rounded-lg p-3 ring ring-default">
          <dt class="text-muted">
            Motivo
          </dt>
          <dd class="wrap-break-word">
            {{ reason.trim() }}
          </dd>
          <dt class="pt-1 text-muted">
            Texto nuevo
          </dt>
          <dd>{{ textCount }} caracteres</dd>
        </dl>
        <UAlert
          v-if="error"
          color="error"
          variant="subtle"
          icon="i-lucide-circle-alert"
          :description="error"
        />
      </div>
    </template>
    <template #footer>
      <div class="flex w-full flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <UButton
          v-if="step === 'edit'"
          color="neutral"
          variant="outline"
          label="Cancelar"
          class="min-h-11 justify-center sm:min-h-0"
          @click="open = false"
        />
        <UButton
          v-else
          color="neutral"
          variant="outline"
          label="Volver a editar"
          class="min-h-11 justify-center sm:min-h-0"
          :disabled="submitting"
          @click="step = 'edit'"
        />
        <UButton
          v-if="step === 'edit'"
          type="submit"
          form="override-form"
          color="neutral"
          label="Revisar y guardar"
          class="min-h-11 justify-center sm:min-h-0"
        />
        <UButton
          v-else
          color="neutral"
          label="Sí, sustituir página"
          class="min-h-11 justify-center sm:min-h-0"
          :loading="submitting"
          :disabled="submitting"
          @click="confirm"
        />
      </div>
    </template>
  </UModal>
</template>
