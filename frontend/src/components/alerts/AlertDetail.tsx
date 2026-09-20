import { Brain, Check, Crosshair, Fingerprint, MapPin, Network, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { snapshotRequestPath } from '@/api/client'
import { alertsApi, evidenceApi } from '@/api/endpoints'
import { ProtectedImage } from '@/components/ui/ProtectedImage'
import { Badge, Button, ConfirmDialog, Field, RiskBadge, Switch, Textarea } from '@/components/ui/primitives'
import { useAcknowledgeAlert } from '@/hooks/useAlerts'
import { RISK_META } from '@/lib/constants'
import { formatDateTime, shortHash, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'
import type { Alert } from '@/types'

export function AlertDetail({ alert, onDeleted }: { alert: Alert; onDeleted?: () => void }) {
  const [falseAlarm, setFalseAlarm] = useState(false)
  const [notes, setNotes] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const acknowledge = useAcknowledgeAlert()
  const { isAdmin } = usePermissions()
  const openModal = useUiStore((state) => state.openModal)
  const queryClient = useQueryClient()
  const meta = RISK_META[alert.risk_level]

  const evidence = useQuery({
    queryKey: ['evidence', 'alert', alert.alert_id],
    queryFn: () => evidenceApi.list({ alert_id: alert.alert_id, limit: 10 }),
    staleTime: 60_000,
  })
  const snapshotEvidence = evidence.data?.items.find((item) => item.evidence_type === 'snapshot' && item.file_exists)
  const imagePath = snapshotEvidence?.file_url ?? snapshotRequestPath(alert.snapshot_path)

  const remove = useMutation({
    mutationFn: () => alertsApi.remove(alert.alert_id),
    onSuccess: () => {
      toast.success(`${alert.alert_id} deleted`)
      setConfirmDelete(false)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
      onDeleted?.()
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const trackActions = alert.track_id !== null
  return (
    <div className="flex flex-col gap-4" data-testid="alert-detail">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[11px] text-muted">{alert.alert_id}</p>
          <h2 className="mt-0.5 text-lg font-semibold text-slate-900">{titleCase(alert.alert_type)}</h2>
          <p className="text-xs text-muted">{formatDateTime(alert.timestamp)}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <RiskBadge level={alert.risk_level} score={alert.risk_score} />
          {alert.acknowledged ? (
            <Badge className="bg-green-50 text-green-700 ring-green-200">
              <Check className="size-3" /> {alert.false_alarm ? 'False alarm' : 'Acknowledged'}
            </Badge>
          ) : (
            <Badge className="bg-red-50 text-red-700 ring-red-200">Awaiting action</Badge>
          )}
        </div>
      </div>

      <ProtectedImage requestPath={imagePath} alt={`Snapshot for ${alert.alert_id}`} className="aspect-video rounded-xl" eager />
      {snapshotEvidence ? (
        <p className="-mt-2 font-mono text-[10px] text-muted" title={snapshotEvidence.file_hash}>
          SHA-256 {shortHash(snapshotEvidence.file_hash, 12)} · evidence #{snapshotEvidence.evidence_id}
        </p>
      ) : null}

      <div className="rounded-xl border border-line">
        <div className="flex items-center justify-between border-b border-line px-3 py-2">
          <p className="text-xs font-semibold text-slate-700">Risk score</p>
          <p className="font-mono text-sm font-bold" style={{ color: meta.color }}>
            {alert.risk_score}/100
          </p>
        </div>
        <div className="px-3 py-2">
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div className="h-full rounded-full" style={{ width: `${alert.risk_score}%`, background: meta.color }} />
          </div>
          {alert.risk_reasons.length ? (
            <ul className="mt-2 space-y-1">
              {alert.risk_reasons.map((reason) => (
                <li key={reason} className="flex gap-2 text-xs text-slate-600">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-slate-400" />
                  {reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-muted">No reasons recorded.</p>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div>
          <dt className="text-muted">Camera</dt>
          <dd className="font-mono font-semibold">
            <Link to={`/cameras/${alert.camera_id}`} className="text-cyan-700 hover:underline">
              {alert.camera_id}
            </Link>
          </dd>
        </div>
        <div>
          <dt className="text-muted">Track</dt>
          <dd className="font-mono font-semibold">{alert.track_id ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted">Zone</dt>
          <dd className="font-semibold">{alert.zone_name ? `${alert.zone_name} (${titleCase(alert.zone_type)})` : '—'}</dd>
        </div>
        <div>
          <dt className="text-muted">GPS</dt>
          <dd className="flex items-center gap-1 font-mono font-semibold">
            {alert.gps_lat !== null && alert.gps_lng !== null ? (
              <>
                <MapPin className="size-3" />
                {alert.gps_lat.toFixed(5)}, {alert.gps_lng.toFixed(5)}
              </>
            ) : (
              '—'
            )}
          </dd>
        </div>
        {alert.acknowledged ? (
          <>
            <div>
              <dt className="text-muted">Acknowledged</dt>
              <dd className="font-semibold">{formatDateTime(alert.acknowledged_at)}</dd>
            </div>
            <div>
              <dt className="text-muted">Notes</dt>
              <dd className="font-semibold">{alert.notes ?? '—'}</dd>
            </div>
          </>
        ) : null}
      </dl>

      {trackActions && alert.track_id !== null ? (
        <div className="grid grid-cols-3 gap-2">
          <Button size="xs" variant="hud" icon={<Fingerprint className="size-3.5" />} onClick={() => openModal({ kind: 'eventDna', trackId: alert.track_id ?? 0, cameraId: alert.camera_id })}>
            Event DNA
          </Button>
          <Button
            size="xs"
            variant="hud"
            icon={<Network className="size-3.5" />}
            onClick={() => openModal({ kind: 'correlation', trackId: alert.track_id ?? 0, cameraId: alert.camera_id, personUuid: alert.person_uuid })}
          >
            Correlate
          </Button>
          <Button size="xs" variant="hud" icon={<Crosshair className="size-3.5" />} onClick={() => openModal({ kind: 'prediction', trackId: alert.track_id ?? 0, cameraId: alert.camera_id })}>
            Predict
          </Button>
        </div>
      ) : null}

      {!alert.acknowledged ? (
        <div className="flex flex-col gap-3 rounded-xl border border-line bg-slate-50/60 p-3">
          <div className="flex items-center justify-between">
            <label className="text-xs font-semibold text-slate-700" htmlFor={`false-alarm-${alert.alert_id}`}>
              Mark as false alarm
            </label>
            <Switch checked={falseAlarm} onCheckedChange={setFalseAlarm} label="Mark as false alarm" />
          </div>
          <Field label={falseAlarm ? 'Notes (required for false alarm)' : 'Notes (optional)'} htmlFor={`notes-${alert.alert_id}`}>
            <Textarea
              id={`notes-${alert.alert_id}`}
              value={notes}
              maxLength={5000}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Action taken, patrol dispatched, observations…"
            />
          </Field>
          <Button
            variant={falseAlarm ? 'secondary' : 'primary'}
            size="md"
            loading={acknowledge.isPending}
            disabled={falseAlarm && !notes.trim()}
            icon={<Brain className="size-4" />}
            onClick={() => acknowledge.mutate({ alertId: alert.alert_id, falseAlarm, notes })}
            data-testid="acknowledge-button"
          >
            {falseAlarm ? 'Confirm false alarm' : 'Acknowledge alert'} <kbd className="kbd ml-1">Enter</kbd>
          </Button>
        </div>
      ) : null}

      {isAdmin ? (
        <Button variant="ghost" size="xs" className="self-start text-red-600 hover:bg-red-50 hover:text-red-700" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)}>
          Delete alert (admin)
        </Button>
      ) : null}
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete alert?"
        description={
          <>
            <span className="font-mono">{alert.alert_id}</span> will be permanently removed. The deletion is recorded in the audit log.
          </>
        }
        confirmLabel="Delete"
        loading={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </div>
  )
}
