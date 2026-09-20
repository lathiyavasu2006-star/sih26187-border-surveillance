import { create } from 'zustand'
import type { Alert, LiveCameraState, WSConnectionState, WSDetection, WSFrameUpdate, WSStats } from '@/types'

export type NotificationSeverity = 'critical' | 'warning' | 'info'

export interface LiveNotification {
  id: string
  kind: 'critical_alert' | 'camera_offline' | 'camera_online' | 'alert_acknowledged' | 'zone_update' | 'neighbor_offline' | 'new_alert'
  severity: NotificationSeverity
  title: string
  message: string
  cameraId: string | null
  alertId: string | null
  timestamp: string
  receivedAt: number
  read: boolean
}

const MAX_NOTIFICATIONS = 200
const MAX_LIVE_ALERTS = 300
/** Detections are published at ~2 Hz while frames arrive at ~10 Hz; keep boxes on screen between updates. */
export const DETECTION_HOLD_MS = 1200

interface LiveState {
  cameras: Record<string, LiveCameraState>
  connection: Record<string, WSConnectionState>
  connectionDetail: Record<string, string | null>
  /** Decoded frame dimensions per camera (source pixel space for zones and detections). */
  frameSizes: Record<string, [number, number]>
  notifications: LiveNotification[]
  /** Alerts created by the ML pipeline, as broadcast in frame_update (newest first). */
  liveAlerts: Alert[]
  lastAlertAt: number

  ingestFrame: (message: WSFrameUpdate) => Alert[]
  setViewerCount: (cameraId: string, count: number) => void
  setFrameSize: (cameraId: string, width: number, height: number) => void
  setConnection: (cameraId: string, state: WSConnectionState, detail?: string | null) => void
  pushNotification: (notification: Omit<LiveNotification, 'receivedAt' | 'read'>) => boolean
  markNotificationsRead: () => void
  clearNotifications: () => void
  markAlertAcknowledged: (alertId: string, falseAlarm: boolean) => void
  resetCamera: (cameraId: string) => void
  reset: () => void
}

function emptyCamera(cameraId: string): LiveCameraState {
  return {
    cameraId,
    frame: null,
    frameReceivedAt: 0,
    detections: [],
    detectionsReceivedAt: 0,
    stats: null,
    viewerCount: 0,
    latencyMs: null,
    frameCount: 0,
    measuredFps: 0,
  }
}

export const useLiveStore = create<LiveState>()((set, get) => ({
  cameras: {},
  connection: {},
  connectionDetail: {},
  frameSizes: {},
  notifications: [],
  liveAlerts: [],
  lastAlertAt: 0,

  ingestFrame: (message) => {
    const now = Date.now()
    const previous = get().cameras[message.camera_id] ?? emptyCamera(message.camera_id)
    const hasFrame = typeof message.frame === 'string' && message.frame.length > 0
    const sent = Date.parse(message.timestamp)

    let measuredFps = previous.measuredFps
    if (hasFrame && previous.frameReceivedAt > 0) {
      const interval = now - previous.frameReceivedAt
      if (interval > 0 && interval < 5000) {
        const instantaneous = 1000 / interval
        measuredFps = previous.measuredFps === 0 ? instantaneous : previous.measuredFps * 0.85 + instantaneous * 0.15
      }
    }

    // The ML pipeline publishes detections at a lower rate than frames: an empty list on a frame-carrying
    // message means "no detection update", not "nothing detected", unless the previous set is stale.
    const detectionsUpdated = message.detections.length > 0 || now - previous.detectionsReceivedAt > DETECTION_HOLD_MS
    const detections: WSDetection[] = detectionsUpdated ? message.detections : previous.detections
    const stats: WSStats | null = hasFrame ? message.stats : previous.stats

    const next: LiveCameraState = {
      cameraId: message.camera_id,
      frame: hasFrame ? message.frame : previous.frame,
      frameReceivedAt: hasFrame ? now : previous.frameReceivedAt,
      detections,
      detectionsReceivedAt: detectionsUpdated && message.detections.length > 0 ? now : previous.detectionsReceivedAt,
      stats,
      viewerCount: message.viewer_count,
      latencyMs: hasFrame && Number.isFinite(sent) ? Math.max(0, now - sent) : previous.latencyMs,
      frameCount: previous.frameCount + (hasFrame ? 1 : 0),
      measuredFps,
    }

    const known = new Set(get().liveAlerts.map((alert) => alert.alert_id))
    const fresh = message.alerts.filter((alert) => !known.has(alert.alert_id))

    set((state) => ({
      cameras: { ...state.cameras, [message.camera_id]: next },
      ...(fresh.length
        ? {
            liveAlerts: [...fresh, ...state.liveAlerts].slice(0, MAX_LIVE_ALERTS),
            lastAlertAt: now,
          }
        : {}),
    }))
    return fresh
  },

  setViewerCount: (cameraId, count) =>
    set((state) => ({
      cameras: { ...state.cameras, [cameraId]: { ...(state.cameras[cameraId] ?? emptyCamera(cameraId)), viewerCount: count } },
    })),

  setFrameSize: (cameraId, width, height) =>
    set((state) => {
      const current = state.frameSizes[cameraId]
      if (current && current[0] === width && current[1] === height) return state
      return { frameSizes: { ...state.frameSizes, [cameraId]: [width, height] } }
    }),

  setConnection: (cameraId, connectionState, detail = null) =>
    set((state) => {
      if (state.connection[cameraId] === connectionState && state.connectionDetail[cameraId] === detail) return state
      return {
        connection: { ...state.connection, [cameraId]: connectionState },
        connectionDetail: { ...state.connectionDetail, [cameraId]: detail },
      }
    }),

  pushNotification: (notification) => {
    // Operator-wide broadcasts arrive once per open camera socket: keep only the first copy.
    if (get().notifications.some((existing) => existing.id === notification.id)) return false
    set((state) => ({
      notifications: [{ ...notification, receivedAt: Date.now(), read: false }, ...state.notifications].slice(0, MAX_NOTIFICATIONS),
    }))
    return true
  },

  markNotificationsRead: () =>
    set((state) => ({ notifications: state.notifications.map((item) => (item.read ? item : { ...item, read: true })) })),

  clearNotifications: () => set({ notifications: [] }),

  markAlertAcknowledged: (alertId, falseAlarm) =>
    set((state) => ({
      liveAlerts: state.liveAlerts.map((alert) =>
        alert.alert_id === alertId ? { ...alert, acknowledged: true, false_alarm: falseAlarm } : alert,
      ),
    })),

  resetCamera: (cameraId) =>
    set((state) => {
      const cameras = { ...state.cameras }
      delete cameras[cameraId]
      return { cameras }
    }),

  reset: () => set({ cameras: {}, connection: {}, connectionDetail: {}, frameSizes: {}, notifications: [], liveAlerts: [], lastAlertAt: 0 }),
}))
