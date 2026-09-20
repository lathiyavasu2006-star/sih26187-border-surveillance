import { refreshAccessToken } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore, type LiveNotification } from '@/stores/liveStore'
import type { Alert, WSServerMessage } from '@/types'
import { isRecord } from '@/lib/utils'

const PING_INTERVAL_MS = 25_000
const MAX_BACKOFF_MS = 30_000
/** Refresh the access token before (re)connecting when it expires within this window. */
const TOKEN_MARGIN_MS = 60_000

export type LiveEvent =
  | { type: 'notification'; notification: LiveNotification }
  | { type: 'alerts'; cameraId: string; alerts: Alert[] }
  | { type: 'zones_changed'; cameraId: string }
  | { type: 'alert_acknowledged'; alertId: string }
  | { type: 'camera_status'; cameraId: string }

type LiveListener = (event: LiveEvent) => void

function wsOrigin(): string {
  const configured = (import.meta.env.VITE_WS_URL || '').trim()
  if (configured) return configured.replace(/\/+$/, '')
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}`
}

const MESSAGE_TYPES = new Set([
  'connected',
  'frame_update',
  'pong',
  'error',
  'acknowledge_ok',
  'camera_offline',
  'camera_online',
  'critical_alert',
  'alert_acknowledged',
  'zone_update',
  'zone_deleted',
  'neighbor_offline_alert',
  'frame_ack',
])

function parseMessage(raw: unknown): WSServerMessage | null {
  if (typeof raw !== 'string') return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(parsed) || typeof parsed.type !== 'string' || !MESSAGE_TYPES.has(parsed.type)) return null
  if (parsed.type === 'frame_update') {
    if (typeof parsed.camera_id !== 'string' || !Array.isArray(parsed.detections) || !Array.isArray(parsed.alerts)) {
      return null
    }
  }
  return parsed as unknown as WSServerMessage
}

/**
 * One reference-counted WebSocket per camera (the backend exposes /ws/{camera_id}). Components call
 * `subscribe(cameraId)` and release the returned function on unmount; the socket closes when the last
 * subscriber leaves. Reconnects with exponential backoff and refreshes an expiring JWT first.
 */
class CameraSocket {
  readonly cameraId: string
  private socket: WebSocket | null = null
  private subscribers = 0
  private attempts = 0
  private reconnectTimer: number | null = null
  private pingTimer: number | null = null
  private closedByClient = false
  private everOpened = false

  constructor(cameraId: string) {
    this.cameraId = cameraId
  }

  retain(): void {
    this.subscribers += 1
    if (this.subscribers === 1) {
      this.closedByClient = false
      void this.connect()
    }
  }

  release(): void {
    this.subscribers = Math.max(0, this.subscribers - 1)
    if (this.subscribers === 0) this.shutdown()
  }

  get idle(): boolean {
    return this.subscribers === 0
  }

  private setState(state: Parameters<ReturnType<typeof useLiveStore.getState>['setConnection']>[1], detail: string | null = null) {
    useLiveStore.getState().setConnection(this.cameraId, state, detail)
  }

  private async connect(): Promise<void> {
    if (this.closedByClient || this.socket) return
    const auth = useAuthStore.getState()
    if (!auth.accessToken || auth.locked) {
      this.setState('closed', auth.locked ? 'Screen locked' : 'Not signed in')
      return
    }
    this.setState(this.attempts === 0 ? 'connecting' : 'reconnecting')

    let token: string | null = auth.accessToken
    if (auth.accessExpiresAt !== null && auth.accessExpiresAt - Date.now() < TOKEN_MARGIN_MS) {
      token = await refreshAccessToken()
    }
    if (!token) {
      // Refresh failed: a cleared session is final, an unreachable backend is retried with backoff.
      if (useAuthStore.getState().refreshToken) {
        this.setState('reconnecting', 'Backend unreachable')
        this.scheduleReconnect()
      } else {
        this.setState('denied', 'Session expired')
      }
      return
    }
    if (this.closedByClient || this.socket) return

    const url = `${wsOrigin()}/ws/${encodeURIComponent(this.cameraId)}?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(url)
    this.socket = socket
    let opened = false

    socket.onopen = () => {
      opened = true
      this.everOpened = true
      this.attempts = 0
      this.setState('open')
      this.startPing()
    }

    socket.onmessage = (event: MessageEvent) => {
      const message = parseMessage(event.data)
      if (message) handleMessage(this.cameraId, message)
    }

    socket.onclose = (event: CloseEvent) => {
      this.stopPing()
      if (this.socket === socket) this.socket = null
      if (this.closedByClient) {
        this.setState('closed')
        return
      }
      if (event.code === 1008) {
        // Policy violation after the handshake: token revoked/expired or access removed.
        this.setState('reconnecting', event.reason || 'Access denied by server')
        void refreshAccessToken().then((fresh) => {
          if (fresh || useAuthStore.getState().refreshToken) this.scheduleReconnect()
          else this.setState('denied', 'Session expired')
        })
        return
      }
      // A refusal before the handshake completes surfaces as 1006 without a reason (HTTP 403).
      const detail = opened ? `Connection lost (${event.code})` : 'Server refused the connection'
      this.setState('reconnecting', detail)
      this.scheduleReconnect()
    }

    socket.onerror = () => {
      /* onclose follows and handles reconnection */
    }
  }

  private scheduleReconnect(): void {
    if (this.closedByClient || this.reconnectTimer !== null) return
    this.attempts += 1
    const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(this.attempts - 1, 5))
    const jitter = Math.round(base * 0.2 * Math.random())
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      void this.connect()
    }, base + jitter)
  }

  private startPing(): void {
    this.stopPing()
    this.pingTimer = window.setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: 'ping' }))
    }, PING_INTERVAL_MS)
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  /** Forces a fresh connection (after token rotation or unlock). */
  restart(): void {
    if (this.idle) return
    const socket = this.socket
    this.socket = null
    this.stopPing()
    if (socket) {
      socket.onclose = null
      socket.close(1000, 'restart')
    }
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.attempts = 0
    this.closedByClient = false
    void this.connect()
  }

  shutdown(): void {
    this.closedByClient = true
    this.stopPing()
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const socket = this.socket
    this.socket = null
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      socket.close(1000, 'client closed')
    }
    this.setState('closed')
    this.everOpened = false
  }

  get hasOpened(): boolean {
    return this.everOpened
  }
}

const sockets = new Map<string, CameraSocket>()
const listeners = new Set<LiveListener>()

function emit(event: LiveEvent): void {
  for (const listener of listeners) {
    try {
      listener(event)
    } catch (error) {
      console.error('live listener failed', error)
    }
  }
}

function notify(notification: Omit<LiveNotification, 'receivedAt' | 'read'>): void {
  if (useLiveStore.getState().pushNotification(notification)) {
    const stored = useLiveStore.getState().notifications.find((item) => item.id === notification.id)
    if (stored) emit({ type: 'notification', notification: stored })
  }
}

function handleMessage(cameraId: string, message: WSServerMessage): void {
  const live = useLiveStore.getState()
  switch (message.type) {
    case 'connected':
      live.setViewerCount(cameraId, message.viewer_count)
      break
    case 'frame_update': {
      const fresh = live.ingestFrame(message)
      if (fresh.length) {
        emit({ type: 'alerts', cameraId: message.camera_id, alerts: fresh })
        for (const alert of fresh) {
          if (alert.risk_level === 'high_risk') {
            notify({
              id: `new_alert:${alert.alert_id}`,
              kind: 'new_alert',
              severity: 'warning',
              title: `High risk ${alert.alert_type.replaceAll('_', ' ')}`,
              message: `${alert.camera_id} · risk ${alert.risk_score}${alert.zone_name ? ` · ${alert.zone_name}` : ''}`,
              cameraId: alert.camera_id,
              alertId: alert.alert_id,
              timestamp: alert.timestamp,
            })
          }
        }
      }
      break
    }
    case 'critical_alert':
      notify({
        id: `critical_alert:${message.alert_id}`,
        kind: 'critical_alert',
        severity: 'critical',
        title: `CRITICAL · ${message.alert_type.replaceAll('_', ' ').toUpperCase()}`,
        message: message.message,
        cameraId: message.camera_id,
        alertId: message.alert_id,
        timestamp: message.timestamp,
      })
      break
    case 'camera_offline':
      notify({
        id: `camera_offline:${message.camera_id}:${message.alert_id ?? message.timestamp}`,
        kind: 'camera_offline',
        severity: 'warning',
        title: `Camera ${message.camera_id} offline`,
        message: message.message,
        cameraId: message.camera_id,
        alertId: message.alert_id,
        timestamp: message.timestamp,
      })
      emit({ type: 'camera_status', cameraId: message.camera_id })
      break
    case 'camera_online':
      notify({
        id: `camera_online:${message.camera_id}:${message.timestamp}`,
        kind: 'camera_online',
        severity: 'info',
        title: `Camera ${message.camera_id} online`,
        message: message.message,
        cameraId: message.camera_id,
        alertId: null,
        timestamp: message.timestamp,
      })
      emit({ type: 'camera_status', cameraId: message.camera_id })
      break
    case 'alert_acknowledged':
      live.markAlertAcknowledged(message.alert_id, message.false_alarm)
      notify({
        id: `alert_acknowledged:${message.alert_id}`,
        kind: 'alert_acknowledged',
        severity: 'info',
        title: `Alert ${message.false_alarm ? 'marked false alarm' : 'acknowledged'}`,
        message: `${message.alert_id} by ${message.acknowledged_by_username ?? 'operator'}`,
        cameraId: message.camera_id,
        alertId: message.alert_id,
        timestamp: message.timestamp,
      })
      emit({ type: 'alert_acknowledged', alertId: message.alert_id })
      break
    case 'zone_update':
    case 'zone_deleted':
      emit({ type: 'zones_changed', cameraId: message.camera_id })
      break
    case 'neighbor_offline_alert':
      notify({
        id: `neighbor_offline:${cameraId}:${message.offline_camera_id}:${message.timestamp}`,
        kind: 'neighbor_offline',
        severity: 'warning',
        title: `Neighbour ${message.offline_camera_id} offline`,
        message: message.message,
        cameraId,
        alertId: null,
        timestamp: message.timestamp,
      })
      break
    case 'error':
      live.setConnection(cameraId, 'open', message.message)
      break
    case 'pong':
    case 'acknowledge_ok':
      break
  }
}

export const cameraSockets = {
  subscribe(cameraId: string): () => void {
    const id = cameraId.toUpperCase()
    let socket = sockets.get(id)
    if (!socket) {
      socket = new CameraSocket(id)
      sockets.set(id, socket)
    }
    socket.retain()
    let released = false
    return () => {
      if (released) return
      released = true
      const current = sockets.get(id)
      if (!current) return
      current.release()
      if (current.idle) sockets.delete(id)
    }
  },
  onEvent(listener: LiveListener): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  restartAll(): void {
    for (const socket of sockets.values()) socket.restart()
  },
  closeAll(): void {
    for (const socket of sockets.values()) socket.shutdown()
    sockets.clear()
  },
  openCount(): number {
    return sockets.size
  },
}
