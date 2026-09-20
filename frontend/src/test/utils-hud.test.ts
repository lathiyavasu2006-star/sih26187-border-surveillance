import { describe, expect, it } from 'vitest'
import { detectionLabel, detectionRect, fitContain, hitTestDetection } from '@/components/camera/hud'
import { cleanParams, snapshotRequestPath, toApiError } from '@/api/client'
import { mergeAlerts } from '@/lib/alerts'
import { formatBytes, formatDuration, pointInPolygon, riskLevelForScore, shortHash, titleCase } from '@/lib/utils'
import { makeAlert, makeDetection } from '@/test/fixtures'
import { AxiosError, AxiosHeaders } from 'axios'

describe('utils', () => {
  it('maps risk scores to backend levels at every boundary', () => {
    expect([0, 20, 21, 40, 41, 60, 61, 80, 81, 100].map(riskLevelForScore)).toEqual([
      'normal',
      'normal',
      'low',
      'low',
      'suspicious',
      'suspicious',
      'high_risk',
      'high_risk',
      'critical',
      'critical',
    ])
  })

  it('formats durations, bytes, hashes and titles', () => {
    expect(formatDuration(41)).toBe('41s')
    expect(formatDuration(3725)).toBe('1h 2m 5s')
    expect(formatDuration(90061)).toBe('1d 1h 1m')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(shortHash('a'.repeat(64), 4)).toBe('aaaa…aaaa')
    expect(titleCase('no_mans_land')).toBe('No Mans Land')
  })

  it('tests points against polygons', () => {
    const square: [number, number][] = [
      [0, 0],
      [10, 0],
      [10, 10],
      [0, 10],
    ]
    expect(pointInPolygon(5, 5, square)).toBe(true)
    expect(pointInPolygon(15, 5, square)).toBe(false)
  })
})

describe('camera HUD geometry', () => {
  it('fits a frame with letterboxing', () => {
    expect(fitContain(1280, 720, 1000, 1000)).toEqual({ scale: 1000 / 1280, offsetX: 0, offsetY: (1000 - 562.5) / 2, width: 1000, height: 562.5 })
  })

  it('maps detection boxes to canvas space and hit-tests the smallest box', () => {
    const viewport = fitContain(640, 480, 320, 240)
    const big = makeDetection({ track_id: 1, bbox_x1: 0, bbox_y1: 0, bbox_x2: 400, bbox_y2: 400 })
    const small = makeDetection({ track_id: 2, bbox_x1: 100, bbox_y1: 100, bbox_x2: 200, bbox_y2: 200 })
    expect(detectionRect(small, viewport)).toEqual({ x: 50, y: 50, w: 50, h: 50 })
    expect(hitTestDetection([big, small], viewport, 60, 60)?.track_id).toBe(2)
    expect(hitTestDetection([big, small], viewport, 20, 20)?.track_id).toBe(1)
    expect(hitTestDetection([big, small], viewport, 300, 230)).toBeNull()
    expect(detectionRect(makeDetection({ bbox_x1: null }), viewport)).toBeNull()
  })

  it('labels detections with class and confidence', () => {
    expect(detectionLabel(makeDetection({ object_class: 'truck', confidence: 0.914 }))).toBe('TRUCK 91%')
    expect(detectionLabel(makeDetection({ confidence: null }))).toBe('PERSON')
  })
})

describe('api client helpers', () => {
  it('maps snapshot paths under the evidence root to the protected mount', () => {
    expect(snapshotRequestPath('E:/sih26187/evidence/snapshots/ALT-1.jpg')).toBe('/evidence/files/snapshots/ALT-1.jpg')
    expect(snapshotRequestPath('E:\\sih26187\\evidence\\snapshots\\a b.jpg')).toBe('/evidence/files/snapshots/a%20b.jpg')
  })

  it('refuses paths outside the root or with traversal', () => {
    expect(snapshotRequestPath('C:/Windows/win.ini')).toBeNull()
    expect(snapshotRequestPath('E:/sih26187/evidence/../.env')).toBeNull()
    expect(snapshotRequestPath(null)).toBeNull()
  })

  it('extracts FastAPI error details, validation messages and Retry-After', () => {
    const response = (status: number, data: unknown, headers: Record<string, string> = {}) =>
      new AxiosError('failed', 'ERR', undefined, undefined, {
        status,
        statusText: '',
        data,
        headers,
        config: { headers: new AxiosHeaders() },
      })
    expect(toApiError(response(401, { detail: 'Incorrect username or password' })).message).toBe('Incorrect username or password')
    expect(toApiError(response(422, { detail: [{ loc: ['body', 'polygon'], msg: 'polygon requires at least 3 points' }] })).message).toBe(
      'polygon: polygon requires at least 3 points',
    )
    const limited = toApiError(response(429, { error: 'Rate limit exceeded' }, { 'retry-after': '42' }))
    expect(limited.status).toBe(429)
    expect(limited.retryAfterSeconds).toBe(42)
    expect(toApiError(new AxiosError('Network Error')).status).toBe(0)
  })

  it('drops empty query parameters', () => {
    expect(cleanParams({ a: 1, b: undefined, c: '', d: false, e: null })).toEqual({ a: 1, d: false })
  })

  it('merges live and REST alerts with the REST copy authoritative', () => {
    const rest = makeAlert({ alert_id: 'ALT-1', acknowledged: true, timestamp: '2026-09-17T06:00:00Z' })
    const liveCopy = makeAlert({ alert_id: 'ALT-1', acknowledged: false, timestamp: '2026-09-17T06:00:00Z' })
    const liveOnly = makeAlert({ alert_id: 'ALT-2', timestamp: '2026-09-17T07:00:00Z' })
    const merged = mergeAlerts([rest], [liveCopy, liveOnly])
    expect(merged.map((alert) => alert.alert_id)).toEqual(['ALT-2', 'ALT-1'])
    expect(merged[1]?.acknowledged).toBe(true)
  })
})
