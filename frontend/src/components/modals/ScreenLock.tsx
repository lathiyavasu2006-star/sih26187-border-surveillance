import { Eye, EyeOff, Lock, LogOut, ShieldAlert } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '@/api/client'
import { authApi } from '@/api/endpoints'
import { Button } from '@/components/ui/primitives'
import { useNow } from '@/hooks/useData'
import { formatDate, formatRelative, formatTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useLiveStore } from '@/stores/liveStore'

/**
 * Screen lock (Ctrl+L or 5 minutes without input). The console stays rendered underneath but is blurred
 * (backdrop-filter 20px) behind an 85 % black overlay, so camera feeds cannot be read. Unlocking
 * re-authenticates against POST /auth/login: the audit log, rate limit and 3-strike lockout all apply.
 * Live monitoring keeps running; the lock screen counts notifications that arrived while locked.
 */
export function ScreenLock({ onSignOut }: { onSignOut: () => void }) {
  const user = useAuthStore((state) => state.user)
  const lockedAt = useAuthStore((state) => state.lockedAt)
  const unlock = useAuthStore((state) => state.unlock)
  const notifications = useLiveStore((state) => state.notifications)
  const now = useNow(1000)
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const sinceLock = notifications.filter((item) => lockedAt !== null && item.receivedAt >= lockedAt)
  const critical = sinceLock.filter((item) => item.severity === 'critical').length

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!user || !password || busy) return
    setBusy(true)
    setError(null)
    try {
      const response = await authApi.login(user.username, password)
      setPassword('')
      unlock(response)
    } catch (caught) {
      const apiError = caught instanceof ApiError ? caught : null
      if (apiError?.status === 423) setError(apiError.message)
      else if (apiError?.status === 429) setError(`Too many attempts — wait ${apiError.retryAfterSeconds ?? 60}s`)
      else if (apiError?.status === 401) setError(apiError.message.includes('locked') ? apiError.message : 'Incorrect password')
      else setError(apiError?.message ?? 'Unlock failed')
      setPassword('')
      inputRef.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[5000] flex items-center justify-center text-slate-100"
      style={{ background: 'rgba(0, 0, 0, 0.85)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)' }}
      role="dialog"
      aria-modal="true"
      aria-label="Screen locked"
      data-testid="screen-lock"
    >
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col items-center px-6 text-center">
        <div className="flex size-20 items-center justify-center rounded-full border border-cyan-400/40 bg-cyan-400/10 text-hud shadow-[0_0_40px_rgba(34,211,238,0.25)]">
          <Lock className="size-9" aria-hidden />
        </div>
        <h1 className="mt-5 font-mono text-2xl font-bold tracking-[0.3em] text-white">SCREEN LOCKED</h1>
        <p className="mt-1 text-sm text-slate-400">SIH26187 Border Surveillance</p>
        <p className="mt-4 font-mono text-4xl font-semibold tabular-nums text-white">{formatTime(now).slice(0, 5)}</p>
        <p className="text-xs text-slate-500">
          {formatDate(now)} IST · {user?.username} · locked {formatRelative(lockedAt, now)}
        </p>

        {sinceLock.length > 0 ? (
          <div className={`mt-4 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs ${critical ? 'bg-red-500/15 text-red-200' : 'bg-amber-500/10 text-amber-200'}`}>
            <ShieldAlert className="size-4 shrink-0" />
            {sinceLock.length} notification{sinceLock.length === 1 ? '' : 's'} while locked{critical ? ` · ${critical} critical` : ''}
          </div>
        ) : null}

        <div className="relative mt-6 w-full">
          <label htmlFor="unlock-password" className="sr-only">
            Password
          </label>
          <input
            id="unlock-password"
            ref={inputRef}
            type={reveal ? 'text' : 'password'}
            autoComplete="current-password"
            placeholder="Password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="h-11 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-4 pr-10 text-center text-sm text-white placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-500/30"
            data-testid="unlock-password"
          />
          <button type="button" onClick={() => setReveal(!reveal)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-white" aria-label={reveal ? 'Hide password' : 'Show password'}>
            {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
        {error ? (
          <p className="mt-2 text-xs text-red-300" role="alert">
            {error}
          </p>
        ) : (
          <p className="mt-2 text-[11px] text-slate-500">3 failed attempts lock the account for 30 minutes.</p>
        )}
        <Button type="submit" variant="hud" size="md" loading={busy} disabled={!password} className="mt-4 w-full border-cyan-400 bg-cyan-400 text-slate-950 hover:bg-cyan-300" data-testid="unlock-submit">
          Unlock
        </Button>
        <button type="button" onClick={onSignOut} className="mt-3 flex items-center gap-1.5 text-xs text-slate-400 hover:text-white">
          <LogOut className="size-3.5" /> Sign out instead
        </button>
      </form>
    </div>
  )
}
