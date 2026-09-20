import { Bell, Car, Cctv, Gauge, LayoutGrid, Maximize2, ShieldAlert, Users } from 'lucide-react'
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertPanel } from '@/components/alerts/AlertPanel'
import { mergeAlerts } from '@/lib/alerts'
import { CameraGrid } from '@/components/camera/CameraGrid'
import { IndiaMap } from '@/components/map/IndiaMap'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner, StatCard, Tip } from '@/components/ui/primitives'
import { useAlerts, useAlertStats } from '@/hooks/useAlerts'
import { useCameras, useNow, useStats } from '@/hooks/useData'
import { useKeyboard } from '@/hooks/useKeyboard'
import { formatDuration, istDayStartIso, titleCase } from '@/lib/utils'
import { livePipelineFps } from '@/lib/liveFps'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore, type GridSize } from '@/stores/uiStore'
import type { SystemStats } from '@/types'

/** Pipeline FPS from the live WebSocket stream (re-renders on its own, not the whole dashboard). */
function PipelineFpsCard({ stats, loading }: { stats: SystemStats | undefined; loading: boolean }) {
  const now = useNow(2_000)
  const liveFps = useLiveStore((state) => livePipelineFps(state.cameras, now))
  return (
    <StatCard
      label="Pipeline FPS"
      value={liveFps !== null ? liveFps.toFixed(1) : stats ? stats.avg_system_fps.toFixed(1) : '—'}
      sub={
        stats
          ? `${liveFps !== null ? 'Live WS' : 'Backend avg'} · CPU ${stats.cpu_percent.toFixed(0)}% · GPU ${stats.gpu_percent === null ? 'n/a' : `${stats.gpu_percent.toFixed(0)}%`}`
          : undefined
      }
      icon={<Gauge className="size-4" />}
      loading={loading}
    />
  )
}

export function DashboardPage() {
  const navigate = useNavigate()
  const now = useNow(10_000)
  const stats = useStats()
  const cameras = useCameras()
  const alertStats = useAlertStats()
  const recent = useAlerts({ limit: 50 })
  // Unacknowledged alerts raised since 00:00 IST: splits ACTIVE ALERTS into today vs earlier days.
  const openToday = useAlerts({ acknowledged: false, date_from: istDayStartIso(now), limit: 1 })
  const liveAlerts = useLiveStore((state) => state.liveAlerts)
  const gridSize = useUiStore((state) => state.gridSize)
  const setGridSize = useUiStore((state) => state.setGridSize)
  const cycleMapStyle = useUiStore((state) => state.cycleMapStyle)

  useKeyboard({
    '1': () => setGridSize(1),
    '2': () => setGridSize(2),
    '3': () => setGridSize(3),
    '4': () => setGridSize(4),
    m: () => toast(`Map style: ${MAP_STYLE_DEFINITIONS[cycleMapStyle()].label}`, { id: 'map-style', duration: 1500 }),
  })

  const alerts = useMemo(() => mergeAlerts(recent.data?.items ?? [], liveAlerts).slice(0, 50), [recent.data, liveAlerts])
  const hourly = useMemo(
    () => (alertStats.data?.hourly_trend ?? []).map((point) => ({ hour: `${String(point.hour).padStart(2, '0')}:00`, count: point.count })),
    [alertStats.data],
  )
  const byType = useMemo(
    () =>
      Object.entries(alertStats.data?.by_type ?? {})
        .map(([type, count]) => ({ type: titleCase(type), count }))
        .sort((a, b) => b.count - a.count),
    [alertStats.data],
  )
  const s = stats.data

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="dashboard">
      <PageHeader
        icon={<LayoutGrid className="size-4" />}
        title="Command Dashboard"
        subtitle={s ? `Backend uptime ${formatDuration(s.uptime_seconds)} · ${s.websocket_clients} live sockets` : 'Connecting to backend…'}
        actions={
          <div className="flex items-center gap-1 rounded-lg border border-line bg-white p-1">
            {([1, 2, 3, 4] as GridSize[]).map((size) => (
              <button
                key={size}
                type="button"
                onClick={() => setGridSize(size)}
                className={`rounded-md px-2 py-1 font-mono text-[11px] font-semibold ${gridSize === size ? 'bg-slate-900 text-hud' : 'text-slate-600 hover:bg-slate-100'}`}
                aria-pressed={gridSize === size}
                title={`Grid ${size}×${size} (${size})`}
              >
                {size}×{size}
              </button>
            ))}
          </div>
        }
      />

      {stats.isError ? <ErrorState error={stats.error} onRetry={() => void stats.refetch()} /> : null}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6" data-testid="dashboard-stats">
        <StatCard label="Persons today" value={s?.persons_detected_today ?? 0} icon={<Users className="size-4" />} loading={stats.isLoading} />
        <StatCard label="Vehicles today" value={s?.vehicles_detected_today ?? 0} icon={<Car className="size-4" />} loading={stats.isLoading} />
        <StatCard
          label="Active alerts"
          value={s?.active_alerts ?? 0}
          sub={
            alertStats.data && s && openToday.data
              ? `${alertStats.data.total_today} raised today · ${Math.max(0, s.active_alerts - openToday.data.total)} from earlier days`
              : alertStats.data
                ? `${alertStats.data.total_today} raised today`
                : undefined
          }
          icon={<Bell className="size-4" />}
          tone={s && s.active_alerts > 0 ? 'warning' : 'default'}
          loading={stats.isLoading}
        />
        <StatCard
          label="Critical"
          value={s?.critical_alerts ?? 0}
          sub={alertStats.data ? `${alertStats.data.critical_today} today` : undefined}
          icon={<ShieldAlert className="size-4" />}
          tone={s && s.critical_alerts > 0 ? 'critical' : 'default'}
          loading={stats.isLoading}
        />
        <StatCard
          label="Cameras online"
          value={s ? `${s.cameras_online}/${s.cameras_total}` : '—'}
          sub={s ? `${s.cameras_offline} offline · ${s.cameras_degraded} degraded` : undefined}
          icon={<Cctv className="size-4" />}
          tone={s && s.cameras_offline > 0 ? 'critical' : 'good'}
          loading={stats.isLoading}
        />
        <PipelineFpsCard stats={s} loading={stats.isLoading} />
      </div>

      <div className="grid gap-4 2xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader
            title="Live camera grid"
            subtitle="Double-click a feed to open the tactical view · keys 1–4 change the grid"
            icon={<Cctv className="size-4" />}
          />
          <div className="p-3">
            {cameras.isLoading ? (
              <Spinner className="py-16" label="Loading cameras…" />
            ) : cameras.isError ? (
              <ErrorState error={cameras.error} onRetry={() => void cameras.refetch()} />
            ) : cameras.data?.items.length ? (
              <CameraGrid cameras={cameras.data.items} size={gridSize} />
            ) : (
              <EmptyState icon={<Cctv className="size-8" />} title="No cameras registered" description="Register a camera from the Live Cameras page." />
            )}
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="overflow-hidden">
            <CardHeader title="Threat map" subtitle="Click the map to open the full view · M cycles map style" />
            <Tip label="Click to expand" side="top">
              <button
                type="button"
                onClick={() => navigate('/map')}
                className="group relative block h-64 w-full cursor-pointer text-left"
                aria-label="Open the full threat map"
                data-testid="dashboard-map-link"
              >
                <IndiaMap
                  cameras={cameras.data?.items ?? []}
                  alerts={alerts.filter((alert) => !alert.acknowledged)}
                  className="pointer-events-none h-64"
                  showStylePicker={false}
                  compact
                  interactive={false}
                />
                <span className="absolute inset-0 z-[450] bg-transparent transition-colors group-hover:bg-slate-900/10" aria-hidden />
                <span className="absolute bottom-2 right-2 z-[460] flex items-center gap-1 rounded-md bg-slate-900/85 px-2 py-1 text-[11px] font-semibold text-white opacity-0 transition-opacity group-hover:opacity-100">
                  <Maximize2 className="size-3" /> Click to expand
                </span>
              </button>
            </Tip>
          </Card>
          <Card className="flex min-h-0 flex-1 flex-col">
            <CardHeader
              title="Recent alerts"
              subtitle={recent.data ? `${recent.data.unacknowledged_count} unacknowledged` : undefined}
              actions={
                <button type="button" onClick={() => navigate('/alerts')} className="text-xs font-semibold text-cyan-700 hover:underline">
                  View all
                </button>
              }
            />
            <div className="scrollbar-thin max-h-80 overflow-y-auto">
              <AlertPanel
                alerts={alerts.slice(0, 20)}
                loading={recent.isLoading}
                selectedId={null}
                onSelect={(alert) => navigate(`/alerts?alert=${encodeURIComponent(alert.alert_id)}`)}
                now={now}
                dense
              />
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Alerts by hour (today, IST)" subtitle={alertStats.data ? `${alertStats.data.total_today} total` : undefined} />
          <div className="h-56 p-3">
            {alertStats.isLoading ? (
              <Spinner className="h-full" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={hourly} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                  <defs>
                    <linearGradient id="hourly-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#0891b2" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#0891b2" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="hour" tick={{ fontSize: 10 }} interval={2} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Area type="monotone" dataKey="count" name="Alerts" stroke="#0891b2" strokeWidth={2} fill="url(#hourly-fill)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Alerts by type (today)" />
          <div className="h-56 p-3">
            {byType.length === 0 ? (
              <EmptyState title="No alerts today" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byType} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 24 }}>
                  <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="type" tick={{ fontSize: 10 }} width={90} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Bar dataKey="count" name="Alerts" fill="#0f172a" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
