import { Bell, Car, Cctv, LogOut, Pin, PinOff, ShieldHalf, UserRound } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { Kbd } from '@/components/ui/primitives'
import { useUnacknowledgedCount } from '@/hooks/useAlerts'
import { useCameras, useStats } from '@/hooks/useData'
import { ROLE_LABEL } from '@/lib/constants'
import { NAV_ITEMS } from '@/lib/navigation'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'

export const SIDEBAR_COLLAPSED = 56
export const SIDEBAR_EXPANDED = 220
const HOVER_OPEN_DELAY_MS = 60
const LATENCY_INTERVAL_MS = 15_000

const STATUS_DOT: Record<string, string> = { online: 'bg-green-500', degraded: 'bg-amber-400', offline: 'bg-red-500' }

/** Round-trip time of the public /ping endpoint through the proxy (null while unreachable). */
function useServerLatency(): { latency: number | null; ok: boolean } {
  const [state, setState] = useState<{ latency: number | null; ok: boolean }>({ latency: null, ok: true })
  useEffect(() => {
    let cancelled = false
    const probe = async () => {
      const started = performance.now()
      try {
        await api.get('/ping', { timeout: 5_000 })
        if (!cancelled) setState({ latency: Math.round(performance.now() - started), ok: true })
      } catch {
        if (!cancelled) setState({ latency: null, ok: false })
      }
    }
    void probe()
    const timer = window.setInterval(() => void probe(), LATENCY_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])
  return state
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <p className="px-[11px] pb-1 pt-3 font-mono text-[9px] font-bold uppercase tracking-[0.18em] text-slate-400">{children}</p>
}

function StatRow({ icon, label, value, tone }: { icon: ReactNode; label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="flex items-center gap-2 px-[11px] py-1 text-xs">
      <span className="text-slate-400">{icon}</span>
      <span className="flex-1 text-slate-600">{label}</span>
      <span className={cn('font-mono font-semibold tabular-nums text-slate-900', tone)}>{value}</span>
    </div>
  )
}

/**
 * Three states: collapsed (56px icon rail), hover (220px overlay above the content, the rail keeps its
 * space; opens in 200 ms, closes in 150 ms) and pinned (220px, pushes the content; Ctrl+B).
 */
export function Sidebar({ onLogout }: { onLogout: () => void }) {
  const navigate = useNavigate()
  const mode = useUiStore((state) => state.sidebarMode)
  const hover = useUiStore((state) => state.sidebarHover)
  const setHover = useUiStore((state) => state.setSidebarHover)
  const togglePinned = useUiStore((state) => state.toggleSidebarPinned)
  const selectedCameraId = useUiStore((state) => state.selectedCameraId)
  const user = useAuthStore((state) => state.user)
  const unacknowledged = useUnacknowledgedCount()
  const { data: stats } = useStats()
  const { data: cameras } = useCameras()
  const server = useServerLatency()
  const hoverTimer = useRef<number | null>(null)

  const pinned = mode === 'pinned'
  const expanded = pinned || hover
  const state = pinned ? 'pinned' : hover ? 'hover' : 'collapsed'

  const onEnter = () => {
    if (pinned) return
    hoverTimer.current = window.setTimeout(() => setHover(true), HOVER_OPEN_DELAY_MS)
  }
  const onLeave = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current)
    hoverTimer.current = null
    if (!pinned) setHover(false)
  }
  const reveal = cn('transition-opacity duration-150', expanded ? 'opacity-100' : 'pointer-events-none opacity-0')

  return (
    <div
      className="relative z-[600] h-full shrink-0 transition-[width] duration-200 ease-out"
      style={{ width: pinned ? SIDEBAR_EXPANDED : SIDEBAR_COLLAPSED }}
      data-testid="sidebar"
      data-state={state}
    >
      <aside
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        className={cn(
          'absolute inset-y-0 left-0 flex flex-col overflow-hidden border-r border-line bg-white transition-[width,box-shadow] ease-out',
          expanded ? 'duration-200' : 'duration-150',
          state === 'hover' && 'shadow-2xl shadow-slate-900/15',
        )}
        style={{ width: expanded ? SIDEBAR_EXPANDED : SIDEBAR_COLLAPSED }}
        aria-label="Primary navigation"
      >
        {/* Collapsed: no scrollbar (a Windows scrollbar would eat a third of the 56px rail and push the icons). */}
        <div className={cn('min-h-0 flex-1 overflow-x-hidden overflow-y-auto', expanded ? 'scrollbar-thin' : 'scrollbar-none')}>
          <nav className="px-2 pt-3">
            <ul className="flex flex-col gap-0.5">
              {NAV_ITEMS.map((item) => {
                const Icon = item.icon
                const badge = item.path === '/alerts' && unacknowledged > 0 ? unacknowledged : null
                return (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      title={expanded ? undefined : item.title}
                      className={({ isActive }) =>
                        cn(
                          'group relative flex h-10 items-center gap-3 rounded-lg px-[11px] text-sm font-medium transition-colors',
                          isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive ? <span className="absolute -left-2 bottom-2 top-2 w-1 rounded-r bg-hud" aria-hidden /> : null}
                          <Icon className={cn('size-[18px] shrink-0', isActive ? 'text-hud' : '')} aria-hidden />
                          <span className={cn('flex-1 truncate whitespace-nowrap', reveal)}>{item.title}</span>
                          {badge !== null ? (
                            <span
                              className={cn(
                                'rounded-full bg-red-600 px-1.5 text-[10px] font-bold leading-4 text-white',
                                expanded ? '' : 'absolute right-1 top-1 px-1 text-[9px]',
                              )}
                            >
                              {badge > 99 ? '99+' : badge}
                            </span>
                          ) : null}
                        </>
                      )}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </nav>

          <div className={cn('mt-1 border-t border-line pb-2', expanded ? reveal : 'hidden')} data-testid="sidebar-details" aria-hidden={!expanded}>
            <SectionTitle>Cameras</SectionTitle>
            <ul className="px-1">
              {(cameras?.items ?? []).slice(0, 12).map((camera) => (
                <li key={camera.camera_id}>
                  <button
                    type="button"
                    tabIndex={expanded ? 0 : -1}
                    onClick={() => navigate(`/cameras/${camera.camera_id}`)}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-[10px] py-1 text-left text-xs hover:bg-slate-100',
                      camera.camera_id === selectedCameraId && 'bg-cyan-50',
                    )}
                  >
                    <span className={cn('size-2 shrink-0 rounded-full', STATUS_DOT[camera.status] ?? 'bg-slate-400')} />
                    <span className="flex-1 truncate font-mono font-semibold text-slate-800">{camera.camera_id}</span>
                    <span className={cn('text-[10px]', camera.status === 'online' ? 'text-green-700' : camera.status === 'offline' ? 'text-red-600' : 'text-amber-600')}>
                      {camera.status}
                    </span>
                  </button>
                </li>
              ))}
              {cameras && cameras.items.length === 0 ? <li className="px-[11px] py-1 text-xs text-muted">None registered</li> : null}
            </ul>

            <SectionTitle>Live stats</SectionTitle>
            <StatRow icon={<UserRound className="size-3.5" />} label="Persons" value={stats?.persons_detected_today ?? '—'} />
            <StatRow icon={<Car className="size-3.5" />} label="Vehicles" value={stats?.vehicles_detected_today ?? '—'} />
            <StatRow icon={<Bell className="size-3.5" />} label="Alerts" value={unacknowledged} tone={unacknowledged > 0 ? 'text-red-600' : undefined} />
            <StatRow
              icon={<Cctv className="size-3.5" />}
              label="Online"
              value={stats ? `${stats.cameras_online}/${stats.cameras_total}` : '—'}
              tone={stats && stats.cameras_offline > 0 ? 'text-amber-600' : undefined}
            />

            <SectionTitle>Servers</SectionTitle>
            <div className="flex items-center gap-2 px-[11px] py-1 text-xs" data-testid="server-latency">
              <span className={cn('size-2 rounded-full', server.ok ? 'bg-green-500' : 'bg-red-500')} />
              <span className="flex-1 font-semibold text-slate-700">LOCAL</span>
              <span className="font-mono text-slate-600">{server.latency !== null ? `${server.latency} ms` : server.ok ? '…' : 'down'}</span>
            </div>
          </div>
        </div>

        <div className="border-t border-line p-2">
          <button
            type="button"
            onClick={togglePinned}
            className="flex h-9 w-full items-center gap-3 rounded-lg px-[11px] text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            aria-pressed={pinned}
            data-testid="sidebar-pin"
            title={expanded ? undefined : 'Pin sidebar (Ctrl+B)'}
          >
            {pinned ? <PinOff className="size-4 shrink-0" /> : <Pin className="size-4 shrink-0" />}
            <span className={cn('flex flex-1 items-center justify-between whitespace-nowrap', reveal)}>
              {pinned ? 'Unpin' : 'Pin'} sidebar
              <span className="flex gap-0.5">
                <Kbd>Ctrl</Kbd>
                <Kbd>B</Kbd>
              </span>
            </span>
          </button>
          <div className="mt-1 flex items-center gap-2 rounded-lg px-[7px] py-1.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-[10px] font-bold uppercase text-hud">
              {user?.username.slice(0, 2) ?? '—'}
            </span>
            <span className={cn('min-w-0 flex-1 leading-tight', reveal)}>
              <span className="block truncate text-xs font-semibold text-slate-900">{user?.username}</span>
              <span className="block truncate text-[10px] text-muted">{user ? ROLE_LABEL[user.role] : ''}</span>
            </span>
            <button
              type="button"
              onClick={onLogout}
              tabIndex={expanded ? 0 : -1}
              className={cn('rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600', reveal)}
              aria-label="Sign out"
              title="Sign out (Ctrl+Shift+L)"
              data-testid="sidebar-logout"
            >
              <LogOut className="size-4" />
            </button>
          </div>
          <div className={cn('mt-1 flex items-center gap-2 px-[11px] text-[10px] text-slate-400', expanded ? '' : 'justify-center px-0')}>
            <ShieldHalf className="size-3.5 shrink-0" />
            {expanded ? <span className="whitespace-nowrap">RESTRICTED · OFFICIAL USE</span> : null}
          </div>
        </div>
      </aside>
    </div>
  )
}
