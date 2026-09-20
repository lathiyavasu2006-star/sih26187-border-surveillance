/**
 * Client-side analysis used by the Event DNA, Correlation, Confidence Fusion and Prediction views.
 * Every result is derived from backend data (tracks, timelines, alerts, zones); nothing is simulated.
 */
import { NIGHT_WINDOW, ZONE_META } from '@/lib/constants'
import { pointInPolygon } from '@/lib/utils'
import type { Direction, TimelinePoint, TrackEvent, WSDetection, Zone, ZoneType } from '@/types'

// ------------------------------------------------------------------ risk reasons

/** Mirrors ml/risk_engine.py constants. */
export const RISK_ENGINE = {
  base: 10,
  loitering: 20,
  towardFence: 20,
  repeatAppearance: 15,
  running: 25,
  weapon: 50,
} as const

export interface RiskComponent {
  key: string
  label: string
  points: number
  detail: string | null
}

export interface RiskBreakdown {
  base: number
  components: RiskComponent[]
  multiplier: number | null
  /** Score implied by the components (before the 100 cap). */
  implied: number
  reported: number
  /** reported - min(100, implied): non-zero when the explanation is incomplete. */
  residual: number
}

const REASON_LABELS: Record<string, string> = {
  loitering: 'Loitering',
  toward_fence: 'Moving toward fence',
  repeat_appearance: 'Repeat appearance',
  running: 'Running',
  weapon_detected: 'Weapon detected',
}

/**
 * Parses ML risk reasons such as "zone_restricted+50", "loitering+20(threshold:30s,dwell:41s)" and
 * "night_multiplier_x1.5" into additive components.
 */
export function parseRiskReasons(reasons: readonly string[], reportedScore: number): RiskBreakdown {
  const components: RiskComponent[] = []
  let multiplier: number | null = null
  for (const reason of reasons) {
    const night = /^night_multiplier_x([\d.]+)$/.exec(reason)
    if (night?.[1]) {
      multiplier = Number.parseFloat(night[1])
      continue
    }
    const match = /^([a-z_]+?)\+(\d+)(?:\((.*)\))?$/.exec(reason)
    if (!match?.[1] || !match[2]) {
      components.push({ key: reason, label: reason.replaceAll('_', ' '), points: 0, detail: null })
      continue
    }
    const key = match[1]
    const points = Number.parseInt(match[2], 10)
    const zone = /^zone_(.+)$/.exec(key)
    const zoneType = zone?.[1] as ZoneType | undefined
    const label = zoneType ? `Zone policy · ${ZONE_META[zoneType]?.label ?? zoneType}` : (REASON_LABELS[key] ?? key.replaceAll('_', ' '))
    components.push({ key, label, points, detail: match[3] ?? null })
  }
  const additive = RISK_ENGINE.base + components.reduce((sum, component) => sum + component.points, 0)
  const implied = multiplier ? Math.round(additive * multiplier) : additive
  return {
    base: RISK_ENGINE.base,
    components,
    multiplier,
    implied,
    reported: reportedScore,
    residual: reportedScore - Math.min(100, implied),
  }
}

export function istHour(date: Date): number {
  const hour = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', hourCycle: 'h23' }).format(date)
  return Number.parseInt(hour, 10)
}

export function isNightHour(hour: number, start: number = NIGHT_WINDOW.start, end: number = NIGHT_WINDOW.end): boolean {
  return start > end ? hour >= start || hour < end : hour >= start && hour < end
}

/**
 * Explains a live detection's risk score from the fields present in the WebSocket payload (zone type,
 * loitering, night window). Factors the payload does not carry (direction toward fence, repeat
 * appearance, running, weapon) remain in the residual.
 */
export function explainDetectionRisk(detection: WSDetection, at: Date, zones: readonly Zone[] = []): RiskBreakdown {
  const reasons: string[] = []
  if (detection.zone_type) {
    const zone = zones.find((candidate) => candidate.zone_name === detection.zone_name && candidate.zone_type === detection.zone_type)
    const bonus = zone?.risk_bonus ?? ZONE_META[detection.zone_type].bonus
    if (bonus > 0) reasons.push(`zone_${detection.zone_type}+${bonus}`)
  }
  if (detection.loitering) reasons.push(`loitering+${RISK_ENGINE.loitering}(dwell:${detection.time_in_zone_seconds}s)`)
  if (isNightHour(istHour(at))) reasons.push(`night_multiplier_x${NIGHT_WINDOW.multiplier}`)
  return parseRiskReasons(reasons, detection.risk_score)
}

// ------------------------------------------------------------------ confidence fusion

export interface FusionChannel {
  key: 'detector' | 'behaviour' | 'zone' | 'persistence'
  label: string
  value: number
  weight: number
  explanation: string
}

export interface FusionResult {
  channels: FusionChannel[]
  /** Weighted mean of the channels, 0..1. */
  fused: number
  /** Probability-weighted severity: detector confidence × risk score / 100. */
  threatIndex: number
}

/**
 * Fuses independent evidence channels into one threat confidence. Weights favour the detector and the
 * behaviour score, which the backend already validates; zone policy and dwell persistence corroborate.
 */
export function fuseConfidence(input: {
  confidence: number | null
  riskScore: number
  zoneType: ZoneType | null
  dwellSeconds: number
  loiterThresholdSeconds: number
}): FusionResult {
  const detector = input.confidence ?? 0
  const behaviour = Math.max(0, Math.min(1, input.riskScore / 100))
  const zone = input.zoneType ? Math.min(1, ZONE_META[input.zoneType].bonus / 100) : 0
  const threshold = Math.max(1, input.loiterThresholdSeconds)
  const persistence = Math.max(0, Math.min(1, input.dwellSeconds / threshold))
  const channels: FusionChannel[] = [
    {
      key: 'detector',
      label: 'Detector confidence',
      value: detector,
      weight: 0.35,
      explanation: input.confidence === null ? 'No confidence reported for this object' : 'YOLOv8x class probability',
    },
    { key: 'behaviour', label: 'Behaviour risk', value: behaviour, weight: 0.35, explanation: `Risk engine score ${input.riskScore}/100` },
    {
      key: 'zone',
      label: 'Zone policy',
      value: zone,
      weight: 0.15,
      explanation: input.zoneType ? `${ZONE_META[input.zoneType].label} zone bonus +${ZONE_META[input.zoneType].bonus}` : 'Outside every fence zone',
    },
    {
      key: 'persistence',
      label: 'Dwell persistence',
      value: persistence,
      weight: 0.15,
      explanation: `${input.dwellSeconds}s in zone of ${threshold}s loiter threshold`,
    },
  ]
  const fused = channels.reduce((sum, channel) => sum + channel.value * channel.weight, 0)
  return { channels, fused, threatIndex: detector * behaviour }
}

// ------------------------------------------------------------------ track geometry

export interface TrackPoint {
  t: number
  x: number
  y: number
}

/** Positions from a timeline response (points without coordinates are skipped), oldest first. */
export function timelineToPoints(points: readonly TimelinePoint[]): TrackPoint[] {
  return points
    .filter((point): point is TimelinePoint & { cx: number; cy: number } => point.cx !== null && point.cy !== null)
    .map((point) => ({ t: Date.parse(point.timestamp), x: point.cx, y: point.cy }))
    .filter((point) => Number.isFinite(point.t))
    .sort((a, b) => a.t - b.t)
}

/** Positions stored on an event row ({t, x, y} objects written by the backend). */
export function eventPositions(event: TrackEvent): TrackPoint[] {
  const points: TrackPoint[] = []
  for (const position of event.positions) {
    const t = typeof position.t === 'string' ? Date.parse(position.t) : Number.NaN
    const x = typeof position.x === 'number' ? position.x : Number.NaN
    const y = typeof position.y === 'number' ? position.y : Number.NaN
    if (Number.isFinite(t) && Number.isFinite(x) && Number.isFinite(y)) points.push({ t, x, y })
  }
  return points.sort((a, b) => a.t - b.t)
}

const COMPASS: Exclude<Direction, 'stationary'>[] = ['east', 'northeast', 'north', 'northwest', 'west', 'southwest', 'south', 'southeast']

/** Histogram of movement headings (image coordinates: y grows downward, so "north" is up). */
export function directionHistogram(points: readonly TrackPoint[], minStepPx = 3): Record<Direction, number> {
  const histogram: Record<Direction, number> = {
    stationary: 0,
    north: 0,
    south: 0,
    east: 0,
    west: 0,
    northeast: 0,
    northwest: 0,
    southeast: 0,
    southwest: 0,
  }
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]
    const b = points[i]
    if (!a || !b) continue
    const dx = b.x - a.x
    const dy = a.y - b.y
    if (Math.hypot(dx, dy) < minStepPx) {
      histogram.stationary += 1
      continue
    }
    const angle = (Math.atan2(dy, dx) * 180) / Math.PI
    const index = Math.round(((angle + 360) % 360) / 45) % 8
    const heading = COMPASS[index]
    if (heading) histogram[heading] += 1
  }
  return histogram
}

export interface PathMetrics {
  distancePx: number
  durationSeconds: number
  averageSpeedPxPerSecond: number
  maxSpeedPxPerSecond: number
  netDisplacementPx: number
  /** Net displacement / path length: 1 = straight line, near 0 = circling or hovering. */
  straightness: number
}

export function pathMetrics(points: readonly TrackPoint[]): PathMetrics {
  let distance = 0
  let maxSpeed = 0
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]
    const b = points[i]
    if (!a || !b) continue
    const step = Math.hypot(b.x - a.x, b.y - a.y)
    distance += step
    const dt = (b.t - a.t) / 1000
    if (dt > 0) maxSpeed = Math.max(maxSpeed, step / dt)
  }
  const first = points[0]
  const last = points[points.length - 1]
  const duration = first && last ? (last.t - first.t) / 1000 : 0
  const net = first && last ? Math.hypot(last.x - first.x, last.y - first.y) : 0
  return {
    distancePx: distance,
    durationSeconds: duration,
    averageSpeedPxPerSecond: duration > 0 ? distance / duration : 0,
    maxSpeedPxPerSecond: maxSpeed,
    netDisplacementPx: net,
    straightness: distance > 0 ? net / distance : 0,
  }
}

// ------------------------------------------------------------------ prediction

export interface PredictedPoint {
  secondsAhead: number
  x: number
  y: number
  /** 1-sigma positional uncertainty in pixels. */
  uncertainty: number
}

export interface Prediction {
  velocity: { vx: number; vy: number }
  speed: number
  points: PredictedPoint[]
  residualStd: number
  samples: number
}

function linearFit(ts: readonly number[], values: readonly number[]): { slope: number; intercept: number; residualStd: number } {
  const n = ts.length
  const meanT = ts.reduce((sum, t) => sum + t, 0) / n
  const meanV = values.reduce((sum, v) => sum + v, 0) / n
  let numerator = 0
  let denominator = 0
  for (let i = 0; i < n; i += 1) {
    const t = ts[i] ?? 0
    const v = values[i] ?? 0
    numerator += (t - meanT) * (v - meanV)
    denominator += (t - meanT) ** 2
  }
  const slope = denominator > 0 ? numerator / denominator : 0
  const intercept = meanV - slope * meanT
  let squared = 0
  for (let i = 0; i < n; i += 1) {
    const predicted = intercept + slope * (ts[i] ?? 0)
    squared += ((values[i] ?? 0) - predicted) ** 2
  }
  return { slope, intercept, residualStd: n > 2 ? Math.sqrt(squared / (n - 2)) : 0 }
}

/**
 * Constant-velocity forecast from a least-squares fit over the most recent `window` seconds of positions.
 * Uncertainty grows with the fit residual and the forecast horizon. Returns null with fewer than 3 points.
 */
export function predictTrajectory(
  points: readonly TrackPoint[],
  horizons: readonly number[] = [1, 2, 3, 5, 8, 10],
  windowSeconds = 6,
): Prediction | null {
  const last = points[points.length - 1]
  if (!last) return null
  const recent = points.filter((point) => last.t - point.t <= windowSeconds * 1000)
  if (recent.length < 3) return null
  const ts = recent.map((point) => (point.t - last.t) / 1000)
  const fx = linearFit(ts, recent.map((point) => point.x))
  const fy = linearFit(ts, recent.map((point) => point.y))
  const residualStd = Math.hypot(fx.residualStd, fy.residualStd)
  const originX = fx.intercept
  const originY = fy.intercept
  const predicted = horizons.map((seconds) => ({
    secondsAhead: seconds,
    x: originX + fx.slope * seconds,
    y: originY + fy.slope * seconds,
    uncertainty: residualStd * (1 + seconds / Math.max(1, windowSeconds)),
  }))
  return {
    velocity: { vx: fx.slope, vy: fy.slope },
    speed: Math.hypot(fx.slope, fy.slope),
    points: predicted,
    residualStd,
    samples: recent.length,
  }
}

export interface ZoneEntryForecast {
  zone: Zone
  secondsAhead: number
  alreadyInside: boolean
}

/** Earliest zone (by forecast time) the predicted path enters, sampling every 0.25 s up to the horizon. */
export function forecastZoneEntries(prediction: Prediction, current: TrackPoint, zones: readonly Zone[], horizonSeconds = 10): ZoneEntryForecast[] {
  const results: ZoneEntryForecast[] = []
  for (const zone of zones) {
    if (!zone.is_active || zone.polygon.length < 3) continue
    if (pointInPolygon(current.x, current.y, zone.polygon)) {
      results.push({ zone, secondsAhead: 0, alreadyInside: true })
      continue
    }
    const first = prediction.points[0]
    const originX = first ? first.x - prediction.velocity.vx * first.secondsAhead : current.x
    const originY = first ? first.y - prediction.velocity.vy * first.secondsAhead : current.y
    for (let s = 0.25; s <= horizonSeconds; s += 0.25) {
      if (pointInPolygon(originX + prediction.velocity.vx * s, originY + prediction.velocity.vy * s, zone.polygon)) {
        results.push({ zone, secondsAhead: s, alreadyInside: false })
        break
      }
    }
  }
  return results.sort((a, b) => a.secondsAhead - b.secondsAhead)
}

// ------------------------------------------------------------------ correlation

export interface CorrelationCandidate {
  event: TrackEvent
  /** 0..1 */
  score: number
  gapSeconds: number
  basis: 'person_uuid' | 'same_track' | 'temporal_handover'
  reasons: string[]
}

/**
 * Finds tracks on other cameras that plausibly belong to the same subject. A shared person_uuid (ReID) is
 * definitive; otherwise candidates are ranked by time gap between one track ending and another starting
 * (within `maxGapSeconds`) and matching object class.
 */
export function correlateTracks(reference: TrackEvent, events: readonly TrackEvent[], maxGapSeconds = 300): CorrelationCandidate[] {
  const refStart = Date.parse(reference.first_seen)
  const refEnd = Date.parse(reference.last_seen)
  const results: CorrelationCandidate[] = []
  for (const event of events) {
    if (event.event_id === reference.event_id) continue
    const start = Date.parse(event.first_seen)
    const end = Date.parse(event.last_seen)
    const gap = start >= refEnd ? (start - refEnd) / 1000 : end <= refStart ? (refStart - end) / 1000 : 0

    if (reference.person_uuid && event.person_uuid === reference.person_uuid) {
      results.push({ event, score: 1, gapSeconds: gap, basis: 'person_uuid', reasons: ['Same re-identification UUID'] })
      continue
    }
    if (event.camera_id === reference.camera_id && event.track_id === reference.track_id) {
      results.push({ event, score: 0.9, gapSeconds: gap, basis: 'same_track', reasons: ['Same tracker ID on the same camera'] })
      continue
    }
    if (event.camera_id === reference.camera_id || gap > maxGapSeconds) continue
    const reasons: string[] = []
    let score = 1 - gap / maxGapSeconds
    reasons.push(gap === 0 ? 'Overlapping time window' : `${Math.round(gap)}s between sightings`)
    if (event.object_class === reference.object_class) reasons.push(`Same class (${event.object_class})`)
    else score *= 0.4
    if (event.max_risk_score > 40 && reference.max_risk_score > 40) {
      score = Math.min(1, score + 0.1)
      reasons.push('Both tracks elevated risk')
    }
    results.push({ event, score: Math.max(0, score) * 0.8, gapSeconds: gap, basis: 'temporal_handover', reasons })
  }
  return results.sort((a, b) => b.score - a.score)
}

// ------------------------------------------------------------------ plotting

const STANDARD_FRAMES: [number, number][] = [
  [640, 480],
  [1280, 720],
  [1920, 1080],
  [2560, 1440],
  [3840, 2160],
]

/** Smallest standard frame size containing every coordinate (positions are in source-frame pixels). */
export function inferFrameSize(points: readonly { x: number; y: number }[], zones: readonly Zone[]): [number, number] {
  let maxX = 0
  let maxY = 0
  for (const point of points) {
    maxX = Math.max(maxX, point.x)
    maxY = Math.max(maxY, point.y)
  }
  for (const zone of zones) {
    for (const [x, y] of zone.polygon) {
      maxX = Math.max(maxX, x)
      maxY = Math.max(maxY, y)
    }
  }
  return STANDARD_FRAMES.find(([w, h]) => maxX <= w && maxY <= h) ?? [Math.ceil(maxX), Math.ceil(maxY)]
}

// ------------------------------------------------------------------ heading summary

const HEADING_LABELS: Record<Exclude<Direction, 'stationary'>, string> = {
  north: 'North',
  northeast: 'North-east',
  east: 'East',
  southeast: 'South-east',
  south: 'South',
  southwest: 'South-west',
  west: 'West',
  northwest: 'North-west',
}

/**
 * Human summary of a track's movement: "Toward fence" when the risk engine recorded it, otherwise the most
 * frequent heading in the camera image (north = up), or "Stationary" when most samples did not move.
 */
export function describeDirection(points: readonly TrackPoint[], riskReasons: readonly string[] = []): string {
  if (riskReasons.some((reason) => reason.startsWith('toward_fence'))) return 'Toward fence'
  if (points.length < 2) return 'Unknown'
  const histogram = directionHistogram(points)
  const moving = (Object.keys(HEADING_LABELS) as (keyof typeof HEADING_LABELS)[]).map((heading) => [heading, histogram[heading]] as const)
  const total = moving.reduce((sum, [, count]) => sum + count, 0)
  if (total === 0 || histogram.stationary > total) return 'Stationary'
  const [best] = [...moving].sort((a, b) => b[1] - a[1])
  return best ? `Moving ${HEADING_LABELS[best[0]].toLowerCase()}` : 'Unknown'
}
