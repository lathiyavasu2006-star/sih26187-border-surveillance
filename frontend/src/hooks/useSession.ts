import { useEffect, useRef } from 'react'
import { refreshAccessToken } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'

/** Refreshes the 15-minute access token one minute before it expires while the app is open and unlocked. */
export function useTokenKeepAlive(): void {
  const expiresAt = useAuthStore((state) => state.accessExpiresAt)
  const locked = useAuthStore((state) => state.locked)
  const signedIn = useAuthStore((state) => Boolean(state.refreshToken))

  useEffect(() => {
    if (!signedIn || locked || expiresAt === null) return undefined
    let timer = 0
    const attempt = (delay: number) => {
      timer = window.setTimeout(() => {
        void refreshAccessToken().then((token) => {
          // Backend unreachable: keep the session and try again (a success changes expiresAt and re-arms).
          if (!token && useAuthStore.getState().refreshToken) attempt(15_000)
        })
      }, delay)
    }
    attempt(Math.max(5_000, expiresAt - Date.now() - 60_000))
    return () => window.clearTimeout(timer)
  }, [expiresAt, locked, signedIn])
}

const ACTIVITY_EVENTS: (keyof WindowEventMap)[] = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']

/** Locks the screen after the configured period without keyboard or pointer activity (0 = never). */
export function useIdleLock(): void {
  const minutes = useUiStore((state) => state.autoLockMinutes)
  const locked = useAuthStore((state) => state.locked)
  const lastActivity = useRef(0)

  useEffect(() => {
    if (minutes <= 0 || locked) return undefined
    lastActivity.current = Date.now()
    const touch = () => {
      lastActivity.current = Date.now()
    }
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, touch, { passive: true }))
    const timer = window.setInterval(() => {
      if (Date.now() - lastActivity.current >= minutes * 60_000) useAuthStore.getState().lock()
    }, 5_000)
    return () => {
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, touch))
      window.clearInterval(timer)
    }
  }, [minutes, locked])
}

const isLKey = (event: KeyboardEvent) => event.code === 'KeyL' || event.key.toLowerCase() === 'l'

/**
 * Hosts that reserve Ctrl+L / Alt+L for themselves (embedded browser panes, app menus, the address bar) swallow
 * the keydown before the page sees it, but the key's release usually still reaches the page. A lock combo
 * whose keydown never arrived therefore locks on keyup. When the keydown did arrive, the shell binding has
 * already locked and the release is ignored. Ctrl+Shift+L (logout) is excluded.
 */
export function useLockKeyReleaseFallback(): void {
  useEffect(() => {
    let keydownSeen = false
    const down = (event: KeyboardEvent) => {
      if (isLKey(event)) keydownSeen = true
    }
    const up = (event: KeyboardEvent) => {
      if (!isLKey(event)) return
      const lockCombo = (event.ctrlKey || event.metaKey || event.altKey) && !event.shiftKey
      if (lockCombo && !keydownSeen) useAuthStore.getState().lock()
      keydownSeen = false
    }
    window.addEventListener('keydown', down, { capture: true })
    window.addEventListener('keyup', up, { capture: true })
    return () => {
      window.removeEventListener('keydown', down, { capture: true })
      window.removeEventListener('keyup', up, { capture: true })
    }
  }, [])
}
