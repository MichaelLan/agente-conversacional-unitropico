<script setup lang="ts">
import { computed } from 'vue'
import UModal from '@nuxt/ui/components/Modal.vue'
import UButton from '@nuxt/ui/components/Button.vue'
import { ACTION_CONSEQUENCES, ACTION_LABELS, STUCK_RECOVERY } from '../lib/versions'
import type { VersionAction, VersionRow } from '../types'

const props = defineProps<{ action: VersionAction | null; row: VersionRow | null }>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ confirm: [] }>()

const title = computed(() => (props.action ? `${ACTION_LABELS[props.action]} versión` : ''))
const target = computed(() => props.row
  ? `${props.row.document.title} · ${props.row.version.version_label ?? 'sin etiqueta'}`
  : '')
const stuckWarning = computed(() =>
  props.action === 'reprocess' && props.row !== null && STUCK_RECOVERY.has(props.row.version.status))
</script>

<template>
  <UModal
    v-model:open="open"
    :title="title"
    :description="target"
  >
    <template #body>
      <p
        v-if="action"
        class="text-sm text-default"
      >
        {{ ACTION_CONSEQUENCES[action] }}
      </p>
      <p
        v-if="stuckWarning"
        class="mt-3 text-sm text-warning"
      >
        Esta versión figura como en procesamiento. Reprocesar solo sirve si el procesamiento quedó detenido;
        si sigue en ejecución, el servidor rechazará la solicitud.
      </p>
    </template>
    <template #footer>
      <div class="flex w-full justify-end gap-2">
        <UButton
          color="neutral"
          variant="outline"
          label="Cancelar"
          @click="open = false"
        />
        <UButton
          :label="action ? ACTION_LABELS[action] : 'Confirmar'"
          @click="emit('confirm')"
        />
      </div>
    </template>
  </UModal>
</template>
