import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import * as Popover from '@radix-ui/react-popover'
import {
  Bell,
  Keyboard,
  Lock,
  LogOut,
  PanelRight,
  Search,
  Siren,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { forwardRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Badge, StatusDot, Tip } from '@/components/ui/primitives'
import { useHealth, useNow, useStats } from '@/hooks/useData'
import { unlockAudio } from '@/lib/alarm'
import { ROLE_LABEL } from '@/lib/constants'
import { cn, formatRelative, formatTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'

const ALERT_ID = /^ALT-\d{14}-[0-9a-f]{16}$/i
const CAMERA_ID = /^CAM-[NSEW]-\d{3}$/i

export const TopNavbar = forwardRef<HTMLInputElement, { onLogout: () => void }>(function TopNavbar({ onLogout }, searchRef) {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const lock = useAuthStore((state) => state.lock)
  const now = useNow(1000)
  const { data: health, isError: healthError } = useHealth()
  const { data: stats } = useStats()
  const connections = useLiveStore((state) => state.connection)
  const notifications = useLiveStore((state) => state.notifications)
  const markRead = useLiveStore((state) => state.markNotificationsRead)
  const clearNotifications = useLiveStore((state) => state.clearNotifications)
  const soundEnabled = useUiStore((state) => state.soundEnabled)
  const setSoundEnabled = useUiStore((state) => state.setSoundEnabled)
  const setShortcutsOpen = useUiStore((state) => state.setShortcutsOpen)
  const setPanicActive = useUiStore((state) => state.setPanicActive)
  const toggleRightPanel = useUiStore((state) => state.toggleRightPanel)
  const [query, setQuery] = useState('')

  const states = Object.values(connections)
  const openSockets = states.filter((state) => state === 'open').length
  const liveState = openSockets > 0 ? 'open' : states.some((state) => state === 'connecting' || state === 'reconnecting') ? 'reconnecting' : 'closed'
  const unread = notifications.filter((item) => !item.read).length
  const serverState = healthError ? 'down' : (health?.status ?? 'idle')

  const onSearch = (event: FormEvent) => {
    event.preventDefault()
    const value = query.trim()
    if (!value) return
    if (CAMERA_ID.test(value)) navigate(`/cameras/${value.toUpperCase()}`)
    else if (ALERT_ID.test(value)) navigate(`/alerts?alert=${encodeURIComponent(value)}`)
    else if (/^\d{1,9}$/.test(value)) navigate(`/events?track=${value}`)
    else {
      toast.error('Search by camera ID (CAM-N-001), alert ID (ALT-…) or track number')
      return
    }
    setQuery('')
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-white pr-4">
      <div className="flex w-[240px] shrink-0 items-center">
        {/* The logo sits in a 56px column so it lines up with the sidebar rail icons below it. */}
        <div className="flex w-14 shrink-0 justify-center">
          <div className="relative flex size-9 items-center justify-center rounded-lg bg-slate-900">
          <svg viewBox="0 0 24 24" className="size-5 text-hud" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M12 2 4 5v6c0 5.2 3.4 9.4 8 11 4.6-1.6 8-5.8 8-11V5z" />
            <circle cx="12" cy="11" r="2.6" fill="currentColor" />
          </svg>
          </div>
        </div>
        <div className="min-w-0 leading-tight">
          <p className="truncate text-sm font-bold tracking-tight text-slate-900">BORDER SURVEILLANCE</p>
          <p className="truncate text-[10px] font-medium uppercase tracking-wider text-muted">MHA · SSB · SIH26187</p>
        </div>
      </div>

      <form onSubmit={onSearch} className="relative w-full max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" aria-hidden />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Camera, alert or track ID…"
          className="h-9 w-full rounded-lg border border-line bg-slate-50 pl-9 pr-16 text-sm placeholder:text-slate-400 focus:border-cyan-600 focus:bg-white focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
          aria-label="Global search"
          data-testid="global-search"
        />
        <kbd className="kbd absolute right-2 top-1/2 -translate-y-1/2">Ctrl F</kbd>
      </form>

      <div className="ml-auto flex items-center gap-2">
        <div className="hidden items-center gap-3 rounded-lg border border-line bg-slate-50 px-3 py-1.5 text-[11px] font-medium text-slate-600 xl:flex">
          <span className="flex items-center gap-1.5" title={`API ${serverState}, database ${health?.db ?? 'unknown'}`}>
            <StatusDot status={serverState} pulse={serverState === 'healthy'} /> API
          </span>
          <span className="flex items-center gap-1.5" title={`${openSockets} live socket(s)`} data-testid="ws-status" data-state={liveState}>
            <StatusDot status={liveState} /> LIVE
          </span>
          <span className="flex items-center gap-1.5">
            <StatusDot status={stats && stats.cameras_offline > 0 ? 'degraded' : 'online'} />
            CAM {stats ? `${stats.cameras_online}/${stats.cameras_total}` : '—'}
          </span>
        </div>

        <div className="rounded-lg bg-slate-900 px-3 py-1.5 font-mono text-xs font-semibold tabular-nums text-hud" aria-label="Indian Standard Time">
          {formatTime(now)} <span className="text-slate-400">IST</span>
        </div>

        <Tip label={soundEnabled ? 'Mute alarms (F8)' : 'Enable alarms (F8)'}>
          <button
            type="button"
            onClick={() => {
              unlockAudio()
              setSoundEnabled(!soundEnabled)
            }}
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            aria-label={soundEnabled ? 'Mute alarms' : 'Enable alarms'}
          >
            {soundEnabled ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
          </button>
        </Tip>

        <Popover.Root onOpenChange={(open) => (open ? undefined : markRead())}>
          <Popover.Trigger asChild>
            <button type="button" className="relative rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" aria-label="Notifications" data-testid="notifications">
              <Bell className="size-4" />
              {unread > 0 ? (
                <span className="absolute right-1 top-1 min-w-4 rounded-full bg-red-600 px-1 text-center text-[9px] font-bold leading-4 text-white">
                  {unread > 99 ? '99+' : unread}
                </span>
              ) : null}
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content align="end" sideOffset={8} className="z-[1100] w-[360px] rounded-xl border border-line bg-white shadow-2xl">
              <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                <p className="text-sm font-semibold">Live notifications</p>
                <button type="button" onClick={clearNotifications} className="text-xs text-muted hover:text-slate-900">
                  Clear
                </button>
              </div>
              <ul className="scrollbar-thin max-h-[420px] overflow-y-auto">
                {notifications.length === 0 ? (
                  <li className="px-4 py-8 text-center text-xs text-muted">No notifications since this session started.</li>
                ) : (
                  notifications.slice(0, 50).map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => {
                          if (item.alertId) navigate(`/alerts?alert=${encodeURIComponent(item.alertId)}`)
                          else if (item.cameraId) navigate(`/cameras/${item.cameraId}`)
                        }}
                        className={cn('flex w-full gap-3 border-b border-slate-100 px-4 py-2.5 text-left hover:bg-slate-50', !item.read && 'bg-cyan-50/40')}
                      >
                        <span
                          className={cn(
                            'mt-1 size-2 shrink-0 rounded-full',
                            item.severity === 'critical' ? 'bg-red-600' : item.severity === 'warning' ? 'bg-amber-500' : 'bg-cyan-500',
                          )}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-semibold text-slate-900">{item.title}</span>
                          <span className="line-clamp-2 block text-[11px] text-muted">{item.message}</span>
                        </span>
                        <span className="shrink-0 text-[10px] text-slate-400">{formatRelative(item.receivedAt, now)}</span>
                      </button>
                    </li>
                  ))
                )}
              </ul>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>

        <Tip label="Right panel (Alt+R)">
          <button type="button" onClick={toggleRightPanel} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" aria-label="Toggle right panel">
            <PanelRight className="size-4" />
          </button>
        </Tip>
        <Tip label="Keyboard shortcuts (?)">
          <button type="button" onClick={() => setShortcutsOpen(true)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" aria-label="Keyboard shortcuts">
            <Keyboard className="size-4" />
          </button>
        </Tip>
        <Tip label="Lock screen (Ctrl+L / Alt+L)">
          <button type="button" onClick={lock} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" aria-label="Lock screen" data-testid="lock-button">
            <Lock className="size-4" />
          </button>
        </Tip>
        <Tip label="Panic mode (F12)">
          <button
            type="button"
            onClick={() => {
              unlockAudio()
              setPanicActive(true)
            }}
            className="flex h-8 items-center gap-1.5 rounded-lg bg-red-600 px-2.5 text-xs font-bold text-white hover:bg-red-700"
            aria-label="Panic mode"
          >
            <Siren className="size-4" /> PANIC
          </button>
        </Tip>

        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button type="button" className="flex items-center gap-2 rounded-lg py-1 pl-1 pr-2 hover:bg-slate-100" aria-label="Account menu" data-testid="user-menu">
              <span className="flex size-8 items-center justify-center rounded-full bg-slate-900 text-xs font-bold uppercase text-hud">
                {user?.username.slice(0, 2) ?? '—'}
              </span>
              <span className="hidden text-left leading-tight 2xl:block">
                <span className="block text-xs font-semibold text-slate-900">{user?.username}</span>
                <span className="block text-[10px] text-muted">{user ? ROLE_LABEL[user.role] : ''}</span>
              </span>
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end" sideOffset={6} className="z-[1100] min-w-56 rounded-xl border border-line bg-white p-1 shadow-xl">
              <div className="px-3 py-2">
                <p className="text-sm font-semibold">{user?.username}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {user ? <Badge>{ROLE_LABEL[user.role]}</Badge> : null}
                  <Badge>{user?.camera_access.length ? `${user.camera_access.length} cameras` : 'All cameras'}</Badge>
                </div>
              </div>
              <DropdownMenu.Separator className="my-1 h-px bg-line" />
              <DropdownMenu.Item onSelect={() => navigate('/settings')} className="cursor-pointer rounded-lg px-3 py-2 text-xs outline-none data-[highlighted]:bg-slate-100">
                Account & settings
              </DropdownMenu.Item>
              <DropdownMenu.Item onSelect={lock} className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs outline-none data-[highlighted]:bg-slate-100">
                <Lock className="size-3.5" /> Lock screen
              </DropdownMenu.Item>
              <DropdownMenu.Item
                onSelect={onLogout}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs text-red-600 outline-none data-[highlighted]:bg-red-50"
                data-testid="logout"
              >
                <LogOut className="size-3.5" /> Sign out
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </header>
  )
})
