import { Circle, Tooltip } from 'react-leaflet'
import { ZONE_META } from '@/lib/constants'
import { GEOFENCE_RINGS } from '@/lib/mapData'
import type { Camera } from '@/types'

/** Geo-fence perimeter rings around each placed camera (outermost drawn first so inner rings stay hoverable). */
export function GeoFences({ cameras }: { cameras: readonly Pick<Camera, 'camera_id' | 'gps_lat' | 'gps_lng'>[] }) {
  const outermostFirst = [...GEOFENCE_RINGS].reverse()
  return (
    <>
      {cameras.flatMap((camera) => {
        if (camera.gps_lat === null || camera.gps_lng === null) return []
        const center: [number, number] = [camera.gps_lat, camera.gps_lng]
        return outermostFirst.map((ring) => {
          const meta = ZONE_META[ring.zoneType]
          return (
            <Circle
              key={`${camera.camera_id}:${ring.zoneType}`}
              center={center}
              radius={ring.radius}
              pathOptions={{
                color: meta.color,
                weight: ring.zoneType === 'restricted' ? 2.5 : 2,
                dashArray: ring.zoneType === 'buffer' ? '8 6' : undefined,
                fillColor: meta.color,
                fillOpacity: 0.07,
              }}
            >
              <Tooltip sticky>
                {camera.camera_id} · {meta.label} perimeter · {ring.radius} m
              </Tooltip>
            </Circle>
          )
        })
      })}
    </>
  )
}
