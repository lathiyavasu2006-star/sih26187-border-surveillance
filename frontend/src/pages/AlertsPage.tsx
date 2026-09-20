import { Bell, ChevronLeft, ChevronRight, Gauge, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { AlertDetail } from '@/components/alerts/AlertDetail'
import { AlertPanel } from '@/components/alerts/AlertPanel'
import { Button, Card, CardHeader, EmptyState, ErrorState, Input, PageHeader, Select, Spinner } from '@/components/ui/primitives'
import { useAcknowledgeAlert, useAlert, useAlerts, useAlertStats } from '@/hooks/useAlerts'
import { useCameras, useNow } from '@/hooks/useData'
import { useKeyboard } from '@/hooks/useKeyboard'
import { ALERT_TYPES, RISK_LEVELS, RISK_META } from '@/lib/constants'
import { titleCase } from '@/lib/utils'
import { useUiStore } from '@/stores/uiStore'
import type { AlertFilters, AlertType, RiskLevel } from '@/types'

const PAGE_SIZE = 50

/** datetime-local value (browser local time) → ISO string with timezone. */
function toIso(value: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

export function AlertsPage() {
  const [params, setParams] = useSearchParams()
  const now = useNow(10_000)
  const cameras = useCameras()
  const stats = useAlertStats()
  const acknowledge = useAcknowledgeAlert()
  const openModal = useUiStore((state) => state.openModal)

  const [page, setPage] = useState(0)
  // Every filter change returns to the first page.
  const withReset = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setPage(0)
  }
  const [cameraId, setCameraIdRaw] = useState('')
  const [alertType, setAlertTypeRaw] = useState('')
  const [riskLevel, setRiskLevelRaw] = useState('')
  const [ackState, setAckStateRaw] = useState<'all' | 'open' | 'acknowledged'>('open')
  const [dateFrom, setDateFromRaw] = useState('')
  const [dateTo, setDateToRaw] = useState('')
  const setCameraId = withReset(setCameraIdRaw)
  const setAlertType = withReset(setAlertTypeRaw)
  const setRiskLevel = withReset(setRiskLevelRaw)
  const setAckState = withReset(setAckStateRaw)
  const setDateFrom = withReset(setDateFromRaw)
  const setDateTo = withReset(setDateToRaw)

  const filters: AlertFilters = useMemo(
    () => ({
      camera_id: cameraId || undefined,
      alert_type: (alertType || undefined) as AlertType | undefined,
      risk_level: (riskLevel || undefined) as RiskLevel | undefined,
      acknowledged: ackState === 'all' ? undefined : ackState === 'acknowledged',
      date_from: toIso(dateFrom),
      date_to: toIso(dateTo),
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [cameraId, alertType, riskLevel, ackState, dateFrom, dateTo, page],
  )

  const list = useAlerts(filters)
  const items = list.data?.items ?? []
  const selectedId = params.get('alert')
  const selectedFromList = items.find((alert) => alert.alert_id === selectedId)
  const selectedQuery = useAlert(selectedFromList ? null : selectedId)
  const selected = selectedFromList ?? selectedQuery.data ?? null

  const select = (alertId: string | null) => {
    const next = new URLSearchParams(params)
    if (alertId) next.set('alert', alertId)
    else next.delete('alert')
    setParams(next, { replace: true })
  }

  // Select the first alert once results arrive (only when nothing is selected).
  const firstId = items[0]?.alert_id
  useEffect(() => {
    if (selectedId || !firstId) return
    const next = new URLSearchParams(params)
    next.set('alert', firstId)
    setParams(next, { replace: true })
  }, [firstId, selectedId, params, setParams])

  const move = (direction: 1 | -1) => {
    if (!items.length) return
    const index = items.findIndex((alert) => alert.alert_id === selectedId)
    const next = items[Math.max(0, Math.min(items.length - 1, index + direction))]
    if (next) {
      select(next.alert_id)
      document.querySelector(`[data-testid="alert-row-${CSS.escape(next.alert_id)}"]`)?.scrollIntoView({ block: 'nearest' })
    }
  }

  useKeyboard({
    j: () => move(1),
    k: () => move(-1),
    a: () => {
      // Latest alert: the newest one loaded, whatever its acknowledgement state.
      const latest = [...items].sort((x, y) => Date.parse(y.timestamp) - Date.parse(x.timestamp))[0]
      if (!latest) return
      select(latest.alert_id)
      document.querySelector(`[data-testid="alert-row-${CSS.escape(latest.alert_id)}"]`)?.scrollIntoView({ block: 'nearest' })
    },
    enter: {
      // Enter on a focused button or link keeps its normal meaning.
      allowDefault: true,
      handler: (event) => {
        const target = event.target
        if (target instanceof HTMLButtonElement || target instanceof HTMLAnchorElement || (target instanceof HTMLElement && target.getAttribute('role') === 'tab')) return
        event.preventDefault()
        if (!selected) return
        if (selected.acknowledged) toast(`${selected.alert_id} is already acknowledged`, { id: 'ack' })
        else acknowledge.mutate({ alertId: selected.alert_id, falseAlarm: false })
      },
    },
    e: () => {
      if (selected?.track_id !== null && selected) openModal({ kind: 'eventDna', trackId: selected.track_id, cameraId: selected.camera_id })
    },
    c: () => {
      if (selected?.track_id !== null && selected) {
        openModal({ kind: 'correlation', trackId: selected.track_id, cameraId: selected.camera_id, personUuid: selected.person_uuid })
      }
    },
    p: () => {
      if (selected?.track_id !== null && selected) openModal({ kind: 'prediction', trackId: selected.track_id, cameraId: selected.camera_id })
    },
    u: () => {
      if (selected) openModal({ kind: 'fusion', cameraId: selected.camera_id, detection: null, alert: selected })
    },
  })

  const total = list.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const resetFilters = () => {
    setCameraId('')
    setAlertType('')
    setRiskLevel('')
    setAckState('open')
    setDateFrom('')
    setDateTo('')
  }

  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="alerts-page">
      <PageHeader
        icon={<Bell className="size-4" />}
        title="Alerts"
        subtitle={
          stats.data
            ? `${stats.data.total_today} today · ${stats.data.critical_today} critical · ${stats.data.unacknowledged} unacknowledged · A latest · J/K navigate · Enter acknowledge · U fusion`
            : undefined
        }
      />
      <Card className="flex flex-wrap items-end gap-2 p-3">
        <Select value={cameraId} onChange={(event) => setCameraId(event.target.value)} className="h-8 w-36 text-xs" aria-label="Camera">
          <option value="">All cameras</option>
          {(cameras.data?.items ?? []).map((camera) => (
            <option key={camera.camera_id} value={camera.camera_id}>
              {camera.camera_id}
            </option>
          ))}
        </Select>
        <Select value={alertType} onChange={(event) => setAlertType(event.target.value)} className="h-8 w-36 text-xs" aria-label="Alert type">
          <option value="">All types</option>
          {ALERT_TYPES.map((type) => (
            <option key={type} value={type}>
              {titleCase(type)}
            </option>
          ))}
        </Select>
        <Select value={riskLevel} onChange={(event) => setRiskLevel(event.target.value)} className="h-8 w-36 text-xs" aria-label="Risk level">
          <option value="">All risk levels</option>
          {RISK_LEVELS.map((level) => (
            <option key={level} value={level}>
              {RISK_META[level].label}
            </option>
          ))}
        </Select>
        <Select value={ackState} onChange={(event) => setAckState(event.target.value as typeof ackState)} className="h-8 w-36 text-xs" aria-label="Acknowledgement">
          <option value="open">Unacknowledged</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="all">All</option>
        </Select>
        <Input type="datetime-local" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="h-8 w-48 text-xs" aria-label="From" />
        <Input type="datetime-local" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="h-8 w-48 text-xs" aria-label="To" />
        <Button size="sm" icon={<RotateCcw className="size-3.5" />} onClick={resetFilters}>
          Reset
        </Button>
      </Card>

      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(0,1fr)_460px]">
        <Card className="flex min-h-0 flex-col">
          <CardHeader
            title={`${total} alert${total === 1 ? '' : 's'}`}
            subtitle={list.isFetching ? 'Refreshing…' : `Page ${page + 1} of ${pages}`}
            actions={
              <div className="flex gap-1">
                <Button size="xs" icon={<ChevronLeft className="size-3" />} disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page" />
                <Button size="xs" icon={<ChevronRight className="size-3" />} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page" />
              </div>
            }
          />
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
            {list.isError ? (
              <ErrorState error={list.error} onRetry={() => void list.refetch()} />
            ) : (
              <AlertPanel alerts={items} loading={list.isLoading} selectedId={selectedId} onSelect={(alert) => select(alert.alert_id)} now={now} emptyTitle="No alerts match these filters" />
            )}
          </div>
        </Card>
        <Card className="scrollbar-thin min-h-0 overflow-y-auto p-4">
          {selected ? (
            <>
              <AlertDetail key={selected.alert_id} alert={selected} onDeleted={() => select(null)} />
              <Button size="xs" variant="hud" className="mt-3" icon={<Gauge className="size-3.5" />} onClick={() => openModal({ kind: 'fusion', cameraId: selected.camera_id, detection: null, alert: selected })}>
                Confidence fusion <kbd className="kbd">U</kbd>
              </Button>
            </>
          ) : selectedQuery.isLoading ? (
            <Spinner className="py-16" />
          ) : selectedQuery.isError ? (
            <ErrorState error={selectedQuery.error} />
          ) : (
            <EmptyState icon={<Bell className="size-8" />} title="Select an alert" description="Choose an alert from the list to review its snapshot, risk breakdown and acknowledge it." />
          )}
        </Card>
      </div>
    </div>
  )
}
