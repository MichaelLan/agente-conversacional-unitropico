/**
 * Preguntas frecuentes de la barra lateral.
 *
 * NO son FAQs institucionales verificadas: son preguntas genéricas formuladas
 * como las haría un estudiante, sin respuestas ni datos de Unitrópico. Las
 * respuestas siempre provienen del asistente (documentos institucionales).
 * Cuando Bienestar Universitario valide un listado real, sustitúyelo aquí y
 * cambia `FAQ_IS_PLACEHOLDER` a `false` para retirar el aviso de la interfaz.
 */
export const FAQ_IS_PLACEHOLDER = true

export interface FaqCategory {
  id: string
  label: string
  icon: string
  questions: readonly string[]
}

export const FAQ_CATEGORIES: readonly FaqCategory[] = [
  {
    id: 'becas',
    label: 'Becas',
    icon: 'i-lucide-graduation-cap',
    questions: [
      '¿Qué becas puedo solicitar como estudiante?',
      '¿Cómo sé si cumplo las condiciones para una beca?',
      '¿Puedo tener una beca y otro apoyo al mismo tiempo?',
    ],
  },
  {
    id: 'apoyos',
    label: 'Apoyos socioeconómicos',
    icon: 'i-lucide-hand-heart',
    questions: [
      '¿Qué apoyos socioeconómicos ofrece Bienestar Universitario?',
      '¿Hay algún apoyo para alimentación o transporte?',
      '¿Quién puede acceder a los apoyos socioeconómicos?',
    ],
  },
  {
    id: 'requisitos',
    label: 'Requisitos',
    icon: 'i-lucide-list-checks',
    questions: [
      '¿Qué documentos necesito para solicitar un apoyo?',
      '¿Qué condiciones debo mantener para conservar un beneficio?',
    ],
  },
  {
    id: 'convocatorias',
    label: 'Convocatorias',
    icon: 'i-lucide-megaphone',
    questions: [
      '¿Cuándo se abren las convocatorias de apoyos?',
      '¿Dónde se publican los resultados de una convocatoria?',
    ],
  },
  {
    id: 'tramites',
    label: 'Trámites',
    icon: 'i-lucide-file-text',
    questions: [
      '¿Cómo presento una solicitud de apoyo socioeconómico?',
      '¿Qué puedo hacer si mi solicitud no fue aprobada?',
      '¿Cómo renuevo un beneficio para el siguiente periodo?',
    ],
  },
]

/** Sugerencias del estado vacío: una pregunta de cada una de las primeras categorías. */
export const FAQ_SUGGESTIONS: readonly string[] = FAQ_CATEGORIES
  .slice(0, 4)
  .map(category => category.questions[0])
  .filter((question): question is string => question !== undefined)
