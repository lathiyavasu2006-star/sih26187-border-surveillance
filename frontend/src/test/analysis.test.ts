import { describe, expect, it } from 'vitest'
import {
  correlateTracks,
  directionHistogram,
  explainDetectionRisk,
  forecastZoneEntries,
  fuseConfidence,
  inferFrameSize,
  isNightHour,
  istHour,
  parseRiskReasons,
  pathMetrics,
  predictTrajectory,
  timelineToPoints,
  type TrackPoint,
} from '@/lib/analysis'
import { makeDetection, makeEvent, makeZone } from '@/test/fixtures'

describe('parseRiskReasons (ml/risk_engine.py format)', () => {
  it('decomposes a real alert score exactly', () => {
    const breakdown = parseRiskReasons(['zone_restricted+50', 'loitering+20(threshold:30s,dwell:41s)'], 80)
    expect(breakdown.base).toBe(10)
    expect(breakdown.components.map((c) => [c.key, c.points])).toEqual([
      ['zone_restricted', 50],
      ['loitering', 20],
    ])
    expect(breakdown.components[1]?.detail).toBe('threshold:30s,dwell:41s')
    expect(breakdown.implied).toBe(80)
    expect(breakdown.residual).toBe(0)
  })

  it('applies the night multiplier and the 100 cap', () => {
    const breakdown = parseRiskReasons(['zone_no_mans_land+100', 'night_multiplier_x1.5'], 100)
    expect(breakdown.multiplier).toBe(1.5)
    expect(breakdown.implied).toBe(165)
    expect(breakdown.residual).toBe(0)
  })

  it('reports an unexplained residual', () => {
    expect(parseRiskReasons(['zone_buffer+10'], 45).residual).toBe(25)
  })
})

describe('night window', () => {
  it('wraps midnight for 22:00–05:00', () => {
    expect(isNightHour(22)).toBe(true)
    expect(isNightHour(2)).toBe(true)
    expect(isNightHour(5)).toBe(false)
    expect(isNightHour(12)).toBe(false)
  })

  it('converts to IST', () => {
    expect(istHour(new Date('2026-09-17T18:45:00Z'))).toBe(0)
    expect(istHour(new Date('2026-09-17T06:00:00Z'))).toBe(11)
  })

  it('explains a live detection including zone bonus and night', () => {
    const detection = makeDetection({ risk_score: 90, loitering: true })
    const night = explainDetectionRisk(detection, new Date('2026-09-17T18:45:00Z'), [makeZone()])
    expect(night.implied).toBe(Math.round((10 + 50 + 20) * 1.5))
    const day = explainDetectionRisk(detection, new Date('2026-09-17T06:00:00Z'))
    expect(day.multiplier).toBeNull()
    expect(day.residual).toBe(10)
  })
})

describe('fuseConfidence', () => {
  it('weights channels and computes the threat index', () => {
    const result = fuseConfidence({ confidence: 0.9, riskScore: 80, zoneType: 'restricted', dwellSeconds: 15, loiterThresholdSeconds: 30 })
    expect(result.channels.reduce((sum, channel) => sum + channel.weight, 0)).toBeCloseTo(1)
    expect(result.fused).toBeCloseTo(0.9 * 0.35 + 0.8 * 0.35 + 0.5 * 0.15 + 0.5 * 0.15)
    expect(result.threatIndex).toBeCloseTo(0.72)
  })

  it('clamps persistence and handles a missing confidence', () => {
    const result = fuseConfidence({ confidence: null, riskScore: 150, zoneType: null, dwellSeconds: 500, loiterThresholdSeconds: 30 })
    expect(result.channels.find((c) => c.key === 'persistence')?.value).toBe(1)
    expect(result.channels.find((c) => c.key === 'behaviour')?.value).toBe(1)
    expect(result.threatIndex).toBe(0)
  })
})

const line = (count: number, dx: number, dy: number, stepMs = 500): TrackPoint[] =>
  Array.from({ length: count }, (_, i) => ({ t: 1_000_000 + i * stepMs, x: 100 + i * dx, y: 200 + i * dy }))

describe('track geometry', () => {
  it('builds sorted points from a timeline and skips missing coordinates', () => {
    const points = timelineToPoints([
      { timestamp: '2026-09-17T11:00:02Z', camera_id: 'C', cx: 5, cy: 6, zone_name: null, zone_type: null, risk_score: 0, risk_level: null, object_class: null },
      { timestamp: '2026-09-17T11:00:01Z', camera_id: 'C', cx: 1, cy: 2, zone_name: null, zone_type: null, risk_score: 0, risk_level: null, object_class: null },
      { timestamp: '2026-09-17T11:00:03Z', camera_id: 'C', cx: null, cy: null, zone_name: null, zone_type: null, risk_score: 0, risk_level: null, object_class: null },
    ])
    expect(points.map((p) => p.x)).toEqual([1, 5])
  })

  it('classifies headings with image y pointing down', () => {
    const histogram = directionHistogram([...line(4, 10, 0), { t: 1_002_000, x: 130, y: 190 }, { t: 1_002_500, x: 130, y: 190 }])
    expect(histogram.east).toBe(3)
    expect(histogram.north).toBe(1)
    expect(histogram.stationary).toBe(1)
  })

  it('measures path length, speed and straightness', () => {
    const metrics = pathMetrics(line(5, 30, 40))
    expect(metrics.distancePx).toBeCloseTo(200)
    expect(metrics.durationSeconds).toBe(2)
    expect(metrics.averageSpeedPxPerSecond).toBeCloseTo(100)
    expect(metrics.straightness).toBeCloseTo(1)
  })
})

describe('predictTrajectory', () => {
  it('extrapolates constant velocity exactly for a straight track', () => {
    const prediction = predictTrajectory(line(9, 10, 5), [2, 5])
    expect(prediction).not.toBeNull()
    expect(prediction?.velocity.vx).toBeCloseTo(20)
    expect(prediction?.velocity.vy).toBeCloseTo(10)
    expect(prediction?.points[0]?.x).toBeCloseTo(180 + 40)
    expect(prediction?.points[1]?.y).toBeCloseTo(240 + 50)
    expect(prediction?.residualStd).toBeCloseTo(0)
  })

  it('needs three recent points', () => {
    expect(predictTrajectory(line(2, 10, 0))).toBeNull()
  })

  it('forecasts entry into a zone and flags a track already inside', () => {
    const points = line(9, 10, 0) // x 100 → 180, y 200, moving +20 px/s
    const prediction = predictTrajectory(points)
    const last = points[points.length - 1]
    if (!prediction || !last) throw new Error('prediction expected')
    const zone = makeZone({ polygon: [[300, 150], [400, 150], [400, 250], [300, 250]] })
    const [entry] = forecastZoneEntries(prediction, last, [zone], 10)
    expect(entry?.alreadyInside).toBe(false)
    expect(entry?.secondsAhead).toBeCloseTo(6, 0)
    const inside = forecastZoneEntries(prediction, last, [makeZone({ polygon: [[150, 150], [250, 150], [250, 250], [150, 250]] })])
    expect(inside[0]?.alreadyInside).toBe(true)
  })
})

describe('correlateTracks', () => {
  const reference = makeEvent({ event_id: 1, camera_id: 'CAM-N-001', last_seen: '2026-09-17T11:01:00Z' })

  it('treats a shared person_uuid as definitive', () => {
    const ref = { ...reference, person_uuid: 'abc' }
    const [match] = correlateTracks(ref, [makeEvent({ event_id: 2, camera_id: 'CAM-N-002', person_uuid: 'abc', first_seen: '2026-09-17T15:00:00Z', last_seen: '2026-09-17T15:01:00Z' })])
    expect(match?.basis).toBe('person_uuid')
    expect(match?.score).toBe(1)
  })

  it('ranks temporal handovers by gap and class, ignoring distant tracks', () => {
    const near = makeEvent({ event_id: 2, camera_id: 'CAM-N-002', first_seen: '2026-09-17T11:01:30Z', last_seen: '2026-09-17T11:02:00Z' })
    const far = makeEvent({ event_id: 3, camera_id: 'CAM-N-003', first_seen: '2026-09-17T11:04:00Z', last_seen: '2026-09-17T11:05:00Z' })
    const tooFar = makeEvent({ event_id: 4, camera_id: 'CAM-N-004', first_seen: '2026-09-17T12:00:00Z', last_seen: '2026-09-17T12:01:00Z' })
    const sameCamera = makeEvent({ event_id: 5, track_id: 9, camera_id: 'CAM-N-001', first_seen: '2026-09-17T11:01:10Z', last_seen: '2026-09-17T11:01:20Z' })
    const results = correlateTracks(reference, [far, tooFar, near, sameCamera])
    expect(results.map((r) => r.event.event_id)).toEqual([2, 3])
    expect(results[0]?.gapSeconds).toBe(30)
    expect(results[0]?.score).toBeGreaterThan(results[1]?.score ?? 1)
  })
})

describe('inferFrameSize', () => {
  it('picks the smallest standard frame containing all coordinates', () => {
    expect(inferFrameSize([{ x: 600, y: 400 }], [])).toEqual([640, 480])
    expect(inferFrameSize([{ x: 700, y: 400 }], [])).toEqual([1280, 720])
    expect(inferFrameSize([], [makeZone({ polygon: [[0, 0], [1900, 0], [1900, 1000]] })])).toEqual([1920, 1080])
  })
})
