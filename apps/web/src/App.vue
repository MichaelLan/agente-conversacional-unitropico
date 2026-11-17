<script setup lang="ts">
import UApp from '@nuxt/ui/components/App.vue'
import UContainer from '@nuxt/ui/components/Container.vue'
import ServiceNotice from './components/ServiceNotice.vue'
import DocumentQuery from './components/DocumentQuery.vue'
</script>

<template>
  <UApp>
    <UContainer class="flex min-h-dvh flex-col gap-10 py-8 sm:py-12">
      <header class="border-b border-default pb-6">
        <p class="text-xl font-semibold text-highlighted">
          Unitrópico
        </p>
        <p class="mt-1 text-sm text-muted">
          Bienestar Universitario
        </p>
      </header>

      <main
        id="contenido"
        class="flex flex-1 flex-col items-start gap-10 py-4 sm:py-12"
      >
        <ServiceNotice />
        <DocumentQuery />
      </main>

      <footer class="border-t border-default pt-6 text-sm text-muted">
        Proyecto de tesis · Aplicación en desarrollo
      </footer>
    </UContainer>
  </UApp>
</template>
