import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, Cpu, Plus, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { hardwareApi } from '@/api/endpoints'
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Select, Spinner, StatCard, StatusBadge, Textarea } from '@/components/ui/primitives'
import { useCameras, useHardwareStatus } from '@/hooks/useData'
import { HARDWARE_TYPES } from '@/lib/constants'
import { formatRelative, isRecord, titleCase } from '@/lib/utils'
import { usePermissions } from '@/stores/authStore'
import type { Hardware, HardwareStatus, HardwareType, JsonObject, JsonValue } from '@/types'

function ConfigValue({ value }: { value: JsonValue }) {
  if (isRecord(value)) {
    return (
      <span className="font-mono">
        {'{'}
        {Object.entries(value)
          .map(([key, inner]) => `${key}: ${typeof inner === 'object' ? JSON.stringify(inner) : String(inner)}`)
          .join(', ')}
        {'}'}
      </span>
    )
  }
  return <span className="font-mono">{Array.isArray(value) ? JSON.stringify(value) : String(value)}</span>
}

export function HardwarePage() {
  const status = useHardwareStatus()
  const queryClient = useQueryClient()
  const { isAdmin, canTestHardware } = usePermissions()
  const [registerOpen, setRegisterOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<Hardware | null>(null)

  const test = useMutation({
    mutationFn: (hardwareId: number) => hardwareApi.testConnection(hardwareId),
    onSuccess: (result) => {
      if (result.connected) toast.success(`#${result.hardware_id}: connected · ${result.latency_ms} ms`)
      else toast.error(`#${result.hardware_id}: ${result.message}`)
      void queryClient.invalidateQueries({ queryKey: ['hardware'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const remove = useMutation({
    mutationFn: (hardwareId: number) => hardwareApi.remove(hardwareId),
    onSuccess: () => {
      toast.success('Hardware removed')
      setPendingDelete(null)
      void queryClient.invalidateQueries({ queryKey: ['hardware'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const groups = Object.entries(status.data ?? {}).filter(([, summary]) => summary.total > 0)
  const totals = Object.values(status.data ?? {}).reduce(
    (sum, summary) => ({
      total: sum.total + summary.total,
      connected: sum.connected + summary.connected,
      disconnected: sum.disconnected + summary.disconnected,
      error: sum.error + summary.error,
      standby: sum.standby + summary.standby,
    }),
    { total: 0, connected: 0, disconnected: 0, error: 0, standby: 0 },
  )

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="hardware-page">
      <PageHeader
        icon={<Cpu className="size-4" />}
        title="Hardware Registry"
        subtitle="Cameras, drones, radar, scanners and satellite links · connection secrets are masked by the server"
        actions={
          isAdmin ? (
            <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setRegisterOpen(true)}>
              Register hardware
            </Button>
          ) : null
        }
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="Devices" value={totals.total} loading={status.isLoading} />
        <StatCard label="Connected" value={totals.connected} tone="good" loading={status.isLoading} />
        <StatCard label="Standby" value={totals.standby} tone="warning" loading={status.isLoading} />
        <StatCard label="Disconnected" value={totals.disconnected} tone={totals.disconnected ? 'critical' : 'default'} loading={status.isLoading} />
        <StatCard label="Error" value={totals.error} tone={totals.error ? 'critical' : 'default'} loading={status.isLoading} />
      </div>

      {status.isLoading ? (
        <Spinner className="py-16" />
      ) : status.isError ? (
        <ErrorState error={status.error} onRetry={() => void status.refetch()} />
      ) : groups.length === 0 ? (
        <Card>
          <EmptyState icon={<Cpu className="size-10" />} title="No hardware registered" description={isAdmin ? 'Register sensors and devices to monitor their connectivity.' : 'An administrator can register sensors and devices.'} />
        </Card>
      ) : (
        groups.map(([type, summary]) => (
          <Card key={type}>
            <CardHeader
              title={titleCase(type)}
              subtitle={`${summary.connected}/${summary.total} connected · ${summary.standby} standby · ${summary.disconnected} disconnected · ${summary.error} error`}
            />
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-line bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
                  <tr>
                    <th className="px-4 py-2">Device</th>
                    <th className="px-4 py-2">Model</th>
                    <th className="px-4 py-2">Camera</th>
                    <th className="px-4 py-2">Connection (masked)</th>
                    <th className="px-4 py-2">Capabilities</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Last seen</th>
                    <th className="px-4 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.items.map((device) => (
                    <tr key={device.hardware_id} className="border-b border-slate-100 align-top">
                      <td className="px-4 py-2">
                        <p className="font-semibold">{device.name}</p>
                        <p className="font-mono text-[10px] text-muted">#{device.hardware_id} · fw {device.firmware_version ?? '—'}</p>
                      </td>
                      <td className="px-4 py-2">
                        {device.manufacturer ?? '—'} {device.model_number ?? ''}
                      </td>
                      <td className="px-4 py-2 font-mono">{device.camera_id ?? '—'}</td>
                      <td className="max-w-72 px-4 py-2 text-[11px] text-slate-600">
                        {Object.keys(device.connection_config).length ? <ConfigValue value={device.connection_config} /> : '—'}
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-wrap gap-1">
                          {device.capabilities.length ? device.capabilities.map((capability) => <Badge key={capability}>{capability}</Badge>) : '—'}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={device.status} />
                      </td>
                      <td className="px-4 py-2 text-muted">{formatRelative(device.last_seen)}</td>
                      <td className="px-4 py-2">
                        <div className="flex justify-end gap-1">
                          {canTestHardware ? (
                            <Button size="xs" icon={<Activity className="size-3" />} loading={test.isPending && test.variables === device.hardware_id} onClick={() => test.mutate(device.hardware_id)}>
                              Test
                            </Button>
                          ) : null}
                          {isAdmin ? (
                            <Button size="xs" variant="ghost" className="text-red-600 hover:bg-red-50" icon={<Trash2 className="size-3" />} onClick={() => setPendingDelete(device)} aria-label={`Delete ${device.name}`} />
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}

      <RegisterHardwareDialog open={registerOpen} onOpenChange={setRegisterOpen} />
      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => (open ? undefined : setPendingDelete(null))}
        title={`Remove ${pendingDelete?.name ?? ''}?`}
        description="The device is removed from the registry. The action is audit logged."
        confirmLabel="Remove"
        loading={remove.isPending}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete.hardware_id)}
      />
    </div>
  )
}

function RegisterHardwareDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const cameras = useCameras()
  const [type, setType] = useState<HardwareType>('standard_camera')
  const [name, setName] = useState('')
  const [manufacturer, setManufacturer] = useState('')
  const [model, setModel] = useState('')
  const [firmware, setFirmware] = useState('')
  const [cameraId, setCameraId] = useState('')
  const [statusValue, setStatusValue] = useState<HardwareStatus>('standby')
  const [capabilities, setCapabilities] = useState('')
  const [config, setConfig] = useState('{\n  "host": "",\n  "port": 554\n}')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)

  const register = useMutation({
    mutationFn: (connection: JsonObject) =>
      hardwareApi.register({
        hardware_type: type,
        name: name.trim(),
        manufacturer: manufacturer.trim() || null,
        model_number: model.trim() || null,
        firmware_version: firmware.trim() || null,
        camera_id: cameraId || null,
        status: statusValue,
        capabilities: capabilities
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        connection_config: connection,
        notes: notes.trim() || null,
      }),
    onSuccess: (device) => {
      toast.success(`${device.name} registered (#${device.hardware_id})`)
      void queryClient.invalidateQueries({ queryKey: ['hardware'] })
      onOpenChange(false)
      setName('')
      setConfig('{\n  "host": "",\n  "port": 554\n}')
      setError(null)
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!name.trim()) return setError('Name is required')
    let parsed: unknown
    try {
      parsed = JSON.parse(config || '{}')
    } catch {
      return setError('Connection config must be valid JSON')
    }
    if (!isRecord(parsed)) return setError('Connection config must be a JSON object')
    register.mutate(parsed as JsonObject)
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      icon={<Cpu className="size-5" />}
      title="Register hardware"
      description="Passwords, tokens and keys in the connection config are stored server-side and never returned unmasked."
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" type="submit" form="register-hardware" loading={register.isPending}>
            Register
          </Button>
        </>
      }
    >
      <form id="register-hardware" onSubmit={submit} className="grid gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Type" htmlFor="hw-type">
            <Select id="hw-type" value={type} onChange={(event) => setType(event.target.value as HardwareType)}>
              {HARDWARE_TYPES.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Name" htmlFor="hw-name">
            <Input id="hw-name" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Manufacturer" htmlFor="hw-mfr">
            <Input id="hw-mfr" value={manufacturer} maxLength={100} onChange={(event) => setManufacturer(event.target.value)} />
          </Field>
          <Field label="Model" htmlFor="hw-model">
            <Input id="hw-model" value={model} maxLength={100} onChange={(event) => setModel(event.target.value)} />
          </Field>
          <Field label="Firmware" htmlFor="hw-fw">
            <Input id="hw-fw" value={firmware} maxLength={50} onChange={(event) => setFirmware(event.target.value)} />
          </Field>
          <Field label="Linked camera" htmlFor="hw-camera">
            <Select id="hw-camera" value={cameraId} onChange={(event) => setCameraId(event.target.value)}>
              <option value="">None</option>
              {(cameras.data?.items ?? []).map((camera) => (
                <option key={camera.camera_id} value={camera.camera_id}>
                  {camera.camera_id}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Initial status" htmlFor="hw-status">
            <Select id="hw-status" value={statusValue} onChange={(event) => setStatusValue(event.target.value as HardwareStatus)}>
              {(['standby', 'connected', 'disconnected', 'error'] as HardwareStatus[]).map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Capabilities" htmlFor="hw-cap" hint="Comma separated, e.g. night_vision, zoom">
            <Input id="hw-cap" value={capabilities} onChange={(event) => setCapabilities(event.target.value)} />
          </Field>
        </div>
        <Field label="Connection config (JSON)" htmlFor="hw-config">
          <Textarea id="hw-config" value={config} onChange={(event) => setConfig(event.target.value)} className="min-h-28 font-mono text-xs" spellCheck={false} autoComplete="off" />
        </Field>
        <Field label="Notes" htmlFor="hw-notes">
          <Textarea id="hw-notes" value={notes} maxLength={5000} onChange={(event) => setNotes(event.target.value)} />
        </Field>
        {error ? (
          <p className="text-xs text-red-600" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  )
}
