import { Crosshair } from 'lucide-react'
import { useMemo } from 'react'
import { TrackPlot } from '@/components/modals/TrackPlot'
import { Badge, EmptyState, ErrorState, Modal, Spinner } from '@/components/ui/primitives'
import { useCameraZones, useNow, useTrackHistory, useTrackTimeline } from '@/hooks/useData'
import { eventPositions, forecastZoneEntries, predictTrajectory, timelineToPoints } from '@/lib/analysis'
import { ZONE_META } from '@/lib/constants'
import { formatDateTime } from '@/lib/utils'

const HORIZONS = [1, 2, 3, 5, 8, 10]

export function PredictionModal({ trackId, cameraId, onClose }: { trackId: number; cameraId: string; onClose: () => void }) {
  const timeline = useTrackTimeline(trackId, cameraId)
  const history = useTrackHistory(trackId, cameraId)
  const zones = useCameraZones(cameraId)
  const now = useNow(1000)

  const points = useMemo(() => {
    const fromTimeline = timelineToPoints(timeline.data?.points ?? [])
    if (fromTimeline.length) return fromTimeline
    const event = history.data?.events[0]
    return event ? eventPositions(event) : []
  }, [timeline.data, history.data])

  const prediction = useMemo(() => predictTrajectory(points, HORIZONS), [points])
  const last = points[points.length - 1]
  const entries = useMemo(
    () => (prediction && last ? forecastZoneEntries(prediction, last, zones.data?.items ?? [], HORIZONS[HORIZONS.length - 1]) : []),
    [prediction, last, zones.data],
  )
  const active = history.data?.events.some((event) => event.is_active) ?? false
  const ageSeconds = last ? Math.max(0, (now - last.t) / 1000) : null

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      tone="hud"
      size="xl"
      icon={<Crosshair className="size-5" />}
      title={`PREDICTION VIEW · TRACK ${trackId}`}
      description={`Constant-velocity least-squares forecast over the last 6 s of positions on ${cameraId}, with widening 1σ uncertainty`}
    >
      {timeline.isLoading || history.isLoading ? (
        <Spinner className="py-16 text-slate-400" label="Fitting trajectory…" />
      ) : timeline.isError && history.isError ? (
        <ErrorState error={timeline.error} />
      ) : points.length === 0 ? (
        <EmptyState title="No positions recorded for this track" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_300px]" data-testid="prediction">
          <div className="overflow-hidden rounded-xl border border-cyan-400/15">
            <TrackPlot points={points} zones={zones.data?.items ?? []} prediction={prediction?.points} className="block aspect-video w-full" />
          </div>
          <div className="flex flex-col gap-3 text-xs text-slate-200">
            <div className="flex flex-wrap gap-1.5">
              <Badge className={active ? 'bg-green-400/10 text-green-300 ring-green-400/30' : 'bg-white/5 text-slate-300 ring-white/10'}>
                {active ? 'TRACK ACTIVE' : 'TRACK ENDED'}
              </Badge>
              {ageSeconds !== null ? (
                <Badge className="bg-white/5 text-slate-300 ring-white/10">last fix {ageSeconds < 120 ? `${Math.round(ageSeconds)}s ago` : formatDateTime(last?.t)}</Badge>
              ) : null}
            </div>

            {!prediction ? (
              <p className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-amber-200">
                At least 3 positions within the last 6 seconds of the track are needed for a forecast.
              </p>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-lg border border-cyan-400/15 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-400">Speed</p>
                    <p className="font-mono text-sm font-semibold">{prediction.speed.toFixed(1)} px/s</p>
                  </div>
                  <div className="rounded-lg border border-cyan-400/15 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-400">Heading</p>
                    <p className="font-mono text-sm font-semibold">
                      {prediction.speed < 2 ? 'stationary' : `${Math.round(((Math.atan2(-prediction.velocity.vy, prediction.velocity.vx) * 180) / Math.PI + 450) % 360)}°`}
                    </p>
                  </div>
                  <div className="rounded-lg border border-cyan-400/15 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-400">Fit residual</p>
                    <p className="font-mono text-sm font-semibold">±{prediction.residualStd.toFixed(1)} px</p>
                  </div>
                  <div className="rounded-lg border border-cyan-400/15 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-400">Samples</p>
                    <p className="font-mono text-sm font-semibold">{prediction.samples}</p>
                  </div>
                </div>

                <div className="overflow-hidden rounded-lg border border-cyan-400/15">
                  <table className="w-full font-mono">
                    <thead className="bg-white/5 text-[10px] uppercase tracking-wider text-slate-400">
                      <tr>
                        <th className="px-2 py-1.5 text-left">T+</th>
                        <th className="px-2 py-1.5 text-right">x</th>
                        <th className="px-2 py-1.5 text-right">y</th>
                        <th className="px-2 py-1.5 text-right">±σ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {prediction.points.map((point) => (
                        <tr key={point.secondsAhead} className="border-t border-white/5">
                          <td className="px-2 py-1">{point.secondsAhead}s</td>
                          <td className="px-2 py-1 text-right">{Math.round(point.x)}</td>
                          <td className="px-2 py-1 text-right">{Math.round(point.y)}</td>
                          <td className="px-2 py-1 text-right">{Math.round(point.uncertainty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div>
                  <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Zone entry forecast (10 s)</p>
                  {entries.length === 0 ? (
                    <p className="text-slate-400">{zones.data?.items.length ? 'Forecast path does not enter any active zone.' : 'No zones configured for this camera.'}</p>
                  ) : (
                    <ul className="flex flex-col gap-1.5">
                      {entries.map((entry) => (
                        <li key={entry.zone.zone_id} className="flex items-center justify-between rounded-lg bg-white/5 px-2.5 py-1.5">
                          <span className="flex items-center gap-2">
                            <span className="size-2 rounded-full" style={{ background: ZONE_META[entry.zone.zone_type].color }} />
                            {entry.zone.zone_name}
                          </span>
                          <span className="font-mono font-semibold" style={{ color: ZONE_META[entry.zone.zone_type].color }}>
                            {entry.alreadyInside ? 'INSIDE' : `ETA ${entry.secondsAhead.toFixed(1)}s`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {!active ? <p className="text-[10px] text-slate-500">The track has ended; the forecast extrapolates from its final positions.</p> : null}
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
