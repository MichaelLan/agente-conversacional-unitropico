<script setup lang="ts">
import UBadge from '@nuxt/ui/components/Badge.vue'
import { STATUS_COLORS, STATUS_LABELS } from '../lib/versions'
import type { VersionStatus } from '../types'

defineProps<{ status: VersionStatus }>()
</script>

<template>
  <UBadge
    :color="STATUS_COLORS[status]"
    variant="subtle"
    size="sm"
    :label="STATUS_LABELS[status]"
  />
</template>
