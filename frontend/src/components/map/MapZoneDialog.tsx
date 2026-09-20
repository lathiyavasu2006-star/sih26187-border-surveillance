import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Hexagon, Ruler, TriangleAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { zonesApi } from '@/api/endpoints'
import { Button, Field, Input, Modal, Select } from '@/components/ui/primitives'
import { ZONE_META, ZONE_TYPES } from '@/lib/constants'
import { titleCase } from '@/lib/utils'
import type { CameraWithAlertCount, ZoneType } from '@/types'

/**
 * Saves a polygon drawn on the threat map as a real fence: the backend projects it into the chosen camera's
 * pixel frame through that camera's calibration, which is what the ML pipeline enforces.
 */
export function MapZoneDialog({
  polygon,
  cameras,
  onClose,
  onCalibrate,
}: {
  polygon: [number, number][]
  cameras: readonly CameraWithAlertCount[]
  onClose: () => void
  onCalibrate: (camera: CameraWithAlertCount) => void
}) {
  const queryClient = useQueryClient()
  const calibrated = cameras.filter((camera) => camera.calibrated)
  const [cameraId, setCameraId] = useState(calibrated[0]?.camera_id ?? '')
  const [zoneName, setZoneName] = useState('Map fence')
  const [zoneType, setZoneType] = useState<ZoneType>('restricted')
  const [loiter, setLoiter] = useState('30')
  const [problem, setProblem] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      zonesApi.create({
        camera_id: cameraId,
        zone_name: zoneName.trim(),
        zone_type: zoneType,
        geo_polygon: polygon,
        loiter_threshold_seconds: Number(loiter) || 30,
        night_rules: { multiplier: 1.5, start: 22, end: 5 },
        allowed_persons: [],
        color_hex: ZONE_META[zoneType].color,
        is_active: true,
      }),
    onSuccess: (zone) => {
      toast.success(`${zone.zone_name} saved — ${zone.camera_id} now enforces it (${zone.polygon.length} corners in frame)`)
      void queryClient.invalidateQueries({ queryKey: ['zones'] })
      onClose()
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setProblem(null)
    if (!cameraId) {
      setProblem('Choose a calibrated camera for this zone')
      return
    }
    if (!zoneName.trim()) {
      setProblem('The zone needs a name')
      return
    }
    create.mutate()
  }

  const uncalibrated = cameras.filter((camera) => !camera.calibrated && camera.gps_lat !== null)

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      icon={<Hexagon className="size-5" />}
      title="Save this area as a fence"
      description={`${polygon.length} corners drawn on the map. The camera converts them into its own view, so a person inside this area raises the zone's risk.`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" type="submit" form="map-zone" loading={create.isPending} data-testid="save-map-zone">
            Save fence
          </Button>
        </>
      }
    >
      <form id="map-zone" onSubmit={submit} className="grid gap-4" data-testid="map-zone-dialog">
        {calibrated.length === 0 ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-[11px] text-amber-900">
            <p className="flex items-start gap-2 font-semibold">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> No camera is calibrated against the map yet
            </p>
            <p className="mt-1">
              A camera has to know which part of the map it is looking at before a map zone can become its fence. Calibrate one
              (mark four landmarks in both views), then draw the zone again.
            </p>
            {uncalibrated.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {uncalibrated.slice(0, 6).map((camera) => (
                  <Button key={camera.camera_id} size="xs" variant="hud" icon={<Ruler className="size-3.5" />} onClick={() => onCalibrate(camera)}>
                    Calibrate {camera.camera_id}
                  </Button>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <Field label="Camera that enforces this fence" htmlFor="zone-camera" hint="Only calibrated cameras can take a map zone">
            <Select id="zone-camera" value={cameraId} onChange={(event) => setCameraId(event.target.value)}>
              {calibrated.map((camera) => (
                <option key={camera.camera_id} value={camera.camera_id}>
                  {camera.camera_id} — {camera.location_name ?? camera.name}
                  {camera.calibration_error_px !== null ? ` (±${camera.calibration_error_px.toFixed(0)} px)` : ''}
                </option>
              ))}
            </Select>
          </Field>
        )}

        <div className="grid grid-cols-2 gap-4">
          <Field label="Zone name" htmlFor="zone-name">
            <Input id="zone-name" value={zoneName} maxLength={100} onChange={(event) => setZoneName(event.target.value)} />
          </Field>
          <Field label="Zone type" htmlFor="zone-type" hint={`Risk bonus +${ZONE_META[zoneType].bonus}`}>
            <Select id="zone-type" value={zoneType} onChange={(event) => setZoneType(event.target.value as ZoneType)}>
              {ZONE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {titleCase(type)}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field label="Loitering threshold (seconds)" htmlFor="zone-loiter" hint="Dwell longer than this inside the zone and the risk rises">
          <Input id="zone-loiter" inputMode="numeric" value={loiter} onChange={(event) => setLoiter(event.target.value.replace(/\D/g, ''))} />
        </Field>

        {problem ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800" role="alert" data-testid="map-zone-error">
            {problem}
          </p>
        ) : null}
      </form>
    </Modal>
  )
}
