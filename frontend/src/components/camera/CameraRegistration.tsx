import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Cctv, ChevronLeft, ChevronRight, XCircle } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { Button, Field, Input, Modal, Select } from '@/components/ui/primitives'
import { CAMERA_TYPES, ZONE_REGIONS } from '@/lib/constants'
import { cn, titleCase } from '@/lib/utils'
import { maskStreamUrl, validateCameraDraft, type CameraDraft } from '@/lib/validation'
import type { CameraRegisterRequest, CameraRegisterResponse, CameraType, ZoneRegion } from '@/types'

const STEPS = ['Identity', 'Location', 'Stream', 'Review'] as const

const EMPTY: CameraDraft = {
  name: '',
  camera_type: 'standard',
  zone_region: 'north',
  sector_name: '',
  location_name: '',
  gps_lat: '',
  gps_lng: '',
  rtsp_url: '',
  device_id: '',
}

function toRequest(draft: CameraDraft): CameraRegisterRequest {
  const optional = (value: string) => (value.trim() ? value.trim() : null)
  return {
    name: draft.name.trim(),
    camera_type: draft.camera_type,
    zone_region: draft.zone_region,
    sector_name: optional(draft.sector_name),
    location_name: optional(draft.location_name),
    gps_lat: draft.gps_lat.trim() ? Number(draft.gps_lat) : null,
    gps_lng: draft.gps_lng.trim() ? Number(draft.gps_lng) : null,
    rtsp_url: optional(draft.rtsp_url),
    device_id: optional(draft.device_id),
  }
}

/** Four-step registration wizard. The backend generates the camera ID and probes the stream. */
export function CameraRegistration({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [step, setStep] = useState(0)
  const [draft, setCameraDraft] = useState<CameraDraft>(EMPTY)
  const [touched, setTouched] = useState(false)
  const [result, setResult] = useState<CameraRegisterResponse | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const register = useMutation({
    mutationFn: () => camerasApi.register(toRequest(draft)),
    onSuccess: (response) => {
      setResult(response)
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
      toast.success(`${response.camera_id} registered`)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const errors = touched ? validateCameraDraft(draft, step) : {}
  const set = <K extends keyof CameraDraft>(key: K, value: CameraDraft[K]) => setCameraDraft((previous) => ({ ...previous, [key]: value }))

  const reset = () => {
    setStep(0)
    setCameraDraft(EMPTY)
    setTouched(false)
    setResult(null)
    register.reset()
  }
  const close = (next: boolean) => {
    onOpenChange(next)
    if (!next) reset()
  }
  const next = () => {
    setTouched(true)
    if (Object.keys(validateCameraDraft(draft, step)).length) return
    setTouched(false)
    if (step < STEPS.length - 1) setStep(step + 1)
    else register.mutate()
  }

  return (
    <Modal
      open={open}
      onOpenChange={close}
      size="md"
      icon={<Cctv className="size-5" />}
      title="Register camera"
      description="The server assigns the camera ID (CAM-{region}-NNN) and tests the stream before saving."
      footer={
        result ? (
          <>
            <Button onClick={reset}>Register another</Button>
            <Button
              variant="primary"
              onClick={() => {
                close(false)
                navigate(`/cameras/${result.camera_id}`)
              }}
            >
              Open {result.camera_id}
            </Button>
          </>
        ) : (
          <>
            <Button icon={<ChevronLeft className="size-3.5" />} disabled={step === 0 || register.isPending} onClick={() => setStep(step - 1)}>
              Back
            </Button>
            <Button variant="primary" loading={register.isPending} onClick={next} data-testid="register-next">
              {step === STEPS.length - 1 ? 'Register & test stream' : 'Next'}
              {step < STEPS.length - 1 ? <ChevronRight className="size-3.5" /> : null}
            </Button>
          </>
        )
      }
    >
      <ol className="mb-5 grid grid-cols-4 gap-2">
        {STEPS.map((label, index) => (
          <li key={label} className="flex flex-col gap-1.5">
            <span className={cn('h-1 rounded-full', index <= step || result ? 'bg-cyan-500' : 'bg-slate-200')} />
            <span className={cn('text-[11px] font-semibold', index === step && !result ? 'text-slate-900' : 'text-slate-400')}>
              {index + 1}. {label}
            </span>
          </li>
        ))}
      </ol>

      {result ? (
        <div className="flex flex-col gap-4">
          <div className={cn('flex items-start gap-3 rounded-xl p-4', result.connection_ok ? 'bg-green-50' : 'bg-amber-50')}>
            {result.connection_ok ? <CheckCircle2 className="size-5 text-green-600" /> : <XCircle className="size-5 text-amber-600" />}
            <div>
              <p className="text-sm font-semibold">
                {result.camera_id} · {result.status.toUpperCase()}
              </p>
              <p className="text-xs text-slate-600">{result.message}</p>
            </div>
          </div>
          {result.preview_frame ? (
            <img src={`data:image/jpeg;base64,${result.preview_frame}`} alt={`Preview from ${result.camera_id}`} className="aspect-video w-full rounded-xl bg-slate-900 object-contain" />
          ) : null}
        </div>
      ) : step === 0 ? (
        <div className="grid gap-4">
          <Field label="Camera name" htmlFor="cam-name" error={errors.name}>
            <Input id="cam-name" value={draft.name} maxLength={100} onChange={(event) => set('name', event.target.value)} placeholder="e.g. Raxaul BOP Gate 2" />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Camera type" htmlFor="cam-type">
              <Select id="cam-type" value={draft.camera_type} onChange={(event) => set('camera_type', event.target.value as CameraType)}>
                {CAMERA_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {titleCase(type)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Region" htmlFor="cam-region" hint="Determines the ID prefix">
              <Select id="cam-region" value={draft.zone_region} onChange={(event) => set('zone_region', event.target.value as ZoneRegion)}>
                {ZONE_REGIONS.map((region) => (
                  <option key={region} value={region}>
                    {titleCase(region)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </div>
      ) : step === 1 ? (
        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Sector" htmlFor="cam-sector">
              <Input id="cam-sector" value={draft.sector_name} maxLength={100} onChange={(event) => set('sector_name', event.target.value)} />
            </Field>
            <Field label="Location" htmlFor="cam-location">
              <Input id="cam-location" value={draft.location_name} maxLength={200} onChange={(event) => set('location_name', event.target.value)} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Latitude" htmlFor="cam-lat" error={errors.gps_lat} hint="Decimal degrees (places the camera on the map)">
              <Input id="cam-lat" inputMode="decimal" value={draft.gps_lat} onChange={(event) => set('gps_lat', event.target.value)} placeholder="26.9910" />
            </Field>
            <Field label="Longitude" htmlFor="cam-lng" error={errors.gps_lng}>
              <Input id="cam-lng" inputMode="decimal" value={draft.gps_lng} onChange={(event) => set('gps_lng', event.target.value)} placeholder="84.8540" />
            </Field>
          </div>
        </div>
      ) : step === 2 ? (
        <div className="grid gap-4">
          <Field label="Stream URL" htmlFor="cam-url" error={errors.rtsp_url} hint="Credentials in the URL are stored server-side and always masked in responses.">
            <Input id="cam-url" value={draft.rtsp_url} maxLength={2048} autoComplete="off" spellCheck={false} onChange={(event) => set('rtsp_url', event.target.value)} placeholder="rtsp://user:pass@10.0.0.21:554/stream1" />
          </Field>
          <Field label="Local device index / ID" htmlFor="cam-device" hint="For USB cameras attached to the ML host (e.g. 0)">
            <Input id="cam-device" value={draft.device_id} maxLength={100} onChange={(event) => set('device_id', event.target.value)} />
          </Field>
        </div>
      ) : (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          {(
            [
              ['Name', draft.name],
              ['Type', titleCase(draft.camera_type)],
              ['Region', titleCase(draft.zone_region)],
              ['Sector', draft.sector_name || '—'],
              ['Location', draft.location_name || '—'],
              ['GPS', draft.gps_lat && draft.gps_lng ? `${draft.gps_lat}, ${draft.gps_lng}` : '—'],
              ['Stream', draft.rtsp_url ? maskStreamUrl(draft.rtsp_url) : '—'],
              ['Device', draft.device_id || '—'],
            ] as const
          ).map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="break-all font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </Modal>
  )
}
