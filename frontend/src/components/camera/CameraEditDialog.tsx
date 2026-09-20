import { useMutation, useQueryClient } from '@tanstack/react-query'
import { LocateFixed, Map as MapIcon, MapPin, MapPinned, Pencil, Save } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { LocateCameraDialog } from '@/components/camera/LocateCameraDialog'
import { MapPicker } from '@/components/map/MapPicker'
import { Button, Field, Input, Modal, Select } from '@/components/ui/primitives'
import { CAMERA_TYPES, ZONE_REGIONS } from '@/lib/constants'
import { isLocalDeviceCamera } from '@/lib/geolocation'
import { titleCase } from '@/lib/utils'
import { cameraEditPayload, validateCameraEdit, type CameraEditForm } from '@/lib/validation'
import type { Camera, CameraType, ZoneRegion } from '@/types'

function formFor(camera: Camera): CameraEditForm {
  return {
    name: camera.name,
    location_name: camera.location_name ?? '',
    gps_lat: camera.gps_lat === null ? '' : String(camera.gps_lat),
    gps_lng: camera.gps_lng === null ? '' : String(camera.gps_lng),
    sector_name: camera.sector_name ?? '',
    camera_type: camera.camera_type,
    zone_region: camera.zone_region,
  }
}

export function CameraEditButton({ camera, size = 'xs' }: { camera: Camera; size?: 'xs' | 'sm' }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size={size} icon={<Pencil className="size-3" />} onClick={() => setOpen(true)} data-testid={`edit-camera-${camera.camera_id}`}>
        Edit
      </Button>
      {open ? <CameraEditDialog camera={camera} onClose={() => setOpen(false)} /> : null}
    </>
  )
}

export function CameraEditDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CameraEditForm>(() => formFor(camera))
  const [picking, setPicking] = useState(false)
  const [touched, setTouched] = useState(false)
  const navigate = useNavigate()
  const [locateOpen, setLocateOpen] = useState(false)
  // Location access, saving and the map all happen in the locate dialog; other unsaved edits travel with it.
  const openLocate = () => {
    setTouched(true)
    if (validateCameraEdit(form).name) return
    setLocateOpen(true)
  }
  const errors = touched ? validateCameraEdit(form) : {}
  const set = <K extends keyof CameraEditForm>(key: K, value: CameraEditForm[K]) => setForm((previous) => ({ ...previous, [key]: value }))

  const save = useMutation({
    mutationFn: () => camerasApi.edit(camera.camera_id, cameraEditPayload(camera, form)),
    onSuccess: (updated) => {
      toast.success(`${updated.camera_id} updated${updated.gps_lat !== null ? ` · ${updated.gps_lat}, ${updated.gps_lng}` : ''}`)
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
      onClose()
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (Object.keys(validateCameraEdit(form)).length) return
    if (Object.keys(cameraEditPayload(camera, form)).length === 0) {
      toast('No changes to save', { id: 'no-camera-changes' })
      return
    }
    save.mutate()
  }

  const lat = form.gps_lat.trim() && Number.isFinite(Number(form.gps_lat)) ? Number(form.gps_lat) : null
  const lng = form.gps_lng.trim() && Number.isFinite(Number(form.gps_lng)) ? Number(form.gps_lng) : null

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      icon={<Pencil className="size-5" />}
      title={`Edit ${camera.camera_id}`}
      description="Camera ID, status and stream URL are managed separately. The change is audit logged."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="edit-camera" loading={save.isPending} icon={<Save className="size-3.5" />} data-testid="save-camera">
            Save changes
          </Button>
        </>
      }
    >
      <form id="edit-camera" onSubmit={submit} className="grid gap-4">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Name" htmlFor="edit-name" error={errors.name}>
            <Input id="edit-name" value={form.name} maxLength={100} onChange={(event) => set('name', event.target.value)} />
          </Field>
          <Field label="Location name" htmlFor="edit-location">
            <Input id="edit-location" value={form.location_name} maxLength={200} onChange={(event) => set('location_name', event.target.value)} placeholder="J&K Border - Sector 7" />
          </Field>
        </div>
        <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3">
          <Field label="GPS latitude" htmlFor="edit-lat" error={errors.gps_lat}>
            <Input id="edit-lat" inputMode="decimal" value={form.gps_lat} onChange={(event) => set('gps_lat', event.target.value)} placeholder="32.7266" />
          </Field>
          <Field label="GPS longitude" htmlFor="edit-lng" error={errors.gps_lng}>
            <Input id="edit-lng" inputMode="decimal" value={form.gps_lng} onChange={(event) => set('gps_lng', event.target.value)} placeholder="74.8570" />
          </Field>
          <Button size="md" variant={picking ? 'primary' : 'hud'} icon={<MapPinned className="size-4" />} onClick={() => setPicking(!picking)} aria-pressed={picking} data-testid="pin-on-map">
            Pin on map
          </Button>
        </div>
        <div className="-mt-2 flex flex-wrap items-center gap-2">
          <Button size="xs" variant="hud" icon={<LocateFixed className="size-3.5" />} onClick={openLocate} data-testid="use-current-location">
            Use current location
          </Button>
          {camera.gps_lat !== null && camera.gps_lng !== null ? (
            <Button
              size="xs"
              icon={<MapIcon className="size-3.5" />}
              onClick={() => {
                onClose()
                navigate(`/map?focus=${encodeURIComponent(camera.camera_id)}`)
              }}
              data-testid="view-on-map"
            >
              View on map
            </Button>
          ) : null}
          <span className="text-[11px] text-muted">
            {isLocalDeviceCamera(camera) ? 'This camera is attached to this console: its real position is the console’s position.' : 'For network cameras, pin the installation point on the map.'}
          </span>
        </div>
        {picking ? (
          <MapPicker
            lat={lat}
            lng={lng}
            onPick={(pickedLat, pickedLng) => {
              setForm((previous) => ({ ...previous, gps_lat: String(pickedLat), gps_lng: String(pickedLng) }))
            }}
          />
        ) : lat !== null && lng !== null ? (
          <p className="-mt-2 flex items-center gap-1 text-[11px] text-muted">
            <MapPin className="size-3" /> The camera will appear on the threat map at {lat}, {lng}.
          </p>
        ) : null}
        <div className="grid grid-cols-3 gap-4">
          <Field label="Sector" htmlFor="edit-sector">
            <Input id="edit-sector" value={form.sector_name} maxLength={100} onChange={(event) => set('sector_name', event.target.value)} />
          </Field>
          <Field label="Camera type" htmlFor="edit-type">
            <Select id="edit-type" value={form.camera_type} onChange={(event) => set('camera_type', event.target.value as CameraType)}>
              {CAMERA_TYPES.map((type) => (
                <option key={type} value={type}>
                  {titleCase(type)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Zone region" htmlFor="edit-region" hint="The camera ID keeps its prefix">
            <Select id="edit-region" value={form.zone_region} onChange={(event) => set('zone_region', event.target.value as ZoneRegion)}>
              {ZONE_REGIONS.map((region) => (
                <option key={region} value={region}>
                  {titleCase(region)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </form>
      {locateOpen ? (
        <LocateCameraDialog camera={camera} extraChanges={cameraEditPayload(camera, form)} onClose={() => setLocateOpen(false)} onLocated={onClose} />
      ) : null}
    </Modal>
  )
}
