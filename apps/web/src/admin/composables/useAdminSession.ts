import { computed, shallowRef } from 'vue'

/**
 * Sesión del administrador SOLO en memoria: el token vive en una referencia de
 * este composable y se pierde al recargar la página (decisión de seguridad:
 * nunca en almacenamiento del navegador, cookies, URL ni logs). La protección real
 * es del servidor, que valida el Bearer en cada petición.
 */
export function useAdminSession() {
  const token = shallowRef<string | null>(null)
  const notice = shallowRef<string | null>(null)

  function signIn(value: string): boolean {
    const clean = value.trim()
    if (clean === '') return false
    token.value = clean
    notice.value = null
    return true
  }

  /** Borra el token de memoria; `reason` se muestra en la pantalla de acceso. */
  function signOut(reason: string | null = null): void {
    token.value = null
    notice.value = reason
  }

  return {
    authenticated: computed(() => token.value !== null),
    notice,
    getToken: (): string | null => token.value,
    signIn,
    signOut,
  }
}

export type AdminSession = ReturnType<typeof useAdminSession>
