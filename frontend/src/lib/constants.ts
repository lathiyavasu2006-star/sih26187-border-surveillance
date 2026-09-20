import type { AlertType, CameraType, HardwareType, RiskLevel, UserRole, ZoneRegion, ZoneType } from '@/types'

export const APP_NAME = 'SIH26187 Border Surveillance'
export const ORG_NAME = 'Ministry of Home Affairs · Sashastra Seema Bal'

export const RISK_LEVELS: RiskLevel[] = ['normal', 'low', 'suspicious', 'high_risk', 'critical']

export const RISK_META: Record<RiskLevel, { label: string; color: string; badge: string }> = {
  normal: { label: 'Normal', color: '#16a34a', badge: 'bg-green-50 text-green-700 ring-green-200' },
  low: { label: 'Low', color: '#0891b2', badge: 'bg-cyan-50 text-cyan-700 ring-cyan-200' },
  suspicious: { label: 'Suspicious', color: '#ca8a04', badge: 'bg-yellow-50 text-yellow-800 ring-yellow-200' },
  high_risk: { label: 'High Risk', color: '#ea580c', badge: 'bg-orange-50 text-orange-700 ring-orange-200' },
  critical: { label: 'Critical', color: '#dc2626', badge: 'bg-red-50 text-red-700 ring-red-200' },
}

export const ALERT_TYPES: AlertType[] = [
  'intrusion',
  'loitering',
  'weapon',
  'vehicle',
  'behavior',
  'camera_offline',
  'zone_breach',
  'animal',
  'smoke',
  'fence_damage',
]

export const ZONE_TYPES: ZoneType[] = ['public', 'buffer', 'sensitive', 'restricted', 'no_mans_land']

/** Mirrors backend settings.ZONE_RISK_BONUS (risk_bonus is derived server-side from the zone type). */
export const ZONE_META: Record<ZoneType, { label: string; color: string; bonus: number }> = {
  public: { label: 'Public', color: '#22c55e', bonus: 0 },
  buffer: { label: 'Buffer', color: '#eab308', bonus: 10 },
  sensitive: { label: 'Sensitive', color: '#f97316', bonus: 30 },
  restricted: { label: 'Restricted', color: '#ef4444', bonus: 50 },
  no_mans_land: { label: 'No Man’s Land', color: '#991b1b', bonus: 100 },
}

export const ZONE_REGIONS: ZoneRegion[] = ['north', 'south', 'east', 'west']
export const CAMERA_TYPES: CameraType[] = ['standard', 'ptz', 'thermal', 'drone', 'radar', 'scanner', 'satellite']
export const HARDWARE_TYPES: HardwareType[] = [
  'standard_camera',
  'ptz_camera',
  'thermal_camera',
  'drone',
  'radar',
  'license_plate_scanner',
  'satellite',
]

export const ROLE_LABEL: Record<UserRole, string> = {
  admin: 'Administrator',
  regional_head: 'Regional Head',
  supervisor: 'Supervisor',
  operator: 'Operator',
}

/** Night window used by the risk engine (backend NIGHT_START_HOUR / NIGHT_END_HOUR, IST). */
export const NIGHT_WINDOW = { start: 22, end: 5, multiplier: 1.5 } as const

/** Roughly India's bounding box, used to frame the map. */
export const INDIA_BOUNDS: [[number, number], [number, number]] = [
  [6.5, 68.0],
  [37.5, 97.5],
]
export const INDIA_CENTER: [number, number] = [22.5, 80.0]

/**
 * Global polling budget. Through the dev proxy every browser request reaches FastAPI from 127.0.0.1,
 * which shares the backend's 100 requests/minute per-client limit with the ML pipeline's HTTP calls.
 * Live data arrives over the WebSocket, so REST polling stays far below that ceiling (~15/min).
 */
export const POLL = {
  stats: 15_000,
  alerts: 30_000,
  alertStats: 60_000,
  cameras: 30_000,
  health: 60_000,
  hardware: 60_000,
  events: 30_000,
} as const
