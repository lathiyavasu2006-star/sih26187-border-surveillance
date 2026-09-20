import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, Cctv, Plus, Power, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { camerasApi } from '@/api/endpoints'
import { CameraEditButton } from '@/components/camera/CameraEditDialog'
import { CameraGrid } from '@/components/camera/CameraGrid'
import { CameraRegistration } from '@/components/camera/CameraRegistration'
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorState, Input, Modal, PageHeader, Select, Spinner, StatusBadge } from '@/components/ui/primitives'
import { useCameras } from '@/hooks/useData'
import { useKeyboard } from '@/hooks/useKeyboard'
import { ZONE_REGIONS } from '@/lib/constants'
import { formatRelative, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import { useUiStore, type GridSize } from '@/stores/uiStore'
import type { CameraStatus, CameraWithAlertCount, StreamTestResponse } from '@/types'

export function CamerasPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const cameras = useCameras()
  const { canManageCameras, isAdmin } = usePermissions()
  const gridSize = useUiStore((state) => state.gridSize)
  const setGridSize = useUiStore((state) => state.setGridSize)
  const [registerOpen, setRegisterOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [region, setRegion] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [testResult, setTestResult] = useState<StreamTestResponse | null>(null)
  const [pendingDelete, setPendingDelete] = useState<CameraWithAlertCount | null>(null)

  useKeyboard({
    '1': () => setGridSize(1),
    '2': () => setGridSize(2),
    '3': () => setGridSize(3),
    '4': () => setGridSize(4),
  })

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (cameras.data?.items ?? []).filter(
      (camera) =>
        (!region || camera.zone_region === region) &&
        (!statusFilter || camera.status === statusFilter) &&
        (!term ||
          camera.camera_id.toLowerCase().includes(term) ||
          camera.name.toLowerCase().includes(term) ||
          (camera.location_name ?? '').toLowerCase().includes(term) ||
          (camera.sector_name ?? '').toLowerCase().includes(term)),
    )
  }, [cameras.data, search, region, statusFilter])

  const test = useMutation({
    mutationFn: (cameraId: string) => camerasApi.test(cameraId),
    onSuccess: (result) => setTestResult(result),
    onError: (error: Error) => toast.error(error.message),
  })
  const setStatus = useMutation({
    mutationFn: ({ cameraId, status }: { cameraId: string; status: CameraStatus }) => camerasApi.setStatus(cameraId, status),
    onSuccess: (camera) => {
      toast.success(`${camera.camera_id}: ${camera.previous_status} → ${camera.status}`)
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const remove = useMutation({
    mutationFn: (cameraId: string) => camerasApi.remove(cameraId),
    onSuccess: (result) => {
      toast.success(result.message)
      setPendingDelete(null)
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="cameras-page">
      <PageHeader
        icon={<Cctv className="size-4" />}
        title="Live Cameras"
        subtitle={cameras.data ? `${cameras.data.total} registered · ${cameras.data.items.filter((camera) => camera.status === 'online').length} online` : undefined}
        actions={
          <>
            <div className="flex items-center gap-1 rounded-lg border border-line bg-white p-1">
              {([1, 2, 3, 4] as GridSize[]).map((size) => (
                <button
                  key={size}
                  type="button"
                  onClick={() => setGridSize(size)}
                  className={`rounded-md px-2 py-1 font-mono text-[11px] font-semibold ${gridSize === size ? 'bg-slate-900 text-hud' : 'text-slate-600 hover:bg-slate-100'}`}
                >
                  {size}×{size}
                </button>
              ))}
            </div>
            {canManageCameras ? (
              <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setRegisterOpen(true)} data-testid="register-camera">
                Register camera
              </Button>
            ) : null}
          </>
        }
      />

      {cameras.isLoading ? (
        <Spinner className="py-20" />
      ) : cameras.isError ? (
        <ErrorState error={cameras.error} onRetry={() => void cameras.refetch()} />
      ) : !cameras.data?.items.length ? (
        <Card>
          <EmptyState
            icon={<Cctv className="size-10" />}
            title="No cameras registered"
            description="Register an RTSP camera or a local device to start live monitoring."
            action={canManageCameras ? <Button variant="primary" onClick={() => setRegisterOpen(true)}>Register camera</Button> : undefined}
          />
        </Card>
      ) : (
        <>
          <CameraGrid cameras={filtered} size={gridSize} />
          <Card>
            <CardHeader
              title="Camera registry"
              actions={
                <div className="flex items-center gap-2">
                  <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search…" className="h-8 w-48 text-xs" aria-label="Search cameras" />
                  <Select value={region} onChange={(event) => setRegion(event.target.value)} className="h-8 w-32 text-xs" aria-label="Region filter">
                    <option value="">All regions</option>
                    {ZONE_REGIONS.map((value) => (
                      <option key={value} value={value}>
                        {titleCase(value)}
                      </option>
                    ))}
                  </Select>
                  <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="h-8 w-32 text-xs" aria-label="Status filter">
                    <option value="">All statuses</option>
                    <option value="online">Online</option>
                    <option value="degraded">Degraded</option>
                    <option value="offline">Offline</option>
                  </Select>
                </div>
              }
            />
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-line bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
                  <tr>
                    <th className="px-4 py-2">Camera</th>
                    <th className="px-4 py-2">Type</th>
                    <th className="px-4 py-2">Region / location</th>
                    <th className="px-4 py-2">Stream</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Last seen</th>
                    <th className="px-4 py-2">Alerts</th>
                    <th className="px-4 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((camera) => (
                    <tr key={camera.camera_id} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="px-4 py-2">
                        <button type="button" onClick={() => navigate(`/cameras/${camera.camera_id}`)} className="text-left">
                          <span className="block font-mono font-semibold text-cyan-700">{camera.camera_id}</span>
                          <span className="text-muted">{camera.name}</span>
                        </button>
                      </td>
                      <td className="px-4 py-2">{titleCase(camera.camera_type)}</td>
                      <td className="px-4 py-2">
                        {titleCase(camera.zone_region)}
                        {camera.sector_name ? ` · ${camera.sector_name}` : ''}
                        <span className="block text-[10px] text-muted">
                          {camera.location_name ?? 'No location'}
                          {camera.gps_lat !== null && camera.gps_lng !== null ? ` · ${camera.gps_lat.toFixed(4)}, ${camera.gps_lng.toFixed(4)}` : ' · no GPS'}
                        </span>
                      </td>
                      <td className="max-w-56 truncate px-4 py-2 font-mono text-[11px] text-muted" title={camera.rtsp_url ?? camera.device_id ?? ''}>
                        {camera.rtsp_url ?? (camera.device_id ? `device ${camera.device_id}` : '—')}
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={camera.status} />
                      </td>
                      <td className="px-4 py-2 text-muted">{formatRelative(camera.last_seen)}</td>
                      <td className="px-4 py-2">{camera.active_alert_count > 0 ? <Badge className="bg-red-50 text-red-700 ring-red-200">{camera.active_alert_count}</Badge> : '0'}</td>
                      <td className="px-4 py-2">
                        <div className="flex justify-end gap-1">
                          {canManageCameras ? (
                            <>
                              <CameraEditButton camera={camera} />
                              <Button size="xs" icon={<Activity className="size-3" />} loading={test.isPending && test.variables === camera.camera_id} onClick={() => test.mutate(camera.camera_id)}>
                                Test
                              </Button>
                              <Button
                                size="xs"
                                icon={<Power className="size-3" />}
                                onClick={() => setStatus.mutate({ cameraId: camera.camera_id, status: camera.status === 'offline' ? 'online' : 'offline' })}
                              >
                                {camera.status === 'offline' ? 'Set online' : 'Set offline'}
                              </Button>
                            </>
                          ) : null}
                          {isAdmin ? (
                            <Button size="xs" variant="ghost" className="text-red-600 hover:bg-red-50" icon={<Trash2 className="size-3" />} onClick={() => setPendingDelete(camera)} aria-label={`Delete ${camera.camera_id}`} />
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      <CameraRegistration open={registerOpen} onOpenChange={setRegisterOpen} />

      <Modal open={testResult !== null} onOpenChange={(open) => (open ? undefined : setTestResult(null))} title={`Stream test · ${testResult?.camera_id ?? ''}`} size="sm">
        {testResult ? (
          <div className="flex flex-col gap-3 text-sm">
            <StatusBadge status={testResult.connected ? 'connected' : 'disconnected'} />
            <p className="text-slate-600">{testResult.message}</p>
            <dl className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <dt className="text-muted">FPS</dt>
                <dd className="font-mono font-semibold">{testResult.fps.toFixed(1)}</dd>
              </div>
              <div>
                <dt className="text-muted">Resolution</dt>
                <dd className="font-mono font-semibold">{testResult.resolution ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted">Latency</dt>
                <dd className="font-mono font-semibold">{testResult.latency_ms} ms</dd>
              </div>
            </dl>
            {testResult.frame_preview ? (
              <img src={`data:image/jpeg;base64,${testResult.frame_preview}`} alt="Stream preview" className="aspect-video w-full rounded-lg bg-slate-900 object-contain" />
            ) : null}
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => (open ? undefined : setPendingDelete(null))}
        title={`Delete ${pendingDelete?.camera_id ?? ''}?`}
        description="Deletion is refused while evidence exists for the camera. Cameras with recorded alerts or events are decommissioned (status offline) and their history kept; otherwise the camera is removed. The action is audit logged."
        confirmLabel="Delete camera"
        loading={remove.isPending}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete.camera_id)}
      />
    </div>
  )
}
