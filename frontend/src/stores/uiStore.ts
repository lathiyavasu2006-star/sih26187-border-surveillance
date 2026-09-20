import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { localJSONStorage } from '@/lib/storage'
import type { Alert, WSDetection } from '@/types'

export type SidebarMode = 'collapsed' | 'pinned'
export type GridSize = 1 | 2 | 3 | 4

export interface AppTab {
  /** Route path, also the tab identity. */
  path: string
  title: string
}

export type SpecialModal =
  | { kind: 'eventDna'; trackId: number; cameraId: string }
  | { kind: 'correlation'; trackId: number; cameraId: string; personUuid: string | null }
  | { kind: 'fusion'; cameraId: string; detection: WSDetection | null; alert?: Alert | null }
  | { kind: 'prediction'; trackId: number; cameraId: string }

export interface SelectedTrack {
  cameraId: string
  trackId: number
  personUuid: string | null
  detection: WSDetection | null
}

export const MAP_STYLES = ['standard', 'street', 'topographic', 'hybrid', 'light', 'dark', 'satellite', 'terrain', 'tactical'] as const
export type MapStyle = (typeof MAP_STYLES)[number]

export const DEFAULT_AUTO_LOCK_MINUTES = 5

const HOME_TAB: AppTab = { path: '/dashboard', title: 'Dashboard' }
const MAX_TABS = 12

interface UiState {
  sidebarMode: SidebarMode
  sidebarHover: boolean
  rightPanelOpen: boolean
  tabs: AppTab[]
  mapStyle: MapStyle
  hudEnabled: boolean
  soundEnabled: boolean
  gridSize: GridSize
  autoLockMinutes: number
  selectedCameraId: string | null
  selectedAlertId: string | null
  selectedTrack: SelectedTrack | null
  shortcutsOpen: boolean
  newTabMenuOpen: boolean
  panicActive: boolean
  modal: SpecialModal | null

  toggleSidebarPinned: () => void
  setSidebarHover: (hover: boolean) => void
  toggleRightPanel: () => void
  openTab: (tab: AppTab) => void
  closeTab: (path: string) => string | null
  cycleMapStyle: () => MapStyle
  setMapStyle: (style: MapStyle) => void
  toggleHud: () => void
  setSoundEnabled: (enabled: boolean) => void
  setGridSize: (size: GridSize) => void
  setAutoLockMinutes: (minutes: number) => void
  selectCamera: (cameraId: string | null) => void
  selectAlert: (alertId: string | null) => void
  selectTrack: (track: SelectedTrack | null) => void
  setShortcutsOpen: (open: boolean) => void
  setNewTabMenuOpen: (open: boolean) => void
  setPanicActive: (active: boolean) => void
  openModal: (modal: SpecialModal) => void
  closeModal: () => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      sidebarMode: 'collapsed',
      sidebarHover: false,
      rightPanelOpen: true,
      tabs: [HOME_TAB],
      mapStyle: 'standard',
      hudEnabled: true,
      soundEnabled: true,
      gridSize: 2,
      autoLockMinutes: DEFAULT_AUTO_LOCK_MINUTES,
      selectedCameraId: null,
      selectedAlertId: null,
      selectedTrack: null,
      shortcutsOpen: false,
      newTabMenuOpen: false,
      panicActive: false,
      modal: null,

      toggleSidebarPinned: () =>
        set((state) => ({ sidebarMode: state.sidebarMode === 'pinned' ? 'collapsed' : 'pinned', sidebarHover: false })),
      setSidebarHover: (hover) => set({ sidebarHover: hover }),
      toggleRightPanel: () => set((state) => ({ rightPanelOpen: !state.rightPanelOpen })),

      openTab: (tab) =>
        set((state) => {
          const index = state.tabs.findIndex((existing) => existing.path === tab.path)
          if (index >= 0) {
            if (state.tabs[index]?.title === tab.title) return state
            const tabs = [...state.tabs]
            tabs[index] = tab
            return { tabs }
          }
          const tabs = [...state.tabs, tab]
          // Drop the oldest closable tab once the strip is full (the dashboard tab always stays).
          while (tabs.length > MAX_TABS) {
            const removable = tabs.findIndex((existing) => existing.path !== HOME_TAB.path)
            tabs.splice(removable, 1)
          }
          return { tabs }
        }),

      closeTab: (path) => {
        const { tabs } = get()
        if (path === HOME_TAB.path) return null
        const index = tabs.findIndex((tab) => tab.path === path)
        if (index < 0) return null
        const next = tabs.filter((tab) => tab.path !== path)
        set({ tabs: next.length ? next : [HOME_TAB] })
        const neighbour = next[Math.min(index, next.length - 1)] ?? HOME_TAB
        return neighbour.path
      },

      cycleMapStyle: () => {
        const current = MAP_STYLES.indexOf(get().mapStyle)
        const next = MAP_STYLES[(current + 1) % MAP_STYLES.length] ?? 'standard'
        set({ mapStyle: next })
        return next
      },
      setMapStyle: (style) => set({ mapStyle: style }),
      toggleHud: () => set((state) => ({ hudEnabled: !state.hudEnabled })),
      setSoundEnabled: (enabled) => set({ soundEnabled: enabled }),
      setGridSize: (size) => set({ gridSize: size }),
      setAutoLockMinutes: (minutes) => set({ autoLockMinutes: Math.max(0, Math.min(120, Math.round(minutes))) }),
      selectCamera: (cameraId) => set({ selectedCameraId: cameraId }),
      selectAlert: (alertId) => set({ selectedAlertId: alertId }),
      selectTrack: (track) => set({ selectedTrack: track }),
      setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
      setNewTabMenuOpen: (open) => set({ newTabMenuOpen: open }),
      setPanicActive: (active) => set({ panicActive: active }),
      openModal: (modal) => set({ modal }),
      closeModal: () => set({ modal: null }),
    }),
    {
      name: 'sih26187-ui',
      storage: localJSONStorage,
      version: 2,
      // v1 → v2: the auto-lock default became 5 minutes; keep any value the operator chose explicitly.
      migrate: (persisted, version) => {
        const state = (persisted ?? {}) as Partial<UiState>
        if (version < 2 && state.autoLockMinutes === 10) state.autoLockMinutes = DEFAULT_AUTO_LOCK_MINUTES
        return state as UiState
      },
      // Preferences only. Operational state (panic, modals, selections) always starts clean.
      partialize: (state) => ({
        sidebarMode: state.sidebarMode,
        rightPanelOpen: state.rightPanelOpen,
        tabs: state.tabs,
        mapStyle: state.mapStyle,
        hudEnabled: state.hudEnabled,
        soundEnabled: state.soundEnabled,
        gridSize: state.gridSize,
        autoLockMinutes: state.autoLockMinutes,
        selectedCameraId: state.selectedCameraId,
      }),
    },
  ),
)
