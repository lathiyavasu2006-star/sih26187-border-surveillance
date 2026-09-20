import { useQuery } from '@tanstack/react-query'
import { Activity } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { alertsApi, statsApi } from '@/api/endpoints'
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Select, Spinner, StatCard } from '@/components/ui/primitives'
import { useAlertStats } from '@/hooks/useAlerts'
import { RISK_LEVELS, RISK_META } from '@/lib/constants'
import { formatTime, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'

const HOURS_OPTIONS = [6, 24, 72, 168]
const TYPE_COLORS = ['#0f172a', '#0891b2', '#f97316', '#dc2626', '#16a34a', '#a855f7', '#eab308', '#64748b', '#db2777', '#0d9488']

export function AnalyticsPage() {
  const { canViewHealthHistory } = usePermissions()
  const [hours, setHours] = useState(24)
  const stats = useAlertStats()
  const history = useQuery({
    queryKey: ['stats', 'health-history', hours],
    queryFn: () => statsApi.healthHistory(hours),
    enabled: canViewHealthHistory,
    refetchInterval: 60_000,
  })
  const falseAlarms = useQuery({
    queryKey: ['alerts', 'list', 'analytics-acknowledged'],
    queryFn: () => alertsApi.list({ acknowledged: true, limit: 1000 }),
    staleTime: 60_000,
  })

  const riskData = useMemo(
    () => RISK_LEVELS.map((level) => ({ level: RISK_META[level].label, count: stats.data?.by_risk_level[level] ?? 0, color: RISK_META[level].color })),
    [stats.data],
  )
  const typeData = useMemo(
    () => Object.entries(stats.data?.by_type ?? {}).map(([type, count]) => ({ name: titleCase(type), value: count })).filter((item) => item.value > 0),
    [stats.data],
  )
  const cameraData = useMemo(() => (stats.data?.by_camera ?? []).map((item) => ({ camera: item.camera_id, count: item.count })), [stats.data])
  const healthData = useMemo(
    () =>
      [...(history.data?.items ?? [])]
        .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
        .map((sample) => ({
          time: formatTime(sample.timestamp).slice(0, 5),
          cpu: sample.cpu_percent,
          ram: sample.ram_percent,
          gpu: sample.gpu_percent,
          fps: sample.avg_fps,
          alerts: sample.active_alerts,
          online: sample.cameras_online,
        })),
    [history.data],
  )
  const acknowledged = useMemo(() => falseAlarms.data?.items ?? [], [falseAlarms.data])
  const falseAlarmCount = acknowledged.filter((alert) => alert.false_alarm).length
  const falseAlarmRate = acknowledged.length ? Math.round((falseAlarmCount / acknowledged.length) * 100) : null
  const meanResponseMinutes = useMemo(() => {
    const durations = acknowledged
      .filter((alert) => alert.acknowledged_at)
      .map((alert) => (Date.parse(alert.acknowledged_at ?? '') - Date.parse(alert.timestamp)) / 60_000)
      .filter((minutes) => Number.isFinite(minutes) && minutes >= 0)
    return durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : null
  }, [acknowledged])

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="analytics-page">
      <PageHeader icon={<Activity className="size-4" />} title="Analytics" subtitle={`Today's alert distribution (${stats.data?.timezone ?? 'IST'}) and recorded system health`} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Alerts today" value={stats.data?.total_today ?? 0} loading={stats.isLoading} />
        <StatCard label="Critical today" value={stats.data?.critical_today ?? 0} tone={stats.data?.critical_today ? 'critical' : 'default'} loading={stats.isLoading} />
        <StatCard
          label="False alarm rate"
          value={falseAlarmRate === null ? '—' : `${falseAlarmRate}%`}
          sub={`${falseAlarmCount} of ${acknowledged.length} acknowledged`}
          loading={falseAlarms.isLoading}
        />
        <StatCard
          label="Mean time to acknowledge"
          value={meanResponseMinutes === null ? '—' : meanResponseMinutes < 60 ? `${meanResponseMinutes.toFixed(1)}m` : `${(meanResponseMinutes / 60).toFixed(1)}h`}
          sub="Across acknowledged alerts"
          loading={falseAlarms.isLoading}
        />
      </div>

      {stats.isError ? <ErrorState error={stats.error} onRetry={() => void stats.refetch()} /> : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="By risk level (today)" />
          <div className="h-64 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={riskData} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="level" tick={{ fontSize: 10 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                <Bar dataKey="count" name="Alerts" radius={[4, 4, 0, 0]}>
                  {riskData.map((entry) => (
                    <Cell key={entry.level} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card>
          <CardHeader title="By alert type (today)" />
          <div className="h-64 p-3">
            {typeData.length === 0 ? (
              <EmptyState title="No alerts today" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={typeData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85} paddingAngle={2}>
                    {typeData.map((entry, index) => (
                      <Cell key={entry.name} fill={TYPE_COLORS[index % TYPE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="By camera (today)" />
          <div className="h-64 p-3">
            {cameraData.length === 0 ? (
              <EmptyState title="No alerts today" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cameraData} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 10 }}>
                  <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="camera" tick={{ fontSize: 10 }} width={80} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Bar dataKey="count" name="Alerts" fill="#0891b2" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="System health history"
          subtitle={canViewHealthHistory ? `${history.data?.total ?? 0} samples recorded by the backend health monitor` : 'Available to administrators and regional heads'}
          actions={
            canViewHealthHistory ? (
              <Select value={String(hours)} onChange={(event) => setHours(Number(event.target.value))} className="h-8 w-32 text-xs" aria-label="History window">
                {HOURS_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    Last {value < 48 ? `${value}h` : `${value / 24}d`}
                  </option>
                ))}
              </Select>
            ) : null
          }
        />
        {!canViewHealthHistory ? (
          <EmptyState title="Restricted" description="System health history requires the admin or regional head role." />
        ) : history.isLoading ? (
          <Spinner className="py-16" />
        ) : history.isError ? (
          <ErrorState error={history.error} />
        ) : healthData.length === 0 ? (
          <EmptyState title="No samples in this window" />
        ) : (
          <div className="grid gap-4 p-3 xl:grid-cols-2">
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={healthData} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} minTickGap={24} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="cpu" name="CPU %" stroke="#0f172a" dot={false} strokeWidth={1.5} />
                  <Line type="monotone" dataKey="ram" name="RAM %" stroke="#0891b2" dot={false} strokeWidth={1.5} />
                  <Line type="monotone" dataKey="gpu" name="GPU %" stroke="#f97316" dot={false} strokeWidth={1.5} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={healthData} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke="#e5e7eb" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} minTickGap={24} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="fps" name="Avg FPS" stroke="#16a34a" dot={false} strokeWidth={1.5} />
                  <Line type="stepAfter" dataKey="alerts" name="Active alerts" stroke="#dc2626" dot={false} strokeWidth={1.5} />
                  <Line type="stepAfter" dataKey="online" name="Cameras online" stroke="#a855f7" dot={false} strokeWidth={1.5} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
