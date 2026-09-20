import { Eye, EyeOff, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { authApi, statsApi } from '@/api/endpoints'
import { StatusDot } from '@/components/ui/primitives'
import { useNow } from '@/hooks/useData'
import { ORG_NAME } from '@/lib/constants'
import { formatDate, formatTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import type { HealthResponse } from '@/types'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const signedIn = useAuthStore((state) => Boolean(state.accessToken && state.user))
  const setSession = useAuthStore((state) => state.setSession)
  const notice = useAuthStore((state) => state.sessionMessage)
  const consumeSessionMessage = useAuthStore((state) => state.consumeSessionMessage)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthError, setHealthError] = useState(false)
  const userRef = useRef<HTMLInputElement>(null)
  const now = useNow(1000)

  useEffect(() => {
    userRef.current?.focus()
    let cancelled = false
    statsApi
      .health()
      .then((response) => !cancelled && setHealth(response))
      .catch(() => !cancelled && setHealthError(true))
    return () => {
      cancelled = true
    }
  }, [])

  const from = (location.state as { from?: string } | null)?.from
  if (signedIn) return <Navigate to={from && from !== '/login' ? from : '/dashboard'} replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    const name = username.trim()
    if (!name || !password) {
      setError('Enter your username and password')
      return
    }
    setBusy(true)
    setError(null)
    consumeSessionMessage()
    try {
      const response = await authApi.login(name, password)
      setSession(response)
      setPassword('')
      navigate(from && from !== '/login' ? from : '/dashboard', { replace: true })
    } catch (caught) {
      const apiError = caught instanceof ApiError ? caught : null
      if (apiError?.status === 429) setError(`Too many sign-in attempts. Try again in ${apiError.retryAfterSeconds ?? 60} seconds.`)
      else if (apiError?.status === 0) setError(apiError.message)
      else setError(apiError?.message ?? 'Sign-in failed')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  const serverStatus = healthError ? 'down' : (health?.status ?? 'idle')

  return (
    <div className="grid h-full grid-cols-1 bg-white lg:grid-cols-[1.1fr_1fr]">
      <section className="hud-grid-bg relative hidden flex-col justify-between overflow-hidden p-10 text-slate-100 lg:flex">
        <div className="pointer-events-none absolute inset-0">
          <div className="animate-scan absolute inset-x-0 h-1/3 bg-gradient-to-b from-transparent via-cyan-400/5 to-transparent" />
        </div>
        <div className="relative flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl border border-cyan-400/40 bg-cyan-400/10">
            <ShieldCheck className="size-6 text-hud" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-widest">BORDER SURVEILLANCE COMMAND</p>
            <p className="text-xs text-slate-400">{ORG_NAME}</p>
          </div>
        </div>
        <div className="relative">
          <p className="font-mono text-7xl font-semibold tabular-nums">{formatTime(now)}</p>
          <p className="mt-2 font-mono text-sm text-slate-400">{formatDate(now)} · INDIAN STANDARD TIME</p>
          <div className="mt-10 grid max-w-lg grid-cols-3 gap-3 font-mono text-xs">
            {[
              ['DETECTION', 'YOLOv8x · TensorRT'],
              ['TRACKING', 'ByteTrack · per camera'],
              ['EVIDENCE', 'SHA-256 chain of custody'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-3">
                <p className="text-[10px] tracking-widest text-cyan-300">{label}</p>
                <p className="mt-1 text-slate-200">{value}</p>
              </div>
            ))}
          </div>
        </div>
        <p className="relative text-[11px] tracking-wider text-slate-500">RESTRICTED SYSTEM · AUTHORISED PERSONNEL ONLY · ALL ACTIVITY IS AUDIT LOGGED</p>
      </section>

      <section className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm" noValidate>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Operator sign-in</h1>
          <p className="mt-1 text-sm text-muted">SIH26187 AI Border Surveillance System</p>

          {notice ? <p className="mt-5 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-700">{notice}</p> : null}

          <div className="mt-6 flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="username" className="text-xs font-semibold text-slate-700">
                Username
              </label>
              <input
                id="username"
                ref={userRef}
                autoComplete="username"
                value={username}
                maxLength={50}
                onChange={(event) => setUsername(event.target.value)}
                className="h-10 rounded-lg border border-line px-3 text-sm focus:border-cyan-600 focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
                data-testid="login-username"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-xs font-semibold text-slate-700">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={reveal ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  maxLength={72}
                  onChange={(event) => setPassword(event.target.value)}
                  className="h-10 w-full rounded-lg border border-line px-3 pr-10 text-sm focus:border-cyan-600 focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
                  data-testid="login-password"
                />
                <button
                  type="button"
                  onClick={() => setReveal(!reveal)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-slate-700"
                  aria-label={reveal ? 'Hide password' : 'Show password'}
                >
                  {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>
            {error ? (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert" data-testid="login-error">
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={busy}
              className="flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 text-sm font-semibold text-white hover:bg-slate-800 disabled:bg-slate-400"
              data-testid="login-submit"
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              Sign in
            </button>
          </div>

          <div className="mt-8 flex items-center justify-between border-t border-line pt-4 text-[11px] text-muted">
            <span className="flex items-center gap-1.5">
              <StatusDot status={serverStatus} pulse={serverStatus === 'healthy'} />
              API {healthError ? 'unreachable' : (health?.status ?? 'checking…')}
              {health ? ` · DB ${health.db}` : ''}
            </span>
            <span>3 failed attempts → 30 min lock</span>
          </div>
        </form>
      </section>
    </div>
  )
}
