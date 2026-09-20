import type { HeatPoint } from '@/components/map/HeatLayer'
import type { Alert, CameraWithAlertCount, ZoneType } from '@/types'

/** Border sectors for the N/E/W/S keys (approximate frontier bounding boxes). */
export const BORDER_REGIONS = {
  north: { label: 'Northern border (J&K · Ladakh · Punjab · Himachal)', bounds: [[29.5, 72.5], [37.2, 80.5]] },
  east: { label: 'Eastern border (Sikkim · North-East · Bengal)', bounds: [[21.5, 85.5], [29.6, 97.4]] },
  west: { label: 'Western border (Rajasthan · Gujarat)', bounds: [[20.0, 68.0], [30.6, 75.6]] },
  south: { label: 'Southern coast (Tamil Nadu · Kerala · Palk Strait)', bounds: [[7.6, 74.5], [16.5, 81.2]] },
} as const satisfies Record<string, { label: string; bounds: [[number, number], [number, number]] }>

export type BorderRegion = keyof typeof BORDER_REGIONS

/**
 * Geo-fence perimeters drawn around a camera on the map, innermost first. They use the zone-type palette of the
 * Zone Manager, so the map reads the same way as the in-frame virtual fences.
 */
export const GEOFENCE_RINGS = [
  { zoneType: 'restricted', radius: 100 },
  { zoneType: 'sensitive', radius: 250 },
  { zoneType: 'buffer', radius: 500 },
] as const satisfies readonly { zoneType: ZoneType; radius: number }[]

/** Map zoom used when the console focuses one camera (every ring is on screen at desktop sizes). */
export const CAMERA_FOCUS_ZOOM = 16

/** Weighted threat points. Alerts without GPS are placed at their camera's position when it is known. */
export function heatPoints(alerts: readonly Alert[], cameras: readonly CameraWithAlertCount[]): HeatPoint[] {
  const cameraPosition = new Map(
    cameras.filter((camera) => camera.gps_lat !== null && camera.gps_lng !== null).map((camera) => [camera.camera_id, [camera.gps_lat ?? 0, camera.gps_lng ?? 0] as const]),
  )
  const points: HeatPoint[] = []
  for (const alert of alerts) {
    const position = alert.gps_lat !== null && alert.gps_lng !== null ? ([alert.gps_lat, alert.gps_lng] as const) : cameraPosition.get(alert.camera_id)
    if (position) points.push([position[0], position[1], Math.max(0.2, alert.risk_score / 100)])
  }
  return points
}
