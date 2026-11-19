<script setup lang="ts">
import { computed, ref } from 'vue'
import UAlert from '@nuxt/ui/components/Alert.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import UForm from '@nuxt/ui/components/Form.vue'
import UFormField from '@nuxt/ui/components/FormField.vue'
import UInput from '@nuxt/ui/components/Input.vue'
import USelect from '@nuxt/ui/components/Select.vue'
import USwitch from '@nuxt/ui/components/Switch.vue'
import { NONE, buildPatch, metadataFormFrom, validateMetadata, type MetadataForm } from '../lib/forms'
import { CORRECTIONS_PENDING_NOTICE, DOCUMENT_TYPE_LABELS, STATUS_LABELS, hasPendingCorrections } from '../lib/versions'
import { DOCUMENT_TYPES, type VersionPatch, type VersionRow } from '../types'

const props = defineProps<{
  row: VersionRow
  /** Todas las versiones de cualquier documento, para elegir la que sustituye. */
  candidates: readonly VersionRow[]
  saving: boolean
  error: string | null
}>()
const emit = defineEmits<{ save: [patch: VersionPatch]; cancel: [] }>()

const original = metadataFormFrom(props.row.document.title, props.row.version, props.row.document.document_type)
const state = ref<MetadataForm>({ ...original })
const noChanges = ref(false)
/** Con correcciones pendientes no se puede marcar como revisada una versión pendiente. */
const reviewBlocked = hasPendingCorrections(props.row.version) && original.requires_review

const typeItems: { label: string; value: string }[] = DOCUMENT_TYPES.map(value => ({ label: DOCUMENT_TYPE_LABELS[value], value }))
const supersedesItems = computed(() => [
  { label: 'Ninguna', value: NONE },
  ...props.candidates
    .filter(candidate => candidate.version.id !== props.row.version.id)
    .map(candidate => ({
      label: `${candidate.document.title} · ${candidate.version.version_label ?? 'sin etiqueta'} (${STATUS_LABELS[candidate.version.status]})`,
      value: candidate.version.id,
    })),
])

const validate = (value: MetadataForm) => validateMetadata(value)

function submit(): void {
  const patch = buildPatch(state.value, original)
  noChanges.value = patch === null
  if (patch !== null) emit('save', patch)
}
</script>

<template>
  <UForm
    :state="state"
    :validate="validate"
    class="space-y-4"
    @submit="submit"
  >
    <UFormField
      name="title"
      label="Título del documento"
      required
    >
      <UInput
        v-model="state.title"
        maxlength="200"
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
        class="w-full"
      />
    </UFormField>
    <UFormField
      name="version_label"
      label="Etiqueta de versión"
      required
    >
      <UInput
        v-model="state.version_label"
        maxlength="80"
        class="w-full"
      />
    </UFormField>
    <div class="grid gap-4 sm:grid-cols-3">
      <UFormField
        name="issued_at"
        label="Emisión"
        help="AAAA-MM-DD"
      >
        <UInput
          v-model="state.issued_at"
          type="date"
          class="w-full"
        />
      </UFormField>
      <UFormField
        name="effective_from"
        label="Vigente desde"
        help="AAAA-MM-DD"
      >
        <UInput
          v-model="state.effective_from"
          type="date"
          class="w-full"
        />
      </UFormField>
      <UFormField
        name="effective_until"
        label="Vigente hasta"
        help="AAAA-MM-DD"
      >
        <UInput
          v-model="state.effective_until"
          type="date"
          class="w-full"
        />
      </UFormField>
    </div>
    <UFormField
      name="supersedes_version_id"
      label="Sustituye a la versión"
    >
      <USelect
        v-model="state.supersedes_version_id"
        :items="supersedesItems"
        class="w-full"
      />
    </UFormField>
    <USwitch
      :model-value="!state.requires_review"
      label="Marcar como revisado"
      :description="reviewBlocked ? `${CORRECTIONS_PENDING_NOTICE}.` : 'Necesario para poder publicar la versión.'"
      :disabled="reviewBlocked"
      @update:model-value="state.requires_review = !$event"
    />
    <div aria-live="polite">
      <UAlert
        v-if="error"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-alert"
        :description="error"
      />
      <UAlert
        v-else-if="noChanges"
        color="neutral"
        variant="subtle"
        description="No hay cambios que guardar."
      />
    </div>
    <div class="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
      <UButton
        color="neutral"
        variant="outline"
        label="Cancelar"
        class="min-h-11 justify-center sm:min-h-0"
        :disabled="saving"
        @click="emit('cancel')"
      />
      <UButton
        type="submit"
        color="neutral"
        class="min-h-11 justify-center sm:min-h-0"
        label="Guardar cambios"
        :loading="saving"
      />
    </div>
  </UForm>
</template>
