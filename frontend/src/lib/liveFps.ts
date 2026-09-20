import type { LiveCameraState } from '@/types'

/** A camera contributes only while its last frame is younger than this. */
export const LIVE_FPS_MAX_AGE_MS = 5_000

/**
 * Mean processing FPS reported by the ML pipeline in the live WebSocket stream, over cameras that sent a
 * frame recently. Null when no camera is live (callers fall back to the backend's average).
 */
export function livePipelineFps(cameras: Record<string, LiveCameraState>, now: number = Date.now()): number | null {
  const values = Object.values(cameras)
    .filter((camera) => camera.stats && now - camera.frameReceivedAt < LIVE_FPS_MAX_AGE_MS)
    .map((camera) => camera.stats?.fps ?? 0)
  if (values.length === 0) return null
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10
}
