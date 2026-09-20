import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'
import type { CameraWithAlertCount } from '@/types'

export const DOCK_HEIGHT = 80
const THUMB_REFRESH_MS = 250
const STALE_MS = 5_000

const STATUS_DOT: Record<string, string> = { online: 'bg-green-500', degraded: 'bg-amber-400', offline: 'bg-red-500' }

/** Small live preview: redraws at most 4×/s from the frames the shell's camera sockets already receive. */
function LiveThumbnail({ cameraId }: { cameraId: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    let disposed = false
    let lastFrame: string | null = null
    let decoding = false

    const draw = async () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      const live = useLiveStore.getState().cameras[cameraId]
      if (!canvas || !ctx) return
      const fresh = live?.frame && Date.now() - live.frameReceivedAt < STALE_MS
      if (!fresh || !live.frame) {
        ctx.fillStyle = '#0b1220'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.fillStyle = 'rgba(148,163,184,0.8)'
        ctx.font = '600 9px ui-monospace, monospace'
        ctx.textAlign = 'center'
        ctx.fillText('NO SIGNAL', canvas.width / 2, canvas.height / 2 + 3)
        lastFrame = null
        return
      }
      if (live.frame === lastFrame || decoding) return
      decoding = true
      lastFrame = live.frame
      try {
        const binary = atob(live.frame)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
        const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/jpeg' }), {
          resizeWidth: canvas.width,
          resizeHeight: canvas.height,
          resizeQuality: 'low',
        })
        if (!disposed) ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
        bitmap.close()
      } catch {
        /* a corrupt frame is skipped; the next one replaces it */
      } finally {
        decoding = false
      }
    }

    void draw()
    const timer = window.setInterval(() => void draw(), THUMB_REFRESH_MS)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [cameraId])

  return <canvas ref={canvasRef} width={240} height={140} className="h-[70px] w-[120px] rounded bg-command" aria-hidden />
}

function DockItem({ camera, active }: { camera: CameraWithAlertCount; active: boolean }) {
  const navigate = useNavigate()
  const selectCamera = useUiStore((state) => state.selectCamera)
  const fps = useLiveStore((state) => {
    const live = state.cameras[camera.camera_id]
    return live && Date.now() - live.frameReceivedAt < STALE_MS ? (live.stats?.fps ?? live.measuredFps) : null
  })
  return (
    <button
      type="button"
      onClick={() => {
        selectCamera(camera.camera_id)
        navigate(`/cameras/${camera.camera_id}`)
      }}
      className={cn(
        'relative h-[70px] w-[120px] shrink-0 overflow-hidden rounded border-2 transition-colors',
        active ? 'border-blue-500 shadow-[0_0_0_2px_rgba(59,130,246,0.35)]' : 'border-white/10 hover:border-white/40',
      )}
      title={`${camera.camera_id} · ${camera.location_name ?? camera.name} · ${camera.status}`}
      aria-current={active}
      data-testid={`dock-${camera.camera_id}`}
    >
      <LiveThumbnail cameraId={camera.camera_id} />
      <span className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/80 to-transparent px-1.5 py-0.5">
        <span className="flex items-center gap-1 font-mono text-[9px] font-bold text-white">
          <span className={cn('size-1.5 rounded-full', STATUS_DOT[camera.status] ?? 'bg-slate-400')} />
          {camera.camera_id}
        </span>
        {camera.active_alert_count > 0 ? (
          <span className="rounded-sm bg-red-600 px-1 font-mono text-[9px] font-bold leading-3 text-white">{camera.active_alert_count > 99 ? '99+' : camera.active_alert_count}</span>
        ) : null}
      </span>
      <span className="absolute bottom-0.5 right-1 rounded-sm bg-black/70 px-1 font-mono text-[8px] text-cyan-300">
        {fps !== null ? `${fps.toFixed(1)} FPS` : 'OFFLINE'}
      </span>
    </button>
  )
}

/** Fixed bottom strip of every accessible camera; the camera being viewed has a blue border. */
export function CameraDock({ cameras }: { cameras: readonly CameraWithAlertCount[] }) {
  const selectedCameraId = useUiStore((state) => state.selectedCameraId)
  return (
    <div
      className="scrollbar-thin flex shrink-0 items-center gap-2 overflow-x-auto border-t border-black/40 px-3"
      style={{ height: DOCK_HEIGHT, background: '#1a1a2e' }}
      data-testid="camera-dock"
      aria-label="Camera strip"
    >
      <span className="mr-1 shrink-0 font-mono text-[9px] font-bold uppercase leading-tight tracking-widest text-slate-500 [writing-mode:vertical-rl] rotate-180">
        CAMERAS
      </span>
      {cameras.length === 0 ? (
        <span className="text-xs text-slate-500">No cameras registered</span>
      ) : (
        cameras.map((camera) => <DockItem key={camera.camera_id} camera={camera} active={camera.camera_id === selectedCameraId} />)
      )}
    </div>
  )
}
