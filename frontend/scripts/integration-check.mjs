// End-to-end smoke test of the frontend's integration contract, run against the live stack through the
// Vite dev proxy exactly as the browser uses it:
//   node scripts/integration-check.mjs            (reads ADMIN_USERNAME / ADMIN_PASSWORD from ../.env)
// Checks: proxied REST endpoints and response shapes, JWT refresh, CORS-free same-origin access, live
// WebSocket frames through /ws, authenticated evidence download, and that no secret leaks in responses.
import { readFileSync } from 'node:fs'

const ORIGIN = process.env.FRONTEND_ORIGIN ?? 'http://localhost:5173'
const env = Object.fromEntries(
  readFileSync(new URL('../../.env', import.meta.url), 'utf8')
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith('#') && line.includes('='))
    .map((line) => [line.slice(0, line.indexOf('=')).trim(), line.slice(line.indexOf('=') + 1).trim()]),
)
const username = process.env.CHECK_USERNAME ?? env.ADMIN_USERNAME
const password = process.env.CHECK_PASSWORD ?? env.ADMIN_PASSWORD
if (!username || !password) throw new Error('ADMIN_USERNAME / ADMIN_PASSWORD missing from .env')

const results = []
const record = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`)
}

async function api(path, { token, method = 'GET', body, headers = {} } = {}) {
  const response = await fetch(`${ORIGIN}/api${path}`, {
    method,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await response.text()
  let json = null
  try {
    json = text ? JSON.parse(text) : null
  } catch {
    /* binary or non-JSON */
  }
  return { status: response.status, json, text, headers: response.headers }
}

const form = new URLSearchParams({ username, password })
const loginResponse = await fetch(`${ORIGIN}/api/auth/login`, { method: 'POST', body: form, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
const login = await loginResponse.json()
record('POST /auth/login via proxy (OAuth2 form)', loginResponse.status === 200 && Boolean(login.access_token) && login.user?.username === username, `status ${loginResponse.status}, role ${login.user?.role}, expires_in ${login.expires_in}`)
record('login response has no password/hash fields', !/password/i.test(JSON.stringify(login.user ?? {})))
const token = login.access_token

const refreshed = await api('/auth/refresh', { method: 'POST', body: { refresh_token: login.refresh_token } })
record('POST /auth/refresh', refreshed.status === 200 && Boolean(refreshed.json?.access_token))

const unauthorised = await api('/cameras')
record('protected endpoint rejects missing token', unauthorised.status === 401, `status ${unauthorised.status}`)

const checks = [
  ['/auth/me', (j) => j.username === username && !('password_hash' in j)],
  ['/stats', (j) => typeof j.cameras_total === 'number' && typeof j.cpu_percent === 'number' && 'websocket_clients' in j],
  ['/health', (j) => ['healthy', 'degraded', 'down'].includes(j.status)],
  ['/cameras', (j) => Array.isArray(j.items) && j.items.every((c) => 'active_alert_count' in c)],
  ['/alerts?limit=5', (j) => Array.isArray(j.items) && typeof j.unacknowledged_count === 'number'],
  ['/alerts/stats', (j) => Array.isArray(j.hourly_trend) && j.hourly_trend.length === 24 && typeof j.by_type === 'object'],
  ['/events?limit=5', (j) => Array.isArray(j.items)],
  ['/events/active', (j) => Array.isArray(j.items)],
  ['/evidence?limit=5', (j) => Array.isArray(j.items) && j.items.every((e) => typeof e.file_url === 'string' && /^[0-9a-f]{64}$/.test(e.file_hash))],
  ['/zones?limit=5', (j) => Array.isArray(j.items)],
  ['/hardware/status', (j) => typeof j === 'object' && j !== null],
  ['/stats/audit-log?limit=5', (j) => Array.isArray(j.items)],
  ['/stats/system-health-history?hours=24', (j) => Array.isArray(j.items)],
]
for (const [path, validate] of checks) {
  const response = await api(path, { token })
  let ok = response.status === 200
  try {
    ok = ok && validate(response.json)
  } catch {
    ok = false
  }
  record(`GET ${path}`, ok, `status ${response.status}`)
}

const cameras = (await api('/cameras', { token })).json?.items ?? []
const camera = cameras[0]
if (camera) {
  const detail = await api(`/cameras/${camera.camera_id}`, { token })
  record(`GET /cameras/${camera.camera_id}`, detail.status === 200 && Array.isArray(detail.json?.recent_alerts))
  const zones = await api(`/zones/${camera.camera_id}`, { token })
  record(`GET /zones/${camera.camera_id}`, zones.status === 200 && Array.isArray(zones.json?.items))
  record('stream URLs are credential-masked', cameras.every((c) => !c.rtsp_url || !/\/\/[^*@/]+:[^*@/]+@/.test(c.rtsp_url)))
}

const alerts = (await api('/alerts?limit=20', { token })).json?.items ?? []
const withTrack = alerts.find((a) => a.track_id !== null)
if (withTrack) {
  const history = await api(`/events/${withTrack.track_id}?camera_id=${withTrack.camera_id}`, { token })
  record(`GET /events/${withTrack.track_id} (Event DNA source)`, history.status === 200 && Array.isArray(history.json?.events), `status ${history.status}`)
  const timeline = await api(`/events/${withTrack.track_id}/timeline?camera_id=${withTrack.camera_id}`, { token })
  record(`GET /events/${withTrack.track_id}/timeline (Prediction source)`, timeline.status === 200 && Array.isArray(timeline.json?.points), `${timeline.json?.points?.length ?? 0} points`)
}

const evidence = (await api('/evidence?limit=10', { token })).json?.items ?? []
const file = evidence.find((e) => e.file_exists && e.file_url)
if (file) {
  const download = await fetch(`${ORIGIN}/api${file.file_url}`, { headers: { Authorization: `Bearer ${token}` } })
  const bytes = new Uint8Array(await download.arrayBuffer())
  record('evidence file download with Bearer header', download.status === 200 && bytes.length === file.file_size_bytes, `${bytes.length} bytes, ${download.headers.get('content-type')}`)
  const anonymous = await fetch(`${ORIGIN}/api${file.file_url}`)
  record('evidence file refused without token', anonymous.status === 401 || anonymous.status === 403, `status ${anonymous.status}`)
  const verify = await api(`/evidence/${file.evidence_id}/verify`, { token })
  record('GET /evidence/{id}/verify', verify.status === 200 && verify.json?.integrity === 'valid', verify.json?.integrity)
}

const video = (await api('/evidence?evidence_type=uploaded_video&limit=1', { token })).json?.items?.[0]
if (video) {
  const playback = await fetch(`${ORIGIN}/api/evidence/${video.evidence_id}/playback`, { headers: { Authorization: `Bearer ${token}` } })
  const body = new Uint8Array(await playback.arrayBuffer())
  const brand = new TextDecoder().decode(body.slice(4, 8))
  record('video playback is browser-playable MP4', playback.status === 200 && brand === 'ftyp' && ['original', 'h264-preview'].includes(playback.headers.get('x-evidence-playback') ?? ''), `${playback.headers.get('x-evidence-playback')}, ${(body.length / 1024).toFixed(0)} KB`)
  const stillValid = await api(`/evidence/${video.evidence_id}/verify`, { token })
  record('playback leaves the evidence hash valid', stillValid.json?.integrity === 'valid')
  const noJob = await api(`/evidence/${video.evidence_id}/analysis-status`, { token })
  record('analysis status endpoint reachable', noJob.status === 200 || noJob.status === 404, `status ${noJob.status}`)
}
// Map-drawn zones: the camera calibration endpoint answers, and every map zone carries a pixel polygon.
if (camera) {
  const calibration = await api(`/cameras/${camera.camera_id}/calibration`, { token })
  record(
    'GET /cameras/{id}/calibration (map zone projection)',
    calibration.status === 200 || calibration.status === 404,
    calibration.status === 200 ? `calibrated, fit ±${calibration.json.error_px} px` : 'not calibrated yet',
  )
}
const mapZones = ((await api('/zones?limit=100', { token })).json?.items ?? []).filter((zone) => zone.geo_polygon)
record(
  'map-drawn zones carry both polygons',
  mapZones.every((zone) => Array.isArray(zone.polygon) && zone.polygon.length >= 3),
  `${mapZones.length} zone(s) drawn on the map`,
)

// Console host position (Windows location service) — the fallback when the browser has no geolocation.
const hostLocation = await api('/cameras/host-location', { token })
record(
  'GET /cameras/host-location (Windows location fallback)',
  (hostLocation.status === 200 && Math.abs(hostLocation.json?.lat) <= 90 && Math.abs(hostLocation.json?.lng) <= 180) ||
    (hostLocation.status === 503 && typeof hostLocation.json?.detail === 'string'),
  hostLocation.status === 200 ? `±${hostLocation.json.accuracy_m ?? '?'} m` : hostLocation.json?.detail,
)

// Every camera is either placed on the map or a local device the console auto-locates on first load.
const unplaced = cameras.filter((item) => (item.gps_lat === null || item.gps_lng === null) && !/^\d+$/.test(`${item.device_id ?? ''}`.trim()))
record(
  'cameras are placed or auto-locatable',
  unplaced.length === 0,
  cameras.map((item) => `${item.camera_id}: ${item.gps_lat !== null ? `${item.gps_lat}, ${item.gps_lng}` : 'auto-locate (local device)'}`).join('; '),
)

const hardware = (await api('/hardware/status', { token })).json ?? {}
const serialisedHardware = JSON.stringify(hardware)
record('hardware configs carry no plain secrets', !/"(password|secret|token|api_key)"\s*:\s*"(?!\*)/.test(serialisedHardware))

// WebSocket through the Vite /ws proxy, as the browser connects.
if (camera) {
  const wsUrl = `${ORIGIN.replace(/^http/, 'ws')}/ws/${camera.camera_id}?token=${encodeURIComponent(token)}`
  const summary = await new Promise((resolve) => {
    const socket = new WebSocket(wsUrl)
    const stats = { connected: false, frames: 0, withDetections: 0, bytes: 0, firstAt: 0, lastAt: 0, pong: false, error: null }
    const finish = () => {
      try {
        socket.close()
      } catch {
        /* ignore */
      }
      resolve(stats)
    }
    const timer = setTimeout(finish, 8000)
    socket.onopen = () => socket.send(JSON.stringify({ type: 'ping' }))
    socket.onmessage = (event) => {
      const message = JSON.parse(String(event.data))
      if (message.type === 'connected') stats.connected = true
      if (message.type === 'pong') stats.pong = true
      if (message.type === 'frame_update' && message.frame) {
        stats.frames += 1
        stats.bytes += message.frame.length
        if (message.detections.length) stats.withDetections += 1
        stats.firstAt ||= Date.now()
        stats.lastAt = Date.now()
      }
    }
    socket.onerror = () => {
      stats.error = 'socket error'
      clearTimeout(timer)
      finish()
    }
  })
  const seconds = Math.max(0.001, (summary.lastAt - summary.firstAt) / 1000)
  const fps = summary.frames > 1 ? (summary.frames - 1) / seconds : 0
  record('WebSocket /ws via proxy: connected handshake', summary.connected)
  record('WebSocket ping → pong', summary.pong)
  record('WebSocket live frame_update stream', summary.frames > 5, `${summary.frames} frames, ${fps.toFixed(1)} fps, avg ${(summary.bytes / Math.max(1, summary.frames) / 1024).toFixed(0)} KB/frame, ${summary.withDetections} with detections`)
  const refused = await new Promise((resolve) => {
    const socket = new WebSocket(`${ORIGIN.replace(/^http/, 'ws')}/ws/${camera.camera_id}?token=invalid`)
    socket.onopen = () => resolve(false)
    socket.onclose = () => resolve(true)
    socket.onerror = () => resolve(true)
  })
  record('WebSocket refuses an invalid token', refused)
}

const logout = await api('/auth/logout', { token, method: 'POST', body: { refresh_token: login.refresh_token } })
record('POST /auth/logout', logout.status === 200)

const failed = results.filter((result) => !result.ok)
console.log(`\n${results.length - failed.length}/${results.length} integration checks passed`)
process.exit(failed.length ? 1 : 0)
