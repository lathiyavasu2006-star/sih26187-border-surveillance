/** Form validation shared by pages and unit tests. Rules mirror the backend Pydantic validators. */
import type { Camera, CameraEditRequest, CameraType, ZoneRegion, ZoneType } from '@/types'

// ------------------------------------------------------------------ password

/** Mirrors backend validate_password_strength plus the change-password rule that forbids the username. */
export function passwordProblems(password: string, username: string): string[] {
  const problems: string[] = []
  if (password.length < 12) problems.push('at least 12 characters')
  if (new TextEncoder().encode(password).length > 72) problems.push('at most 72 bytes')
  if (!/[A-Z]/.test(password)) problems.push('an uppercase letter')
  if (!/[a-z]/.test(password)) problems.push('a lowercase letter')
  if (!/\d/.test(password)) problems.push('a digit')
  if (!/[^A-Za-z0-9]/.test(password)) problems.push('a special character')
  if (/\s/.test(password)) problems.push('no whitespace')
  if (username && password.toLowerCase().includes(username.toLowerCase())) problems.push('must not contain the username')
  return problems
}

// ------------------------------------------------------------------ camera registration

export const STREAM_PREFIXES = ['rtsp://', 'rtsps://', 'http://', 'https://', 'rtmp://', 'udp://', 'file://']

export interface CameraDraft {
  name: string
  camera_type: CameraType
  zone_region: ZoneRegion
  sector_name: string
  location_name: string
  gps_lat: string
  gps_lng: string
  rtsp_url: string
  device_id: string
}

/** Validates the registration wizard up to and including `step` (0 identity, 1 location, 2 stream). */
export function validateCameraDraft(draft: CameraDraft, step: number): Record<string, string> {
  const errors: Record<string, string> = {}
  if (step >= 0) {
    if (!draft.name.trim()) errors.name = 'Name is required'
    else if (draft.name.trim().length > 100) errors.name = 'At most 100 characters'
  }
  if (step >= 1) {
    const lat = draft.gps_lat.trim()
    const lng = draft.gps_lng.trim()
    if ((lat === '') !== (lng === '')) errors.gps_lat = 'Provide both latitude and longitude, or neither'
    if (lat !== '' && (!Number.isFinite(Number(lat)) || Math.abs(Number(lat)) > 90)) errors.gps_lat = 'Latitude must be between -90 and 90'
    if (lng !== '' && (!Number.isFinite(Number(lng)) || Math.abs(Number(lng)) > 180)) errors.gps_lng = 'Longitude must be between -180 and 180'
  }
  if (step >= 2) {
    const url = draft.rtsp_url.trim()
    if (!url && !draft.device_id.trim()) errors.rtsp_url = 'Provide a stream URL or a local device index'
    if (url && !/^\d+$/.test(url) && !STREAM_PREFIXES.some((prefix) => url.toLowerCase().startsWith(prefix))) {
      errors.rtsp_url = `Must start with ${STREAM_PREFIXES.join(', ')} or be a device index`
    }
  }
  return errors
}

/** Hides credentials in a stream URL before it is displayed (rtsp://user:pass@host → rtsp://****@host). */
export function maskStreamUrl(url: string): string {
  return url.replace(/\/\/[^@/]+@/, '//****@')
}

// ------------------------------------------------------------------ zones

export interface ZoneForm {
  zone_name: string
  zone_type: ZoneType
  loiter_threshold_seconds: string
  multiplier: string
  night_start: string
  night_end: string
  color_hex: string
  allowed_persons: string
  is_active: boolean
}

export function validateZoneForm(form: ZoneForm, points: readonly [number, number][]): string | null {
  if (!form.zone_name.trim()) return 'Zone name is required'
  if (form.zone_name.trim().length > 100) return 'Zone name must be at most 100 characters'
  if (points.length < 3) return 'Draw at least 3 points on the feed'
  if (new Set(points.map(([x, y]) => `${x},${y}`)).size < 3) return 'The polygon needs at least 3 distinct points'
  if (points.some(([x, y]) => !Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0)) return 'Polygon points must be non-negative whole pixels'
  const loiter = Number(form.loiter_threshold_seconds)
  if (!Number.isInteger(loiter) || loiter < 1 || loiter > 86400) return 'Loiter threshold must be 1–86400 seconds'
  const multiplier = Number(form.multiplier)
  if (!Number.isFinite(multiplier) || multiplier < 1 || multiplier > 5) return 'Night multiplier must be between 1.0 and 5.0'
  const start = Number(form.night_start)
  const end = Number(form.night_end)
  if (![start, end].every((hour) => Number.isInteger(hour) && hour >= 0 && hour <= 23)) return 'Night hours must be 0–23'
  if (!/^#[0-9a-fA-F]{6}$/.test(form.color_hex)) return 'Colour must be a #RRGGBB value'
  return null
}

// ------------------------------------------------------------------ camera edit

export interface CameraEditForm {
  name: string
  location_name: string
  gps_lat: string
  gps_lng: string
  sector_name: string
  camera_type: CameraType
  zone_region: ZoneRegion
}

/** Mirrors backend CameraEditRequest: name required, GPS in range and provided as a pair. */
export function validateCameraEdit(form: CameraEditForm): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.name.trim()) errors.name = 'Name is required'
  else if (form.name.trim().length > 100) errors.name = 'At most 100 characters'
  const lat = form.gps_lat.trim()
  const lng = form.gps_lng.trim()
  if ((lat === '') !== (lng === '')) errors.gps_lat = 'Provide both latitude and longitude, or neither'
  if (lat !== '' && (!Number.isFinite(Number(lat)) || Math.abs(Number(lat)) > 90)) errors.gps_lat = 'Latitude must be between -90 and 90'
  if (lng !== '' && (!Number.isFinite(Number(lng)) || Math.abs(Number(lng)) > 180)) errors.gps_lng = 'Longitude must be between -180 and 180'
  return errors
}

/** Only changed fields are sent, so a concurrent edit of another field is never overwritten. */
export function cameraEditPayload(camera: Camera, form: CameraEditForm): CameraEditRequest {
  const payload: CameraEditRequest = {}
  const text = (value: string) => (value.trim() ? value.trim() : null)
  if (form.name.trim() !== camera.name) payload.name = form.name.trim()
  if (text(form.location_name) !== camera.location_name) payload.location_name = text(form.location_name)
  if (text(form.sector_name) !== camera.sector_name) payload.sector_name = text(form.sector_name)
  if (form.camera_type !== camera.camera_type) payload.camera_type = form.camera_type
  if (form.zone_region !== camera.zone_region) payload.zone_region = form.zone_region
  const lat = form.gps_lat.trim() ? Number(form.gps_lat) : null
  const lng = form.gps_lng.trim() ? Number(form.gps_lng) : null
  if (lat !== camera.gps_lat || lng !== camera.gps_lng) {
    payload.gps_lat = lat
    payload.gps_lng = lng
  }
  return payload
}

// ------------------------------------------------------------------ video analysis upload

const ANALYSIS_EXTENSIONS = ['mp4', 'avi', 'mov', 'mkv']
export const MAX_ANALYSIS_BYTES = 500 * 1024 * 1024

export function validateAnalysisFile(file: { name: string; size: number } | null): string | null {
  if (!file) return 'Choose a video file'
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ANALYSIS_EXTENSIONS.includes(extension)) return 'Only MP4, AVI, MOV or MKV videos can be analysed'
  if (file.size === 0) return 'The file is empty'
  if (file.size > MAX_ANALYSIS_BYTES) return 'Videos are limited to 500 MB'
  return null
}
