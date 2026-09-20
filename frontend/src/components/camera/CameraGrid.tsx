import { Maximize2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { StatusDot } from '@/components/ui/primitives'
import { cn, titleCase } from '@/lib/utils'
import { useUiStore, type GridSize } from '@/stores/uiStore'
import type { CameraWithAlertCount } from '@/types'

export function CameraGrid({ cameras, size, className }: { cameras: readonly CameraWithAlertCount[]; size: GridSize; className?: string }) {
  const navigate = useNavigate()
  const selectedCameraId = useUiStore((state) => state.selectedCameraId)
  const selectCamera = useUiStore((state) => state.selectCamera)
  const slots = size * size
  const visible = cameras.slice(0, slots)

  return (
    <div
      className={cn('grid gap-2', className)}
      style={{ gridTemplateColumns: `repeat(${size}, minmax(0, 1fr))` }}
      data-testid="camera-grid"
      data-grid-size={size}
    >
      {visible.map((camera) => {
        const selected = camera.camera_id === selectedCameraId
        return (
          <div
            key={camera.camera_id}
            className={cn(
              'group relative overflow-hidden rounded-xl border bg-command shadow-sm',
              selected ? 'border-cyan-400 ring-2 ring-cyan-400/40' : 'border-slate-800',
            )}
          >
            <div className="aspect-video" onClick={() => selectCamera(camera.camera_id)}>
              <CameraFeed
                cameraId={camera.camera_id}
                cameraName={camera.name}
                compact={size >= 3}
                interactive={false}
                onDoubleClick={() => navigate(`/cameras/${camera.camera_id}`)}
              />
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-slate-800 bg-slate-950 px-2.5 py-1.5 text-[11px] text-slate-300">
              <span className="flex min-w-0 items-center gap-1.5">
                <StatusDot status={camera.status} />
                <span className="truncate font-mono">{camera.camera_id}</span>
                {size <= 2 ? <span className="truncate text-slate-500">· {camera.location_name ?? titleCase(camera.zone_region)}</span> : null}
              </span>
              <span className="flex items-center gap-2">
                {camera.active_alert_count > 0 ? (
                  <span className="rounded bg-red-600 px-1.5 font-mono text-[10px] font-bold text-white">{camera.active_alert_count}</span>
                ) : null}
                <button
                  type="button"
                  onClick={() => navigate(`/cameras/${camera.camera_id}`)}
                  className="rounded p-0.5 text-slate-400 hover:bg-white/10 hover:text-white"
                  aria-label={`Open ${camera.camera_id}`}
                >
                  <Maximize2 className="size-3.5" />
                </button>
              </span>
            </div>
          </div>
        )
      })}
      {Array.from({ length: Math.max(0, Math.min(slots, Math.max(cameras.length, 1)) - visible.length) }, (_, index) => (
        <div key={`empty-${index}`} className="hud-grid-bg aspect-video rounded-xl border border-dashed border-slate-700" />
      ))}
    </div>
  )
}
