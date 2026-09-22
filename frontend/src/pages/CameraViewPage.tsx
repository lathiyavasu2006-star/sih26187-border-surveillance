import { Camera as CameraIcon, ChevronLeft, ChevronRight, Circle, Crosshair, Eye, EyeOff, Fingerprint, Gauge, Maximize, Network, Pause, Play, Radio, Square } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { WeaponCountBox } from '@/components/analysis/WeaponCountBox'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { CameraEditButton } from '@/components/camera/CameraEditDialog'
import { CameraThumbStrip } from '@/components/camera/CameraThumbStrip'
import { AlertPanel } from '@/components/alerts/AlertPanel'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, RiskBadge, Spinner, StatusBadge, StatusDot } from '@/components/ui/primitives'
import { useActiveEvents, useCameraDetail, useCameraZones, useCameras, useNow } from '@/hooks/useData'
import { useKeyboard } from '@/hooks/useKeyboard'
import { useWebSocket } from '@/hooks/useWebSocket'
import { startCanvasRecording, saveLiveSnapshot, type ActiveRecording } from '@/lib/recording'
import { cn, formatDuration, formatRelative, shortHash, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import { DETECTION_HOLD_MS, useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'
import type { LiveCameraState, WSDetection } from '@/types'

/** Live camera state sampled at most every `intervalMs` (tables must not re-render at the frame rate). */
function useSampledLive(cameraId: string, intervalMs = 500): LiveCameraState | undefined {
  const [snapshot, setSnapshot] = useState<LiveCameraState | undefined>(undefined)
  useEffect(() => {
    const timer = window.setInterval(() => setSnapshot(useLiveStore.getState().cameras[cameraId]), intervalMs)
    return () => window.clearInterval(timer)
  }, [cameraId, intervalMs])
  // Never show another camera's sample while the first one for this camera is pending.
  return snapshot?.cameraId === cameraId ? snapshot : undefined
}

export function CameraViewPage() {
  const params = useParams<{ cameraId: string }>()
  const cameraId = (params.cameraId ?? '').toUpperCase()
  const navigate = useNavigate()
  const now = useNow(1000)
  const detail = useCameraDetail(cameraId)
  const cameras = useCameras()
  const zones = useCameraZones(cameraId)
  const activeEvents = useActiveEvents(cameraId)
  const { connection, detail: connectionDetail } = useWebSocket(cameraId)
  const live = useSampledLive(cameraId)
  const feedRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const recordingRef = useRef<ActiveRecording | null>(null)
  const [paused, setPaused] = useState(false)
  const [recordingSince, setRecordingSince] = useState<number | null>(null)
  const [snapshotBusy, setSnapshotBusy] = useState(false)
  const { canUploadEvidence, canManageCameras } = usePermissions()
  const onCanvasReady = useCallback((canvas: HTMLCanvasElement | null) => {
    canvasRef.current = canvas
  }, [])

  const hudEnabled = useUiStore((state) => state.hudEnabled)
  const toggleHud = useUiStore((state) => state.toggleHud)
  const selectCamera = useUiStore((state) => state.selectCamera)
  const selectedTrack = useUiStore((state) => state.selectedTrack)
  const selectTrack = useUiStore((state) => state.selectTrack)
  const openModal = useUiStore((state) => state.openModal)

  useEffect(() => {
    if (cameraId) selectCamera(cameraId)
  }, [cameraId, selectCamera])

  // Leaving the camera (or the page) finishes and uploads a running recording instead of dropping it.
  useEffect(
    () => () => {
      const recording = recordingRef.current
      recordingRef.current = null
      if (recording) {
        void recording.stop().then(
          (saved) => toast.success(`Recording saved as evidence #${saved.evidence_id}`),
          (error: Error) => toast.error(error.message),
        )
      }
    },
    [cameraId],
  )

  const takeSnapshot = async () => {
    if (!canUploadEvidence) {
      toast.error('Saving evidence requires the supervisor role or above')
      return
    }
    if (snapshotBusy) return
    setSnapshotBusy(true)
    try {
      const saved = await saveLiveSnapshot(cameraId)
      toast.success(`Snapshot saved as evidence #${saved.evidence_id} · SHA-256 ${shortHash(saved.file_hash, 6)}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Snapshot failed')
    } finally {
      setSnapshotBusy(false)
    }
  }

  const finishRecording = (result: Promise<{ evidence_id: number; file_hash: string }>) => {
    recordingRef.current = null
    setRecordingSince(null)
    const pending = toast.loading('Uploading recording…')
    void result.then(
      (saved) => toast.success(`Recording saved as evidence #${saved.evidence_id} · SHA-256 ${shortHash(saved.file_hash, 6)}`, { id: pending }),
      (error: Error) => toast.error(error.message, { id: pending }),
    )
  }

  const toggleRecording = () => {
    if (recordingRef.current) {
      finishRecording(recordingRef.current.stop())
      return
    }
    if (!canUploadEvidence) {
      toast.error('Recording evidence requires the supervisor role or above')
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return
    try {
      recordingRef.current = startCanvasRecording(canvas, cameraId, finishRecording)
      setRecordingSince(recordingRef.current.startedAt)
      toast('Recording started (R to stop, max 5 min)', { id: 'rec', icon: '🔴' })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Recording failed')
    }
  }

  const togglePause = () => {
    setPaused((value) => {
      toast(value ? 'Feed resumed' : 'Feed paused', { id: 'pause', duration: 1000 })
      return !value
    })
  }

  const detections: WSDetection[] = live && now - live.detectionsReceivedAt <= DETECTION_HOLD_MS * 4 ? live.detections : []
  const trackSelected = selectedTrack && selectedTrack.cameraId === cameraId ? selectedTrack : null
  // Weapons in view right now (one per armed person) and distinct armed people since this view opened.
  const weaponsInView: Record<string, number> = {}
  for (const detection of detections) {
    if (detection.weapon_class) weaponsInView[detection.weapon_class] = (weaponsInView[detection.weapon_class] ?? 0) + 1
  }
  const armedThisSession: Record<string, number> = Object.fromEntries(
    Object.entries(live?.armedTracks ?? {}).map(([type, tracks]) => [type, tracks.length]),
  )

  // Keep the selected detection's live fields current.
  useEffect(() => {
    if (!trackSelected || !live) return
    const current = live.detections.find((detection) => detection.track_id === trackSelected.trackId)
    if (current && current !== trackSelected.detection) {
      selectTrack({ ...trackSelected, detection: current, personUuid: current.person_uuid })
    }
  }, [live, trackSelected, selectTrack])

  const onSelectDetection = useCallback(
    (detection: WSDetection | null) => {
      selectTrack(detection ? { cameraId, trackId: detection.track_id, personUuid: detection.person_uuid, detection } : null)
    },
    [cameraId, selectTrack],
  )

  const list = cameras.data?.items ?? []
  const index = list.findIndex((camera) => camera.camera_id === cameraId)
  const step = (direction: 1 | -1) => {
    if (list.length < 2) return
    const next = list[(index + direction + list.length) % list.length]
    if (next) navigate(`/cameras/${next.camera_id}`)
  }
  const fullscreen = () => {
    const element = feedRef.current
    if (!element) return
    if (document.fullscreenElement) void document.exitFullscreen()
    else void element.requestFullscreen().catch(() => toast.error('Full screen was blocked by the browser'))
  }
  const requireTrack = (action: (trackId: number) => void) => {
    if (!trackSelected) {
      toast('Click a detection on the feed to select a track first', { id: 'select-track' })
      return
    }
    action(trackSelected.trackId)
  }

  useKeyboard({
    f: fullscreen,
    s: () => void takeSnapshot(),
    r: toggleRecording,
    ' ': togglePause,
    '[': () => step(-1),
    ']': () => step(1),
    e: () => requireTrack((trackId) => openModal({ kind: 'eventDna', trackId, cameraId })),
    c: () => requireTrack((trackId) => openModal({ kind: 'correlation', trackId, cameraId, personUuid: trackSelected?.personUuid ?? null })),
    p: () => requireTrack((trackId) => openModal({ kind: 'prediction', trackId, cameraId })),
    u: () =>
      requireTrack(() => {
        if (trackSelected?.detection) openModal({ kind: 'fusion', cameraId, detection: trackSelected.detection })
        else toast('The selected track is no longer in view', { id: 'fusion' })
      }),
  })

  if (detail.isLoading) return <Spinner className="py-24" label={`Loading ${cameraId}…`} />
  if (detail.isError) {
    return (
      <div className="p-4">
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </div>
    )
  }
  const camera = detail.data
  if (!camera) return null

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="camera-view">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button size="sm" icon={<ChevronLeft className="size-3.5" />} onClick={() => step(-1)} disabled={list.length < 2} aria-label="Previous camera" />
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-mono text-lg font-semibold">{camera.camera_id}</h1>
              <StatusBadge status={camera.status} />
              <Badge className="bg-white text-slate-700 ring-slate-200">
                <StatusDot status={connection} /> WS {connection}
              </Badge>
            </div>
            <p className="text-xs text-muted">
              {camera.name} · {titleCase(camera.camera_type)} · {titleCase(camera.zone_region)}
              {camera.sector_name ? ` · ${camera.sector_name}` : ''}
              {camera.location_name ? ` · ${camera.location_name}` : ''}
            </p>
          </div>
          <Button size="sm" icon={<ChevronRight className="size-3.5" />} onClick={() => step(1)} disabled={list.length < 2} aria-label="Next camera" />
        </div>
        <div className="flex items-center gap-2">
          {canManageCameras ? <CameraEditButton camera={camera} size="sm" /> : null}
          <Button size="sm" icon={<CameraIcon className="size-3.5" />} loading={snapshotBusy} onClick={() => void takeSnapshot()} data-testid="snapshot-button">
            Snapshot <kbd className="kbd">S</kbd>
          </Button>
          <Button
            size="sm"
            variant={recordingSince !== null ? 'danger' : 'secondary'}
            icon={recordingSince !== null ? <Square className="size-3.5" /> : <Circle className="size-3.5 fill-red-600 text-red-600" />}
            onClick={toggleRecording}
            data-testid="record-button"
          >
            {recordingSince !== null ? `Stop ${formatDuration((now - recordingSince) / 1000)}` : 'Record'} <kbd className="kbd">R</kbd>
          </Button>
          <Button size="sm" icon={paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />} onClick={togglePause} aria-pressed={paused}>
            {paused ? 'Resume' : 'Pause'} <kbd className="kbd">Space</kbd>
          </Button>
          <Button size="sm" icon={hudEnabled ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />} onClick={toggleHud}>
            HUD {hudEnabled ? 'on' : 'off'} <kbd className="kbd">H</kbd>
          </Button>
          <Button size="sm" icon={<Maximize className="size-3.5" />} onClick={fullscreen}>
            Full screen <kbd className="kbd">F</kbd>
          </Button>
        </div>
      </div>

      <div className="grid gap-4 2xl:grid-cols-[1fr_400px]">
        <div className="flex flex-col gap-3">
          <div ref={feedRef} className="relative overflow-hidden rounded-2xl border border-slate-800 bg-command shadow-lg">
            <div className="aspect-video">
              <CameraFeed
                cameraId={camera.camera_id}
                cameraName={camera.name}
                zones={zones.data?.items ?? []}
                onSelectDetection={onSelectDetection}
                paused={paused}
                onCanvasReady={onCanvasReady}
              />
              {recordingSince !== null ? (
                <span className="absolute right-3 top-12 flex items-center gap-1.5 rounded bg-red-600 px-2 py-0.5 font-mono text-[11px] font-bold text-white" data-testid="rec-indicator">
                  <span className="size-2 animate-pulse rounded-full bg-white" /> REC {formatDuration((now - recordingSince) / 1000)}
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
            <span className="flex items-center gap-1">
              <Radio className="size-3.5" /> {live?.frameCount ?? 0} frames · {live ? live.measuredFps.toFixed(1) : '0.0'} fps received
            </span>
            <span>· {live?.viewerCount ?? camera.viewer_count} viewers</span>
            <span>· {zones.data?.total ?? 0} zones</span>
            {connectionDetail ? <span className="text-amber-600">· {connectionDetail}</span> : null}
            <span className="ml-auto">Click a box to select a track · E DNA · C correlate · P predict · U fusion</span>
          </div>
          {list.length > 1 ? <CameraThumbStrip cameras={list} activeId={camera.camera_id} /> : null}
        </div>

        <div className="flex flex-col gap-4">
          <WeaponCountBox counts={weaponsInView} title="Weapons in view now" unit="person holding it" showNote={false} />
          <WeaponCountBox counts={armedThisSession} title="Armed people this session" />
          <Card>
            <CardHeader
              title={`Live detections (${detections.length})`}
              subtitle={trackSelected ? `Selected track ID ${trackSelected.trackId}` : 'No track selected'}
              icon={<Crosshair className="size-4" />}
            />
            {trackSelected ? (
              <div className="grid grid-cols-4 gap-1.5 border-b border-line p-3">
                <Button size="xs" variant="hud" icon={<Fingerprint className="size-3" />} onClick={() => openModal({ kind: 'eventDna', trackId: trackSelected.trackId, cameraId })}>
                  DNA
                </Button>
                <Button size="xs" variant="hud" icon={<Network className="size-3" />} onClick={() => openModal({ kind: 'correlation', trackId: trackSelected.trackId, cameraId, personUuid: trackSelected.personUuid })}>
                  Corr.
                </Button>
                <Button size="xs" variant="hud" icon={<Crosshair className="size-3" />} onClick={() => openModal({ kind: 'prediction', trackId: trackSelected.trackId, cameraId })}>
                  Predict
                </Button>
                <Button
                  size="xs"
                  variant="hud"
                  icon={<Gauge className="size-3" />}
                  disabled={!trackSelected.detection}
                  onClick={() => trackSelected.detection && openModal({ kind: 'fusion', cameraId, detection: trackSelected.detection })}
                >
                  Fusion
                </Button>
              </div>
            ) : null}
            {detections.length === 0 ? (
              <EmptyState title="Nothing detected right now" description={connection === 'open' ? 'Detections update twice per second while the ML pipeline is running.' : 'Waiting for the live connection.'} />
            ) : (
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
                    <tr>
                      <th className="px-3 py-1.5">ID</th>
                      <th className="px-3 py-1.5">Class</th>
                      <th className="px-3 py-1.5">Conf</th>
                      <th className="px-3 py-1.5">Risk</th>
                      <th className="px-3 py-1.5">Zone</th>
                      <th className="px-3 py-1.5">Dwell</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detections.map((detection) => (
                      <tr
                        key={detection.track_id}
                        onClick={() => onSelectDetection(detection)}
                        className={cn('cursor-pointer border-t border-slate-100 hover:bg-slate-50', trackSelected?.trackId === detection.track_id && 'bg-cyan-50')}
                      >
                        <td className="px-3 py-1.5 font-mono font-semibold text-cyan-700">{detection.track_id}</td>
                        <td className="px-3 py-1.5">
                          {titleCase(detection.object_class)}
                          {detection.loitering ? <span className="ml-1 text-[10px] font-bold text-amber-600">LOITER</span> : null}
                        </td>
                        <td className="px-3 py-1.5 font-mono">{detection.confidence !== null ? `${Math.round(detection.confidence * 100)}%` : '—'}</td>
                        <td className="px-3 py-1.5">
                          <RiskBadge level={detection.risk_level} score={detection.risk_score} />
                        </td>
                        <td className="px-3 py-1.5">{detection.zone_name ?? '—'}</td>
                        <td className="px-3 py-1.5 font-mono">{formatDuration(detection.time_in_zone_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader title={`Active tracks (${activeEvents.data?.total ?? 0})`} subtitle="Recorded by the backend for this camera" />
            <div className="max-h-56 overflow-y-auto">
              {activeEvents.data?.items.length ? (
                activeEvents.data.items.map((event) => (
                  <button
                    type="button"
                    key={event.event_id}
                    onClick={() => openModal({ kind: 'eventDna', trackId: event.track_id, cameraId })}
                    className="flex w-full items-center justify-between border-b border-slate-100 px-4 py-2 text-left text-xs hover:bg-slate-50"
                  >
                    <span>
                      <span className="font-mono font-semibold text-cyan-700">ID {event.track_id}</span> · {titleCase(event.object_class)}
                    </span>
                    <span className="text-muted">
                      {formatDuration(event.total_time_seconds)} · max risk {event.max_risk_score} · {formatRelative(event.last_seen, now)}
                    </span>
                  </button>
                ))
              ) : (
                <p className="px-4 py-6 text-center text-xs text-muted">No active tracks.</p>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title={`Recent alerts (${camera.active_alert_count} open)`} />
            <div className="max-h-72 overflow-y-auto">
              <AlertPanel
                alerts={camera.recent_alerts}
                selectedId={null}
                onSelect={(alert) => navigate(`/alerts?alert=${encodeURIComponent(alert.alert_id)}`)}
                now={now}
                dense
              />
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
