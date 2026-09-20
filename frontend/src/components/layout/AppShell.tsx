import { useEffect, useRef } from 'react'
import toast from 'react-hot-toast'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { CameraDock } from '@/components/camera/CameraDock'
import { RightPanel } from '@/components/layout/RightPanel'
import { Sidebar } from '@/components/layout/Sidebar'
import { TabBar } from '@/components/layout/TabBar'
import { TopNavbar } from '@/components/layout/TopNavbar'
import { CorrelationModal } from '@/components/modals/CorrelationModal'
import { EventDnaModal } from '@/components/modals/EventDnaModal'
import { FusionModal } from '@/components/modals/FusionModal'
import { PanicMode } from '@/components/modals/PanicMode'
import { PredictionModal } from '@/components/modals/PredictionModal'
import { ScreenLock } from '@/components/modals/ScreenLock'
import { ShortcutsOverlay } from '@/components/modals/ShortcutsOverlay'
import { useAlertLiveSync } from '@/hooks/useAlerts'
import { useCameras } from '@/hooks/useData'
import { KEY_PRIORITY, useKeyboard, type KeyBinding, type KeyBindings } from '@/hooks/useKeyboard'
import { useAutoLocateLocalCameras } from '@/hooks/useAutoLocate'
import { useIdleLock, useLockKeyReleaseFallback, useTokenKeepAlive } from '@/hooks/useSession'
import { useSignOut } from '@/hooks/useSignOut'
import { useCameraSubscriptions } from '@/hooks/useWebSocket'
import { playAlarm, unlockAudio } from '@/lib/alarm'
import { cameraSockets } from '@/lib/cameraSocket'
import { NAV_ITEMS, titleForPath } from '@/lib/navigation'
import { useAuthStore } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'

/** Live notifications stay on for this many cameras at once (one WebSocket each). */
const MAX_MONITORED_CAMERAS = 16

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const searchRef = useRef<HTMLInputElement>(null)
  const signOut = useSignOut()

  const locked = useAuthStore((state) => state.locked)
  const lock = useAuthStore((state) => state.lock)
  const rightPanelOpen = useUiStore((state) => state.rightPanelOpen)
  const shortcutsOpen = useUiStore((state) => state.shortcutsOpen)
  const setShortcutsOpen = useUiStore((state) => state.setShortcutsOpen)
  const panicActive = useUiStore((state) => state.panicActive)
  const setPanicActive = useUiStore((state) => state.setPanicActive)
  const modal = useUiStore((state) => state.modal)
  const closeModal = useUiStore((state) => state.closeModal)
  const openTab = useUiStore((state) => state.openTab)

  useTokenKeepAlive()
  useIdleLock()
  useLockKeyReleaseFallback()
  useAlertLiveSync()

  // Keep a live channel to every accessible camera so camera-scoped alerts arrive wherever the operator is.
  const { data: cameras } = useCameras()
  const cameraItems = cameras?.items ?? []
  useCameraSubscriptions(cameraItems.slice(0, MAX_MONITORED_CAMERAS).map((camera) => camera.camera_id))
  useAutoLocateLocalCameras(cameras?.items)

  // Every navigation opens (or focuses) a browser-style tab.
  useEffect(() => {
    openTab({ path: location.pathname, title: titleForPath(location.pathname) })
  }, [location.pathname, openTab])

  // Browsers only allow audio after a user gesture.
  useEffect(() => {
    const unlock = () => unlockAudio()
    window.addEventListener('pointerdown', unlock, { once: true })
    window.addEventListener('keydown', unlock, { once: true })
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [])

  // Toasts + alarm tones for live notifications.
  useEffect(
    () =>
      cameraSockets.onEvent((event) => {
        if (event.type !== 'notification') return
        const { notification } = event
        if (useUiStore.getState().soundEnabled && notification.severity !== 'info') playAlarm(notification.severity)
        if (notification.severity === 'critical') {
          toast.error(`${notification.title}\n${notification.message}`, { id: notification.id, duration: 10_000 })
        } else if (notification.severity === 'warning') {
          toast(`${notification.title}\n${notification.message}`, { id: notification.id, icon: '⚠️', duration: 6_000 })
        }
      }),
    [],
  )

  const tabStep = (direction: 1 | -1) => {
    const { tabs } = useUiStore.getState()
    const index = tabs.findIndex((tab) => tab.path === location.pathname)
    const next = tabs[(index + direction + tabs.length) % tabs.length]
    if (next) navigate(next.path)
  }
  const openNewTabMenu = () => useUiStore.getState().setNewTabMenuOpen(true)

  // Security keys work everywhere, including inside text fields.
  const lockKey: KeyBinding = { handler: () => lock(), allowInInputs: true }
  const panicKey: KeyBinding = {
    handler: () => {
      unlockAudio()
      setPanicActive(!useUiStore.getState().panicActive)
    },
    allowInInputs: true,
  }
  const logoutKey: KeyBinding = { handler: () => void signOut(), allowInInputs: true }
  const muteKey: KeyBinding = {
    handler: () => {
      const enabled = !useUiStore.getState().soundEnabled
      useUiStore.getState().setSoundEnabled(enabled)
      if (enabled) unlockAudio()
      toast(enabled ? 'Alarm sound on' : 'Alarm muted', { id: 'mute', duration: 1200, icon: enabled ? '🔔' : '🔕' })
    },
    allowInInputs: true,
  }
  const helpKey = () => setShortcutsOpen(!useUiStore.getState().shortcutsOpen)

  const bindings: KeyBindings = {
    'ctrl+l': lockKey,
    // Hosts that reserve Ctrl+L for their address bar (embedded browser panes) never deliver it to the page.
    'alt+l': lockKey,
    f12: panicKey,
    'ctrl+shift+l': logoutKey,
    f8: muteKey,
    '?': helpKey,
    escape: {
      handler: () => {
        if (panicActive) setPanicActive(false)
      },
      allowDefault: true,
    },
    'ctrl+d': () => navigate('/dashboard'),
    'ctrl+m': () => navigate('/map'),
    'ctrl+f': () => searchRef.current?.focus(),
    '/': () => searchRef.current?.focus(),
    'ctrl+b': () => {
      useUiStore.getState().toggleSidebarPinned()
      toast(`Sidebar ${useUiStore.getState().sidebarMode === 'pinned' ? 'pinned' : 'unpinned'}`, { id: 'sidebar', duration: 1000 })
    },
    'ctrl+t': openNewTabMenu,
    'alt+t': openNewTabMenu,
    'alt+r': () => useUiStore.getState().toggleRightPanel(),
    h: () => {
      useUiStore.getState().toggleHud()
      toast(`Tactical HUD ${useUiStore.getState().hudEnabled ? 'on' : 'off'}`, { id: 'hud', duration: 1200 })
    },
    'alt+w': () => {
      const next = useUiStore.getState().closeTab(location.pathname)
      if (next) navigate(next)
    },
    'alt+arrowright': () => tabStep(1),
    'alt+arrowleft': () => tabStep(-1),
  }
  NAV_ITEMS.forEach((item) => {
    bindings[item.shortcut] = () => navigate(item.path)
  })

  const overlayOpen = shortcutsOpen || modal !== null
  // Nothing but the lock screen's own form works while locked; panic mode and dialogs keep the security keys.
  const activeBindings: KeyBindings = locked
    ? {}
    : panicActive
      ? { f12: panicKey, escape: bindings.escape as KeyBinding, 'ctrl+l': lockKey, 'alt+l': lockKey, 'ctrl+shift+l': logoutKey, f8: muteKey }
      : overlayOpen
        ? { 'ctrl+l': lockKey, 'alt+l': lockKey, f12: panicKey, 'ctrl+shift+l': logoutKey, f8: muteKey, '?': helpKey }
        : bindings
  useKeyboard(activeBindings, true, KEY_PRIORITY.shell)

  return (
    <div className="flex h-full flex-col" data-testid="app-shell">
      <TopNavbar ref={searchRef} onLogout={() => void signOut()} />
      <div className="flex min-h-0 flex-1">
        <Sidebar onLogout={() => void signOut()} />
        <div className="flex min-w-0 flex-1 flex-col">
          <TabBar />
          <main className="scrollbar-thin min-h-0 flex-1 overflow-y-auto bg-shell" id="main">
            <Outlet />
          </main>
        </div>
        {rightPanelOpen ? <RightPanel /> : null}
      </div>
      <CameraDock cameras={cameraItems} />

      <ShortcutsOverlay open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      {modal?.kind === 'eventDna' ? <EventDnaModal trackId={modal.trackId} cameraId={modal.cameraId} onClose={closeModal} /> : null}
      {modal?.kind === 'correlation' ? (
        <CorrelationModal trackId={modal.trackId} cameraId={modal.cameraId} personUuid={modal.personUuid} onClose={closeModal} />
      ) : null}
      {modal?.kind === 'prediction' ? <PredictionModal trackId={modal.trackId} cameraId={modal.cameraId} onClose={closeModal} /> : null}
      {modal?.kind === 'fusion' ? <FusionModal cameraId={modal.cameraId} detection={modal.detection} alert={modal.alert ?? null} onClose={closeModal} /> : null}
      {panicActive && !locked ? <PanicMode onExit={() => setPanicActive(false)} /> : null}
      {locked ? <ScreenLock onSignOut={() => void signOut()} /> : null}
    </div>
  )
}
