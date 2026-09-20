import { describe, expect, it } from 'vitest'
import { maskStreamUrl, passwordProblems, validateCameraDraft, validateZoneForm, type CameraDraft, type ZoneForm } from '@/lib/validation'

describe('passwordProblems (backend validate_password_strength)', () => {
  it('accepts a compliant password', () => {
    expect(passwordProblems('Border#Watch2026', 'operator1')).toEqual([])
  })

  it('lists every missing rule', () => {
    expect(passwordProblems('short', 'x')).toEqual(['at least 12 characters', 'an uppercase letter', 'a digit', 'a special character'])
  })

  it('rejects whitespace, the username and more than 72 bytes', () => {
    expect(passwordProblems('Has Space#2026x', 'bob')).toContain('no whitespace')
    expect(passwordProblems('Admin@Example2026', 'admin')).toContain('must not contain the username')
    expect(passwordProblems(`Aa1#${'ह'.repeat(25)}`, 'x')).toContain('at most 72 bytes')
  })
})

const draft = (overrides: Partial<CameraDraft> = {}): CameraDraft => ({
  name: 'Gate 2',
  camera_type: 'standard',
  zone_region: 'north',
  sector_name: '',
  location_name: '',
  gps_lat: '',
  gps_lng: '',
  rtsp_url: 'rtsp://10.0.0.2/stream',
  device_id: '',
  ...overrides,
})

describe('validateCameraDraft', () => {
  it('passes a complete draft', () => {
    expect(validateCameraDraft(draft(), 2)).toEqual({})
  })

  it('validates each step cumulatively', () => {
    expect(validateCameraDraft(draft({ name: ' ' }), 0)).toHaveProperty('name')
    expect(validateCameraDraft(draft({ gps_lat: '26.9' }), 1)).toHaveProperty('gps_lat')
    expect(validateCameraDraft(draft({ gps_lat: '95', gps_lng: '84' }), 1).gps_lat).toMatch(/between -90 and 90/)
    expect(validateCameraDraft(draft({ rtsp_url: 'ftp://x' }), 2)).toHaveProperty('rtsp_url')
    expect(validateCameraDraft(draft({ rtsp_url: '', device_id: '' }), 2)).toHaveProperty('rtsp_url')
    expect(validateCameraDraft(draft({ rtsp_url: '0' }), 2)).toEqual({})
    // Later-step errors are not reported on earlier steps.
    expect(validateCameraDraft(draft({ rtsp_url: 'ftp://x' }), 0)).toEqual({})
  })

  it('masks stream credentials for display', () => {
    expect(maskStreamUrl('rtsp://admin:s3cret@10.0.0.21:554/s1')).toBe('rtsp://****@10.0.0.21:554/s1')
    expect(maskStreamUrl('rtsp://10.0.0.21/s1')).toBe('rtsp://10.0.0.21/s1')
  })
})

const zoneForm = (overrides: Partial<ZoneForm> = {}): ZoneForm => ({
  zone_name: 'Fence line',
  zone_type: 'restricted',
  loiter_threshold_seconds: '30',
  multiplier: '1.5',
  night_start: '22',
  night_end: '5',
  color_hex: '#ef4444',
  allowed_persons: '',
  is_active: true,
  ...overrides,
})
const square: [number, number][] = [
  [0, 0],
  [10, 0],
  [10, 10],
]

describe('validateZoneForm (backend ZoneBase rules)', () => {
  it('accepts a valid zone', () => {
    expect(validateZoneForm(zoneForm(), square)).toBeNull()
  })

  it.each([
    [zoneForm({ zone_name: '' }), square, /name is required/],
    [zoneForm(), square.slice(0, 2), /at least 3 points/],
    [zoneForm(), [[1, 1], [1, 1], [2, 2]] as [number, number][], /3 distinct/],
    [zoneForm(), [[0, 0], [10.5, 0], [10, 10]] as [number, number][], /whole pixels/],
    [zoneForm({ loiter_threshold_seconds: '0' }), square, /Loiter threshold/],
    [zoneForm({ multiplier: '6' }), square, /multiplier/],
    [zoneForm({ night_end: '24' }), square, /0–23/],
    [zoneForm({ color_hex: 'red' }), square, /#RRGGBB/],
  ])('rejects invalid input %#', (form, points, message) => {
    expect(validateZoneForm(form, points)).toMatch(message)
  })
})
