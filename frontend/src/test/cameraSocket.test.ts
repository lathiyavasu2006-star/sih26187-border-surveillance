import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cameraSockets, type LiveEvent } from '@/lib/cameraSocket'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import { makeAlert, makeFrame, makeToken } from '@/test/fixtures'

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: MockWebSocket[] = []
  readonly url: string
  readyState = MockWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
  open() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }
  receive(message: unknown) {
    this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) })
  }
  send(data: string) {
    this.sent.push(data)
  }
  close(code = 1000, reason = '') {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({ code, reason })
  }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('cameraSockets', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    useLiveStore.getState().reset()
    useAuthStore.getState().setSession(makeToken())
  })
  afterEach(() => {
    cameraSockets.closeAll()
    useAuthStore.getState().clearSession()
    vi.unstubAllGlobals()
  })

  it('opens one reference-counted socket per camera with the JWT', async () => {
    const releaseA = cameraSockets.subscribe('cam-n-001')
    const releaseB = cameraSockets.subscribe('CAM-N-001')
    await flush()
    expect(MockWebSocket.instances).toHaveLength(1)
    const socket = MockWebSocket.instances[0]
    expect(socket?.url).toBe(`ws://${window.location.host}/ws/CAM-N-001?token=access-token-value`)
    socket?.open()
    expect(useLiveStore.getState().connection['CAM-N-001']).toBe('open')
    releaseA()
    expect(socket?.readyState).toBe(MockWebSocket.OPEN)
    releaseB()
    expect(socket?.readyState).toBe(MockWebSocket.CLOSED)
    expect(useLiveStore.getState().connection['CAM-N-001']).toBe('closed')
  })

  it('feeds frame updates into the live store and emits new alerts', async () => {
    const events: LiveEvent[] = []
    const off = cameraSockets.onEvent((event) => events.push(event))
    cameraSockets.subscribe('CAM-N-001')
    await flush()
    const socket = MockWebSocket.instances[0]
    socket?.open()
    socket?.receive({ type: 'connected', camera_id: 'CAM-N-001', user_id: 'u', role: 'admin', may_ingest_frames: true, viewer_count: 3, timestamp: '' })
    socket?.receive(makeFrame({ alerts: [makeAlert({ risk_level: 'high_risk' })] }))
    expect(useLiveStore.getState().cameras['CAM-N-001']?.frameCount).toBe(1)
    expect(events.some((event) => event.type === 'alerts')).toBe(true)
    expect(useLiveStore.getState().notifications[0]?.kind).toBe('new_alert')
    off()
  })

  it('ignores malformed and unknown messages', async () => {
    cameraSockets.subscribe('CAM-N-001')
    await flush()
    const socket = MockWebSocket.instances[0]
    socket?.open()
    socket?.receive('not json')
    socket?.receive({ type: 'evil', payload: 1 })
    socket?.receive({ type: 'frame_update', camera_id: 'CAM-N-001' })
    expect(useLiveStore.getState().cameras['CAM-N-001']).toBeUndefined()
  })

  it('shows one critical notification although every socket receives the broadcast', async () => {
    cameraSockets.subscribe('CAM-N-001')
    cameraSockets.subscribe('CAM-S-001')
    await flush()
    const broadcast = {
      type: 'critical_alert',
      severity: 'critical',
      alert_id: 'ALT-20260917063723-6564bb83e3bb4181',
      camera_id: 'CAM-N-001',
      alert_type: 'intrusion',
      risk_score: 95,
      risk_reasons: [],
      message: 'CRITICAL ALERT on CAM-N-001 — Risk Score: 95',
      sound: 'alarm',
      timestamp: '2026-09-17T00:00:00Z',
    }
    for (const socket of MockWebSocket.instances) {
      socket.open()
      socket.receive(broadcast)
    }
    expect(MockWebSocket.instances).toHaveLength(2)
    expect(useLiveStore.getState().notifications.filter((item) => item.kind === 'critical_alert')).toHaveLength(1)
  })

  it('reconnects with backoff after an unexpected close', async () => {
    vi.useFakeTimers()
    try {
      cameraSockets.subscribe('CAM-N-001')
      await vi.advanceTimersByTimeAsync(0)
      const first = MockWebSocket.instances[0]
      first?.open()
      first?.close(1006, '')
      expect(useLiveStore.getState().connection['CAM-N-001']).toBe('reconnecting')
      await vi.advanceTimersByTimeAsync(1300)
      expect(MockWebSocket.instances).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not connect without a session', async () => {
    useAuthStore.getState().clearSession()
    cameraSockets.subscribe('CAM-N-001')
    await flush()
    expect(MockWebSocket.instances).toHaveLength(0)
    expect(useLiveStore.getState().connectionDetail['CAM-N-001']).toBe('Not signed in')
  })
})
