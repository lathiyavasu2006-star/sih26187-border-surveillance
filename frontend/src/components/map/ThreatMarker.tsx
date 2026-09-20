import L from 'leaflet'
import { useMemo } from 'react'
import { Marker, Popup, Tooltip } from 'react-leaflet'
import { Link, useNavigate } from 'react-router-dom'
import { RISK_META } from '@/lib/constants'
import { formatDateTime, titleCase } from '@/lib/utils'
import type { Alert, CameraWithAlertCount, RiskLevel } from '@/types'

const iconCache = new Map<string, L.DivIcon>()

/** Pulsing threat icon. Only fixed markup and palette colours are interpolated (never user data). */
function threatIcon(level: RiskLevel, acknowledged: boolean): L.DivIcon {
  const key = `threat:${level}:${acknowledged}`
  const cached = iconCache.get(key)
  if (cached) return cached
  const color = RISK_META[level].color
  const pulse = acknowledged
    ? ''
    : `<span style="position:absolute;inset:0;border-radius:9999px;background:${color};opacity:.55" class="animate-pulse-ring"></span>`
  const icon = L.divIcon({
    className: '',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<span style="position:relative;display:block;width:22px;height:22px">${pulse}<span style="position:absolute;inset:4px;border-radius:9999px;background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4);opacity:${acknowledged ? 0.55 : 1}"></span></span>`,
  })
  iconCache.set(key, icon)
  return icon
}

/** Camera status → square fill: green online, red offline, amber degraded. */
const CAMERA_MARKER_COLORS: Record<string, string> = { online: '#16a34a', degraded: '#d97706', offline: '#dc2626' }

function cameraIcon(status: string, critical: boolean): L.DivIcon {
  const key = `camera:${status}:${critical}`
  const cached = iconCache.get(key)
  if (cached) return cached
  const color = CAMERA_MARKER_COLORS[status] ?? '#64748b'
  const pulse = critical
    ? '<span style="position:absolute;left:-13px;top:-13px;width:52px;height:52px;border-radius:9999px;border:3px solid #dc2626;background:rgba(220,38,38,.25)" class="animate-pulse-ring"></span>'
    : ''
  const icon = L.divIcon({
    className: '',
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    html: `<span style="position:relative;display:block;width:26px;height:26px">${pulse}<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;border-radius:5px;background:${color};border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.45)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M16.75 12h3.63a1 1 0 0 1 .88 1.47l-2.1 3.9"/><rect x="2" y="4" width="15" height="10" rx="2"/><path d="M9 14v4H3"/></svg></span></span>`,
  })
  iconCache.set(key, icon)
  return icon
}

export function ThreatMarker({ alert }: { alert: Alert }) {
  const icon = useMemo(() => threatIcon(alert.risk_level, alert.acknowledged), [alert.risk_level, alert.acknowledged])
  if (alert.gps_lat === null || alert.gps_lng === null) return null
  return (
    <Marker position={[alert.gps_lat, alert.gps_lng]} icon={icon} zIndexOffset={alert.acknowledged ? 0 : 1000}>
      <Tooltip direction="top" offset={[0, -10]} className="threat-tooltip">
        {titleCase(alert.alert_type)} · {RISK_META[alert.risk_level].label} {alert.risk_score}
      </Tooltip>
      <Popup>
        <div className="min-w-52 text-xs">
          <p className="font-mono text-[10px] text-slate-500">{alert.alert_id}</p>
          <p className="text-sm font-semibold">{titleCase(alert.alert_type)}</p>
          <p className="mt-0.5" style={{ color: RISK_META[alert.risk_level].color }}>
            {RISK_META[alert.risk_level].label} · risk {alert.risk_score}
          </p>
          <p className="mt-1 text-slate-600">
            {alert.camera_id}
            {alert.zone_name ? ` · ${alert.zone_name}` : ''}
          </p>
          <p className="text-slate-500">{formatDateTime(alert.timestamp)}</p>
          <Link to={`/alerts?alert=${encodeURIComponent(alert.alert_id)}`} className="mt-2 inline-block font-semibold text-cyan-700">
            Open alert →
          </Link>
        </div>
      </Popup>
    </Marker>
  )
}

/** Camera position. Click opens its live feed; a red pulsing ring marks an unacknowledged critical alert. */
export function CameraMarker({ camera, critical }: { camera: CameraWithAlertCount; critical: boolean }) {
  const navigate = useNavigate()
  const icon = useMemo(() => cameraIcon(camera.status, critical), [camera.status, critical])
  if (camera.gps_lat === null || camera.gps_lng === null) return null
  return (
    <Marker
      position={[camera.gps_lat, camera.gps_lng]}
      icon={icon}
      zIndexOffset={critical ? 2000 : 1500}
      title={camera.camera_id}
      eventHandlers={{ click: () => navigate(`/cameras/${camera.camera_id}`) }}
    >
      <Tooltip direction="top" offset={[0, -16]} className="threat-tooltip">
        <span className="block font-mono font-bold">{camera.camera_id}</span>
        <span className="block">{camera.location_name ?? camera.name}</span>
        <span className="block">
          <span style={{ color: CAMERA_MARKER_COLORS[camera.status] ?? '#94a3b8' }}>● {camera.status.toUpperCase()}</span>
          {' · '}
          {camera.active_alert_count} active alert{camera.active_alert_count === 1 ? '' : 's'}
          {critical ? ' · CRITICAL' : ''}
        </span>
        <span className="block text-[10px] text-slate-400">Click to open live feed</span>
      </Tooltip>
    </Marker>
  )
}
