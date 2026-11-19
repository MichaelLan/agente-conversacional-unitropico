/**
 * Ajustes de presentación compartidos (mobile first): en pantallas estrechas los
 * modales de formulario ocupan toda la pantalla; desde `sm` vuelven a ser diálogos.
 */
export const MODAL_FULLSCREEN_ON_MOBILE = {
  content: 'max-sm:inset-0 max-sm:top-0 max-sm:left-0 max-sm:translate-x-0 max-sm:translate-y-0 max-sm:h-dvh max-sm:max-h-dvh max-sm:w-full max-sm:max-w-none max-sm:rounded-none max-sm:ring-0 max-sm:shadow-none',
  header: 'pt-[max(1rem,env(safe-area-inset-top))]',
  body: 'overflow-y-auto',
  footer: 'pb-[max(1rem,env(safe-area-inset-bottom))]',
} as const

/** Slideover de detalle: pantalla completa en móvil, panel ancho en escritorio. */
export const DETAIL_SLIDEOVER_UI = {
  content: 'sm:max-w-2xl',
  header: 'pt-[max(1rem,env(safe-area-inset-top))] pe-14',
  body: 'pb-[max(1rem,env(safe-area-inset-bottom))]',
  title: 'text-base wrap-break-word',
} as const
