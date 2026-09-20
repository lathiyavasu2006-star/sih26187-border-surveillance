import { useQuery } from '@tanstack/react-query'
import { Network } from 'lucide-react'
import { useMemo } from 'react'
import { eventsApi } from '@/api/endpoints'
import { Badge, EmptyState, ErrorState, Modal, Spinner } from '@/components/ui/primitives'
import { useTrackHistory } from '@/hooks/useData'
import { correlateTracks } from '@/lib/analysis'
import { RISK_META } from '@/lib/constants'
import { formatDuration, formatTime, riskLevelForScore, titleCase } from '@/lib/utils'
import { useUiStore } from '@/stores/uiStore'
import type { TrackEvent } from '@/types'

const WINDOW_MINUTES = 30
const MAX_GAP_SECONDS = 300

export function CorrelationModal({
  trackId,
  cameraId,
  onClose,
}: {
  trackId: number
  cameraId: string
  personUuid: string | null
  onClose: () => void
}) {
  const openModal = useUiStore((state) => state.openModal)
  const history = useTrackHistory(trackId, cameraId)
  const reference: TrackEvent | undefined = history.data?.events[0]

  const timeWindow = useMemo(() => {
    if (!reference) return null
    const from = new Date(Date.parse(reference.first_seen) - WINDOW_MINUTES * 60_000).toISOString()
    const to = new Date(Date.parse(reference.last_seen) + WINDOW_MINUTES * 60_000).toISOString()
    return { from, to }
  }, [reference])

  const nearby = useQuery({
    queryKey: ['events', 'correlation-window', timeWindow?.from, timeWindow?.to],
    queryFn: () => eventsApi.list({ date_from: timeWindow?.from, date_to: timeWindow?.to, limit: 1000 }),
    enabled: Boolean(timeWindow),
  })

  const candidates = useMemo(
    () => (reference && nearby.data ? correlateTracks(reference, nearby.data.items, MAX_GAP_SECONDS) : []),
    [reference, nearby.data],
  )
  const cameras = useMemo(() => {
    const set = new Set<string>()
    if (reference) set.add(reference.camera_id)
    candidates.forEach((candidate) => set.add(candidate.event.camera_id))
    return [...set].sort()
  }, [reference, candidates])

  const range = useMemo(() => {
    const all = reference ? [reference, ...candidates.map((candidate) => candidate.event)] : []
    const start = Math.min(...all.map((event) => Date.parse(event.first_seen)))
    const end = Math.max(...all.map((event) => Date.parse(event.last_seen)))
    return { start, end: Math.max(end, start + 1000) }
  }, [reference, candidates])

  const bar = (event: TrackEvent) => {
    const left = ((Date.parse(event.first_seen) - range.start) / (range.end - range.start)) * 100
    const width = Math.max(0.8, ((Date.parse(event.last_seen) - Date.parse(event.first_seen)) / (range.end - range.start)) * 100)
    return { left: `${left}%`, width: `${Math.min(100 - left, width)}%` }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      tone="hud"
      size="xl"
      icon={<Network className="size-5" />}
      title={`CROSS-CAMERA CORRELATION · TRACK ${trackId}`}
      description={`Tracks within ±${WINDOW_MINUTES} min of track ${trackId} on ${cameraId}; re-identification UUID matches are definitive, handovers ranked by time gap (≤${MAX_GAP_SECONDS}s) and class`}
    >
      {history.isLoading || nearby.isLoading ? (
        <Spinner className="py-16 text-slate-400" label="Correlating tracks across cameras…" />
      ) : history.isError ? (
        <ErrorState error={history.error} />
      ) : nearby.isError ? (
        <ErrorState error={nearby.error} onRetry={() => void nearby.refetch()} />
      ) : !reference ? (
        <EmptyState title="No recorded events for this track" />
      ) : (
        <div className="flex flex-col gap-4" data-testid="correlation">
          <div className="rounded-xl border border-cyan-400/15 p-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Swimlanes · {formatTime(range.start)} – {formatTime(range.end)} IST
            </p>
            <div className="flex flex-col gap-2">
              {cameras.map((camera) => (
                <div key={camera} className="grid grid-cols-[96px_1fr] items-center gap-3">
                  <span className="font-mono text-[11px] text-slate-300">{camera}</span>
                  <div className="relative h-6 rounded bg-white/5">
                    {[reference, ...candidates.map((candidate) => candidate.event)]
                      .filter((event) => event.camera_id === camera)
                      .map((event) => {
                        const isReference = event.event_id === reference.event_id
                        return (
                          <span
                            key={event.event_id}
                            className="absolute top-1 h-4 rounded"
                            style={{
                              ...bar(event),
                              background: isReference ? '#22d3ee' : RISK_META[riskLevelForScore(event.max_risk_score)].color,
                              opacity: isReference ? 1 : 0.75,
                            }}
                            title={`Track ${event.track_id} · ${formatTime(event.first_seen)}–${formatTime(event.last_seen)}`}
                          />
                        )
                      })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {candidates.length === 0 ? (
            <EmptyState
              title="No correlated tracks"
              description={
                cameras.length <= 1
                  ? `No other camera recorded a track within ${MAX_GAP_SECONDS / 60} minutes of this sighting, and no track shares its re-identification UUID.`
                  : 'No candidate satisfied the correlation rules.'
              }
            />
          ) : (
            <div className="overflow-hidden rounded-xl border border-cyan-400/15">
              <table className="w-full text-left text-xs text-slate-200">
                <thead className="bg-white/5 text-[10px] uppercase tracking-wider text-slate-400">
                  <tr>
                    <th className="px-3 py-2">Match</th>
                    <th className="px-3 py-2">Camera · track</th>
                    <th className="px-3 py-2">Class</th>
                    <th className="px-3 py-2">Seen</th>
                    <th className="px-3 py-2">Gap</th>
                    <th className="px-3 py-2">Basis</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {candidates.slice(0, 50).map((candidate) => (
                    <tr key={candidate.event.event_id} className="border-t border-white/5">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/10">
                            <div className="h-full rounded-full bg-cyan-400" style={{ width: `${Math.round(candidate.score * 100)}%` }} />
                          </div>
                          <span className="font-mono">{Math.round(candidate.score * 100)}%</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {candidate.event.camera_id} · {candidate.event.track_id}
                      </td>
                      <td className="px-3 py-2">{titleCase(candidate.event.object_class)}</td>
                      <td className="px-3 py-2 font-mono">
                        {formatTime(candidate.event.first_seen)}–{formatTime(candidate.event.last_seen)}
                      </td>
                      <td className="px-3 py-2 font-mono">{formatDuration(candidate.gapSeconds)}</td>
                      <td className="px-3 py-2">
                        <Badge className="bg-white/5 text-slate-200 ring-white/10">{candidate.basis.replaceAll('_', ' ')}</Badge>
                        <p className="mt-0.5 text-[10px] text-slate-400">{candidate.reasons.join(' · ')}</p>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          className="text-[11px] font-semibold text-cyan-300 hover:underline"
                          onClick={() => openModal({ kind: 'eventDna', trackId: candidate.event.track_id, cameraId: candidate.event.camera_id })}
                        >
                          DNA →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
