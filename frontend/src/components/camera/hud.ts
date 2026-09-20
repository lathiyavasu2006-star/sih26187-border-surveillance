import { RISK_META, ZONE_META } from '@/lib/constants'
import type { WSDetection, Zone } from '@/types'

/** Geometry that maps frame pixels onto the canvas ("contain" fit with letterboxing). */
export interface Viewport {
  scale: number
  offsetX: number
  offsetY: number
  width: number
  height: number
}

export function fitContain(frameWidth: number, frameHeight: number, canvasWidth: number, canvasHeight: number): Viewport {
  if (frameWidth <= 0 || frameHeight <= 0 || canvasWidth <= 0 || canvasHeight <= 0) {
    return { scale: 1, offsetX: 0, offsetY: 0, width: frameWidth, height: frameHeight }
  }
  const scale = Math.min(canvasWidth / frameWidth, canvasHeight / frameHeight)
  const width = frameWidth * scale
  const height = frameHeight * scale
  return { scale, offsetX: (canvasWidth - width) / 2, offsetY: (canvasHeight - height) / 2, width, height }
}

export interface BoxRect {
  x: number
  y: number
  w: number
  h: number
}

/** Detection box in canvas coordinates, or null when the detection has no bounding box. */
export function detectionRect(detection: WSDetection, viewport: Viewport): BoxRect | null {
  const { bbox_x1: x1, bbox_y1: y1, bbox_x2: x2, bbox_y2: y2 } = detection
  if (x1 === null || y1 === null || x2 === null || y2 === null || x2 <= x1 || y2 <= y1) return null
  return {
    x: viewport.offsetX + x1 * viewport.scale,
    y: viewport.offsetY + y1 * viewport.scale,
    w: (x2 - x1) * viewport.scale,
    h: (y2 - y1) * viewport.scale,
  }
}

/** Returns the top-most detection under a canvas point (smallest box wins when boxes overlap). */
export function hitTestDetection(detections: readonly WSDetection[], viewport: Viewport, x: number, y: number): WSDetection | null {
  let best: WSDetection | null = null
  let bestArea = Number.POSITIVE_INFINITY
  for (const detection of detections) {
    const rect = detectionRect(detection, viewport)
    if (!rect) continue
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      const area = rect.w * rect.h
      if (area < bestArea) {
        best = detection
        bestArea = area
      }
    }
  }
  return best
}

export const HUD_CYAN = '#22d3ee'
const PILL_BG = 'rgba(2, 6, 23, 0.82)'
const MONO = '"JetBrains Mono", "Cascadia Mono", Consolas, ui-monospace, monospace'

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const radius = Math.min(r, h / 2, w / 2)
  ctx.beginPath()
  ctx.moveTo(x + radius, y)
  ctx.arcTo(x + w, y, x + w, y + h, radius)
  ctx.arcTo(x + w, y + h, x, y + h, radius)
  ctx.arcTo(x, y + h, x, y, radius)
  ctx.arcTo(x, y, x + w, y, radius)
  ctx.closePath()
}

/** Draws a text pill and returns its rectangle. */
export function drawPill(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  options: { background?: string; color?: string; size?: number; border?: string; dot?: string } = {},
): BoxRect {
  const size = options.size ?? 11
  ctx.font = `600 ${size}px ${MONO}`
  const padX = size * 0.6
  const dotSpace = options.dot ? size * 0.9 : 0
  const w = ctx.measureText(text).width + padX * 2 + dotSpace
  const h = size + 8
  roundedRect(ctx, x, y, w, h, h / 2)
  ctx.fillStyle = options.background ?? PILL_BG
  ctx.fill()
  if (options.border) {
    ctx.strokeStyle = options.border
    ctx.lineWidth = 1
    ctx.stroke()
  }
  if (options.dot) {
    ctx.beginPath()
    ctx.arc(x + padX + size * 0.3, y + h / 2, size * 0.28, 0, Math.PI * 2)
    ctx.fillStyle = options.dot
    ctx.fill()
  }
  ctx.fillStyle = options.color ?? '#ffffff'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, x + padX + dotSpace, y + h / 2 + 0.5)
  return { x, y, w, h }
}

/** Four L-shaped corner brackets around a rectangle. */
export function drawCornerBrackets(
  ctx: CanvasRenderingContext2D,
  rect: BoxRect,
  color: string,
  lineWidth = 2,
  lengthRatio = 0.22,
) {
  const len = Math.max(8, Math.min(rect.w, rect.h) * lengthRatio)
  const { x, y, w, h } = rect
  ctx.strokeStyle = color
  ctx.lineWidth = lineWidth
  ctx.lineCap = 'square'
  ctx.beginPath()
  ctx.moveTo(x, y + len)
  ctx.lineTo(x, y)
  ctx.lineTo(x + len, y)
  ctx.moveTo(x + w - len, y)
  ctx.lineTo(x + w, y)
  ctx.lineTo(x + w, y + len)
  ctx.moveTo(x + w, y + h - len)
  ctx.lineTo(x + w, y + h)
  ctx.lineTo(x + w - len, y + h)
  ctx.moveTo(x + len, y + h)
  ctx.lineTo(x, y + h)
  ctx.lineTo(x, y + h - len)
  ctx.stroke()
}

export function detectionLabel(detection: WSDetection): string {
  const cls = detection.object_class.toUpperCase()
  const confidence = detection.confidence !== null ? ` ${Math.round(detection.confidence * 100)}%` : ''
  return `${cls}${confidence}`
}

export function drawDetection(
  ctx: CanvasRenderingContext2D,
  detection: WSDetection,
  viewport: Viewport,
  options: { selected: boolean; compact: boolean },
) {
  const rect = detectionRect(detection, viewport)
  if (!rect) return
  const color = RISK_META[detection.risk_level].color
  const selected = options.selected

  if (selected) {
    ctx.save()
    ctx.shadowColor = HUD_CYAN
    ctx.shadowBlur = 12
    drawCornerBrackets(ctx, rect, HUD_CYAN, 3)
    ctx.restore()
    ctx.fillStyle = 'rgba(34, 211, 238, 0.08)'
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h)
  } else {
    ctx.strokeStyle = `${color}55`
    ctx.lineWidth = 1
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h)
    drawCornerBrackets(ctx, rect, color, 2)
  }

  const size = options.compact ? 9 : 11
  // Dark pill label under the box: class + confidence (+ risk when elevated).
  const risk = detection.risk_score > 20 ? ` · R${detection.risk_score}` : ''
  const flags = `${detection.loitering ? ' · LOITER' : ''}${detection.in_fence ? ' · FENCE' : ''}`
  const label = `${detectionLabel(detection)}${options.compact ? '' : risk + flags}`
  const labelY = Math.min(viewport.offsetY + viewport.height - (size + 10), rect.y + rect.h + 4)
  drawPill(ctx, label, rect.x, labelY, { size, dot: color })

  // Cyan TRACK-ID pill above the box with a line connector to the top-left bracket.
  const pillX = rect.x + Math.min(18, rect.w * 0.2)
  const pillY = Math.max(viewport.offsetY + 4, rect.y - (size + 8) - 14)
  ctx.font = `700 ${size}px ${MONO}`
  const pill = drawPill(ctx, `ID ${detection.track_id}`, pillX, pillY, {
    size,
    background: selected ? HUD_CYAN : 'rgba(8, 145, 178, 0.92)',
    color: selected ? '#020617' : '#ecfeff',
  })
  ctx.strokeStyle = HUD_CYAN
  ctx.lineWidth = 1.25
  ctx.beginPath()
  ctx.moveTo(rect.x, rect.y)
  ctx.lineTo(pill.x, pill.y + pill.h)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(rect.x, rect.y, 2.5, 0, Math.PI * 2)
  ctx.fillStyle = HUD_CYAN
  ctx.fill()
}

export function drawZones(ctx: CanvasRenderingContext2D, zones: readonly Zone[], viewport: Viewport, compact: boolean) {
  for (const zone of zones) {
    if (!zone.is_active || zone.polygon.length < 3) continue
    const color = ZONE_META[zone.zone_type]?.color ?? zone.color_hex
    ctx.save()
    ctx.setLineDash([6, 4])
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.beginPath()
    zone.polygon.forEach(([px, py], index) => {
      const x = viewport.offsetX + px * viewport.scale
      const y = viewport.offsetY + py * viewport.scale
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.closePath()
    ctx.stroke()
    ctx.restore()
    if (!compact) {
      const first = zone.polygon[0]
      if (first) {
        drawPill(ctx, `${zone.zone_name.toUpperCase()} · +${zone.risk_bonus}`, viewport.offsetX + first[0] * viewport.scale + 4, viewport.offsetY + first[1] * viewport.scale + 4, {
          size: 9,
          border: color,
        })
      }
    }
  }
}

export interface ChromeInfo {
  cameraId: string
  cameraName: string
  timestamp: string
  live: boolean
  fps: number | null
  latencyMs: number | null
  viewers: number
  people: number
  vehicles: number
  animals: number
  compact: boolean
  blink: boolean
}

/** Frame-level military HUD: corner frame, crosshair, camera identity, clock and telemetry. */
export function drawChrome(ctx: CanvasRenderingContext2D, width: number, height: number, info: ChromeInfo) {
  const margin = info.compact ? 6 : 10
  const size = info.compact ? 9 : 11

  drawCornerBrackets(ctx, { x: margin, y: margin, w: width - margin * 2, h: height - margin * 2 }, 'rgba(34, 211, 238, 0.85)', info.compact ? 1.5 : 2, 0.06)

  // Crosshair
  const cx = width / 2
  const cy = height / 2
  const arm = info.compact ? 6 : 10
  ctx.strokeStyle = 'rgba(34, 211, 238, 0.55)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(cx - arm * 2, cy)
  ctx.lineTo(cx - arm, cy)
  ctx.moveTo(cx + arm, cy)
  ctx.lineTo(cx + arm * 2, cy)
  ctx.moveTo(cx, cy - arm * 2)
  ctx.lineTo(cx, cy - arm)
  ctx.moveTo(cx, cy + arm)
  ctx.lineTo(cx, cy + arm * 2)
  ctx.stroke()

  const top = margin + 6
  const left = margin + 8
  const liveDot = info.live ? (info.blink ? '#ef4444' : 'rgba(239,68,68,0.35)') : '#94a3b8'
  const idPill = drawPill(ctx, info.live ? `LIVE · ${info.cameraId}` : `NO SIGNAL · ${info.cameraId}`, left, top, { size, dot: liveDot })
  if (!info.compact && info.cameraName) {
    drawPill(ctx, info.cameraName.toUpperCase(), idPill.x + idPill.w + 6, top, { size, color: '#cbd5e1' })
  }

  ctx.font = `600 ${size}px ${MONO}`
  const clock = `${info.timestamp} IST`
  const clockWidth = ctx.measureText(clock).width + size * 1.2
  drawPill(ctx, clock, width - margin - 8 - clockWidth, top, { size })

  const bottom = height - margin - 6 - (size + 8)
  const telemetry = [
    info.fps !== null ? `${info.fps.toFixed(1)} FPS` : '— FPS',
    info.latencyMs !== null ? `${Math.round(info.latencyMs)} MS` : null,
    info.compact ? null : `${info.viewers} VIEW`,
  ]
    .filter(Boolean)
    .join(' · ')
  drawPill(ctx, telemetry, left, bottom, { size, color: HUD_CYAN })

  const counts = `P ${info.people} · V ${info.vehicles} · A ${info.animals}`
  ctx.font = `600 ${size}px ${MONO}`
  const countsWidth = ctx.measureText(counts).width + size * 1.2
  drawPill(ctx, counts, width - margin - 8 - countsWidth, bottom, { size })
}

/** "No signal" background: dark grid with a diagonal hatch, drawn when no frame has arrived. */
export function drawNoSignal(ctx: CanvasRenderingContext2D, width: number, height: number, message: string, compact: boolean) {
  ctx.fillStyle = '#0b1220'
  ctx.fillRect(0, 0, width, height)
  ctx.strokeStyle = 'rgba(34, 211, 238, 0.06)'
  ctx.lineWidth = 1
  for (let x = 0; x < width; x += 24) {
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
  }
  for (let y = 0; y < height; y += 24) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }
  ctx.fillStyle = 'rgba(148, 163, 184, 0.9)'
  ctx.font = `600 ${compact ? 11 : 14}px ${MONO}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(message, width / 2, height / 2 + (compact ? 22 : 34))
  ctx.textAlign = 'start'
}
