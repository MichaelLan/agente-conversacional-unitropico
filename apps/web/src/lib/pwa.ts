/**
 * Registro del service worker (solo en producción). El SW lo genera el build a
 * partir de `pwa/sw.js` y nunca intercepta `/api/*`. Un fallo de registro no
 * afecta a la app: simplemente no será instalable ni tendrá página sin conexión.
 */
export function registerServiceWorker(url: string, scope: string): void {
  if (!import.meta.env.PROD || typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(url, { scope, updateViaCache: 'none' }).catch(() => undefined)
  }, { once: true })
}
