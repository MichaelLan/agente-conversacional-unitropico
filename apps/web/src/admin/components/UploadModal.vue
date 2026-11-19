<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import UModal from '@nuxt/ui/components/Modal.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import { MODAL_FULLSCREEN_ON_MOBILE } from '../lib/ui'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UForm from '@nuxt/ui/components/Form.vue'
import UFormField from '@nuxt/ui/components/FormField.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import USelect from '@nuxt/ui/components/Select.vue'
import UProgress from '@nuxt/ui/components/Progress.vue'
import { MAX_UPLOAD_BYTES, NONE, validateFile, validateUpload, type UploadForm } from '../lib/forms'
import { DOCUMENT_TYPE_LABELS, formatBytes } from '../lib/versions'
import { DOCUMENT_TYPES, type DocumentSummary, type DocumentType, type UploadInput } from '../types'

const props = defineProps<{
  documents: readonly DocumentSummary[]
  uploading: boolean
  error: string | null
}>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ submit: [meta: UploadInput, file: File] }>()

const blank = (): UploadForm => ({ title: '', document_type: '', version_label: '', document_id: NONE })
const state = ref<UploadForm>(blank())
const file = ref<File | null>(null)
const fileError = ref<string | null>(null)

const typeItems: { label: string; value: string }[] = DOCUMENT_TYPES.map(value => ({ label: DOCUMENT_TYPE_LABELS[value], value }))
const documentItems = computed(() => [
  { label: 'Documento nuevo', value: NONE },
  ...props.documents.map(document => ({ label: document.title, value: document.id })),
])

watch(open, isOpen => {
  if (isOpen) {
    state.value = blank()
    file.value = null
    fileError.value = null
  }
})

// Al elegir un documento existente se reutilizan su título y tipo.
watch(() => state.value.document_id, id => {
  const existing = props.documents.find(document => document.id === id)
  if (existing) {
    state.value.title = existing.title
    state.value.document_type = existing.document_type
  }
})

function onFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const picked = input.files?.[0] ?? null
  file.value = picked
  fileError.value = picked ? (validateFile(picked)?.message ?? null) : null
}

const validate = (value: UploadForm) => validateUpload(value, file.value)

function submit(): void {
  const picked = file.value
  if (picked === null || !isType(state.value.document_type)) return
  emit('submit', {
    title: state.value.title.trim(),
    document_type: state.value.document_type,
    version_label: state.value.version_label.trim(),
    document_id: state.value.document_id === NONE ? undefined : state.value.document_id,
  }, picked)
}

const isType = (value: string): value is DocumentType =>
  (DOCUMENT_TYPES as readonly string[]).includes(value)
</script>

<template>
  <UModal
    v-model:open="open"
    title="Subir documento"
    description="Se registra el PDF y comienza su procesamiento. Después deberás revisarlo y publicarlo."
    :dismissible="!uploading"
    :ui="MODAL_FULLSCREEN_ON_MOBILE"
  >
    <template #body>
      <UForm
        id="upload-form"
        :state="state"
        :validate="validate"
        class="space-y-4"
        @submit="submit"
      >
        <UFormField
          name="file"
          label="Archivo PDF"
          required
          :help="`Solo PDF, hasta ${formatBytes(MAX_UPLOAD_BYTES)}.`"
          :error="fileError ?? false"
        >
          <input
            type="file"
            accept="application/pdf,.pdf"
            class="block w-full rounded-md p-1.5 text-base text-muted ring ring-accented file:me-3 file:min-h-10 file:cursor-pointer file:rounded-md file:border-0 file:bg-elevated file:px-3 file:text-sm file:font-medium file:text-highlighted focus-visible:outline-2 focus-visible:outline-primary sm:text-sm sm:file:min-h-8"
            :disabled="uploading"
            @change="onFile"
          >
        </UFormField>
        <UFormField
          name="document_id"
          label="Documento existente"
          help="Elige uno para registrar una versión nueva; déjalo en «Documento nuevo» para crear otro."
        >
          <USelect
            v-model="state.document_id"
            :items="documentItems"
            :disabled="uploading"
            class="w-full"
          />
        </UFormField>
        <UFormField
          name="title"
          label="Título"
          required
        >
          <UInput
            v-model="state.title"
            maxlength="200"
            :disabled="uploading || state.document_id !== NONE"
            class="w-full"
          />
        </UFormField>
        <UFormField
          name="document_type"
          label="Tipo de documento"
          required
        >
          <USelect
            v-model="state.document_type"
            :items="typeItems"
            placeholder="Selecciona un tipo"
            :disabled="uploading || state.document_id !== NONE"
            class="w-full"
          />
        </UFormField>
        <UFormField
          name="version_label"
          label="Etiqueta de versión"
          required
          help="Por ejemplo: «2026-1» o «Acuerdo 012 de 2025»."
        >
          <UInput
            v-model="state.version_label"
            maxlength="80"
            :disabled="uploading"
            class="w-full"
          />
        </UFormField>
        <div
          aria-live="polite"
          class="space-y-2"
        >
          <UProgress
            v-if="uploading"
            aria-label="Subiendo documento"
          />
          <p
            v-if="uploading"
            class="text-sm text-muted"
          >
            Subiendo… puede tardar hasta un par de minutos.
          </p>
          <UAlert
            v-if="error"
            color="error"
            variant="subtle"
            icon="i-lucide-circle-alert"
            :description="error"
          />
        </div>
      </UForm>
    </template>
    <template #footer>
      <div class="flex w-full flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <UButton
          color="neutral"
          variant="outline"
          label="Cancelar"
          class="min-h-11 justify-center sm:min-h-0"
          :disabled="uploading"
          @click="open = false"
        />
        <UButton
          type="submit"
          form="upload-form"
          color="neutral"
          label="Subir"
          icon="i-lucide-upload"
          class="min-h-11 justify-center sm:min-h-0"
          :loading="uploading"
        />
      </div>
    </template>
  </UModal>
</template>
