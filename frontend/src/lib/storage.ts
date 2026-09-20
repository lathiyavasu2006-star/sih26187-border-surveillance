import { createJSONStorage, type StateStorage } from 'zustand/middleware'

/** Web Storage that never throws (private mode, blocked site data or quota errors degrade to memory). */
function safeStorage(kind: 'local' | 'session'): StateStorage {
  const memory = new Map<string, string>()
  const backend = (): Storage | null => {
    try {
      return kind === 'local' ? window.localStorage : window.sessionStorage
    } catch {
      return null
    }
  }
  return {
    getItem: (name) => {
      try {
        return backend()?.getItem(name) ?? memory.get(name) ?? null
      } catch {
        return memory.get(name) ?? null
      }
    },
    setItem: (name, value) => {
      memory.set(name, value)
      try {
        backend()?.setItem(name, value)
      } catch {
        /* memory copy is kept */
      }
    },
    removeItem: (name) => {
      memory.delete(name)
      try {
        backend()?.removeItem(name)
      } catch {
        /* ignore */
      }
    },
  }
}

/** Session-scoped: credentials disappear when the browser tab/window is closed. */
export const sessionJSONStorage = createJSONStorage(() => safeStorage('session'))
/** Device-scoped UI preferences only (never tokens or operational data). */
export const localJSONStorage = createJSONStorage(() => safeStorage('local'))
