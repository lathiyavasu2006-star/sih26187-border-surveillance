import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Circle, Globe, Loader2, LocateFixed, MapPinned, MonitorSmartphone, Save, XCircle } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { Button, Modal } from '@/components/ui/primitives'
import {
  FIX_SOURCE_LABEL,
  browserLocationPermission,
  describeAccuracy,
  isLocalDeviceCamera,
  locateConsole,
  type BrowserLocationPermission,
  type FixSource,
} from '@/lib/geolocation'
import { cn } from '@/lib/utils'
import type { Camera, CameraEditRequest } from '@/types'

type Phase = 'idle' | FixSource | 'saving'
type StepState = 'pending' | 'active' | 'done' | 'skipped' | 'failed'

const PERMISSION_TEXT: Record<BrowserLocationPermission, string> = {
  granted: 'Allowed',
  prompt: 'The browser will ask you to allow location',
  denied: 'Blocked in this browser — the Windows location service is used instead',
  unsupported: 'Not available in this browser — the Windows location service is used instead',
}

function Step({ state, icon, title, detail }: { state: StepState; icon: ReactNode; title: string; detail: ReactNode }) {
  const marker = {
    pending: <Circle className="size-4 text-slate-300" />,
    active: <Loader2 className="size-4 animate-spin text-cyan-600" />,
    done: <CheckCircle2 className="size-4 text-green-600" />,
    skipped: <Circle className="size-4 text-slate-300" />,
    failed: <XCircle className="size-4 text-red-600" />,
  }[state]
  return (
    <li className={cn('flex items-start gap-3 rounded-lg border px-3 py-2', state === 'active' ? 'border-cyan-400/60 bg-cyan-50' : 'border-line bg-white')}>
      <span className="mt-0.5">{marker}</span>
      <span className="mt-0.5 text-slate-500">{icon}</span>
      <span className="min-w-0">
        <span className={cn('block text-xs font-semibold', state === 'skipped' ? 'text-slate-400' : 'text-slate-800')}>{title}</span>
        <span className="block text-[11px] text-muted">{detail}</span>
      </span>
    </li>
  )
}

/**
 * "Enable location" flow for one camera: asks the browser for the console's position (Windows location service
 * as fallback), saves it to the camera (audited PATCH) and opens the threat map centred on the camera with its
 * virtual fences. `extraChanges` carries other unsaved edits from the edit form so nothing typed is lost.
 */
export function LocateCameraDialog({
  camera,
  onClose,
  onLocated,
  extraChanges,
}: {
  camera: Camera
  onClose: () => void
  onLocated?: (camera: Camera) => void
  extraChanges?: CameraEditRequest
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [permission, setPermission] = useState<BrowserLocationPermission | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [usedSource, setUsedSource] = useState<FixSource | null>(null)
  const [error, setError] = useState<string | null>(null)
  const busy = phase !== 'idle'
  const local = isLocalDeviceCamera(camera)

  useEffect(() => {
    let active = true
    void browserLocationPermission().then((state) => {
      if (active) setPermission(state)
    })
    return () => {
      active = false
    }
  }, [])

  const run = async () => {
    setError(null)
    setUsedSource(null)
    try {
      const fix = await locateConsole({ promptBrowser: true, onStep: setPhase })
      setUsedSource(fix.source)
      setPhase('saving')
      const { gps_lat: _lat, gps_lng: _lng, ...otherChanges } = extraChanges ?? {}
      const locationName = otherChanges.location_name !== undefined ? otherChanges.location_name : camera.location_name
      const updated = await camerasApi.edit(camera.camera_id, {
        ...otherChanges,
        gps_lat: fix.lat,
        gps_lng: fix.lng,
        ...(locationName ? {} : { location_name: `Console position (${describeAccuracy(fix.accuracy)}, ${FIX_SOURCE_LABEL[fix.source]})` }),
      })
      await queryClient.invalidateQueries({ queryKey: ['cameras'] })
      toast.success(`${updated.camera_id} located at ${fix.lat.toFixed(5)}, ${fix.lng.toFixed(5)} (${describeAccuracy(fix.accuracy)}, ${FIX_SOURCE_LABEL[fix.source]})`, {
        id: `locate-${updated.camera_id}`,
        duration: 5_000,
      })
      navigate(`/map?focus=${encodeURIComponent(updated.camera_id)}`)
      onClose()
      onLocated?.(updated)
    } catch (caught) {
      setPhase('idle')
      setError(caught instanceof Error ? caught.message : 'Location failed')
    }
  }

  const browserSkipped = permission === 'denied' || permission === 'unsupported'
  const browserState: StepState =
    phase === 'browser'
      ? 'active'
      : usedSource === 'browser'
        ? 'done'
        : browserSkipped
          ? 'skipped'
          : phase === 'windows' || usedSource === 'windows' || error
            ? 'failed'
            : 'pending'
  const windowsState: StepState =
    phase === 'windows' ? 'active' : usedSource === 'windows' ? 'done' : usedSource === 'browser' ? 'skipped' : error ? 'failed' : 'pending'
  const saveState: StepState = phase === 'saving' ? 'active' : 'pending'

  return (
    <Modal
      open
      onOpenChange={(open) => (open || busy ? undefined : onClose())}
      size="sm"
      icon={<LocateFixed className="size-5" />}
      title={`Locate ${camera.camera_id}`}
      description="Turn on location to place this camera at its real position and open it on the threat map with its virtual fences."
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} icon={<LocateFixed className="size-3.5" />} onClick={() => void run()} data-testid="enable-location">
            {error ? 'Try again' : 'Enable location & locate'}
          </Button>
        </>
      }
    >
      <div className="grid gap-3" data-testid="locate-camera-dialog">
        {!local ? (
          <p className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            This is a network camera. Its position is this console&apos;s position only if it is installed here — otherwise cancel and use Pin on map.
          </p>
        ) : null}
        <ol className="grid gap-2">
          <Step
            state={browserState}
            icon={<Globe className="size-4" />}
            title="Browser location"
            detail={permission ? PERMISSION_TEXT[permission] : 'Checking permission…'}
          />
          <Step
            state={windowsState}
            icon={<MonitorSmartphone className="size-4" />}
            title="Windows location service (fallback)"
            detail="Position of this computer from Wi-Fi / GPS, read by the backend. Uses the Windows location privacy switch."
          />
          <Step state={saveState} icon={<Save className="size-4" />} title="Save GPS to the camera" detail="Audit logged as EDIT_CAMERA." />
          <Step state="pending" icon={<MapPinned className="size-4" />} title="Open on the threat map" detail="Centred on the camera with its geo-fence rings and virtual fences." />
        </ol>
        {error ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800" role="alert" data-testid="locate-error">
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}
