import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Hexagon, Pencil, Plus, Save, Trash2, Undo2, X } from 'lucide-react'
import { useMemo, useRef, useState, type PointerEvent } from 'react'
import toast from 'react-hot-toast'
import { zonesApi } from '@/api/endpoints'
import { CameraFeed } from '@/components/camera/CameraFeed'
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorState, Field, Input, PageHeader, Select, Spinner, Switch } from '@/components/ui/primitives'
import { useCameraZones, useCameras } from '@/hooks/useData'
import { NIGHT_WINDOW, ZONE_META, ZONE_TYPES } from '@/lib/constants'
import { cn, formatDuration } from '@/lib/utils'
import { validateZoneForm, type ZoneForm } from '@/lib/validation'
import { usePermissions } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'
import { useUiStore } from '@/stores/uiStore'
import type { Zone, ZoneCreateRequest, ZoneType } from '@/types'

const FALLBACK_SIZES: [number, number][] = [
  [640, 480],
  [1280, 720],
  [1920, 1080],
]

function formFromZone(zone: Zone | null): ZoneForm {
  return {
    zone_name: zone?.zone_name ?? '',
    zone_type: zone?.zone_type ?? 'restricted',
    loiter_threshold_seconds: String(zone?.loiter_threshold_seconds ?? 30),
    multiplier: String(zone?.night_rules.multiplier ?? NIGHT_WINDOW.multiplier),
    night_start: String(zone?.night_rules.start ?? NIGHT_WINDOW.start),
    night_end: String(zone?.night_rules.end ?? NIGHT_WINDOW.end),
    color_hex: zone?.color_hex ?? ZONE_META.restricted.color,
    allowed_persons: (zone?.allowed_persons ?? []).join(', '),
    is_active: zone?.is_active ?? true,
  }
}

export function ZoneManagerPage() {
  const queryClient = useQueryClient()
  const cameras = useCameras()
  const { canManageZones } = usePermissions()
  const storedCamera = useUiStore((state) => state.selectedCameraId)
  const [chosenCameraId, setCameraId] = useState<string>('')
  const defaultCameraId = (cameras.data?.items.find((camera) => camera.camera_id === storedCamera) ?? cameras.data?.items[0])?.camera_id ?? ''
  const cameraId = chosenCameraId || defaultCameraId
  const [includeInactive, setIncludeInactive] = useState(false)
  const zones = useCameraZones(cameraId || null, includeInactive)
  const liveSize = useLiveStore((state) => (cameraId ? state.frameSizes[cameraId] : undefined))
  const [fallbackSize, setFallbackSize] = useState<[number, number]>([1280, 720])
  const [editing, setEditing] = useState<Zone | 'new' | null>(null)
  const [points, setPoints] = useState<[number, number][]>([])
  const [form, setForm] = useState<ZoneForm>(formFromZone(null))
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Zone | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)


  const [frameWidth, frameHeight] = liveSize ?? fallbackSize

  const startEdit = (zone: Zone | 'new') => {
    setEditing(zone)
    setPoints(zone === 'new' ? [] : zone.polygon.map(([x, y]) => [Math.round(x), Math.round(y)]))
    setForm(formFromZone(zone === 'new' ? null : zone))
  }
  const cancel = () => {
    setEditing(null)
    setPoints([])
    setDragIndex(null)
  }

  const toFrame = (event: PointerEvent<SVGSVGElement>): [number, number] | null => {
    const svg = svgRef.current
    const matrix = svg?.getScreenCTM()
    if (!svg || !matrix) return null
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
    if (point.x < 0 || point.y < 0 || point.x > frameWidth || point.y > frameHeight) return null
    return [Math.round(point.x), Math.round(point.y)]
  }

  const onCanvasPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (!editing || dragIndex !== null) return
    const point = toFrame(event)
    if (point) setPoints((previous) => [...previous, point])
  }
  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (dragIndex === null) return
    const point = toFrame(event)
    if (point) setPoints((previous) => previous.map((existing, index) => (index === dragIndex ? point : existing)))
  }

  const save = useMutation({
    mutationFn: async () => {
      const error = validateZoneForm(form, points)
      if (error) throw new Error(error)
      const payload: ZoneCreateRequest = {
        camera_id: cameraId,
        zone_name: form.zone_name.trim(),
        zone_type: form.zone_type,
        polygon: points,
        loiter_threshold_seconds: Number(form.loiter_threshold_seconds),
        night_rules: { multiplier: Number(form.multiplier), start: Number(form.night_start), end: Number(form.night_end) },
        allowed_persons: form.allowed_persons
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        color_hex: form.color_hex,
        is_active: form.is_active,
      }
      if (editing === 'new') return zonesApi.create(payload)
      if (editing) {
        const { camera_id: _camera, ...update } = payload
        void _camera
        return zonesApi.update(editing.zone_id, update)
      }
      throw new Error('Nothing to save')
    },
    onSuccess: (zone) => {
      toast.success(`Zone "${zone.zone_name}" saved · risk bonus +${zone.risk_bonus}`)
      cancel()
      void queryClient.invalidateQueries({ queryKey: ['zones'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const remove = useMutation({
    mutationFn: (zoneId: number) => zonesApi.remove(zoneId),
    onSuccess: (result) => {
      toast.success(result.message)
      setPendingDelete(null)
      cancel()
      void queryClient.invalidateQueries({ queryKey: ['zones'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const zoneItems = useMemo(() => zones.data?.items ?? [], [zones.data])
  const visibleZones = useMemo(() => zoneItems.filter((zone) => editing === null || editing === 'new' || zone.zone_id !== editing.zone_id), [zoneItems, editing])
  const color = ZONE_META[form.zone_type].color
  const camera = cameras.data?.items.find((item) => item.camera_id === cameraId)

  if (cameras.isLoading) return <Spinner className="py-24" />
  if (cameras.isError) return <ErrorState error={cameras.error} onRetry={() => void cameras.refetch()} />

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="zone-manager">
      <PageHeader
        icon={<Hexagon className="size-4" />}
        title="Zone Manager"
        subtitle="Fence zones drive the risk engine: public +0 · buffer +10 · sensitive +30 · restricted +50 · no man’s land +100"
        actions={
          <>
            <Select value={cameraId} onChange={(event) => { setCameraId(event.target.value); cancel() }} className="h-8 w-44 text-xs" aria-label="Camera">
              {(cameras.data?.items ?? []).map((item) => (
                <option key={item.camera_id} value={item.camera_id}>
                  {item.camera_id} · {item.name}
                </option>
              ))}
            </Select>
            {canManageZones && !editing ? (
              <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => startEdit('new')} disabled={!cameraId} data-testid="new-zone">
                New zone
              </Button>
            ) : null}
          </>
        }
      />

      {!cameraId ? (
        <Card>
          <EmptyState icon={<Hexagon className="size-8" />} title="No cameras" description="Register a camera before defining zones." />
        </Card>
      ) : (
        <div className="grid gap-3 2xl:grid-cols-[1fr_380px]">
          <Card className="overflow-hidden">
            <CardHeader
              title={editing ? (editing === 'new' ? 'Drawing new zone' : `Editing ${editing.zone_name}`) : 'Zones on live feed'}
              subtitle={
                editing
                  ? `Click to add points · drag points to move · ${points.length} point(s) · coordinates in ${frameWidth}×${frameHeight} source pixels`
                  : liveSize
                    ? `Live frame ${frameWidth}×${frameHeight}`
                    : 'No live frame yet — coordinates use the selected resolution'
              }
              actions={
                <div className="flex items-center gap-2">
                  {!liveSize ? (
                    <Select
                      value={`${fallbackSize[0]}x${fallbackSize[1]}`}
                      onChange={(event) => {
                        const size = FALLBACK_SIZES.find(([w, h]) => `${w}x${h}` === event.target.value)
                        if (size) setFallbackSize(size)
                      }}
                      className="h-8 w-32 text-xs"
                      aria-label="Frame resolution"
                    >
                      {FALLBACK_SIZES.map(([w, h]) => (
                        <option key={`${w}x${h}`} value={`${w}x${h}`}>
                          {w}×{h}
                        </option>
                      ))}
                    </Select>
                  ) : null}
                  {editing ? (
                    <Button size="xs" icon={<Undo2 className="size-3" />} disabled={!points.length} onClick={() => setPoints(points.slice(0, -1))}>
                      Undo point
                    </Button>
                  ) : null}
                </div>
              }
            />
            <div className="relative aspect-video bg-command">
              <CameraFeed cameraId={cameraId} cameraName={camera?.name} interactive={false} zones={editing ? visibleZones : zoneItems} />
              {editing ? (
                <svg
                  ref={svgRef}
                  viewBox={`0 0 ${frameWidth} ${frameHeight}`}
                  preserveAspectRatio="xMidYMid meet"
                  className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
                  onPointerDown={onCanvasPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={() => setDragIndex(null)}
                  onPointerLeave={() => setDragIndex(null)}
                  data-testid="zone-editor-canvas"
                >
                  {points.length >= 3 ? (
                    <polygon points={points.map(([x, y]) => `${x},${y}`).join(' ')} fill={`${color}33`} stroke={color} strokeWidth={frameWidth / 400} />
                  ) : points.length === 2 ? (
                    <polyline points={points.map(([x, y]) => `${x},${y}`).join(' ')} fill="none" stroke={color} strokeWidth={frameWidth / 400} />
                  ) : null}
                  {points.map(([x, y], index) => (
                    <circle
                      key={`${index}-${x}-${y}`}
                      cx={x}
                      cy={y}
                      r={frameWidth / 140}
                      fill={index === 0 ? '#ffffff' : color}
                      stroke="#020617"
                      strokeWidth={frameWidth / 600}
                      className="cursor-move"
                      onPointerDown={(event) => {
                        event.stopPropagation()
                        ;(event.currentTarget.ownerSVGElement ?? event.currentTarget).setPointerCapture?.(event.pointerId)
                        setDragIndex(index)
                      }}
                    />
                  ))}
                </svg>
              ) : null}
            </div>
          </Card>

          <div className="flex flex-col gap-3">
            {editing && canManageZones ? (
              <Card>
                <CardHeader
                  title={editing === 'new' ? 'New zone' : 'Edit zone'}
                  actions={
                    <Button size="xs" variant="ghost" icon={<X className="size-3.5" />} onClick={cancel} aria-label="Cancel editing" />
                  }
                />
                <div className="grid gap-3 p-4">
                  <Field label="Zone name" htmlFor="zone-name">
                    <Input id="zone-name" value={form.zone_name} maxLength={100} onChange={(event) => setForm({ ...form, zone_name: event.target.value })} />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Type" htmlFor="zone-type" hint={`Risk bonus +${ZONE_META[form.zone_type].bonus}`}>
                      <Select
                        id="zone-type"
                        value={form.zone_type}
                        onChange={(event) => {
                          const type = event.target.value as ZoneType
                          setForm({ ...form, zone_type: type, color_hex: ZONE_META[type].color })
                        }}
                      >
                        {ZONE_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {ZONE_META[type].label}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Loiter threshold (s)" htmlFor="zone-loiter">
                      <Input id="zone-loiter" inputMode="numeric" value={form.loiter_threshold_seconds} onChange={(event) => setForm({ ...form, loiter_threshold_seconds: event.target.value })} />
                    </Field>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <Field label="Night ×" htmlFor="zone-mult">
                      <Input id="zone-mult" inputMode="decimal" value={form.multiplier} onChange={(event) => setForm({ ...form, multiplier: event.target.value })} />
                    </Field>
                    <Field label="Night from" htmlFor="zone-start">
                      <Input id="zone-start" inputMode="numeric" value={form.night_start} onChange={(event) => setForm({ ...form, night_start: event.target.value })} />
                    </Field>
                    <Field label="Night to" htmlFor="zone-end">
                      <Input id="zone-end" inputMode="numeric" value={form.night_end} onChange={(event) => setForm({ ...form, night_end: event.target.value })} />
                    </Field>
                  </div>
                  <div className="grid grid-cols-[1fr_auto] items-end gap-3">
                    <Field label="Allowed persons (UUIDs, comma separated)" htmlFor="zone-allowed">
                      <Input id="zone-allowed" value={form.allowed_persons} onChange={(event) => setForm({ ...form, allowed_persons: event.target.value })} />
                    </Field>
                    <Field label="Colour" htmlFor="zone-color">
                      <input id="zone-color" type="color" value={form.color_hex} onChange={(event) => setForm({ ...form, color_hex: event.target.value })} className="h-9 w-12 cursor-pointer rounded border border-line" />
                    </Field>
                  </div>
                  <label className="flex items-center justify-between text-xs font-semibold text-slate-700">
                    Active
                    <Switch checked={form.is_active} onCheckedChange={(checked) => setForm({ ...form, is_active: checked })} label="Zone active" />
                  </label>
                  <div className="flex gap-2">
                    <Button variant="primary" size="md" className="flex-1" icon={<Save className="size-4" />} loading={save.isPending} onClick={() => save.mutate()} data-testid="save-zone">
                      Save zone
                    </Button>
                    {editing !== 'new' ? (
                      <Button size="md" variant="ghost" className="text-red-600 hover:bg-red-50" icon={<Trash2 className="size-4" />} onClick={() => setPendingDelete(editing)} aria-label="Deactivate zone" />
                    ) : null}
                  </div>
                </div>
              </Card>
            ) : null}

            <Card>
              <CardHeader
                title={`Zones (${zones.data?.total ?? 0})`}
                actions={
                  <label className="flex items-center gap-2 text-[11px] text-muted">
                    Inactive <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} label="Include inactive zones" />
                  </label>
                }
              />
              {zones.isLoading ? (
                <Spinner className="py-8" />
              ) : zones.isError ? (
                <ErrorState error={zones.error} />
              ) : zoneItems.length === 0 ? (
                <EmptyState title="No zones on this camera" description="Without zones every detection is scored as public area (+0)." />
              ) : (
                <ul>
                  {zoneItems.map((zone) => (
                    <li key={zone.zone_id} className={cn('flex items-center gap-3 border-b border-slate-100 px-4 py-2.5', editing !== 'new' && editing?.zone_id === zone.zone_id && 'bg-cyan-50')}>
                      <span className="size-3 shrink-0 rounded-sm" style={{ background: ZONE_META[zone.zone_type].color }} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">
                          {zone.zone_name} {!zone.is_active ? <Badge>inactive</Badge> : null}
                        </p>
                        <p className="text-[11px] text-muted">
                          {ZONE_META[zone.zone_type].label} · +{zone.risk_bonus} · loiter {formatDuration(zone.loiter_threshold_seconds)} · night ×{zone.night_rules.multiplier} ({zone.night_rules.start}:00–{zone.night_rules.end}:00) · {zone.polygon.length} pts
                        </p>
                      </div>
                      {canManageZones ? (
                        <Button size="xs" icon={<Pencil className="size-3" />} onClick={() => startEdit(zone)} aria-label={`Edit ${zone.zone_name}`} />
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => (open ? undefined : setPendingDelete(null))}
        title={`Deactivate "${pendingDelete?.zone_name ?? ''}"?`}
        description="The zone is soft-deleted (deactivated) and stops affecting risk scores. The ML pipeline picks up zone changes within 30 seconds (its zone cache interval). The action is audit logged."
        confirmLabel="Deactivate zone"
        loading={remove.isPending}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete.zone_id)}
      />
    </div>
  )
}
