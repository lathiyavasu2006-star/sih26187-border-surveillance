import { useMutation, useQueryClient } from '@tanstack/react-query'
import L from 'leaflet'
import { Crosshair, MapPin, Ruler, Trash2, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { MapContainer, Marker, TileLayer, useMapEvents } from 'react-leaflet'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { Button, EmptyState, Modal, Spinner } from '@/components/ui/primitives'
import { useLiveStore } from '@/stores/liveStore'
import { cn } from '@/lib/utils'
import type { CalibrationPoint, Camera } from '@/types'

const MIN_POINTS = 4
const MARKER_COLOURS = ['#22d3ee', '#f97316', '#a3e635', '#e879f9', '#facc15', '#38bdf8']

function numberedIcon(index: number): L.DivIcon {
  const colour = MARKER_COLOURS[index % MARKER_COLOURS.length]
  return L.divIcon({
    className: '',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<span style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:9999px;background:${colour};border:2px solid #0f172a;color:#0f172a;font:700 12px/1 ui-monospace,monospace">${index + 1}</span>`,
  })
}

function MapClicks({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(event) {
      onPick(Number(event.latlng.lat.toFixed(7)), Number(event.latlng.lng.toFixed(7)))
    },
  })
  return null
}

type Pending = { image?: [number, number]; geo?: [number, number] }

/**
 * Ground-plane calibration: the operator marks the same landmark in the camera image and on the map, four
 * times or more. The server fits a homography from those pairs, and from then on a zone drawn on the map is
 * projected into this camera's pixel fence.
 */
export function CalibrationDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const queryClient = useQueryClient()
  const live = useLiveStore((state) => state.cameras[camera.camera_id])
  const frameSize = useLiveStore((state) => state.frameSizes[camera.camera_id])
  const [snapshot, setSnapshot] = useState<{ frame: string; size: [number, number] } | null>(null)
  const [probing, setProbing] = useState(false)
  const [points, setPoints] = useState<CalibrationPoint[]>([])
  const [pending, setPending] = useState<Pending>({})
  const [result, setResult] = useState<string | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const frame = live?.frame ?? snapshot?.frame ?? null
  const size = frameSize ?? snapshot?.size ?? null
  const centre = useMemo<[number, number]>(
    () => [camera.gps_lat ?? 21.1458, camera.gps_lng ?? 79.0882],
    [camera.gps_lat, camera.gps_lng],
  )

  // No live stream: ask the backend for one frame so the operator still has an image to mark.
  useEffect(() => {
    if (live?.frame || snapshot || probing) return
    setProbing(true)
    void camerasApi
      .test(camera.camera_id)
      .then((probe) => {
        const [width, height] = (probe.resolution ?? '').split('x').map(Number)
        if (probe.frame_preview && width && height) setSnapshot({ frame: probe.frame_preview, size: [width, height] })
        else toast.error(probe.message || 'The camera did not return a frame to calibrate against')
      })
      .catch((error: Error) => toast.error(error.message))
      .finally(() => setProbing(false))
    // Runs once per dialog: a later live frame is picked up by the store subscription above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera.camera_id])

  const onImageClick = (event: ReactPointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    const matrix = svg?.getScreenCTM()
    if (!svg || !matrix || !size) return
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
    if (point.x < 0 || point.y < 0 || point.x > size[0] || point.y > size[1]) return
    setPending((previous) => ({ ...previous, image: [Number(point.x.toFixed(1)), Number(point.y.toFixed(1))] }))
  }

  // A pair is complete once the same landmark has been marked in both views.
  useEffect(() => {
    if (pending.image && pending.geo) {
      setPoints((previous) => [...previous, { image: pending.image as [number, number], geo: pending.geo as [number, number] }])
      setPending({})
      setResult(null)
    }
  }, [pending])

  const save = useMutation({
    mutationFn: () => camerasApi.calibrate(camera.camera_id, { points, image_size: size ?? [0, 0] }),
    onSuccess: (calibration) => {
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
      void queryClient.invalidateQueries({ queryKey: ['zones'] })
      toast.success(
        `${calibration.camera_id} calibrated · fit error ±${calibration.error_px.toFixed(1)} px` +
          (calibration.zones_projected ? ` · ${calibration.zones_projected} map zone(s) reprojected` : ''),
      )
      onClose()
    },
    onError: (error: Error) => setResult(error.message),
  })

  const street = MAP_STYLE_DEFINITIONS.hybrid
  const ready = points.length >= MIN_POINTS && size !== null

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="xl"
      icon={<Ruler className="size-5" />}
      title={`Calibrate ${camera.camera_id} against the map`}
      description="Mark the same landmark in the camera image and on the map — a gate post, a road junction, a tower base. Four or more pairs let the console turn map zones into this camera's fence."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={save.isPending} disabled={!ready} onClick={() => save.mutate()} data-testid="save-calibration">
            Save calibration ({points.length}/{MIN_POINTS})
          </Button>
        </>
      }
    >
      <div className="grid gap-3" data-testid="calibration-dialog">
        <div className="grid grid-cols-2 gap-3">
          {/* Camera image */}
          <div className="overflow-hidden rounded-xl border border-line bg-slate-950">
            <p className="border-b border-slate-800 px-3 py-1.5 font-mono text-[11px] text-hud">
              1 · CAMERA IMAGE {size ? `· ${size[0]}×${size[1]}` : ''}
            </p>
            <div className="relative aspect-video">
              {frame && size ? (
                <>
                  <img src={`data:image/jpeg;base64,${frame}`} alt={`${camera.camera_id} frame`} className="absolute inset-0 size-full object-contain" />
                  <svg
                    ref={svgRef}
                    viewBox={`0 0 ${size[0]} ${size[1]}`}
                    className="absolute inset-0 size-full cursor-crosshair"
                    onPointerDown={onImageClick}
                    data-testid="calibration-image"
                  >
                    {points.map((point, index) => (
                      <g key={`img-${index}`}>
                        <circle cx={point.image[0]} cy={point.image[1]} r={Math.max(6, size[0] / 160)} fill={MARKER_COLOURS[index % MARKER_COLOURS.length]} stroke="#0f172a" strokeWidth={2} />
                        <text x={point.image[0]} y={point.image[1] + 4} textAnchor="middle" fontSize={Math.max(10, size[0] / 140)} fontWeight={700} fill="#0f172a">
                          {index + 1}
                        </text>
                      </g>
                    ))}
                    {pending.image ? (
                      <circle cx={pending.image[0]} cy={pending.image[1]} r={Math.max(6, size[0] / 160)} fill="none" stroke="#f43f5e" strokeWidth={3} />
                    ) : null}
                  </svg>
                </>
              ) : (
                <div className="absolute inset-0 grid place-items-center">
                  {probing ? <Spinner label="Capturing a frame from the camera…" /> : <EmptyState icon={<Crosshair className="size-8" />} title="No frame yet" description="Start the camera stream, then reopen this dialog." />}
                </div>
              )}
            </div>
          </div>

          {/* Map */}
          <div className="overflow-hidden rounded-xl border border-line">
            <p className="border-b border-line bg-slate-50 px-3 py-1.5 font-mono text-[11px] text-slate-600">2 · SAME SPOT ON THE MAP</p>
            <MapContainer center={centre} zoom={camera.gps_lat !== null ? 18 : 5} className="h-[calc(100%-1.9rem)] min-h-[240px] w-full cursor-crosshair" keyboard={false}>
              <TileLayer url={street.url} attribution={street.attribution} maxZoom={street.maxZoom} />
              <TileLayer
                url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
                attribution="Labels &copy; Esri"
                maxZoom={19}
              />
              {points.map((point, index) => (
                <Marker key={`geo-${index}`} position={point.geo} icon={numberedIcon(index)} />
              ))}
              {pending.geo ? <Marker position={pending.geo} icon={numberedIcon(points.length)} /> : null}
              <MapClicks onPick={(lat, lng) => setPending((previous) => ({ ...previous, geo: [lat, lng] }))} />
            </MapContainer>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1 rounded-xl border border-line">
            <p className="border-b border-line bg-slate-50 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">
              Landmark pairs ({points.length})
            </p>
            {points.length === 0 ? (
              <p className="px-3 py-2 text-[11px] text-muted">
                Click a spot in the camera image, then the same spot on the map. Pick landmarks that lie on the ground and are far apart.
              </p>
            ) : (
              <ul className="max-h-28 overflow-y-auto">
                {points.map((point, index) => (
                  <li key={`pair-${index}`} className="flex items-center gap-2 border-b border-slate-100 px-3 py-1 font-mono text-[11px] last:border-0">
                    <span className="size-3 rounded-full" style={{ background: MARKER_COLOURS[index % MARKER_COLOURS.length] }} />
                    <span className="text-slate-600">{index + 1}</span>
                    <span className="text-slate-500">
                      px {point.image[0]}, {point.image[1]}
                    </span>
                    <span className="text-slate-400">→</span>
                    <span className="text-slate-500">
                      {point.geo[0].toFixed(6)}, {point.geo[1].toFixed(6)}
                    </span>
                    <button
                      type="button"
                      className="ml-auto rounded p-0.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
                      onClick={() => setPoints((previous) => previous.filter((_, other) => other !== index))}
                      aria-label={`Remove pair ${index + 1}`}
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className={cn('w-64 shrink-0 rounded-xl border p-3 text-[11px]', result ? 'border-red-200 bg-red-50 text-red-800' : 'border-line bg-white text-muted')}>
            {result ? (
              <p className="flex gap-2" data-testid="calibration-error">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> {result}
              </p>
            ) : (
              <p className="flex gap-2">
                <MapPin className="mt-0.5 size-3.5 shrink-0" />
                {pending.image ? 'Now click the same spot on the map.' : pending.geo ? 'Now click the same spot in the camera image.' : `Add ${Math.max(0, MIN_POINTS - points.length)} more pair(s).`}
              </p>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
