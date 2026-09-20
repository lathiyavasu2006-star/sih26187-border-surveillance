import { Gauge } from 'lucide-react'
import { useMemo } from 'react'
import { Badge, Modal, RiskBadge } from '@/components/ui/primitives'
import { useCameraZones } from '@/hooks/useData'
import { explainDetectionRisk, fuseConfidence, parseRiskReasons } from '@/lib/analysis'
import { RISK_META } from '@/lib/constants'
import { formatDateTime, riskLevelForScore, titleCase } from '@/lib/utils'
import type { Alert, WSDetection, ZoneType } from '@/types'

function Ring({ value, label, color }: { value: number; label: string; color: string }) {
  const radius = 42
  const circumference = 2 * Math.PI * radius
  return (
    <div className="flex flex-col items-center gap-1">
      <svg viewBox="0 0 100 100" className="size-32" role="img" aria-label={`${label} ${Math.round(value * 100)}%`}>
        <circle cx={50} cy={50} r={radius} fill="none" stroke="rgba(148,163,184,0.15)" strokeWidth={8} />
        <circle
          cx={50}
          cy={50}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={8}
          strokeLinecap="round"
          strokeDasharray={`${circumference * value} ${circumference}`}
          transform="rotate(-90 50 50)"
        />
        <text x={50} y={54} textAnchor="middle" fill="#f8fafc" fontSize={18} fontFamily="monospace" fontWeight={700}>
          {Math.round(value * 100)}%
        </text>
      </svg>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</p>
    </div>
  )
}

export function FusionModal({
  cameraId,
  detection,
  alert,
  onClose,
}: {
  cameraId: string
  detection: WSDetection | null
  alert?: Alert | null
  onClose: () => void
}) {
  const zones = useCameraZones(cameraId)
  const at = useMemo(() => (alert ? new Date(alert.timestamp) : new Date()), [alert])

  const riskScore = alert?.risk_score ?? detection?.risk_score ?? 0
  const zoneType = (alert?.zone_type as ZoneType | null | undefined) ?? detection?.zone_type ?? null
  const zoneName = alert?.zone_name ?? detection?.zone_name ?? null
  const zone = zones.data?.items.find((candidate) => candidate.zone_name === zoneName)

  const breakdown = useMemo(() => {
    if (alert) return parseRiskReasons(alert.risk_reasons, alert.risk_score)
    if (detection) return explainDetectionRisk(detection, at, zones.data?.items ?? [])
    return null
  }, [alert, detection, at, zones.data])

  const fusion = useMemo(
    () =>
      fuseConfidence({
        confidence: detection?.confidence ?? null,
        riskScore,
        zoneType,
        dwellSeconds: detection?.time_in_zone_seconds ?? 0,
        loiterThresholdSeconds: zone?.loiter_threshold_seconds ?? 30,
      }),
    [detection, riskScore, zoneType, zone],
  )

  const level = riskLevelForScore(riskScore)
  const title = alert ? alert.alert_id : detection ? `TRACK ${detection.track_id}` : cameraId

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      tone="hud"
      size="lg"
      icon={<Gauge className="size-5" />}
      title={`CONFIDENCE FUSION · ${title}`}
      description={`Detector confidence, behaviour score, zone policy and dwell persistence combined · ${cameraId} · ${formatDateTime(at)}`}
    >
      <div className="flex flex-col gap-5" data-testid="fusion">
        <div className="flex flex-wrap items-center gap-2">
          <RiskBadge level={level} score={riskScore} />
          {detection ? <Badge className="bg-white/5 text-slate-200 ring-white/10">{titleCase(detection.object_class)}</Badge> : null}
          {alert ? <Badge className="bg-white/5 text-slate-200 ring-white/10">{titleCase(alert.alert_type)}</Badge> : null}
          <Badge className="bg-white/5 text-slate-200 ring-white/10">{zoneName ? `${zoneName} · ${titleCase(zoneType)}` : 'No zone'}</Badge>
          {detection?.loitering ? <Badge className="bg-amber-400/10 text-amber-300 ring-amber-400/30">LOITERING</Badge> : null}
        </div>

        <div className="grid items-center gap-4 md:grid-cols-[auto_1fr]">
          <div className="flex gap-4">
            <Ring value={fusion.fused} label="Fused confidence" color="#22d3ee" />
            <Ring value={fusion.threatIndex} label="Threat index" color={RISK_META[level].color} />
          </div>
          <div className="flex flex-col gap-3">
            {fusion.channels.map((channel) => (
              <div key={channel.key}>
                <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                  <span className="font-semibold text-slate-200">
                    {channel.label} <span className="font-mono text-[10px] text-slate-500">w={channel.weight}</span>
                  </span>
                  <span className="font-mono text-slate-100">{Math.round(channel.value * 100)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/10">
                  <div className="h-full rounded-full bg-cyan-400" style={{ width: `${channel.value * 100}%` }} />
                </div>
                <p className="mt-0.5 text-[10px] text-slate-500">{channel.explanation}</p>
              </div>
            ))}
            <p className="text-[10px] text-slate-500">
              Fused confidence = Σ wᵢ·channelᵢ. Threat index = detector confidence × risk score / 100
              {detection?.confidence === null || !detection ? ' (0 when no detector confidence is available).' : '.'}
            </p>
          </div>
        </div>

        {breakdown ? (
          <div className="overflow-hidden rounded-xl border border-cyan-400/15">
            <div className="flex items-center justify-between bg-white/5 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <span>Risk engine decomposition {alert ? '(from alert reasons)' : '(from live detection fields)'}</span>
              <span className="font-mono">
                reported {breakdown.reported} · explained {Math.min(100, breakdown.implied)}
              </span>
            </div>
            <table className="w-full text-xs text-slate-200">
              <tbody className="font-mono">
                <tr className="border-t border-white/5">
                  <td className="px-3 py-1.5">Base score</td>
                  <td className="px-3 py-1.5 text-right">+{breakdown.base}</td>
                </tr>
                {breakdown.components.map((component) => (
                  <tr key={component.key} className="border-t border-white/5">
                    <td className="px-3 py-1.5">
                      {component.label}
                      {component.detail ? <span className="ml-2 text-[10px] text-slate-500">{component.detail}</span> : null}
                    </td>
                    <td className="px-3 py-1.5 text-right">+{component.points}</td>
                  </tr>
                ))}
                {breakdown.multiplier ? (
                  <tr className="border-t border-white/5">
                    <td className="px-3 py-1.5">Night window multiplier (22:00–05:00 IST)</td>
                    <td className="px-3 py-1.5 text-right">×{breakdown.multiplier}</td>
                  </tr>
                ) : null}
                {breakdown.residual !== 0 ? (
                  <tr className="border-t border-white/5 text-amber-300">
                    <td className="px-3 py-1.5">
                      Not carried in the live payload (toward fence / repeat appearance / running / weapon)
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {breakdown.residual > 0 ? '+' : ''}
                      {breakdown.residual}
                    </td>
                  </tr>
                ) : null}
                <tr className="border-t border-cyan-400/20 bg-white/5 font-bold">
                  <td className="px-3 py-1.5">Risk score (capped at 100)</td>
                  <td className="px-3 py-1.5 text-right" style={{ color: RISK_META[level].color }}>
                    {breakdown.reported}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
