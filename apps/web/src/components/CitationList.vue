<script setup lang="ts">
import { computed } from 'vue'
import UCollapsible from '@nuxt/ui/components/Collapsible.vue'
import UIcon from '@nuxt/ui/components/Icon.vue'
import type { Citation } from '../types/chat'
import { citationDetails } from '../lib/format'

const props = defineProps<{ citations: readonly Citation[] }>()

const items = computed(() => props.citations.map((citation, index) => ({
  key: `${index}-${citation.chunk_id}`,
  title: citation.document_title,
  details: citationDetails(citation).join(' · '),
  quote: citation.quote,
})))
</script>

<template>
  <section
    aria-label="Fuentes"
    class="mt-3 text-xs text-muted"
  >
    <ol class="space-y-1">
      <li
        v-for="item in items"
        :key="item.key"
      >
        <UCollapsible>
          <template #default="{ open }">
            <button
              type="button"
              class="group flex max-w-full items-start gap-1.5 rounded-sm text-start hover:text-highlighted focus-visible:outline-2 focus-visible:outline-primary"
            >
              <UIcon
                name="i-lucide-chevron-right"
                class="mt-px size-3.5 shrink-0 transition-transform"
                :class="{ 'rotate-90': open }"
                aria-hidden="true"
              />
              <span class="min-w-0 wrap-break-word">
                <span class="font-medium">{{ item.title }}</span>
                <span> · {{ item.details }}</span>
              </span>
            </button>
          </template>
          <template #content>
            <blockquote class="my-1.5 ms-5 border-s border-accented ps-3 whitespace-pre-line wrap-break-word text-toned">
              {{ item.quote }}
            </blockquote>
          </template>
        </UCollapsible>
      </li>
    </ol>
  </section>
</template>
