<script setup lang="ts">
defineProps<{ suggestions: readonly string[] }>()

const emit = defineEmits<{ select: [question: string] }>()
</script>

<template>
  <section
    aria-labelledby="bienvenida"
    class="flex flex-1 flex-col justify-center gap-6 py-10"
  >
    <div>
      <h2
        id="bienvenida"
        class="text-2xl font-semibold text-highlighted sm:text-3xl"
      >
        Hola, ¿en qué puedo ayudarte?
      </h2>
      <p class="mt-2 text-muted">
        Respondo preguntas sobre procesos, requisitos, servicios y procedimientos
        de Fomento Socioeconómico a partir de los documentos institucionales de
        Bienestar Universitario.
      </p>
    </div>
    <ul
      aria-label="Preguntas sugeridas"
      class="grid gap-2 sm:grid-cols-2"
    >
      <li
        v-for="question in suggestions"
        :key="question"
      >
        <button
          type="button"
          class="h-full w-full rounded-lg px-3 py-2.5 text-start text-sm text-toned ring ring-default hover:bg-elevated/60 hover:text-highlighted focus-visible:outline-2 focus-visible:outline-primary"
          @click="emit('select', question)"
        >
          {{ question }}
        </button>
      </li>
    </ul>
  </section>
</template>
