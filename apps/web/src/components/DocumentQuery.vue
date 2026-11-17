<script setup lang="ts">
/* global AbortController */
import { computed, onBeforeUnmount, ref } from 'vue'
import { queryRetrieval } from '../services/retrieval'
import type { RetrievalResult } from '../types/retrieval'

const question = ref('')
const state = ref<'idle' | 'loading' | 'error' | RetrievalResult['status']>('idle')
const fragments = ref<RetrievalResult['evidence']>([])
const loading = computed(() => state.value === 'loading')
const length = computed(() => Array.from(question.value.trim()).length)
const messages = {
  idle: 'Escribe una pregunta para buscar fragmentos en los documentos disponibles.',
  loading: 'Buscando fragmentos…',
  unavailable: 'La recuperación no está disponible. Puedes intentarlo más tarde.',
  abstained: 'No se recuperaron fragmentos para esta pregunta. Prueba reformularla.',
  evidence_found: 'Fragmentos recuperados; no respuesta generada',
  error: 'No se pudo completar la consulta. Inténtalo de nuevo.',
}
let controller: AbortController | undefined
onBeforeUnmount(() => controller?.abort())

/** Keep only the current request in memory and ignore cancellation on unmount. */
async function submit() {
  if (loading.value || length.value < 1 || length.value > 500) return
  state.value = 'loading'
  fragments.value = []
  controller = new AbortController()
  try {
    const result = await queryRetrieval(question.value, controller.signal)
    if (controller.signal.aborted) return
    fragments.value = result.evidence
    state.value = result.status
  } catch {
    if (!controller.signal.aborted) state.value = 'error'
  }
}
</script>

<template>
  <section
    aria-labelledby="titulo-consulta"
    class="w-full max-w-3xl"
  >
    <h2
      id="titulo-consulta"
      class="text-xl font-semibold text-highlighted"
    >
      Recuperación documental experimental
    </h2>
    <p
      id="aviso-consulta"
      class="mt-3 text-toned"
    >
      Servicio en preparación: busca fragmentos, no genera respuestas institucionales.
      No incluyas datos personales. La pregunta se envía al servicio para esta consulta;
      esta interfaz no guarda un historial.
    </p>
    <form
      class="mt-6 flex flex-col gap-3"
      :aria-busy="loading"
      @submit.prevent="submit"
    >
      <label
        for="pregunta"
        class="font-medium"
      >Pregunta sobre los documentos</label>
      <textarea
        id="pregunta"
        v-model="question"
        name="pregunta"
        rows="4"
        required
        minlength="1"
        maxlength="500"
        :disabled="loading"
        aria-describedby="aviso-consulta contador-pregunta"
        class="w-full rounded-md border border-default bg-default p-3 focus-visible:outline-2 focus-visible:outline-primary"
      />
      <p
        id="contador-pregunta"
        class="text-sm text-muted"
      >
        {{ length }} / 500 caracteres (sin espacios exteriores)
      </p>
      <button
        type="submit"
        :disabled="loading || length < 1 || length > 500"
        class="min-h-11 self-start rounded-md bg-primary px-5 py-3 font-medium text-inverted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-50"
      >
        {{ loading ? 'Buscando…' : 'Buscar fragmentos' }}
      </button>
    </form>
    <p
      role="status"
      aria-live="polite"
      class="mt-6 font-medium"
    >
      {{ messages[state] }}
    </p>
    <ol
      v-if="state === 'evidence_found'"
      class="mt-4 space-y-4"
    >
      <li
        v-for="(fragment, index) in fragments"
        :key="index"
        class="rounded-md border border-default p-4 [overflow-wrap:anywhere]"
      >
        <p class="whitespace-pre-wrap text-toned">
          {{ fragment.text }}
        </p>
        <dl class="mt-4 text-sm">
          <dt class="font-medium">
            Documento
          </dt>
          <dd>{{ fragment.document_id }}</dd>
          <dt class="mt-2 font-medium">
            Página física
          </dt>
          <dd>{{ fragment.page }}</dd>
          <template v-if="fragment.section?.trim()">
            <dt class="mt-2 font-medium">
              Sección
            </dt>
            <dd>{{ fragment.section }}</dd>
          </template>
        </dl>
      </li>
    </ol>
  </section>
</template>
