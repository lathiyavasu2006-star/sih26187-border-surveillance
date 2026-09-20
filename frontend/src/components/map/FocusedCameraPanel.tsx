import { Hexagon, LocateFixed, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button, StatusDot } from '@/components/ui/primitives'
import { useCameraZones } from '@/hooks/useData'
import { ZONE_META } from '@/lib/constants'
import { GEOFENCE_RINGS } from '@/lib/mapData'
import { useUiStore } from '@/stores/uiStore'
import type { CameraWithAlertCount } from '@/types'

/** Map overlay for the camera opened with /map?focus=ID: position, fence perimeters and its in-frame virtual fences. */
export function FocusedCameraPanel({
  camera,
  onClose,
  onLocate,
}: {
  camera: CameraWithAlertCount
  onClose: () => void
  onLocate?: () => void
}) {
  const navigate = useNavigate()
  const selectCamera = useUiStore((state) => state.selectCamera)
  const zones = useCameraZones(camera.camera_id)
  const placed = camera.gps_lat !== null && camera.gps_lng !== null
  const activeZones = (zones.data?.items ?? []).filter((zone) => zone.is_active)

  return (
    <div
      className="absolute left-14 top-3 z-[500] w-72 rounded-xl border border-line bg-white/95 text-xs shadow-lg backdrop-blur"
      data-testid="focused-camera-panel"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 font-mono text-[11px] font-semibold text-slate-900">
            <StatusDot status={camera.status} /> {camera.camera_id}
          </p>
          <p className="truncate text-[11px] text-muted">{camera.location_name ?? camera.name}</p>
        </div>
        <button type="button" onClick={onClose} className="rounded p-0.5 text-slate-500 hover:bg-slate-100" aria-label="Close camera focus">
          <X className="size-3.5" />
        </button>
      </div>
      {placed ? (
        <div className="space-y-2 px-3 py-2">
          <p className="font-mono text-[11px] text-slate-700">
            {camera.gps_lat?.toFixed(6)}, {camera.gps_lng?.toFixed(6)}
          </p>
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">Geo-fence perimeters</p>
            {GEOFENCE_RINGS.map((ring) => (
              <p key={ring.zoneType} className="flex items-center gap-2 py-0.5">
                <span className="size-3 rounded-full border-2" style={{ borderColor: ZONE_META[ring.zoneType].color, background: `${ZONE_META[ring.zoneType].color}22` }} />
                {ZONE_META[ring.zoneType].label} · {ring.radius} m
              </p>
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-2 px-3 py-2">
          <p className="text-[11px] text-muted">This camera has no GPS yet, so it is not drawn on the map.</p>
          {onLocate ? (
            <Button size="xs" variant="hud" icon={<LocateFixed className="size-3.5" />} onClick={onLocate} data-testid="focus-locate">
              Locate camera
            </Button>
          ) : null}
        </div>
      )}
      <div className="border-t border-line px-3 py-2">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
          Virtual fences on this camera ({zones.isLoading ? '…' : activeZones.length})
        </p>
        {activeZones.slice(0, 6).map((zone) => (
          <p key={zone.zone_id} className="flex items-center gap-2 py-0.5">
            <span className="size-2.5 rounded-sm" style={{ background: zone.color_hex || ZONE_META[zone.zone_type].color }} />
            <span className="truncate">{zone.zone_name}</span>
            <span className="ml-auto shrink-0 text-[10px] text-muted">{ZONE_META[zone.zone_type].label}</span>
          </p>
        ))}
        {!zones.isLoading && activeZones.length === 0 ? <p className="text-[11px] text-muted">No active fences drawn in the camera frame.</p> : null}
        <Button
          size="xs"
          className="mt-1.5"
          icon={<Hexagon className="size-3.5" />}
          onClick={() => {
            selectCamera(camera.camera_id)
            navigate('/zones')
          }}
        >
          Edit fences
        </Button>
      </div>
    </div>
  )
}
