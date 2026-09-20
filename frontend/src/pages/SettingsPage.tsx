import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eraser, KeyRound, Settings, ShieldCheck, SlidersHorizontal, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import toast from 'react-hot-toast'
import { alertsApi, authApi, statsApi } from '@/api/endpoints'
import { MAP_STYLE_DEFINITIONS } from '@/components/map/mapStyles'
import { Badge, Button, Card, CardHeader, ConfirmDialog, EmptyState, ErrorState, Field, Input, PageHeader, Select, Spinner, Switch } from '@/components/ui/primitives'
import { playAlarm, unlockAudio } from '@/lib/alarm'
import { ROLE_LABEL } from '@/lib/constants'
import { passwordProblems } from '@/lib/validation'
import { formatDateTime, titleCase } from '@/lib/utils'
import { useAuthStore, usePermissions } from '@/stores/authStore'
import { MAP_STYLES, useUiStore, type MapStyle } from '@/stores/uiStore'

function ChangePassword() {
  const user = useAuthStore((state) => state.user)
  const clearSession = useAuthStore((state) => state.clearSession)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const problems = next ? passwordProblems(next, user?.username ?? '') : []

  const change = useMutation({
    mutationFn: () => authApi.changePassword(current, next),
    onSuccess: (response) => {
      toast.success(response.message)
      // The backend revokes the current access token: sign in again with the new password.
      clearSession('Password changed — sign in with your new password')
    },
    onError: (caught: Error) => setError(caught.message),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!current) return setError('Enter your current password')
    if (problems.length) return setError(`New password needs ${problems.join(', ')}`)
    if (next !== confirm) return setError('The new passwords do not match')
    if (next === current) return setError('The new password must differ from the current one')
    change.mutate()
  }

  return (
    <form onSubmit={submit} className="grid gap-3 p-4">
      <Field label="Current password" htmlFor="pw-current">
        <Input id="pw-current" type="password" autoComplete="current-password" value={current} maxLength={72} onChange={(event) => setCurrent(event.target.value)} />
      </Field>
      <Field label="New password" htmlFor="pw-new" hint={next && problems.length ? `Needs ${problems.join(', ')}` : '12+ characters with upper, lower, digit and special character; no spaces'}>
        <Input id="pw-new" type="password" autoComplete="new-password" value={next} maxLength={72} onChange={(event) => setNext(event.target.value)} />
      </Field>
      <Field label="Confirm new password" htmlFor="pw-confirm">
        <Input id="pw-confirm" type="password" autoComplete="new-password" value={confirm} maxLength={72} onChange={(event) => setConfirm(event.target.value)} />
      </Field>
      {error ? (
        <p className="text-xs text-red-600" role="alert">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant="primary" size="md" loading={change.isPending} icon={<KeyRound className="size-4" />}>
        Change password
      </Button>
      <p className="text-[11px] text-muted">A wrong current password counts toward the 3-attempt account lockout.</p>
    </form>
  )
}

function AuditLog() {
  const [action, setAction] = useState('')
  const [status, setStatus] = useState('')
  const log = useQuery({
    queryKey: ['stats', 'audit', action, status],
    queryFn: () => statsApi.auditLog({ action: action.trim() || undefined, status: (status || undefined) as 'success' | undefined, limit: 100 }),
    placeholderData: (previous) => previous,
  })
  return (
    <Card>
      <CardHeader
        title="Audit trail"
        subtitle={log.data ? `${log.data.total} entries · newest 100 shown · values masked by the server` : undefined}
        icon={<ShieldCheck className="size-4" />}
        actions={
          <div className="flex gap-2">
            <Input value={action} onChange={(event) => setAction(event.target.value.toUpperCase())} placeholder="Action (e.g. LOGIN_SUCCESS)" className="h-8 w-56 font-mono text-xs" aria-label="Audit action" />
            <Select value={status} onChange={(event) => setStatus(event.target.value)} className="h-8 w-32 text-xs" aria-label="Audit status">
              <option value="">All outcomes</option>
              {['success', 'failure', 'denied', 'error'].map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      {log.isLoading ? (
        <Spinner className="py-10" />
      ) : log.isError ? (
        <ErrorState error={log.error} />
      ) : !log.data?.items.length ? (
        <EmptyState title="No audit entries match" />
      ) : (
        <div className="max-h-[480px] overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wider text-muted">
              <tr>
                <th className="px-4 py-2">Time</th>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2">Action</th>
                <th className="px-4 py-2">Record</th>
                <th className="px-4 py-2">IP</th>
                <th className="px-4 py-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {log.data.items.map((entry) => (
                <tr key={entry.log_id} className="border-t border-slate-100">
                  <td className="whitespace-nowrap px-4 py-1.5">{formatDateTime(entry.timestamp)}</td>
                  <td className="px-4 py-1.5">{entry.username ?? '—'}</td>
                  <td className="px-4 py-1.5 font-mono">{entry.action}</td>
                  <td className="max-w-56 truncate px-4 py-1.5 font-mono text-muted" title={entry.record_id ?? ''}>
                    {entry.table_name ? `${entry.table_name}/` : ''}
                    {entry.record_id ?? ''}
                  </td>
                  <td className="px-4 py-1.5 font-mono">{entry.ip_address ?? '—'}</td>
                  <td className="px-4 py-1.5">
                    <Badge className={entry.status === 'success' ? 'bg-green-50 text-green-700 ring-green-200' : 'bg-red-50 text-red-700 ring-red-200'}>{entry.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

/** Admin: closes every unacknowledged alert raised before today (IST) as a false alarm. Nothing is deleted. */
function ClearTestData() {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const clear = useMutation({
    mutationFn: () => alertsApi.clearTestData(),
    onSuccess: (result) => {
      toast.success(result.message)
      setConfirming(false)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      void queryClient.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })
  return (
    <Card>
      <CardHeader title="Data maintenance" subtitle="Administrator only" icon={<Eraser className="size-4" />} />
      <div className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="max-w-2xl text-xs text-slate-600">
          <p className="text-sm font-semibold text-slate-900">Clear test data</p>
          <p className="mt-1">
            Closes every <b>unacknowledged</b> alert raised before today (00:00 IST) as a false alarm noted &ldquo;test data&rdquo;, so ACTIVE ALERTS
            shows only real, current alerts. Alerts are not deleted: snapshot evidence references them and their SHA-256 chain of custody must stay
            intact. The action is recorded in the audit log.
          </p>
        </div>
        <Button variant="danger" icon={<Eraser className="size-3.5" />} onClick={() => setConfirming(true)} data-testid="clear-test-data">
          Clear test data
        </Button>
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Clear test data?"
        description="All unacknowledged alerts raised before today will be marked acknowledged as false alarms (test data). Today's alerts are not touched. This is audit logged."
        confirmLabel="Clear test data"
        loading={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Card>
  )
}

export function SettingsPage() {
  const { isAdmin } = usePermissions()
  const profile = useQuery({ queryKey: ['auth', 'me'], queryFn: authApi.me })
  const ui = useUiStore()

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="settings-page">
      <PageHeader icon={<Settings className="size-4" />} title="Settings" subtitle="Account security and console preferences (preferences are stored on this device only)" />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Profile" icon={<UserRound className="size-4" />} />
          {profile.isLoading ? (
            <Spinner className="py-10" />
          ) : profile.isError ? (
            <ErrorState error={profile.error} />
          ) : profile.data ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-4 text-xs">
              <div>
                <dt className="text-muted">Username</dt>
                <dd className="font-semibold">{profile.data.username}</dd>
              </div>
              <div>
                <dt className="text-muted">Role</dt>
                <dd className="font-semibold">{ROLE_LABEL[profile.data.role]}</dd>
              </div>
              <div>
                <dt className="text-muted">Camera access</dt>
                <dd className="font-semibold">{profile.data.camera_access.length ? profile.data.camera_access.join(', ') : 'All cameras'}</dd>
              </div>
              <div>
                <dt className="text-muted">Region access</dt>
                <dd className="font-semibold">{profile.data.zone_access.length ? profile.data.zone_access.map(titleCase).join(', ') : 'All regions'}</dd>
              </div>
              <div>
                <dt className="text-muted">Last login</dt>
                <dd className="font-semibold">{formatDateTime(profile.data.last_login)}</dd>
              </div>
              <div>
                <dt className="text-muted">Account created</dt>
                <dd className="font-semibold">{formatDateTime(profile.data.created_at)}</dd>
              </div>
              <div>
                <dt className="text-muted">Failed attempts</dt>
                <dd className="font-semibold">{profile.data.failed_login_attempts}</dd>
              </div>
              <div>
                <dt className="text-muted">Status</dt>
                <dd className="font-semibold">{profile.data.is_active ? 'Active' : 'Disabled'}</dd>
              </div>
            </dl>
          ) : null}
        </Card>

        <Card>
          <CardHeader title="Change password" icon={<KeyRound className="size-4" />} />
          <ChangePassword />
        </Card>

        <Card>
          <CardHeader title="Console preferences" icon={<SlidersHorizontal className="size-4" />} />
          <div className="grid gap-4 p-4 text-xs">
            <label className="flex items-center justify-between gap-3 font-semibold text-slate-700">
              Alarm sounds
              <span className="flex items-center gap-2">
                <Button
                  size="xs"
                  onClick={() => {
                    unlockAudio()
                    window.setTimeout(() => playAlarm('critical'), 50)
                  }}
                >
                  Test
                </Button>
                <Switch checked={ui.soundEnabled} onCheckedChange={ui.setSoundEnabled} label="Alarm sounds" />
              </span>
            </label>
            <label className="flex items-center justify-between font-semibold text-slate-700">
              Tactical HUD overlay (H)
              <Switch checked={ui.hudEnabled} onCheckedChange={() => ui.toggleHud()} label="Tactical HUD overlay" />
            </label>
            <label className="flex items-center justify-between font-semibold text-slate-700">
              Pinned sidebar (Ctrl+B)
              <Switch checked={ui.sidebarMode === 'pinned'} onCheckedChange={() => ui.toggleSidebarPinned()} label="Pinned sidebar" />
            </label>
            <label className="flex items-center justify-between font-semibold text-slate-700">
              Right panel (Alt+R)
              <Switch checked={ui.rightPanelOpen} onCheckedChange={() => ui.toggleRightPanel()} label="Right panel" />
            </label>
            <Field label="Auto-lock after inactivity" htmlFor="auto-lock">
              <Select id="auto-lock" value={String(ui.autoLockMinutes)} onChange={(event) => ui.setAutoLockMinutes(Number(event.target.value))}>
                {[0, 2, 5, 10, 15, 30, 60].map((minutes) => (
                  <option key={minutes} value={minutes}>
                    {minutes === 0 ? 'Never' : `${minutes} minutes`}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Map style (M cycles)" htmlFor="map-style">
              <Select id="map-style" value={ui.mapStyle} onChange={(event) => ui.setMapStyle(event.target.value as MapStyle)}>
                {MAP_STYLES.map((style) => (
                  <option key={style} value={style}>
                    {MAP_STYLE_DEFINITIONS[style].label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </Card>
      </div>
      {isAdmin ? <ClearTestData /> : null}
      {isAdmin ? <AuditLog /> : null}
    </div>
  )
}
