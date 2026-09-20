import { Crosshair, Fingerprint, Gauge, Network, Radio, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertPanel } from '@/components/alerts/AlertPanel'
import { mergeAlerts } from '@/lib/alerts'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { Button, ProgressBar, RiskBadge } from '@/components/ui/primitives'
import { useAlerts } from '@/hooks/useAlerts'
import { useCameras, useNow, useStats } from '@/hooks/useData'
import { formatDuration, titleCase } from '@/lib/utils'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'

export const RIGHT_PANEL_WIDTH = 280

function Section({ title, icon, children, action }: { title: string; icon: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="border-b border-line">
      <div className="flex items-center justify-between px-3 pb-1.5 pt-3">
        <h2 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-500">
          {icon}
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

function Meter({ label, value, suffix = '%' }: { label: string; value: number | null; suffix?: string }) {
  const tone = value === null ? 'cyan' : value >= 90 ? 'red' : value >= 75 ? 'amber' : 'cyan'
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px]">
        <span className="font-medium text-slate-600">{label}</span>
        <span className="font-mono font-semibold text-slate-900">{value === null ? 'n/a' : `${value.toFixed(0)}${suffix}`}</span>
      </div>
      <ProgressBar value={value ?? 0} tone={tone} />
    </div>
  )
}

export function RightPanel() {
  const navigate = useNavigate()
  const now = useNow(5000)
  const { data: cameras } = useCameras()
  const { data: stats } = useStats()
  const { data: alertPage, isLoading } = useAlerts({ acknowledged: false, limit: 25 })
  const liveAlerts = useLiveStore((state) => state.liveAlerts)
  const selectedCameraId = useUiStore((state) => state.selectedCameraId)
  const selectedTrack = useUiStore((state) => state.selectedTrack)
  const selectTrack = useUiStore((state) => state.selectTrack)
  const openModal = useUiStore((state) => state.openModal)

  const camera = cameras?.items.find((item) => item.camera_id === selectedCameraId) ?? cameras?.items[0]
  const alerts = mergeAlerts(alertPage?.items ?? [], liveAlerts.filter((alert) => !alert.acknowledged)).filter((alert) => !alert.acknowledged).slice(0, 25)

  return (
    <aside className="flex h-full shrink-0 flex-col border-l border-line bg-white" style={{ width: RIGHT_PANEL_WIDTH }} aria-label="Situational panel" data-testid="right-panel">
      <div className="scrollbar-thin flex-1 overflow-y-auto">
        <Section title="Live feed" icon={<Radio className="size-3.5" />}>
          <div className="px-3 pb-3">
            {camera ? (
              <button type="button" onClick={() => navigate(`/cameras/${camera.camera_id}`)} className="block w-full overflow-hidden rounded-lg text-left" aria-label={`Open ${camera.camera_id}`}>
                <div className="aspect-video">
                  <CameraFeed cameraId={camera.camera_id} cameraName={camera.name} compact interactive={false} />
                </div>
              </button>
            ) : (
              <p className="py-4 text-center text-xs text-muted">No cameras registered.</p>
            )}
          </div>
        </Section>

        {selectedTrack ? (
          <Section
            title={`Track ID ${selectedTrack.trackId}`}
            icon={<Crosshair className="size-3.5" />}
            action={
              <button type="button" onClick={() => selectTrack(null)} className="rounded p-0.5 text-slate-400 hover:bg-slate-100" aria-label="Clear selected track">
                <X className="size-3.5" />
              </button>
            }
          >
            <div className="space-y-2 px-3 pb-3">
              {selectedTrack.detection ? (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                  <RiskBadge level={selectedTrack.detection.risk_level} score={selectedTrack.detection.risk_score} />
                  <span className="font-semibold">{titleCase(selectedTrack.detection.object_class)}</span>
                  <span className="text-muted">{selectedTrack.detection.zone_name ?? 'no zone'}</span>
                  <span className="text-muted">{formatDuration(selectedTrack.detection.time_in_zone_seconds)} in zone</span>
                </div>
              ) : null}
              <div className="grid grid-cols-2 gap-1.5">
                <Button size="xs" variant="hud" icon={<Fingerprint className="size-3.5" />} onClick={() => openModal({ kind: 'eventDna', trackId: selectedTrack.trackId, cameraId: selectedTrack.cameraId })}>
                  DNA
                </Button>
                <Button
                  size="xs"
                  variant="hud"
                  icon={<Network className="size-3.5" />}
                  onClick={() => openModal({ kind: 'correlation', trackId: selectedTrack.trackId, cameraId: selectedTrack.cameraId, personUuid: selectedTrack.personUuid })}
                >
                  Correlate
                </Button>
                <Button size="xs" variant="hud" icon={<Crosshair className="size-3.5" />} onClick={() => openModal({ kind: 'prediction', trackId: selectedTrack.trackId, cameraId: selectedTrack.cameraId })}>
                  Predict
                </Button>
                <Button
                  size="xs"
                  variant="hud"
                  icon={<Gauge className="size-3.5" />}
                  disabled={!selectedTrack.detection}
                  onClick={() => selectedTrack.detection && openModal({ kind: 'fusion', cameraId: selectedTrack.cameraId, detection: selectedTrack.detection })}
                >
                  Fusion
                </Button>
              </div>
            </div>
          </Section>
        ) : null}

        <Section
          title={`Unacknowledged (${alertPage?.unacknowledged_count ?? alerts.length})`}
          icon={<span className="size-1.5 animate-pulse rounded-full bg-red-600" />}
          action={
            <button type="button" onClick={() => navigate('/alerts')} className="text-[11px] font-medium text-cyan-700 hover:underline">
              All
            </button>
          }
        >
          <AlertPanel
            alerts={alerts}
            loading={isLoading}
            selectedId={null}
            onSelect={(alert) => navigate(`/alerts?alert=${encodeURIComponent(alert.alert_id)}`)}
            now={now}
            dense
            emptyTitle="All alerts acknowledged"
          />
        </Section>

        <Section title="System" icon={<Gauge className="size-3.5" />}>
          <div className="space-y-2.5 px-3 pb-3">
            <Meter label="CPU" value={stats?.cpu_percent ?? null} />
            <Meter label="RAM" value={stats?.ram_percent ?? null} />
            <Meter label="GPU" value={stats?.gpu_percent ?? null} />
            <div className="grid grid-cols-2 gap-2 pt-1 text-[11px]">
              <div className="rounded-lg bg-slate-50 px-2 py-1.5">
                <p className="text-muted">Pipeline FPS</p>
                <p className="font-mono font-semibold">{stats ? stats.avg_system_fps.toFixed(1) : '—'}</p>
              </div>
              <div className="rounded-lg bg-slate-50 px-2 py-1.5">
                <p className="text-muted">WS clients</p>
                <p className="font-mono font-semibold">{stats?.websocket_clients ?? '—'}</p>
              </div>
              <div className="rounded-lg bg-slate-50 px-2 py-1.5">
                <p className="text-muted">Disk free</p>
                <p className="font-mono font-semibold">{stats ? `${stats.disk_free_gb.toFixed(0)} GB` : '—'}</p>
              </div>
              <div className="rounded-lg bg-slate-50 px-2 py-1.5">
                <p className="text-muted">Uptime</p>
                <p className="font-mono font-semibold">{stats ? formatDuration(stats.uptime_seconds) : '—'}</p>
              </div>
            </div>
          </div>
        </Section>
      </div>
    </aside>
  )
}
