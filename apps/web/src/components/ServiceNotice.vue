<script setup lang="ts">
import UBadge from '@nuxt/ui/components/Badge.vue'
import UCard from '@nuxt/ui/components/Card.vue'
</script>

<template>
  <section
    aria-labelledby="titulo-servicio"
    class="w-full max-w-3xl"
  >
    <UBadge
      color="neutral"
      variant="subtle"
      size="lg"
    >
      Servicio en preparación
    </UBadge>
    <h1
      id="titulo-servicio"
      class="mt-6 text-3xl font-semibold tracking-tight text-highlighted sm:text-5xl"
    >
      Orientación de Fomento Socioeconómico
    </h1>
    <p class="mt-6 max-w-2xl text-lg leading-relaxed text-toned">
      Estamos preparando un servicio de orientación para estudiantes de Unitrópico.
      La recuperación documental experimental permite buscar fragmentos, no respuestas generadas.
    </p>

    <UCard class="mt-8">
      <h2 class="text-base font-semibold text-highlighted">
        Alcance de esta prueba
      </h2>
      <p class="mt-2 leading-relaxed text-toned">
        Esta prueba muestra fragmentos y referencias de los documentos disponibles.
        Recuperar un fragmento no confirma que sea suficiente para resolver una pregunta.
      </p>
      <p class="mt-3 leading-relaxed text-toned">
        Esta pantalla no ofrece respuestas institucionales ni utiliza un generador de respuestas.
        No incluyas datos personales en tu pregunta.
      </p>
    </UCard>
  </section>
</template>
