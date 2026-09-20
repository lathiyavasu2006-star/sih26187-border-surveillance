import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Crosshair, Fingerprint, Network, Route } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { eventsApi } from '@/api/endpoints'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Input, PageHeader, RiskBadge, Select, Spinner } from '@/components/ui/primitives'
import { useCameras } from '@/hooks/useData'
import { POLL } from '@/lib/constants'
import { formatDateTime, formatDuration, riskLevelForScore, titleCase } from '@/lib/utils'
import { useUiStore } from '@/stores/uiStore'
import type { EventFilters } from '@/types'

const PAGE_SIZE = 50

export function EventsPage() {
  const [params] = useSearchParams()
  const cameras = useCameras()
  const openModal = useUiStore((state) => state.openModal)
  const [page, setPage] = useState(0)
  // Every filter change returns to the first page.
  const withReset = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setPage(0)
  }
  const [cameraId, setCameraIdRaw] = useState('')
  const [objectClass, setObjectClassRaw] = useState('')
  const [activeOnly, setActiveOnlyRaw] = useState<'all' | 'active' | 'ended'>('all')
  const setCameraId = withReset(setCameraIdRaw)
  const setObjectClass = withReset(setObjectClassRaw)
  const setActiveOnly = withReset(setActiveOnlyRaw)
  const [trackSearch, setTrackSearch] = useState(params.get('track') ?? '')
  // A new ?track= from global search replaces the field (state adjusted during render, not in an effect).
  const trackParam = params.get('track')
  const [seenTrackParam, setSeenTrackParam] = useState(trackParam)
  if (trackParam !== seenTrackParam) {
    setSeenTrackParam(trackParam)
    if (trackParam) setTrackSearch(trackParam)
  }

  const filters: EventFilters = useMemo(
    () => ({
      camera_id: cameraId || undefined,
      object_class: objectClass || undefined,
      is_active: activeOnly === 'all' ? undefined : activeOnly === 'active',
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [cameraId, objectClass, activeOnly, page],
  )

  const trackId = /^\d{1,9}$/.test(trackSearch.trim()) ? Number(trackSearch.trim()) : null
  const events = useQuery({
    queryKey: ['events', 'list', filters],
    queryFn: () => eventsApi.list(filters),
    refetchInterval: POLL.events,
    placeholderData: (previous) => previous,
    enabled: trackId === null,
  })
  const trackHistory = useQuery({
    queryKey: ['events', 'history', trackId, cameraId || 'all'],
    queryFn: () => eventsApi.history(trackId ?? 0, cameraId || undefined),
    enabled: trackId !== null,
    retry: false,
  })

  const rows = trackId !== null ? (trackHistory.data?.events ?? []) : (events.data?.items ?? [])
  const loading = trackId !== null ? trackHistory.isLoading : events.isLoading
  const error = trackId !== null ? trackHistory.error : events.error
  const total = trackId !== null ? (trackHistory.data?.total_events ?? 0) : (events.data?.total ?? 0)
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="events-page">
      <PageHeader icon={<Route className="size-4" />} title="Events & Tracks" subtitle="Every tracked object recorded by the ML pipeline, with dwell time, zone history and alert counts" />
      <Card className="flex flex-wrap items-end gap-2 p-3">
        <Input value={trackSearch} onChange={(event) => setTrackSearch(event.target.value)} placeholder="Track ID" className="h-8 w-32 font-mono text-xs" aria-label="Track ID" inputMode="numeric" />
        <Select value={cameraId} onChange={(event) => setCameraId(event.target.value)} className="h-8 w-36 text-xs" aria-label="Camera">
          <option value="">All cameras</option>
          {(cameras.data?.items ?? []).map((camera) => (
            <option key={camera.camera_id} value={camera.camera_id}>
              {camera.camera_id}
            </option>
          ))}
        </Select>
        <Select value={objectClass} onChange={(event) => setObjectClass(event.target.value)} className="h-8 w-36 text-xs" aria-label="Object class" disabled={trackId !== null}>
          <option value="">All classes</option>
          {['person', 'car', 'truck', 'motorcycle', 'bicycle', 'bus', 'dog', 'cow', 'horse'].map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </Select>
        <Select value={activeOnly} onChange={(event) => setActiveOnly(event.target.value as typeof activeOnly)} className="h-8 w-32 text-xs" aria-label="Active state" disabled={trackId !== null}>
          <option value="all">All tracks</option>
          <option value="active">Active</option>
          <option value="ended">Ended</option>
        </Select>
        {trackId !== null && trackHistory.data ? (
          <div className="ml-auto flex items-center gap-2 text-xs">
            <Badge>{formatDuration(trackHistory.data.total_time_seconds)} total</Badge>
            <Badge>{trackHistory.data.alert_count} alerts</Badge>
            <RiskBadge level={riskLevelForScore(trackHistory.data.max_risk_score)} score={trackHistory.data.max_risk_score} />
          </div>
        ) : null}
      </Card>

      <Card>
        <CardHeader
          title={trackId !== null ? `Track ${trackId}` : `${total} events`}
          subtitle={trackId !== null ? trackHistory.data?.cameras.join(', ') : `Page ${page + 1} of ${pages}`}
          actions={
            trackId === null ? (
              <div className="flex gap-1">
                <Button size="xs" icon={<ChevronLeft className="size-3" />} disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page" />
                <Button size="xs" icon={<ChevronRight className="size-3" />} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page" />
              </div>
            ) : null
          }
        />
        {loading ? (
          <Spinner className="py-16" />
        ) : error ? (
          <ErrorState error={error} />
        ) : rows.length === 0 ? (
          <EmptyState icon={<Route className="size-8" />} title="No events" description="Tracks appear once the ML pipeline has followed an object for a few frames." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-line bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="px-4 py-2">Track</th>
                  <th className="px-4 py-2">Camera</th>
                  <th className="px-4 py-2">Class</th>
                  <th className="px-4 py-2">First seen</th>
                  <th className="px-4 py-2">Last seen</th>
                  <th className="px-4 py-2">Dwell</th>
                  <th className="px-4 py-2">Positions</th>
                  <th className="px-4 py-2">Zones</th>
                  <th className="px-4 py-2">Alerts</th>
                  <th className="px-4 py-2">Max risk</th>
                  <th className="px-4 py-2 text-right">Analysis</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.event_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-2 font-mono font-semibold text-cyan-700">
                      {event.track_id}
                      {event.is_active ? <span className="ml-1.5 inline-block size-1.5 animate-pulse rounded-full bg-green-500" aria-label="active" /> : null}
                    </td>
                    <td className="px-4 py-2 font-mono">{event.camera_id}</td>
                    <td className="px-4 py-2">{titleCase(event.object_class)}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(event.first_seen)}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(event.last_seen)}</td>
                    <td className="px-4 py-2 font-mono">{formatDuration(event.total_time_seconds)}</td>
                    <td className="px-4 py-2 font-mono">{event.positions.length}</td>
                    <td className="px-4 py-2">{event.zone_history.length}</td>
                    <td className="px-4 py-2 font-mono">{event.alert_count}</td>
                    <td className="px-4 py-2">
                      <RiskBadge level={riskLevelForScore(event.max_risk_score)} score={event.max_risk_score} />
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex justify-end gap-1">
                        <Button size="xs" variant="hud" icon={<Fingerprint className="size-3" />} onClick={() => openModal({ kind: 'eventDna', trackId: event.track_id, cameraId: event.camera_id })} aria-label="Event DNA" />
                        <Button
                          size="xs"
                          variant="hud"
                          icon={<Network className="size-3" />}
                          onClick={() => openModal({ kind: 'correlation', trackId: event.track_id, cameraId: event.camera_id, personUuid: event.person_uuid })}
                          aria-label="Correlate"
                        />
                        <Button size="xs" variant="hud" icon={<Crosshair className="size-3" />} onClick={() => openModal({ kind: 'prediction', trackId: event.track_id, cameraId: event.camera_id })} aria-label="Predict" />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
