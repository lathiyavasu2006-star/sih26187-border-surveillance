import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { sessionJSONStorage } from '@/lib/storage'
import type { LoginUserInfo, TokenResponse, UserRole } from '@/types'

const ROLE_RANK: Record<UserRole, number> = { operator: 1, supervisor: 2, regional_head: 3, admin: 4 }

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  /** Epoch milliseconds at which the access token expires. */
  accessExpiresAt: number | null
  user: LoginUserInfo | null
  /** Screen lock survives a reload, so refreshing the page can never bypass it. */
  locked: boolean
  lockedAt: number | null
  /** Why the last session ended (shown once on the login page). */
  sessionMessage: string | null

  setSession: (response: TokenResponse) => void
  setAccessToken: (token: string, expiresInSeconds: number) => void
  clearSession: (message?: string) => void
  consumeSessionMessage: () => string | null
  lock: () => void
  unlock: (response: TokenResponse) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      accessExpiresAt: null,
      user: null,
      locked: false,
      lockedAt: null,
      sessionMessage: null,

      setSession: (response) =>
        set({
          accessToken: response.access_token,
          refreshToken: response.refresh_token,
          accessExpiresAt: Date.now() + response.expires_in * 1000,
          user: response.user,
          locked: false,
          lockedAt: null,
          sessionMessage: null,
        }),

      setAccessToken: (token, expiresInSeconds) =>
        set({ accessToken: token, accessExpiresAt: Date.now() + expiresInSeconds * 1000 }),

      clearSession: (message) =>
        set({
          accessToken: null,
          refreshToken: null,
          accessExpiresAt: null,
          user: null,
          locked: false,
          lockedAt: null,
          sessionMessage: message ?? null,
        }),

      consumeSessionMessage: () => {
        const message = get().sessionMessage
        if (message) set({ sessionMessage: null })
        return message
      },

      lock: () => {
        if (get().user && !get().locked) set({ locked: true, lockedAt: Date.now() })
      },

      unlock: (response) => {
        const current = get().user
        // Unlocking must re-authenticate the same operator; a different account starts a new session.
        if (current && current.user_id !== response.user.user_id) {
          get().setSession(response)
          return
        }
        set({
          accessToken: response.access_token,
          refreshToken: response.refresh_token,
          accessExpiresAt: Date.now() + response.expires_in * 1000,
          user: response.user,
          locked: false,
          lockedAt: null,
        })
      },
    }),
    {
      name: 'sih26187-session',
      storage: sessionJSONStorage,
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        accessExpiresAt: state.accessExpiresAt,
        user: state.user,
        locked: state.locked,
        lockedAt: state.lockedAt,
      }),
    },
  ),
)

export function hasRole(role: UserRole | undefined, minimum: UserRole): boolean {
  if (!role) return false
  return ROLE_RANK[role] >= ROLE_RANK[minimum]
}

/** Permission helpers matching backend dependencies (get_supervisor_or_above, get_admin_user, ...). */
export function usePermissions() {
  const role = useAuthStore((state) => state.user?.role)
  return {
    role,
    canManageCameras: hasRole(role, 'supervisor'),
    canManageZones: hasRole(role, 'supervisor'),
    canUploadEvidence: hasRole(role, 'supervisor'),
    canTestHardware: hasRole(role, 'supervisor'),
    canViewHealthHistory: role === 'admin' || role === 'regional_head',
    isAdmin: role === 'admin',
  }
}
