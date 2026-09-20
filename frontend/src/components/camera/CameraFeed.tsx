import { useEffect, useRef, type MouseEvent } from 'react'
import {
  drawChrome,
  drawDetection,
  drawNoSignal,
  drawZones,
  fitContain,
  hitTestDetection,
  type Viewport,
} from '@/components/camera/hud'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn, formatTime } from '@/lib/utils'
import { DETECTION_HOLD_MS, useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'
import type { LiveCameraState, WSDetection, Zone } from '@/types'

/** A frame older than this is treated as a lost signal (the ML pipeline publishes at ~10 fps). */
const STALE_FRAME_MS = 5_000

function base64ToBlob(base64: string): Blob {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: 'image/jpeg' })
}

export interface CameraFeedProps {
  cameraId: string
  cameraName?: string
  compact?: boolean
  zones?: readonly Zone[]
  interactive?: boolean
  className?: string
  onDoubleClick?: () => void
  /** Called with the detection under the pointer (null when clicking empty space). */
  onSelectDetection?: (detection: WSDetection | null) => void
  /** Freeze the picture on the current frame (the socket keeps receiving). */
  paused?: boolean
  /** Receives the canvas element (for recording the operator's view). */
  onCanvasReady?: (canvas: HTMLCanvasElement | null) => void
}

/**
 * Live camera canvas. Frames arrive as base64 JPEG inside frame_update messages; they are decoded off the
 * main thread with createImageBitmap and drawn with a vector HUD (corner brackets, dark pill labels, cyan
 * TRACK-ID pills with connectors, zones, telemetry). Rendering reads the live store directly so incoming
 * frames never re-render React.
 */
export function CameraFeed({
  cameraId,
  cameraName = '',
  compact = false,
  zones = [],
  interactive = true,
  className,
  onDoubleClick,
  onSelectDetection,
  paused = false,
  onCanvasReady,
}: CameraFeedProps) {
  const id = cameraId.toUpperCase()
  const { connection, detail } = useWebSocket(id)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const bitmapRef = useRef<ImageBitmap | null>(null)
  const viewportRef = useRef<Viewport | null>(null)
  const dirtyRef = useRef(true)
  const hudEnabled = useUiStore((state) => state.hudEnabled)
  const selectedTrack = useUiStore((state) => state.selectedTrack)

  // Latest props for the render loop without restarting it.
  const renderProps = useRef({ zones, hudEnabled, selectedTrack, compact, cameraName, connection, detail, paused })
  useEffect(() => {
    renderProps.current = { zones, hudEnabled, selectedTrack, compact, cameraName, connection, detail, paused }
    dirtyRef.current = true
  }, [zones, hudEnabled, selectedTrack, compact, cameraName, connection, detail, paused])

  useEffect(() => {
    onCanvasReady?.(canvasRef.current)
    return () => onCanvasReady?.(null)
  }, [onCanvasReady])

  // Frame decoding: latest frame wins, at most one decode in flight.
  useEffect(() => {
    let disposed = false
    let decoding = false
    let pending: string | null = null
    let lastFrame: string | null = null

    const decode = (frame: string) => {
      if (decoding) {
        pending = frame
        return
      }
      decoding = true
      let blob: Blob
      try {
        blob = base64ToBlob(frame)
      } catch {
        decoding = false
        return
      }
      createImageBitmap(blob)
        .then((bitmap) => {
          if (disposed) {
            bitmap.close()
            return
          }
          bitmapRef.current?.close()
          bitmapRef.current = bitmap
          useLiveStore.getState().setFrameSize(id, bitmap.width, bitmap.height)
          dirtyRef.current = true
        })
        .catch(() => undefined)
        .finally(() => {
          decoding = false
          if (!disposed && pending) {
            const next = pending
            pending = null
            decode(next)
          }
        })
    }

    const handle = (state: LiveCameraState | undefined) => {
      if (renderProps.current.paused) return
      if (state?.frame && state.frame !== lastFrame) {
        lastFrame = state.frame
        decode(state.frame)
      }
      dirtyRef.current = true
    }
    handle(useLiveStore.getState().cameras[id])
    const unsubscribe = useLiveStore.subscribe((state, previous) => {
      if (state.cameras[id] !== previous.cameras[id]) handle(state.cameras[id])
    })
    return () => {
      disposed = true
      unsubscribe()
      bitmapRef.current?.close()
      bitmapRef.current = null
    }
  }, [id])

  // Canvas size follows the container (device-pixel accurate).
  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    if (!container || !canvas) return undefined
    const observer = new ResizeObserver(() => {
      const ratio = window.devicePixelRatio || 1
      const { width, height } = container.getBoundingClientRect()
      canvas.width = Math.max(1, Math.round(width * ratio))
      canvas.height = Math.max(1, Math.round(height * ratio))
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      dirtyRef.current = true
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  // Render loop: redraws only when something changed, plus twice a second for the clock / REC blink.
  useEffect(() => {
    let raf = 0
    let lastTick = 0
    const render = (time: number) => {
      raf = window.requestAnimationFrame(render)
      if (time - lastTick > 500) {
        lastTick = time
        dirtyRef.current = true
      }
      if (!dirtyRef.current) return
      dirtyRef.current = false
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (!canvas || !ctx) return

      const ratio = window.devicePixelRatio || 1
      const width = canvas.width / ratio
      const height = canvas.height / ratio
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      ctx.clearRect(0, 0, width, height)

      const props = renderProps.current
      const live = useLiveStore.getState().cameras[id]
      const bitmap = bitmapRef.current
      const now = Date.now()
      const fresh = Boolean(live && bitmap && (props.paused || now - live.frameReceivedAt < STALE_FRAME_MS))

      if (bitmap) {
        const viewport = fitContain(bitmap.width, bitmap.height, width, height)
        viewportRef.current = viewport
        ctx.fillStyle = '#020617'
        ctx.fillRect(0, 0, width, height)
        ctx.globalAlpha = fresh ? 1 : 0.35
        ctx.drawImage(bitmap, viewport.offsetX, viewport.offsetY, viewport.width, viewport.height)
        ctx.globalAlpha = 1
      } else {
        viewportRef.current = null
      }

      if (!fresh) {
        const message =
          props.connection === 'open'
            ? 'AWAITING VIDEO FROM ML PIPELINE'
            : props.connection === 'denied'
              ? 'ACCESS DENIED'
              : props.connection === 'reconnecting'
                ? 'RECONNECTING…'
                : 'CONNECTING…'
        if (!bitmap) drawNoSignal(ctx, width, height, message, props.compact)
        else {
          ctx.fillStyle = 'rgba(2, 6, 23, 0.55)'
          ctx.fillRect(0, 0, width, height)
          ctx.fillStyle = '#fca5a5'
          ctx.font = `700 ${props.compact ? 11 : 14}px ui-monospace, monospace`
          ctx.textAlign = 'center'
          ctx.fillText('SIGNAL LOST', width / 2, height / 2)
          ctx.textAlign = 'start'
        }
      }

      if (props.hudEnabled && viewportRef.current && fresh && live) {
        const viewport = viewportRef.current
        drawZones(ctx, props.zones, viewport, props.compact)
        const detections = now - live.detectionsReceivedAt <= DETECTION_HOLD_MS ? live.detections : []
        for (const detection of detections) {
          const selected =
            props.selectedTrack?.cameraId === id && props.selectedTrack.trackId === detection.track_id
          drawDetection(ctx, detection, viewport, { selected, compact: props.compact })
        }
      }

      drawChrome(ctx, width, height, {
        cameraId: id,
        cameraName: props.cameraName,
        timestamp: formatTime(now),
        live: fresh,
        fps: live?.stats?.fps ?? (live?.measuredFps ? live.measuredFps : null),
        latencyMs: fresh ? live?.latencyMs ?? null : null,
        viewers: live?.viewerCount ?? 0,
        people: fresh ? live?.stats?.people_count ?? 0 : 0,
        vehicles: fresh ? live?.stats?.vehicle_count ?? 0 : 0,
        animals: fresh ? live?.stats?.animal_count ?? 0 : 0,
        compact: props.compact,
        blink: Math.floor(now / 600) % 2 === 0,
      })
      if (props.paused) {
        const label = 'PAUSED · SPACE TO RESUME'
        ctx.font = `700 ${props.compact ? 10 : 13}px ui-monospace, monospace`
        const w = ctx.measureText(label).width + 24
        ctx.fillStyle = 'rgba(2, 6, 23, 0.85)'
        ctx.fillRect(width / 2 - w / 2, height * 0.12, w, props.compact ? 20 : 26)
        ctx.fillStyle = '#fbbf24'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(label, width / 2, height * 0.12 + (props.compact ? 10 : 13))
        ctx.textAlign = 'start'
      }
    }
    raf = window.requestAnimationFrame(render)
    return () => window.cancelAnimationFrame(raf)
  }, [id])

  const handleClick = (event: MouseEvent<HTMLCanvasElement>) => {
    if (!interactive || !onSelectDetection) return
    const viewport = viewportRef.current
    const live = useLiveStore.getState().cameras[id]
    if (!viewport || !live) {
      onSelectDetection(null)
      return
    }
    const rect = event.currentTarget.getBoundingClientRect()
    const hit = hitTestDetection(live.detections, viewport, event.clientX - rect.left, event.clientY - rect.top)
    onSelectDetection(hit)
  }

  return (
    <div ref={containerRef} className={cn('relative h-full w-full overflow-hidden bg-command', className)}>
      <canvas
        ref={canvasRef}
        className={cn('absolute inset-0 block', interactive && onSelectDetection ? 'cursor-crosshair' : '')}
        onClick={handleClick}
        onDoubleClick={onDoubleClick}
        role="img"
        aria-label={`Live feed ${id}`}
        data-testid={`camera-feed-${id}`}
        data-connection={connection}
      />
    </div>
  )
}
