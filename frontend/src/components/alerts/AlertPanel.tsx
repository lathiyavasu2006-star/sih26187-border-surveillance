import { CheckCircle2, ShieldAlert } from 'lucide-react'
import { EmptyState, RiskBadge, Spinner } from '@/components/ui/primitives'
import { RISK_META } from '@/lib/constants'
import { cn, formatRelative, formatTime, titleCase } from '@/lib/utils'
import type { Alert } from '@/types'

export function AlertRow({
  alert,
  selected,
  onSelect,
  now,
  dense = false,
}: {
  alert: Alert
  selected: boolean
  onSelect: (alert: Alert) => void
  now: number
  dense?: boolean
}) {
  const meta = RISK_META[alert.risk_level]
  return (
    <button
      type="button"
      onClick={() => onSelect(alert)}
      className={cn(
        'relative flex w-full gap-3 border-b border-slate-100 text-left transition-colors',
        dense ? 'px-3 py-2' : 'px-4 py-3',
        selected ? 'bg-cyan-50' : 'hover:bg-slate-50',
        alert.acknowledged && 'opacity-60',
      )}
      data-testid={`alert-row-${alert.alert_id}`}
      aria-current={selected}
    >
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: meta.color }} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className={cn('truncate font-semibold text-slate-900', dense ? 'text-xs' : 'text-sm')}>{titleCase(alert.alert_type)}</p>
          {alert.acknowledged ? (
            <CheckCircle2 className="size-3.5 shrink-0 text-green-600" aria-label="Acknowledged" />
          ) : alert.risk_level === 'critical' ? (
            <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-red-600" aria-label="Unacknowledged critical" />
          ) : null}
        </div>
        <p className="mt-0.5 truncate font-mono text-[10px] text-muted">
          {alert.camera_id}
          {alert.track_id !== null ? ` · ID ${alert.track_id}` : ''}
          {alert.zone_name ? ` · ${alert.zone_name}` : ''}
        </p>
        {!dense && alert.risk_reasons.length > 0 ? (
          <p className="mt-1 line-clamp-1 text-[11px] text-slate-500">{alert.risk_reasons.join(' · ')}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <RiskBadge level={alert.risk_level} score={alert.risk_score} />
        <span className="text-[10px] text-slate-400" title={formatTime(alert.timestamp)}>
          {formatRelative(alert.timestamp, now)}
        </span>
      </div>
    </button>
  )
}

export function AlertPanel({
  alerts,
  loading,
  selectedId,
  onSelect,
  now,
  dense,
  emptyTitle = 'No alerts',
}: {
  alerts: Alert[]
  loading?: boolean
  selectedId: string | null
  onSelect: (alert: Alert) => void
  now: number
  dense?: boolean
  emptyTitle?: string
}) {
  if (loading && alerts.length === 0) return <Spinner className="py-10" label="Loading alerts…" />
  if (alerts.length === 0) {
    return <EmptyState icon={<ShieldAlert className="size-8" />} title={emptyTitle} description="Alerts raised by the ML pipeline appear here in real time." />
  }
  return (
    <div role="list">
      {alerts.map((alert) => (
        <div role="listitem" key={alert.alert_id}>
          <AlertRow alert={alert} selected={alert.alert_id === selectedId} onSelect={onSelect} now={now} dense={dense} />
        </div>
      ))}
    </div>
  )
}
