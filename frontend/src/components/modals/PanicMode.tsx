import { Siren, Volume2, VolumeX, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { mergeAlerts } from '@/lib/alerts'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { RiskBadge, StatusDot } from '@/components/ui/primitives'
import { useAcknowledgeAlert, useAlerts } from '@/hooks/useAlerts'
import { useCameras, useNow, useStats } from '@/hooks/useData'
import { playAlarm } from '@/lib/alarm'
import { formatRelative, formatTime, titleCase } from '@/lib/utils'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'

const SIREN_INTERVAL_MS = 4_000
const MAX_FEEDS = 9

/**
 * Panic (F12): an emergency command view that replaces the console with every camera feed, the open
 * critical/high alerts with one-key acknowledgement, and a repeating siren until muted or exited.
 */
export function PanicMode({ onExit }: { onExit: () => void }) {
  const navigate = useNavigate()
  const now = useNow(1000)
  const [sirenOn, setSirenOn] = useState(true)
  const { data: cameras } = useCameras()
  const { data: stats } = useStats()
  const { data: alertPage } = useAlerts({ acknowledged: false, limit: 100 })
  const liveAlerts = useLiveStore((state) => state.liveAlerts)
  const connections = useLiveStore((state) => state.connection)
  const acknowledge = useAcknowledgeAlert()
  const selectCamera = useUiStore((state) => state.selectCamera)

  const alerts = useMemo(
    () =>
      mergeAlerts(alertPage?.items ?? [], liveAlerts)
        .filter((alert) => !alert.acknowledged && (alert.risk_level === 'critical' || alert.risk_level === 'high_risk'))
        .slice(0, 30),
    [alertPage, liveAlerts],
  )

  useEffect(() => {
    if (!sirenOn) return undefined
    playAlarm('critical')
    const timer = window.setInterval(() => playAlarm('critical'), SIREN_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [sirenOn])

  const feeds = (cameras?.items ?? []).slice(0, MAX_FEEDS)
  const columns = feeds.length <= 1 ? 1 : feeds.length <= 4 ? 2 : 3

  return (
    <div className="fixed inset-0 z-[4000] flex flex-col bg-slate-950 text-slate-100" role="dialog" aria-modal="true" aria-label="Panic mode" data-testid="panic-mode">
      <div className="pointer-events-none absolute inset-0 animate-pulse border-[6px] border-red-600/70" aria-hidden />
      <header className="flex h-14 shrink-0 items-center gap-4 bg-red-700 px-5">
        <Siren className="size-6 animate-pulse" />
        <div>
          <p className="text-sm font-black tracking-widest">PANIC MODE · EMERGENCY COMMAND VIEW</p>
          <p className="text-[11px] text-red-100">
            {formatTime(now)} IST · {stats ? `${stats.cameras_online}/${stats.cameras_total} cameras online` : 'loading status'} · {alerts.length} open critical/high alerts
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" onClick={() => setSirenOn(!sirenOn)} className="flex items-center gap-1.5 rounded-lg bg-black/25 px-3 py-1.5 text-xs font-semibold hover:bg-black/40">
            {sirenOn ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
            {sirenOn ? 'Mute siren' : 'Siren off'}
          </button>
          <button type="button" onClick={onExit} className="flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-xs font-bold text-red-700 hover:bg-red-50" data-testid="panic-exit">
            <X className="size-4" /> Exit (F12 / Esc)
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[1fr_360px]">
        <div className="grid min-h-0 gap-1 bg-black p-1" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gridAutoRows: 'minmax(0, 1fr)' }}>
          {feeds.length === 0 ? (
            <div className="flex items-center justify-center text-sm text-slate-500">No cameras registered.</div>
          ) : (
            feeds.map((camera) => (
              <button
                type="button"
                key={camera.camera_id}
                className="relative min-h-0 overflow-hidden rounded text-left"
                onDoubleClick={() => {
                  selectCamera(camera.camera_id)
                  onExit()
                  navigate(`/cameras/${camera.camera_id}`)
                }}
                aria-label={`Feed ${camera.camera_id}`}
              >
                <CameraFeed cameraId={camera.camera_id} cameraName={camera.name} compact={feeds.length > 1} interactive={false} />
                <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px]">
                  <StatusDot status={connections[camera.camera_id] ?? 'idle'} /> {camera.status.toUpperCase()}
                </span>
              </button>
            ))
          )}
        </div>
        <aside className="flex min-h-0 flex-col border-l border-red-900/60 bg-slate-950">
          <p className="border-b border-white/10 px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-red-300">Open critical & high-risk alerts</p>
          <ul className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
            {alerts.length === 0 ? (
              <li className="px-4 py-10 text-center text-xs text-slate-500">No open critical or high-risk alerts.</li>
            ) : (
              alerts.map((alert) => (
                <li key={alert.alert_id} className="border-b border-white/5 px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{titleCase(alert.alert_type)}</p>
                      <p className="font-mono text-[10px] text-slate-400">
                        {alert.camera_id}
                        {alert.zone_name ? ` · ${alert.zone_name}` : ''} · {formatRelative(alert.timestamp, now)}
                      </p>
                    </div>
                    <RiskBadge level={alert.risk_level} score={alert.risk_score} />
                  </div>
                  <button
                    type="button"
                    disabled={acknowledge.isPending}
                    onClick={() => acknowledge.mutate({ alertId: alert.alert_id, falseAlarm: false, notes: 'Acknowledged from panic mode' })}
                    className="mt-2 w-full rounded-md bg-white/10 py-1.5 text-xs font-semibold hover:bg-white/20 disabled:opacity-50"
                  >
                    Acknowledge
                  </button>
                </li>
              ))
            )}
          </ul>
        </aside>
      </div>
    </div>
  )
}
