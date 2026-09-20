import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { fetchProtectedObjectUrl } from '@/api/client'
import { camerasApi, eventsApi, hardwareApi, statsApi, zonesApi } from '@/api/endpoints'
import { POLL } from '@/lib/constants'
import { useLiveEvents } from '@/hooks/useWebSocket'
import type { LiveEvent } from '@/lib/cameraSocket'

export function useCameras() {
  return useQuery({ queryKey: ['cameras', 'list'], queryFn: () => camerasApi.list(), refetchInterval: POLL.cameras })
}

export function useCameraDetail(cameraId: string | null | undefined) {
  return useQuery({
    queryKey: ['cameras', 'detail', cameraId],
    queryFn: () => camerasApi.get(cameraId ?? ''),
    enabled: Boolean(cameraId),
    refetchInterval: POLL.cameras,
  })
}

export function useStats() {
  return useQuery({ queryKey: ['stats', 'live'], queryFn: statsApi.get, refetchInterval: POLL.stats })
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: statsApi.health, refetchInterval: POLL.health, retry: false })
}

export function useHardwareStatus() {
  return useQuery({ queryKey: ['hardware', 'status'], queryFn: hardwareApi.status, refetchInterval: POLL.hardware })
}

/** Zones of one camera, refreshed immediately when the backend broadcasts zone_update / zone_deleted. */
export function useCameraZones(cameraId: string | null | undefined, includeInactive = false) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['zones', 'camera', cameraId, includeInactive],
    queryFn: () => zonesApi.forCamera(cameraId ?? '', includeInactive),
    enabled: Boolean(cameraId),
    staleTime: 60_000,
  })
  const onEvent = useCallback(
    (event: LiveEvent) => {
      if (event.type === 'zones_changed' && event.cameraId === cameraId) {
        void queryClient.invalidateQueries({ queryKey: ['zones'] })
      }
    },
    [cameraId, queryClient],
  )
  useLiveEvents(onEvent)
  return query
}

export function useActiveEvents(cameraId?: string) {
  return useQuery({
    queryKey: ['events', 'active', cameraId ?? 'all'],
    queryFn: () => eventsApi.active(cameraId),
    refetchInterval: POLL.events,
  })
}

export function useTrackHistory(trackId: number | null, cameraId?: string) {
  return useQuery({
    queryKey: ['events', 'history', trackId, cameraId ?? 'all'],
    queryFn: () => eventsApi.history(trackId ?? 0, cameraId),
    enabled: trackId !== null,
    retry: false,
  })
}

export function useTrackTimeline(trackId: number | null, cameraId?: string) {
  return useQuery({
    queryKey: ['events', 'timeline', trackId, cameraId ?? 'all'],
    queryFn: () => eventsApi.timeline(trackId ?? 0, cameraId),
    enabled: trackId !== null,
    retry: false,
  })
}

export interface ProtectedFileState {
  url: string | null
  loading: boolean
  error: string | null
}

/** Object URL for an authenticated backend file; `enabled=false` defers the download (lazy galleries). */
export function useProtectedFile(requestPath: string | null, enabled = true): ProtectedFileState {
  const [result, setResult] = useState<{ path: string; url: string | null; error: string | null } | null>(null)
  const active = Boolean(requestPath && enabled)
  useEffect(() => {
    if (!requestPath || !enabled) return undefined
    let cancelled = false
    fetchProtectedObjectUrl(requestPath)
      .then((url) => {
        if (!cancelled) setResult({ path: requestPath, url, error: null })
      })
      .catch((error: unknown) => {
        if (!cancelled) setResult({ path: requestPath, url: null, error: error instanceof Error ? error.message : 'Download failed' })
      })
    return () => {
      cancelled = true
    }
  }, [requestPath, enabled])
  const current = active && result?.path === requestPath ? result : null
  return { url: current?.url ?? null, loading: active && current === null, error: current?.error ?? null }
}

/** Current time, re-rendering every `intervalMs`. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}
