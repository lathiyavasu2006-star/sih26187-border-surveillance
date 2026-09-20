import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { clearProtectedObjectUrls } from '@/api/client'
import { authApi } from '@/api/endpoints'
import { cameraSockets } from '@/lib/cameraSocket'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'

/** Revokes the tokens server-side (best effort) and clears every trace of the session in this tab. */
export function useSignOut(): () => Promise<void> {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  return useCallback(async () => {
    const { refreshToken } = useAuthStore.getState()
    try {
      await authApi.logout(refreshToken)
    } catch {
      /* the local session is cleared regardless */
    }
    cameraSockets.closeAll()
    queryClient.clear()
    clearProtectedObjectUrls()
    useLiveStore.getState().reset()
    useUiStore.getState().setPanicActive(false)
    useUiStore.getState().closeModal()
    useAuthStore.getState().clearSession('You have been signed out')
    navigate('/login', { replace: true })
  }, [navigate, queryClient])
}
