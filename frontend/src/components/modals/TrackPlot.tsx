import { ZONE_META } from '@/lib/constants'
import { inferFrameSize, type PredictedPoint, type TrackPoint } from '@/lib/analysis'
import type { Zone } from '@/types'

/** Camera-plane plot: zones, observed path (fading from old to new) and optional forecast with uncertainty. */
export function TrackPlot({
  points,
  zones,
  prediction,
  className,
}: {
  points: readonly TrackPoint[]
  zones: readonly Zone[]
  prediction?: readonly PredictedPoint[]
  className?: string
}) {
  const [width, height] = inferFrameSize([...points, ...(prediction ?? [])], zones)
  const last = points[points.length - 1]
  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
  const forecastPath =
    last && prediction?.length
      ? `M${last.x.toFixed(1)},${last.y.toFixed(1)} ${prediction.map((point) => `L${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')}`
      : null
  const stroke = Math.max(2, width / 400)

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={className} role="img" aria-label="Track path on the camera plane" preserveAspectRatio="xMidYMid meet">
      <rect width={width} height={height} fill="#0b1220" />
      <g stroke="rgba(34,211,238,0.07)" strokeWidth={1}>
        {Array.from({ length: Math.floor(width / 80) }, (_, i) => (
          <line key={`v${i}`} x1={(i + 1) * 80} y1={0} x2={(i + 1) * 80} y2={height} />
        ))}
        {Array.from({ length: Math.floor(height / 80) }, (_, i) => (
          <line key={`h${i}`} x1={0} y1={(i + 1) * 80} x2={width} y2={(i + 1) * 80} />
        ))}
      </g>
      {zones.map((zone) => (
        <g key={zone.zone_id}>
          <polygon
            points={zone.polygon.map(([x, y]) => `${x},${y}`).join(' ')}
            fill={`${ZONE_META[zone.zone_type].color}22`}
            stroke={ZONE_META[zone.zone_type].color}
            strokeWidth={stroke}
            strokeDasharray={`${stroke * 4} ${stroke * 3}`}
          />
          {zone.polygon[0] ? (
            <text x={zone.polygon[0][0] + 8} y={zone.polygon[0][1] + width / 60} fill={ZONE_META[zone.zone_type].color} fontSize={width / 60} fontFamily="monospace" fontWeight={700}>
              {zone.zone_name.toUpperCase()}
            </text>
          ) : null}
        </g>
      ))}
      {path ? <path d={path} fill="none" stroke="#22d3ee" strokeWidth={stroke} strokeLinejoin="round" strokeLinecap="round" opacity={0.9} /> : null}
      {points.length > 0 && points[0] ? <circle cx={points[0].x} cy={points[0].y} r={stroke * 2.5} fill="#94a3b8" /> : null}
      {prediction?.map((point) => (
        <circle key={point.secondsAhead} cx={point.x} cy={point.y} r={Math.max(stroke * 2, point.uncertainty)} fill="rgba(249,115,22,0.10)" stroke="rgba(249,115,22,0.5)" strokeWidth={1} />
      ))}
      {forecastPath ? <path d={forecastPath} fill="none" stroke="#f97316" strokeWidth={stroke} strokeDasharray={`${stroke * 3} ${stroke * 2}`} /> : null}
      {prediction?.map((point) => (
        <text key={`t${point.secondsAhead}`} x={point.x + stroke * 3} y={point.y - stroke * 3} fill="#fdba74" fontSize={width / 70} fontFamily="monospace">
          +{point.secondsAhead}s
        </text>
      ))}
      {last ? (
        <g>
          <circle cx={last.x} cy={last.y} r={stroke * 4} fill="none" stroke="#22d3ee" strokeWidth={stroke} />
          <circle cx={last.x} cy={last.y} r={stroke * 1.6} fill="#22d3ee" />
        </g>
      ) : null}
      <text x={10} y={height - 10} fill="rgba(148,163,184,0.8)" fontSize={width / 75} fontFamily="monospace">
        {width}×{height} px camera plane
      </text>
    </svg>
  )
}
