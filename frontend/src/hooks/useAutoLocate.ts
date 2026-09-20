import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { FIX_SOURCE_LABEL, describeAccuracy, isLocalDeviceCamera, locateConsole } from '@/lib/geolocation'
import { usePermissions } from '@/stores/authStore'
import type { CameraWithAlertCount } from '@/types'

// v2: attempts made before the Windows location fallback existed must not block it.
const ATTEMPTED_KEY = 'sih26187-autolocate-attempted-v2'

function attempted(): Set<string> {
  try {
    return new Set(JSON.parse(window.sessionStorage.getItem(ATTEMPTED_KEY) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

function markAttempted(ids: string[]): void {
  try {
    window.sessionStorage.setItem(ATTEMPTED_KEY, JSON.stringify([...attempted(), ...ids]))
  } catch {
    /* best effort: at worst the browser asks again next load */
  }
}

/**
 * Cameras plugged into this console (webcam / USB device index) that have no GPS yet are placed at the
 * console's real position: browser location when already allowed, otherwise the console host's Windows
 * location service (no permission prompt is raised on page load). Coordinates entered by an operator are never
 * overwritten (only empty GPS is filled), and each camera is tried once per session so a failure does not nag.
 */
export function useAutoLocateLocalCameras(cameras: readonly CameraWithAlertCount[] | undefined): void {
  const queryClient = useQueryClient()
  const { canManageCameras } = usePermissions()
  const pending = (cameras ?? []).filter(
    (camera) => isLocalDeviceCamera(camera) && camera.gps_lat === null && camera.gps_lng === null,
  )
  const key = pending.map((camera) => camera.camera_id).join(',')

  useEffect(() => {
    if (!canManageCameras || !key) return
    const tried = attempted()
    const targets = key.split(',').filter((id) => !tried.has(id))
    if (!targets.length) return
    markAttempted(targets)
    // Not cancelled on unmount: the fix is written to the server, so a remount must not lose it.
    void locateConsole({ promptBrowser: false })
      .then(async (fix) => {
        for (const cameraId of targets) {
          const camera = (cameras ?? []).find((item) => item.camera_id === cameraId)
          const updated = await camerasApi.edit(cameraId, {
            gps_lat: fix.lat,
            gps_lng: fix.lng,
            ...(camera?.location_name ? {} : { location_name: `Auto-located console position (${describeAccuracy(fix.accuracy)}, ${FIX_SOURCE_LABEL[fix.source]})` }),
          })
          toast.success(`${updated.camera_id} located at ${fix.lat.toFixed(5)}, ${fix.lng.toFixed(5)} (${describeAccuracy(fix.accuracy)})`, { id: `locate-${cameraId}` })
        }
        void queryClient.invalidateQueries({ queryKey: ['cameras'] })
      })
      .catch((error: unknown) => {
        toast(`Camera location: ${error instanceof Error ? error.message : 'unavailable'}. Use Edit → Use current location or Pin on map.`, { id: 'locate-failed', icon: '📍', duration: 6_000 })
      })
    // `cameras` is read for names only; the effect is keyed on which cameras still need a location.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, canManageCameras, queryClient])
}
