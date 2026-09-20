import { useNavigate } from 'react-router-dom'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { StatusDot } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import type { CameraWithAlertCount } from '@/types'

/** Horizontal strip of live thumbnails; the active camera is highlighted. */
export function CameraThumbStrip({ cameras, activeId }: { cameras: readonly CameraWithAlertCount[]; activeId: string }) {
  const navigate = useNavigate()
  return (
    <div className="scrollbar-thin flex gap-2 overflow-x-auto pb-1" data-testid="camera-thumb-strip">
      {cameras.map((camera) => {
        const active = camera.camera_id === activeId
        return (
          <button
            type="button"
            key={camera.camera_id}
            onClick={() => navigate(`/cameras/${camera.camera_id}`)}
            className={cn(
              'relative w-48 shrink-0 overflow-hidden rounded-lg border text-left transition-shadow',
              active ? 'border-cyan-500 ring-2 ring-cyan-400/40' : 'border-line hover:shadow-md',
            )}
            aria-current={active}
          >
            <div className="aspect-video">
              {active ? (
                <div className="hud-grid-bg flex h-full items-center justify-center font-mono text-[10px] text-cyan-300">VIEWING</div>
              ) : (
                <CameraFeed cameraId={camera.camera_id} cameraName={camera.name} compact interactive={false} />
              )}
            </div>
            <div className="flex items-center gap-1.5 bg-white px-2 py-1 text-[11px]">
              <StatusDot status={camera.status} />
              <span className="font-mono font-semibold">{camera.camera_id}</span>
              {camera.active_alert_count > 0 ? (
                <span className="ml-auto rounded bg-red-600 px-1 font-mono text-[10px] font-bold text-white">{camera.active_alert_count}</span>
              ) : null}
            </div>
          </button>
        )
      })}
    </div>
  )
}
