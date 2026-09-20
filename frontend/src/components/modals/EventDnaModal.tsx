import { Camera, Clock, Compass as CompassIcon, Fingerprint, MapPin, Timer } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { TrackPlot } from '@/components/modals/TrackPlot'
import { Badge, EmptyState, ErrorState, Modal, RiskBadge, Spinner } from '@/components/ui/primitives'
import { useCameraZones, useCameras, useTrackHistory, useTrackTimeline } from '@/hooks/useData'
import { describeDirection, directionHistogram, eventPositions, pathMetrics, timelineToPoints, type TrackPoint } from '@/lib/analysis'
import { RISK_META } from '@/lib/constants'
import { formatDateTime, formatDuration, riskLevelForScore, titleCase } from '@/lib/utils'
import type { Direction, RiskLevel, TimelinePoint } from '@/types'

const HEADINGS: Direction[] = ['north', 'northeast', 'east', 'southeast', 'south', 'southwest', 'west', 'northwest']

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-cyan-400/15 bg-white/[0.03] px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</p>
      <p className="mt-0.5 font-mono text-sm font-semibold text-slate-100">{value}</p>
    </div>
  )
}

/** DNA strip: one bar per sample, colour = risk level, height = risk score. */
function DnaStrip({ samples }: { samples: readonly TimelinePoint[] }) {
  const visible = samples.slice(-160)
  return (
    <div className="flex h-16 items-end gap-px overflow-hidden rounded-lg border border-cyan-400/15 bg-black/30 p-2" aria-label="Risk sequence">
      {visible.map((sample, index) => {
        const level = (sample.risk_level as RiskLevel | null) ?? riskLevelForScore(sample.risk_score)
        const color = RISK_META[level]?.color ?? '#64748b'
        return (
          <span
            key={`${sample.timestamp}-${index}`}
            className="min-w-[2px] flex-1 rounded-sm"
            style={{ height: `${Math.max(8, sample.risk_score)}%`, background: color }}
            title={`${formatDateTime(sample.timestamp)} · risk ${sample.risk_score}${sample.zone_name ? ` · ${sample.zone_name}` : ''}`}
          />
        )
      })}
    </div>
  )
}

function Compass({ histogram }: { histogram: Record<Direction, number> }) {
  const total = HEADINGS.reduce((sum, heading) => sum + histogram[heading], 0)
  const max = Math.max(1, ...HEADINGS.map((heading) => histogram[heading]))
  return (
    <svg viewBox="-60 -60 120 120" className="size-40" role="img" aria-label="Movement heading distribution">
      <circle r={50} fill="none" stroke="rgba(34,211,238,0.2)" />
      <circle r={25} fill="none" stroke="rgba(34,211,238,0.1)" />
      {HEADINGS.map((heading, index) => {
        const angle = (index * 45 - 90) * (Math.PI / 180)
        const length = 8 + (histogram[heading] / max) * 40
        return (
          <g key={heading}>
            <line x1={0} y1={0} x2={Math.cos(angle) * length} y2={Math.sin(angle) * length} stroke="#22d3ee" strokeWidth={6} strokeLinecap="round" opacity={histogram[heading] ? 0.9 : 0.15} />
            <text x={Math.cos(angle) * 57} y={Math.sin(angle) * 57 + 3} fill="#94a3b8" fontSize={8} textAnchor="middle" fontFamily="monospace">
              {heading.replace('north', 'N').replace('south', 'S').replace('east', 'E').replace('west', 'W').toUpperCase()}
            </text>
          </g>
        )
      })}
      <text y={3} textAnchor="middle" fill="#e2e8f0" fontSize={9} fontFamily="monospace">
        {total ? `${Math.round((histogram.stationary / (total + histogram.stationary)) * 100)}% idle` : 'no motion'}
      </text>
    </svg>
  )
}


const DNA_NAVY = '#0a1628'
const DOT_GRID = 'radial-gradient(rgba(34, 211, 238, 0.14) 1px, transparent 1px)'

interface DnaNode {
  key: string
  label: string
  value: string
  detail?: string
  icon: ReactNode
  /** Position of the card centre in percent of the canvas. */
  x: number
  y: number
}

/** Radial summary: six facts about the track connected to a central EVENT DNA block by cyan lines. */
function DnaMap({ nodes }: { nodes: DnaNode[] }) {
  return (
    <div className="relative h-[380px] w-full overflow-hidden rounded-xl border border-cyan-400/15" style={{ backgroundImage: DOT_GRID, backgroundSize: '18px 18px' }} data-testid="event-dna-map">
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
        {nodes.map((node) => (
          <g key={node.key}>
            <line x1={50} y1={50} x2={node.x} y2={node.y} stroke="#22d3ee" strokeWidth={0.35} strokeOpacity={0.75} vectorEffect="non-scaling-stroke" />
            <circle cx={node.x} cy={node.y} r={0.6} fill="#22d3ee" />
          </g>
        ))}
      </svg>
      <div className="absolute left-1/2 top-1/2 flex h-24 w-44 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-md bg-white text-slate-900 shadow-[0_0_40px_rgba(34,211,238,0.35)]">
        <Fingerprint className="size-5 text-cyan-700" aria-hidden />
        <p className="mt-1 font-mono text-lg font-black tracking-[0.25em]">EVENT DNA</p>
      </div>
      {nodes.map((node) => (
        <div
          key={node.key}
          className="absolute w-52 -translate-x-1/2 -translate-y-1/2 rounded-lg border border-cyan-400/40 bg-[#0d1f38]/95 px-3 py-2.5 shadow-lg"
          style={{ left: `${node.x}%`, top: `${node.y}%` }}
          data-testid={`dna-${node.key}`}
        >
          <p className="flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-hud">
            {node.icon}
            {node.label}
          </p>
          <p className="mt-1 truncate text-sm font-semibold text-white" title={node.value}>
            {node.value}
          </p>
          {node.detail ? (
            <p className="truncate text-[11px] text-slate-400" title={node.detail}>
              {node.detail}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  )
}

export function EventDnaModal({ trackId, cameraId, onClose }: { trackId: number; cameraId: string; onClose: () => void }) {
  const history = useTrackHistory(trackId, cameraId)
  const timeline = useTrackTimeline(trackId, cameraId)
  const zones = useCameraZones(cameraId)
  const cameras = useCameras()

  const latestEvent = history.data?.events[0]
  const points: TrackPoint[] = useMemo(() => {
    const fromTimeline = timelineToPoints(timeline.data?.points ?? [])
    if (fromTimeline.length) return fromTimeline
    return latestEvent ? eventPositions(latestEvent) : []
  }, [timeline.data, latestEvent])
  const metrics = useMemo(() => pathMetrics(points), [points])
  const histogram = useMemo(() => directionHistogram(points), [points])
  const zoneSequence = useMemo(() => {
    const sequence: string[] = []
    for (const point of timeline.data?.points ?? []) {
      const label = point.zone_name ?? 'outside zones'
      if (sequence[sequence.length - 1] !== label) sequence.push(label)
    }
    return sequence
  }, [timeline.data])

  const camera = cameras.data?.items.find((item) => item.camera_id === cameraId)
  const lastZone = [...(timeline.data?.points ?? [])].reverse().find((point) => point.zone_name)?.zone_name ?? null
  const nodes: DnaNode[] = history.data
    ? [
        {
          key: 'who',
          label: 'Who',
          value: `Track-ID ${trackId}`,
          detail: [latestEvent ? titleCase(latestEvent.object_class) : null, latestEvent?.person_uuid ? `UUID ${latestEvent.person_uuid.slice(0, 8)}` : null].filter(Boolean).join(' · '),
          icon: <Fingerprint className="size-3" />,
          x: 16,
          y: 17,
        },
        {
          key: 'where',
          label: 'Where',
          value: lastZone ?? camera?.location_name ?? cameraId,
          detail: [camera?.location_name && lastZone ? camera.location_name : null, camera?.sector_name ?? null, cameraId].filter(Boolean).join(' · '),
          icon: <MapPin className="size-3" />,
          x: 84,
          y: 17,
        },
        {
          key: 'when',
          label: 'When',
          value: latestEvent ? formatDateTime(latestEvent.first_seen) : '—',
          detail: latestEvent ? `until ${formatDateTime(latestEvent.last_seen)}` : undefined,
          icon: <Clock className="size-3" />,
          x: 10,
          y: 50,
        },
        {
          key: 'direction',
          label: 'Direction',
          value: describeDirection(points),
          detail: `${Math.round(metrics.netDisplacementPx)} px net · ${Math.round(metrics.straightness * 100)}% straight`,
          icon: <CompassIcon className="size-3" />,
          x: 90,
          y: 50,
        },
        {
          key: 'duration',
          label: 'Duration',
          value: formatDuration(history.data.total_time_seconds),
          detail: `${history.data.total_events} event${history.data.total_events === 1 ? '' : 's'} · ${history.data.alert_count} alert${history.data.alert_count === 1 ? '' : 's'}`,
          icon: <Timer className="size-3" />,
          x: 16,
          y: 83,
        },
        {
          key: 'cameras',
          label: 'Cameras',
          value: history.data.cameras.join(', ') || cameraId,
          detail: `${history.data.cameras.length} camera${history.data.cameras.length === 1 ? '' : 's'} saw this track`,
          icon: <Camera className="size-3" />,
          x: 84,
          y: 83,
        },
      ]
    : []

  const maxRisk = history.data?.max_risk_score ?? 0
  const loading = history.isLoading || timeline.isLoading

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      tone="hud"
      size="full"
      surfaceStyle={{ backgroundColor: DNA_NAVY }}
      icon={<Fingerprint className="size-5" />}
      title={`EVENT DNA · TRACK ${trackId}`}
      description={`Behavioural fingerprint of track ${trackId} on ${cameraId} from recorded events and movement timeline`}
    >
      {loading ? (
        <Spinner className="py-16 text-slate-400" label="Assembling track fingerprint…" />
      ) : history.isError ? (
        <div className="text-slate-200">
          <ErrorState error={history.error} onRetry={() => void history.refetch()} />
        </div>
      ) : !history.data ? (
        <EmptyState title="No events for this track" />
      ) : (
        <div className="flex flex-col gap-4" data-testid="event-dna">
          <DnaMap nodes={nodes} />
          <div className="flex flex-wrap items-center gap-2">
            <RiskBadge level={riskLevelForScore(maxRisk)} score={maxRisk} />
            {history.data.cameras.map((camera) => (
              <Badge key={camera} className="bg-cyan-400/10 font-mono text-cyan-200 ring-cyan-400/30">
                {camera}
              </Badge>
            ))}
            {latestEvent ? <Badge className="bg-white/5 text-slate-200 ring-white/10">{titleCase(latestEvent.object_class)}</Badge> : null}
            {latestEvent?.person_uuid ? <Badge className="bg-white/5 font-mono text-slate-200 ring-white/10">UUID {latestEvent.person_uuid.slice(0, 8)}</Badge> : null}
            {history.data.events.some((event) => event.is_active) ? <Badge className="bg-green-400/10 text-green-300 ring-green-400/30">ACTIVE</Badge> : null}
          </div>

          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-8">
            <Stat label="Events" value={history.data.total_events} />
            <Stat label="Dwell" value={formatDuration(history.data.total_time_seconds)} />
            <Stat label="Alerts" value={history.data.alert_count} />
            <Stat label="Max risk" value={maxRisk} />
            <Stat label="Samples" value={points.length} />
            <Stat label="Path" value={`${Math.round(metrics.distancePx)} px`} />
            <Stat label="Avg speed" value={`${metrics.averageSpeedPxPerSecond.toFixed(1)} px/s`} />
            <Stat label="Straightness" value={`${Math.round(metrics.straightness * 100)}%`} />
          </div>

          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Risk sequence ({timeline.data?.points.length ?? 0} samples · source {timeline.data?.source ?? '—'})
            </p>
            {timeline.data?.points.length ? <DnaStrip samples={timeline.data.points} /> : <p className="text-xs text-slate-500">No timeline samples recorded.</p>}
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
            <div className="overflow-hidden rounded-xl border border-cyan-400/15">
              {points.length ? (
                <TrackPlot points={points} zones={zones.data?.items ?? []} className="block aspect-video w-full" />
              ) : (
                <p className="p-6 text-center text-xs text-slate-500">No positions recorded for this track.</p>
              )}
            </div>
            <div className="flex flex-col items-center gap-3 rounded-xl border border-cyan-400/15 p-3">
              <p className="self-start text-[10px] font-semibold uppercase tracking-wider text-slate-400">Heading distribution</p>
              <Compass histogram={histogram} />
              <div className="w-full">
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Zone sequence</p>
                <div className="flex max-w-56 flex-wrap gap-1">
                  {zoneSequence.length ? (
                    zoneSequence.map((zone, index) => (
                      <span key={`${zone}-${index}`} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                        {index > 0 ? '→ ' : ''}
                        {zone}
                      </span>
                    ))
                  ) : (
                    <span className="text-[11px] text-slate-500">—</span>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="overflow-hidden rounded-xl border border-cyan-400/15">
            <table className="w-full text-left text-xs">
              <thead className="bg-white/5 text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-3 py-2">Event</th>
                  <th className="px-3 py-2">Camera</th>
                  <th className="px-3 py-2">First seen</th>
                  <th className="px-3 py-2">Last seen</th>
                  <th className="px-3 py-2">Dwell</th>
                  <th className="px-3 py-2">Alerts</th>
                  <th className="px-3 py-2">Max risk</th>
                </tr>
              </thead>
              <tbody className="font-mono text-slate-200">
                {history.data.events.map((event) => (
                  <tr key={event.event_id} className="border-t border-white/5">
                    <td className="px-3 py-1.5">#{event.event_id}</td>
                    <td className="px-3 py-1.5">{event.camera_id}</td>
                    <td className="px-3 py-1.5">{formatDateTime(event.first_seen)}</td>
                    <td className="px-3 py-1.5">{formatDateTime(event.last_seen)}</td>
                    <td className="px-3 py-1.5">{formatDuration(event.total_time_seconds)}</td>
                    <td className="px-3 py-1.5">{event.alert_count}</td>
                    <td className="px-3 py-1.5" style={{ color: RISK_META[riskLevelForScore(event.max_risk_score)].color }}>
                      {event.max_risk_score}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Modal>
  )
}
