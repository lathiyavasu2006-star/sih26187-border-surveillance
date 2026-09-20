import { beforeEach, describe, expect, it, vi } from 'vitest'
import { hasRole, useAuthStore } from '@/stores/authStore'
import { DETECTION_HOLD_MS, useLiveStore } from '@/stores/liveStore'
import { MAP_STYLES, useUiStore } from '@/stores/uiStore'
import { makeAlert, makeDetection, makeFrame, makeToken } from '@/test/fixtures'

describe('authStore', () => {
  beforeEach(() => useAuthStore.getState().clearSession())

  it('stores a session in sessionStorage without the password', () => {
    useAuthStore.getState().setSession(makeToken())
    const state = useAuthStore.getState()
    expect(state.user?.username).toBe('admin')
    expect(state.accessExpiresAt).toBeGreaterThan(Date.now() + 800_000)
    const persisted = window.sessionStorage.getItem('sih26187-session') ?? ''
    expect(persisted).toContain('access-token-value')
    expect(persisted.toLowerCase()).not.toContain('password')
    expect(window.localStorage.getItem('sih26187-session')).toBeNull()
  })

  it('locks, persists the lock and unlocks the same operator', () => {
    useAuthStore.getState().setSession(makeToken())
    useAuthStore.getState().lock()
    expect(useAuthStore.getState().locked).toBe(true)
    expect(window.sessionStorage.getItem('sih26187-session')).toContain('"locked":true')
    useAuthStore.getState().unlock(makeToken({ access_token: 'fresh' }))
    expect(useAuthStore.getState().locked).toBe(false)
    expect(useAuthStore.getState().accessToken).toBe('fresh')
  })

  it('does not lock without a session and clears with a message', () => {
    useAuthStore.getState().lock()
    expect(useAuthStore.getState().locked).toBe(false)
    useAuthStore.getState().setSession(makeToken())
    useAuthStore.getState().clearSession('Session expired')
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().consumeSessionMessage()).toBe('Session expired')
    expect(useAuthStore.getState().consumeSessionMessage()).toBeNull()
  })

  it('ranks roles like the backend dependencies', () => {
    expect(hasRole('admin', 'supervisor')).toBe(true)
    expect(hasRole('regional_head', 'supervisor')).toBe(true)
    expect(hasRole('operator', 'supervisor')).toBe(false)
    expect(hasRole(undefined, 'operator')).toBe(false)
  })
})

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.setState({ tabs: [{ path: '/dashboard', title: 'Dashboard' }], mapStyle: 'standard', sidebarMode: 'collapsed' })
  })

  it('cycles through every map style (Street, Topographic and Hybrid included) and wraps', () => {
    expect(MAP_STYLES).toHaveLength(9)
    const seen = MAP_STYLES.map(() => useUiStore.getState().cycleMapStyle())
    expect(seen).toEqual(['street', 'topographic', 'hybrid', 'light', 'dark', 'satellite', 'terrain', 'tactical', 'standard'])
  })

  it('defaults auto-lock to 5 minutes and migrates the old 10-minute default', async () => {
    expect(useUiStore.getInitialState().autoLockMinutes).toBe(5)
    const options = useUiStore.persist.getOptions()
    const migrated = (await options.migrate?.({ autoLockMinutes: 10, mapStyle: 'dark' }, 1)) as { autoLockMinutes: number; mapStyle: string }
    expect(migrated.autoLockMinutes).toBe(5)
    expect(migrated.mapStyle).toBe('dark')
    const custom = (await options.migrate?.({ autoLockMinutes: 30 }, 1)) as { autoLockMinutes: number }
    expect(custom.autoLockMinutes).toBe(30)
  })

  it('opens, de-duplicates and closes tabs; dashboard is permanent', () => {
    const ui = useUiStore.getState()
    ui.openTab({ path: '/alerts', title: 'Alerts' })
    ui.openTab({ path: '/map', title: 'Map' })
    ui.openTab({ path: '/alerts', title: 'Alerts' })
    expect(useUiStore.getState().tabs.map((tab) => tab.path)).toEqual(['/dashboard', '/alerts', '/map'])
    expect(useUiStore.getState().closeTab('/alerts')).toBe('/map')
    expect(useUiStore.getState().closeTab('/dashboard')).toBeNull()
    expect(useUiStore.getState().tabs.map((tab) => tab.path)).toEqual(['/dashboard', '/map'])
  })

  it('caps the tab strip at 12', () => {
    for (let i = 0; i < 20; i += 1) useUiStore.getState().openTab({ path: `/cameras/CAM-N-${i}`, title: `CAM ${i}` })
    const tabs = useUiStore.getState().tabs
    expect(tabs).toHaveLength(12)
    expect(tabs[0]?.path).toBe('/dashboard')
    expect(tabs[tabs.length - 1]?.path).toBe('/cameras/CAM-N-19')
  })

  it('toggles the pinned sidebar and clamps auto-lock', () => {
    useUiStore.getState().toggleSidebarPinned()
    expect(useUiStore.getState().sidebarMode).toBe('pinned')
    useUiStore.getState().setAutoLockMinutes(500)
    expect(useUiStore.getState().autoLockMinutes).toBe(120)
  })
})

describe('liveStore', () => {
  beforeEach(() => useLiveStore.getState().reset())

  it('ingests frames, measures latency and counts frames', () => {
    const sent = new Date(Date.now() - 120).toISOString()
    useLiveStore.getState().ingestFrame(makeFrame({ timestamp: sent }))
    const camera = useLiveStore.getState().cameras['CAM-N-001']
    expect(camera?.frameCount).toBe(1)
    expect(camera?.viewerCount).toBe(2)
    expect(camera?.latencyMs).toBeGreaterThanOrEqual(100)
    expect(camera?.stats?.fps).toBe(29.5)
  })

  it('holds detections between 2 Hz updates and clears them when stale', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(1_000_000)
      const store = useLiveStore.getState()
      store.ingestFrame(makeFrame({ detections: [makeDetection()] }))
      vi.setSystemTime(1_000_000 + 300)
      store.ingestFrame(makeFrame({ detections: [] }))
      expect(useLiveStore.getState().cameras['CAM-N-001']?.detections).toHaveLength(1)
      vi.setSystemTime(1_000_000 + DETECTION_HOLD_MS + 50)
      store.ingestFrame(makeFrame({ detections: [] }))
      expect(useLiveStore.getState().cameras['CAM-N-001']?.detections).toHaveLength(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('returns only new alerts and marks acknowledgements', () => {
    const alert = makeAlert()
    expect(useLiveStore.getState().ingestFrame(makeFrame({ frame: null, alerts: [alert] }))).toHaveLength(1)
    expect(useLiveStore.getState().ingestFrame(makeFrame({ frame: null, alerts: [alert] }))).toHaveLength(0)
    useLiveStore.getState().markAlertAcknowledged(alert.alert_id, true)
    expect(useLiveStore.getState().liveAlerts[0]).toMatchObject({ acknowledged: true, false_alarm: true })
    // An alerts-only message must not count as a video frame.
    expect(useLiveStore.getState().cameras['CAM-N-001']?.frameCount).toBe(0)
  })

  it('de-duplicates notifications broadcast on several sockets', () => {
    const notification = {
      id: 'critical_alert:ALT-1',
      kind: 'critical_alert' as const,
      severity: 'critical' as const,
      title: 'CRITICAL',
      message: 'm',
      cameraId: 'CAM-N-001',
      alertId: 'ALT-1',
      timestamp: '2026-09-17T00:00:00Z',
    }
    expect(useLiveStore.getState().pushNotification(notification)).toBe(true)
    expect(useLiveStore.getState().pushNotification(notification)).toBe(false)
    expect(useLiveStore.getState().notifications).toHaveLength(1)
  })

  it('records frame sizes once per change', () => {
    const store = useLiveStore.getState()
    store.setFrameSize('CAM-N-001', 1280, 720)
    const first = useLiveStore.getState().frameSizes
    store.setFrameSize('CAM-N-001', 1280, 720)
    expect(useLiveStore.getState().frameSizes).toBe(first)
    expect(first['CAM-N-001']).toEqual([1280, 720])
  })
})
