import { useEffect } from 'react'
import { cameraSockets, type LiveEvent } from '@/lib/cameraSocket'
import { useLiveStore } from '@/stores/liveStore'
import type { LiveCameraState, WSConnectionState } from '@/types'

export interface CameraLiveView {
  connection: WSConnectionState
  detail: string | null
  live: LiveCameraState | undefined
}

/**
 * Subscribes to the live WebSocket of one camera for the lifetime of the component and returns its
 * connection state and latest frame metadata. Pass `null` to subscribe to nothing.
 * Components that draw frames should read `useLiveStore.getState()` / `subscribe` instead of re-rendering.
 */
export function useWebSocket(cameraId: string | null | undefined): CameraLiveView {
  const id = cameraId ? cameraId.toUpperCase() : null
  useEffect(() => {
    if (!id) return undefined
    return cameraSockets.subscribe(id)
  }, [id])

  const connection = useLiveStore((state) => (id ? state.connection[id] ?? 'idle' : 'idle'))
  const detail = useLiveStore((state) => (id ? state.connectionDetail[id] ?? null : null))
  const live = useLiveStore((state) => (id ? state.cameras[id] : undefined))
  return { connection, detail, live }
}

/** Keeps sockets open for several cameras (grids, panic mode) without subscribing to their state. */
export function useCameraSubscriptions(cameraIds: readonly string[]): void {
  const key = [...new Set(cameraIds.map((id) => id.toUpperCase()))].sort().join(',')
  useEffect(() => {
    if (!key) return undefined
    const releases = key.split(',').map((id) => cameraSockets.subscribe(id))
    return () => releases.forEach((release) => release())
  }, [key])
}

export function useLiveEvents(listener: (event: LiveEvent) => void): void {
  useEffect(() => cameraSockets.onEvent(listener), [listener])
}
