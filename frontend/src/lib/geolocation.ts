import { camerasApi } from '@/api/endpoints'
import type { Camera } from '@/types'

export interface GeoFix {
  lat: number
  lng: number
  /** 95 % confidence radius in metres; null when the source does not report one. */
  accuracy: number | null
}

/** Where a fix came from: the operator's browser, or the console host's Windows location service. */
export type FixSource = 'browser' | 'windows'

export interface LocatedFix extends GeoFix {
  source: FixSource
}

export type BrowserLocationPermission = PermissionState | 'unsupported'

export const FIX_SOURCE_LABEL: Record<FixSource, string> = {
  browser: 'browser location',
  windows: 'Windows location service',
}

/**
 * Current position of this console from the browser Geolocation API (asks the operator's permission; works
 * on localhost/HTTPS). Coordinates are rounded to 6 decimals (~0.1 m).
 */
export function currentPosition(timeoutMs = 15_000): Promise<GeoFix> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      reject(new Error('This browser cannot report its location'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          lat: Number(position.coords.latitude.toFixed(6)),
          lng: Number(position.coords.longitude.toFixed(6)),
          accuracy: Math.round(position.coords.accuracy),
        }),
      (error) => {
        const reason =
          error.code === error.PERMISSION_DENIED
            ? 'Location permission is blocked in this browser'
            : error.code === error.TIMEOUT
              ? 'Location request timed out'
              : 'Location is unavailable in this browser'
        reject(new Error(reason))
      },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 60_000 },
    )
  })
}

/** The browser's geolocation permission without triggering a prompt. */
export async function browserLocationPermission(): Promise<BrowserLocationPermission> {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return 'unsupported'
  try {
    return (await navigator.permissions.query({ name: 'geolocation' })).state
  } catch {
    return 'prompt' // Permissions API missing: the only way to know is to ask
  }
}

/**
 * Locates this console: the browser first (it prompts the operator when `promptBrowser` is set), then the
 * console host's Windows location service. Embedded browsers and kiosk shells often have geolocation blocked,
 * and the host is where local (USB / webcam) cameras are physically attached.
 */
export async function locateConsole({
  promptBrowser = true,
  onStep,
}: { promptBrowser?: boolean; onStep?: (source: FixSource) => void } = {}): Promise<LocatedFix> {
  const permission = await browserLocationPermission()
  let browserProblem = 'This browser cannot report its location'
  if (permission === 'granted' || (promptBrowser && permission === 'prompt')) {
    onStep?.('browser')
    try {
      // A pending permission prompt counts against the timeout, so give the operator time to answer it.
      return { ...(await currentPosition(permission === 'granted' ? 10_000 : 30_000)), source: 'browser' }
    } catch (error) {
      browserProblem = error instanceof Error ? error.message : 'Browser location failed'
    }
  } else if (permission === 'denied') {
    browserProblem = 'Location permission is blocked in this browser'
  } else if (permission === 'prompt') {
    browserProblem = 'Browser location not requested'
  }

  onStep?.('windows')
  try {
    const host = await camerasApi.hostLocation()
    return { lat: host.lat, lng: host.lng, accuracy: host.accuracy_m, source: 'windows' }
  } catch (error) {
    const windowsProblem = error instanceof Error ? error.message : 'Windows location service failed'
    throw new Error(`${browserProblem}. ${windowsProblem}.`.replace(/\.\./g, '.'))
  }
}

/**
 * A camera attached to the console machine (USB/webcam device index). Its physical position is the
 * console's position, so it can be located automatically. Network (RTSP) cameras cannot.
 */
export function isLocalDeviceCamera(camera: Pick<Camera, 'device_id' | 'rtsp_url'>): boolean {
  const device = (camera.device_id ?? '').trim()
  const url = (camera.rtsp_url ?? '').trim()
  return /^\d+$/.test(device) || /^\d+$/.test(url)
}

export function describeAccuracy(accuracy: number | null): string {
  if (accuracy === null) return 'accuracy not reported'
  return accuracy >= 1000 ? `±${(accuracy / 1000).toFixed(1)} km` : `±${accuracy} m`
}
